"""Notification wire decoding, explicit callback dispatch and stale-owner safety."""

import ctypes
import os
import struct
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from docking.platform import environment
from docking.platform.backends.base import Rect
from docking.platform.composition.windows import WindowsPlatformProvider
from docking.platform.tray.implementations.windows.backend import WindowsTrayBackend
from docking.platform.tray.implementations.windows.host import WindowsTrayHost
from docking.platform.tray.implementations.windows.icons import composite_pixels
from docking.platform.tray.implementations.windows.messages import (
    callback_parameters,
    decode_icon_query,
    decode_message,
    icon_query_reply,
)
from docking.platform.tray.state import TrayIconPixmap

GUID = "1dc183b6-df18-45ea-89d5-1049f9b2df91"


def wire(
    *,
    operation=0,
    flags=7,
    window=0x1234,
    uid=37,
    callback=0x8001,
    icon=9,
    tip="Original",
    state=0,
    mask=0,
    version=0,
    guid="",
    size=956,
):
    data = bytearray(8 + size + 520)
    struct.pack_into(
        "<8I",
        data,
        0,
        0x34753423,
        operation,
        size,
        window,
        uid,
        flags | (0x20 if guid else 0),
        callback,
        icon,
    )
    text = tip.encode("utf-16-le")
    data[32 : 32 + len(text)] = text
    if size >= 936:
        struct.pack_into("<II", data, 8 + 280, state, mask)
        struct.pack_into("<I", data, 8 + 800, version)
    if guid:
        data[8 + 936 : 8 + 952] = uuid.UUID(guid).bytes_le
    return bytes(data)


class API:
    def __init__(self):
        self.owners = {0x1234: 42}
        self.callback = None
        self.closed = 0
        self.posts = []
        self.authorized = []
        self.images = []
        self.fail = False
        self.foreground_identity = (0x1234, 42)
        self.timers = []
        self.post_success = True
        self.now = 0.0
        self.cursor = (-200, 420)

    def start(self, callback, icon_bounds=None, *, shell_changed=None):
        if self.fail:
            raise OSError("Host failed")
        self.callback = callback
        self.icon_bounds = icon_bounds
        self.shell_changed = shell_changed

    def owner(self, window):
        return self.owners.get(window, 0)

    def pixels(self, icon):
        self.images.append(icon)
        return TrayIconPixmap(1, 1, bytes((icon, 2, 3, 255)))

    def point(self):
        return self.cursor

    def post(self, *arguments):
        self.posts.append(arguments)
        return self.post_success

    def authorize(self, pid):
        self.authorized.append(pid)

    def foreground(self):
        return self.foreground_identity

    def hover_settings(self):
        return 400, 4, 4

    def timer(self, interval, callback):
        timer = Timer(interval, callback)
        self.timers.append(timer)
        return timer

    def close(self):
        self.closed += 1

    def emit(self, **fields):
        message = decode_message(wire(**fields))
        assert message is not None and self.callback is not None
        return self.callback(message)


class Timer:
    def __init__(self, interval, callback):
        self.interval, self.callback = interval, callback
        self.daemon = False
        self.started = self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # An already queued timer can run after cancel; backend generations
        # must reject it instead of relying solely on Timer.cancel().
        self.callback()


@pytest.fixture
def tray(monkeypatch):
    api = API()
    backend = WindowsTrayBackend(
        api=api, timer_factory=api.timer, clock=lambda: api.now
    )
    monkeypatch.setattr(
        backend._processes, "executable", lambda pid: Path("Recorder.exe")
    )
    assert backend.get_state().available
    return backend, api


def geometry_query(*, operation=1, guid="", window=0x1234, uid=37):
    return decode_icon_query(
        struct.pack(
            "<6I16s",
            0x34753423,
            operation,
            32,
            0,
            window,
            uid,
            uuid.UUID(guid or "00000000-0000-0000-0000-000000000000").bytes_le,
        )
    )


def test_native_geometry_wire_uses_guid_or_window_uid_and_dimensions():
    origin = geometry_query()
    size = geometry_query(operation=2, guid=GUID, window=99, uid=100)
    assert origin.identifier == "window:00001234:37"
    assert size.identifier == "guid:" + GUID
    rect = Rect(-320, 250, 24, 48)
    assert icon_query_reply(origin, rect) == ((-320 & 0xFFFF) | (250 << 16))
    assert icon_query_reply(size, rect) == (24 | (48 << 16))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\0" * 40,
        b"\0" * 39,
        b"\0" * 41,
        struct.pack("<10I", 0x34753423, 3, 32, 0, 0, 0, 0, 0, 0, 0),
    ],
)
def test_unknown_icon_query_is_left_to_explorer(data):
    assert decode_icon_query(data) is None


