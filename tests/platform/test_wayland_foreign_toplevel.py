"""Tests for Wayland foreign-toplevel window tracking."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult, DisplayServer
from docking.platform.backends.wayland.toplevels import (
    STATE_ACTIVATED,
    STATE_MAXIMIZED,
    STATE_MINIMIZED,
    WaylandAppIdMatcher,
    WaylandForeignToplevelWindowService,
)


def _item(desktop_id: str, wm_class: str = "") -> SimpleNamespace:
    return SimpleNamespace(desktop_id=desktop_id, wm_class=wm_class)


def _launcher() -> SimpleNamespace:
    resolved = {
        "org.gnome.Nautilus.desktop": SimpleNamespace(
            desktop_id="org.gnome.Nautilus.desktop"
        ),
        "firefox.desktop": SimpleNamespace(desktop_id="firefox.desktop"),
    }
    aliases = {
        "firefox": SimpleNamespace(desktop_id="firefox.desktop"),
        "nautilus": SimpleNamespace(desktop_id="org.gnome.Nautilus.desktop"),
    }

    return SimpleNamespace(
        resolve=MagicMock(side_effect=lambda desktop_id, **_: resolved.get(desktop_id)),
        resolve_by_wm_class=MagicMock(
            side_effect=lambda wm_class: aliases.get(wm_class.lower())
        ),
    )


def _model(*items: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(return_value=list(items)),
        update_running=MagicMock(),
    )


def _protocol() -> SimpleNamespace:
    return SimpleNamespace(
        start=MagicMock(),
        stop=MagicMock(),
        activate=MagicMock(),
        close=MagicMock(),
        set_minimized=MagicMock(),
    )


def test_wayland_app_id_matcher_prefers_visible_items():
    launcher = _launcher()
    matcher = WaylandAppIdMatcher(launcher=launcher)
    matcher.sync_visible_items([_item("org.gnome.Nautilus.desktop")])

    assert matcher.match("org.gnome.Nautilus") == "org.gnome.Nautilus.desktop"
    launcher.resolve.assert_not_called()


def test_wayland_app_id_matcher_falls_back_to_launcher_aliases():
    launcher = _launcher()
    matcher = WaylandAppIdMatcher(launcher=launcher)
    matcher.sync_visible_items([])

    assert matcher.match("firefox") == "firefox.desktop"
    assert matcher.match("unknown") is None


def test_foreign_toplevel_service_publishes_running_state_and_snapshots():
    model = _model(_item("org.gnome.Nautilus.desktop"))
    protocol = _protocol()
    service = WaylandForeignToplevelWindowService(
        model=model,
        launcher=_launcher(),
        protocol=protocol,
    )
    handle = object()

    window_id = service.toplevel_created(handle)
    service.title_changed(handle, "Files")
    service.app_id_changed(handle, "org.gnome.Nautilus")
    service.state_changed(handle, [STATE_ACTIVATED, STATE_MAXIMIZED])
    service.done(handle)

    assert window_id.backend is DisplayServer.WAYLAND
    running = model.update_running.call_args.kwargs["running"]
    info = running["org.gnome.Nautilus.desktop"]
    assert info.count == 1
    assert info.active is True
    assert info.window_ids == (window_id,)

    snapshots = service.list_windows("org.gnome.Nautilus.desktop")
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.id == window_id
    assert snapshot.title == "Files"
    assert snapshot.app_id == "org.gnome.Nautilus"
    assert snapshot.active is True
    assert snapshot.maximized is True
    assert snapshot.can_activate is True
    assert snapshot.can_close is True
    assert snapshot.can_minimize is True
    assert snapshot.can_preview is False


def test_foreign_toplevel_service_actions_call_protocol_methods():
    model = _model(_item("firefox.desktop"))
    protocol = _protocol()
    service = WaylandForeignToplevelWindowService(
        model=model,
        launcher=_launcher(),
        protocol=protocol,
    )
    handle = object()
    window_id = service.toplevel_created(handle)
    service.app_id_changed(handle, "firefox")
    service.state_changed(handle, [STATE_MINIMIZED])
    service.done(handle)

    assert service.activate(window_id) is ActionResult.OK
    assert service.close(window_id) is ActionResult.OK
    assert service.activate_most_recent("firefox.desktop") is ActionResult.OK
    assert service.minimize_all("firefox.desktop") is ActionResult.OK
    assert service.close_all("firefox.desktop") is ActionResult.OK

    protocol.activate.assert_any_call(handle)
    protocol.close.assert_any_call(handle)
    protocol.set_minimized.assert_any_call(handle)


def test_foreign_toplevel_service_removes_closed_windows():
    model = _model(_item("firefox.desktop"))
    service = WaylandForeignToplevelWindowService(
        model=model,
        launcher=_launcher(),
        protocol=_protocol(),
    )
    handle = object()
    service.toplevel_created(handle)
    service.app_id_changed(handle, "firefox")
    service.done(handle)

    service.closed(handle)

    assert service.list_windows("firefox.desktop") == ()
    assert model.update_running.call_args.kwargs["running"] == {}


def test_foreign_toplevel_service_returns_not_found_for_missing_windows():
    service = WaylandForeignToplevelWindowService(
        model=_model(),
        launcher=_launcher(),
        protocol=_protocol(),
    )

    assert service.activate_most_recent("missing.desktop") is ActionResult.NOT_FOUND
    assert service.close_all("missing.desktop") is ActionResult.NOT_FOUND
