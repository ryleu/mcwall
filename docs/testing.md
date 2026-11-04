# Testing

Everything below runs without the user, except the visual review.

| Check (from the plan) | How it is run | State |
| --- | --- | --- |
| Mapping logic | `nix flake check` → `tests/test_mctime.py`, `test_seasons.py`, `test_sun.py`, `test_select.py` | Passing. Both hemispheres, six fixture dates, fallback order asserted explicitly. |
| Library completeness | `mcwall verify --decode` walks the manifest grid and opens every image | Passing: 144/144 of the first sub-season present, correct resolution, 0 undecodable. |
| Still consistency | `scripts/repeatability.py` renders one job twice and reports PSNR | Script present; not yet run (the game is busy with the full library). |
| Resume | `scripts/kill_and_resume.sh` kills the client mid-batch and reruns | **PASSES.** 16 jobs, client killed at 75 s, rerun finished the remainder: no duplicates, no `.part` files left behind. |
| Power stop | `mcwall render` watchdog polls `/sys/class/power_supply` every 5 s and writes `STOP`; the mod checks it between jobs | Implemented; `tests/test_power.py` covers the state reading, including this laptop's two always-offline USB-C PD ports. |
| Terrain is fully generated before a capture | `BatchRunner.terrainReady`: server chunk queue empty, and the client's loaded-chunk and rendered-section counts both steady for 40 ticks | Implemented. Replaces a fixed frame count, which at render distance 16 captured fog where terrain should be. The old count survives as a timeout that logs a warning. |
| Loop seam | Not built — phase 6 stretch goal | Not built. |
| Visual review | `mcwall scout`, `mcwall probe`, contact sheets | Scout done and camera chosen (h30-west). Probe and per-sub-season sheets still to run in phase 5. |

## Running them

```sh
nix flake check              # unit tests in a sandbox: no network, no game
pytest -q tests              # same, in the devShell
pytest -m network            # adds the Skyfield ephemeris cross-check
mcwall verify --decode       # opens every image in the library
```

## What the unit tests actually pin down

- `test_mctime.py` restates `DimensionType.timeOfDay` independently and checks
  the implementation against it, then asserts the horizon crossings land at
  23215 / 12785 and that they are *not* at ticks 0 and 12000 — the fact the
  whole twilight-window layout rests on.
- `test_seasons.py` checks six published equinox and solstice instants to the
  minute, that a year visits all twelve sub-seasons, and that the hemispheres
  are six months apart.
- `test_sun.py` enforces the plan's hard requirement directly: at the real
  sunrise, for six fixture dates in both hemispheres, `select` must land on the
  slot nearest the vanilla horizon crossing. It also walks a whole day in
  five-minute steps and asserts the mapping is monotonic and covers exactly
  24000 ticks.
- `test_select.py` pins the fallback order, including that weather is
  substituted before time and that season is never substituted at all, and that
  selection spawns no subprocess (monkeypatched to fail if it tries).
- `test_producer.py` asserts the job manifest contains nothing machine-specific,
  so a mode 2 producer on another host can consume it unchanged.

## Checks the runs added

Three failure modes found by running the thing, now guarded:

- **The installed CLI differs from the checkout.** `headless.sh` resolved only
  from the source tree, so `nix build` produced a binary that could not render.
  Worth re-testing against the *built* package, not just `PYTHONPATH=.`, after
  any change to how files are located.
- **A stray client silently renders the wrong manifest.** Killing the launcher
  orphans the JVM, which keeps going against the previous `jobs.jsonl` and
  holds the world lock. `run_jobs` now refuses to start while
  `running_clients()` finds one, and every run reaps the JVM on the way out.
- **Settings-hash churn.** Both Iris and `setup_instance.py` rewrite
  `iris.properties`, in opposite directions. `tests/test_producer.py` pins the
  normalisation; the end-to-end check is that the hash is unchanged across a
  `setup_instance.py` run.
