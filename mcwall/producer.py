"""The producer side: plan a batch, run the game once, convert the output.

Everything machine-specific lives here — Prism, cage, Hyprland's monitor list.
The job manifest and the library it writes are plain data, so a mode 2 producer
on another host can replace this file and nothing downstream changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .library import GridCell, Library, Manifest
from .power import read as read_power

log = logging.getLogger(__name__)

PRISM_DIRS = [
    Path.home() / ".local/share/PrismLauncher",
    Path.home() / ".var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher",
]
STATE_SUBDIR = "mcwall"


class RenderError(Exception):
    pass


def prism_root() -> Path:
    for candidate in PRISM_DIRS:
        if (candidate / "accounts.json").exists():
            return candidate
    raise RenderError("no Prism Launcher data directory found")


def prism_accounts() -> list[tuple[str, bool]]:
    """Every Minecraft profile Prism knows, and which one is active."""
    try:
        raw = json.loads((prism_root() / "accounts.json").read_text())
    except (OSError, ValueError) as problem:
        raise RenderError(f"could not read Prism's accounts.json: {problem}") from problem

    found = []
    for account in raw.get("accounts", []):
        name = (account.get("profile") or {}).get("name")
        if name:
            found.append((name, bool(account.get("active"))))
    return found


def prism_account(config: Config) -> str:
    """Which profile to launch the game as.

    Configured wins; otherwise Prism's own active account, and failing that
    the only one there is. The name used to be a constant in this file — one
    developer's account, which was correct on exactly one machine.
    """
    if config.render.prism_account:
        return config.render.prism_account

    accounts = prism_accounts()
    if not accounts:
        raise RenderError(
            "Prism has no Minecraft accounts; log in to one, or set "
            "prism_account in the config"
        )
    active = [name for name, is_active in accounts if is_active]
    if active:
        return active[0]
    if len(accounts) == 1:
        return accounts[0][0]
    raise RenderError(
        "Prism has no active account and several to choose from ("
        + ", ".join(name for name, _ in accounts)
        + "); set prism_account in the config"
    )


def instance_dir(config: Config) -> Path:
    path = prism_root() / "instances" / config.render.prism_instance
    if not (path / ".mcwall-managed").exists():
        raise RenderError(
            f"{path} is not an mcwall-managed instance; run scripts/setup_instance.py"
        )
    return path


def game_dir(config: Config) -> Path:
    return instance_dir(config) / ".minecraft"


def monitor_resolution() -> tuple[int, int] | None:
    """Native pixel size of the focused Hyprland monitor, if we are on one."""
    try:
        output = subprocess.run(
            ["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5
        )
        monitors = json.loads(output.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for monitor in monitors:
        if monitor.get("focused"):
            return int(monitor["width"]), int(monitor["height"])
    return None


# --------------------------------------------------------------------- plan


@dataclass
class Plan:
    """Which cells are missing, and the jobs that would fill them."""

    manifest: Manifest
    missing: list[GridCell]
    jobs: list[dict]

    @property
    def total(self) -> int:
        return len(self.manifest.cells())


def world_for(cell: GridCell, config: Config) -> str:
    """Winter sub-seasons render in the snow-covered copy of the template."""
    if cell.sub_season.season.value == "winter":
        return f"{config.render.world}-snow"
    return config.render.world


def shaderpack_identity(config: Config) -> str:
    """Hash of the shaderpack and its settings.

    A library is only comparable with itself: change the pack's settings and
    every image in it is subtly wrong. A different *pack* lands in a different
    `pack_id` directory on its own, so this only has to catch settings changes
    within one pack.
    """
    digest = hashlib.sha256()
    try:
        game = game_dir(config)
    except RenderError:
        # No render instance on this machine. Planning and the whole display
        # path still work against an existing library; only the staleness
        # check needs the pack itself.
        return ""
    digest.update(config.render.shaderpack.encode())

    pack = game / "shaderpacks" / config.render.shaderpack
    if pack.exists():
        digest.update(str(pack.stat().st_size).encode())
        with pack.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)

    # The pack's own option overrides — this is what "the pack's settings"
    # actually means, and what changes the look of every image. Iris names
    # this after the pack entry as it appears in the folder, so for a zip it
    # keeps the extension ("Pack.zip.txt"); hash both spellings rather than
    # guess, or a settings change could slip through unnoticed.
    for name in (
        f"{config.render.shaderpack}.txt",
        f"{Path(config.render.shaderpack).stem}.txt",
    ):
        digest.update(normalise_properties(game / "shaderpacks" / name))

    # From Iris's own config, only the two keys that decide what gets
    # rendered. The rest is bookkeeping that churns on its own: Iris fills in
    # defaults when the game exits and setup_instance.py writes the file back
    # to its minimal form, so hashing the whole file would mark the library
    # stale after almost every run.
    iris = dict(_properties(game / "config/iris.properties"))
    for key in ("shaderPack", "enableShaders"):
        digest.update(f"{key}={iris.get(key, '')}".encode())

    return digest.hexdigest()[:16]


def _properties(path: Path) -> list[tuple[str, str]]:
    """Parse a .properties file into key/value pairs, ignoring comments."""
    pairs = []
    for line in normalise_properties(path).decode().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            pairs.append((key.strip(), value.strip()))
    return pairs


def normalise_properties(path: Path) -> bytes:
    """A .properties file reduced to its meaning: sorted settings, no comments."""
    try:
        text = path.read_text()
    except OSError:
        return b""
    settings = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        settings.append(line)
    return "\n".join(sorted(settings)).encode()


def render_camera(config: Config) -> tuple[float, float, float, float, float]:
    """Where the shot is taken from: the world's own saved player position.

    The world is the source of truth, so changing the shot means flying
    somewhere and quitting rather than editing a config file. A world that has
    never been opened has no camera, and that is worth saying plainly instead
    of rendering 1,728 pictures of wherever spawn happened to be.
    """
    from . import session

    world = session.saves_dir(game_dir(config)) / config.render.world
    try:
        return session.read_camera(world).as_tuple()
    except session.NoPlayerPosition as error:
        raise RenderError(
            f"{error}\n"
            f"`mcwall edit` opens the instance so you can set the shot."
        ) from error


def world_slug(name: str) -> str:
    """A world name reduced to something safe in a path."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "world"


