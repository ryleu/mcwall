# What is built for mode 2 and loops, and what is not

Neither is in scope for mode 1. Both were designed for, so neither needs the
system restructured later. This records exactly where the seams are.

## Mode 2 — accurate sky, rendered elsewhere

Mode 2 swaps the producer and leaves the display path alone. Three things were
done today so it slots in:

**Selection is behind a strategy interface.** `mcwall/select.py` defines a
`Selector` protocol with a single `select(now, conditions) -> Choice`.
`Mode1Selector` looks up by sub-season, time slot and weather. A mode 2
selector looks up by real timestamp and weather, and `mcwall.apply` does not
change, because it only ever sees a `Choice`.

**The job manifest names nothing local.** `tests/test_producer.py` asserts this
rather than trusting it: no `/home/`, no `hypr`, no `Prism`, no `swww`, no
absolute library path anywhere in a job. Output paths are relative to a run
root the mod is told once. A render host on another machine can consume the
same manifest and rsync the results in.

**State setting is separate from the capture loop.** In `BatchRunner`,
`applyJob` puts the world into a state and `advanceCapture` / `afterFrame`
count frames and write the image. A mode 2 sky override is a step inside
`applyJob`; nothing below it moves.

What mode 2 still needs: Astrocraft with its Live Mode clock overridden per
job, a library keyed by timestamp rather than grid cell (a new `Manifest.mode`
value and a second `Selector`), and a sync step. Astrocraft warns of visual
bugs under shaders, so mode 2 may need shaders off or a tuned pack.

## Loops — the phase 6 stretch goal

**Not built.** The display half exists and is tested; the producer half is not.

Already in place:

- `Manifest.loop_ticks` — the coarser loop grid, separate from the still grid.
- `Library.has_loop` / `path_for(cell, loop=True)` — `<pack>/loops/<sub-season>/<weather>/<tick>.mp4`.
- `Mode1Selector` prefers a loop only on AC and only for `rain`/`thunder`,
  falls back to the nearest loop tick, then to stills. Covered by
  `test_loop_used_on_ac_for_rain`, `test_battery_forces_stills` and
  `test_clear_weather_never_loops`.
- `mcwall.apply` drives `mpvpaper` with auto-pause-when-covered, and stops it
  when the machine drops to battery.

Still missing, all of it inside the capture mod:

- A mixin on the frame timer so each frame advances a fixed partial tick, with
  game ticks advanced by hand. Iris's `frameTimeCounter` and `frameTime` must
  come from that same fixed clock or shader animation jitters between frames.
- Randomness removal: reseed the particle RNG at frame 0, freeze the cloud
  offset, no random lightning.
- Loop length: 256 ticks (12.8 s) by default. Vanilla rain is believed to
  repeat every 32 ticks and still water every 64 — both need checking in
  `var/mcsrc` before being relied on, like the sun formula was.
- Encoding: render the loop plus ~2 s, crossfade tail into head with ffmpeg
  `xfade`, encode H.264/HEVC at 24-30 fps with a closed GOP so the loop point
  is a keyframe.

A full loop grid would be 2 x 48 x 12 = 1,152 clips; the plan's coarser
12 x 12 x 2 = 288 is what `loop_ticks` is sized for.
