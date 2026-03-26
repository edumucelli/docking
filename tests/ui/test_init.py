"""Tests for lazy imports in docking.ui package."""

from __future__ import annotations

import sys
import types

import pytest

import docking.ui as ui_pkg


def test_getattr_loads_dock_window_from_submodule(monkeypatch):
    sentinel = object()
    module = types.ModuleType("docking.ui.dock_window")
    module.DockWindow = sentinel
    monkeypatch.setitem(sys.modules, "docking.ui.dock_window", module)

    assert ui_pkg.__getattr__("DockWindow") is sentinel


def test_getattr_loads_renderer_from_submodule(monkeypatch):
    sentinel = object()
    module = types.ModuleType("docking.ui.renderer")
    module.DockRenderer = sentinel
    monkeypatch.setitem(sys.modules, "docking.ui.renderer", module)

    assert ui_pkg.__getattr__("DockRenderer") is sentinel


def test_getattr_rejects_unknown_name():
    with pytest.raises(AttributeError):
        ui_pkg.__getattr__("UnknownThing")
