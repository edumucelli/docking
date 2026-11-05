"""Windows WLAN bindings and Wi-Fi network/radio values.

Connection management implements the shared contract in windows_connections.
These bindings own one client handle per operation and never read credentials.
"""

from __future__ import annotations

import ctypes
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

from docking.applets.network.state import AvailableNetwork
from docking.log import get_logger
from docking.platform.native.windows.com import GUID
from docking.platform.native.windows.ffi import bind

log = get_logger("network.windows_wifi")
_RADIO_STATE = 4


class _Interface(ctypes.Structure):
    _fields_ = [
        ("identifier", GUID),
        ("description", ctypes.c_uint16 * 256),
        ("state", ctypes.c_uint32),
    ]


class _Interfaces(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("index", ctypes.c_uint32),
        ("items", _Interface * 1),
    ]


class _SSID(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("data", ctypes.c_ubyte * 32)]


class _Network(ctypes.Structure):
    _fields_ = [
        ("profile", ctypes.c_uint16 * 256),
        ("ssid", _SSID),
        ("bss_type", ctypes.c_uint32),
        ("bssids", ctypes.c_uint32),
        ("connectable", ctypes.c_int32),
        ("reason", ctypes.c_uint32),
        ("phy_count", ctypes.c_uint32),
        ("phy_types", ctypes.c_uint32 * 8),
        ("more_phys", ctypes.c_int32),
        ("strength", ctypes.c_uint32),
        ("security", ctypes.c_int32),
        ("auth", ctypes.c_uint32),
        ("cipher", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class _Networks(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("index", ctypes.c_uint32),
        ("items", _Network * 1),
    ]


class _PhyRadio(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("software", ctypes.c_uint32),
        ("hardware", ctypes.c_uint32),
    ]


class _Radio(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("phys", _PhyRadio * 64)]


class _ConnectionParameters(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_uint32),
        ("profile", ctypes.c_void_p),
        ("ssid", ctypes.POINTER(_SSID)),
        ("bssids", ctypes.c_void_p),
        ("bss_type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class _RadioState:
    identifier: str
    index: int
    software: bool
    hardware: bool


@dataclass(frozen=True)
class _Candidate:
    identifier: str
    ssid: bytes
    profile: str
    bss_type: int
    strength: int
    active: bool
    connectable: bool

    @property
    def key(self) -> str:
        # A display SSID is not an identity: adapters/profiles/security can differ.
        return json.dumps(
            (self.identifier, self.ssid.hex(), self.profile, self.bss_type)
        )

    def visible(self) -> AvailableNetwork:
        return AvailableNetwork(
            self.ssid.decode("utf-8", errors="replace"),
            self.strength,
            self.key,
            self.active,
            self.connectable,
        )


def _check(result: int, action: str) -> None:
    if result:
        raise OSError(result, f"{action} failed")


class _Wlan:
    def __init__(self):
        dll = ctypes.WinDLL("wlanapi", use_last_error=True)
        self._open = bind(
            dll,
            "WlanOpenHandle",
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._close = bind(
            dll, "WlanCloseHandle", ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p
        )
        self._enum = bind(
            dll,
            "WlanEnumInterfaces",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._query = bind(
            dll,
            "WlanQueryInterface",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        )
        self._set = bind(
            dll,
            "WlanSetInterface",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        self._scan = bind(
            dll,
            "WlanScan",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        self._networks = bind(
            dll,
            "WlanGetAvailableNetworkList",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._connect = bind(
            dll,
            "WlanConnect",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(_ConnectionParameters),
            ctypes.c_void_p,
        )
        self._free = bind(dll, "WlanFreeMemory", None, ctypes.c_void_p)

    @contextmanager
    def _client(self):
        version, handle = ctypes.c_uint32(), ctypes.c_void_p()
        _check(
            self._open(2, None, ctypes.byref(version), ctypes.byref(handle)),
            "WlanOpenHandle",
        )
        if not handle.value:
            raise OSError("Missing WLAN client handle")
        try:
            yield handle
        finally:
            self._close(handle, None)

    @contextmanager
    def _memory(self):
        pointer = ctypes.c_void_p()
        try:
            yield pointer
        finally:
            if pointer.value:
                self._free(pointer)

    def _identifiers(self, handle) -> list[str]:
        with self._memory() as pointer:
            _check(
                self._enum(handle, None, ctypes.byref(pointer)), "WlanEnumInterfaces"
            )
            if not pointer.value:
                raise OSError("Missing WLAN interface list")
            count = ctypes.cast(pointer, ctypes.POINTER(_Interfaces)).contents.count
            if count > 4096:
                raise OSError("Invalid WLAN interface count")
            entries = (_Interface * count).from_address(
                pointer.value + _Interfaces.items.offset
            )
            return [
                "{" + str(uuid.UUID(bytes_le=bytes(e.identifier))) + "}"
                for e in entries
            ]

    def radios(self) -> tuple[list[str], list[_RadioState]]:
        with self._client() as handle:
            identifiers = self._identifiers(handle)
            radios = []
            for identifier in identifiers:
                guid, size = GUID.parse(identifier), ctypes.c_uint32()
                try:
                    with self._memory() as pointer:
                        _check(
                            self._query(
                                handle,
                                ctypes.byref(guid),
                                _RADIO_STATE,
                                None,
                                ctypes.byref(size),
                                ctypes.byref(pointer),
                                None,
                            ),
                            "WlanQueryInterface(radio)",
                        )
                        if not pointer.value or size.value < 4:
                            raise OSError("Missing WLAN radio state")
                        count = ctypes.cast(
                            pointer, ctypes.POINTER(ctypes.c_uint32)
                        ).contents.value
                        if count > 64 or size.value < 4 + count * ctypes.sizeof(
                            _PhyRadio
                        ):
                            raise OSError("Invalid WLAN radio state")
                        phys = (_PhyRadio * count).from_address(pointer.value + 4)
                        radios.extend(
                            _RadioState(
                                identifier,
                                int(p.index),
                                p.software == 1,
                                p.hardware == 1,
                            )
                            for p in phys
                            if p.software in (1, 2) and p.hardware in (1, 2)
                        )
                except OSError as exc:
                    log.debug("Wi-Fi radio unavailable for %s: %s", identifier, exc)
            return identifiers, radios

    def available(self, *, scan: bool) -> list[_Candidate]:
        with self._client() as handle:
            result = []
            failure = None
            for identifier in self._identifiers(handle):
                guid = GUID.parse(identifier)
                try:
                    if scan:
                        # The cache is readable while an asynchronous scan completes.
                        code = self._scan(handle, ctypes.byref(guid), None, None, None)
                        if code == 5:
                            _check(code, "WlanScan")
                    with self._memory() as pointer:
                        _check(
                            self._networks(
                                handle,
                                ctypes.byref(guid),
                                0,
                                None,
                                ctypes.byref(pointer),
                            ),
                            "WlanGetAvailableNetworkList",
                        )
                        if not pointer.value:
                            raise OSError("Missing WLAN network list")
                        count = ctypes.cast(
                            pointer, ctypes.POINTER(_Networks)
                        ).contents.count
                        if count > 65536:
                            raise OSError("Invalid WLAN network count")
                        entries = (_Network * count).from_address(
                            pointer.value + _Networks.items.offset
                        )
                        for value in entries:
                            if not 0 < value.ssid.length <= 32:
                                continue
                            profile = (
                                bytes(value.profile)
                                .decode("utf-16-le", errors="surrogatepass")
                                .split("\0", 1)[0]
                            )
                            result.append(
                                _Candidate(
                                    identifier,
                                    bytes(value.ssid.data[: value.ssid.length]),
                                    profile,
                                    int(value.bss_type),
                                    min(100, int(value.strength)),
                                    bool(value.flags & 1),
                                    bool(value.connectable),
                                )
                            )
                except OSError as exc:
                    failure = exc
            if failure is not None and not result:
                raise failure
            return result

    def set_radio(self, *, enabled: bool) -> None:
        # Query again: a menu snapshot's PHY/device may have disappeared.
        _identifiers, radios = self.radios()
        with self._client() as handle:
            for radio in radios:
                guid = GUID.parse(radio.identifier)
                value = _PhyRadio(radio.index, 1 if enabled else 2, 0)
                _check(
                    self._set(
                        handle,
                        ctypes.byref(guid),
                        _RADIO_STATE,
                        ctypes.sizeof(value),
                        ctypes.byref(value),
                        None,
                    ),
                    "WlanSetInterface(radio)",
                )

    def connect(self, candidate: _Candidate) -> None:
        if not candidate.profile or "\0" in candidate.profile:
            raise OSError("Missing WLAN connection profile")
        text = candidate.profile.encode("utf-16-le", errors="surrogatepass")
        if len(text) // 2 > 255:
            raise OSError("Invalid WLAN connection profile")
        profile = (ctypes.c_uint16 * (len(text) // 2 + 1)).from_buffer_copy(
            text + b"\0\0"
        )
        ssid = _SSID(len(candidate.ssid), (ctypes.c_ubyte * 32)(*candidate.ssid))
        parameters = _ConnectionParameters(
            0,
            ctypes.addressof(profile),
            ctypes.pointer(ssid),
            None,
            candidate.bss_type,
            0,
        )
        guid = GUID.parse(candidate.identifier)
        with self._client() as handle:
            _check(
                self._connect(
                    handle, ctypes.byref(guid), ctypes.byref(parameters), None
                ),
                "WlanConnect",
            )


@lru_cache(maxsize=1)
def _wlan() -> _Wlan:
    return _Wlan()
