# Verified facts and design decisions

Everything here was checked against decompiled source, a shipped jar, or a
measurement on this machine. Where a number appears, it was measured rather
than estimated. Read `README.md` first for what the system does.

Hardware: NixOS 26.05, AMD Radeon 840M (Krackan, radeonsi), Hyprland, one
eDP-1 panel at 2880x1800.

## Sun geometry

`DimensionType.timeOfDay` in 1.21.4 is:

```java
public float timeOfDay(long l) {
    double d = Mth.frac(this.fixedTime.orElse(l) / 24000.0 - 0.25);
    double e = 0.5 - Math.cos(d * Math.PI) / 2.0;
    return (float)(d * 2.0 + e) / 3.0F;
}
```

`SkyRenderer` rotates the celestial bodies by `timeOfDay * 360` degrees about
X, on a great circle through the zenith, so elevation is `90 - |angle|` with
the angle wrapped to (-180, 180].

Solving that for the horizon:

| Event | Tick |
| --- | --- |
| Sunrise (0 deg) | **23214.96** |
| Sunset (0 deg) | **12785.04** |
| Civil dawn (-6 deg) | 22844.85 |
| Civil dusk (-6 deg) | 13155.15 |

The crossings are not at ticks 0 and 12000, which is why the two twilight
windows are centred where they are.

Real sun times come from astral. `time_at_elevation` applies a refraction
correction of its own, so the elevation anchors pass `with_refraction=False`
and use geometric elevations (-0.833 for the visible horizon, -6 for civil
twilight). Asking for -0.833 *with* refraction double-counts it and puts
sunrise about two minutes early. Cross-checked against Skyfield/de421 on six
fixture days: agreement within 30 seconds.

## Capture

| Property | Value |
| --- | --- |
| GL inside headless cage | `AMD Radeon 840M Graphics (radeonsi, krackan1, ACO)`, OpenGL 4.6 Core — hardware, no llvmpipe |
| Capture bit depth | **8-bit.** `takeScreenshot` builds a `NativeImage` in `Format.RGBA` (4 bytes/pixel, offsets 0/8/16/24) and reads back with `_readPixels(..., 5121, ...)` = `GL_UNSIGNED_BYTE` |
| Shader output | `Screenshot.takeScreenshot(getMainRenderTarget())` at the tail of `GameRenderer.render` includes Iris's final composite |
| Resolution | The headless output is resized to the capture size, so the framebuffer read is the one the game renders |

Serene Seasons exposes `/season set <sub_season>`
(`sereneseasons/command/CommandSetSeason` in the shipped 1.21.4 jar), and its
`Season$SubSeason` enum names are exactly `EARLY_SPRING`…`LATE_WINTER`.

## Why the pieces are shaped the way they are

**Commands run from the server's own source stack have no `@s`.** A
`CommandSourceStack` from `server.createCommandSourceStack()` has no executing
entity, so `/tp @s` and `/gamemode` match nothing, and
`performPrefixedCommand` returns void so the failure is invisible. The camera
and game mode go through `ServerPlayer.teleportTo` and `setGameMode` instead.
Commands that need no entity (`season set`, `difficulty`, `gamerule`) are fine.

**The camera comes from the world, and biome anchoring is opt-in.** The shot
is read from `level.dat` — wherever the player was standing when they quit —
so framing is chosen by looking at it rather than by editing coordinates.

`BiomeLocator` can override the camera's x and z instead, searching outward
for a named biome. It exists because Serene Seasons recolours temperate
broadleaf foliage strongly and conifers barely at all: world spawn here is
spruce taiga, where all twelve sub-seasons would look alike apart from winter
snow, a twelvefold library for no visible difference. It forces the chunk to
exist before reading its height, because the biome search works on the
generator's noise rather than loaded chunks and would otherwise answer with
the world floor. `locate_biome` defaults to empty, since a search that moves
the camera would discard a shot that was just framed by hand.

