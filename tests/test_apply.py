"""The display path must not try to own the wallpaper daemon's lifetime."""

import subprocess

import pytest

from mcwall import apply as apply_mod


def test_send_fails_when_the_daemon_is_not_running(monkeypatch):
    """It must raise, not silently start a daemon systemd would then reap."""
    spawned = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: spawned.append(a) or pytest.fail("spawned")
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "no daemon socket"),
    )
    with pytest.raises(apply_mod.NoWallpaperDaemon, match="could not reach its daemon"):
        apply_mod._send(["/nix/store/x/bin/awww", "img", "a.webp"], "/nix/store/x/bin/awww-daemon")
    assert spawned == []


def test_send_succeeds_quietly_when_the_daemon_is_up(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    apply_mod._send(["awww", "img", "a.webp"], "awww-daemon")


def test_the_error_names_the_daemon_to_start(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )
    with pytest.raises(apply_mod.NoWallpaperDaemon) as error:
        apply_mod._send(["/p/awww", "img", "x"], "/p/awww-daemon")
    message = str(error.value)
    assert "awww-daemon" in message
    assert "systemd" in message
