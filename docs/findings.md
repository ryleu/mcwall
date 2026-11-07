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

## Why the stack is pinned to 1.21.4

Nothing in `mods.lock.json` requires it. Every mod in the current set —
fabric-api, sodium, iris, glitchcore, serene-seasons — and Complementary
Unbound all ship builds for 1.21.11 and the 26.x line.

The pin is held by a dependency that is not installed yet. Astrocraft, which
mode 2 needs (`docs/mode2-and-loops.md`), has these builds and no newer:

```
1.20.1 1.20.2 1.20.3 1.20.4 1.20.5 1.20.6 1.21 1.21.1 1.21.4
```

1.21.4 is therefore the newest version on which mode 2 is possible at all.
Moving the stack forward buys maintained mods and costs the next phase, so
the version stays until Astrocraft moves or mode 2 is abandoned.

One consequence is already paid: rrtt217's HDR Mod, which promotes the
framebuffer to `RGBA16F`, publishes 1.20–1.21.1 and 1.21.9–26.x with nothing
between. Getting more than 8 bits out of the game at 1.21.4 is a port, not an
install.

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

**Ten-bit output cannot reach the screen through awww.** The panel is 10-bit
and so is the compositor surface, but the wallpaper daemon between them is
not. `PixelFormat` in swww's IPC is `Bgr | Rgb | Abgr | Argb` and nothing
else, and the compression either side of the socket works on 3- and 4-*byte*
pixels, so a 10-bit image has nowhere to go even once decoded. AVIF is also
absent: upstream puts it behind a non-default `avif = ["image/avif-native"]`
cargo feature needing dav1d, which this build does not enable. A genuine
10-bit path would have to go through `mpvpaper`, which is a different display
path than stills use.

That costs less than it looks. Dithering from float down to 8 bits is the
ordinary case and it works, because there is input precision to spend. The
pass that failed was 8-bit into 8-bit, which has no headroom at all — the
gradient was already gone.

## Capturing in float

`mcwall world render --hdr` promotes the colour attachments to `RGBA16F` and
stages 16-bit PNG. Measured on this camera, midnight, `early_autumn/clear`:

| Source | p1..p99 | 8-bit levels | distinct 16-bit levels |
| --- | --- | --- | --- |
| Live 8-bit library | 15.4..40.0 | 26 | 1,029 |
| **Float capture** | 15.1..40.4 | 26 | **6,486** |

The mod reports `float range <min>..<max>, N% on 8-bit steps` per frame. `0%`
is the proof the promotion applied; a mixin that fails to apply still reads
back and still writes a 16-bit file, and would say `100%`.

**It needs two mixins, not one.** `MainTarget` does not override
`createBuffers` — it allocates from its own private `allocateColorAttachment`
(`MainTarget.java:23`), which a mixin on `RenderTarget` never sees. Promoting
only `RenderTarget` builds, loads, renders and leaves the captured image
exactly as it was.

**Complementary writes above 1.0.** Observed up to 1.0117. The capture clamps
to 0..1, which is right for a tone-mapped final pass, but the headroom is
real and `Stats.max` records it.

**gradfun cannot consume a 16-bit frame.** It has no deep pixel format, so
ffmpeg auto-inserts a conversion — `rgb48be -> fmt:gbrp`, 8 bits — *before*
the filter runs. Feeding the float capture to the existing deband pass
throws the extra bits away and produces what it always did.

**ffmpeg cannot dither an RGB depth reduction at all.** `zscale`'s four
dither modes and every `-sws_dither` setting give byte-identical output to
`d=none` on `rgb48 -> rgb24`; they apply on YUV paths. The `noise` filter
rejects 16-bit input outright. So the quantisation is done in numpy
(`deband.dither_to_8bit`) with triangular noise one 8-bit level wide, under a
fixed seed so `scripts/repeatability.py` still compares equal.

**near-lossless is the wrong encoder for a dithered frame, and the slow one.**
Its whole job is smoothing away low-amplitude noise, which is the dither.
Measured on one 2880x1800 post-sunset frame:

| Encoder | Time | Size | Sky column inside a flat plateau |
| --- | --- | --- | --- |
| `-near_lossless 60` | 3.96 s | 3.7 MB | 30% |
| `-near_lossless 90` | 3.72 s | 3.8 MB | 28% |
| **`-lossless -z 4`** | **1.25 s** | 4.9 MB | **9%** |

Three times faster and it keeps the dither; a third more disk. Deep sources
are therefore encoded truly losslessly, and `near_lossless` now applies only
to the 8-bit repair path.

End to end on the three post-sunset slots, same camera and sub-season, as the
share of a sky column sitting inside a >=4px flat plateau:

| Tick | 8-bit capture + gradfun + near-lossless | Float capture + TPDF + lossless |
| --- | --- | --- |
| 12800 | 49% | **6%** |
| 13050 | 45% | **7%** |
| 13300 | 47% | **7%** |