@pytest.mark.parametrize(
    "rect", [Rect(0, 0, 0, 24), Rect(0, 0, 24, -1), Rect(40000, 0, 24, 24)]
)
def test_invalid_or_unrepresentable_icon_origin_has_no_reply(rect):
    assert icon_query_reply(geometry_query(), rect) is None


def test_geometry_is_published_and_survives_duplicate_registration(tray):
    backend, api = tray
    api.emit()
    key = geometry_query().identifier
    rect = Rect(-300, 410, 24, 24)
    backend.set_geometry({key: rect, "unknown": rect})
    assert api.icon_bounds(geometry_query()) == rect
    api.emit()  # TaskbarCreated re-adds the same client while the view stays open.
    assert api.icon_bounds(geometry_query()) == rect
    api.emit(flags=8, state=1, mask=1, operation=1)
    assert api.icon_bounds(geometry_query()) is None
    api.emit(flags=8, state=0, mask=1, operation=1)
    assert api.icon_bounds(geometry_query()) == rect
    backend.set_geometry({})
    assert api.icon_bounds(geometry_query()) is None


def test_context_anchor_survives_popup_clear_then_invalidates_on_owner_exit(tray):
    backend, api = tray
    api.emit(guid=GUID)
    api.emit(guid=GUID, operation=4, version=4)
    query = geometry_query(guid=GUID)
    rect = Rect(350, 420, 24, 24)
    backend.set_geometry({})
    assert backend.context_menu(query.identifier, anchor=rect)
    assert api.icon_bounds(query) == rect
    assert api.posts[-1][2:] == callback_parameters(
        version=4, uid=37, event=0x7B, x=362, y=444
    )
    api.owners[0x1234] = 77
    assert api.icon_bounds(query) is None
    assert not backend.context_menu(query.identifier, anchor=rect)


def test_geometry_cannot_outlive_removal_or_closed_backend(tray):
    backend, api = tray
    api.emit()
    query = geometry_query()
    rect = Rect(20, 30, 24, 24)
    backend.set_geometry({query.identifier: rect})
    api.emit(operation=2)
    assert api.icon_bounds(query) is None
    api.emit()
    backend.set_geometry({query.identifier: rect})
    backend.close()
    backend.set_geometry({query.identifier: rect})
    assert api.icon_bounds(query) is None


def test_guid_only_modify_keeps_callback_window_uid_version_and_anchor(tray):
    backend, api = tray
    api.emit(guid=GUID, tip="Before")
    api.emit(guid=GUID, operation=4, version=4)
    query = geometry_query(guid=GUID)
    anchor = Rect(350, 420, 24, 24)
    backend.set_geometry({query.identifier: anchor})
    assert api.emit(
        guid=GUID, operation=1, window=0, uid=0, flags=6, tip="After", icon=10
    )
    item = backend.get_state().items[0]
    assert item.title == "After" and item.icon_pixmap.rgba[0] == 10
    assert api.icon_bounds(query) == anchor
    assert backend.double_click(query.identifier)
    assert api.posts[-1][0] == 0x1234
    assert api.posts[-1][3] >> 16 == 37
    assert api.emit(guid=GUID, operation=1, window=0, uid=0, flags=8, state=1, mask=1)
    assert not backend.get_state().items
    assert api.emit(guid=GUID, operation=1, window=0, uid=0, flags=8, state=0, mask=1)
    assert backend.get_state().items[0].title == "After"


def test_guid_only_update_requires_a_live_existing_registration(tray):
    backend, api = tray
    assert not api.emit(guid=GUID, operation=1, window=0, uid=0, flags=4, tip="Unknown")
    api.emit(guid=GUID)
    assert not api.emit(guid=GUID, operation=0, window=0, uid=0)
    api.owners[0x1234] = 77
    assert not api.emit(
        guid=GUID, operation=1, window=0, uid=0, flags=4, tip="Reused owner"
    )
    assert not backend.get_state().items


def test_replacement_explorer_is_reseeded_once_for_each_window_and_owner(monkeypatch):
    host = WindowsTrayHost()
    host._explorer = 101
    owner = Mock(return_value=202)
    monkeypatch.setattr(host, "owner", owner)
    monkeypatch.setattr(host, "_integrity", lambda pid: 0x2000)
    announce = Mock()
    monkeypatch.setattr(host, "_announce", announce)
    host._seed_changed_explorer()
    host._seed_changed_explorer()
    assert announce.call_count == 1
    host._explorer = 303
    host._seed_changed_explorer()
    owner.return_value = 404
    host._seed_changed_explorer()
    assert announce.call_count == 3
    host._explorer = 0
    host._seed_changed_explorer()
    assert announce.call_count == 3


