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


def test_a_strict_load_rejects_an_unknown_key(tmp_path):
    """A typo should be findable — `mcwall verify` loads this way."""
    path = tmp_path / "config.toml"
    path.write_text("[render]\nwarmup_framez = 60\n")
    with pytest.raises(ValueError, match="unknown config key"):
        config_mod.load(path, strict=True)


def test_a_normal_load_survives_an_unknown_key(tmp_path, caplog):
    """Loading must not fail on an option a newer mcwall wrote.

    The config has two readers of different ages — this checkout and whatever
    revision the system installed. Adding `library.path` made the installed
    one raise on every five-minute tick until it caught up.
    """
    path = tmp_path / "config.toml"
    path.write_text("[render]\nwarmup_frames = 60\nwarmup_framez = 99\n")
    config = config_mod.load(path)
    assert config.render.warmup_frames == 60, "the keys it knows still apply"
    assert "warmup_framez" in caplog.text


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


def test_library_path_points_somewhere_or_nowhere(tmp_path, monkeypatch):
    """`path` is optional, so there is no existing Path to infer the type from."""
    from mcwall import config as config_mod

    written = tmp_path / "config.toml"
    written.write_text('[library]\npath = "/nix/store/abc-library"\n')
    monkeypatch.setattr(config_mod, "CONFIG_PATH", written)
    assert config_mod.load().library.path == Path("/nix/store/abc-library")

    written.write_text('[library]\npath = ""\n')
    assert config_mod.load().library.path is None, "empty means unset, not '.'"


def test_an_unknown_key_is_ignored_not_fatal(tmp_path, monkeypatch, caplog):
    """A newer mcwall's option must not break an older one's timer.

    The config is read both by this checkout and by whatever revision the
    system has installed. Adding `library.path` made the installed one raise
    on every five-minute tick until it caught up.
    """
    from mcwall import config as config_mod

    written = tmp_path / "config.toml"
    written.write_text('[library]\nquality = 80\nan_option_from_the_future = 3\n')
    monkeypatch.setattr(config_mod, "CONFIG_PATH", written)

    loaded = config_mod.load()
    assert loaded.library.quality == 80, "the keys it knows still apply"
    assert "an_option_from_the_future" in caplog.text
