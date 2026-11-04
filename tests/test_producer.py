"""Planning and job ordering — the parts of the producer with no game in them."""

import json
from dataclasses import replace

import pytest

from mcwall.config import Config, LibraryConfig, RenderConfig
from mcwall.grid import Weather
from mcwall.library import GridCell, Manifest
from mcwall.producer import build_plan, world_for
from mcwall.seasons import Phase, Season, SubSeason


def _move_camera(config, pos):
    """Move the player in the fixture's world, as playing the game would."""
    import nbtlib

    from mcwall import producer

    level = producer.game_dir(config) / "saves" / "tpl" / "level.dat"
    with nbtlib.load(level) as handle:
        handle["Data"]["Player"]["Pos"] = nbtlib.List[nbtlib.Double](
            [nbtlib.Double(v) for v in pos]
        )


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A config whose render instance is a throwaway tree with one world.

    The camera lives in the world's save, so a fixture without one cannot
    plan anything — which is the behaviour we want, just not here.
    """
    nbtlib = pytest.importorskip("nbtlib")
    from mcwall import producer

    game = tmp_path / "game"
    world = game / "saves" / "tpl"
    world.mkdir(parents=True)
    nbtlib.File(
        {
            "Data": nbtlib.Compound(
                {
                    "LevelName": nbtlib.String("tpl"),
                    "WorldGenSettings": nbtlib.Compound({"seed": nbtlib.Long(4242)}),
                    "Player": nbtlib.Compound(
                        {
                            "Pos": nbtlib.List[nbtlib.Double](
                                [nbtlib.Double(v) for v in (1.5, 70.0, -2.5)]
                            ),
                            "Rotation": nbtlib.List[nbtlib.Float](
                                [nbtlib.Float(v) for v in (90.0, -12.0)]
                            ),
                        }
                    ),
                }
            )
        }
    ).save(world / "level.dat", gzipped=True)
    monkeypatch.setattr(producer, "game_dir", lambda _config: game)

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


def test_plan_reuses_a_manifest_for_the_same_build(config):
    from mcwall.producer import pack_id

    manifest = Manifest.from_config(config, "hash", pack_id=pack_id(config))
    manifest.ticks = [0, 12000]
    manifest.write(config.library.root)

    plan = build_plan(config)
    assert plan.manifest.ticks == [0, 12000]
    assert plan.total == 12 * 3 * 2


def test_a_different_build_does_not_reuse_the_old_manifest(config):
    """Changing what the pictures are of must not reuse the old grid."""
    from dataclasses import replace as dc_replace

    from mcwall.producer import pack_id

    manifest = Manifest.from_config(config, "hash", pack_id=pack_id(config))
    manifest.ticks = [0, 12000]
    manifest.write(config.library.root)

    # Moving the player in the world moves the shot, and so the build.
    _move_camera(config, (111.0, 70.0, 222.0))
    plan = build_plan(config)
    assert plan.manifest.pack_id != manifest.pack_id
    assert plan.manifest.ticks == config.ticks(), "stale grid carried over"


def test_identity_covers_camera_resolution_and_distance(config):
    """Anything that changes the picture must change the directory."""
    from dataclasses import replace as dc_replace

    from mcwall.producer import pack_id

    base = pack_id(config)
    variants = {
        "resolution": dc_replace(config, library=dc_replace(config.library, width=1920)),
        "render_distance": dc_replace(config, render=dc_replace(config.render, render_distance=8)),
    }
    for name, variant in variants.items():
        assert pack_id(variant) != base, f"{name} does not change the build key"

    # Warm-up frames decide how settled a frame is, not what it shows.
    tuned = dc_replace(config, render=dc_replace(config.render, warmup_frames=60))
    assert pack_id(tuned) == base, "warm-up frames should not fork the library"

    # Moving the player is a different shot, so a different build.
    _move_camera(config, (500.0, 90.0, -500.0))
    assert pack_id(config) != base, "moving the camera does not change the build key"


def test_the_build_key_says_how_it_was_made(config):
    """A shelf of libraries has to be tellable apart without opening them."""
    from mcwall.producer import pack_id

    key = pack_id(config)
    assert key.startswith("tpl-"), key          # the world
    assert "4242" in key, key                    # its seed
    assert config.pack_stem in key, key          # the shaderpack
    assert key != f"tpl-4242-{config.pack_stem}", "no identity hash"


def test_a_world_never_opened_cannot_be_rendered(config, tmp_path):
    """Better to say so than to render 1,728 pictures of spawn."""
    import nbtlib

    from mcwall import producer

    world = producer.game_dir(config) / "saves" / "tpl"
    nbtlib.File({"Data": nbtlib.Compound({"LevelName": nbtlib.String("tpl")})}).save(
        world / "level.dat", gzipped=True
    )
    with pytest.raises(producer.RenderError, match="mcwall edit"):
        build_plan(config)


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


def test_adopting_an_older_library_keeps_it_readable(config):
    """Renaming the directory without the manifest blinds the display path.

    A dry run plans but never writes a manifest, so if the rename did not
    carry one the library would stay unreadable until the next full render.
    """
    from mcwall.library import Manifest
    from mcwall.producer import migrate_legacy_build, pack_id, shaderpack_identity

    root = config.library.root
    legacy = root / config.pack_stem
    (legacy / "mid_autumn" / "clear").mkdir(parents=True)
    Manifest.from_config(
        config, shaderpack_identity(config), pack_id=config.pack_stem
    ).write(root)

    key = pack_id(config)
    assert migrate_legacy_build(config, key)
    assert (root / key).is_dir(), "images left behind"
    assert Manifest.read(root).pack_id == key, "manifest still names the old build"


def test_a_library_from_other_settings_is_left_alone(config):
    """Its images are not of this build, so renaming would misfile them."""
    from mcwall.library import Manifest
    from mcwall.producer import migrate_legacy_build, pack_id

    root = config.library.root
    legacy = root / config.pack_stem
    legacy.mkdir(parents=True)
    Manifest.from_config(config, "a-different-hash", pack_id=config.pack_stem).write(root)

    assert not migrate_legacy_build(config, pack_id(config))
    assert legacy.is_dir(), "a library from other settings was adopted anyway"


def test_rendering_without_the_capture_mod_fails_fast(config, monkeypatch):
    """Otherwise the game boots, captures nothing, and times out minutes later.

    The failure then looks like a stuck render rather than a missing jar.
    """
    from mcwall import power, producer

    game = producer.game_dir(config)
    (game / "mods").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(producer, "running_clients", lambda _c: [])
    monkeypatch.setattr(
        producer, "read_power", lambda: power.PowerState(on_ac=True, percent=100)
    )

    # Nothing past the preflight may run during this test.
    class Launched(Exception):
        pass

    def refuse(*_args, **_kwargs):
        raise Launched

    monkeypatch.setattr(producer, "ensure_world_variants", refuse)
    staging = config.library.root / ".staging"

    with pytest.raises(producer.RenderError, match="mcwall setup"):
        producer.run_jobs(config, [], staging)

    # With the jar there, the preflight passes and the run proceeds.
    (game / "mods" / "mcwall-capture-0.1.0.jar").write_bytes(b"")
    with pytest.raises(Launched):
        producer.run_jobs(config, [], staging)


def test_dry_run_leaves_the_active_library_alone(config, monkeypatch, capsys):
    """A dry run plans; it must not retire the library currently on screen.

    Writing the manifest here names a build with no images in it yet, so the
    display path goes looking in an empty directory and the wallpaper stops
    updating until a full render finishes hours later.
    """
    from mcwall import cli, power, producer
    from mcwall.library import Manifest

    root = config.library.root
    Manifest.from_config(config, "settings", pack_id="the-build-on-screen").write(root)

    monkeypatch.setattr(cli, "_load", lambda: config)
    monkeypatch.setattr(cli, "read_power", lambda: power.PowerState(True, 100))
    monkeypatch.setattr(
        producer, "run_jobs", lambda *a, **k: pytest.fail("dry run launched the game")
    )

    assert cli.main(["render", "--dry-run"]) == 0
    assert "jobs queued" in capsys.readouterr().out
    assert Manifest.read(root).pack_id == "the-build-on-screen"