def test_factory_and_close_do_no_native_or_unix_work(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setitem(
        sys.modules, "docking.platform.tray.implementations.unix.backend", None
    )
    monkeypatch.setitem(
        sys.modules, "docking.applets.systemtray.implementations.unix.xembed", None
    )

    def fail(*args, **kwargs):
        raise AssertionError("Constructor must stay lazy")

    monkeypatch.setattr(ctypes, "WinDLL", fail, raising=False)
    backend = WindowsPlatformProvider().tray_backend()
    assert isinstance(backend, WindowsTrayBackend)
    backend.close()


def test_wire_format_is_fixed_width_and_preserves_unicode_and_guid():
    message = decode_message(wire(guid=GUID, tip="Caméra 🎥"))
    assert message.window == 0x1234 and message.uid == 37 and message.callback == 0x8001
    assert message.tip == "Caméra 🎥" and message.identifier == "guid:" + GUID
    assert decode_message(wire(size=152, tip="Legacy")).tip == "Legacy"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        bytes(159),
        bytes(9000),
        wire()[:400],
        b"xxxx" + wire()[4:],
        wire(operation=99),
    ],
    ids=["empty", "short", "oversize", "truncated", "signature", "operation"],
)
def test_malformed_or_unsupported_wire_data_is_rejected(payload):
    assert decode_message(payload) is None


def test_modify_updates_only_explicitly_flagged_fields_and_copies_borrowed_icon(tray):
    backend, api = tray
    assert api.emit(guid=GUID)
    original = backend.get_state().items[0]
    assert api.images == [9] and original.icon_pixmap.rgba == bytes((9, 2, 3, 255))
    assert api.emit(
        operation=1,
        guid=GUID,
        flags=4,
        tip="Updated",
        callback=16,
        icon=10,
        state=1,
        mask=1,
    )
    changed = backend.get_state().items[0]
    assert changed.title == "Updated" and changed.icon_pixmap == original.icon_pixmap
    assert changed.can_activate and api.images == [9]
    assert backend.activate(changed.identifier)
    assert [post[1] for post in api.posts] == [0x8001, 0x8001]
    assert api.emit(operation=2, guid=GUID)
    assert not backend.get_state().items and not backend.activate(changed.identifier)


@pytest.mark.parametrize("version", [0, 3, 4])
def test_callback_versions_use_correct_events_ids_and_signed_coordinates(tray, version):
    backend, api = tray
    assert api.emit()
    assert api.emit(operation=4, version=version)
    item = backend.get_state().items[0]
    assert backend.activate(item.identifier) and backend.context_menu(item.identifier)
    events = [0x201, 0x202, 0x204, 0x205] if version == 0 else [0x400, 0x7B]
    expected = [
        (
            0x1234,
            0x8001,
            *callback_parameters(version=version, uid=37, event=e, x=-200, y=420),
        )
        for e in events
    ]
    assert api.posts == expected and api.authorized == [42, 42]
    if version == 4:
        assert api.posts[0][2] == ((420 << 16) | (-200 & 0xFFFF))
        assert api.posts[0][3] == (37 << 16) | 0x400


def test_hidden_state_mask_controls_inventory_and_actions_without_losing_metadata(tray):
    backend, api = tray
    assert api.emit()
    item = backend.get_state().items[0]
    assert api.emit(operation=1, flags=8, state=1, mask=1)
    assert not backend.get_state().items and not backend.activate(item.identifier)
    assert api.emit(operation=1, flags=8, state=0, mask=1)
    assert backend.get_state().items == (item,)


def test_reused_or_destroyed_owner_cannot_receive_stale_icon_actions(tray):
    backend, api = tray
    assert api.emit()
    item = backend.get_state().items[0]
    api.owners[0x1234] = 43
    assert not backend.activate(item.identifier) and not backend.context_menu(
        item.identifier
    )
    assert not backend.get_state().items and not api.posts and not api.authorized


def test_window_management_messages_cannot_be_used_as_icon_callbacks(tray):
    backend, api = tray
    assert api.emit(callback=16)
    item = backend.get_state().items[0]
    assert not item.can_activate and not item.can_context_menu
    assert not backend.activate(item.identifier) and not api.posts


def test_stop_rejects_late_registrations_and_repeated_close_is_harmless(tray):
    backend, api = tray
    assert api.emit()
    backend.close()
    backend.close()
    assert api.closed == 1 and not api.emit(tip="Late")
    assert not backend.get_state().available


def test_host_failure_is_not_an_empty_healthy_tray(tray):
    backend, api = tray
    api.fail = True
    state = backend.get_state()
    assert not state.available and state.error


def test_multiline_windows_tooltip_keeps_title_and_status_distinct(tray):
    backend, api = tray
    assert api.emit(tip="OneDrive - Personal\nNot signed in")
    item = backend.get_state().items[0]
    assert item.title == item.tooltip_title == "OneDrive - Personal"
    assert item.tooltip_text == "Not signed in"


