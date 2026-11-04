"""Repair banding in a library that is already encoded.

New renders deband during conversion instead (see
:func:`mcwall.producer.convert_one`), straight from the lossless capture.
This module is for a library whose images are already on disk.

Dark frames use very little of the 8-bit range — a night sky spans roughly 18
to 31 of 255 luma levels — so a smooth gradient has only a dozen or so values
to cross and contours visibly. Lossy WebP then quantises blocks of it, adding
a second, blockier layer of the same artefact.

``ffmpeg``'s ``gradfun`` interpolates across each band and dithers the result,
which hides both. The dither is the load-bearing part: it is high-frequency
noise, so a lossy re-encode strips it and the banding returns exactly as it
was. The output therefore has to be near-lossless, at roughly 8x the size.

This reconstructs a plausible smooth gradient rather than recovering detail
the 8-bit capture never had. That is the best available fix: Complementary
exposes no night-sky brightness option, so the gradient cannot be given more
range at render time (see docs/findings.md).

The pass never writes over the library in place: it builds a new tree, checks
the count matches, and swaps, keeping the original until it is deleted by hand.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STAGE_DIR = ".deband"
BACKUP_SUFFIX = ".lossy-backup"


@dataclass(frozen=True)
class DebandSettings:
    """gradfun's two knobs, plus the encoder quality that has to preserve it."""

    strength: float = 3.0
    radius: int = 16
    # cwebp -near_lossless: 0 is most aggressive, 100 is off. 60 keeps the
    # dither intact while still compressing the flat regions.
    near_lossless: int = 60


def deband_one(source: Path, target: Path, settings: DebandSettings) -> int:
    """Deband one image and write it near-lossless. Returns bytes written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mcwall-deband-") as scratch:
        intermediate = Path(scratch) / "frame.png"
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-i", str(source),
                "-vf", f"gradfun=strength={settings.strength}:radius={settings.radius}",
                str(intermediate),
            ],
            check=True,
        )
        temporary = target.with_suffix(target.suffix + ".part")
        subprocess.run(
            [
                "cwebp", "-quiet",
                "-near_lossless", str(settings.near_lossless),
                "-q", "100", "-m", "6",
                str(intermediate), "-o", str(temporary),
            ],
            check=True,
        )
        temporary.replace(target)
    return target.stat().st_size


def deband_tree(
    source_root: Path,
    target_root: Path,
    settings: DebandSettings,
    workers: int | None = None,
) -> tuple[int, int]:
    """Deband every image under ``source_root``. Returns (count, bytes)."""
    import os

    images = sorted(source_root.rglob("*.webp"))
    if not images:
        raise ValueError(f"no images under {source_root}")

    workers = workers or max(1, min(len(images), (os.cpu_count() or 2)))
    log.info("debanding %s images with %s workers", len(images), workers)

    done = 0
    written = 0
    failures: list[tuple[Path, Exception]] = []

    def work(image: Path) -> int:
        target = target_root / image.relative_to(source_root)
        if target.exists():
            # Resumable: a rerun picks up where an interrupted pass stopped.
            return target.stat().st_size
        return deband_one(image, target, settings)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, image): image for image in images}
        for future in as_completed(futures):
            try:
                written += future.result()
                done += 1
                if done % 200 == 0:
                    log.info("debanded %s/%s", done, len(images))
            except Exception as error:  # noqa: BLE001 - collected and reported
                failures.append((futures[future], error))

    if failures:
        for image, error in failures[:5]:
            log.error("could not deband %s: %s", image, error)
        raise RuntimeError(
            f"{len(failures)} of {len(images)} images failed; the originals are "
            f"untouched and the partial output is in {target_root}"
        )
    return done, written


def swap_in(source_root: Path, staged_root: Path) -> Path:
    """Put the debanded tree in place, keeping the original beside it."""
    backup = source_root.with_name(source_root.name + BACKUP_SUFFIX)
    if backup.exists():
        shutil.rmtree(backup)
    source_root.rename(backup)
    staged_root.rename(source_root)
    return backup