**The build's identity is its directory name.** Shaderpack and settings, seed,
camera, resolution and render distance are hashed into
`<world>-<seed>-<pack>-<hash>`, so changing any of them renders alongside the
old library rather than over it. The config file is left to runtime behaviour
only. Adopting a library built before this means renaming its directory *and*
rewriting its manifest — the manifest names the build it describes, and a
rename alone makes every image unfindable.

**Terrain readiness gates each capture, not a frame count.** At render
distance 16 a fresh camera spot has thousands of chunks to generate. Three
signals must agree and then hold steady for `terrain_stable_ticks`: the
server's chunk queue is empty, and neither the client's loaded-chunk count nor
its rendered-section count is still moving. Sodium replaces the section
renderer, so the client counts are used only for stability, never compared
against an expected total.

**The launcher is not the batch.** Prism hands the game off to a detached JVM
and exits within seconds, so the launcher's lifetime says nothing about the
run. `watch` waits for the mod's `DONE` file, or for a client it has actually
observed running to disappear. An orphaned client holds the world lock and
keeps rendering the previous manifest, so `run_jobs` refuses to start while
`running_clients()` finds one, and every run reaps the JVM on the way out.

**Snow is placed, not waited for.** `ServerLevel.tickChunk` touches one random
column per chunk roughly every 16 ticks, so covering the ground inside render
distance would take about 17 minutes per world and land randomly. The soak
teleports to the camera first, then walks every column within
`renderDistance * 16 + 32` and places snow wherever `Biome.shouldSnow` and
`Blocks.SNOW.canSurvive` both agree: 228,101 layers in 57 seconds, complete
and repeatable. Only biome-approved columns are touched, so nothing melts back
out between captures.

**The in-cage helper is generated at run time.** A path relative to the source
tree resolves in a git checkout and nowhere else, and the installed package has
no `scripts/` directory — which is exactly the path the home-manager unit uses.

**The settings hash ignores Iris's own churn.** Iris rewrites
`iris.properties` on exit, stamping a timestamp and filling in defaults, and
`mcwall setup` writes it back to its minimal form. The hash covers the
pack bytes, the pack's own option file (both spellings Iris might use), and
only `shaderPack` and `enableShaders` from Iris's config.

## Banding

Measured sky luma across a day:

| Frame | p1..p99 | Levels used |
| --- | --- | --- |
| Midday | 110..196 | 85 |
| Dusk | 50..128 | 78 |
| Night | 12..31 | **18** |

A night sky crossing eighteen of 255 levels contours in any 8-bit format, so
lossless encoding alone does not help. The display runs at 10-bit
(`XRGB2101010`), but the capture is 8-bit and WebP has no 10-bit mode, so the
compositor is bit-replicating.

**Brightening the night does not fix it, and the shaderpack cannot.** A
six-variant sweep (`scripts/night_sweep.py`) found `ATM_NIGHT_I` has no effect
on the sky: 1.30, 1.60 and 2.00 all give an identical 12.3..32.0 range.
`atmColorMult` reaches fog, clouds, nebula and aurora, never the sky dome, and
the dome's colours are hardcoded with no option exposed:

```glsl
vec3 nightUpSkyColor     = pow(nightColFactor, vec3(0.90)) * 0.45;
vec3 nightMiddleSkyColor = sqrt(nightUpSkyColor) * 0.65;
```

`TM_EXPOSURE` is the only exposed lever that touches the sky, worth 19.7 to
22.3 levels (+13%) while brightening every day frame. The vanilla brightness
slider is not a lever either: it is already at its 1.0 maximum,
`UnitDouble.validateValue` rejects anything outside 0..1 so `options.txt`
cannot be edited past it, and Complementary applies `screenBrightness` only to
terrain lighting.

So conversion runs `gradfun` and encodes near-lossless, straight from the
lossless capture. The dither is load-bearing: a lossy re-encode strips it and
the banding returns exactly as it was.

## Rain is a window, not an instant

Open-Meteo's `current.weather_code` is one sample of a model, and showers
arrive in bursts. Measured in Huntsville on 2026-11-04, during rain:

