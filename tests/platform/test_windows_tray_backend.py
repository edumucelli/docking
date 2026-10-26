"""Notification wire decoding, explicit callback dispatch and stale-owner safety."""

import ctypes
import struct
import sys
import uuid
from pathlib import Path

import pytest

from docking.platform import environment
from docking.platform.tray.backend import create_backend
from docking.platform.tray.state import TrayIconPixmap
from docking.platform.tray.windows import WindowsTrayBackend
from docking.platform.tray.windows_icons import composite_pixels
from docking.platform.tray.windows_messages import callback_parameters, decode_message

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

    def start(self, callback):
        if self.fail:
            raise OSError("Host failed")
        self.callback = callback

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
