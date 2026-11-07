"""Windows VPN phonebooks, observed RAS sessions and native dialer actions.

RAS headers use four-byte packing even in the 64-bit ABI. All buffers here
are caller-owned, sized from the API and bounded across topology changes.
No credentials are read or written; Windows' dialer owns authentication UI.
"""

from __future__ import annotations

import ctypes
import json
import ntpath
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

from docking.platform.native.windows.com import GUID
from docking.platform.native.windows.ffi import bind

_WORD = ctypes.c_uint16
_DWORD = ctypes.c_uint32
_SMALL = 603  # ERROR_BUFFER_TOO_SMALL
_GONE = (6, 1168)  # ERROR_INVALID_HANDLE, ERROR_NOT_FOUND during teardown


class _EntryName(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("size", _DWORD),
        ("name", _WORD * 257),
        ("flags", _DWORD),
        ("phonebook", _WORD * 261),
    ]


class _Connection(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("size", _DWORD),
        ("handle", ctypes.c_void_p),
        ("name", _WORD * 257),
        ("device_type", _WORD * 17),
        ("device_name", _WORD * 129),
        ("phonebook", _WORD * 260),
        ("subentry", _DWORD),
        ("identifier", GUID),
        ("flags", _DWORD),
        ("luid", ctypes.c_ubyte * 8),
        ("correlation", GUID),
    ]


class _Entry(ctypes.Structure):
    """Complete Windows 10+ RASENTRYW, including IKEv2 identity fields."""

    _pack_ = 4
    _fields_ = [
        ("size", _DWORD),
        ("options", _DWORD),
        ("country_id", _DWORD),
        ("country_code", _DWORD),
        ("area_code", _WORD * 11),
        ("phone", _WORD * 129),
        ("alternate_offset", _DWORD),
        *[
            (n, ctypes.c_ubyte * 4)
            for n in ("ip", "dns", "dns_alt", "wins", "wins_alt")
        ],
        *[(n, _DWORD) for n in ("frame_size", "net_protocols", "framing_protocol")],
        *[(n, _WORD * 260) for n in ("script", "autodial_dll", "autodial_function")],
        ("device_type", _WORD * 17),
        ("device_name", _WORD * 129),
        ("pad_type", _WORD * 33),
        *[(n, _WORD * 201) for n in ("x25_address", "x25_facilities", "x25_data")],
        *[
            (n, _DWORD)
            for n in (
                "channels",
                "reserved1",
                "reserved2",
                "subentries",
                "dial_mode",
                "dial_extra_percent",
                "dial_extra_sample_seconds",
                "hangup_extra_percent",
                "hangup_extra_sample_seconds",
                "idle_disconnect_seconds",
                "kind",
                "encryption",
                "auth_key",
            )
        ],
        ("identifier", GUID),
        ("custom_dial_dll", _WORD * 260),
        ("vpn_strategy", _DWORD),
        ("options2", _DWORD),
        ("options3", _DWORD),
        ("dns_suffix", _WORD * 256),
        ("tcp_window", _DWORD),
        ("prerequisite_book", _WORD * 260),
        ("prerequisite_entry", _WORD * 257),
        ("redial_count", _DWORD),
        ("redial_pause", _DWORD),
        ("ipv6_dns", ctypes.c_ubyte * 16),
        ("ipv6_dns_alt", ctypes.c_ubyte * 16),
        ("ipv4_metric", _DWORD),
        ("ipv6_metric", _DWORD),
        ("ipv6", ctypes.c_ubyte * 16),
        ("ipv6_prefix", _DWORD),
        ("network_outage_time", _DWORD),
        ("idi", _WORD * 257),
        ("idr", _WORD * 257),
        ("ims_config", ctypes.c_int32),
        ("idi_type", ctypes.c_int32),
        ("idr_type", ctypes.c_int32),
        ("disable_ikev2_fragmentation", ctypes.c_int32),
    ]


class _Endpoint(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("kind", _DWORD), ("address", ctypes.c_ubyte * 16)]


class _Status(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("size", _DWORD),
        ("state", _DWORD),
        ("error", _DWORD),
        ("device_type", _WORD * 17),
        ("device_name", _WORD * 129),
        ("phone", _WORD * 129),
        ("local", _Endpoint),
        ("remote", _Endpoint),
        ("substate", _DWORD),
    ]


def _text(value) -> str:
    return bytes(value).decode("utf-16-le", errors="surrogatepass").split("\0", 1)[0]


def _identifier(value: GUID) -> str:
    return str(uuid.UUID(bytes_le=bytes(value)))