@pytest.mark.parametrize("version", [0, 3, 4])
def test_double_click_is_available_without_assuming_primary_click_behavior(
    tray, version
):
    backend, api = tray
    assert api.emit() and api.emit(operation=4, version=version)
    item = backend.get_state().items[0]
    assert item.can_double_click and backend.double_click(item.identifier)
    assert api.posts == [
        (
            0x1234,
            0x8001,
            *callback_parameters(version=version, uid=37, event=0x203, x=-200, y=420),
        )
    ]


def test_composite_alpha_handles_modern_and_classic_masked_icons():
    black = bytes((0, 0, 128, 0, 10, 20, 30, 0, 0, 0, 0, 0))
    white = bytes((127, 127, 255, 0, 10, 20, 30, 0, 255, 255, 255, 0))
    assert composite_pixels(black, white) == bytes(
        (255, 0, 0, 128, 30, 20, 10, 255, 0, 0, 0, 0)
    )
    with pytest.raises(ValueError):
        composite_pixels(b"bad", b"bad")


def test_live_guid_collision_cannot_steal_owner_but_guid_only_modify_still_works(tray):
    backend, api = tray
    assert api.emit(guid=GUID)
    assert api.emit(guid=GUID, operation=4, version=4)
    key = "guid:" + GUID
    anchor = Rect(-300, 400, 24, 24)
    backend.set_geometry({key: anchor})
    api.owners[0x5678] = 99
    assert not api.emit(guid=GUID, window=0x5678, uid=99, callback=0x8002, icon=10)
    assert not api.emit(guid=GUID, window=0x5678, operation=4, version=3)
    assert api.images == [9]
    assert api.emit(guid=GUID, operation=1, window=0, uid=0, flags=4, tip="Updated")
    assert backend.activate(key)
    assert api.posts[-1][:2] == (0x1234, 0x8001)
    assert api.posts[-1][3] == (37 << 16) | 0x400
    assert api.icon_bounds(geometry_query(guid=GUID)) == anchor


def test_same_owner_guid_reseed_resets_version_and_replaces_dead_registration(tray):
    backend, api = tray
    assert api.emit(guid=GUID) and api.emit(guid=GUID, operation=4, version=4)
    assert api.emit(guid=GUID, tip="Reseeded")
    assert backend.activate("guid:" + GUID)
    assert api.posts[-1][2:] == (37, 0x202)
    api.owners[0x1234] = 0
    api.owners[0x5678] = 99
    assert api.emit(guid=GUID, window=0x5678, uid=99)
    assert backend.activate("guid:" + GUID)
    assert api.posts[-1][0] == 0x5678


@pytest.mark.parametrize("version", [0, 3, 4])
def test_flag_only_showtip_policy_does_not_change_stored_text_or_pixels(tray, version):
    backend, api = tray
    assert api.emit(guid=GUID) and api.emit(guid=GUID, operation=4, version=version)
    item = backend.get_state().items[0]
    assert item.show_tooltip == (version < 4)
    assert api.emit(guid=GUID, operation=1, window=0, uid=0, flags=0x80, tip="Ignored")
    shown = backend.get_state().items[0]
    assert shown.show_tooltip and shown.tooltip_title == "Original"
    assert shown.icon_pixmap == item.icon_pixmap
    assert api.emit(guid=GUID, operation=1, window=0, uid=0, flags=0, tip="Ignored")
    hidden = backend.get_state().items[0]
    assert hidden.show_tooltip == (version < 4)
    assert hidden.tooltip_title == "Original" and api.images == [9]


def test_empty_native_tooltip_does_not_reuse_executable_fallback(tray):
    backend, api = tray
    api.emit(tip="")
    item = backend.get_state().items[0]
    assert item.title == "Recorder" and not item.tooltip_title and not item.tooltip_text
    assert not item.show_tooltip


@pytest.mark.parametrize("version", [0, 3, 4])
@pytest.mark.parametrize("context", [False, True])
def test_keyboard_invocation_uses_versioned_events_and_signed_icon_origin(
    tray, version, context
):
    backend, api = tray
    assert api.emit() and api.emit(operation=4, version=version)
    key = backend.get_state().items[0].identifier
    action = backend.context_menu if context else backend.activate
    anchor = Rect(-500, -300, 24, 24)
    assert action(key, anchor=anchor, keyboard=True)
    events = (0x204, 0x205) if version == 0 else ((0x7B,) if context else (0x401,))
    assert api.posts == [
        (
            0x1234,
            0x8001,
            *callback_parameters(version=version, uid=37, event=event, x=-500, y=-300),
        )
        for event in events
    ]
    assert api.icon_bounds(geometry_query()) == anchor