About 5.1 MB per frame, 3.8 s to convert: roughly 8.8 GB and ten minutes
across twelve cores for a full grid.

**Banding is after sunset, and the measurement that finds it is 1-D.**
Scoring 300x300 windows for "few levels, little texture" ranks twilight
*below* night and misses it entirely — these bands are 50-150px tall, so a
window that size sees a plausible gradient. What finds them is a vertical
column: count runs of identical value. A post-sunset frame from the 8-bit
capture has 27 plateaus of >=4px in 400 rows and spends 45% of the column
inside one. The same frame captured in float has **zero**, which is the
proof the contouring is quantisation and not something the shader drew.

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

**Overcast counts as rain, and so does fog.** The grid has three skies and no
cloudy one, so every code becomes clear, rain or thunder — and the two ways of
being wrong are not equal. A grey afternoon rendered as bright sunshine reads
as broken; rain under a solid overcast reads as weather. Clear keeps WMO 0-2
(clear, mainly clear, partly cloudy); 3-94 is rain; 95-99 is thunder.

So the weather is the most notable sky in the last `RAIN_LOOKBACK_MINUTES`
(30) of the nowcast, which is radar-derived. Spans are stamped with their
start, so one straddling the cutoff still counts; spans starting after now are
forecasts and are ignored. Precipitation with a dry-looking code is rain — the
millimetres win. `past_minutely_15=3&forecast_minutely_15=1` returns five
rows, so this costs no more than the old single-value call.

The cache is 300 s rather than 900 s, matching the display timer, so a shower
does not also wait out a stale entry.

## A build goes on screen only when it is whole

`<root>/manifest.json` names the build the display path reads; a render writes
its own copy to `<root>/<build>/manifest.json` and promotes it only once every
cell is present. Stamping the root manifest up front pointed the wallpaper at
a directory that would not be filled for five hours, which froze the screen on
whatever was last applied and failed the timer every five minutes until the
run finished.

A resumed run reads that per-build manifest back, so `rendered_at` and the
recorded settings hash survive being interrupted.

**The launch profile comes from Prism.** `--profile` used to be a constant in
`config.py` — one developer's account name, correct on exactly one machine.
It is now Prism's own active account, or the only one it has, with
`render.prism_account` pinning it when that matters. Several accounts and
none active is an error naming them rather than a guess, because guessing
launches the game as the wrong person.

Pinning is worth doing once a world holds playerdata for an account: the
rendered viewpoint does not depend on who logs in, since the mod teleports to
a camera mcwall supplies, but `level.dat`'s `Player` tag tracks whoever saved
last and that tag is where the camera is read from.

## What the package carries, and what it does not

The wrapper puts `cage`, `wlr-randr`, `ffmpeg`, `cwebp`, `mpvpaper` and
`pkill` on `PATH`: mcwall shells out to all of them, and a devShell that
happens to provide them hides their absence from every test. `prismlauncher`
is deliberately left out — the render drives the user's own authenticated
launcher, and a second copy in mcwall's closure could shadow it.

`missing_render_tools` checks the lot before the game starts, because a
missing binary otherwise surfaces as a bare `FileNotFoundError` from inside
`subprocess.Popen`, on a command that has already reported its job count.

## The wallpaper daemon

`mcwall library apply` fails rather than starting a daemon. `mcwall.service` is a
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

Conversion runs *during* the batch, on half the cores, and sweeps up whatever
is left when the game quits. The mod writes each frame to `.part` and renames
it atomically, so a PNG on disk is always whole and converting beside a live
run is safe. Spreading the work costs the same CPU but keeps staging down to
whatever the encoder has not caught up with, instead of the ~16 GB a full grid
used to hold, and leaves almost no tail once the game exits. The other half of
the cores stays with the game: chunk generation is CPU-bound, and a capture
waiting on terrain costs eleven seconds while an encode waiting its turn costs
about one. An interrupted run still leaves its backlog for the next
`mcwall world render`.

## Deviations from the original plan

| Plan | Built | Why |
| --- | --- | --- |
| Skyfield for sun times | astral, cross-checked against Skyfield | Skyfield needs a ~17 MB ephemeris at runtime; the display path stays offline and non-blocking |
| `portablemc` or HeadlessMC | Prism's own CLI | Prism 11.1.1 takes `--launch <instance> --profile <account>` and is already authenticated here |
| `swww` | `swww` or `awww`, detected at runtime | nixpkgs renamed the package; both CLIs take the same arguments |
| Gradle from nixpkgs | Gradle wrapper (9.8.2) | Fabric Loom 1.19.3 needs Gradle >= 9.8; nixpkgs has 8.14.4 and 9.4.1 |
| JDK 21 for the mod build | JDK 25 runs Gradle, bytecode targets 21 | Loom 1.19.3 requires a JVM >= 25 to resolve |
| Lossy WebP at q90 | gradfun + near-lossless | q90 contours badly on dark frames; see Banding above |
