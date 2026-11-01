"""Tests for the System Tray applet helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, GLib, Gtk

import docking.applets.systemtray.applet as applet_mod
from docking.applets.services import AppletServices
from docking.applets.systemtray.applet import SystemTrayApplet
from docking.applets.systemtray.render import create_status_tray_icon
from docking.applets.systemtray.state import tooltip_text
from docking.core.config import Config
from docking.platform.backends.base import Rect
from docking.platform.tray.implementations.unix.dbusmenu import (
    DBusMenuClient,
    DBusMenuItem,
    DBusMenuLayout,
    parse_menu_node,
)
from docking.platform.tray.implementations.unix.status_notifier import (
    DEFAULT_ITEM_PATH,
    RegisteredItemAddress,
    parse_registered_item,
    tray_item_from_properties,
)
from docking.platform.tray.state import (
    TrayFocusRequest,
    TrayItem,
    unavailable_state,
)
from docking.platform.tray.state import (
    TrayState as StatusTrayState,
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
        text = tooltip_text(StatusTrayState(available=True, mode="host", items=()))

        assert text == "System Tray: waiting for tray apps"

    def test_legacy_tray_owner_text(self):
        text = tooltip_text(
            StatusTrayState(
                available=True,
                mode="watcher",
                items=(),
                legacy_owner="notification-area-applet",
            )
        )

        assert text == "System Tray: legacy tray owned by notification-area-applet"

    def test_lists_item_titles(self):
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service="org.example.App", path="/Item"),
            properties={"Title": "Example", "Status": "Active"},
        )

        assert "- Example" in tooltip_text(
            StatusTrayState(available=True, mode="watcher", items=(item,))
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
    item = TrayItem(identifier=":1.42/Tray", title=title, item_id="example")
    return StatusTrayState(available=True, mode="watcher", items=(item,))


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
        self.focus_listeners: list = []
        self.pointers: list = []
        self.action_arguments: list = []
        self.generation = 0

    def add_focus_listener(self, listener) -> None:
        if listener not in self.focus_listeners:
            self.focus_listeners.append(listener)

    def remove_focus_listener(self, listener) -> None:
        if listener in self.focus_listeners:
            self.focus_listeners.remove(listener)

    def pointer(self, identifier, *, point=None, anchor=None, generation=None) -> bool:
        if generation is not None and generation != self.generation:
            return False
        self.pointers.append((identifier, point, anchor))
        return True

    def add_listener(self, listener) -> None:
        if listener not in self.listeners:
            self.listeners.append(listener)
        listener(self.state)

    def remove_listener(self, listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    def start(self) -> None:
        self.started += 1
        self.generation += 1

    def stop(self) -> None:
        self.stopped += 1
        self.generation += 1

    def refresh(self) -> None:
        self.refreshes += 1

    def set_geometry(self, bounds) -> None:
        self.geometry = dict(bounds)

    def activate(
        self, identifier: str, *, anchor=None, keyboard=False, generation=None
    ) -> bool:
        if generation is not None and generation != self.generation:
            return False
        self.activated.append(identifier)
        self.action_arguments.append((identifier, anchor, keyboard))
        return True

    def context_menu(
        self, identifier: str, *, anchor=None, keyboard=False, generation=None
    ) -> bool:
        if generation is not None and generation != self.generation:
            return False
        self.context_menus.append(identifier)
        self.action_arguments.append((identifier, anchor, keyboard))
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
    def stop(self) -> None:
        pass

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

    def stop(self) -> None:
        pass

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

    borrowed = service if service is not None else factory()
    monkeypatch.setattr(
        applet_mod,
        "SerialWorker",
        lambda **_kwargs: worker or _ImmediateWorker(),
    )
    instance = SystemTrayApplet(
        48, config=Config(), platform_services=make_applet_platform(tray=borrowed)
    )
    return SimpleNamespace(applet=instance, created=created)


@pytest.fixture
def tray(monkeypatch):
    return _make_tray(monkeypatch)


@pytest.mark.parametrize("managed", [False, True])
def test_tray_uses_its_surface_policy_for_keyboard_focus(tray, monkeypatch, managed):
    from docking.platform.backends.reduced.services import ReducedSurfaceService
    from docking.platform.backends.windows.surface import WindowsSurfaceService

    applet = tray.applet
    applet._surface = WindowsSurfaceService() if managed else ReducedSurfaceService()
    window = MagicMock()
    create = MagicMock(return_value=window)
    monkeypatch.setattr(applet_mod, "create_popup_window", create)
    monkeypatch.setattr(applet, "_build_popup_content", lambda: object())
    monkeypatch.setattr(applet_mod, "show_wrapped_popup", lambda **_kwargs: None)
    try:
        applet._show_popup()
        create.assert_called_once_with(managed=managed)
        assert applet._popup is window
    finally:
        applet.stop()


def test_geometry_stays_with_the_borrowed_service_and_is_cleared_on_stop(tray):
    shared = tray.applet._service
    shared.set_geometry({"current": "bounds"})
    other = _FakeService()
    tray.applet.set_services(AppletServices(status_notifier=other))
    assert shared.geometry == {"current": "bounds"}
    assert tray.applet._service is shared
    tray.applet.start(lambda: None)
    tray.applet.stop()
    assert shared.geometry == {} and shared.stopped == 0
    assert other.stopped == 0


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

    def test_state_from_the_shared_service_reaches_the_applet(self, tray):
        shared = tray.applet._service
        tray.applet.start(notify=lambda: None)
        item = tray_item_from_properties(
            address=RegisteredItemAddress(service=":1.42", path="/Tray"),
            properties={"Id": "x", "Title": "Example", "Status": "Active"},
        )
        state = StatusTrayState(available=True, mode="watcher", items=(item,))

        shared.publish(state)

        assert tray.applet._state == state
        assert tray.applet.item.name == tooltip_text(state)

    def test_stop_leaves_a_shared_service_running(self, tray):
        shared = _FakeService()
        tray.applet.set_services(AppletServices(status_notifier=shared))

        tray.applet.stop()

        assert shared.stopped == 0
        assert shared.listeners == []

    def test_stop_without_start_leaves_the_borrowed_service_running(self, tray):
        tray.applet.stop()

        assert tray.created[0].stopped == 0

    def test_start_subscribes_to_the_borrowed_service(self, tray):
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
        monkeypatch.setattr(applet, "_gtk_menu_from_tray_menu", build_menu)

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


def test_old_menu_cannot_open_after_applet_restart(monkeypatch):
    worker = _DeferredWorker()
    applet = _make_tray(monkeypatch, worker=worker).applet
    service = _FakeService()
    service.menu_client_result = _FakeMenuClient(layout=_menu_layout())
    applet.set_services(AppletServices(status_notifier=service))
    applet.start(notify=lambda: None)
    build_menu = MagicMock()
    monkeypatch.setattr(applet, "_gtk_menu_from_tray_menu", build_menu)
    applet._show_item_menu(":1.42/Tray")
    applet.stop()
    applet.start(notify=lambda: None)
    worker.deliver()
    build_menu.assert_not_called()


def test_queued_action_keeps_the_construction_service(monkeypatch):
    worker = _DeferredWorker()
    first, second = _FakeService(), _FakeService()
    applet = _make_tray(monkeypatch, worker=worker, service=first).applet
    applet.start(notify=lambda: None)
    applet._on_activate(":1.42/Tray")
    applet.set_services(AppletServices(status_notifier=second))
    while worker.tasks:
        worker.deliver()
    assert first.activated == [":1.42/Tray"]
    assert not second.activated


def _active_tray(monkeypatch, *, worker=None):
    service = _FakeService()
    service.state = _tray_state("Example")
    applet = _make_tray(monkeypatch, worker=worker, service=service).applet
    applet.start(notify=lambda: None)
    if worker is not None:
        while worker.tasks:
            worker.deliver()
    return applet, service


def test_hover_motion_coalesces_and_leave_precedes_explicit_action(monkeypatch):
    worker = _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=worker)
    order = []
    monkeypatch.setattr(
        service, "pointer", lambda *args, **kw: order.append((args, kw))
    )
    monkeypatch.setattr(
        service, "activate", lambda *args, **kw: order.append((args, kw))
    )
    anchor = Rect(-20, 90, 24, 24)
    applet._queue_pointer(":1.42/Tray", point=(-15, 96), anchor=anchor)
    applet._queue_pointer(":1.42/Tray", point=(-12, 98), anchor=anchor)
    assert len(worker.tasks) == 1
    worker.deliver()
    assert order == [
        ((":1.42/Tray",), {"point": (-12, 98), "anchor": anchor, "generation": 0})
    ]

    applet._popup = SimpleNamespace(hide=applet._on_popup_hidden)
    monkeypatch.setattr(applet, "_publish_geometry", lambda: None)
    monkeypatch.setattr(applet, "_action_rectangle", lambda _id: anchor)
    applet._on_activate(":1.42/Tray", keyboard=True)
    while worker.tasks:
        worker.deliver()
    assert order[1:] == [
        ((None,), {"generation": 0}),
        ((":1.42/Tray",), {"anchor": anchor, "keyboard": True, "generation": 0}),
    ]


def test_old_hover_completion_cannot_consume_new_generation_motion(monkeypatch):
    worker = _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=worker)
    applet._queue_pointer(":1.42/Tray", point=(1, 2))
    old_fn, old_result, _ = worker.tasks.pop(0)
    applet.stop()
    applet.start(notify=lambda: None)
    applet._queue_pointer("second", point=(3, 4))
    old_fn()
    old_result(None)
    while worker.tasks:
        worker.deliver()
    assert service.pointers == [(None, None, None), ("second", (3, 4), None)]


def test_old_worker_closes_hover_after_applet_restart_without_new_pointer(monkeypatch):
    worker = _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=worker)
    applet.stop()
    applet.start(notify=lambda: None)
    while worker.tasks:
        worker.deliver()
    assert service.pointers == [(None, None, None)]
    applet._queue_pointer(":1.42/Tray", point=(3, 4))
    worker.deliver()
    assert service.pointers == [(None, None, None), (":1.42/Tray", (3, 4), None)]


def test_hover_leave_is_a_barrier_before_new_motion(monkeypatch):
    worker = _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=worker)
    applet._queue_pointer("old-row", point=(1, 2))
    applet._clear_pointer()
    applet._queue_pointer("new-row", point=(3, 4))
    while worker.tasks:
        worker.deliver()
    assert service.pointers == [(None, None, None), ("new-row", (3, 4), None)]


def test_old_worker_leave_cannot_close_new_hover_delivered_after_restart(monkeypatch):
    old_worker, new_worker = _DeferredWorker(), _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=old_worker)
    applet.stop()
    monkeypatch.setattr(applet_mod, "SerialWorker", lambda **_kw: new_worker)
    applet.start(notify=lambda: None)
    applet._queue_pointer(":1.42/Tray", point=(3, 4))
    new_worker.deliver()
    while old_worker.tasks:
        old_worker.deliver()
    assert service.pointers == [(":1.42/Tray", (3, 4), None)]


def test_service_restart_rejects_old_work_and_does_not_strand_new_hover(monkeypatch):
    worker = _DeferredWorker()
    applet, service = _active_tray(monkeypatch, worker=worker)
    applet._on_activate(":1.42/Tray")
    applet._queue_pointer("old-row", point=(1, 2))
    applet._clear_pointer()
    service.stop()
    service.start()
    applet._queue_pointer("new-row", point=(3, 4))
    while worker.tasks:
        worker.deliver()
    assert not service.activated
    assert service.pointers == [("new-row", (3, 4), None)]


def test_hiding_popup_cannot_retarget_an_action_to_a_restarted_service(monkeypatch):
    applet, service = _active_tray(monkeypatch)

    def hide():
        service.stop()
        service.start()

    applet._popup = SimpleNamespace(hide=hide)
    monkeypatch.setattr(applet, "_action_rectangle", lambda _identifier: None)
    applet._on_activate(":1.42/Tray")
    assert not service.activated


@pytest.mark.parametrize(
    ("keyval", "state", "context", "menu"),
    [
        (Gdk.KEY_Return, 0, False, False),
        (Gdk.KEY_KP_Enter, 0, False, False),
        (Gdk.KEY_space, 0, True, True),
        (Gdk.KEY_Menu, 0, False, True),
        (Gdk.KEY_F10, Gdk.ModifierType.SHIFT_MASK, False, True),
    ],
)
def test_native_control_keyboard_actions_keep_origin_and_anchor(
    monkeypatch, keyval, state, context, menu
):
    applet, service = _active_tray(monkeypatch)
    anchor = Rect(-20, 90, 24, 24)
    monkeypatch.setattr(applet, "_action_rectangle", lambda _id: anchor)
    assert applet._on_action_key(
        None,
        SimpleNamespace(keyval=keyval, state=state),
        ":1.42/Tray",
        context_menu=context,
    )
    assert service.action_arguments == [(":1.42/Tray", anchor, True)]
    assert bool(service.context_menus) is menu


@pytest.mark.parametrize("accepted", [False, True])
def test_focus_return_never_shows_popup_before_native_claim(monkeypatch, accepted):
    applet, _service = _active_tray(monkeypatch)
    order = []
    popup = object()
    applet._popup = popup
    applet._focus_target = (":1.42/Tray", applet._request())

    def restore(window, claim, *, validate):
        assert window is popup
        assert validate()
        order.append("surface")
        result = claim()
        assert validate()  # Claim consumption must not retire the UI intent yet.
        return result

    applet._surface = SimpleNamespace(restore_popup_focus=restore)
    target = SimpleNamespace(grab_focus=lambda: order.append("row-focus"))

    def show():
        order.append("show")
        applet._row_targets[":1.42/Tray"] = target

    monkeypatch.setattr(applet, "_show_popup", show)

    def native_claim():
        order.append("native-claim")
        return accepted

    applet._on_focus_return(TrayFocusRequest(":1.42/Tray", native_claim, lambda: True))
    assert order == ["surface", "native-claim"] + (
        ["show", "row-focus"] if accepted else []
    )


@pytest.mark.parametrize("invalidate", ["dismiss", "remove", "restart", "stop"])
def test_focus_return_claim_is_rechecked_after_ui_changes(monkeypatch, invalidate):
    applet, service = _active_tray(monkeypatch)
    popup = SimpleNamespace(
        hide=lambda: None, destroy=lambda: None, get_visible=lambda: False
    )
    applet._popup = popup
    applet._focus_target = (":1.42/Tray", applet._request())
    claim = MagicMock(return_value=True)

    def restore(_window, guarded_claim, *, validate):
        if invalidate == "dismiss":
            applet._on_popup_key(None, SimpleNamespace(keyval=Gdk.KEY_Escape))
        elif invalidate == "remove":
            service.publish(StatusTrayState(True, "watcher", ()))
        elif invalidate == "restart":
            service.stop()
            service.start()
        else:
            applet.stop()
        assert not validate()
        return guarded_claim()

    applet._surface = SimpleNamespace(restore_popup_focus=restore)
    monkeypatch.setattr(applet, "_show_popup", MagicMock())
    applet._on_focus_return(TrayFocusRequest(":1.42/Tray", claim, lambda: True))
    claim.assert_not_called()
    applet._show_popup.assert_not_called()


@pytest.mark.parametrize("invalidate", ["dismiss", "remove", "restart", "new-action"])
def test_focus_return_revalidates_after_claim_during_mapping(monkeypatch, invalidate):
    applet, service = _active_tray(monkeypatch)
    popup = SimpleNamespace(hide=lambda: None, get_visible=lambda: False)
    applet._popup = popup
    applet._focus_target = (":1.42/Tray", applet._request())
    native_claim = MagicMock(return_value=True)
    native_focus = MagicMock()

    def restore(_window, claim, *, validate):
        assert validate() and claim()
        # A real Gtk.show() can synchronously dispatch these GTK callbacks.
        if invalidate == "dismiss":
            applet._on_popup_key(None, SimpleNamespace(keyval=Gdk.KEY_Escape))
        elif invalidate == "remove":
            service.publish(StatusTrayState(True, "watcher", ()))
        elif invalidate == "restart":
            service.stop()
            service.start()
        elif invalidate == "new-action":
            applet._on_activate(":1.42/Tray")
        else:
            service.stop()
            service.start()
        if not validate():
            return False
        native_focus()
        return True

    applet._surface = SimpleNamespace(restore_popup_focus=restore)
    monkeypatch.setattr(applet, "_show_popup", MagicMock())
    applet._on_focus_return(TrayFocusRequest(":1.42/Tray", native_claim, lambda: True))
    native_claim.assert_called_once_with()
    native_focus.assert_not_called()
    applet._show_popup.assert_not_called()
    assert (applet._focus_target is not None) is (invalidate == "new-action")


@pytest.mark.parametrize("show_tooltip", [False, True])
@pytest.mark.parametrize("can_double_click", [False, True])
def test_native_tooltip_policy_keeps_label_and_hover_protocol(
    monkeypatch, show_tooltip, can_double_click
):
    applet, _service = _active_tray(monkeypatch)
    for name in ("Box", "Image", "Label", "Button"):
        monkeypatch.setattr(applet_mod.Gtk, name, MagicMock())
    target = MagicMock()
    monkeypatch.setattr(applet_mod.Gtk, "EventBox", lambda: target)
    monkeypatch.setattr(applet_mod, "_load_item_icon", lambda **_kwargs: None)
    item = TrayItem(
        "icon",
        title="Application",
        tooltip_title="Tooltip",
        tooltip_text="Detail",
        show_tooltip=show_tooltip,
        can_double_click=can_double_click,
    )
    applet._build_item_row(item)
    expected = "Tooltip\nDetail" if show_tooltip else None
    applet._row_icons["icon"].set_tooltip_text.assert_called_once_with(expected)
    signals = {call.args[0] for call in target.connect.call_args_list}
    if can_double_click:
        target.set_tooltip_text.assert_called_once_with(expected)
        assert {
            "enter-notify-event",
            "motion-notify-event",
            "leave-notify-event",
        } <= signals
        assert applet._row_targets["icon"] is target
    else:
        assert not signals
    assert [call.kwargs["label"] for call in Gtk.Label.call_args_list] == [
        "Application",
        "Detail",
    ]