@pytest.mark.parametrize("version,show_tip", [(0, False), (3, False), (4, True)])
def test_standard_hover_sends_mouse_motion_without_rich_popup_or_focus_grant(
    tray, version, show_tip
):
    backend, api = tray
    assert api.emit(flags=7 | (0x80 if show_tip else 0))
    assert api.emit(operation=4, version=version)
    key = backend.get_state().items[0].identifier
    assert backend.pointer(key, point=(-250, 430), anchor=Rect(-262, 418, 24, 24))
    assert backend.pointer(key, point=(-249, 431))
    assert backend.pointer(None)
    assert [post[2:] for post in api.posts] == [
        callback_parameters(version=version, uid=37, event=0x200, x=x, y=y)
        for x, y in ((-250, 430), (-249, 431))
    ]
    assert not api.timers and not api.authorized


def test_rich_hover_obeys_system_delay_and_movement_rectangle_then_pairs_close(tray):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    assert backend.pointer(key, point=(-250, 430))
    first = api.timers[-1]
    assert first.started and first.daemon and first.interval == 0.4
    assert backend.pointer(key, point=(-249, 431))
    assert len(api.timers) == 1
    assert backend.pointer(key, point=(-240, 440))
    second = api.timers[-1]
    assert first.cancelled and second is not first
    first.fire()
    assert [post[3] & 0xFFFF for post in api.posts] == [0x200] * 3
    second.fire()
    assert api.posts[-1][2:] == callback_parameters(
        version=4, uid=37, event=0x406, x=-240, y=440
    )
    assert backend.pointer(key, point=(-230, 445))
    assert len(api.timers) == 2
    assert backend.pointer(None) and backend.pointer(None)
    assert [post[3] & 0xFFFF for post in api.posts] == [
        0x200,
        0x200,
        0x200,
        0x406,
        0x200,
        0x407,
    ]
    assert not api.authorized


@pytest.mark.parametrize(
    "change",
    [
        "delete",
        "hide",
        "callback",
        "version",
        "showtip",
        "reseed",
        "geometry",
        "action",
        "close",
    ],
)
def test_hover_close_and_late_timer_cannot_cross_provider_or_presentation_lifecycle(
    tray, change
):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    backend.pointer(key, point=(-250, 430))
    timer = api.timers[-1]
    timer.fire()
    before = len(api.posts)
    if change == "delete":
        api.emit(operation=2)
    elif change == "hide":
        api.emit(operation=1, flags=8, state=1, mask=1)
    elif change == "callback":
        api.emit(operation=1, flags=1, callback=0x8002)
    elif change == "version":
        api.emit(operation=4, version=3)
    elif change == "showtip":
        api.emit(operation=1, flags=0x80)
    elif change == "reseed":
        api.emit()
    elif change == "geometry":
        backend.set_geometry({})
    elif change == "action":
        backend.context_menu(key)
    else:
        backend.close()
    assert api.posts[before][3] & 0xFFFF == 0x407
    after = list(api.posts)
    timer.fire()
    assert api.posts == after


def test_native_cursor_watch_closes_rich_popup_without_a_gtk_leave_event(tray):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    api.cursor = (-250, 430)
    backend.pointer(key, point=api.cursor, anchor=Rect(-262, 418, 24, 24))
    api.timers[-1].fire()
    watch = api.timers[-1]
    assert watch.interval == 0.1 and watch.started and watch.daemon
    assert api.posts[-1][3] & 0xFFFF == 0x406
    watch.fire()  # Staying over the icon is silent and rearms one watch.
    assert api.posts[-1][3] & 0xFFFF == 0x406
    successor = api.timers[-1]
    api.cursor = (10, 10)
    successor.fire()
    assert api.posts[-1][3] & 0xFFFF == 0x407
    count = len(api.posts)
    watch.fire()
    successor.fire()
    assert len(api.posts) == count and not api.authorized


@pytest.mark.parametrize("cursor", [(-263, 430), (-238, 430), (-250, 417), (-250, 442)])
def test_delayed_rich_open_rechecks_physical_cursor_at_each_native_edge(tray, cursor):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    backend.pointer(key, point=(-250, 430), anchor=Rect(-262, 418, 24, 24))
    api.cursor = cursor
    api.timers[-1].fire()
    assert [post[3] & 0xFFFF for post in api.posts] == [0x200]
    assert backend._hover is None and backend._hover_watch is None


@pytest.mark.parametrize(
    "change",
    [
        "delete",
        "hide",
        "callback",
        "version",
        "showtip",
        "reseed",
        "shell",
        "stop",
        "geometry",
        "action",
        "leave",
    ],
)
def test_native_hover_watch_is_canceled_and_cannot_cross_lifecycles(tray, change):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    api.cursor = (-250, 430)
    backend.pointer(key, point=api.cursor, anchor=Rect(-262, 418, 24, 24))
    api.timers[-1].fire()
    watch = api.timers[-1]
    if change == "delete":
        api.emit(operation=2)
    elif change == "hide":
        api.emit(operation=1, flags=8, state=1, mask=1)
    elif change == "reseed":
        api.emit()
    elif change == "callback":
        api.emit(operation=1, flags=1, callback=0x8002)
    elif change == "version":
        api.emit(operation=4, version=3)
    elif change == "showtip":
        api.emit(operation=1, flags=0x80)
    elif change == "shell":
        api.shell_changed()
    elif change == "geometry":
        backend.set_geometry({})
    elif change == "action":
        backend.context_menu(key)
    elif change == "leave":
        backend.pointer(None)
    else:
        backend.close()
    assert watch.cancelled
    before = list(api.posts)
    watch.fire()
    assert api.posts == before and backend._hover_watch is None


