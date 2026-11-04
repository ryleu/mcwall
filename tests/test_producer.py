"""Planning and job ordering — the parts of the producer with no game in them."""

import json
from dataclasses import replace

import pytest

from mcwall.config import Config, LibraryConfig, RenderConfig
from mcwall.grid import Weather
from mcwall.library import GridCell, Manifest
from mcwall.producer import build_plan, world_for
from mcwall.seasons import Phase, Season, SubSeason


@pytest.fixture
def config(tmp_path):
    return replace(
        Config(),
        library=replace(LibraryConfig(), root=tmp_path / "library"),
        render=replace(RenderConfig(), world="tpl"),
    )


def test_winter_uses_the_snow_world_variant(config):
    winter = GridCell(SubSeason(Season.WINTER, Phase.MID), Weather.CLEAR, 0)
    summer = GridCell(SubSeason(Season.SUMMER, Phase.MID), Weather.CLEAR, 0)
    assert world_for(winter, config) == "tpl-snow"
    assert world_for(summer, config) == "tpl"


def test_plan_on_an_empty_library_covers_the_whole_grid(config):
    plan = build_plan(config)
    assert plan.total == 1728
    assert len(plan.missing) == 1728
    assert len(plan.jobs) == 1728


def test_jobs_are_grouped_so_the_expensive_change_happens_least(config):
    """World first, then season: a season change costs a full chunk rebuild."""
    jobs = build_plan(config).jobs

    worlds = [job["world"] for job in jobs]
    assert worlds == sorted(worlds, key=worlds.index), "world groups are not contiguous"

    seen: list[tuple[str, str]] = []
    for job in jobs:
        key = (job["world"], job["sub_season"])
        if not seen or seen[-1] != key:
            seen.append(key)
    assert len(seen) == 12, f"season changes {len(seen)} times, should be 12"


def test_every_job_names_a_distinct_output(config):
    jobs = build_plan(config).jobs
    outputs = [job["output"] for job in jobs]
    assert len(set(outputs)) == len(outputs)
    assert all(output.endswith(".png") for output in outputs)


def test_job_sub_season_matches_serene_seasons_naming(config):
    jobs = build_plan(config).jobs
    names = {job["sub_season"] for job in jobs}
    assert names == {s.serene_name.lower() for s in SubSeason.all()}


def test_jobs_are_machine_independent(config):
    """Nothing in a job may name this machine, or mode 2 cannot reuse the mod."""
    jobs = build_plan(config).jobs
    blob = json.dumps(jobs)
    for leak in ("/home/", "hypr", "Prism", "swww", str(config.library.root)):
        assert leak not in blob, f"job manifest leaks {leak!r}"


def test_existing_images_are_not_replanned(config, tmp_path):
    plan = build_plan(config)
    manifest = plan.manifest
    manifest.write(config.library.root)

    done = plan.jobs[0]
    target = config.library.root / done["output"].replace(".png", ".webp")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")

    again = build_plan(config)
    assert len(again.jobs) == len(plan.jobs) - 1
    assert done["output"] not in {job["output"] for job in again.jobs}


def test_plan_reuses_an_existing_manifest(config):
    manifest = Manifest.from_config(config, "hash")
    manifest.ticks = [0, 12000]
    manifest.write(config.library.root)

    plan = build_plan(config)
    assert plan.manifest.ticks == [0, 12000]
    assert plan.total == 12 * 3 * 2


# --- shaderpack identity ---------------------------------------------------


def test_properties_normalisation_ignores_comments_and_order(tmp_path):
    """Iris rewrites its config on every exit; only the settings may count."""
    from mcwall.producer import normalise_properties

    first = tmp_path / "a.properties"
    first.write_text(
        "#This file stores configuration options for Iris\n"
        "#Tue Nov 03 18:03:42 CST 2026\n"
        "enableShaders=true\n"
        "shaderPack=Complementary.zip\n"
    )
    second = tmp_path / "b.properties"
    second.write_text(
        "#Wed Nov 04 09:11:02 CST 2026\n"
        "\n"
        "shaderPack=Complementary.zip\n"
        "enableShaders=true\n"
    )
    assert normalise_properties(first) == normalise_properties(second)


def test_properties_normalisation_still_sees_a_real_change(tmp_path):
    from mcwall.producer import normalise_properties

    before = tmp_path / "a.properties"
    before.write_text("#stamp\nenableShaders=true\nsunPathRotation=0\n")
    after = tmp_path / "b.properties"
    after.write_text("#stamp\nenableShaders=true\nsunPathRotation=25\n")
    assert normalise_properties(before) != normalise_properties(after)


