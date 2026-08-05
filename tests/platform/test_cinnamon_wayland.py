"""Tests for Cinnamon Wayland's read-only Muffin integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult
from docking.platform.backends.cinnamon.muffin import MuffinWindowService
from docking.platform.backends.cinnamon.session import CinnamonWaylandSessionBackend


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[
                SimpleNamespace(desktop_id="firefox.desktop", wm_class="firefox")
            ]
        ),
        update_running=MagicMock(),
    )


def _launcher() -> SimpleNamespace:
    return SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock())


def _layer_shell() -> SimpleNamespace:
    return SimpleNamespace(
        Edge=SimpleNamespace(TOP=1, BOTTOM=2, LEFT=4, RIGHT=8),
        Layer=SimpleNamespace(TOP=1),
        KeyboardMode=SimpleNamespace(NONE=0),
        init_for_window=MagicMock(),
        set_namespace=MagicMock(),
        set_layer=MagicMock(),
        set_keyboard_mode=MagicMock(),
        set_anchor=MagicMock(),
        set_margin=MagicMock(),
        set_monitor=MagicMock(),
        set_size=MagicMock(),
        set_exclusive_zone=MagicMock(),
    )


def test_muffin_window_service_publishes_read_only_state(monkeypatch):
    model = _model()
    client = SimpleNamespace(
        list_windows=MagicMock(
            return_value=(
                {
                    "id": 42,
                    "title": "Browser",
                    "app-id": "firefox",
                    "focused": True,
                    "demands-attention": True,
                    "workspace": 2,
                    "frame-rect": (10, 20, 800, 600),
                    "skip-taskbar": False,
                },
            )
        )
    )
    service = MuffinWindowService(
        model=model,
        launcher=_launcher(),
        client=client,
    )
    monkeypatch.setattr(
        service._matcher,
        "match",
        MagicMock(return_value="firefox.desktop"),
    )

    service.refresh()

    snapshot = service.list_windows("firefox.desktop")[0]
    assert snapshot.active is True
    assert snapshot.urgent is True
    assert snapshot.geometry is not None
    assert snapshot.geometry.width == 800
    assert snapshot.workspace_id == "2"
    assert snapshot.can_activate is False
    assert service.activate(snapshot.id) is ActionResult.UNSUPPORTED
    running = model.update_running.call_args.kwargs["running"]
    assert running["firefox.desktop"].active is True
    assert running["firefox.desktop"].urgent is True


def test_muffin_window_service_uses_snapshot_identity_fallbacks(monkeypatch):
    model = _model()
    client = SimpleNamespace(
        list_windows=MagicMock(
            return_value=(
                {
                    "id": 42,
                    "title": "Browser",
                    "app-id": "",
                    "gtk-application-id": "",
                    "sandboxed-app-id": "",
                    "wm-class": "Firefox",
                    "skip-taskbar": False,
                },
            )
        )
    )
    service = MuffinWindowService(
        model=model,
        launcher=_launcher(),
        client=client,
    )
    match = MagicMock(return_value="firefox.desktop")
    monkeypatch.setattr(service._matcher, "match", match)

    service.refresh()

    match.assert_called_once_with("Firefox")
    snapshot = service.list_windows("firefox.desktop")[0]
    assert snapshot.app_id == "Firefox"


def test_cinnamon_session_advertises_only_available_window_capabilities(monkeypatch):
    monkeypatch.setattr(
        "docking.platform.backends.wayland.session.WaylandProtocolRuntime.start",
        lambda _runtime: False,
    )
    backend = CinnamonWaylandSessionBackend(
        layer_shell=_layer_shell(),
        model=_model(),
        launcher=_launcher(),
        client=SimpleNamespace(list_windows=MagicMock(return_value=())),
    )

    assert backend.name == "cinnamon-wayland"
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.tracks_attention is True
    assert backend.capabilities.tracks_window_geometry is True
    assert backend.capabilities.supports_activate is False
    assert backend.capabilities.supports_close is False
