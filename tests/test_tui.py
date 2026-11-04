"""The TUI's non-drawing half.

The window itself is checked by a smoke test that drives a real terminal;
everything worth asserting about — progress arithmetic, which job is next,
whether the command list matches the CLI — lives in plain functions here.
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from mcwall.tui import layout, state

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)


def write_status(directory, **overrides):
    payload = {
        "total": 1727,
        "completed": 71,
        "skipped": 0,
        "failed": 0,
        "remaining": 1656,
        "done": False,
        "updated_at": int(NOW.timestamp() * 1000),
        "elapsed_ms": 747_000,
        "current": "early_autumn/rain/13550",
    }
    payload.update(overrides)
    (directory / state.STATUS_NAME).write_text(json.dumps(payload))
    return payload


# ----------------------------------------------------------------- progress


def test_progress_matches_the_mod_status_file(tmp_path):
    write_status(tmp_path)
    progress = state.read_progress(tmp_path)

    assert progress.finished == 71
    assert progress.total == 1727
    assert progress.per_still_seconds == pytest.approx(747 / 71, rel=1e-3)
    # 1656 left at ~10.5s each is a little under five hours.
    assert progress.eta_seconds() == pytest.approx(1656 * 747 / 71, rel=1e-3)
    assert progress.age_seconds(NOW + timedelta(seconds=30)) == pytest.approx(30)


def test_a_torn_status_file_is_not_an_error(tmp_path):
    """The mod rewrites it as we read it, so half a file is expected."""
    (tmp_path / state.STATUS_NAME).write_text('{"total": 17')
    assert state.read_progress(tmp_path) is None
    assert state.read_progress(tmp_path / "nowhere") is None


def test_skipped_stills_count_as_finished(tmp_path):
    """A resumed run starts with thousands already present."""
    write_status(tmp_path, completed=10, skipped=1700)
    progress = state.read_progress(tmp_path)
    assert progress.finished == 1710
    assert progress.fraction == pytest.approx(1710 / 1727)


def test_a_finished_batch_has_no_eta(tmp_path):
    write_status(tmp_path, done=True, completed=1727)
    assert state.read_progress(tmp_path).eta_seconds() is None


# --------------------------------------------------------------------- jobs


JOBS = [
    {"id": "a", "output": "build/a.png"},
    {"id": "b", "output": "build/b.png"},
    {"id": "c", "output": "build/c.png"},
    {"id": "d", "output": "build/d.png"},
]


def test_the_job_before_the_current_one_is_the_last_picture_taken():
    view = state.job_view(JOBS, "c", ahead=2)
    assert view.previous["id"] == "b"
    assert view.current["id"] == "c"
    assert [j["id"] for j in view.upcoming] == ["d"]
    assert view.index == 2 and view.total == 4


def test_the_first_job_has_nothing_before_it():
    """Index 0 must not wrap round to the end of the batch."""
    view = state.job_view(JOBS, "a")
    assert view.previous is None


def test_an_unknown_current_job_still_shows_the_queue():
    view = state.job_view(JOBS, "not-in-this-batch", ahead=2)
    assert view.current is None
    assert [j["id"] for j in view.upcoming] == ["a", "b"]


def test_no_jobs_is_not_an_error():
    assert state.job_view([], None).total == 0


def test_a_capture_is_found_in_staging_then_in_the_library(tmp_path):
    staging, library = tmp_path / "staging", tmp_path / "library"
    (staging / "build").mkdir(parents=True)
    (library / "build").mkdir(parents=True)
    job = {"id": "a", "output": "build/a.png"}

    assert state.job_image(job, library, staging, "webp") is None

    (library / "build/a.webp").write_bytes(b"")
    assert state.job_image(job, library, staging, "webp").suffix == ".webp"

    # A run in flight has the lossless capture, which wins.
    (staging / "build/a.png").write_bytes(b"")
    assert state.job_image(job, library, staging, "webp").suffix == ".png"


# ------------------------------------------------------------------- worlds


class FakeCamera:
    def as_tuple(self):
        return (1.0, 2.0, 3.0, 90.0, 0.0)


def test_a_world_that_cannot_be_read_still_appears(tmp_path):
    """It is exactly the world you still need to open and frame."""

    def read_camera(world):
        if world.name == "fresh":
            raise RuntimeError("no saved player position")
        return FakeCamera()

    rows = state.world_rows(
        [tmp_path / "chosen", tmp_path / "fresh", tmp_path / "chosen-snow"],
        lambda _w: 42,
        read_camera,
        selected="chosen",
    )
    by_name = {row.name: row for row in rows}
    assert by_name["chosen"].is_selected
    assert not by_name["fresh"].is_selected
    assert "no saved player position" in by_name["fresh"].error
    assert by_name["fresh"].camera is None
    assert by_name["chosen-snow"].is_variant, "derived copies are not render targets"


# ----------------------------------------------------------------- commands


def test_the_command_list_is_the_cli(tmp_path):
    """The TUI must never offer a command the CLI does not have, or miss one."""
    from mcwall.cli import build_parser

    parser = build_parser()
    subparsers = next(a for a in parser._actions if getattr(a, "dest", None) == "command")
    specs = state.command_specs(parser)

    assert {s.name for s in specs} == set(subparsers.choices)
    assert all(s.help for s in specs), "every command needs a one-line summary"

    render = next(s for s in specs if s.name == "render")
    kinds = {o.flags[-1]: o.kind for o in render.options}
    assert kinds["--dry-run"] == "flag"
    assert kinds["--limit"] == "value"
    assert kinds["--world"] == "value"


def test_the_form_builds_the_command_you_would_have_typed():
    assert state.build_argv("render", {}) == ["render"]
    assert state.build_argv(
        "render", {"--dry-run": True, "--limit": "5", "--world": "", "--force": False}
    ) == ["render", "--dry-run", "--limit", "5"]


# ------------------------------------------------------------------- layout


def test_the_bar_is_never_full_before_the_work_is():
    assert layout.progress_bar(0.999, 10).count("█") == 9
    assert layout.progress_bar(1.0, 10) == "█" * 10
    assert layout.progress_bar(0.0, 10).count("█") == 0


def test_a_stopped_client_is_reported_not_hidden(tmp_path):
    """A batch that died looks exactly like a slow one unless we say so."""
    from mcwall.tui.app import Snapshot

    write_status(tmp_path)
    snapshot = Snapshot(
        build_key="b",
        progress=state.read_progress(tmp_path),
        jobs=state.job_view(JOBS, "c"),
        client_running=False,
    )
    text = " ".join(line.text for line in layout.dashboard(snapshot, 80))
    assert "stopped early" in text


def test_failures_are_shown(tmp_path):
    from mcwall.tui.app import Snapshot

    write_status(tmp_path, failed=3)
    snapshot = Snapshot(progress=state.read_progress(tmp_path), client_running=True)
    lines = layout.dashboard(snapshot, 80)
    assert any("3 failed" in line.text and line.style == layout.ERR for line in lines)


def test_durations_read_as_time():
    assert layout.human_duration(None) == "—"
    assert layout.human_duration(45) == "45s"
    assert layout.human_duration(750) == "12m 30s"
    assert layout.human_duration(17460) == "4h 51m"


# -------------------------------------------------------------- the window


def terminal_available() -> bool:
    """curses needs a terminfo database, which a build sandbox may not have."""
    import curses

    try:
        curses.setupterm(term="xterm-256color", fd=1)
    except Exception:  # noqa: BLE001
        return False
    return True


@pytest.mark.skipif(not terminal_available(), reason="no terminfo for xterm-256color")
def test_the_window_opens_draws_every_view_and_quits(tmp_path):
    """The one thing the pure tests cannot catch: curses blowing up on draw.

    Drives a real pty through each view and out again. Any traceback in the
    output, or a non-zero exit, is a failure — a crashed TUI still "renders"
    perfectly in the layout tests.
    """
    import os
    import pty
    import subprocess

    env = dict(os.environ, TERM="xterm-256color", LINES="40", COLUMNS="120")
    env.pop("MCWALL_CONFIG", None)
    primary, secondary = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "mcwall.cli", "tui"],
        stdin=secondary, stdout=secondary, stderr=subprocess.PIPE,
        env=env, text=True,
    )
    os.close(secondary)
    try:
        time.sleep(2.0)
        # Every view, then the command form, then out. Never 'r' or 'e':
        # those launch the game.
        for key in "wjjcjjo d":
            os.write(primary, key.encode())
            time.sleep(0.12)
        os.write(primary, b"q")
        code = process.wait(timeout=20)
        errors = process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        os.close(primary)

    assert "Traceback" not in errors, errors
    assert code == 0, f"exit {code}: {errors}"
