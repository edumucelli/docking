"""Tests for GameScope's pre-GTK Wayland environment preparation."""

import os

from docking.platform.gamescope import prepare_gamescope_wayland_environment


def test_gamescope_private_socket_becomes_native_wayland_display(monkeypatch):
    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "gamescope-7")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("GDK_BACKEND", raising=False)

    assert prepare_gamescope_wayland_environment() is True
    assert os.environ["WAYLAND_DISPLAY"] == "gamescope-7"
    assert os.environ["GDK_BACKEND"] == "wayland"


def test_gamescope_bootstrap_preserves_explicit_display_choices(monkeypatch):
    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "gamescope-7")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("GDK_BACKEND", "x11")

    assert prepare_gamescope_wayland_environment() is True
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"
    assert os.environ["GDK_BACKEND"] == "x11"


def test_gamescope_bootstrap_is_inactive_outside_gamescope(monkeypatch):
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("GDK_BACKEND", raising=False)

    assert prepare_gamescope_wayland_environment() is False
    assert "WAYLAND_DISPLAY" not in os.environ
