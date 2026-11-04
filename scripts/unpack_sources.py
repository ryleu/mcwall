#!/usr/bin/env python3
"""Unpack Loom's decompile cache into a readable source tree.

``./gradlew genSources`` leaves the decompiled source in a content-addressed
zip (``var/gradle/caches/fabric-loom/decompile/v1.zip``) where each entry is a
small Loom record holding the class name and the source text, not a path. This
rebuilds the normal ``net/minecraft/...`` layout so the source can be read and
grepped while writing mixins.

    python3 scripts/unpack_sources.py [--out var/mcsrc]
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO / "var/gradle/caches/fabric-loom/decompile/v1.zip"

# Each record is "LOOM\t...NAME\t<len><class/name>SRC\t<len><source>", with the
# lengths written as a varint-ish prefix. Rather than decode the framing, find
# the two markers and take what lies between them.
NAME_MARKER = b"NAME"
SRC_MARKER = b"SRC"
# Anchor on a real package root: the record's length prefix is often an
# alphanumeric byte that would otherwise be swallowed into the class name.
CLASS_NAME = re.compile(rb"((?:net|com|org|io|it|javax|java)/[a-zA-Z0-9_$/]+)")


def parse(record: bytes) -> tuple[str, str] | None:
    name_at = record.find(NAME_MARKER)
    src_at = record.find(SRC_MARKER, name_at)
    if name_at < 0 or src_at < 0:
        return None
    match = CLASS_NAME.search(record, name_at, src_at)
    if not match:
        return None
    name = match.group(1).decode("utf-8")
    body = record[src_at + len(SRC_MARKER) :]
    # Skip the length prefix: source always starts at the "package" keyword or
    # a comment/annotation at the top of the file.
    start = body.find(b"package ")
    if start < 0:
        start = 0
    return name, body[start:].decode("utf-8", "replace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=REPO / "var/mcsrc")
    args = parser.parse_args()

    if not args.cache.exists():
        raise SystemExit(f"{args.cache} not found; run ./gradlew genSources first")

    written = skipped = 0
    with zipfile.ZipFile(args.cache) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parsed = parse(archive.read(info))
            if parsed is None:
                skipped += 1
                continue
            name, source = parsed
            target = args.out / f"{name}.java"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
            written += 1

    print(f"wrote {written} sources to {args.out} ({skipped} records unparsed)")


if __name__ == "__main__":
    main()
