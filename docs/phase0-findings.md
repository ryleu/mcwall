# Phase 0 — spike results

Run on 2026-11-03, NixOS 26.05, AMD Radeon 840M (Krackan, radeonsi), Hyprland,
single eDP-1 at 2880x1800.

## Acceptance

One shaded 2880x1800 PNG captured unattended in a headless cage session:
`early_autumn/clear/tick 200`. Shadows, ambient occlusion and the warm twilight
sky are all plainly Complementary Unbound, not vanilla.

## The four assumptions the plan said to settle first

| Assumption | Result | Evidence |
| --- | --- | --- |
| GPU rendering works in headless cage with Iris | Holds | Client log: `OpenGL Renderer: AMD Radeon 840M Graphics (radeonsi, krackan1, ACO)`, `OpenGL Version: 4.6 (Core Profile)`. No llvmpipe, no software fallback. |
| The screenshot path captures Iris's final output at the exact window size | Holds | `Screenshot.takeScreenshot(getMainRenderTarget())` at the tail of `GameRenderer.render` yields a 2880x1800 PNG with shaders visible. |
| Serene Seasons can be set to a specific sub-season programmatically | Holds | `/season set <sub_season>` exists in the shipped 1.21.4 jar (`sereneseasons/command/CommandSetSeason`), and `Season$SubSeason` enum names are exactly `EARLY_SPRING`…`LATE_WINTER`. |
| Foliage colours update after a season change | Not yet proven | `LevelRenderer.allChanged()` is called on every season change, followed by a settle window. Needs a two-season comparison in phase 2. |

## Sun geometry — verified, not remembered

The plan flagged the horizon-crossing ticks as coming from memory. They are now
derived. `DimensionType.timeOfDay` in decompiled 1.21.4 is exactly:

```java
public float timeOfDay(long l) {
    double d = Mth.frac(this.fixedTime.orElse(l) / 24000.0 - 0.25);
    double e = 0.5 - Math.cos(d * Math.PI) / 2.0;
    return (float)(d * 2.0 + e) / 3.0F;
}
```

and `SkyRenderer` rotates the celestial bodies by `timeOfDay * 360` degrees
about X on a great circle through the zenith, so elevation is `90 - |angle|`.
Solving that for the horizon gives **sunrise 23214.96** and **sunset 12785.04**
— the plan's remembered 23215 / 12785, confirmed to within 0.05 ticks.
Civil twilight (-6 deg) sits at 22844.85 and 13155.15.

## Deviations from the plan

| Plan | Built | Why |
| --- | --- | --- |
| Skyfield for sun times | astral, cross-checked against Skyfield | Skyfield needs a ~17 MB ephemeris downloaded at runtime; the display path must stay offline and non-blocking. The two agree to under 30 s on every fixture day (`tests/test_sun.py::test_astral_agrees_with_skyfield`, marked `network`). |
| `portablemc` or HeadlessMC to launch | Prism's own CLI | Prism 11.1.1 takes `--launch <instance> --profile <account>`, and the machine already has an authenticated Prism. No second auth path to build or keep working. |
| `swww` | `swww` or `awww`, detected at runtime | nixpkgs now ships this package as `awww` with binaries `awww`/`awww-daemon`. Same arguments; `mcwall.apply` tries both names. |
| Gradle from nixpkgs | Gradle wrapper (9.8.2) | Current Fabric Loom (1.19.3) needs Gradle >= 9.8; nixpkgs has 8.14.4 and 9.4.1. The wrapper is the standard Fabric setup and pins the build independently of nixpkgs. |
| JDK 21 for the mod build | JDK 25 runs Gradle, bytecode still targets 21 | Loom 1.19.3 requires a JVM >= 25 to resolve. The mod itself is compiled with `options.release = 21`, which is what the game needs. |

## Timings, measured

| Step | Time |
| --- | --- |
| Launcher start to game window | ~10 s |
| World generation (first run only) | ~35 s |
| A job that changes season or camera (terrain settle + 120 warmup frames) | ~22 s |
| A job that only changes clock or weather (120 warmup frames) | 11.4 s |

The 11.4 s is the measured mean over a complete 144-still sub-season at render
distance 16, not an estimate: 144 captured, 0 failed, 27.4 minutes. At that
rate the full 1,728-cell library is about **5.5 hours**. Warm-up frames are
most of the per-still cost, so `scripts/repeatability.py` is the lever if that
needs to come down.

## Size, measured

Per 2880x1800 frame, on real sky-dominant captures:

| Format | Per image | 1,728 images |
| --- | --- | --- |
| PNG (written by the mod, deleted after conversion) | 10.8 MB | 18.7 GB |
| **WebP q90 (default), measured over 144 real stills** | **0.25 MB** | **0.43 GB** |

The q90 figure is the mean over a complete sub-season, spanning 0.06 MB for a
dark night frame to 0.89 MB for a bright rainy one. An earlier estimate of
0.62 GB came from a handful of daylight frames and was pessimistic: night
frames are a quarter the size and a third of the grid is night.

Peak disk during a run is much higher than the final figure: `convert_staging`
runs once *after* the whole batch, so `library/.staging` holds every PNG of the
run at once. A full 1,544-still render peaked at about **16 GB** of staging
before collapsing to ~0.4 GB of WebP. An interrupted run leaves that backlog on
disk until the next `mcwall render` converts it — recoverable, not lost, but
worth knowing before starting a full build on a small disk.

## Bug found by the scouting run

Every camera candidate rendered the identical frame. `BatchRunner` was
teleporting with `/tp @s …` through `server.getCommands().performPrefixedCommand`
on a source stack built by `server.createCommandSourceStack()`. That source has
no executing entity, so `@s` matched nothing and the teleport — and the
`gamemode spectator` next to it — silently did nothing. Both now go through
`ServerPlayer.teleportTo(...)` and `setGameMode(...)` directly.

