"""One CLI over the whole system: init, plan, render, select, apply, verify."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import apply as apply_mod
from . import config as config_mod
from . import deband as deband_mod
from . import instance as instance_mod
from . import producer, scout, session, weather
from .grid import Weather
from .library import Library, Manifest
from .seasons import SubSeason
from .power import read as read_power
from .select import Conditions, Mode1Selector, NoAssetAvailable

log = logging.getLogger("mcwall")


def _load() -> config_mod.Config:
    return config_mod.load()


def _require_location(config: config_mod.Config) -> tuple[float, float]:
    if config.location.latitude is None or config.location.longitude is None:
        raise SystemExit("no location set — run `mcwall init` first")
    return config.location.latitude, config.location.longitude


def _open_library(config: config_mod.Config) -> Library:
    root = config.library.root
    if not (root / "manifest.json").exists():
        raise SystemExit(f"no library at {root} — run `mcwall render` first")
    return Library.open(root)


# ------------------------------------------------------------------ commands


def cmd_init(args) -> int:
    config = _load()
    if args.latitude is not None and args.longitude is not None:
        latitude, longitude, source = args.latitude, args.longitude, "manual"
    else:
        latitude, longitude = weather.detect_location()
        source = "ip"
    config_mod.save_location(latitude, longitude, source)
    print(f"location: {latitude:.4f}, {longitude:.4f} ({source})")
    print(f"written to {config_mod.CONFIG_PATH}")

    resolution = producer.monitor_resolution()
    if resolution and resolution != (config.library.width, config.library.height):
        print(
            f"note: focused monitor is {resolution[0]}x{resolution[1]}, "
            f"library is configured for {config.library.width}x{config.library.height}"
        )
    return 0


def cmd_plan(args) -> int:
    config = _load()
    plan = producer.build_plan(config)
    print(f"grid:    {plan.total} cells ({len(config.ticks())} ticks x 3 weather x 12 sub-seasons)")
    print(f"missing: {len(plan.missing)}")
    if args.json:
        print(json.dumps(plan.jobs, indent=2))
    elif plan.jobs:
        by_world: dict[str, int] = {}
        for job in plan.jobs:
            by_world[job["world"]] = by_world.get(job["world"], 0) + 1
        for world, count in sorted(by_world.items()):
            print(f"  {world}: {count} jobs")
    return 0


def cmd_render(args) -> int:
    config = _load()
    if args.world:
        config = config_mod.replace_render(config, world=args.world)
        print(f"rendering from world: {args.world}")
    power = read_power()
    if not power.on_ac and not (args.force or config.render.allow_on_battery):
        raise SystemExit("on battery — refusing to render (pass --force to override)")

    if args.now:
        latitude, _ = _require_location(config)
        from .seasons import sub_season_at

        here = sub_season_at(datetime.now(timezone.utc), latitude)
        args.sub_season = (args.sub_season or []) + [here.key]
        print(f"current sub-season: {here.key}")

    identity = producer.shaderpack_identity(config)
    plan = producer.build_plan(config, rebuild=args.rebuild)
    # The build directory is keyed on everything that decides the picture, so
    # a changed shaderpack, world, camera or resolution simply lands somewhere
    # new and leaves the old images alone.
    print(f"build:  {plan.manifest.pack_id}")
    if plan.manifest.seed is not None:
        print(f"world:  {plan.manifest.world} (seed {plan.manifest.seed})")

    if args.sub_season:
        wanted = {SubSeason.from_key(key).serene_name.lower() for key in args.sub_season}
        plan.jobs = [job for job in plan.jobs if job["sub_season"] in wanted]
    if args.limit:
        plan.jobs = plan.jobs[: args.limit]
    if not plan.jobs:
        print("library is complete; nothing to render")
        return 0

    print(f"{len(plan.jobs)} jobs queued")

    # Before any writing. The manifest names the active build, so stamping it
    # here would retire the library currently on screen in favour of one with
    # no images in it yet — and a dry run is the one command that promises to
    # leave the disk alone.
    if args.dry_run:
        print("--dry-run: not launching the game")
        return 0

    root = config.library.root
    build = root / plan.manifest.pack_id
    build.mkdir(parents=True, exist_ok=True)
    plan.manifest.rendered_at = datetime.now(timezone.utc).isoformat()
    # Stamp the identity on a library that predates the check, so the next
    # settings change is caught.
    plan.manifest.settings_hash = plan.manifest.settings_hash or identity
    plan.manifest.world = config.render.world
    plan.manifest.seed = producer.world_seed(config)
    # Beside the images, not at the root. The root manifest names the build
    # the display path reads, and a build with holes in it is not one: the
    # library already on screen keeps serving until this one is whole.
    plan.manifest.write(build)

    staging = root / ".staging"

    started = time.monotonic()
    status = producer.run_jobs(
        config, plan.jobs, staging, headless=not args.windowed, force=args.force
    )
    elapsed = time.monotonic() - started
    converted = producer.convert_staging(config, staging)

    captured = status.get("completed", 0)
    if captured:
        print(f"{elapsed / captured:.1f} s per still ({elapsed / 60:.1f} min for {captured})")

    print(
        f"captured {status.get('completed', 0)}, "
        f"already present {status.get('skipped', 0)}, "
        f"failed {status.get('failed', 0)}, "
        f"converted {converted}"
    )

    _promote_if_complete(config, plan.manifest)
    return 0 if status.get("failed", 0) == 0 else 1


def _promote_if_complete(config, manifest) -> bool:
    """Hand the display path a build once it has every image.

    A render writes for hours, and pointing the wallpaper at a half-filled
    directory on the way through means a frozen screen and a timer that fails
    every tick until it finishes. Promotion is therefore the last step, and a
    run that stops early simply leaves the previous library in place.
    """
    root = config.library.root
    library = Library(root, manifest)
    library.rescan()
    missing = library.missing_stills()

    if missing:
        try:
            on_screen = Manifest.read(root).pack_id
        except (OSError, ValueError):
            on_screen = None
        print(
            f"{len(missing)} stills still missing — "
            + (f"{on_screen} stays on screen" if on_screen
               else "nothing is on screen yet")
        )
        return False

    manifest.write(root)
    print(f"library complete — now showing {manifest.pack_id}")
    return True


def cmd_select(args) -> int:
    config = _load()
    latitude, longitude = _require_location(config)
    library = _open_library(config)
    power = read_power()

    observation = weather.current(
        latitude, longitude, config.display.weather_cache_seconds
    )
    conditions = Conditions(weather=observation.weather, on_ac=power.on_ac)
    now = datetime.now(timezone.utc) if args.at is None else datetime.fromisoformat(args.at)

    selector = Mode1Selector(
        library, latitude, longitude, allow_loops=config.display.allow_loops_on_ac
    )
    try:
        choice = selector.select(now, conditions)
    except NoAssetAvailable as error:
        raise SystemExit(str(error))

    if args.json:
        print(json.dumps({
            "path": str(choice.path),
            "kind": choice.kind,
            "exact": choice.exact,
            "cell": choice.cell.as_dict(),
            "wanted": choice.wanted.as_dict() if choice.wanted else None,
            "reasons": list(choice.reasons),
            "weather": observation.weather.value,
            "weather_source": observation.source,
            "on_ac": power.on_ac,
            "battery_percent": power.percent,
        }, indent=2))
    else:
        detail = "exact" if choice.exact else "; ".join(choice.reasons)
        print(f"{choice.path}  [{choice.kind}] {detail}")
    return 0


def cmd_apply(args) -> int:
    config = _load()
    latitude, longitude = _require_location(config)
    power = read_power()

    if power.percent is not None and not power.on_ac:
        if power.percent < config.display.low_battery_percent and not args.force:
            print(f"battery {power.percent}% — leaving the wallpaper alone")
            return 0

    library = _open_library(config)
    observation = weather.current(
        latitude, longitude, config.display.weather_cache_seconds
    )
    selector = Mode1Selector(
        library, latitude, longitude, allow_loops=config.display.allow_loops_on_ac
    )
    try:
        choice = selector.select(
            datetime.now(timezone.utc),
            Conditions(weather=observation.weather, on_ac=power.on_ac),
        )
    except NoAssetAvailable as error:
        raise SystemExit(str(error))

    changed = apply_mod.apply(choice, config.display, force=args.force)
    if changed:
        print(f"wallpaper -> {choice.path}")
    elif args.verbose:
        print("unchanged")
    return 0


def _report(config, world_dir) -> int:
    """Describe a world's shot and how to render it.

    Nothing is written. The camera lives in the world's save, so a world is
    already a complete description of a shot — there is no config to keep in
    step with it.
    """
    camera = session.read_camera(world_dir)
    seed = session.read_seed(world_dir)
    print(f"world:  {world_dir.name}")
    print(f"seed:   {seed}")
    print(f"camera: {camera}")
    print()
    print("render it with:")
    print(f"  mcwall render --world {world_dir.name!r}")
    if world_dir.name == config.render.world:
        print("  (or just `mcwall render` — this is the configured default)")
    return 0


def cmd_setup(args) -> int:
    """Create or refresh the dedicated Prism render instance."""
    return instance_mod.setup(args)


def cmd_edit(args) -> int:
    """Open the render instance as a normal game to set up the shot."""
    config = _load()
    state = producer.game_dir(config) / producer.STATE_SUBDIR

    stray = producer.running_clients(config)
    if stray:
        raise SystemExit(
            f"a game is already running for this instance (pid "
            f"{', '.join(map(str, stray))}); close it first"
        )

    print("opening the instance — fly to a spot, set your framing, then quit.")
    process = producer.launch(config, state, headless=False, capture=False)
    process.wait()
    producer.kill_clients(config)

    world_dir = (
        session.saves_dir(producer.game_dir(config)) / args.world
        if args.world
        else session.latest_world(producer.game_dir(config))
    )
    if world_dir is None or not world_dir.exists():
        raise SystemExit("no world was played; nothing to adopt")

    print()
    try:
        return _report(config, world_dir)
    except session.NoPlayerPosition as error:
        raise SystemExit(str(error))


def cmd_adopt(args) -> int:
    """Show the shot a world holds, without opening the game."""
    config = _load()
    world_dir = (
        session.saves_dir(producer.game_dir(config)) / args.world
        if args.world
        else session.latest_world(producer.game_dir(config))
    )
    if world_dir is None or not world_dir.exists():
        raise SystemExit("no world found in the render instance")
    try:
        return _report(config, world_dir)
    except session.NoPlayerPosition as error:
        raise SystemExit(str(error))


def cmd_scout(args) -> int:
    """Render candidate camera spots and tile them for a human to choose."""
    config = _load()
    located = producer.located_spot(config)
    surface = float(located["surface_y"]) if located else 68.0
    if located:
        print(f"camera anchor: x={located['x']} z={located['z']}, ground at y={surface:.0f}")
    else:
        print("no located.json from a previous run — assuming ground at y=68")
    candidates = scout.default_candidates(surface)
    staging = config.library.root / ".scout"
    jobs = scout.scout_jobs(
        config,
        candidates,
        tick=args.tick,
        sub_season=SubSeason.from_key(args.sub_season),
        weather=Weather(args.weather),
    )
    status = producer.run_jobs(config, jobs, staging, force=args.force)
    print(f"rendered {status.get('completed', 0)} of {len(jobs)} candidates")

    images = sorted((staging / "scout").glob("*.png"))
    if not images:
        raise SystemExit("no candidate frames were produced")
    sheet = scout.contact_sheet(images, config.library.root / "scout-sheet.png")
    print(f"contact sheet: {sheet}")
    print("frames:")
    for image in images:
        print(f"  {image}")
    print("\npick one, then fly there with `mcwall edit` — the world holds the shot")
    return 0


def cmd_probe(args) -> int:
    """Sweep the whole day, then place the time slots by visual change."""
    config = _load()
    staging = config.library.root / ".probe"
    jobs = scout.probe_jobs(config, step=args.step)
    status = producer.run_jobs(config, jobs, staging, force=args.force)
    print(f"rendered {status.get('completed', 0)} of {len(jobs)} probe frames")

    frames = []
    for image in sorted((staging / "probe").glob("*.png")):
        frames.append((int(image.stem), image))
    if len(frames) < args.slots:
        raise SystemExit(f"only {len(frames)} probe frames; need at least {args.slots}")

    ticks = scout.even_change_slots(frames, slots=args.slots)
    print(f"slot ticks ({len(ticks)}): {ticks}")

    if args.write:
        target = scout.write_slot_config(ticks, config_mod.SLOTS_PATH)
        print(f"written to {target} — it is overlaid on the config automatically")
    sheet = scout.contact_sheet(
        [path for _, path in frames][:48], config.library.root / "probe-sheet.png", columns=8
    )
    print(f"contact sheet: {sheet}")
    return 0


def cmd_deband(args) -> int:
    """Repair banding in an existing library, non-destructively."""
    config = _load()
    root = config.library.root
    pack = root / producer.pack_id(config)
    if not pack.is_dir():
        raise SystemExit(f"no rendered images at {pack}")

    staged = root / deband_mod.STAGE_DIR / producer.pack_id(config)
    settings = deband_mod.DebandSettings(
        strength=args.strength, radius=args.radius, near_lossless=args.near_lossless
    )

    before = sum(p.stat().st_size for p in pack.rglob("*.webp"))
    count, written = deband_mod.deband_tree(pack, staged, settings)

    staged_count = len(list(staged.rglob("*.webp")))
    source_count = len(list(pack.rglob("*.webp")))
    if staged_count != source_count:
        raise SystemExit(
            f"refusing to swap: debanded {staged_count} images but the library "
            f"has {source_count}; the originals are untouched"
        )

    print(f"debanded {count} images")
    print(f"  before: {before / 1e9:.2f} GB")
    print(f"  after:  {written / 1e9:.2f} GB")

    if args.dry_run:
        print(f"\n--dry-run: left the new tree in {staged}, library untouched")
        return 0

    backup = deband_mod.swap_in(pack, staged)
    shutil.rmtree(root / deband_mod.STAGE_DIR, ignore_errors=True)
    print(f"\nswapped in. originals kept at {backup}")
    print("delete that directory once you are happy with the result")
    return 0


def cmd_gallery(args) -> int:
    """Contact sheets for the look review: a day per sub-season, plus a strip."""
    config = _load()
    library = _open_library(config)
    out_dir = config.library.root / "gallery"
    weather = Weather(args.weather)

    sheets = scout.sub_season_sheets(library, out_dir, weather)
    print(f"{len(sheets)} per-sub-season sheets in {out_dir}")
    for sheet in sheets:
        print(f"  {sheet.name}")

    strip = scout.season_strip(library, out_dir, args.tick, weather)
    if strip:
        print(f"\nall twelve sub-seasons at tick {args.tick}: {strip}")
    else:
        print(f"\nno images at tick {args.tick} to build the season strip from")
    return 0


def cmd_verify(args) -> int:
    config = _load()
    root = config.library.root
    if not (root / "manifest.json").exists():
        raise SystemExit(f"no library at {root}")
    manifest = Manifest.read(root)
    library = Library(root, manifest)
    library.rescan()

    missing = library.missing_stills()
    print(f"manifest: {manifest.pack_name} @ {manifest.width}x{manifest.height}")
    print(f"cells:    {len(manifest.cells())}")
    print(f"present:  {len(library.stills)}")
    print(f"missing:  {len(missing)}")

    broken = []
    if args.decode:
        from PIL import Image

        for cell in sorted(library.stills, key=lambda c: (c.sub_season.index, c.tick)):
            path = library.path_for(cell)
            try:
                with Image.open(path) as image:
                    image.load()
                    if (image.width, image.height) != (manifest.width, manifest.height):
                        broken.append((path, f"{image.width}x{image.height}"))
            except Exception as error:  # noqa: BLE001
                broken.append((path, str(error)))
        print(f"undecodable or wrong size: {len(broken)}")
        for path, why in broken[:20]:
            print(f"  {path}: {why}")

    return 0 if not missing and not broken else 1


def cmd_tui(args) -> int:
    from .tui.app import run
    from .tui.state import command_specs

    specs = [s for s in command_specs(build_parser()) if s.name != "tui"]
    return run(_load(), specs)


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface, in one place.

    Returned rather than built inline so the TUI can enumerate commands and
    their options from the same definition the CLI parses, instead of keeping
    a second list that drifts.
    """
    parser = argparse.ArgumentParser(prog="mcwall", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="create the Prism render instance")
    setup.add_argument("--name", default="mc-wallpaper")
    setup.add_argument("--width", type=int, default=2880)
    setup.add_argument("--height", type=int, default=1800)
    setup.add_argument("--java", default=os.environ.get("MCWALL_JAVA"))
    setup.add_argument("--render-distance", type=int, default=16)
    setup.add_argument("--force", action="store_true",
                       help="also reset instance.cfg, options.txt and iris.properties")
    setup.add_argument("--no-build", action="store_true",
                       help="do not build the capture mod if it is missing")
    setup.set_defaults(func=cmd_setup)

    init = subparsers.add_parser("init", help="detect location and write config")
    init.add_argument("--latitude", type=float)
    init.add_argument("--longitude", type=float)
    init.set_defaults(func=cmd_init)

    plan = subparsers.add_parser("plan", help="list the grid cells still missing")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    render = subparsers.add_parser("render", help="fill the library in one game run")
    render.add_argument("--limit", type=int, help="render at most N jobs")
    render.add_argument("--world", help="render from this world instead of the configured one")
    render.add_argument(
        "--sub-season",
        action="append",
        metavar="KEY",
        help="restrict to one sub-season, e.g. mid_autumn (repeatable)",
    )
    render.add_argument(
        "--now",
        action="store_true",
        help="shorthand for --sub-season <the one it is right now>",
    )
    render.add_argument("--dry-run", action="store_true")
    render.add_argument("--windowed", action="store_true", help="show the game window")
    render.add_argument("--force", action="store_true", help="render even on battery")
    render.add_argument(
        "--rebuild",
        action="store_true",
        help="re-render everything after a shaderpack settings change",
    )
    render.set_defaults(func=cmd_render)

    select = subparsers.add_parser("select", help="print the asset for right now")
    select.add_argument("--json", action="store_true")
    select.add_argument("--at", help="ISO timestamp instead of now")
    select.set_defaults(func=cmd_select)

    apply_parser = subparsers.add_parser("apply", help="select, then set the wallpaper")
    apply_parser.add_argument("--force", action="store_true")
    apply_parser.set_defaults(func=cmd_apply)

    edit = subparsers.add_parser(
        "edit", help="open the instance to set the shot, then adopt the camera"
    )
    edit.add_argument("--world", help="which save to report (default: last played)")
    edit.set_defaults(func=cmd_edit)

    adopt = subparsers.add_parser(
        "adopt", help="show the shot a world holds, without opening the game"
    )
    adopt.add_argument("--world")
    adopt.set_defaults(func=cmd_adopt)

    scout_parser = subparsers.add_parser("scout", help="render camera candidates to choose from")
    scout_parser.add_argument("--tick", type=int, default=1000)
    scout_parser.add_argument("--sub-season", default="mid_summer")
    scout_parser.add_argument("--weather", default="clear", choices=[w.value for w in Weather])
    scout_parser.add_argument("--force", action="store_true")
    scout_parser.set_defaults(func=cmd_scout)

    probe = subparsers.add_parser("probe", help="sweep the day and re-place the time slots")
    probe.add_argument("--step", type=int, default=100, help="ticks between probe frames")
    probe.add_argument("--slots", type=int, default=48)
    probe.add_argument("--write", action="store_true", help="write the slot list to config")
    probe.add_argument("--force", action="store_true")
    probe.set_defaults(func=cmd_probe)

    deband = subparsers.add_parser(
        "deband", help="repair banding in an existing library (near-lossless)"
    )
    deband.add_argument("--strength", type=float, default=3.0)
    deband.add_argument("--radius", type=int, default=16)
    deband.add_argument("--near-lossless", type=int, default=60)
    deband.add_argument("--dry-run", action="store_true", help="build it but do not swap")
    deband.set_defaults(func=cmd_deband)

    gallery = subparsers.add_parser("gallery", help="contact sheets for the look review")
    gallery.add_argument("--weather", default="clear", choices=[w.value for w in Weather])
    gallery.add_argument("--tick", type=int, default=1583, help="moment for the season strip")
    gallery.set_defaults(func=cmd_gallery)

    verify = subparsers.add_parser("verify", help="check the library against its manifest")
    verify.add_argument("--decode", action="store_true", help="open every image")
    verify.set_defaults(func=cmd_verify)

    tui = subparsers.add_parser(
        "tui", help="watch a render and drive the rest from one window"
    )
    tui.set_defaults(func=cmd_tui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
