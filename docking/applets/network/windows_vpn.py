"""VPN actions exposed by the Windows ConnectionManager implementation."""

from __future__ import annotations

import getpass
import ntpath
import os
import threading
from collections import Counter
from collections.abc import Callable

from gi.repository import GLib

from docking.applets.network.backend import VpnConnection
from docking.applets.network.windows_ras import VpnProfile, _Ras, launch_dialer, ras_api
from docking.log import get_logger

log = get_logger("network.windows_vpn")


class WindowsVpn:
    def __init__(
        self,
        *,
        api: _Ras | None = None,
        dial: Callable[[VpnProfile], None] = launch_dialer,
        idle_add=GLib.idle_add,
    ):
        self._api = api
        self._dial = dial
        self._idle_add = idle_add
        self._profiles: dict[str, VpnProfile] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._generation = 0
        self._stopped = False
        self.changed: Callable[[], None] = lambda: None

    def _native(self) -> _Ras:
        return self._api if self._api is not None else ras_api()

    def connections(self) -> list[VpnConnection]:
        if self._stopped:
            return []
        try:
            profiles, sessions = self._native().profiles(), self._native().sessions()
        except OSError as exc:
            log.debug("VPN inventory unavailable: %s", exc)
            self._profiles = {}
            return []
        self._profiles = {p.key: p for p in profiles}
        counts = Counter(p.name.casefold() for p in profiles)
        result = []
        for profile in profiles:
            matches = [s for s in sessions if s.matches(profile)]
            label = profile.name
            if counts[label.casefold()] > 1:
                scope = (
                    ntpath.basename(
                        os.environ.get("ALLUSERSPROFILE", r"C:\ProgramData")
                    )
                    if profile.all_users
                    else getpass.getuser()
                )
                label += f" ({scope})"
            pending = profile.key in self._workers
            result.append(
                VpnConnection(
                    profile.key,
                    label,
                    any(s.connected for s in matches),
                    pending or bool(matches and not any(s.connected for s in matches)),
                    profile.phonebook,
                    all(s.can_disconnect for s in matches),
                )
            )
        return sorted(result, key=lambda v: (v.label.casefold(), v.uuid))

    def toggle(self, key: str) -> None:
        if self._stopped or key not in self._profiles or key in self._workers:
            return
        api = self._native()
        try:
            profile = next((p for p in api.profiles() if p.key == key), None)
            if profile is None:
                return
            sessions = [s for s in api.sessions() if s.matches(profile)]
            if not sessions:
                # OS-owned UI stays independent of the dock, as do Settings windows.
                self._dial(profile)
                return
            if not all(s.connected and s.can_disconnect for s in sessions):
                return
        except OSError as exc:
            log.warning("Could not activate VPN profile: %s", exc)
            return
        generation = self._generation

        def disconnect():
            try:
                api.disconnect(sessions)
            except OSError as exc:
                log.warning("Could not disconnect VPN profile: %s", exc)
            finally:
                self._idle_add(finish)

        def finish():
            if (
                generation == self._generation
                and not self._stopped
                and self._workers.get(key) is worker
            ):
                self._workers.pop(key, None)
                self.changed()
            return False

        worker = threading.Thread(
            target=disconnect, name="windows-vpn-disconnect", daemon=True
        )
        self._workers[key] = worker
        try:
            worker.start()
        except RuntimeError as exc:
            self._workers.pop(key, None)
            log.warning("Could not start VPN disconnect: %s", exc)

    def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        self.changed = lambda: None
        self._profiles = {}
        # RAS requires teardown to finish before the caller exits its process.
        # The native wait for a batch of connections is bounded at ten seconds.
        for worker in list(self._workers.values()):
            worker.join(11)
        self._workers = {}
