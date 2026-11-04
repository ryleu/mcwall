"""Config loading, and that the documented example stays truthful."""

import dataclasses
import tomllib
from pathlib import Path

import pytest

from mcwall import config as config_mod

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example.toml"

SECTIONS = {
    "location": config_mod.Location,
    "library": config_mod.LibraryConfig,
    "render": config_mod.RenderConfig,
    "display": config_mod.DisplayConfig,
}


def documented() -> dict:
    return tomllib.loads(EXAMPLE.read_text())


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_example_documents_every_option_and_invents_none(section):
    """The example claims to list every option; hold it to that."""
    fields = {f.name for f in dataclasses.fields(SECTIONS[section]())}
    if section == "library":
        # `format` is spelled image_format internally to avoid the builtin.
        fields = (fields - {"image_format"}) | {"format"}
    keys = set(documented().get(section, {}))

    assert not keys - fields, f"[{section}] documents options that do not exist"
    assert not fields - keys, f"[{section}] has undocumented options"


def test_the_example_actually_loads():
    config = config_mod.load(EXAMPLE)
    assert len(config.ticks()) == 48
    assert config.library.deband is True


def test_unknown_keys_are_rejected_rather_than_ignored(tmp_path):
    """A typo in a config file should be loud, not silently defaulted."""
    path = tmp_path / "config.toml"
    path.write_text("[render]\nwarmup_framez = 60\n")
    with pytest.raises(ValueError, match="unknown config key"):
        config_mod.load(path)


def test_every_config_option_is_read_somewhere():
    """Config that nothing consumes is a lie about what is adjustable."""
    source = "\n".join(
        p.read_text()
        for p in (Path(__file__).resolve().parent.parent / "mcwall").glob("*.py")
        if p.name != "config.py"
    )
    # Options consumed by the home-manager module rather than the Python.
    external = set()
    unread = []
    for section, cls in SECTIONS.items():
        for field in dataclasses.fields(cls()):
            if field.name in external:
                continue
            if field.name not in source:
                unread.append(f"{section}.{field.name}")
    assert not unread, f"config options nothing reads: {unread}"
