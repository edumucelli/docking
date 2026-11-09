"""AppBar forwarding preserves output and releases only owned shared handles."""

import ctypes
import struct
from contextlib import contextmanager

import pytest

from docking.platform.tray.implementations.windows.appbar import WindowsAppBarBridge


def packet(operation=2):
    data = bytearray(b"\xa5" * 64)
    struct.pack_into("<IIIIiiiiq", data, 0, 40, 101, 0xC001, 3, -1280, 0, 0, 752, -1)
    struct.pack_into("<I", data, 40, operation)
    struct.pack_into("<QI", data, 48, 202, 303)
    return bytes(data)


class Memory:
    def __init__(self, *, failed=""):
        self.failed = failed
        self.events = []
        self.original = ctypes.create_string_buffer(packet()[:40])
        self.shared = None

    @contextmanager
    def lock(self, handle, pid):
        self.events.append(("lock", handle, pid))
        if self.failed == "original" or (self.failed == "reply" and handle == 404):
            raise OSError("mapping failed")
        try:
            yield ctypes.addressof(self.original if handle == 202 else self.shared)
        finally:
            self.events.append(("unlock", handle, pid))

    def allocate(self, pointer, size, pid):
        self.events.append(("allocate", size, pid))
        if self.failed == "allocate":
            return 0
        self.shared = ctypes.create_string_buffer(ctypes.string_at(pointer, size))
        return 404

    def free(self, handle, pid):
        self.events.append(("free", handle, pid))


@pytest.mark.parametrize("operation", [2, 3, 5])
def test_relay_retargets_shared_reply_and_copies_explorer_output(operation):
    memory = Memory()
    bridge = WindowsAppBarBridge(memory=memory)
    original = packet(operation)
    expected = bytearray(original[:40])
    struct.pack_into("<iiii", expected, 16, -1280, 0, 0, 681)

    def send(data):
        assert data[:48] == original[:48] and data[60:] == original[60:]
        assert struct.unpack_from("<QI", data, 48) == (404, 505)
        assert memory.shared.raw[:40] == memory.original.raw[:40]
        ctypes.memmove(memory.shared, bytes(expected), 40)
        return 1

    assert bridge.forward(original, 505, send) == 1
    assert memory.original.raw[:40] == bytes(expected)
    assert memory.events == [
        ("lock", 202, 303),
        ("allocate", 40, 505),
        ("lock", 404, 505),
        ("unlock", 404, 505),
        ("free", 404, 505),
        ("unlock", 202, 303),
    ]


@pytest.mark.parametrize("failure", ["original", "allocate", "send", "reply"])
def test_failed_relay_releases_maps_and_its_allocation(failure):
    memory = Memory(failed=failure)
    bridge = WindowsAppBarBridge(memory=memory)

    def send(_data):
        if failure == "send":
            raise OSError("send failed")
        return 1

    with pytest.raises(OSError):
        bridge.forward(packet(), 505, send)
    assert memory.original.raw[:40] == packet()[:40]
    freed = [e for e in memory.events if e[0] == "free"]
    assert freed == ([("free", 404, 505)] if failure in {"send", "reply"} else [])
    assert memory.events[-1] == (
        ("lock", 202, 303) if failure == "original" else ("unlock", 202, 303)
    )


def test_send_timeout_keeps_callers_original_data_and_frees_shared_reply():
    memory = Memory()

    def send(_data):
        ctypes.memset(memory.shared, 0, 40)
        return 0

    assert WindowsAppBarBridge(memory=memory).forward(packet(), 505, send) == 0
    assert memory.original.raw[:40] == packet()[:40]
    assert memory.events[-2:] == [("free", 404, 505), ("unlock", 202, 303)]
    assert ("lock", 404, 505) not in memory.events


@pytest.mark.parametrize("operation", [0, 1, 4, 6, 7, 8, 9, 10, 11, 12, 0xFFFFFFFF])
def test_input_only_and_unknown_operations_remain_for_normal_forwarding(operation):
    memory = Memory()
    assert (
        WindowsAppBarBridge(memory=memory).forward(packet(operation), 505, None) is None
    )
    assert memory.events == []


@pytest.mark.parametrize(
    "data", [b"", b"short", b"\0" * 64, packet()[:-1], packet() + b"x"]
)
def test_unknown_wire_layout_never_maps_a_handle(data):
    memory = Memory()
    assert WindowsAppBarBridge(memory=memory).forward(data, 505, None) is None
    assert memory.events == []


@pytest.mark.parametrize(
    "handle,pid,explorer", [(0, 303, 505), (202, 0, 505), (202, 303, 0)]
)
def test_missing_shared_identity_fails_without_touching_memory(handle, pid, explorer):
    data = bytearray(packet())
    struct.pack_into("<QI", data, 48, handle, pid)
    memory = Memory()
    assert WindowsAppBarBridge(memory=memory).forward(bytes(data), explorer, None) == 0
    assert memory.events == []
