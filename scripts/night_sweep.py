#!/usr/bin/env python3
"""Render the same night moment at several night-brightness settings.

The night frames in the grid use only about 18 to 31 of the 255 available
luma levels, which is why they contour: a smooth gradient has barely a dozen
values to cross. Debanding hides that; brightening the night at render time
fixes it, by spending more of the 8-bit range on the part of the picture you
actually see.

The lever is the shaderpack's own options, not the game's brightness slider —
that is already at its maximum of 1.0, and Complementary only applies it to
terrain lighting, never to the sky gradient.

    ATM_NIGHT_I    night atmosphere intensity   the sky itself
    LIGHT_NIGHT_I  night lighting intensity     the ground
    TM_EXPOSURE    tonemap exposure             everything, day included

One game launch per variant, so this costs a couple of minutes each. The
original settings file is restored at the end.

    python3 scripts/night_sweep.py [--tick 15533]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcwall import config as config_mod  # noqa: E402
from mcwall import producer, scout  # noqa: E402

# Each variant is a set of shaderpack option overrides. Values must come from
# the option's allowed list in the pack, or Iris falls back to the default.
VARIANTS: list[tuple[str, dict[str, str]]] = [
    ("00-default", {}),
    ("01-sky-1.3", {"ATM_NIGHT_I": "1.30"}),
    ("02-sky-1.6", {"ATM_NIGHT_I": "1.60"}),
    ("03-sky-2.0", {"ATM_NIGHT_I": "2.00"}),
    ("04-sky-1.6-ground-1.5", {"ATM_NIGHT_I": "1.60", "LIGHT_NIGHT_I": "1.50"}),
    ("05-sky-2.0-exposure-1.3", {"ATM_NIGHT_I": "2.00", "TM_EXPOSURE": "1.30"}),
]


def settings_paths(config) -> list[Path]:
    """Both spellings Iris might use for the per-pack options file."""
    shaderpacks = producer.game_dir(config) / "shaderpacks"
    return [
        shaderpacks / f"{config.render.shaderpack}.txt",
        shaderpacks / f"{Path(config.render.shaderpack).stem}.txt",
    ]


def write_settings(config, options: dict[str, str]) -> None:
    """Write the overrides to every candidate path, so one of them lands."""
    body = "".join(f"{key}={value}\n" for key, value in sorted(options.items()))
    for path in settings_paths(config):
        if options:
            path.write_text(body)
        else:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick", type=int, default=15533, help="a night tick")
    parser.add_argument("--sub-season", default="mid_autumn")
    parser.add_argument("--force", action="store_true", help="run on battery")
    args = parser.parse_args()

    config = config_mod.load()
    staging = config.library.root / ".nightsweep"
    shutil.rmtree(staging, ignore_errors=True)

    saved = {path: path.read_text() for path in settings_paths(config) if path.exists()}
    rendered: list[Path] = []

    try:
        for label, options in VARIANTS:
            print(f"\n=== {label}: {options or 'pack defaults'} ===")
            write_settings(config, options)
            jobs = [
                {
                    "id": f"nightsweep/{label}",
                    "sub_season": args.sub_season.lower(),
                    "world": config.render.world,
                    "tick": args.tick,
                    "weather": "clear",
                    "output": f"nightsweep/{label}.png",
                }
            ]
            status = producer.run_jobs(config, jobs, staging, force=args.force)
            if status.get("failed"):
                print(f"  WARNING: {label} reported {status['failed']} failures")
            frame = staging / "nightsweep" / f"{label}.png"
            if frame.exists():
                rendered.append(frame)
                print(f"  -> {frame.name}")
    finally:
        for path in settings_paths(config):
            path.unlink(missing_ok=True)
        for path, body in saved.items():
            path.write_text(body)
        print("\nrestored the original shaderpack settings")

    if not rendered:
        raise SystemExit("no frames rendered")

    sheet = scout.contact_sheet(
        sorted(rendered), config.library.root / "nightsweep-sheet.png",
        columns=3, width=620,
    )
    print(f"\ncontact sheet: {sheet}")
    for frame in sorted(rendered):
        print(f"  {frame}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