def test_missing_properties_file_is_empty(tmp_path):
    from mcwall.producer import normalise_properties

    assert normalise_properties(tmp_path / "nope.properties") == b""


def test_properties_parsed_into_pairs(tmp_path):
    from mcwall.producer import _properties

    path = tmp_path / "iris.properties"
    path.write_text(
        "#stamp\nenableShaders=true\nshaderPack=Complementary.zip\ncolorSpace=SRGB\n"
    )
    assert dict(_properties(path))["shaderPack"] == "Complementary.zip"
    assert dict(_properties(path))["enableShaders"] == "true"


# --- conversion ------------------------------------------------------------


def test_conversion_is_parallel_and_atomic(tmp_path, monkeypatch):
    """Every staged image lands, nothing partial survives, staging is cleared."""
    from dataclasses import replace as dc_replace

    from mcwall import producer
    from mcwall.config import Config, LibraryConfig

    config = dc_replace(
        Config(),
        library=dc_replace(LibraryConfig(), root=tmp_path / "library", deband=False),
    )
    staging = tmp_path / "staging"
    for index in range(12):
        png = staging / "pack" / "mid_autumn" / "clear" / f"{index}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"pretend png")

    # Stand in for cwebp: copy the bytes so the test needs no encoder.
    def fake_encode(cfg, source, target, quality):
        return ["cp", str(source), str(target)]

    monkeypatch.setattr(producer, "_encode_command", fake_encode)

    converted = producer.convert_staging(config, staging, workers=4)

    assert converted == 12
    webp = list((tmp_path / "library").rglob("*.webp"))
    assert len(webp) == 12
    assert not list((tmp_path / "library").rglob("*.part"))
    assert not staging.exists()


def test_already_converted_images_are_not_redone(tmp_path, monkeypatch):
    from dataclasses import replace as dc_replace

    from mcwall import producer
    from mcwall.config import Config, LibraryConfig

    config = dc_replace(
        Config(),
        library=dc_replace(LibraryConfig(), root=tmp_path / "library", deband=False),
    )
    staging = tmp_path / "staging"
    png = staging / "pack" / "a.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"x")
    done = tmp_path / "library" / "pack" / "a.webp"
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_bytes(b"already here")

    monkeypatch.setattr(
        producer,
        "_encode_command",
        lambda *a: (_ for _ in ()).throw(AssertionError("should not re-encode")),
    )
    assert producer.convert_staging(config, staging, workers=2) == 0
    assert done.read_bytes() == b"already here"


def test_a_failed_conversion_keeps_the_pngs(tmp_path, monkeypatch):
    """A failure must be recoverable: rerunning retries, nothing is lost."""
    from dataclasses import replace as dc_replace

    import pytest as _pytest

    from mcwall import producer
    from mcwall.config import Config, LibraryConfig

    config = dc_replace(
        Config(),
        library=dc_replace(LibraryConfig(), root=tmp_path / "library", deband=False),
    )
    staging = tmp_path / "staging"
    png = staging / "pack" / "a.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"x")

    monkeypatch.setattr(producer, "_encode_command", lambda *a: ["false"])
    with _pytest.raises(producer.RenderError, match="failed to convert"):
        producer.convert_staging(config, staging, workers=2)
    assert png.exists(), "the source PNG must survive a failed conversion"
    assert staging.exists()


def test_deband_is_applied_at_conversion_when_enabled(tmp_path, monkeypatch):
    """A fresh render should produce debanded output directly from the PNG."""
    from dataclasses import replace as dc_replace

    from mcwall import deband as deband_mod
    from mcwall import producer
    from mcwall.config import Config, LibraryConfig

    config = dc_replace(
        Config(),
        library=dc_replace(LibraryConfig(), root=tmp_path / "library", deband=True),
    )
    staging = tmp_path / "staging"
    png = staging / "pack" / "a.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"source")

    seen = {}

    def fake_deband_one(source, target, settings):
        seen["source"] = source
        seen["near_lossless"] = settings.near_lossless
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"debanded")
        return target.stat().st_size

    monkeypatch.setattr(deband_mod, "deband_one", fake_deband_one)
    monkeypatch.setattr(
        producer,
        "_encode_command",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not plain-encode")),
    )

    assert producer.convert_staging(config, staging, workers=2) == 1
    # It debands the original capture, not a re-encoded intermediate.
    assert seen["source"].suffix == ".png"
    assert seen["near_lossless"] == 60
    assert not png.exists()
