#!/usr/bin/env python3
"""Pin the render instance's mod set to exact Modrinth files.

Writes ``config/mods.lock.json`` with a URL, size and SHA-512 per file, so
``setup_instance.py`` installs a byte-identical set every time and a library
rendered today can be reproduced later.

Run it by hand when you want to move to newer mods; the lockfile is committed.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.modrinth.com/v2"
UA = {"User-Agent": "mcwall/0.1 (personal project; github.com/ryleu)"}

MINECRAFT = "1.21.4"
FABRIC_LOADER = "0.16.14"  # contemporary with the 1.21.4 mod set
LWJGL = "3.3.3"

MODS = ["fabric-api", "sodium", "iris", "glitchcore", "serene-seasons"]
SHADERPACKS = ["complementary-unbound"]


def fetch(slug: str, loaders: list[str] | None) -> dict:
    params = {"game_versions": json.dumps([MINECRAFT])}
    if loaders:
        params["loaders"] = json.dumps(loaders)
    url = f"{API}/project/{slug}/version?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as response:
        versions = json.load(response)
    if not versions:
        raise SystemExit(f"no {MINECRAFT} release for {slug}")
    version = versions[0]
    file = version["files"][0]
    return {
        "project": slug,
        "version": version["version_number"],
        "filename": file["filename"],
        "url": file["url"],
        "sha512": file["hashes"]["sha512"],
        "size": file["size"],
    }


def main() -> None:
    lock = {
        "minecraft": MINECRAFT,
        "fabric_loader": FABRIC_LOADER,
        "lwjgl": LWJGL,
        "mods": {slug: fetch(slug, ["fabric"]) for slug in MODS},
        "shaderpacks": {slug: fetch(slug, None) for slug in SHADERPACKS},
    }
    out = Path(__file__).resolve().parent.parent / "config" / "mods.lock.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, indent=2) + "\n")

    for group in ("mods", "shaderpacks"):
        for slug, entry in lock[group].items():
            print(f"{slug:18} {entry['version']:24} {entry['size'] / 1e6:6.2f} MB")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