def library_identity(config: Config) -> str:
    """Hash of everything that decides what the images look like.

    The shaderpack and its settings, the world's seed, where the camera
    stands and which way it points, the resolution, and how far it can see.
    Change any of them and the pictures change, so they belong in the
    directory name rather than in a warning: a different build lands in a
    different directory, the old one is left alone, and `plan` cannot mistake
    stale images for finished work.

    Warm-up frames are deliberately absent. They decide how settled a frame
    is, not what it is of, and tuning them should not fork the library.
    """
    digest = hashlib.sha256()
    digest.update(shaderpack_identity(config).encode())
    digest.update(str(world_seed(config)).encode())
    digest.update(",".join(f"{v:.3f}" for v in render_camera(config)).encode())
    digest.update(f"{config.library.width}x{config.library.height}".encode())
    digest.update(str(config.render.render_distance).encode())
    return digest.hexdigest()[:10]


def pack_id(config: Config) -> str:
    """Directory name describing how this build was made.

    World, seed and shaderpack are in the name so a shelf of libraries can be
    told apart at a glance; the hash on the end covers the rest of the
    identity — the camera, the pack's settings, the resolution and the render
    distance — which would be unreadable spelled out.
    """
    seed = world_seed(config)
    parts = [world_slug(config.render.world)]
    if seed is not None:
        parts.append(str(seed))
    parts += [config.pack_stem, library_identity(config)]
    return "-".join(parts)


