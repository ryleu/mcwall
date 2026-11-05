"""One CLI over the whole system: setup, worlds, libraries."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import apply as apply_mod
from . import config as config_mod
from . import instance as instance_mod
from . import producer, session, weather
from .grid import Weather
from .library import Library, Manifest, builds as library_builds
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


def _library_dir(config: config_mod.Config) -> Path:
    """Where the display path reads from.

    A configured `library.path` wins, so a finished render cannot change what
    is on screen; otherwise the root, where a completed build promotes itself.
    """
    return config.library.path or config.library.root


def _open_library(config: config_mod.Config, override=None) -> Library:
    where = Path(override).expanduser() if override else _library_dir(config)
    if not (where / "manifest.json").exists():
        hint = (
            "run `mcwall world render` first"
            if where == config.library.root
            else "check the path, or library.path in the config"
        )
        raise SystemExit(f"no library at {where} — {hint}")
    return Library.open(where)


# ------------------------------------------------------------------ commands


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


def _forced_cell(args, selector, now, conditions):
    """The cell to show, with any hand-set parts replacing the measured ones.

    Overrides compose: whatever is not given still comes from the real sun,
    season and weather, so `--weather thunder` alone shows this moment's sky
    in a storm rather than some arbitrary frame.
    """
    from .grid import Weather, nearest_slot
    from .seasons import SubSeason

    wanted = selector.target_cell(now, conditions)
    if args.weather:
        wanted = replace(wanted, weather=Weather(args.weather))
    if args.season:
        wanted = replace(wanted, sub_season=SubSeason.from_key(args.season))
    if args.tick is not None:
        wanted = replace(
            wanted, tick=nearest_slot(args.tick, selector.library.manifest.ticks)
        )
    return wanted


def cmd_apply(args) -> int:
    config = _load()
    latitude, longitude = _require_location(config)
    power = read_power()

    if not args.dry_run and power.percent is not None and not power.on_ac:
        if power.percent < config.display.low_battery_percent and not args.force:
            print(f"battery {power.percent}% — leaving the wallpaper alone")
            return 0

    library = _open_library(config, args.path)
    observation = weather.current(
        latitude, longitude, config.display.weather_cache_seconds
    )
    conditions = Conditions(weather=observation.weather, on_ac=power.on_ac)
    now = (
        datetime.now(timezone.utc)
        if args.at is None
        else datetime.fromisoformat(args.at)
    )
    selector = Mode1Selector(
        library, latitude, longitude, allow_loops=config.display.allow_loops_on_ac
    )
    try:
        wanted = _forced_cell(args, selector, now, conditions)
        choice = selector.resolve(wanted, on_ac=power.on_ac)
    except NoAssetAvailable as error:
        raise SystemExit(str(error))
    except (KeyError, ValueError) as error:
        raise SystemExit(f"no such season or weather: {error}")

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
        if args.dry_run:
            return 0
    elif args.dry_run:
        detail = "exact" if choice.exact else "; ".join(choice.reasons)
        print(f"{choice.path}  [{choice.kind}] {detail}")
        return 0

    changed = apply_mod.apply(choice, config.display, force=args.force)
    if changed:
        print(f"wallpaper -> {choice.path}")
    elif args.verbose:
        print("unchanged")
    return 0


def cmd_setup(args) -> int:
    """Create the render instance, and settle the location while we are here.

    Location used to be its own command, run once and never again. It is one
    of the two things that must be true before anything works, so it belongs
    in the command you already have to run.
    """
    status = instance_mod.setup(args)

    config = _load()
    known = config.location.latitude is not None
    if args.latitude is not None and args.longitude is not None:
        latitude, longitude, source = args.latitude, args.longitude, "manual"
    elif known and not args.relocate:
        print(f"location: {config.location.latitude:.4f}, "
              f"{config.location.longitude:.4f} (already set)")
        latitude = None
    else:
        latitude, longitude = weather.detect_location()
        source = "ip"

    if latitude is not None:
        config_mod.save_location(latitude, longitude, source)
        print(f"location: {latitude:.4f}, {longitude:.4f} ({source})")

    resolution = producer.monitor_resolution()
    if resolution and resolution != (config.library.width, config.library.height):
        print(
            f"note: focused monitor is {resolution[0]}x{resolution[1]}, "
            f"library is configured for {config.library.width}x{config.library.height}"
        )
    return status


def cmd_world_list(args) -> int:
    """Every save in the render instance, and what each would build."""
    config = _load()
    game = producer.game_dir(config)
    worlds = session.worlds(game)
    if not worlds:
        raise SystemExit(f"no worlds in {game} — run `mcwall world edit` to make one")

    for world in worlds:
        chosen = "*" if world.name == config.render.world else " "
        seed = session.read_seed(world)
        print(f" {chosen} {world.name}")
        print(f"     seed   {seed if seed is not None else '—'}")
        try:
            camera = session.read_camera(world)
            print(f"     camera {camera}")
        except session.NoPlayerPosition:
            print("     camera —  never played in; open it to set the shot")
            continue
        try:
            key = producer.pack_id(config_mod.replace_render(config, world=world.name))
        except producer.RenderError:
            continue
        built = config.library.root / key
        count = len(list(built.rglob("*.webp"))) if built.is_dir() else 0
        print(f"     builds {key}  ({count or 'no'} images)")
    print("\n * is the world `mcwall world render` uses")
    return 0


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


def _check_config() -> int:
    """Report config problems that normal loading only warns about."""
    try:
        config_mod.load(strict=True)
    except ValueError as problem:
        print(f"config: {problem}")
        return 1
    return 0


def cmd_verify(args) -> int:
    """Check a library against its own manifest."""
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

    bad_config = _check_config()
    return 0 if not missing and not broken and not bad_config else 1


def cmd_use(args) -> int:
    """Point the config at a library, or list the ones available."""
    config = _load()
    root = config.library.root
    active = _library_dir(config)
    if active == root:
        try:
            active = root / Manifest.read(root).pack_id
        except (OSError, ValueError):
            active = None

    if args.auto:
        config_mod.update("library", {"path": ""})
        print("following the root manifest again — a completed render decides")
        return 0

    if not args.path:
        print(f"reading from: {active or '(nothing)'}\n")
        for build in library_builds(root, active):
            mark = "*" if build.active else " "
            state = "complete" if build.complete else f"{build.present}/{build.total}"
            if not build.switchable:
                state += "  (no manifest — cannot be selected)"
            print(f" {mark} {build.pack_id:<72} {state}")
        print("\npass a name, or any path holding a manifest.json")
        return 0

    candidate = Path(args.path).expanduser()
    if (candidate / "manifest.json").exists():
        chosen_path, chosen = candidate, None
    else:
        found = library_builds(root, active)
        matches = [b for b in found if b.pack_id == args.path] or [
            b for b in found if args.path in b.pack_id
        ]
        if len(matches) != 1:
            which = ", ".join(b.pack_id for b in matches) or "nothing"
            print(f"{args.path!r} matches {which}")
            return 1
        chosen = matches[0]
        chosen_path = chosen.path
        if not chosen.switchable:
            print(f"{chosen.pack_id} has no manifest, so nothing knows what its "
                  f"images are of; it cannot be selected")
            return 1
        if not chosen.complete and not args.force:
            print(f"{chosen.pack_id} has {chosen.present} of {chosen.total} "
                  f"images; the wallpaper would have gaps. "
                  f"Pass --force to use it anyway.")
            return 1

    # Prove it opens before writing it into the config, so a bad path is an
    # error here rather than a failing timer every five minutes.
    try:
        library = Library.open(chosen_path)
    except (OSError, ValueError) as problem:
        print(f"{chosen_path} is not a readable library: {problem}")
        return 1
    missing = len(library.missing_stills())
    if missing and not args.force:
        print(f"{chosen_path} is missing {missing} images; pass --force to use it")
        return 1

    config_mod.update("library", {"path": str(chosen_path)})
    print(f"now reading from {chosen_path}")
    if missing:
        print(f"warning: {missing} images missing")
    return 0


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
    # Hidden: the shells call this, people do not. Generated from this very
    # parser, so a completion can never offer a command that is not here.
    parser.add_argument("--completion", choices=("bash", "zsh"),
                        help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command")

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
    setup.add_argument("--latitude", type=float, help="skip IP geolocation")
    setup.add_argument("--longitude", type=float)
    setup.add_argument("--relocate", action="store_true",
                       help="detect the location again even if one is set")
    setup.set_defaults(func=cmd_setup)

    world = subparsers.add_parser(
        "world", help="the Minecraft saves renders are taken from"
    )
    world_actions = world.add_subparsers(dest="action", required=True)

    world_list = world_actions.add_parser(
        "list", help="every save, its seed, its camera and what it would build"
    )
    world_list.set_defaults(func=cmd_world_list)

    world_edit = world_actions.add_parser(
        "edit", help="open the game to set the shot"
    )
    world_edit.add_argument("world", nargs="?",
                            help="open straight into this save")
    world_edit.set_defaults(func=cmd_edit)

    world_render = world_actions.add_parser(
        "render", help="fill a library from this world in one game run"
    )
    world_render.add_argument("world", nargs="?",
                              help="render from this save instead of the configured one")
    world_render.add_argument("--limit", type=int, help="render at most N jobs")
    world_render.add_argument(
        "--sub-season",
        action="append",
        metavar="KEY",
        help="restrict to one sub-season, e.g. mid_autumn (repeatable)",
    )
    world_render.add_argument(
        "--now",
        action="store_true",
        help="shorthand for --sub-season <the one it is right now>",
    )
    world_render.add_argument("--dry-run", action="store_true",
                              help="plan the work without starting the game")
    world_render.add_argument("--windowed", action="store_true",
                              help="show the game window")
    world_render.add_argument("--force", action="store_true",
                              help="render even on battery")
    world_render.add_argument(
        "--rebuild",
        action="store_true",
        help="re-render everything after a shaderpack settings change",
    )
    world_render.set_defaults(func=cmd_render)

    library = subparsers.add_parser(
        "library", help="the built libraries, and which one is on screen"
    )
    library_actions = library.add_subparsers(dest="action", required=True)

    library_apply = library_actions.add_parser(
        "apply", help="work out what to show now, and show it"
    )
    library_apply.add_argument("path", nargs="?",
                               help="read this library instead of the configured one")
    library_apply.add_argument("--dry-run", action="store_true",
                               help="print the choice without touching the wallpaper")
    library_apply.add_argument("--json", action="store_true",
                               help="the choice and why it was made")
    library_apply.add_argument("--at", help="pretend it is this ISO instant")
    library_apply.add_argument("--tick", type=int,
                               help="force the time of day (0-23999)")
    library_apply.add_argument("--weather", choices=[w.value for w in Weather],
                               help="force the weather")
    library_apply.add_argument("--season", help="force the sub-season, e.g. mid_winter")
    library_apply.add_argument("--force", action="store_true",
                               help="apply on low battery, and re-send an unchanged image")
    library_apply.set_defaults(func=cmd_apply)

    library_verify = library_actions.add_parser(
        "verify", help="check a library against its manifest"
    )
    library_verify.add_argument("path", nargs="?")
    library_verify.add_argument("--decode", action="store_true",
                                help="open every image")
    library_verify.set_defaults(func=cmd_verify)

    library_use = library_actions.add_parser(
        "use", help="point the config at a library"
    )
    library_use.add_argument("path", nargs="?",
                             help="a build id, part of one, or any path with a manifest")
    library_use.add_argument("--force", action="store_true",
                             help="select it even though images are missing")
    library_use.add_argument("--auto", action="store_true",
                             help="unset library.path and follow completed renders")
    library_use.set_defaults(func=cmd_use)

    tui = subparsers.add_parser(
        "tui", help="watch a render and drive the rest from one window"
    )
    tui.set_defaults(func=cmd_tui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.completion:
        from .completion import render

        print(render(build_parser(), args.completion), end="")
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
