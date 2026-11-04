# Testing

```sh
nix flake check              # 193 unit tests in a sandbox: no network, no game
pytest -q tests              # the same, in the devShell
pytest -m network            # adds the Skyfield ephemeris cross-check
mcwall verify --decode       # opens every image in the library
```

Everything except the visual review runs without a person watching.

## State

| Check | How | State |
| --- | --- | --- |
| Mapping logic | `tests/test_mctime.py`, `test_seasons.py`, `test_sun.py`, `test_select.py` | Passing. Both hemispheres, six fixture dates, fallback order asserted. |
| Library completeness | `mcwall verify --decode` | Passing: 1728/1728 present, correct resolution, 0 undecodable. |
| Resume after a kill | `scripts/kill_and_resume.sh` | **Passing.** 16 jobs, client killed at 75 s, rerun finished the remainder with no duplicates and no `.part` files. |
| Terrain ready before capture | `BatchRunner.terrainReady` | Implemented; the frame count survives only as a timeout that logs a warning. |
| Power stop | `watch` polls `/sys/class/power_supply` and writes `STOP`; the mod checks it between jobs | Implemented. `tests/test_power.py` covers the state reading, including this laptop's two always-offline USB-C PD ports. |
| Conversion | `tests/test_producer.py` | Passing: parallel, atomic, skips existing, and a failure keeps its source PNGs so a rerun retries. |
| Build identity | `tests/test_producer.py` | Passing: seed, camera, resolution and render distance each fork the library; warm-up frames do not. A world never played in refuses to plan rather than rendering spawn. |
| Adopting an older library | `tests/test_producer.py` | Passing: the rename carries the manifest, and a library built with other shaderpack settings is left where it is. |
| Slot placement | `tests/test_scout.py` | Passing: slots concentrate where the picture changes, spread evenly when it changes uniformly, and identical frames are rejected rather than guessed at. |
| Promotion on completion | `tests/test_producer.py` | Passing: a half-finished build leaves the active manifest alone, a complete one replaces it, and a resumed run keeps its own build record. Confirmed live: a `--limit 1` run reported 1626 missing and left the forest library on screen. |
| Conversion during a run | `tests/test_producer.py` | Passing: converts finished captures, never deletes the live staging tree, defers a failed encode to the final sweep, and the thread stops with the run. |
| TUI subprocesses | `tests/test_tui.py` | Passing: a spawned command can import mcwall from a directory with no PYTHONPATH. Confirmed against the built package by driving its TUI through a pty and running `verify`. |
| TUI | `tests/test_tui.py` | Passing: progress arithmetic, job neighbours, torn status files, and a pty smoke test that drives every view — verified to fail when the draw is broken. The command list is asserted equal to the CLI's own parser. |
| Dry run writes nothing | `tests/test_producer.py` | Passing, and verified to fail when the old ordering is restored. |
| Missing capture mod | `tests/test_producer.py` | Passing: the render refuses before launching rather than timing out. |
| Weather window | `tests/test_weather.py` | Passing: a dry gap between showers still reads as rain, rain older than the window is forgotten, forecast rows are ignored, thunder outranks rain, and a missing nowcast is not an error. |
| Daemon dependency | `tests/test_apply.py` | Passing: `apply` raises rather than spawning a daemon, and the error names the unit to start. |
| home-manager module | Built as real `homeManagerConfiguration`s | `services.awww` and `services.swww` both yield `Requires=awww.service`; an explicit `daemonUnit` passes through; neither fails to evaluate. |
| Still consistency | `scripts/repeatability.py` — renders one job twice, reports PSNR | **Not yet run.** |
| Visual review | `mcwall scout`, `mcwall gallery` | Camera chosen (h30-west). Per-sub-season sheets and the twelve-season strip build; the look has not been signed off frame by frame. |
| Loop seam | — | Not built; loops are a stretch goal, see `mode2-and-loops.md`. |

## What the tests actually pin down

- `test_mctime.py` restates `DimensionType.timeOfDay` independently and checks
  the implementation against it, then asserts the horizon crossings land at
  23215 / 12785 and that they are *not* at ticks 0 and 12000 — the fact the
  twilight windows rest on.
- `test_seasons.py` checks six published equinox and solstice instants to the
  minute, that a year visits all twelve sub-seasons, and that the hemispheres
  are six months apart.
- `test_sun.py` enforces the hard requirement directly: at the real sunrise,
  for six fixture dates in both hemispheres, `select` must land on the slot
  nearest the vanilla horizon crossing. It also walks a day in five-minute
  steps and asserts the mapping is monotonic and covers exactly 24000 ticks.
- `test_select.py` pins the fallback order — weather is substituted before
  time, season never — and asserts selection spawns no subprocess.
- `test_producer.py` asserts the job manifest contains nothing
  machine-specific, so a mode 2 producer on another host can consume it, and
  pins the config/output split: only settings that change what a frame *shows*
  may change the build id.

## Things worth re-testing after a change

- **The built package, not just the checkout.** `PYTHONPATH=. python3 -m
  mcwall.cli` and `nix build` can disagree about which files exist. The
  home-manager unit runs the built one.
- **A stray client.** Killing the launcher orphans the JVM, which keeps
  rendering the previous manifest and holds the world lock.
- **The settings hash across a `mcwall setup` run.** Both Iris and
  `mcwall setup` rewrite `iris.properties`, in opposite directions; the
  hash must not move.