def _book(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


def _check(code: int, action: str) -> None:
    if code:
        raise OSError(code, f"{action} failed")


@dataclass(frozen=True)
class VpnProfile:
    name: str
    phonebook: str
    identifier: str
    all_users: bool = False

    @property
    def key(self) -> str:
        return json.dumps((_book(self.phonebook), self.identifier))


@dataclass(frozen=True)
class VpnSession:
    name: str
    phonebook: str
    identifier: str
    handle: int
    connected: bool
    can_disconnect: bool = True

    def matches(self, profile: VpnProfile) -> bool:
        return self.identifier == profile.identifier and _book(self.phonebook) == _book(
            profile.phonebook
        )


class _Ras:
    def __init__(self):
        dll = ctypes.WinDLL("rasapi32", use_last_error=True)
        self._entries = bind(
            dll,
            "RasEnumEntriesW",
            _DWORD,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_DWORD),
        )
        self._connections = bind(
            dll,
            "RasEnumConnectionsW",
            _DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_DWORD),
        )
        self._properties = bind(
            dll,
            "RasGetEntryPropertiesW",
            _DWORD,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(_DWORD),
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        self._status = bind(
            dll,
            "RasGetConnectStatusW",
            _DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(_Status),
        )
        self._hangup = bind(dll, "RasHangUpW", _DWORD, ctypes.c_void_p)

    def _array(self, function, kind, *prefix):
        size, count = _DWORD(ctypes.sizeof(kind)), _DWORD()
        for _ in range(4):
            if not ctypes.sizeof(kind) <= size.value <= 16 * 1024 * 1024:
                raise OSError("Invalid RAS array size")
            capacity = size.value
            buffer = ctypes.create_string_buffer(capacity)
            ctypes.cast(buffer, ctypes.POINTER(_DWORD))[0] = ctypes.sizeof(kind)
            code = function(*prefix, buffer, ctypes.byref(size), ctypes.byref(count))
            if code == _SMALL:
                continue
            _check(
                code,
                function.__name__
                if hasattr(function, "__name__")
                else "RAS enumeration",
            )
            if count.value * ctypes.sizeof(kind) > capacity:
                raise OSError("RAS array exceeds its buffer")
            # Copy entries before the caller-owned buffer is released.
            return [
                kind.from_buffer_copy(buffer, i * ctypes.sizeof(kind))
                for i in range(count.value)
            ]
        raise OSError(_SMALL, "RAS topology kept changing")

    def profiles(self) -> list[VpnProfile]:
        result = []
        for entry in self._array(self._entries, _EntryName, None, None):
            name, book = _text(entry.name), _text(entry.phonebook)
            if not name or not book:
                continue
            try:
                value = self._entry(book, name)
            except OSError:
                # Deleted, inaccessible or vendor-managed entries are not actions.
                continue
            identifier = _identifier(value.identifier)
            if value.kind == 2 and identifier != str(uuid.UUID(int=0)):
                result.append(VpnProfile(name, book, identifier, bool(entry.flags & 1)))
        return result

    def _entry(self, book: str, name: str) -> _Entry:
        size = _DWORD(ctypes.sizeof(_Entry))
        for _ in range(3):
            if not ctypes.sizeof(_Entry) <= size.value <= 16 * 1024 * 1024:
                raise OSError("Invalid RAS entry size")
            buffer = ctypes.create_string_buffer(size.value)
            ctypes.cast(buffer, ctypes.POINTER(_DWORD))[0] = ctypes.sizeof(_Entry)
            code = self._properties(book, name, buffer, ctypes.byref(size), None, None)
            if code == _SMALL:
                continue
            _check(code, "RasGetEntryPropertiesW")
            return _Entry.from_buffer_copy(buffer)
        raise OSError(_SMALL, "RAS entry kept changing")

    def sessions(self) -> list[VpnSession]:
        result = []
        for item in self._array(self._connections, _Connection):
            if _text(item.device_type).casefold() != "vpn" or not item.handle:
                continue
            status = _Status(size=ctypes.sizeof(_Status))
            code = self._status(item.handle, ctypes.byref(status))
            if code in _GONE:  # A connection disappeared between the two queries.
                continue
            _check(code, "RasGetConnectStatusW")
            result.append(
                VpnSession(
                    _text(item.name),
                    _text(item.phonebook),
                    _identifier(item.identifier),
                    int(item.handle),
                    status.state == 0x2000 and status.error == 0,
                    item.flags & 12 != 4,
                )
            )
        return result

    def disconnect(self, sessions: list[VpnSession]) -> None:
        # Never act on handles retained by an old menu snapshot.
        fresh = self.sessions()
        pending = set()
        for session in sessions:
            if session not in fresh or not session.can_disconnect:
                continue
            code = self._hangup(session.handle)
            if code in _GONE:
                continue
            _check(code, "RasHangUpW")
            pending.add(session.handle)
        deadline = time.monotonic() + 10
        while pending and time.monotonic() < deadline:
            for handle in tuple(pending):
                status = _Status(size=ctypes.sizeof(_Status))
                code = self._status(handle, ctypes.byref(status))
                if code in _GONE:
                    pending.remove(handle)
                else:
                    _check(code, "RasGetConnectStatusW")
            if pending:
                time.sleep(0.05)
        if pending:
            raise TimeoutError("RAS connection did not finish disconnecting")


def launch_dialer(profile: VpnProfile) -> None:
    """Launch Windows' own credential/status UI for the exact phonebook entry."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = bind(
        kernel,
        "GetSystemDirectoryW",
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    path = ctypes.create_unicode_buffer(32768)
    length = get_directory(path, len(path))
    if not 0 < length < len(path):
        raise OSError("Windows system directory is unavailable")
    executable = ntpath.join(path.value, "rasphone.exe")
    # startfile delegates process lifetime to Windows, as with opening Settings.
    # list2cmdline quotes names/paths without a shell or credential arguments.
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("Windows dialer launch is unavailable")
    startfile(
        executable,
        "open",
        subprocess.list2cmdline(["-f", profile.phonebook, "-d", profile.name]),
    )


@lru_cache(maxsize=1)
def ras_api() -> _Ras:
    return _Ras()