| Source | Time (UTC) | Reported |
| --- | --- | --- |
| `current.weather_code` | 18:45 | 3 (overcast), 0.0 mm |
| KHSV station observation | 18:30 | "Cloudy" |
| 15-minute nowcast | 18:00, 18:15 | **1.4 / 1.3 mm, code 63** |
| 15-minute nowcast | 18:30, 18:45 | 0.0 mm, code 3 |
| 15-minute nowcast | 19:00 | 0.5 mm, code 80 |

Both the model and the airport were right and both were useless: the sample
landed in a dry gap between two showers while it was raining outside. An
instantaneous categorical code does not mean what a person means by "it is
raining", and it makes the wallpaper flicker between skies.

So the weather is the most notable sky in the last `RAIN_LOOKBACK_MINUTES`
(30) of the nowcast, which is radar-derived. Spans are stamped with their
start, so one straddling the cutoff still counts; spans starting after now are
forecasts and are ignored. Precipitation with a dry-looking code is rain — the
millimetres win. `past_minutely_15=3&forecast_minutely_15=1` returns five
rows, so this costs no more than the old single-value call.

The cache is 300 s rather than 900 s, matching the display timer, so a shower
does not also wait out a stale entry.

## The wallpaper daemon

`mcwall apply` fails rather than starting a daemon. `mcwall.service` is a
`oneshot`, and systemd's default `KillMode=control-group` reaps the whole
cgroup when it exits — `start_new_session` makes a new session, not a new
cgroup — so a daemon started from inside dies seconds later and every tick
starts a fresh one.

home-manager ships one daemon module, `services.awww`, defining `awww.service`.
`services.swww.{enable,package,extraArgs}` are `mkRenamedOptionModule` aliases
onto it, so there is only one option to read. With neither that module nor an
explicit `daemonUnit`, the configuration fails to evaluate.

Two things that make the daemon awkward to probe:

- its `comm` is `.awww-daemon-wr`, a nixpkgs wrapper, so `pkill -x awww-daemon`
  silently matches nothing;
- its socket file outlives the process, so the file existing proves nothing.
  Connecting to it is the real check, and `awww img` does that for us — with
  the daemon down it exits 1 with `failed to connect to socket`.

## Measurements

| | |
| --- | --- |
| Per still, clock or weather change only | **11.4 s** (mean over a complete 144-still sub-season, 0 failed) |
| Per still, season or camera change | ~22 s |
| Full 1,544-still run | 13.0 s/still, 334 minutes |
| Warm-up frames | 120, which is essentially the entire per-still cost at ~10 fps |
| WebP q90, lossy | 0.25 MB mean, 0.43 GB for the grid |
| **Debanded near-lossless (default)** | **~1.9 MB mean, 3.2 GB for the grid** |
| Staging PNGs during a run | 10.8 MB each, ~16 GB peak before conversion |
| Conversion | ~1 s of CPU per image, run across all cores |
| Display path | 43 ms end to end; 1.7 s wall / 791 ms CPU as a systemd unit |

Conversion happens after the batch, so a full build needs about 16 GB free on
the way through even though the result is 3.2 GB. An interrupted run leaves
that backlog until the next `mcwall render` converts it.

## Deviations from the original plan

| Plan | Built | Why |
| --- | --- | --- |
| Skyfield for sun times | astral, cross-checked against Skyfield | Skyfield needs a ~17 MB ephemeris at runtime; the display path stays offline and non-blocking |
| `portablemc` or HeadlessMC | Prism's own CLI | Prism 11.1.1 takes `--launch <instance> --profile <account>` and is already authenticated here |
| `swww` | `swww` or `awww`, detected at runtime | nixpkgs renamed the package; both CLIs take the same arguments |
| Gradle from nixpkgs | Gradle wrapper (9.8.2) | Fabric Loom 1.19.3 needs Gradle >= 9.8; nixpkgs has 8.14.4 and 9.4.1 |
| JDK 21 for the mod build | JDK 25 runs Gradle, bytecode targets 21 | Loom 1.19.3 requires a JVM >= 25 to resolve |
| Lossy WebP at q90 | gradfun + near-lossless | q90 contours badly on dark frames; see Banding above |
