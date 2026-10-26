"""Notification wire decoding, explicit callback dispatch and stale-owner safety."""

import ctypes
import struct
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from docking.platform import environment
from docking.platform.backends.base import Rect
from docking.platform.tray.backend import create_backend
from docking.platform.tray.state import TrayIconPixmap
from docking.platform.tray.windows import WindowsTrayBackend
from docking.platform.tray.windows_host import WindowsTrayHost
from docking.platform.tray.windows_icons import composite_pixels
from docking.platform.tray.windows_messages import (
    callback_parameters,
    decode_icon_query,
    decode_message,
    icon_query_reply,
)

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

    def start(self, callback, icon_bounds=None):
        if self.fail:
            raise OSError("Host failed")
        self.callback = callback
        self.icon_bounds = icon_bounds

    def owner(self, window):
        return self.owners.get(window, 0)

    def pixels(self, icon):
        self.images.append(icon)
        return TrayIconPixmap(1, 1, bytes((icon, 2, 3, 255)))

    def point(self):
        return -200, 420

    def post(self, *arguments):
        self.posts.append(arguments)
        return True

    def authorize(self, pid):
        self.authorized.append(pid)

    def close(self):
        self.closed += 1

    def emit(self, **fields):
        message = decode_message(wire(**fields))
        assert message is not None and self.callback is not None
        return self.callback(message)


@pytest.fixture
def tray(monkeypatch):
    api = API()
    backend = WindowsTrayBackend(api=api)
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
    monkeypatch.setitem(sys.modules, "docking.platform.tray.linux", None)
    monkeypatch.setitem(sys.modules, "docking.applets.systemtray.xembed", None)

    def fail(*args, **kwargs):
        raise AssertionError("Constructor must stay lazy")

    monkeypatch.setattr(ctypes, "WinDLL", fail, raising=False)
    backend = create_backend()
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