def migrate_legacy_build(config: Config, key: str) -> bool:
    """Move a pre-identity build directory onto its computed key.

    Older libraries were keyed on the shaderpack alone. Renaming is only safe
    when the recorded shaderpack settings still match, which is the one part
    of the identity those libraries did track.
    """
    root = config.library.root
    legacy = root / config.pack_stem
    target = root / key
    if not legacy.is_dir() or target.exists():
        return False

    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return False
    recorded = json.loads(manifest_path.read_text()).get("settings_hash")
    if recorded != shaderpack_identity(config):
        log.warning(
            "leaving %s alone: its shaderpack settings (%s) are not the current "
            "ones, so it was not rendered with this configuration",
            legacy.name, recorded,
        )
        return False

    legacy.rename(target)
    # The manifest names the directory it describes, so the rename has to
    # carry it. Leaving the two out of step blinds the display path until
    # the next full render writes a manifest — and a dry run never does.
    stored = Manifest.read(root)
    stored.pack_id = key
    stored.write(root)
    log.info("moved %s to %s", legacy.name, target.name)
    return True


class StaleLibrary(Exception):
    """The library on disk was rendered with different shaderpack settings."""


def build_plan(config: Config, settings_hash: str = "", rebuild: bool = False) -> Plan:
    """Compare the library against the grid and emit jobs for the gaps.

    Jobs are ordered season -> world -> weather -> tick, so the expensive state
    change (a season, which needs a full chunk rebuild) happens least often.
    With ``rebuild`` every cell counts as missing, whatever is on disk.
    """
    root = config.library.root
    key = pack_id(config)
    migrate_legacy_build(config, key)

    manifest_path = root / "manifest.json"
    manifest = None

    # A build in progress keeps its record beside its own images, so a
    # resumed run carries on with the same one rather than restamping it.
    if (root / key / "manifest.json").exists():
        try:
            stored = Manifest.read(root / key)
            if stored.pack_id == key:
                manifest = stored
        except (OSError, ValueError):
            manifest = None

    if manifest is None and manifest_path.exists():
        stored = Manifest.read(root)
        # A manifest describes one build. If the configuration now points at a
        # different one, start fresh rather than measuring the new build
        # against the old build's images.
        if stored.pack_id == key:
            manifest = stored
        else:
            log.info("configuration changed: %s -> %s", stored.pack_id, key)
    if manifest is None:
        manifest = Manifest.from_config(
            config,
            settings_hash or shaderpack_identity(config),
            pack_id=key,
            camera=render_camera(config),
        )

    library = Library(root, manifest)
    library.rescan()
    missing = manifest.cells() if rebuild else library.missing_stills()

    suffix = ".png"  # the mod always writes lossless PNG; we convert after
    jobs = []
    for cell in sorted(
        missing,
        key=lambda c: (c.sub_season.index, c.weather.value, c.tick),
    ):
        jobs.append(
            {
                "id": f"{cell.sub_season.key}/{cell.weather.value}/{cell.tick}",
                "sub_season": cell.sub_season.serene_name.lower(),
                "world": world_for(cell, config),
                "tick": cell.tick,
                "weather": cell.weather.value,
                "output": str(cell.relative_path(manifest.pack_id, suffix)),
            }
        )
    # Group by world so the batch loads each world variant once.
    jobs.sort(key=lambda j: (j["world"], j["sub_season"], j["weather"], j["tick"]))
    return Plan(manifest, missing, jobs)


# ------------------------------------------------------------------- render


