"""Pre-GTK display choices for a Cinnamon dock without native layer-shell."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.cinnamon import startup as cinnamon


@pytest.fixture
def display_probe(monkeypatch):
    for name in ("GDK_BACKEND", "DOCKING_BACKEND", "GAMESCOPE_WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_SESSION_DESKTOP", "cinnamon")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":7")
    probe = MagicMock(return_value=MagicMock(returncode=42))
    monkeypatch.setattr(cinnamon.subprocess, "run", probe)
    return probe


@pytest.mark.parametrize("requested", ["", "cinnamon", "cinnamon-wayland"])
def test_verified_xwayland_surface_keeps_native_session_choice(
    monkeypatch, display_probe, requested
):
    fake_gtk = types.SimpleNamespace(init_check=MagicMock(return_value=(True, [])))
    fake_repo = types.SimpleNamespace(Gtk=fake_gtk)
    fake_gi = types.SimpleNamespace(require_version=MagicMock(), repository=fake_repo)
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    monkeypatch.setenv("DOCKING_BACKEND", requested)
    assert cinnamon.prepare_cinnamon_display()
    fake_gtk.init_check.assert_called_once_with()
    assert "GDK_BACKEND" not in os.environ
    assert os.environ["DOCKING_BACKEND"] == requested
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    args, kwargs = display_probe.call_args
    assert args[0][0] == sys.executable
    assert kwargs["env"]["GDK_BACKEND"] == "wayland,x11"
    assert kwargs["env"]["DISPLAY"] == ":7"
    assert kwargs["timeout"] == 3


@pytest.mark.parametrize("result", [0, 1, 2, -11])
def test_native_layer_shell_and_unverified_probes_preserve_display(
    display_probe, result
):
    display_probe.return_value.returncode = result
    assert not cinnamon.prepare_cinnamon_display()
    assert "GDK_BACKEND" not in os.environ


@pytest.mark.parametrize(
    "error", [OSError("missing interpreter"), subprocess.TimeoutExpired("probe", 3)]
)
def test_probe_failure_does_not_force_an_unverified_display(display_probe, error):
    display_probe.side_effect = error
    assert not cinnamon.prepare_cinnamon_display()
    assert "GDK_BACKEND" not in os.environ


@pytest.mark.parametrize("requested", ["wayland", "x11", "wayland,x11"])
def test_explicit_gtk_display_wins(monkeypatch, display_probe, requested):
    monkeypatch.setenv("GDK_BACKEND", requested)
    assert not cinnamon.prepare_cinnamon_display()
    assert os.environ["GDK_BACKEND"] == requested
    display_probe.assert_not_called()


@pytest.mark.parametrize("requested", ["reduced", "x11", "layer-shell", "gnome-shell"])
def test_explicit_other_session_backend_wins(monkeypatch, display_probe, requested):
    monkeypatch.setenv("DOCKING_BACKEND", requested)
    assert not cinnamon.prepare_cinnamon_display()
    assert "GDK_BACKEND" not in os.environ
    display_probe.assert_not_called()


@pytest.mark.parametrize(
    ("name", "value"),
    [("XDG_SESSION_TYPE", "x11"), ("XDG_SESSION_DESKTOP", "gnome"), ("DISPLAY", "")],
)
def test_other_sessions_and_missing_xwayland_are_not_probed(
    monkeypatch, display_probe, name, value
):
    monkeypatch.setenv(name, value)
    assert not cinnamon.prepare_cinnamon_display()
    assert "GDK_BACKEND" not in os.environ
    display_probe.assert_not_called()
