# mcwall — a Minecraft sky as your Hyprland wallpaper

The wallpaper is a still rendered in Minecraft with shaders, picked to match the
real sky right now: the local time of day, the local weather, and the season.
The game never runs while you are using the machine. It runs once to build a
library of 1,728 stills; after that the display path is a few milliseconds of
Python every five minutes.

```
        PRODUCER (runs once)                    DISPLAY (every 5 minutes)

  mcwall plan ──► job manifest                 mcwall select ──► mcwall apply
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
cross-checked against Skyfield's ephemeris. See `docs/phase0-findings.md`.

## Setup

```sh
nix develop                                  # devShell with every tool pinned
python3 scripts/setup_instance.py            # dedicated Prism instance + mods
(cd mod && ./gradlew build)                  # the capture mod
python3 scripts/setup_instance.py            # again, to install the built mod
mcwall init                                  # detect location, write config
```

The instance is named `mc-wallpaper` and is marked with a `.mcwall-managed`
file. Nothing outside it is ever written to — your own instances, worlds and
launcher profiles are not touched.

## Building the library

```sh
mcwall scout                 # render camera candidates, pick one by looking
mcwall probe --write         # place the 48 time slots by measured sky change
mcwall plan                  # what is missing
mcwall render                # one headless game run fills it
mcwall verify --decode       # every cell present and decodable
```

The camera is anchored automatically in a biome whose foliage actually changes
with the season — `forest`, `flower_forest`, `birch_forest`, `dark_forest`,
`plains` or `meadow`, whichever is nearest. That matters more than it sounds:
world spawn here was spruce taiga, and Serene Seasons barely recolours
conifers, so a taiga camera would make all twelve sub-seasons look identical
apart from winter snow. Set `[render].locate_biome = []` to pin the camera
exactly where you put it instead.

Captures are gated on terrain actually being ready — the server's chunk queue
empty, and the client's loaded-chunk and rendered-section counts both steady —
not on a frame count. At render distance 16 a fixed count captured fog where
terrain should be.

`render` is resumable: outputs are written with an atomic rename and existing
ones are skipped, so killing it mid-batch and rerunning costs nothing. It
refuses to start on battery, and stops cleanly between jobs if the power is
pulled mid-run. `--sub-season mid_autumn` (or `--now`) renders one season's 144
stills if you want something usable before the whole grid is done.

Measured on an AMD Radeon 840M at 2880x1800, render distance 16, with
Complementary Unbound: **11.4 s per still** over a full 144-still sub-season,
so the whole grid is about **5.5 hours**. The images come out at **0.25 MB on
average** (0.06 MB for a dark night frame, 0.89 MB for a bright rainy one), so
the full library is **~0.43 GB** at quality 90.

Budget disk for the run, not just the result: the mod writes lossless PNGs into
`library/.staging` and they are only converted once the batch finishes, so a
full build needs about **16 GB free** on the way through.

## Showing it

```sh
mcwall select --json         # what would be shown, and why
mcwall apply                 # set the wallpaper
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
| `scripts/` | Instance setup, mod pinning, source unpacking |
| `docs/` | Phase findings and verified assumptions |

## Tests

```sh
nix flake check              # unit tests, no network, no game
pytest -m network            # the Skyfield cross-check as well
```