def test_canceled_native_watch_cannot_close_a_new_providers_popup(tray):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    first = backend.get_state().items[0].identifier
    api.cursor = (-250, 430)
    backend.pointer(first, point=api.cursor, anchor=Rect(-262, 418, 24, 24))
    api.timers[-1].fire()
    stale = api.timers[-1]
    api.owners[0x5678] = 99
    api.emit(window=0x5678, uid=38)
    api.emit(operation=4, window=0x5678, uid=38, version=4)
    second = "window:00005678:38"
    api.cursor = (110, 110)
    backend.pointer(second, point=api.cursor, anchor=Rect(100, 100, 24, 24))
    api.timers[-1].fire()
    assert backend._hover.identifier == second and backend._hover.opened
    before = list(api.posts)
    stale.fire()
    assert stale.cancelled and api.posts == before
    assert backend._hover.identifier == second and backend._hover.opened


@pytest.mark.parametrize("change", ["moved", "dead", "point-failed"])
def test_native_hover_watch_handles_moved_bounds_dead_owners_and_failed_reads(
    tray, monkeypatch, change
):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    api.cursor = (-250, 430)
    backend.pointer(key, point=api.cursor, anchor=Rect(-262, 418, 24, 24))
    api.timers[-1].fire()
    watch = api.timers[-1]
    if change == "moved":
        backend.set_geometry({key: Rect(300, 200, 24, 24)})
    elif change == "dead":
        api.owners.clear()
    else:
        monkeypatch.setattr(
            api, "point", Mock(side_effect=OSError("Owned pointer read failed"))
        )
    watch.fire()
    assert backend._hover is None and backend._hover_watch is None
    assert api.posts[-1][3] & 0xFFFF == (0x406 if change == "dead" else 0x407)
    assert not api.authorized


def test_dead_owner_and_failed_callback_never_receive_hover_or_focus_grants(tray):
    backend, api = tray
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    backend.pointer(key)
    timer = api.timers[-1]
    api.owners[0x1234] = 99
    timer.fire()
    assert not backend.pointer(key) and not api.authorized
    assert len(api.posts) == 1


def _focus_request(tray):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    assert api.emit()
    key = backend.get_state().items[0].identifier
    assert backend.context_menu(key)
    assert api.emit(operation=3)
    assert len(requests) == 1
    return backend, api, key, requests[0]


def test_setfocus_detaches_one_explicit_action_and_claim_is_one_use(tray):
    _backend, api, key, request = _focus_request(tray)
    assert request.identifier == key
    assert api.emit(operation=3)
    assert request.validate() and request.validate()
    assert request.claim() and not request.claim()
    assert request.validate() and request.validate()
    assert api.authorized == [42]


@pytest.mark.parametrize(
    "change",
    [
        "foreground",
        "owner",
        "delete",
        "hide",
        "callback",
        "version",
        "reseed",
        "handler",
        "action",
        "close",
    ],
)
@pytest.mark.parametrize("claimed", [False, True])
def test_queued_focus_claim_rechecks_registration_foreground_and_lifetime(
    tray, change, claimed
):
    backend, api, key, request = _focus_request(tray)
    if claimed:
        assert request.claim()
    if change == "foreground":
        api.foreground_identity = (0x5678, 99)
    elif change == "owner":
        api.owners[0x1234] = 99
    elif change == "delete":
        api.emit(operation=2)
    elif change == "hide":
        api.emit(operation=1, flags=8, state=1, mask=1)
    elif change == "callback":
        api.emit(operation=1, flags=1, callback=0x8002)
    elif change == "version":
        api.emit(operation=4, version=4)
    elif change == "reseed":
        api.emit()
    elif change == "handler":
        backend.set_focus_handler(None)
    elif change == "action":
        backend.activate(key)
    else:
        backend.close()
    assert not request.validate()
    assert not request.claim()
    api.foreground_identity = (0x1234, 42)
    assert not request.claim()  # A failed final claim also consumes the request.


@pytest.mark.parametrize("foreground", [None, (0x5678, 99)])
def test_focus_request_cannot_steal_an_unrelated_or_unknown_foreground(
    tray, foreground
):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    backend.activate(backend.get_state().items[0].identifier)
    api.foreground_identity = foreground
    assert api.emit(operation=3) and not requests
    api.foreground_identity = (0x1234, 42)
    assert api.emit(operation=3)


