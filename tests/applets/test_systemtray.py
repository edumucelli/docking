"""Tests for the System Tray applet helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from gi.repository import GLib

import docking.applets.systemtray.applet as applet_mod
from docking.applets.services import AppletServices
from docking.applets.systemtray.applet import SystemTrayApplet
from docking.applets.systemtray.render import create_status_tray_icon
from docking.applets.systemtray.state import tooltip_text
from docking.core.config import Config
from docking.platform.status_notifier.backend import (
    DEFAULT_ITEM_PATH,
    RegisteredItemAddress,
    StatusTrayState,
    parse_registered_item,
    tray_item_from_properties,
    unavailable_state,
)
from docking.platform.status_notifier.dbusmenu import parse_menu_node


class TestRegisteredItemParsing:
    def test_service_only_uses_default_path(self):
        address = parse_registered_item("org.example.Tray")

        assert address == RegisteredItemAddress(
            service="org.example.Tray",
            path=DEFAULT_ITEM_PATH,
        )

    def test_service_and_path(self):
        address = parse_registered_item(":1.42/Tray/Icon")

        assert address == RegisteredItemAddress(service=":1.42", path="/Tray/Icon")

    def test_path_only_uses_sender_service(self):
        address = parse_registered_item(
            "/StatusNotifierItem",
            default_service=":1.99",
        )

        assert address == RegisteredItemAddress(
            service=":1.99",
            path="/StatusNotifierItem",
        )

    def test_path_only_without_sender_is_invalid(self):
        assert parse_registered_item("/StatusNotifierItem") is None
        assert parse_registered_item("") is None


class TestTrayItemParsing:
    def test_tray_item_from_status_notifier_properties(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(
                service="org.example.App",
                path="/StatusNotifierItem",
            ),
            properties={
                "Id": "example-id",
                "Title": "Example",
                "Status": "Active",
                "Category": "ApplicationStatus",
                "IconName": "example-icon",
                "AttentionIconName": "example-alert",
                "IconThemePath": "/tmp/icons",
                "IconPixmap": [(1, 1, [255, 17, 34, 51])],
                "Menu": "/Menu",
                "ToolTip": ("", [], "Tooltip title", "Tooltip body"),
                "ItemIsMenu": True,
            },
        )

        assert item.identifier == "org.example.App/StatusNotifierItem"
        assert item.item_id == "example-id"
        assert item.display_title == "Example"
        assert item.effective_icon_name == "example-icon"
        assert item.menu_path == "/Menu"
        assert item.icon_theme_path == "/tmp/icons"
        assert item.icon_pixmap is not None
        assert item.icon_pixmap.width == 1
        assert item.icon_pixmap.height == 1
        assert item.icon_pixmap.rgba == b"\x11\x22\x33\xff"
        assert item.tooltip_title == "Tooltip title"
        assert item.tooltip_text == "Tooltip body"
        assert item.item_is_menu is True

    def test_attention_icon_wins_when_status_needs_attention(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.example.App", path="/Item"),
            properties={
                "Title": "Example",
                "Status": "NeedsAttention",
                "IconName": "normal",
                "AttentionIconName": "alert",
            },
        )

        assert item.effective_icon_name == "alert"


class TestTooltipText:
    def test_unavailable_state_mentions_error(self):
        assert "session bus unavailable" in tooltip_text(
            unavailable_state("session bus unavailable")
        )

    def test_host_waiting_text(self):
        text = tooltip_text(
            StatusTrayState(available=True, watcher_mode="host", items=())
        )

        assert text == "System Tray: waiting for tray apps"

    def test_legacy_tray_owner_text(self):
        text = tooltip_text(
            StatusTrayState(
                available=True,
                watcher_mode="watcher",
                items=(),
                legacy_tray_owner="notification-area-applet",
            )
        )

        assert text == "System Tray: legacy tray owned by notification-area-applet"

    def test_lists_item_titles(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.example.App", path="/Item"),
            properties={"Title": "Example", "Status": "Active"},
        )

        assert "- Example" in tooltip_text(
            StatusTrayState(available=True, watcher_mode="watcher", items=(item,))
        )


class TestDBusMenuParsing:
    def test_parse_menu_tree(self):
        root = parse_menu_node(
            (
                0,
                {},
                [
                    (
                        1,
                        {
                            "label": "_Open",
                            "enabled": True,
                            "icon-name": "document-open",
                        },
                        [],
                    ),
                    (2, {"type": "separator"}, []),
                    (
                        3,
                        {
                            "label": "_Enabled",
                            "toggle-type": "checkmark",
                            "toggle-state": 1,
                            "icon-data": [1, 2, 3],
                        },
                        [],
                    ),
                ],
            )
        )

        assert root is not None
        assert [child.label for child in root.children] == ["Open", "", "Enabled"]
        assert root.children[0].icon_name == "document-open"
        assert root.children[1].is_separator is True
        assert root.children[2].toggle_type == "checkmark"
        assert root.children[2].toggle_state == 1
        assert root.children[2].icon_data == b"\x01\x02\x03"

    def test_parse_glib_variant_node(self):
        root = parse_menu_node(
            GLib.Variant(
                "(ia{sv}av)",
                (
                    0,
                    {"label": GLib.Variant("s", "_Root")},
                    [],
                ),
            )
        )

        assert root is not None
        assert root.label == "Root"


def test_create_status_tray_icon_dimensions():
    pixbuf = create_status_tray_icon(size=48, available=True, item_count=3)

    assert pixbuf is not None
    assert pixbuf.get_width() == 48
    assert pixbuf.get_height() == 48


class _FakeService:
    """Stand-in for the shared StatusNotifierService."""

    def __init__(self) -> None:
        self.state = unavailable_state()
        self.listeners: list = []
        self.started = 0
        self.stopped = 0
        self.refreshes = 0
        self.activated: list[str] = []
        self.context_menus: list[str] = []

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)
        listener(self.state)

    def remove_listener(self, listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def refresh(self) -> None:
        self.refreshes += 1

    def activate(self, identifier: str) -> bool:
        self.activated.append(identifier)
        return True

    def context_menu(self, identifier: str) -> bool:
        self.context_menus.append(identifier)
        return True

    def menu_client(self, identifier: str):
        return None

    def publish(self, state: StatusTrayState) -> None:
        self.state = state
        for listener in tuple(self.listeners):
            listener(state)


class _ImmediateWorker:
    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


@pytest.fixture
def tray(monkeypatch):
    """A SystemTrayApplet with a fake private service and worker."""
    created: list[_FakeService] = []

    def factory(**_kwargs) -> _FakeService:
        service = _FakeService()
        created.append(service)
        return service

    monkeypatch.setattr(applet_mod, "StatusNotifierService", factory)
    monkeypatch.setattr(
        applet_mod, "BackgroundWorker", lambda **_kwargs: _ImmediateWorker()
    )
    instance = SystemTrayApplet(48, config=Config())
    return SimpleNamespace(applet=instance, created=created)


class TestSystemTrayAppletService:
    def test_construction_does_not_read_the_tray(self, tray):
        # The private service has no get_state at all: a blocking read at
        # construction would fail here instead of freezing the dock.
        assert tray.created
        assert tray.created[0].refreshes == 0
        assert tray.created[0].started == 0

    def test_shared_service_replaces_the_private_one(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))

        assert tray.created[0].stopped == 1
        assert shared.listeners == [tray.applet._on_state_result]
        assert tray.applet._service is shared
        assert tray.applet._owns_service is False

    def test_state_from_the_shared_service_reaches_the_applet(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service=":1.42", path="/Tray"),
            properties={"Id": "x", "Title": "Example", "Status": "Active"},
        )
        state = StatusTrayState(available=True, watcher_mode="watcher", items=(item,))

        shared.publish(state)

        assert tray.applet._state == state
        assert tray.applet.item.name == tooltip_text(state)

    def test_stop_leaves_a_shared_service_running(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))

        tray.applet.stop()

        assert shared.stopped == 0
        assert shared.listeners == []

    def test_stop_stops_the_private_service(self, tray):
        tray.applet.stop()

        assert tray.created[0].stopped == 1

    def test_start_starts_only_the_private_service(self, tray):
        tray.applet.start(notify=lambda: None)
        assert tray.created[0].started == 1

        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))
        tray.applet.start(notify=lambda: None)

        assert shared.started == 0

    def test_refresh_now_does_not_block(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))

        tray.applet._refresh_now()

        assert shared.refreshes == 1

    def test_activate_runs_off_the_main_thread_and_repolls(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))

        tray.applet._on_activate(":1.42/Tray")

        assert shared.activated == [":1.42/Tray"]
        assert shared.refreshes == 1

    def test_resubscribing_drops_the_previous_listener(self, tray):
        first = _FakeService()
        second = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=first))

        tray.applet.set_services(AppletServices(status_notifier=second))

        assert first.listeners == []
        assert second.listeners == [tray.applet._on_state_result]
        assert tray.applet._service is second
