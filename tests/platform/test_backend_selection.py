"""Tests for session backend selection."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends import selection


def test_create_session_backend_selects_x11_backend(monkeypatch):
    backend = MagicMock()
    builder = MagicMock(return_value=backend)
    x11_session = MagicMock(build_x11_session_backend=builder)

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
    builder.assert_called_once_with(model=model, launcher=launcher, config=config)
