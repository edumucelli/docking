"""Tests for the System Tray applet helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import GLib, Gtk

import docking.applets.systemtray.applet as applet_mod
import tests.platform_factories as _native_tests_platform_factories
from docking.applets.services import AppletServices
from docking.applets.systemtray.applet import SystemTrayApplet
from docking.applets.systemtray.render import create_status_tray_icon
from docking.applets.systemtray.state import tooltip_text
from docking.core.config import Config
from docking.platform.tray.implementations.unix.dbusmenu import (
    DBusMenuClient,
    DBusMenuItem,
    DBusMenuLayout,
    parse_menu_node,
)
from docking.platform.tray.implementations.unix.status_notifier import (
    DEFAULT_ITEM_PATH,
    RegisteredItemAddress,
    StatusTrayState,
    parse_registered_item,
    tray_item_from_properties,
    unavailable_state,
)
from tests.platform_factories import make_applet_platform


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


def _tray_state(title: str) -> StatusTrayState:
    item = tray_item_from_properties(
        address=RegisteredItemAddress(service=":1.42", path="/Tray"),
        properties={"Id": "example", "Title": title, "Status": "Active"},
    )
    return StatusTrayState(available=True, watcher_mode="watcher", items=(item,))


class _FakeService:
    """Stand-in for the shared StatusNotifierService."""

    def __init__(self) -> None:
        self.state = unavailable_state()
        self.menu_client_result = None
        self.listeners: list = []
        self.started = 0
        self.stopped = 0
        self.refreshes = 0
        self.activated: list[str] = []
        self.context_menus: list[str] = []

    def add_listener(self, listener) -> None:
        if listener not in self.listeners:
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
        return self.menu_client_result

    def publish(self, state: StatusTrayState) -> None:
        self.state = state
        for listener in tuple(self.listeners):
            listener(state)


def _menu_layout() -> DBusMenuLayout:
    return DBusMenuLayout(
        revision=1,
        root=DBusMenuItem(
            item_id=0,
            label="",
            children=(DBusMenuItem(item_id=1, label="Open"),),
        ),
    )


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


class _DeferredWorker:
    """Collect worker tasks so a test can deliver them after the applet stops."""

    def __init__(self) -> None:
        self.tasks: list = []

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        self.tasks.append((fn, on_result, on_error))

    def deliver(self) -> None:
        fn, on_result, on_error = self.tasks.pop(0)
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


class _FakeMenuClient(DBusMenuClient):
    """A DBusMenu client that answers with one canned layout."""

    def __init__(self, layout: DBusMenuLayout | None) -> None:
        self.layout = layout

    def about_to_show(self, item_id: int) -> bool:
        return False

    def get_layout(self) -> DBusMenuLayout | None:
        return self.layout

    def event(self, item_id: int, event_id: str = "clicked") -> bool:
        return True


def _make_tray(monkeypatch, *, worker=None, service=None) -> SimpleNamespace:
    """A SystemTrayApplet with a fake private service and worker."""
    created: list[_FakeService] = []

    def factory(**_kwargs) -> _FakeService:
        service = _FakeService()
        created.append(service)
        return service

    monkeypatch.setattr(
        _native_tests_platform_factories.TestAppletBackendFactories,
        "tray_service",
        staticmethod(factory),
    )
    monkeypatch.setattr(
        applet_mod,
        "BackgroundWorker",
        lambda **_kwargs: worker or _ImmediateWorker(),
    )
    instance = SystemTrayApplet(
        48, config=Config(), platform_services=make_applet_platform(tray=service)
    )
    return SimpleNamespace(applet=instance, created=created)


@pytest.fixture
def tray(monkeypatch):
    return _make_tray(monkeypatch)


class TestSystemTrayAppletService:
    def test_construction_borrows_the_shared_service_without_starting_it(self, tray):
        assert len(tray.created) == 1
        assert tray.applet._service is tray.created[0]
        assert tray.created[0].refreshes == 0
        assert tray.created[0].started == 0

    def test_late_services_cannot_replace_the_construction_dependency(self, tray):
        original = tray.applet._service
        other = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=other))
        assert tray.applet._service is original
        assert not other.listeners
        assert original.stopped == 0

    def test_shared_state_reaches_the_applet(self, tray):
        tray.applet.start(notify=lambda: None)
        shared = tray.applet._service
        shared.publish(_tray_state("Example"))
        assert tray.applet._state == shared.state
        assert tray.applet.item.name == tooltip_text(shared.state)

    def test_stop_releases_the_subscription_and_preserves_shared_service(self, tray):
        shared = tray.applet._service
        tray.applet.start(notify=lambda: None)
        tray.applet.stop()
        tray.applet.stop()
        assert shared.stopped == 0
        assert shared.listeners == []

    def test_start_does_not_start_a_shared_service(self, tray):
        tray.applet.start(notify=lambda: None)
        assert tray.created[0].started == 0
        assert tray.created[0].listeners == [tray.applet._on_state_result]

    def test_refresh_now_delegates_without_a_blocking_read(self, tray):
        tray.applet._refresh_now()
        assert tray.created[0].refreshes == 1

    def test_activate_runs_on_the_worker_and_repolls(self, tray):
        tray.applet.start(notify=lambda: None)
        tray.applet._on_activate(":1.42/Tray")
        assert tray.created[0].activated == [":1.42/Tray"]
        assert tray.created[0].refreshes == 1

    def test_cached_state_is_used_from_construction(self, monkeypatch):
        shared = _FakeService()
        shared.state = _tray_state("Example")
        tray = _make_tray(monkeypatch, service=shared)
        assert tray.applet._state == shared.state
        assert tray.applet.item.name == tooltip_text(shared.state)
        assert not tray.created
        tray.applet.stop()
        assert shared.stopped == 0

    def test_restart_resubscribes_to_the_same_shared_service(self, tray):
        shared = tray.applet._service
        tray.applet.start(notify=lambda: None)
        tray.applet.stop()
        assert not shared.listeners
        shared.publish(_tray_state("Cached"))
        tray.applet.start(notify=lambda: None)
        assert tray.applet._service is shared
        assert shared.listeners == [tray.applet._on_state_result]
        assert tray.applet._state == shared.state
        assert shared.started == shared.stopped == 0

    def test_late_menu_result_is_dropped_after_stop(self, monkeypatch):
        worker = _DeferredWorker()
        shared = _FakeService()
        shared.menu_client_result = _FakeMenuClient(layout=_menu_layout())
        applet = _make_tray(monkeypatch, worker=worker, service=shared).applet
        applet.start(notify=lambda: None)
        build_menu = MagicMock()
        monkeypatch.setattr(applet, "_gtk_menu_from_dbus_menu", build_menu)

        applet._show_item_menu(":1.42/Tray")
        assert worker.tasks, "the layout fetch should still be in flight"
        applet.stop()
        worker.deliver()

        build_menu.assert_not_called()

    def test_stop_destroys_an_open_item_menu(self, tray):
        applet = tray.applet
        menu = Gtk.Menu()
        applet._item_menu = menu

        applet.stop()

        assert applet._item_menu is None
        assert menu.destroyed is True