def write_batch(config: Config, jobs: list[dict], staging: Path) -> Path:
    """Drop the manifest and run config where the mod will find them."""
    state = game_dir(config) / STATE_SUBDIR
    state.mkdir(parents=True, exist_ok=True)
    for stale in ("STOP", "DONE", "status.json"):
        (state / stale).unlink(missing_ok=True)

    with (state / "jobs.jsonl").open("w") as handle:
        for job in jobs:
            handle.write(json.dumps(job) + "\n")

    x, y, z, yaw, pitch = render_camera(config)
    run = {
        "output_root": str(staging),
        "warmup_frames": config.render.warmup_frames,
        # Upper bound only: the real gate is the mod's terrain-readiness check.
        "season_settle_frames": config.render.job_timeout_seconds * 20,
        "terrain_stable_ticks": config.render.terrain_stable_ticks,
        "render_distance": config.render.render_distance,
        "job_timeout_ticks": config.render.job_timeout_seconds * 20,
        "quit_when_done": True,
        "snow_soak_ticks": config.render.snow_soak_ticks,
        "snow_random_tick_speed": config.render.snow_random_tick_speed,
        "snow_season": config.render.snow_season,
        "base_world": config.render.world,
        "camera": {"x": x, "y": y, "z": z, "yaw": yaw, "pitch": pitch},
    }
    if config.render.locate_biome:
        run["locate_biome"] = {
            "radius": config.render.locate_radius,
            "prefer": list(config.render.locate_biome),
        }
    (state / "run.json").write_text(json.dumps(run, indent=2))
    return state


def ensure_world_variants(config: Config, jobs: list[dict]) -> None:
    """Make every world a job names exist, before the client looks for it.

    A variant is a copy of the base world rather than a fresh generation, so
    its terrain matches whatever seed the base world was created with. The
    copy happens here, with no client running, because a save cannot safely be
    duplicated underneath a loaded world.
    """
    from . import session

    saves = session.saves_dir(game_dir(config))
    base = saves / config.render.world
    wanted = {job["world"] for job in jobs} - {config.render.world}

    for name in sorted(wanted):
        target = saves / name
        if target.exists():
            continue
        if not base.exists():
            # The base world does not exist yet either; the mod creates it on
            # first run and the variant is derived on the next.
            log.warning("cannot derive %s: %s does not exist yet", name, base.name)
            continue
        log.info("deriving %s from %s so the terrain matches", name, base.name)
        session.derive_world(base, target)


def world_seed(config: Config) -> int | None:
    """The seed of the render world, read from its save."""
    from . import session

    return session.read_seed(session.saves_dir(game_dir(config)) / config.render.world)


def located_spot(config: Config) -> dict | None:
    """Where the last run's biome search put the camera, if it ran."""
    path = game_dir(config) / STATE_SUBDIR / "located.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# Runs *inside* cage, as cage's single client.
#
# wlroots' headless backend always starts its output at 1280x720, and cage
# fullscreens its client onto that output. Shaders make offscreen capture at
# arbitrary sizes unreliable, so the output is resized to the exact capture
# resolution first and the game renders at native size.
#
# Kept here rather than as a file in scripts/: the installed package has no
# scripts/ directory, so a path relative to the source tree works from a git
# checkout and fails everywhere else — including under the home-manager unit.
HEADLESS_HELPER = """#!/usr/bin/env bash
set -euo pipefail

width="$1"
height="$2"
shift 2

output="$(wlr-randr 2>/dev/null | sed -n 's/^\\(HEADLESS-[0-9]*\\) .*/\\1/p' | head -1)"
output="${output:-HEADLESS-1}"

if ! wlr-randr --output "$output" --custom-mode "${width}x${height}"; then
    echo "mcwall: could not resize $output to ${width}x${height}" >&2
    exit 1
fi

exec "$@"
"""


def write_headless_helper(state: Path) -> Path:
    """Drop the in-cage helper beside the batch state and make it runnable."""
    helper = state / "headless.sh"
    helper.write_text(HEADLESS_HELPER)
    helper.chmod(0o755)
    return helper


def cage_command(helper: Path, width: int, height: int, inner: list[str]) -> list[str]:
    """Wrap a command in a headless cage sized to the capture resolution."""
    return ["cage", "--", "bash", str(helper), str(width), str(height), *inner]


