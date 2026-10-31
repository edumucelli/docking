"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, ClassVar

import gi

from docking.applets.powerprofiles.backend import (
    PowerprofilesOperations as PowerprofilesOperationsBase,
)

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from ...backend import PowerProfilesControlBackend
from ...state import (
    DBUS_GET_ALL_TIMEOUT_MS,
    DBUS_IFACE,
    DBUS_NAME_HAS_OWNER_TIMEOUT_MS,
    DBUS_PATH,
    DBUS_PROPERTY_SET_TIMEOUT_MS,
    DBUS_SERVICE,
    DEFAULT_COMMAND_TIMEOUT_S,
    PATH,
    POWER_PROFILES_IFACE,
    PROPERTIES_IFACE,
    SERVICE,
    TLP_SET_TIMEOUT_S,
    TLP_STATUS_TIMEOUT_S,
    TUNED_ACTIVE_TIMEOUT_S,
    TUNED_LIST_TIMEOUT_S,
    TUNED_SET_TIMEOUT_S,
    PowerProfilesState,
    _as_str,
    _unpack,
    log,
    normalize_profile,
    order_profiles,
    unavailable_state,
)


def detect_backend() -> PowerProfilesControlBackend:
    """Select first available backend: PPD DBus -> tuned -> TLP.

    Detection intentionally executes lightweight state probes:
    - PPD probe checks DBus owner/properties.
    - tuned/TLP probes check command availability and query state.

    Returning a backend object (instead of state only) keeps subsequent state
    polling and profile-change requests backend-consistent.
    """
    ppd = PowerProfilesBackend()
    if ppd.get_state().available:
        return ppd

    if _has_command("tuned-adm"):
        tuned = TunedBackend()
        if tuned.get_state().available:
            return tuned

    if _has_command("tlp") or _has_command("tlp-stat"):
        tlp = TlpBackend()
        if tlp.get_state().available:
            return tlp

    return NullPowerProfilesBackend()


class NullPowerProfilesBackend(PowerProfilesControlBackend):
    """Unavailable backend placeholder.

    This backend preserves a stable contract when no real backend exists.
    The applet can still render an informative unavailable tooltip/menu.
    """

    def get_state(self) -> PowerProfilesState:
        return unavailable_state(error="No supported backend found")

    def set_active_profile(self, profile: str) -> bool:
        _ = profile
        return False


