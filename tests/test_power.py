"""Power state, read from a fake sysfs tree."""

import pytest

from mcwall.power import PowerState, read


def make_supply(root, name, kind, **files):
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "type").write_text(kind + "\n")
    for key, value in files.items():
        (entry / key).write_text(str(value) + "\n")
    return entry


def test_laptop_on_ac(tmp_path):
    make_supply(tmp_path, "AC", "Mains", online=1)
    make_supply(tmp_path, "BAT0", "Battery", capacity=87)
    state = read(tmp_path)
    assert state == PowerState(on_ac=True, percent=87)
    assert not state.low


def test_laptop_on_battery(tmp_path):
    make_supply(tmp_path, "AC", "Mains", online=0)
    make_supply(tmp_path, "BAT0", "Battery", capacity=54)
    state = read(tmp_path)
    assert state.on_ac is False
    assert state.percent == 54
    assert not state.low


def test_low_battery(tmp_path):
    make_supply(tmp_path, "AC", "Mains", online=0)
    make_supply(tmp_path, "BAT0", "Battery", capacity=12)
    assert read(tmp_path).low is True


def test_usb_c_port_does_not_count_as_unplugged(tmp_path):
    """This laptop exposes two USB-C PD source ports that are always offline."""
    make_supply(tmp_path, "AC", "Mains", online=1)
    make_supply(tmp_path, "ucsi-source-psy-USBC000:001", "Mains", online=0)
    make_supply(tmp_path, "ucsi-source-psy-USBC000:002", "Mains", online=0)
    make_supply(tmp_path, "BAT0", "Battery", capacity=61)
    assert read(tmp_path).on_ac is True


def test_desktop_without_a_battery_is_always_on_ac(tmp_path):
    make_supply(tmp_path, "AC", "Mains", online=1)
    state = read(tmp_path)
    assert state.on_ac is True
    assert state.percent is None


def test_missing_sysfs_assumes_ac(tmp_path):
    state = read(tmp_path / "nope")
    assert state.on_ac is True
    assert state.percent is None


def test_unreadable_capacity_does_not_crash(tmp_path):
    make_supply(tmp_path, "AC", "Mains", online=0)
    make_supply(tmp_path, "BAT0", "Battery", capacity="unknown")
    state = read(tmp_path)
    assert state.on_ac is False
    assert state.percent is None
    assert not state.low  # unknown charge must not read as "low"
