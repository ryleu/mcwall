#!/usr/bin/env python3
"""Create the dedicated Prism render instance.

Idempotent, and it only ever writes inside its own instance directory. The
user's real instances, worlds and accounts are never touched.

    python3 scripts/setup_instance.py [--name mc-wallpaper] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCKFILE = REPO / "config" / "mods.lock.json"
UA = {"User-Agent": "mcwall/0.1 (personal project)"}

PRISM_DIRS = [
    Path.home() / ".local/share/PrismLauncher",
    Path.home() / ".var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher",
]

# Headless-friendly, shader-friendly, and deterministic: no view bob, no
# distortion effects, no pause when the window loses focus.
OPTIONS_TXT = """version:4189
fov:0.0
gamma:1.0
renderDistance:{render_distance}
simulationDistance:8
maxFps:260
enableVsync:false
graphicsMode:1
ao:true
particles:0
guiScale:2
bobView:false
screenEffectScale:0.0
fovEffectScale:0.0
darknessEffectScale:0.0
damageTiltStrength:0.0
glintSpeed:0.0
glintStrength:0.0
entityShadows:true
pauseOnLostFocus:false
narrator:0
autoJump:false
showSubtitles:false
toggleCrouch:false
toggleSprint:false
hideMatchedNames:false
chatVisibility:2
soundCategory_master:0.0
mouseSensitivity:0.5
fullscreen:false
resourcePacks:[]
incompatibleResourcePacks:[]
lang:en_us
"""

IRIS_PROPERTIES = """# Managed by mcwall/scripts/setup_instance.py
enableShaders=true
shaderPack={shaderpack}
disableUpdateMessage=true
maxShadowRenderDistance=32
"""


def find_prism() -> Path:
    for candidate in PRISM_DIRS:
        if (candidate / "accounts.json").exists():
            return candidate
    raise SystemExit(
        "no Prism Launcher data directory found; looked in:\n  "
        + "\n  ".join(str(p) for p in PRISM_DIRS)
    )


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(entry: dict, target: Path) -> bool:
    """Fetch one locked file unless it is already there and intact."""
    if target.exists() and sha512(target) == entry["sha512"]:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(entry["url"], headers=UA)
    with urllib.request.urlopen(request) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out)
    got = sha512(tmp)
    if got != entry["sha512"]:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {entry['filename']}: {got}")
    tmp.replace(target)
    return True


def mmc_pack(lock: dict) -> dict:
    """Minimal component list; Prism fills in cached names on first load."""
    return {
        "formatVersion": 1,
        "components": [
            {
                "uid": "org.lwjgl3",
                "version": lock["lwjgl"],
                "cachedVolatile": True,
                "dependencyOnly": True,
            },
            {"uid": "net.minecraft", "version": lock["minecraft"], "important": True},
            {
                "uid": "net.fabricmc.intermediary",
                "version": lock["minecraft"],
                "cachedVolatile": True,
                "dependencyOnly": True,
            },
            {
                "uid": "net.fabricmc.fabric-loader",
                "version": lock["fabric_loader"],
            },
        ],
    }


def instance_cfg(name: str, width: int, height: int, java: str | None) -> str:
    """Prism instance settings.

    The window is sized to the capture resolution: shaders make offscreen
    capture at arbitrary sizes unreliable, so the framebuffer we read is the
    one the game actually renders.
    """
    settings = {
        "InstanceType": "OneSix",
        "ConfigVersion": "1.3",
        "name": name,
        "notes": "Managed by mcwall. Do not play in this instance; render only.",
        "iconKey": "default",
        "OverrideWindow": "true",
        "LaunchMaximized": "false",
        "MinecraftWinWidth": str(width),
        "MinecraftWinHeight": str(height),
        "OverrideMemory": "true",
        "MinMemAlloc": "1024",
        "MaxMemAlloc": "6144",
        "OverrideJavaArgs": "true",
        "JvmArgs": "-XX:+UseG1GC -XX:MaxGCPauseMillis=50 -Dmcwall.enabled=true",
        "OverrideCommands": "true",
        "PreLaunchCommand": "",
        "PostExitCommand": "",
        "WrapperCommand": "",
        "OverrideConsole": "true",
        "ShowConsole": "false",
        "ShowConsoleOnError": "false",
        "AutoCloseConsole": "true",
        "LogPrePostOutput": "true",
        "CloseAfterLaunch": "true",
        "QuitAfterGameStop": "true",
        "JoinServerOnLaunch": "false",
        "UseAccountForInstance": "false",
        "AutomaticJava": "true" if java is None else "false",
    }
    if java is not None:
        settings["OverrideJavaLocation"] = "true"
        settings["JavaPath"] = java
    body = "\n".join(f"{k}={v}" for k, v in sorted(settings.items()))
    return f"[General]\n{body}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="mc-wallpaper")
    parser.add_argument("--width", type=int, default=2880)
    parser.add_argument("--height", type=int, default=1800)
    parser.add_argument("--java", default=os.environ.get("MCWALL_JAVA"))
    parser.add_argument("--render-distance", type=int, default=16)
    parser.add_argument(
        "--force", action="store_true", help="rewrite instance.cfg and mmc-pack.json"
    )
    args = parser.parse_args()

    lock = json.loads(LOCKFILE.read_text())
    prism = find_prism()
    instance = prism / "instances" / args.name
    game = instance / ".minecraft"

    if instance.exists() and not (instance / ".mcwall-managed").exists():
        raise SystemExit(
            f"{instance} exists and was not created by mcwall; refusing to touch it"
        )

    for sub in ("mods", "shaderpacks", "saves", "config", "screenshots"):
        (game / sub).mkdir(parents=True, exist_ok=True)
    (instance / ".mcwall-managed").write_text(
        "Created by mcwall/scripts/setup_instance.py. Safe to delete.\n"
    )

    pack_path = instance / "mmc-pack.json"
    if args.force or not pack_path.exists():
        pack_path.write_text(json.dumps(mmc_pack(lock), indent=4) + "\n")
        print(f"wrote {pack_path.name}")

    cfg_path = instance / "instance.cfg"
    if args.force or not cfg_path.exists():
        cfg_path.write_text(instance_cfg(args.name, args.width, args.height, args.java))
        print(f"wrote {cfg_path.name}")

    # Mods: install exactly the locked set, and remove anything else so a stale
    # jar can never silently change the render.
    wanted = {entry["filename"] for entry in lock["mods"].values()}
    for entry in lock["mods"].values():
        changed = download(entry, game / "mods" / entry["filename"])
        print(f"{'downloaded' if changed else 'present   '} {entry['filename']}")
    for stray in (game / "mods").glob("*.jar"):
        if stray.name not in wanted and not stray.name.startswith("mcwall-capture"):
            stray.unlink()
            print(f"removed stray mod {stray.name}")

    # The capture mod, if it has been built. Without it the instance is just a
    # normal 1.21.4 Fabric install, which is what you want while setting up the
    # template world by hand.
    built = sorted((REPO / "mod/build/libs").glob("mcwall-capture-*.jar"))
    built = [jar for jar in built if "sources" not in jar.name]
    for stale in (game / "mods").glob("mcwall-capture-*.jar"):
        stale.unlink()
    if built:
        shutil.copy2(built[-1], game / "mods" / built[-1].name)
        print(f"installed   {built[-1].name}")
    else:
        print("note: capture mod not built yet (cd mod && ./gradlew build)")

    shaderpack = None
    for entry in lock["shaderpacks"].values():
        changed = download(entry, game / "shaderpacks" / entry["filename"])
        shaderpack = entry["filename"]
        print(f"{'downloaded' if changed else 'present   '} {entry['filename']}")

    (game / "options.txt").write_text(
        OPTIONS_TXT.format(render_distance=args.render_distance)
    )

    # Leave iris.properties alone once it already selects the right pack with
    # shaders on. Iris fills in its own defaults when the game exits, and
    # rewriting the file every time would churn the library's settings hash.
    (game / "config").mkdir(exist_ok=True)
    iris_config = game / "config" / "iris.properties"
    existing = iris_config.read_text() if iris_config.exists() else ""
    selects_pack = f"shaderPack={shaderpack}" in existing
    shaders_on = "enableShaders=true" in existing
    if args.force or not (selects_pack and shaders_on):
        iris_config.write_text(IRIS_PROPERTIES.format(shaderpack=shaderpack))
        print("wrote       config/iris.properties")
    print(f"\ninstance ready: {instance}")
    print("launch with: prismlauncher --launch", args.name, "--profile <account>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
