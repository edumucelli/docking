"""GTK-facing implementation of BrowserBackend for the owned Qt companion."""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.i18n import _
from docking.platform.environment.xdg import docking_data_dir

from ..backend import BrowserBackend, BrowserProfile, ClearCallback
from ..bridge import parse_bridge_message
from ..state import BrowserPhase, NavigationTarget, navigation_target
from .process import BrowserProcess
from .protocol import Configuration


class WindowsCompanionBrowser(BrowserBackend):
    def __init__(
        self,
        *,
        profile: BrowserProfile,
        executable: Path | None = None,
        entrypoint: Path | None = None,
        **callbacks,
    ) -> None:
        super().__init__(profile=profile, **callbacks)
        self._executable = executable or Path(
            os.environ.get("DOCKING_WEBENGINE_PYTHON", "python.exe")
        )
        self._entrypoint = entrypoint
        self._process: BrowserProcess | None = None
        self._started = self._ready = self._visible = self._show_requested = False
        self._lifetime = self._clear_serial = self._recovery = 0
        self._clear_pending: ClearCallback | None = None
        self._clear_sent = False
        self._clear_sources: set[int] = set()
        self._callback_serial = 0

    @property
    def visible(self) -> bool:
        return self._visible

    def start(self) -> bool:
        self._started = True
        if self._process is not None:
            return True
        self._lifetime += 1
        lifetime = self._lifetime
        self._ready = False
        self._set_phase(BrowserPhase.STARTING)

        def packet_received(packet) -> None:
            GLib.idle_add(self._packet, packet, lifetime)

        def process_closed() -> None:
            GLib.idle_add(self._closed, lifetime)

        self._process = BrowserProcess(
            self._executable,
            Configuration(
                self.profile,
                docking_data_dir() / self.profile.session_name / "qtwebengine",
            ),
            on_packet=packet_received,
            on_closed=process_closed,
            entrypoint=self._entrypoint,
        )
        self._process.start()
        return True

    def _send(self, operation: str, **values) -> None:
        process = self._process
        if process is not None:
            try:
                process.send({"op": operation, **values})
            except OSError:
                process.stop()

    def show(self) -> None:
        self._show_requested = True
        self.start()
        if self._ready and self._clear_pending is None:
            if self._process is not None:
                self._process.allow_foreground()
            self._send("show")
        self._on_attention_cleared()

    def hide(self) -> None:
        self._show_requested = False
        if self._ready and self._clear_pending is None:
            self._send("hide")

    def reload(self) -> None:
        if self._ready and self._clear_pending is None:
            self._set_phase(BrowserPhase.STARTING)
            self._send("reload")
        else:
            self.start()

    def clear_session(self, on_complete: ClearCallback) -> None:
        if self._clear_pending is not None:
            return
        self._clear_serial += 1
        self._clear_pending, self._clear_sent = on_complete, False
        self._set_phase(BrowserPhase.STARTING)
        self.start()
        self._request_clear()

    def _request_clear(self) -> None:
        if self._ready and self._clear_pending is not None and not self._clear_sent:
            self._clear_sent = True
            self._send("clear", id=self._clear_serial)

    def _notify_clear(self, success: bool) -> None:
        callback, self._clear_pending = self._clear_pending, None
        self._clear_sent = False
        if callback is None:
            return
        serial = self._callback_serial

        def notify() -> bool:
            self._clear_sources.discard(source)
            if self._started and serial == self._callback_serial:
                callback(
                    success,
                    ""
                    if success
                    else _("Could not disconnect {service}").format(
                        service=self.profile.name
                    ),
                )
            return False

        source = GLib.idle_add(notify)
        self._clear_sources.add(source)

    def _packet(self, packet, lifetime: int) -> bool:
        if not self._started or lifetime != self._lifetime:
            return False
        event, value = packet.get("event"), packet.get("value")
        if event == "started":
            self._ready = True
            if self._clear_pending is not None:
                self._request_clear()
            else:
                self._send("show" if self._show_requested else "hide")
        elif event == "visible" and type(value) is bool:
            self._visible = value
            if value:
                self._on_attention_cleared()
        elif event == "phase" and isinstance(value, str):
            try:
                phase = BrowserPhase(value)
            except ValueError:
                return False
            if phase is BrowserPhase.STARTING:
                self._last_bridge_badge = self._last_bridge_status = None
            self._set_phase(
                phase,
                _("browser process stopped") if phase is BrowserPhase.ERROR else "",
            )
        elif event == "bridge" and isinstance(value, str):
            bridge = parse_bridge_message(value)
            if bridge is not None:
                self._apply_bridge_event(bridge)
        elif event == "title" and isinstance(value, str) and len(value) <= 4096:
            self._on_title(value)
        elif event == "notification":
            self._on_notification()
        elif event == "attention":
            self._on_attention_cleared()
        elif event == "external":
            uri = packet.get("uri")
            if (
                isinstance(uri, str)
                and len(uri) <= 32768
                and navigation_target(uri, self.profile.host)
                is NavigationTarget.EXTERNAL
            ):
                self._open_external_uri(uri)
        elif (
            event == "cleared"
            and type(packet.get("id")) is int
            and packet["id"] == self._clear_serial
            and type(packet.get("ok")) is bool
        ):
            self._ready = self._visible = False
            if packet["ok"]:
                self._on_title("")
                self._on_attention_cleared()
                self._last_bridge_badge = self._last_bridge_status = None
            self._notify_clear(packet["ok"])
        return False

    def _closed(self, lifetime: int) -> bool:
        if not self._started or lifetime != self._lifetime:
            return False
        was_ready = self._ready
        # Packets queued by the terminated process must not revive its UI state
        # while recovery or a failed disconnect callback is being delivered.
        self._lifetime += 1
        self._process = None
        self._ready = self._visible = False
        self._notify_clear(False)
        self._set_phase(
            BrowserPhase.ERROR if was_ready else BrowserPhase.UNAVAILABLE,
            _("Could not open browser session"),
        )
        if was_ready and not self._recovery:
            self._recovery = GLib.timeout_add(1500, self._recover)
        return False

    def _recover(self) -> bool:
        self._recovery = 0
        if self._started:
            self.start()
        return False

    def stop(self) -> None:
        self._started = self._ready = self._visible = self._show_requested = False
        self._lifetime += 1
        self._callback_serial += 1
        self._clear_pending = None
        for source in self._clear_sources:
            GLib.source_remove(source)
        self._clear_sources.clear()
        if self._recovery:
            GLib.source_remove(self._recovery)
            self._recovery = 0
        process, self._process = self._process, None
        if process is not None:
            process.stop()
        self._set_phase(BrowserPhase.STOPPED)
