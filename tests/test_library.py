"""The library format: the contract between producer and display path."""

import json

import pytest

from mcwall.config import Config
from mcwall.grid import Weather
from mcwall.library import GridCell, Library, Manifest
from mcwall.seasons import Phase, Season, SubSeason


def make_manifest():
    return Manifest(
        pack_id="pack",
        pack_name="Pack.zip",
        settings_hash="abc123",
        width=2880,
        height=1800,
        image_format="webp",
        sub_seasons=[s.key for s in SubSeason.all()],
        weathers=[w.value for w in Weather],
        ticks=[0, 6000, 12000, 18000],
    )


def test_cell_path_layout():
    cell = GridCell(SubSeason(Season.WINTER, Phase.EARLY), Weather.THUNDER, 12785)
    assert str(cell.relative_path("pack", ".webp")) == "pack/early_winter/thunder/12785.webp"
    assert str(cell.relative_path("pack", ".mp4", loop=True)) == (
        "pack/loops/early_winter/thunder/12785.mp4"
    )


def test_manifest_round_trips(tmp_path):
    manifest = make_manifest()
    manifest.write(tmp_path)
    loaded = Manifest.read(tmp_path)
    assert loaded == manifest


def test_manifest_write_is_atomic(tmp_path):
    make_manifest().write(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    json.loads((tmp_path / "manifest.json").read_text())


def test_unknown_schema_is_rejected(tmp_path):
    manifest = make_manifest()
    manifest.write(tmp_path)
    data = json.loads((tmp_path / "manifest.json").read_text())
    data["schema"] = 99
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema"):
        Manifest.read(tmp_path)


def test_cells_cover_the_whole_grid():
    manifest = make_manifest()
    cells = manifest.cells()
    assert len(cells) == 12 * 3 * 4
    assert len(set(cells)) == len(cells)


def test_rescan_finds_only_what_is_on_disk(tmp_path):
    manifest = make_manifest()
    manifest.write(tmp_path)
    library = Library(tmp_path, manifest)

    present = GridCell(SubSeason(Season.SPRING, Phase.MID), Weather.CLEAR, 6000)
    path = library.path_for(present)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not really a webp")

    library.rescan()
    assert library.has_still(present)
    assert len(library.stills) == 1
    assert len(library.missing_stills()) == len(manifest.cells()) - 1


def test_loops_are_tracked_separately(tmp_path):
    manifest = make_manifest()
    manifest.loop_ticks = [6000]
    manifest.write(tmp_path)
    library = Library(tmp_path, manifest)

    cell = GridCell(SubSeason(Season.AUTUMN, Phase.LATE), Weather.RAIN, 6000)
    loop = library.path_for(cell, loop=True)
    loop.parent.mkdir(parents=True)
    loop.write_bytes(b"not really an mp4")

    library.rescan()
    assert library.has_loop(cell)
    assert not library.has_still(cell)


def test_manifest_from_config_matches_the_grid():
    config = Config()
    manifest = Manifest.from_config(config, "hash")
    assert len(manifest.ticks) == 48
    assert len(manifest.cells()) == 1728
    assert manifest.width == config.library.width


def test_a_manifest_from_a_newer_build_still_reads(tmp_path, caplog):
    """A field this build does not know must not take the library offline.

    The display path only needs the grid and the build id; everything else is
    provenance. An older reader met a manifest carrying `world` and died with
    a TypeError, so the wallpaper stopped updating until the reader caught up.
    """
    import json

    from mcwall.library import MANIFEST_NAME, Manifest

    payload = {
        "pack_id": "b",
        "pack_name": "p.zip",
        "settings_hash": "h",
        "width": 2880,
        "height": 1800,
        "image_format": "webp",
        "sub_seasons": ["mid_autumn"],
        "weathers": ["clear"],
        "ticks": [7767],
        "mode": 1,
        "schema": 1,
        "rendered_at": None,
        "loop_ticks": [],
        "notes": {},
        "a_field_from_the_future": {"nested": True},
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(payload))

    manifest = Manifest.read(tmp_path)
    assert manifest.pack_id == "b"
    assert manifest.cells(), "the grid survived"
    assert "a_field_from_the_future" in caplog.text, "dropped silently"


def _write_build(directory, pack_id, cells=2):
    """A library directory holding its own manifest and images."""
    from mcwall.grid import Weather
    from mcwall.library import GridCell, Manifest
    from mcwall.seasons import SubSeason

    seasons = [s.key for s in SubSeason.all()][:1]
    manifest = Manifest(
        pack_id=pack_id, pack_name="p.zip", settings_hash="h",
        width=16, height=9, image_format="webp",
        sub_seasons=seasons, weathers=["clear"], ticks=list(range(cells)),
    )
    directory.mkdir(parents=True, exist_ok=True)
    manifest.write(directory)
    for cell in manifest.cells():
        path = directory / GridCell(
            SubSeason.from_key(cell.sub_season.key), Weather("clear"), cell.tick
        ).relative_path("", ".webp")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return manifest


def test_a_manifest_beside_its_images_finds_them(tmp_path):
    """What lets a config point straight at one library, wherever it lives."""
    from mcwall.library import Library

    _write_build(tmp_path / "build", "some-build")
    library = Library.open(tmp_path / "build")

    assert library.nested is False
    assert library.missing_stills() == []
    shown = library.path_for(library.manifest.cells()[0])
    assert shown.parent.parent.parent == tmp_path / "build", shown
    assert "some-build" not in str(shown.relative_to(tmp_path / "build"))


def test_a_manifest_above_its_build_still_finds_them(tmp_path):
    """The root layout, where a completed render promotes itself."""
    from mcwall.library import Library, Manifest

    manifest = _write_build(tmp_path / "some-build", "some-build")
    manifest.write(tmp_path)

    library = Library.open(tmp_path)
    assert library.nested is True
    assert library.missing_stills() == []


def test_builds_marks_the_active_one_by_path(tmp_path):
    from mcwall.library import builds

    _write_build(tmp_path / "a", "a")
    _write_build(tmp_path / "b", "b")

    found = {b.pack_id: b for b in builds(tmp_path, active=tmp_path / "b")}
    assert found["b"].active and not found["a"].active
    assert all(b.complete and b.switchable for b in found.values())


def test_a_build_without_a_manifest_cannot_be_selected(tmp_path):
    from mcwall.library import builds

    stray = tmp_path / "orphan" / "mid_autumn" / "clear"
    stray.mkdir(parents=True)
    (stray / "100.webp").write_bytes(b"")

    orphan = next(b for b in builds(tmp_path) if b.pack_id == "orphan")
    assert not orphan.switchable, "a library that cannot describe itself"
    assert orphan.present == 1