def test_passive_hover_unsolicited_focus_and_failed_action_do_not_arm_focus_return(
    tray,
):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    key = backend.get_state().items[0].identifier
    backend.pointer(key)
    assert not api.emit(operation=3)
    api.post_success = False
    assert not backend.activate(key)
    assert not api.emit(operation=3) and not requests


def test_same_process_focus_may_return_to_docking_but_requires_exact_window(tray):
    _backend, api = tray
    api.foreground_identity = (0x5678, os.getpid())
    _backend, _api, _key, request = _focus_request(tray)
    api.foreground_identity = (0x5679, os.getpid())
    assert not request.claim()


@pytest.mark.parametrize(
    "change", ["used", "foreground", "delete", "hide", "handler", "reseed"]
)
def test_local_late_focus_returns_are_consumed_without_forwarding_to_explorer(
    tray, change
):
    backend, api, _key, request = _focus_request(tray)
    if change == "used":
        assert request.claim()
    elif change == "foreground":
        api.foreground_identity = (0x5678, 99)
    elif change == "delete":
        api.emit(operation=2)
    elif change == "hide":
        api.emit(operation=1, flags=8, state=1, mask=1)
    elif change == "handler":
        backend.set_focus_handler(None)
    else:
        api.emit()
    host = WindowsTrayHost()
    host._on_message = backend._on_message
    host._forward = Mock(return_value=77)
    assert host._project_notification(wire(operation=3)) == (True, True)
    assert host._project_notification(wire(operation=3)) == (True, True)
    assert host._dispatch_notification(10, 11, 12, wire(operation=3)) == 1
    host._forward.assert_not_called()


def test_duplicate_focus_grace_expires_without_extension_and_explorer_resumes(tray):
    backend, api, _key, request = _focus_request(tray)
    assert request.claim()
    host = WindowsTrayHost()
    host._on_message = backend._on_message
    host._forward = Mock(return_value=77)
    api.now = 0.75
    assert host._dispatch_notification(10, 11, 12, wire(operation=3)) == 1
    host._forward.assert_not_called()
    api.now = 1.01
    assert host._dispatch_notification(10, 11, 12, wire(operation=3)) == 77
    host._forward.assert_called_once_with(10, 0x4A, 11, 12, handled=False)
    # History expiry does not itself consume or invalidate a queued handoff.
    assert request.validate()


def test_focus_ownership_covers_an_open_menu_before_starting_duplicate_grace(tray):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    assert backend.context_menu(backend.get_state().items[0].identifier)
    api.now = 299.0
    assert api.emit(operation=3) and len(requests) == 1
    assert requests[0].claim()
    api.now += 0.75
    assert api.emit(operation=3) and len(requests) == 1
    api.now += 0.26
    assert not api.emit(operation=3)


def test_unanswered_focus_interaction_expires_and_new_explicit_action_can_arm(tray):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    key = backend.get_state().items[0].identifier
    assert backend.context_menu(key)
    api.now = 300.0
    assert not api.emit(operation=3) and not requests
    assert backend._pending_action is None
    assert backend.activate(key)
    assert api.emit(operation=3) and len(requests) == 1
    assert requests[0].claim()


def test_invalidation_cannot_restart_expired_focus_ownership(tray):
    backend, api = tray
    backend.set_focus_handler(lambda request: None)
    api.emit()
    assert backend.context_menu(backend.get_state().items[0].identifier)
    api.now = 300.0
    backend.set_focus_handler(None)
    assert not api.emit(operation=3)


@pytest.mark.parametrize("change", ["delete", "hide", "handler", "shell", "reseed"])
def test_invalidated_open_interaction_has_only_bounded_focus_grace(tray, change):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    assert backend.context_menu(backend.get_state().items[0].identifier)
    api.now = 100.0
    if change == "delete":
        api.emit(operation=2)
    elif change == "hide":
        api.emit(operation=1, flags=8, state=1, mask=1)
    elif change == "handler":
        backend.set_focus_handler(None)
    elif change == "shell":
        api.shell_changed()
    else:
        api.emit()
    api.now += 0.75
    assert api.emit(operation=3) and not requests
    api.now += 0.26
    assert not api.emit(operation=3) and not requests


def test_partial_action_dispatch_failure_bounds_focus_ownership(tray, monkeypatch):
    backend, api = tray
    backend.set_focus_handler(lambda request: None)
    api.emit()
    # Legacy context actions dispatch DOWN then UP. A successful first event
    # may already open provider UI even when dispatching the second fails.
    monkeypatch.setattr(api, "post", Mock(side_effect=[True, False]))
    assert not backend.context_menu(backend.get_state().items[0].identifier)
    assert api.emit(operation=3)
    api.now = 1.01
    assert not api.emit(operation=3)


