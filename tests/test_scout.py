"""Slot placement from measured visual change — the phase 5 tuning core.

Driven with synthetic frames rather than renders: a "day" that is flat for most
of its length and changes fast in one window is exactly the shape the probe
has to cope with, and it is reproducible without the game.
"""

import pytest

from mcwall.scout import DEFAULT_HEIGHT_OFFSETS, default_candidates, even_change_slots

pytest.importorskip("PIL")


def frame(tmp_path, tick: int, level: int):
    """A flat grey frame; `level` stands in for how bright the sky is."""
    from PIL import Image

    path = tmp_path / f"{tick:05d}.png"
    Image.new("RGB", (32, 20), (level, level, level)).save(path)
    return tick, path


def flat_then_fast(tmp_path, total=240):
    """A day that is static for three quarters, then sweeps through brightness.

    The last quarter carries essentially all of the visual change, so a correct
    placement puts most slots there.
    """
    frames = []
    for index in range(total):
        tick = index * (24000 // total)
        if index < total * 3 // 4:
            level = 0
        else:
            level = int(255 * (index - total * 3 // 4) / (total // 4))
        frames.append(frame(tmp_path, tick, level))
    return frames


def test_slots_concentrate_where_the_picture_changes(tmp_path):
    frames = flat_then_fast(tmp_path)
    ticks = even_change_slots(frames, slots=48)

    # Every slot is a real tick from the probe, and they are unique and sorted.
    probe_ticks = {tick for tick, _ in frames}
    assert set(ticks) <= probe_ticks
    assert ticks == sorted(set(ticks))

    # The change all happens in the last quarter of the day; the placement
    # should spend nearly all its slots there rather than on the flat part.
    boundary = 24000 * 3 // 4
    in_active = [tick for tick in ticks if tick >= boundary]
    assert len(in_active) >= 0.8 * len(ticks), ticks


def test_a_uniformly_changing_day_spreads_slots_evenly(tmp_path):
    frames = [frame(tmp_path, index * 100, index) for index in range(240)]
    ticks = even_change_slots(frames, slots=48)

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    # No gap should be wildly larger than the mean when change is uniform.
    mean = sum(gaps) / len(gaps)
    assert max(gaps) < 3 * mean, gaps


def test_too_few_probe_frames_is_an_error(tmp_path):
    frames = [frame(tmp_path, index * 1000, index) for index in range(10)]
    with pytest.raises(ValueError, match="at least"):
        even_change_slots(frames, slots=48)


def test_identical_frames_are_rejected_rather_than_guessed(tmp_path):
    frames = [frame(tmp_path, index * 100, 128) for index in range(240)]
    with pytest.raises(ValueError, match="identical"):
        even_change_slots(frames, slots=48)


def test_candidates_are_relative_to_the_ground(tmp_path):
    at_sea_level = default_candidates(68.0)
    higher_up = default_candidates(120.0)

    assert len(at_sea_level) == 4 * len(DEFAULT_HEIGHT_OFFSETS)
    assert {candidate.y for candidate in at_sea_level} == {
        68.0 + offset for offset in DEFAULT_HEIGHT_OFFSETS
    }
    # The same named candidate moves with the ground, so a sweep means the same
    # thing in a valley as on a plateau.
    by_name = {candidate.name: candidate for candidate in at_sea_level}
    for candidate in higher_up:
        assert candidate.y - by_name[candidate.name].y == pytest.approx(52.0)
        assert candidate.yaw == by_name[candidate.name].yaw


def test_every_candidate_has_a_distinct_name():
    names = [candidate.name for candidate in default_candidates()]
    assert len(set(names)) == len(names)
