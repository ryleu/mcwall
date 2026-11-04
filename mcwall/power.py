"""AC/battery state, read straight from sysfs.

No upower dependency on the hot path: two small file reads cost microseconds
and work in a bare systemd user session.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

POWER_SUPPLY = Path("/sys/class/power_supply")


@dataclass(frozen=True)
class PowerState:
    on_ac: bool
    percent: int | None

    @property
    def low(self) -> bool:
        return not self.on_ac and self.percent is not None and self.percent < 20


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def read(root: Path = POWER_SUPPLY, low_percent: int = 20) -> PowerState:
    """Current power state; assumes AC when sysfs says nothing useful."""
    on_ac = True
    percent: int | None = None

    if not root.exists():
        return PowerState(on_ac=True, percent=None)

    mains = []
    batteries = []
    for entry in sorted(root.iterdir()):
        kind_path = entry / "type"
        try:
            kind = kind_path.read_text().strip()
        except OSError:
            continue
        # USB-C PD source ports also show up as "Mains"; only the real adapter
        # has an `online` file that flips, so read them all and OR the result.
        if kind == "Mains":
            mains.append(entry)
        elif kind == "Battery":
            batteries.append(entry)

    if batteries:
        on_ac = any(_read_int(entry / "online") == 1 for entry in mains)
        for entry in batteries:
            percent = _read_int(entry / "capacity")
            if percent is not None:
                break

    state = PowerState(on_ac=on_ac, percent=percent)
    return state
