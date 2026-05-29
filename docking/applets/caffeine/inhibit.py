# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Idle and sleep inhibition backends for the Caffeine applet.

Two independent, best-effort locks are held while Caffeine is active:

1. Screensaver/idle, via the session-bus ``ScreenSaver`` service. The lock is a
   cookie returned by ``Inhibit(app, reason)`` and released by ``UnInhibit``.
   It MUST be held on a long-lived connection: a one-shot ``gdbus call Inhibit``
   would release the moment its connection closes, so we keep the Gio
   session-bus connection alive for the applet's lifetime and call ``UnInhibit``
   explicitly. Dropping the cookie without uninhibiting leaks the lock.

2. System sleep/suspend, via a held ``systemd-inhibit`` child process blocking
   ``idle:sleep``. Releasing the lock means terminating that process.

The two are acquired and released independently so a missing one (no session
bus, no systemd) never blocks the other. Several desktops export the same
``Inhibit(ss)->u`` ScreenSaver interface under different names, so we try the
freedesktop name first and fall back through the common vendor variants. GNOME
is intentionally absent: it has no ScreenSaver ``Inhibit`` method (it routes
inhibition through ``SessionManager`` with a different signature); its
``systemd-inhibit`` idle lock covers the idle case instead.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.environment import flatpak

log = get_logger("caffeine.inhibit")

_APP_NAME = "Docking"
_REASON = "Caffeine keeps the session awake"

# (bus name, object path, interface) candidates that all expose the same
# ``Inhibit(s application, s reason) -> u cookie`` / ``UnInhibit(u)`` contract.
_SCREENSAVER_TARGETS: tuple[tuple[str, str, str], ...] = (
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver",
    ),
    ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver"),
    ("org.mate.ScreenSaver", "/org/mate/ScreenSaver", "org.mate.ScreenSaver"),
    (
        "org.cinnamon.ScreenSaver",
        "/org/cinnamon/ScreenSaver",
        "org.cinnamon.ScreenSaver",
    ),
    ("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver", "org.xfce.ScreenSaver"),
)

_DBUS_TIMEOUT_MS = 2000


class Inhibitor(Protocol):
    """Acquire/release contract for a wake lock."""

    @property
    def active(self) -> bool: ...

    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class ScreenSaverInhibitor:
    """Hold a screensaver/idle inhibit cookie on the session bus."""

    def __init__(self) -> None:
        self._conn: Gio.DBusConnection | None = None
        self._target: tuple[str, str, str] | None = None
        self._cookie: int | None = None

    @property
    def active(self) -> bool:
        return self._cookie is not None

    def acquire(self) -> bool:
        if self._cookie is not None:
            return True
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            log.debug("No session bus for screensaver inhibit: %s", exc)
            return False
        for name, path, iface in _SCREENSAVER_TARGETS:
            try:
                result = conn.call_sync(
                    name,
                    path,
                    iface,
                    "Inhibit",
                    GLib.Variant("(ss)", (_APP_NAME, _REASON)),
                    GLib.VariantType("(u)"),
                    Gio.DBusCallFlags.NONE,
                    _DBUS_TIMEOUT_MS,
                    None,
                )
            except GLib.Error as exc:
                log.debug("Screensaver inhibit via %s failed: %s", name, exc)
                continue
            self._conn = conn
            self._target = (name, path, iface)
            self._cookie = int(result.unpack()[0])
            log.debug("Screensaver inhibited via %s (cookie=%s)", name, self._cookie)
            return True
        return False

    def release(self) -> None:
        cookie, conn, target = self._cookie, self._conn, self._target
        self._cookie = None
        self._target = None
        if cookie is None or conn is None or target is None:
            return
        name, path, iface = target
        try:
            conn.call_sync(
                name,
                path,
                iface,
                "UnInhibit",
                GLib.Variant("(u)", (cookie,)),
                None,
                Gio.DBusCallFlags.NONE,
                _DBUS_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            log.debug("Screensaver uninhibit failed: %s", exc)


class SystemdSleepInhibitor:
    """Hold a ``systemd-inhibit`` child blocking idle and sleep."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def acquire(self) -> bool:
        if self.active:
            return True
        cmd = [
            "systemd-inhibit",
            "--what=idle:sleep",
            f"--who={_APP_NAME}",
            f"--why={_REASON}",
            "--mode=block",
            "sleep",
            "infinity",
        ]
        resolved = flatpak.host_command(cmd) or cmd
        try:
            self._proc = subprocess.Popen(
                resolved,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.debug("systemd-inhibit unavailable: %s", exc)
            self._proc = None
            return False
        return True

    def release(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError as exc:
            log.debug("Failed to stop systemd-inhibit: %s", exc)


class CompositeInhibitor:
    """Combine several inhibitors, acquiring/releasing all of them."""

    def __init__(self, parts: tuple[Inhibitor, ...] | None = None) -> None:
        self._parts: tuple[Inhibitor, ...] = (
            parts
            if parts is not None
            else (ScreenSaverInhibitor(), SystemdSleepInhibitor())
        )

    @property
    def active(self) -> bool:
        return any(part.active for part in self._parts)

    def acquire(self) -> bool:
        # Acquire every part (no short-circuit) so a failing one does not skip
        # the others; report success if any lock was taken.
        results = [part.acquire() for part in self._parts]
        return any(results)

    def release(self) -> None:
        for part in self._parts:
            part.release()


def default_inhibitor() -> Inhibitor:
    """Build the production inhibitor (screensaver + systemd sleep)."""
    return CompositeInhibitor()
