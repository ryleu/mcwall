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
the 8-bit capture never had, which is all that can be done after the fact.

``deband_one`` also serves new renders through
:func:`mcwall.producer.convert_one`, and those may arrive 16 bits deep from
``render.hdr_capture``. A deep frame takes a different route — real dithering
down to 8 bits, then lossless encoding — because it still has the detail
rather than needing one invented. Both routes end at the same place, so the
caller does not choose; the source's bit depth does.

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


def png_bit_depth(path: Path) -> int:
    """Bit depth from a PNG's IHDR, or 8 for anything that is not a PNG.

    Read from the header rather than asked of ffprobe: this runs once per
    image on every conversion, and the byte is at a fixed offset.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(26)
    except OSError:
        return 8
    if len(header) < 26 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return 8
    return header[24]


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR."""
    with path.open("rb") as handle:
        header = handle.read(24)
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def dither_to_8bit(source: Path, target: Path) -> None:
    """Quantise a 16-bit capture to 8 bits, spending the spare precision.

    Done here rather than in ffmpeg because ffmpeg cannot dither an RGB depth
    reduction at all: `zscale`'s four dither modes and every `-sws_dither`
    setting produce byte-identical output to `d=none` on an rgb48 -> rgb24
    conversion, and the `noise` filter rejects 16-bit input outright.

    The noise is triangular — the difference of two uniforms, one 8-bit level
    wide. That is the standard choice for quantising to a fixed depth: it
    decorrelates the error from the signal, so what is left is even grain
    instead of the contours or the ordered crosshatch that make banding
    something the eye picks out.
    """
    import numpy

    width, height = png_size(source)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source),
         "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
        capture_output=True, check=True,
    ).stdout
    deep = numpy.frombuffer(raw, numpy.uint16).reshape(height, width, 3)
    values = deep.astype(numpy.float32) * (255.0 / 65535.0)

    # Fixed seed: two renders of the same job must still compare equal, which
    # is what scripts/repeatability.py measures.
    rng = numpy.random.default_rng(0)
    noise = rng.random(values.shape, dtype=numpy.float32)
    noise -= rng.random(values.shape, dtype=numpy.float32)
    values += noise
    eight = numpy.clip(numpy.rint(values, out=values), 0, 255).astype(numpy.uint8)

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{width}x{height}", "-i", "-", str(target)],
        input=eight.tobytes(), check=True,
    )


def encode_command(source: Path, target: Path, deep: bool,
                   settings: DebandSettings) -> list[str]:
    """How to encode an already-dithered frame.

    A deep capture gets true lossless, which is not the cautious choice but
    the cheap one twice over: `-near_lossless` is *slower* (3.9s against 1.3s
    on one 2880x1800 frame) and its whole job is smoothing away low-amplitude
    noise, which is exactly the dither. Measured on a post-sunset frame, the
    share of a sky column sitting inside a flat plateau is 28-30% through
    near-lossless at any setting and 9% through lossless. It costs about a
    third more disk.
    """
    if deep:
        return ["cwebp", "-quiet", "-lossless", "-z", "4",
                str(source), "-o", str(target)]
    return ["cwebp", "-quiet",
            "-near_lossless", str(settings.near_lossless),
            "-q", "100", "-m", "6",
            str(source), "-o", str(target)]


def deband_one(source: Path, target: Path, settings: DebandSettings) -> int:
    """Deband one image and write it near-lossless. Returns bytes written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    deep = png_bit_depth(source) > 8
    with tempfile.TemporaryDirectory(prefix="mcwall-deband-") as scratch:
        intermediate = Path(scratch) / "frame.png"
        if deep:
            # gradfun has no deep pixel format, so handing it a 16-bit frame
            # makes ffmpeg auto-insert `rgb48be -> gbrp` first: an undithered
            # drop to 8 bits *before* the filter meant to do the dithering.
            # A float capture through that path comes out indistinguishable
            # from the 8-bit one, with nothing to show for the render.
            dither_to_8bit(source, intermediate)
        else:
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y", "-i", str(source),
                    "-vf", f"gradfun=strength={settings.strength}"
                           f":radius={settings.radius}",
                    str(intermediate),
                ],
                check=True,
            )
        temporary = target.with_suffix(target.suffix + ".part")
        subprocess.run(encode_command(intermediate, temporary, deep, settings),
                       check=True)
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
