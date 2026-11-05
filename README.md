# mcwall — a Minecraft sky as your Hyprland wallpaper

The wallpaper is a still rendered in Minecraft with shaders, picked to match the
real sky right now: the local time of day, the local weather, and the season.
The game never runs while you are using the machine. It runs once to build a
library of 1,728 stills; after that the display path is a few milliseconds of
Python every five minutes.

```
        PRODUCER (runs once)                    DISPLAY (every 5 minutes)

  mcwall world render ──► job manifest           mcwall library apply
        │                                            │                 │
        ▼                                            │                 ▼
  one headless Minecraft run                         │            swww / awww
  (cage + Prism + Iris + Serene Seasons)             │
        │                                            │
        ▼                                            │
   library/<pack>/<sub-season>/<weather>/<tick>.webp ┘
   library/manifest.json
```

Producer and display meet only at the library. Nothing on the display side
knows the images came from Minecraft, which is what lets a future "mode 2"
producer (accurate sky mods, rendered on another machine) drop in unchanged.

## The grid

1,728 stills = **3 weather** x **48 time slots** x **12 sub-seasons**.

- **Weather** — `clear`, `rain`, `thunder`, mapped from Open-Meteo's WMO codes.
  Snow has no slot of its own: Serene Seasons turns winter precipitation into
  snowfall, so real winter rain renders as snow.
- **Time** — 48 slots, bunched where the sky moves fastest. Two 3,000-tick
  twilight windows sit centred on vanilla's actual horizon crossings, which are
  at ticks **23215** and **12785**, not 0 and 12000.
- **Season** — each astronomical season (solstice or equinox to the next) split
  into equal thirds, giving early/mid/late x 4. The southern hemisphere is
  shifted six months.

## Matching the real sun

The hard requirement: the sun crosses the horizon at the real local time.

Vanilla's sun angle is a warped function of the tick, so six real instants per
day are pinned to the vanilla ticks at the *same sun elevation* — civil dawn,
sunrise, solar noon, sunset, civil dusk, solar midnight — and the mapping
interpolates between them. Seasonal day length is absorbed because the real
anchors move while the ticks stay put.

Both halves are checked rather than assumed: the tick side is solved from
`DimensionType.timeOfDay` in decompiled 1.21.4, and the real side is
cross-checked against Skyfield's ephemeris. See `docs/findings.md`.

## The flow

```sh
mcwall setup                 # instance, mods, shaderpack — builds the capture mod if needed
mcwall world edit            # open the game: pick a world, a spot, your settings
mcwall world render <name>   # render from that world, headless
```

`mcwall setup` builds the capture mod itself when it is missing. That build is
wrapped by nix but is not a pure derivation — Fabric Loom downloads the game,
its mappings and the Fabric libraries while it runs, and decompiles them, none
of which a sandbox allows. Nix supplies the toolchain and one command:

```sh
nix run .#build-mod          # rebuild the mod on its own
mcwall setup --no-build      # install whatever is already built
```

The instance is named `mc-wallpaper` and marked with a `.mcwall-managed`
file. Nothing outside it is ever written to — your own instances, worlds and
launcher profiles are not touched.

### Setting the shot

`mcwall world edit` opens the render instance as an ordinary game. Create a world
with whatever seed you like, fly where you want, set the framing, tune the
shaderpack in Iris's options, then quit. The shot is wherever you were
standing: Minecraft saves the player's position and rotation on quit, so
`mcwall world render` reads the camera straight out of that world's `level.dat`.

Nothing is written back to config. To render from a different spot, fly
somewhere else and quit; to render from a different world, pass
`--world <name>`. The config file never says where the camera is.

The capture mod only runs when the orchestrator sets `MCWALL_CAPTURE=1`, which
it does only for a headless render. Opening the instance by hand — from
`mcwall world edit` or from Prism directly — is always just the game, even with a
job manifest sitting there from the last render.

The TUI's worlds view lists every save with its seed and camera, so you can
see what each would render as without opening the game.

`locate_biome` is empty by default, because the biome search replaces the
camera's x and z and would discard the framing you just set. Name biomes in it
only if you would rather have the camera placed for you.

Your in-game video settings survive: `mcwall setup` will not overwrite an
existing `options.txt` or `iris.properties` unless you pass `--force`.

### Where the images go

Each build gets its own directory, named after what produced it:

```
~/.local/share/mcwall/library/new-world-69420018030897796-complementaryunbound-r5.9.3-76496f5866
                              ^world      ^seed             ^shaderpack            ^the rest
```

The trailing hash covers the shaderpack and its settings, the seed, the
camera, the resolution and the render distance — everything that changes what
the pictures look like. Change any of them and the next render goes to a new
directory, so builds never overwrite each other and you never have to edit a
config file to keep one. Settings that only affect *how* a frame is reached,
like warm-up frames, do not fork the library.

A build in progress keeps its manifest beside its own images, and only
replaces the root one — the build the display path reads — once every image
is present. So a render can run for hours without disturbing the wallpaper
that is already up, and a run that stops early leaves it alone entirely.

This is the rule the config file follows: it changes mcwall's behaviour at
runtime, never what gets built. The world, its seed and the camera all come
from the save itself, and the manifest records them so a build can be traced
back to a reproducible starting point.

Winter renders in a snow-covered variant, and that variant is a **copy** of
the world you chose rather than a fresh generation. Generating it would only
match if both used the same seed, which stops being true the moment a world is
made by hand — and a quarter of the library showing different terrain is the
kind of mistake you find months later.

### One window for all of it

```sh
mcwall tui
```

Four views, switched with `d` `w` `c` `o`:

- **dashboard** — which build is being made and which one is on screen, the
  camera it is shot from, a progress bar, pace, ETA, the job running now with
  the one before and the next few, and a thumbnail of the last capture
  (`i` toggles it).
- **worlds** — every save with its seed and camera. `enter` picks the one to
  render from, `e` opens it in the game to move the shot.
- **commands** — every `mcwall` command, read from the CLI's own parser, so
  the list cannot drift. `enter` opens its options, `space` toggles a flag,
  `x` runs it. The exact command line is shown before it runs.
- **output** — what the running command is printing. `x` stops it; a render
  is resumable, so stopping costs only the still in flight.

It is safe to open while a render is going: the TUI only reads, and the
render it watches is one it does not own.

### Building the library

```sh
mcwall world render --dry-run   # what is missing, without starting the game
mcwall world render             # one headless game run fills it
mcwall library verify --decode  # every cell present and decodable
```

`render` is resumable: outputs are written with an atomic rename and existing
ones are skipped, so killing it mid-batch and rerunning costs nothing. It
refuses to start on battery, and stops cleanly between jobs if the power is
pulled mid-run. `--sub-season mid_autumn` (or `--now`) renders one season's 144
stills if you want something usable before the whole grid is done.

Measured on an AMD Radeon 840M at 2880x1800, render distance 16, with
Complementary Unbound: **11.4 s per still**, so the whole grid is about
**5.5 hours**. Warm-up frames are nearly the entire per-still cost.

Images are debanded and written near-lossless, which costs about 8x the disk
but is the only thing that fixes contouring in the dark frames — see
[Banding](docs/findings.md#banding). The grid lands at **~3.2 GB**, roughly
1.9 MB per image. Set `[library].deband = false` for plain lossy WebP at
~0.43 GB, with visible banding at night.

Captures are encoded during the run on half the cores, so `library/.staging`
only ever holds what the encoder has not caught up with rather than the whole
grid of lossless PNGs.

## Commands

Three groups, because the nouns are the thing: the program, the worlds you
render from, and the libraries you render into.

```
mcwall setup                 # instance, mods, shaderpack, and your location
mcwall world list            # every save, its seed, its camera, what it built
mcwall world edit [name]     # open the game to set the shot
mcwall world render [name]   # fill a library from that world
mcwall library apply [path]  # work out what to show now, and show it
mcwall library verify [path] # check a library against its manifest
mcwall library use <path>    # point the config at a library
mcwall tui                   # all of the above, in one window
```

`library apply` takes `--dry-run` to print the choice without setting
anything, `--json` to see why it chose that cell, and `--at`, `--tick`,
`--weather` and `--season` to override parts of the decision. Whatever you do
not override still comes from the real sun, season and sky, so
`--weather thunder` shows this exact moment in a storm.

Tab completion for bash and zsh is installed with the package. It is generated
from the parser the CLI itself uses, so it cannot offer a command that does
not exist:

```sh
mcwall --completion zsh > ~/.zfunc/_mcwall    # if you are not installing the package
```

## Showing it

```sh
mcwall library apply --dry-run --json   # what would be shown, and why
mcwall library apply                    # set the wallpaper
```

Install the timer with the home-manager module:

```nix
{
  inputs.mcwall.url = "path:/home/ryleu/Code/minecraft-wallpaper";

  # in your home-manager configuration:
  imports = [ inputs.mcwall.homeManagerModules.default ];
  services.mcwall.enable = true;
}
```

`mcwall library apply` fails rather than starting a wallpaper daemon itself. It has to:
the unit is a `oneshot`, and systemd's default `KillMode=control-group` reaps
everything in the cgroup when it exits, so a daemon started from inside would
be killed seconds later and every tick would start a fresh one.

So the module needs to know which unit runs your daemon, and it works that out
itself:

| Your configuration | What mcwall requires |
| --- | --- |
| `services.awww.enable = true` | `awww.service` |
| `services.swww.enable = true` | `awww.service` — home-manager renamed the option, it is the same module |
| `services.mcwall.daemonUnit = "…"` | whatever you name |
| none of the above | **the configuration fails to evaluate** |

That last row is deliberate. Installing a timer that errors every five minutes
is worse than refusing to build, and the assertion says exactly what to add.

### Power policy

| State | Behaviour |
| --- | --- |
| On AC | Loops when one exists for the cell, otherwise stills |
| On battery | Stills only; any running `mpvpaper` is stopped |
| Below 20% | The wallpaper is left alone; `apply` returns before any work |

### Fallback order

Exact cell, then weather (`thunder` → `rain` → `clear`), then the nearest time
slot. Season is never substituted — a summer sky in December is more wrong than
a clear sky during a shower. Selection never blocks and never launches the game;
if the weather fetch fails it uses the last cached reading, then clear.

## Layout

| Path | What |
| --- | --- |
| `mcwall/` | Orchestrator and display path (pure Python, unit-tested) |
| `mcwall/mctime.py` | Vanilla sky geometry; no I/O, no game |
| `mcwall/sun.py` | Real sun times and the real-moment → tick map |
| `mcwall/select.py` | The whole display-time decision, as a pure function |
| `mcwall/producer.py` | Prism, cage and Hyprland — all the machine-specific parts |
| `mod/` | The Fabric capture mod (Gradle, wrapper-pinned) |
| `nix/home-manager.nix` | The user timer |
| `mcwall/instance.py` | Creates the Prism instance; driven by `mcwall setup` |
| `scripts/` | Mod build, mod pinning, source unpacking, test helpers |
| `docs/` | Verified facts, testing state, and what is deliberately not built |

## Tests

```sh
nix flake check              # unit tests, no network, no game
pytest -m network            # the Skyfield cross-check as well
```