class PowerProfilesBackend(PowerProfilesControlBackend):
    """power-profiles-daemon DBus backend.

    This is the preferred backend and the only one with true profile semantics
    matching the applet's profile model.
    """

    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._dbus_proxy: Gio.DBusProxy | None = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                DBUS_SERVICE,
                DBUS_PATH,
                DBUS_IFACE,
                None,
            )
        except GLib.Error as exc:
            log.bind(action="bus_init").warning(
                "Failed to connect system bus: %s",
                exc,
            )
            self._bus = None
            self._dbus_proxy = None

    def get_state(self) -> PowerProfilesState:
        """Read PPD DBus state and translate into canonical applet state."""
        if not self._has_service_owner():
            return unavailable_state(error="power-profiles-daemon not available")

        props = self._get_all_properties()
        if props is None:
            return unavailable_state(error="Failed to query power profile state")

        active_profile = normalize_profile(_as_str(props.get("ActiveProfile")))
        profiles = self._extract_profiles(props=props, active_profile=active_profile)
        degraded = _as_str(props.get("PerformanceInhibited"))

        return PowerProfilesState(
            available=True,
            active_profile=active_profile,
            profiles=profiles,
            degraded_reason=degraded,
            error="",
        )

    def set_active_profile(self, profile: str) -> bool:
        """Set ``ActiveProfile`` DBus property on PPD."""
        normalized = normalize_profile(profile)
        if not normalized:
            return False
        return self._set_property(
            interface=POWER_PROFILES_IFACE,
            property_name="ActiveProfile",
            signature="s",
            value=normalized,
        )

    def _has_service_owner(self) -> bool:
        """Return True when PPD DBus name currently has an owner."""
        if self._dbus_proxy is None:
            return False
        try:
            result = self._dbus_proxy.call_sync(
                "NameHasOwner",
                GLib.Variant("(s)", (SERVICE,)),
                Gio.DBusCallFlags.NONE,
                DBUS_NAME_HAS_OWNER_TIMEOUT_MS,
                None,
            )
            unpacked = result.unpack() if result is not None else ()
            return bool(unpacked[0]) if unpacked else False
        except GLib.Error as exc:
            log.debug("PowerProfiles NameHasOwner failed: %s", exc)
            return False

    def _get_all_properties(self) -> dict[str, Any] | None:
        """Fetch all properties from PPD interface using DBus Properties.GetAll."""
        if self._bus is None:
            return None
        try:
            result = self._bus.call_sync(
                SERVICE,
                PATH,
                PROPERTIES_IFACE,
                "GetAll",
                GLib.Variant("(s)", (POWER_PROFILES_IFACE,)),
                GLib.VariantType("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                DBUS_GET_ALL_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            log.bind(action="GetAll").debug("PowerProfiles query failed: %s", exc)
            return None

        unpacked = result.unpack() if result is not None else ()
        if not unpacked:
            return {}
        raw = _unpack(unpacked[0])
        if isinstance(raw, dict):
            return raw
        return {}

    def _set_property(
        self,
        *,
        interface: str,
        property_name: str,
        signature: str,
        value: Any,
    ) -> bool:
        """Set one DBus property on the PPD object."""
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                SERVICE,
                PATH,
                PROPERTIES_IFACE,
                "Set",
                GLib.Variant(
                    "(ssv)",
                    (interface, property_name, GLib.Variant(signature, value)),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                DBUS_PROPERTY_SET_TIMEOUT_MS,
                None,
            )
            return True
        except GLib.Error as exc:
            log.bind(action=f"set_{property_name}").debug(
                "PowerProfiles set failed: %s",
                exc,
            )
            return False

    @staticmethod
    def _extract_profiles(
        *,
        props: dict[str, Any],
        active_profile: str,
    ) -> tuple[str, ...]:
        """Extract canonical profile list from PPD ``Profiles`` payload.

        BlueZ-like nested variants are unpacked first via ``_unpack``.
        We accept common key spellings to stay resilient to variant wrappers.
        """
        extracted: list[str] = []
        raw_profiles = _unpack(props.get("Profiles"))
        if isinstance(raw_profiles, list | tuple):
            for entry in raw_profiles:
                if not isinstance(entry, dict):
                    continue
                profile = ""
                for key in ("Profile", "profile", "Name", "name"):
                    profile = _as_str(entry.get(key))
                    if profile:
                        break
                normalized = normalize_profile(profile)
                if normalized:
                    extracted.append(normalized)

        if active_profile and active_profile not in extracted:
            extracted.append(active_profile)

        if not extracted and active_profile:
            extracted = [active_profile]

        return order_profiles(tuple(extracted))


class TunedBackend(PowerProfilesControlBackend):
    """Fallback backend using tuned-adm.

    ``tuned`` has many profile names and different naming conventions.
    This backend maps those names into the applet's canonical trio:
    performance / balanced / power-saver.
    """

    _PROFILE_PREFS: ClassVar[dict[str, tuple[str, ...]]] = {
        "performance": (
            "throughput-performance",
            "latency-performance",
            "accelerator-performance",
            "network-latency",
        ),
        "balanced": ("balanced",),
        "power-saver": ("powersave",),
    }

    def get_state(self) -> PowerProfilesState:
        """Read active and available tuned profiles, then canonicalize."""
        if not _has_command("tuned-adm"):
            return unavailable_state(error="tuned-adm not available")

        active_raw = self._active_profile_name()
        available_raw = self._available_profile_names()
        canonical = [self._canonical_profile(name=name) for name in available_raw]
        profiles = order_profiles(
            tuple(profile for profile in canonical if profile in _supported_profiles())
        )

        active = self._canonical_profile(name=active_raw)
        if active and active not in profiles:
            profiles = order_profiles((*profiles, active))

        if not profiles:
            return unavailable_state(error="tuned profile data unavailable")

        if not active:
            active = profiles[0]

        return PowerProfilesState(
            available=True,
            active_profile=active,
            profiles=profiles,
            degraded_reason="Fallback backend: tuned-adm",
            error="",
        )

    def set_active_profile(self, profile: str) -> bool:
        """Set tuned profile by selecting the best matching tuned profile name."""
        canonical = normalize_profile(profile)
        if canonical not in _supported_profiles():
            return False

        available = self._available_profile_names()
        target = self._select_tuned_profile_name(
            canonical=canonical,
            available_profiles=available,
        )
        if not target:
            return False
        out = _run(cmd=["tuned-adm", "profile", target], timeout_s=TUNED_SET_TIMEOUT_S)
        return out is not None

    def _active_profile_name(self) -> str:
        """Parse active tuned profile from ``tuned-adm active`` output."""
        text = _run(cmd=["tuned-adm", "active"], timeout_s=TUNED_ACTIVE_TIMEOUT_S) or ""
        match = re.search(r"current active profile:\s*([^\n]+)", text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
        return ""

    def _available_profile_names(self) -> tuple[str, ...]:
        """Parse available tuned profile names from ``tuned-adm list``."""
        text = _run(cmd=["tuned-adm", "list"], timeout_s=TUNED_LIST_TIMEOUT_S) or ""
        names: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line.startswith("-"):
                continue
            name = line[1:].strip().split(maxsplit=1)[0].strip()
            if name:
                names.append(name)
        return tuple(dict.fromkeys(names))

    def _select_tuned_profile_name(
        self,
        *,
        canonical: str,
        available_profiles: tuple[str, ...],
    ) -> str:
        """Choose concrete tuned profile name for a canonical target profile."""
        lowered = {name.lower(): name for name in available_profiles}
        for preferred in self._PROFILE_PREFS.get(canonical, ()):
            chosen = lowered.get(preferred.lower())
            if chosen:
                return chosen

        # Fallback heuristic by token matching.
        tokens: tuple[str, ...]
        if canonical == "performance":
            tokens = ("performance",)
        elif canonical == "power-saver":
            tokens = ("power", "save")
        else:
            tokens = ("balanced",)

        for name in available_profiles:
            low = name.lower()
            if all(token in low for token in tokens):
                return name
        return ""

    @staticmethod
    def _canonical_profile(*, name: str) -> str:
        """Map tuned profile names to canonical applet profiles."""
        low = name.strip().lower()
        if not low:
            return ""
        if "balanced" in low:
            return "balanced"
        if "power" in low and "save" in low:
            return "power-saver"
        if "performance" in low or "throughput" in low or "latency" in low:
            return "performance"
        return normalize_profile(low)


class TlpBackend(PowerProfilesControlBackend):
    """Fallback backend using TLP mode commands.

    TLP is fundamentally mode-oriented rather than profile-oriented.
    We therefore expose a best-effort mapping and mark state as degraded.
    """

    def get_state(self) -> PowerProfilesState:
        """Map TLP status output to canonical profile state."""
        if not (_has_command("tlp") or _has_command("tlp-stat")):
            return unavailable_state(error="tlp not available")

        status = _run(cmd=["tlp-stat", "-s"], timeout_s=TLP_STATUS_TIMEOUT_S) or ""
        lowered = status.lower()
        if "mode" in lowered and "battery" in lowered:
            active = "power-saver"
        elif "mode" in lowered and "ac" in lowered:
            active = "performance"
        elif "power source" in lowered and "battery" in lowered:
            active = "power-saver"
        elif "power source" in lowered and "ac" in lowered:
            active = "performance"
        else:
            active = "balanced"

        return PowerProfilesState(
            available=True,
            active_profile=active,
            profiles=("power-saver", "balanced", "performance"),
            degraded_reason="Fallback backend: tlp mode mapping",
            error="",
        )

    def set_active_profile(self, profile: str) -> bool:
        """Map canonical profiles to TLP commands."""
        canonical = normalize_profile(profile)
        if canonical == "power-saver":
            out = _run(cmd=["tlp", "bat"], timeout_s=TLP_SET_TIMEOUT_S)
            return out is not None
        if canonical == "performance":
            out = _run(cmd=["tlp", "ac"], timeout_s=TLP_SET_TIMEOUT_S)
            return out is not None
        if canonical == "balanced":
            out = _run(cmd=["tlp", "start"], timeout_s=TLP_SET_TIMEOUT_S)
            return out is not None
        return False


def _run(cmd: list[str], timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> str | None:
    """Run command and return stdout on success; ``None`` on failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action="run").debug("Failed running %s: %s", cmd, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _has_command(command: str) -> bool:
    """Return True when command exists in PATH."""
    return shutil.which(command) is not None


def _supported_profiles() -> tuple[str, ...]:
    """Canonical profile IDs supported by applet presentation."""
    return ("power-saver", "balanced", "performance")


class PowerprofilesOperations(PowerprofilesOperationsBase):
    def detect_backend(self) -> PowerProfilesControlBackend:
        return detect_backend()
