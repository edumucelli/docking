"""Tests for session backend selection."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends import selection


def test_create_session_backend_selects_x11_backend(monkeypatch):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock()
    backend_cls = MagicMock(return_value=backend)
    x11_session = MagicMock(X11SessionBackend=backend_cls)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docking.platform.backends.x11.session":
            return x11_session
        return real_import(name, globals, locals, fromlist, level)

    real_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    config = MagicMock()
    launcher = MagicMock()
    model = MagicMock()
    result = selection.create_session_backend(
        config=config,
        launcher=launcher,
        model=model,
    )

    assert result is backend
    backend_cls.assert_called_once_with(model=model, launcher=launcher, config=config)


def test_create_session_backend_selects_reduced_for_non_x11_without_x11_import(
    monkeypatch,
):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "is_wayland_session", lambda: True)
    monkeypatch.setattr(selection, "backend_name", lambda: "GdkWayland.WaylandDisplay")

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docking.platform.backends.x11.session":
            raise AssertionError("reduced backend should not import X11 session")
        return real_import(name, globals, locals, fromlist, level)

    real_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result.name == "reduced"


def test_create_session_backend_can_select_reduced_backend_by_override(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "reduced")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docking.platform.backends.x11.session":
            raise AssertionError("reduced backend should not import X11 session")
        return real_import(name, globals, locals, fromlist, level)

    real_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result.name == "reduced"


def test_create_session_backend_can_force_x11_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "x11")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    backend = MagicMock()
    backend_cls = MagicMock(return_value=backend)
    x11_session = MagicMock(X11SessionBackend=backend_cls)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docking.platform.backends.x11.session":
            return x11_session
        return real_import(name, globals, locals, fromlist, level)

    real_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)
    config = MagicMock()
    launcher = MagicMock()
    model = MagicMock()

    result = selection.create_session_backend(
        config=config,
        launcher=launcher,
        model=model,
    )

    assert result is backend
    backend_cls.assert_called_once_with(model=model, launcher=launcher, config=config)