def launch(
    config: Config, state: Path, headless: bool = True, capture: bool = True
) -> subprocess.Popen:
    """Start one game run.

    With ``capture`` the mod picks up the job manifest, works through it and
    quits. Without it the game opens as an ordinary client.
    """
    width, height = config.library.width, config.library.height
    # No --world: the mod opens (and, the first time, creates) the world named
    # in each job, so one run can walk several world variants.
    prism = [
        "prismlauncher",
        "--launch",
        config.render.prism_instance,
        "--profile",
        prism_account(config),
    ]
    helper = write_headless_helper(state)
    command = cage_command(helper, width, height, prism) if headless else prism

    environment = dict(os.environ)
    environment.update(
        {
            "WLR_BACKENDS": "headless",
            "WLR_LIBINPUT_NO_DEVICES": "1",
            "MCWALL_STATE": str(state),
        }
    )
    # The capture mod only runs when this is set, so opening the instance by
    # hand is always just the game.
    if capture:
        environment["MCWALL_CAPTURE"] = "1"
    else:
        environment.pop("MCWALL_CAPTURE", None)
    # Straight to a file, never a pipe: the client logs far more than a pipe
    # buffer holds and nothing here would be draining it.
    log_path = state / "client.log"
    log.info("launching: %s", " ".join(command))
    log.info("client log: %s", log_path)
    handle = log_path.open("w")
    # Own process group, so the watchdog can take the whole tree down. Killing
    # the launcher alone orphans the JVM, which keeps rendering the previous
    # manifest and holds the world lock against the next run.
    return subprocess.Popen(
        command,
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def running_clients(config: Config) -> list[int]:
    """PIDs of game processes already running for this instance."""
    needle = f"{config.render.prism_instance}/natives"
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if needle in cmdline and "java" in cmdline:
            found.append(int(entry.name))
    return found


def kill_clients(config: Config, timeout: float = 20.0) -> None:
    """Make sure no game process for this instance survives the run."""
    import signal

    for pid in running_clients(config):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and running_clients(config):
        time.sleep(0.5)
    for pid in running_clients(config):
        log.warning("client %s did not exit, killing it", pid)
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def kill_tree(process: subprocess.Popen) -> None:
    """Kill the launch's whole process group, then any surviving client."""
    import signal

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def watch(
    process: subprocess.Popen,
    state: Path,
    config: Config,
    stall_seconds: int = 600,
    startup_grace_seconds: int = 240,
    on_battery_stop: bool = True,
) -> dict:
    """Supervise a run: stop on AC loss, kill it if the mod stops reporting."""
    last_progress = time.monotonic()
    last_status: dict = {}

    # Do not wait on `process`: Prism hands the game off to a detached JVM and
    # exits, so the launcher dying says nothing about the batch. The batch is
    # over when the mod writes DONE, or when a client we have actually seen
    # running has gone away.
    #
    # "Seen running" matters. Prism exits within a few seconds of handing off,
    # well before the JVM finishes starting, so a naive "launcher gone and no
    # client" test fires during startup and abandons a perfectly healthy run.
    started = time.monotonic()
    seen_client = False

    def finished() -> bool:
        nonlocal seen_client
        if (state / "DONE").exists():
            return True
        if running_clients(config):
            seen_client = True
            return False
        if process.poll() is None:
            return False
        # Launcher gone and no client: real only if the client ever ran, or if
        # it has had long enough to appear and never did.
        return seen_client or (time.monotonic() - started) > startup_grace_seconds

    while not finished():
        time.sleep(5)
        status = read_status(state)
        if status and status != last_status:
            last_status = status
            last_progress = time.monotonic()
            log.info(
                "render %s/%s (%s failed)",
                status.get("completed", 0) + status.get("skipped", 0),
                status.get("total", "?"),
                status.get("failed", 0),
            )

        if on_battery_stop and not read_power().on_ac:
            log.warning("on battery — asking the batch to stop")
            request_stop(state)
            on_battery_stop = False  # ask once, then let it finish the job
            last_progress = time.monotonic()

        if time.monotonic() - last_progress > stall_seconds:
            log.error("no progress for %ss — killing the client", stall_seconds)
            kill_tree(process)
            kill_clients(config)
            break

    # The launcher may already be gone; make sure the JVM is too, so the next
    # run does not trip over a client still holding the world.
    kill_clients(config)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(process)
    return read_status(state)


def request_stop(state: Path) -> None:
    (state / "STOP").write_text("stop\n")


def read_status(state: Path) -> dict:
    try:
        return json.loads((state / "status.json").read_text())
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------- convert


def run_jobs(
    config: Config,
    jobs: list[dict],
    staging: Path,
    headless: bool = True,
    force: bool = False,
) -> dict:
    """Write a manifest, run the game once, and return the mod's final status.

    Used by `render`, `scout` and `probe` alike — they differ only in which
    jobs they hand over.
    """
    if not force and not read_power().on_ac and not config.render.allow_on_battery:
        raise RenderError("on battery — refusing to run the game (use --force)")

    stray = running_clients(config)
    if stray:
        raise RenderError(
            f"a game is already running for instance "
            f"'{config.render.prism_instance}' (pid {', '.join(map(str, stray))}).\n"
            f"It holds the world lock and is reading the old job manifest. "
            f"Stop it first: kill {' '.join(map(str, stray))}"
        )

    # Without the mod the game is just a game: it boots, renders a title
    # screen, captures nothing, and the batch fails only once the watchdog
    # gives up — several minutes per attempt, with the cause buried in a log.
    if not list((game_dir(config) / "mods").glob("mcwall-capture-*.jar")):
        raise RenderError(
            "the capture mod is not installed in "
            f"'{config.render.prism_instance}', so the game would render "
            "nothing.\nRun `mcwall setup` to install it."
        )

    missing_tools = missing_render_tools(config, headless)
    if missing_tools:
        raise RenderError(
            "these programs are not on PATH: " + ", ".join(missing_tools) + ".\n"
            "mcwall carries its own copies of the capture and encoding tools; "
            "prismlauncher is yours and has to be installed."
        )

    ensure_world_variants(config, jobs)

    staging.mkdir(parents=True, exist_ok=True)
    state = write_batch(config, jobs, staging)
    process = launch(config, state, headless=headless)
    with BackgroundConverter(config, staging):
        status = watch(process, state, config, on_battery_stop=not force)

    if not status:
        raise RenderError(
            f"the client exited without reporting any progress; see {state / 'client.log'} "
            f"and {game_dir(config) / 'logs/latest.log'}"
        )
    return status


# ------------------------------------------------------------------- convert


def _encode_command(config: Config, source: Path, target: Path, quality: int) -> list[str]:
    """Prefer cwebp for its quality control; fall back to ffmpeg's encoder."""
    if config.library.image_format == "webp" and shutil.which("cwebp"):
        return ["cwebp", "-quiet", "-q", str(quality), "-m", "6", str(source), "-o", str(target)]
    if config.library.image_format == "webp":
        return [
            "ffmpeg", "-loglevel", "error", "-y", "-i", str(source),
            "-c:v", "libwebp", "-quality", str(quality), "-preset", "picture",
            "-f", "webp", str(target),
        ]
    return ["ffmpeg", "-loglevel", "error", "-y", "-i", str(source), str(target)]


def convert_one(config: Config, png: Path, staging: Path) -> bool:
    """Convert a single staged PNG into the library. True if it did work."""
    root = config.library.root
    relative = png.relative_to(staging)
    target = root / relative.with_suffix(f".{config.library.image_format}")
    if target.exists():
        png.unlink()
        return False
    target.parent.mkdir(parents=True, exist_ok=True)

    if config.library.deband:
        # Deband straight from the lossless capture rather than from an
        # already-encoded image: repairing a lossy WebP compounds two sets of
        # artefacts, while this one only ever sees the original.
        from .deband import DebandSettings, deband_one

        deband_one(png, target, DebandSettings(near_lossless=config.library.near_lossless))
        png.unlink()
        return True

    # Encode beside the target and rename, so an interrupted conversion never
    # leaves a truncated image that `verify` would count as present.
    temporary = target.with_suffix(target.suffix + ".part")
    subprocess.run(
        _encode_command(config, png, temporary, config.library.quality), check=True
    )
    temporary.replace(target)
    png.unlink()
    return True


def missing_render_tools(config: Config, headless: bool = True) -> list[str]:
    """Which programs a render needs and cannot find.

    Worth checking before the game starts rather than after: `cage` missing
    surfaces as a bare FileNotFoundError from deep inside Popen, minutes into
    a command that looked like it was working.
    """
    needed = ["prismlauncher"]
    if headless:
        needed += ["cage", "wlr-randr"]
    if config.library.deband:
        needed += ["ffmpeg", "cwebp"]
    elif not shutil.which("cwebp"):
        needed.append("ffmpeg")  # the fallback encoder
    return [name for name in dict.fromkeys(needed) if not shutil.which(name)]


def background_workers() -> int:
    """How many cores conversion may use while the game is running.

    Half, because the other half is doing the actual work: chunk generation
    is CPU-bound, and a capture waiting on terrain costs eleven seconds while
    an encode waiting its turn costs about one.
    """
    return max(1, (os.cpu_count() or 2) // 2)


def convert_available(config: Config, staging: Path, workers: int | None = None) -> int:
    """Convert the captures that are finished, leaving the tree in place.

    Safe to run against a live batch: the mod writes each frame to `.part`
    and atomically renames it, so every `.png` on disk is whole.

    Unlike `convert_staging` this never deletes the directory the game is
    writing into, and it logs a failed image instead of raising — the final
    sweep retries it, and one bad encode must not end a five-hour run.
    """
    pngs = sorted(staging.rglob("*.png"))
    if not pngs:
        return 0

    workers = workers or background_workers()
    converted = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(convert_one, config, png, staging): png for png in pngs}
        for future in as_completed(futures):
            try:
                if future.result():
                    converted += 1
            except Exception as error:  # noqa: BLE001 - the final sweep retries
                log.warning("deferring %s: %s", futures[future].name, error)
    return converted


class BackgroundConverter:
    """Encode captures as they land, rather than in one tail at the end.

    A batch used to hold every lossless PNG until the game quit — about 16 GB
    for a full grid — and then spend minutes encoding with the GPU idle. The
    work is the same either way, but spread across the run it needs a fraction
    of the disk and leaves almost nothing to do at the end.
    """

    def __init__(self, config: Config, staging: Path, interval: float = 20.0):
        self.config = config
        self.staging = staging
        self.interval = interval
        self.converted = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "BackgroundConverter":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exception) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=120)
        if self.converted:
            log.info("converted %s images during the run", self.converted)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.converted += convert_available(self.config, self.staging)
            except Exception as error:  # noqa: BLE001 - never kill the render
                log.warning("background conversion paused: %s", error)