Worth remembering: a command run from the server's own source stack has no
`@s`, and `performPrefixedCommand` returns void, so a failure is invisible.
Commands that need no entity (`season set`, `difficulty`, `gamerule`) were fine
all along, which is why the failure looked like a camera config problem.

## Biome: the season axis nearly got wasted

With the camera fixed, the scouting sheet showed the world spawn is spruce
taiga. Serene Seasons recolours temperate broadleaf foliage strongly and
conifers barely at all, so a taiga camera would have made all twelve
sub-seasons look identical apart from winter snow — twelve times the library
for no visible difference. The plan called for "a temperate biome that Serene
Seasons changes through the year"; spawn is not it, and nothing would have
flagged that until the contact-sheet review at the very end.

`BiomeLocator` now searches outward from the origin for the first of
`forest, flower_forest, birch_forest, dark_forest, plains, meadow`
(`ServerLevel.findClosestBiome3d`), once per world load, and re-anchors the
camera's x and z onto it. Height and heading stay as configured, so a camera
sweep still works. The result is written to `mcwall/located.json`.

## The launcher is not the batch

`watch` originally looped on `process.poll()`, where `process` was the
`cage -> prismlauncher` chain. Prism hands the game off to a detached JVM and
exits, so the launcher dying says nothing about whether the batch is still
running. A perfectly healthy run looked like an immediate failure: the
orchestrator raised "the client exited without reporting any progress" while
the game went on capturing in the background, orphaned.

Two consequences, both fixed:

- `watch` now waits for the mod's own `DONE` file, or for no client process to
  remain. The mod is the authority on when the batch is over, not Prism.
- An orphaned client holds the world lock and keeps reading the *previous* job
  manifest, so the next run silently renders the wrong thing. `run_jobs` now
  refuses to start when `running_clients()` finds one, the launch gets its own
  process group, and `kill_clients()` reaps the JVM at the end of every run.

## The packaged CLI could not render at all

`cage_command` built the path to the in-cage helper as
`Path(__file__).parent.parent / "scripts" / "headless.sh"`. That resolves
inside a git checkout and nowhere else: `pyproject.toml` declares
`packages = ["mcwall"]`, so the installed wheel has no `scripts/` directory.
Every run from the nix-built binary died with
`cage: Failed to spawn client: No such file or directory`.

This mattered more than it looks: the home-manager unit runs the *installed*
binary, so the one path a user would actually use was the broken one, and the
repo-checkout path everything had been tested through was the exception.

The helper is now a string constant in `producer.py`, written to the batch
state directory at run time and invoked as `bash <path>`. One source of truth,
no packaging data, and no reliance on a file mode surviving the build.

## Winter needed snow put down, not snowed in

Two separate problems, both silent:

1. The soak ran immediately after the world loaded, while the player was still
   at spawn — 279 blocks from the camera. Chunk ticking follows the player, so
   the snow fell somewhere nobody was looking. The camera teleport now happens
   at the start of the soak, not after it.
2. Even correctly placed, vanilla snowfall is slow. `ServerLevel.tickChunk`
   touches one random column per chunk roughly every 16 ticks, so covering the
   ground inside render distance 16 would take about 17 minutes of wall clock
   for one world, and the coverage would be random.

So the soak no longer waits for weather at all: once terrain is ready it walks
every column within `renderDistance * 16 + 32` blocks and places a snow layer
wherever `Biome.shouldSnow` and `Blocks.SNOW.canSurvive` both agree. That is
228,101 layers in 57 seconds, complete and repeatable, and because only
biome-approved columns are touched nothing melts back out between captures.

The biome search needed a related fix: it works on the generator's noise, not
on loaded chunks, so in a brand new world `getHeight` answered with the world
floor (-64). It now forces the chunk to exist before asking.

## The shaderpack identity hash had to ignore Iris's own churn

The plan asks for "the pack name and a hash of its settings" in the manifest,
so that changing either invalidates the library. Hashing the raw bytes of
`config/iris.properties` does not work: Iris rewrites that file every time the
game exits, stamping a fresh timestamp comment and filling in defaults it did
not previously write.

```
#This file stores configuration options for Iris, such as the currently active shaderpack
#Tue Nov 03 18:03:42 CST 2026      <- new on every run
allowUnknownShaders=false          <- default, filled in by Iris
colorSpace=SRGB                    <- default, filled in by Iris
enableDebugOptions=false           <- default, filled in by Iris
...
```

So the first render after any other render was refused with "shaderpack
settings changed", every time, for ever. `normalise_properties` now strips
comments and blank lines and sorts the remaining settings before hashing, which
keeps the check meaningful (a real `sunPathRotation` change is still caught)
without tripping on Iris's bookkeeping. Pinned by three tests in
`tests/test_producer.py`.

## Terrain readiness replaced the frame-count settle

The first implementation waited a fixed 240 frames after a state change. At
render distance 16 a fresh camera spot has thousands of chunks to generate, and
a fixed count either captures a wall of fog or wastes time on every job that
needed none. `BatchRunner.terrainReady` now gates each capture on three signals
agreeing and then holding steady for `terrain_stable_ticks` (default 40):

- `ServerChunkCache.getPendingTasksCount() == 0` — generation finished;
- `ClientChunkCache.getLoadedChunksCount()` stopped changing;
- `LevelRenderer.countRenderedSections()` stopped changing.

Sodium replaces the section renderer, so the two client counts are used only
for *stability*, never compared against an expected total. The old frame count
survives as a timeout with a logged warning, so a stuck job cannot hang a
batch.
