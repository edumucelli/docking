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
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.UNKNOWN)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    monkeypatch.setattr(selection, "backend_name", lambda: "GdkWayland.WaylandDisplay")
    monkeypatch.setattr(selection, "_wayfire_ipc_available", lambda: False)
    monkeypatch.setattr(
        selection, "_create_wayland_layer_shell_backend", lambda **_: None
    )
    monkeypatch.setattr(
        selection, "_create_gnome_shell_bridge_backend", lambda **_: None
    )

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


def test_create_session_backend_explains_cage_reduced_mode(monkeypatch):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.CAGE)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    monkeypatch.setattr(selection, "_wayfire_ipc_available", lambda: False)
    monkeypatch.setattr(
        selection, "_create_wayland_layer_shell_backend", lambda **_: None
    )
    monkeypatch.setattr(
        selection, "_create_gnome_shell_bridge_backend", lambda **_: None
    )
    create_reduced = MagicMock(return_value=MagicMock(name="reduced"))
    monkeypatch.setattr(selection, "_create_reduced_backend", create_reduced)

    selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    reason = create_reduced.call_args.kwargs["reason"]
    assert "single-application kiosk" in reason
    assert "layer-shell" in reason


def test_create_session_backend_selects_layer_shell_for_supported_wayland(monkeypatch):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.UNKNOWN)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    monkeypatch.setattr(selection, "_wayfire_ipc_available", lambda: False)
    backend = MagicMock(name="wayland-layer-shell")
    create_wayland = MagicMock(return_value=backend)
    monkeypatch.setattr(
        selection, "_create_wayland_layer_shell_backend", create_wayland
    )

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_wayland.assert_called_once()


def test_create_session_backend_selects_gnome_bridge_after_layer_shell_fallback(
    monkeypatch,
):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.UNKNOWN)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    monkeypatch.setattr(selection, "_wayfire_ipc_available", lambda: False)
    monkeypatch.setattr(
        selection, "_create_wayland_layer_shell_backend", lambda **_: None
    )
    backend = MagicMock(name="gnome-shell-bridge")
    create_gnome = MagicMock(return_value=backend)
    monkeypatch.setattr(selection, "_create_gnome_shell_bridge_backend", create_gnome)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_gnome.assert_called_once()


def test_create_session_backend_can_force_layer_shell_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "wayland-layer-shell")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock(name="wayland-layer-shell")
    create_wayland = MagicMock(return_value=backend)
    monkeypatch.setattr(
        selection, "_create_wayland_layer_shell_backend", create_wayland
    )

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_wayland.assert_called_once()


def test_create_session_backend_can_force_gnome_bridge_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "gnome-shell")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock(name="gnome-shell-bridge")
    create_gnome = MagicMock(return_value=backend)
    monkeypatch.setattr(selection, "_create_gnome_shell_bridge_backend", create_gnome)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_gnome.assert_called_once()


def test_create_session_backend_can_force_hyprland_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "hyprland")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock(name="hyprland")
    create_hyprland = MagicMock(return_value=backend)
    monkeypatch.setattr(selection, "_create_hyprland_backend", create_hyprland)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_hyprland.assert_called_once()


def test_create_session_backend_auto_selects_hyprland_before_generic_wayland(
    monkeypatch,
):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.HYPRLAND)
    backend = MagicMock(name="hyprland")
    create_hyprland = MagicMock(return_value=backend)
    create_wayland = MagicMock()
    monkeypatch.setattr(selection, "_create_hyprland_backend", create_hyprland)
    monkeypatch.setattr(
        selection,
        "_create_wayland_layer_shell_backend",
        create_wayland,
    )

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_hyprland.assert_called_once()
    create_wayland.assert_not_called()


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


def test_create_session_backend_can_force_niri_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "niri")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock(name="niri")
    create_niri = MagicMock(return_value=backend)
    monkeypatch.setattr(selection, "_create_niri_backend", create_niri)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_niri.assert_called_once()


def test_create_session_backend_can_force_wayfire_backend(monkeypatch):
    monkeypatch.setenv("DOCKING_BACKEND", "wayfire")
    monkeypatch.setattr(selection, "is_x11_backend", lambda: True)
    backend = MagicMock(name="wayfire")
    create_wayfire = MagicMock(return_value=backend)
    monkeypatch.setattr(selection, "_create_wayfire_backend", create_wayfire)

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_wayfire.assert_called_once()


def test_create_session_backend_auto_selects_niri_before_generic_wayland(
    monkeypatch,
):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.NIRI)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    backend = MagicMock(name="niri")
    create_niri = MagicMock(return_value=backend)
    create_wayland = MagicMock()
    monkeypatch.setattr(selection, "_create_niri_backend", create_niri)
    monkeypatch.setattr(
        selection,
        "_create_wayland_layer_shell_backend",
        create_wayland,
    )

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_niri.assert_called_once()
    create_wayland.assert_not_called()


def test_create_session_backend_auto_selects_wayfire_before_generic_wayland(
    monkeypatch,
):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)
    monkeypatch.setattr(selection, "is_x11_backend", lambda: False)
    monkeypatch.setattr(selection, "detect_desktop", lambda: selection.Desktop.WAYFIRE)
    monkeypatch.setattr(selection, "is_kde_session", lambda: False)
    backend = MagicMock(name="wayfire")
    create_wayfire = MagicMock(return_value=backend)
    create_wayland = MagicMock()
    monkeypatch.setattr(selection, "_create_wayfire_backend", create_wayfire)
    monkeypatch.setattr(
        selection,
        "_create_wayland_layer_shell_backend",
        create_wayland,
    )

    result = selection.create_session_backend(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
    )

    assert result is backend
    create_wayfire.assert_called_once()
    create_wayland.assert_not_called()