def test_never_local_or_replacement_provider_focus_still_belongs_to_explorer(tray):
    backend, api = tray
    api.emit(guid=GUID)
    host = WindowsTrayHost()
    host._on_message = backend._on_message
    assert host._project_notification(wire(guid=GUID, operation=3)) == (False, False)
    backend.set_focus_handler(lambda request: None)
    assert backend.activate("guid:" + GUID)
    assert host._project_notification(wire(guid=GUID, window=0x5678, operation=3)) == (
        False,
        False,
    )
    api.emit(operation=2, guid=GUID)
    api.owners[0x5678] = 99
    assert api.emit(window=0x5678, guid=GUID)
    assert host._project_notification(wire(guid=GUID, window=0x5678, operation=3)) == (
        False,
        False,
    )


@pytest.mark.parametrize(
    "operation,local", [(0, False), (1, False), (2, False), (3, True), (4, False)]
)
def test_host_intercepts_only_claimed_focus_and_forwards_balloon_packets(
    operation, local
):
    host = WindowsTrayHost()
    host._on_message = lambda message: True
    assert host._project_notification(wire(operation=operation, flags=0x10)) == (
        True,
        local,
    )
    host._on_message = lambda message: False
    assert host._project_notification(wire(operation=operation, flags=0x10)) == (
        False,
        False,
    )


def test_another_docking_window_cannot_receive_a_provider_focus_handoff(tray):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    assert backend.activate(backend.get_state().items[0].identifier)
    api.foreground_identity = (0x5678, os.getpid())
    assert api.emit(operation=3) and not requests


def test_host_rejects_foreign_guid_collision_but_accepts_same_owner_shell_reseed(tray):
    backend, api = tray
    api.emit(guid=GUID)
    api.owners[0x5678] = 99
    host = WindowsTrayHost()
    host._on_message = backend._on_message
    host._forward = Mock(return_value=0)
    assert host._dispatch_notification(10, 11, 12, wire(guid=GUID, window=0x5678)) == 0
    host._forward.assert_called_once_with(10, 0x4A, 11, 12, handled=False)
    host._forward.reset_mock()
    assert host._dispatch_notification(10, 11, 12, wire(guid=GUID)) == 1
    host._forward.assert_called_once_with(10, 0x4A, 11, 12, handled=True)


def test_never_local_focus_and_balloon_notifications_forward_original_message(tray):
    backend, api = tray
    api.emit()
    host = WindowsTrayHost()
    host._on_message = backend._on_message
    host._forward = Mock(return_value=77)
    assert host._dispatch_notification(10, 11, 12, wire(operation=3)) == 77
    host._forward.assert_called_once_with(10, 0x4A, 11, 12, handled=False)
    host._forward.reset_mock()
    assert host._dispatch_notification(10, 11, 12, wire(operation=1, flags=0x10)) == 1
    host._forward.assert_called_once_with(10, 0x4A, 11, 12, handled=True)


def test_shell_replacement_closes_hover_and_invalidates_queued_focus_without_losing_icons(
    tray,
):
    backend, api = tray
    requests = []
    backend.set_focus_handler(requests.append)
    api.emit()
    api.emit(operation=4, version=4)
    key = backend.get_state().items[0].identifier
    backend.context_menu(key)
    assert api.emit(operation=3) and len(requests) == 1
    backend.pointer(key, point=(-250, 430))
    timer = api.timers[-1]
    timer.fire()
    assert api.posts[-1][3] & 0xFFFF == 0x406
    api.shell_changed()
    assert api.posts[-1][3] & 0xFFFF == 0x407
    assert not requests[0].claim()
    count = len(api.posts)
    timer.fire()
    assert len(api.posts) == count
    assert backend.get_state().items[0].identifier == key
    assert api.emit(operation=3) and len(requests) == 1


def test_host_notifies_projection_before_shell_reregistration_broadcast(monkeypatch):
    host = WindowsTrayHost()
    host._user = object()
    host._explorer = 101
    events = []
    monkeypatch.setattr(host, "owner", lambda window: 202)
    monkeypatch.setattr(host, "_integrity", lambda pid: 0x2000)
    host._shell_changed = lambda: events.append("invalidate")
    monkeypatch.setattr(host, "_announce", lambda: events.append("broadcast"))
    host._seed_changed_explorer()
    host._seed_changed_explorer()
    assert events == ["invalidate", "broadcast"]


def test_hover_settings_only_read_the_sdk_mouse_dwell_parameters(monkeypatch):
    import docking.platform.tray.implementations.windows.host as module

    host = WindowsTrayHost()
    host._user = object()
    calls = []

    def query(operation, argument, value, flags):
        calls.append((operation, argument, flags))
        value._obj.value = {0x66: 400, 0x62: 4, 0x64: 6}[operation]
        return 1

    monkeypatch.setattr(module, "bind", lambda *args: query)
    assert host.hover_settings() == (400, 4, 6)
    assert calls == [(0x66, 0, 0), (0x62, 0, 0), (0x64, 0, 0)]
