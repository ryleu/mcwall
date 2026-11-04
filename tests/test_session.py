"""Reading the camera back out of a world, and keeping the mod out of the way."""

import pytest

from mcwall import session

nbtlib = pytest.importorskip("nbtlib")


def write_level(world, pos=(1.5, 70.0, -2.5), rot=(90.0, -12.0), player=True):
    world.mkdir(parents=True, exist_ok=True)
    data = {"LevelName": nbtlib.String(world.name)}
    if player:
        data["Player"] = nbtlib.Compound(
            {
                "Pos": nbtlib.List[nbtlib.Double]([nbtlib.Double(v) for v in pos]),
                "Rotation": nbtlib.List[nbtlib.Float]([nbtlib.Float(v) for v in rot]),
            }
        )
    nbtlib.File({"Data": nbtlib.Compound(data)}).save(world / "level.dat", gzipped=True)
    return world


def test_reads_position_and_rotation(tmp_path):
    world = write_level(tmp_path / "w", pos=(-191.5, 98.0, 192.5), rot=(90.0, -12.0))
    camera = session.read_camera(world)
    assert camera.as_tuple() == (-191.5, 98.0, 192.5, 90.0, -12.0)


def test_a_world_never_played_in_is_a_clear_error(tmp_path):
    world = write_level(tmp_path / "w", player=False)
    with pytest.raises(session.NoPlayerPosition, match="no saved player position"):
        session.read_camera(world)


def test_missing_level_dat_is_a_clear_error(tmp_path):
    (tmp_path / "w").mkdir()
    with pytest.raises(session.NoPlayerPosition, match="no level.dat"):
        session.read_camera(tmp_path / "w")


def test_latest_world_is_the_most_recently_played(tmp_path):
    game = tmp_path / "game"
    saves = session.saves_dir(game)
    old = write_level(saves / "old")
    new = write_level(saves / "new")
    import os

    os.utime(old / "level.dat", (1000, 1000))
    os.utime(new / "level.dat", (2000, 2000))
    assert session.latest_world(game).name == "new"


def test_no_worlds_yet(tmp_path):
    assert session.latest_world(tmp_path / "game") is None
    assert session.worlds(tmp_path / "game") == []


def test_jobs_are_stashed_so_the_mod_stays_idle(tmp_path):
    """Opening the instance by hand must not replay the last batch."""
    state = tmp_path
    (state / "jobs.jsonl").write_text('{"id": "x"}\n')

    assert session.stash_jobs(state) is True
    assert not (state / "jobs.jsonl").exists(), "the mod would still find a manifest"

    session.restore_jobs(state)
    assert (state / "jobs.jsonl").read_text() == '{"id": "x"}\n'


def test_stashing_nothing_is_harmless(tmp_path):
    assert session.stash_jobs(tmp_path) is False
    session.restore_jobs(tmp_path)
