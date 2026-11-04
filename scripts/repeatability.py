#!/usr/bin/env python3
"""Render one job twice and report PSNR between the two frames.

Shaders carry temporal noise, so two captures of the same state are never
bit-identical. The plan's bar is PSNR above 40 dB; below that the warm-up is
too short and shader state is still settling when the capture is taken.

    python3 scripts/repeatability.py [--warmup 120] [--tick 23215]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcwall import config as config_mod  # noqa: E402
from mcwall import producer  # noqa: E402
from mcwall.grid import Weather  # noqa: E402
from mcwall.seasons import SubSeason  # noqa: E402


def psnr(first: Path, second: Path) -> float:
    from PIL import Image

    with Image.open(first) as a, Image.open(second) as b:
        a = a.convert("RGB")
        b = b.convert("RGB")
        if a.size != b.size:
            raise SystemExit(f"size mismatch: {a.size} vs {b.size}")
        pixels_a = a.tobytes()
        pixels_b = b.tobytes()

    total = sum((x - y) ** 2 for x, y in zip(pixels_a, pixels_b))
    mse = total / len(pixels_a)
    if mse == 0:
        return math.inf
    return 10 * math.log10(255**2 / mse)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick", type=int, default=23215, help="default: sunrise")
    parser.add_argument("--sub-season", default="mid_summer")
    parser.add_argument("--weather", default="clear")
    parser.add_argument("--warmup", type=int, help="override warmup_frames")
    args = parser.parse_args()

    config = config_mod.load()
    if args.warmup:
        from dataclasses import replace

        config = replace(config, render=replace(config.render, warmup_frames=args.warmup))

    sub_season = SubSeason.from_key(args.sub_season)
    weather = Weather(args.weather)
    staging = config.library.root / ".repeat"

    jobs = [
        {
            "id": f"repeat/{run}",
            "sub_season": sub_season.serene_name.lower(),
            "world": config.render.world,
            "tick": args.tick,
            "weather": weather.value,
            "output": f"repeat/{run}.png",
        }
        # Two different output paths for the same state, with an unrelated job
        # between them so the second is not captured from identical GPU state.
        for run in ("a", "spacer", "b")
    ]
    jobs[1]["tick"] = (args.tick + 6000) % 24000

    status = producer.run_jobs(config, jobs, staging)
    print(f"captured {status.get('completed', 0)} of {len(jobs)}")

    first = staging / "repeat/a.png"
    second = staging / "repeat/b.png"
    if not (first.exists() and second.exists()):
        raise SystemExit("one of the two captures is missing")

    value = psnr(first, second)
    print(f"PSNR: {value:.2f} dB  (pass is > 40 dB)")
    print(f"  {first}\n  {second}")
    return 0 if value > 40 else 1


if __name__ == "__main__":
    sys.exit(main())