def convert_staging(config: Config, staging: Path, workers: int | None = None) -> int:
    """Turn the batch's lossless PNGs into the library's WebP, atomically.

    Encoding one 2880x1800 frame costs about a second of CPU, so a full
    1,544-still batch is roughly 24 minutes serially — a long silent tail
    after the last capture. The work is one independent process per image and
    `subprocess.run` releases the GIL, so a thread pool across the cores turns
    that into a couple of minutes.
    """
    pngs = sorted(staging.rglob("*.png"))
    if not pngs:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return 0

    workers = workers or max(1, min(len(pngs), (os.cpu_count() or 2)))
    log.info("converting %s images with %s workers", len(pngs), workers)

    converted = 0
    failures: list[tuple[Path, Exception]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(convert_one, config, png, staging): png for png in pngs}
        for future in as_completed(futures):
            try:
                if future.result():
                    converted += 1
            except Exception as error:  # noqa: BLE001 - reported together below
                failures.append((futures[future], error))

    if failures:
        for png, error in failures[:5]:
            log.error("could not convert %s: %s", png.name, error)
        raise RenderError(
            f"{len(failures)} of {len(pngs)} images failed to convert; "
            f"the PNGs are still in {staging}, so rerunning will retry them"
        )

    # Drop the now-empty staging tree.
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    return converted
