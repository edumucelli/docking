"""Persistent native WebView2 implementation of the shared web-app browser."""

from __future__ import annotations

import ctypes
import json
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets.worker import BackgroundWorker
from docking.core.paths import ensure_dir
from docking.i18n import _
from docking.log import get_logger
from docking.platform.backends.base import MonitorSnapshot, Rect
from docking.platform.backends.windows.com import (
    HRESULT,
    method,
    object_result,
    release,
    value,
)
from docking.platform.backends.windows.ffi import window_handle
from docking.platform.backends.windows.geometry import (
    WindowsMonitorGeometry,
    constrain_window,
)
from docking.platform.backends.windows.webview import WebView2Host
from docking.platform.backends.windows.winrt import check
from docking.platform.environment.xdg import docking_data_dir

from .backend import BrowserBackend, BrowserProfile, ClearCallback
from .bridge import parse_bridge_message
from .state import BrowserPhase, NavigationTarget, navigation_target

log = get_logger("webapp.windows")


class WindowsWebAppBrowser(BrowserBackend):
    def __init__(self, *, profile: BrowserProfile, **callbacks) -> None:
        super().__init__(profile=profile, **callbacks)
        self._engine: WebView2Host | None = None
        self._window: Gtk.Window | None = None
        self._area: Gtk.DrawingArea | None = None
        self._worker = BackgroundWorker()
        self._started = False
        self._preparing = False
        self._ready = False
        self._show_requested = False
        self._lifetime = 0
        self._recovery_id = 0
        self._fit_source = 0
        self._clear_pending: ClearCallback | None = None
        self._clearing = False
        self._permission_dialogs: list[Gtk.MessageDialog] = []
        self._permission_requests: dict[int, Callable[[], None]] = {}
        self._permission_idles: dict[int, int] = {}
        self._blocked_navigations: set[int] = set()

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.get_visible())

    def start(self) -> bool:
        self._started = True
        if self._engine is not None or self._preparing:
            return True
        self._preparing = True
        lifetime = self._lifetime
        self._set_phase(BrowserPhase.STARTING)
        self._worker.run(
            name="webapp-profile",
            fn=lambda: ensure_dir(
                docking_data_dir() / self.profile.session_name / "webview2"
            ),
            on_result=lambda path: self._prepared(path, lifetime),
            on_error=lambda error: self._prepare_failed(error, lifetime),
        )
        return True

    def _prepare_failed(self, _error: Exception, lifetime: int) -> bool:
        if self._started and lifetime == self._lifetime:
            self._preparing = False
            self._set_phase(BrowserPhase.ERROR, _("Could not open browser session"))
        return False

    def _prepared(self, profile: Path, lifetime: int) -> bool:
        if not self._started or lifetime != self._lifetime:
            return False
        self._preparing = False
        try:
            window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            window.set_title(self.profile.name)
            display = Gdk.Display.get_default()
            if display is None:
                raise OSError("Browser display unavailable")
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            workarea = None
            if monitor is not None:
                bounds = monitor.get_geometry()
                snapshot = MonitorSnapshot(
                    index=next(
                        i
                        for i in range(display.get_n_monitors())
                        if display.get_monitor(i) == monitor
                    ),
                    geometry=Rect(bounds.x, bounds.y, bounds.width, bounds.height),
                    scale=monitor.get_scale_factor(),
                )
                mapping = WindowsMonitorGeometry().mapping(snapshot, display)
                workarea = mapping.to_logical(mapping.workarea)
            width = min(1100, max(1, workarea.width - 32)) if workarea else 1100
            height = min(720, max(1, workarea.height - 80)) if workarea else 720
            window.set_default_size(width, height)
            window.set_size_request(min(640, width), min(480, height))
            if workarea is not None:
                window.move(workarea.x + (workarea.width - width) // 2, workarea.y + 8)
            window.set_skip_taskbar_hint(True)
            window.set_role(f"docking-{self.profile.session_name}")
            window.connect("delete-event", self._delete)
            window.connect("map-event", lambda *_args: self._schedule_fit())
            window.connect("focus-in-event", lambda *_args: self._attention())
            header = Gtk.HeaderBar()
            header.set_title(self.profile.name)
            header.set_subtitle(_("Docking applet"))
            header.set_show_close_button(True)
            button = Gtk.Button.new_from_icon_name(
                "view-refresh-symbolic", Gtk.IconSize.BUTTON
            )
            button.set_tooltip_text(
                _("Reload {service}").format(service=self.profile.name)
            )
            button.connect("clicked", lambda _button: self.reload())
            header.pack_start(button)
            window.set_titlebar(header)
            area = Gtk.DrawingArea()
            area.set_hexpand(True)
            area.set_vexpand(True)
            window.add(area)
            self._window, self._area = window, area
            window.realize()
            area.realize()
            native = window_handle(area.get_window())
            if native is None:
                raise OSError("Browser parent window unavailable")
            engine = WebView2Host(
                profile, on_ready=self._browser_ready, on_error=self._browser_failed
            )
            self._engine = engine
            area.connect("size-allocate", lambda *_args: engine.resize())
            engine.start(native)
            if self._show_requested:
                window.show_all()
        except OSError:
            self._close_runtime()
            self._set_phase(
                BrowserPhase.UNAVAILABLE, _("Microsoft Edge WebView2 is unavailable")
            )
        return False

    def _browser_ready(self) -> None:
        engine = self._engine
        if engine is None or not self._started:
            return
        self._ready = True
        engine.on("navigation_starting", self._navigation_starting)
        engine.on("content_loading", lambda _args: self._content_loading())
        engine.on("navigation_completed", self._navigation_completed)
        engine.on("title", lambda _args: self._title_changed())
        engine.on("message", self._message)
        engine.on("permission", self._permission)
        engine.on("new_window", self._new_window)
        engine.on(
            "process_failed",
            lambda _args: self._browser_failed(OSError("Browser process stopped")),
        )
        engine.on("window_close", lambda _args: GLib.idle_add(self._hide_idle))
        engine.on("notification", self._notification)
        engine.on_accelerator(self._accelerator)
        with ExitStack() as scope:
            settings = object_result(scope, engine.core, 3)
            engine.put(settings, 12, False)  # Developer tools.
            engine.put(settings, 16, False)  # Native host-object injection.
        # Install first, then navigate: no first-document badge/status gap.
        script = (
            "if (window === window.top && location.origin === "
            + json.dumps(self.profile.origin.rstrip("/"))
            + ") {\n"
            + self.profile.bridge_script
            + "\n}"
        )
        engine.script(script, lambda _identifier: self._navigate_home())
        engine.show(self._show_requested)

    def _navigate_home(self) -> None:
        if self._engine is not None:
            self._last_bridge_badge = self._last_bridge_status = None
            if self._clear_pending is not None:
                self._engine.navigate("about:blank")
            else:
                self._engine.navigate(self.profile.url)

    def _schedule_fit(self) -> bool:
        if not self._fit_source and self._started:
            self._fit_source = GLib.idle_add(self._fit_window)
        return False

    def _fit_window(self) -> bool:
        self._fit_source = 0
        window = self._window
        if not self._started or window is None or not window.get_visible():
            return False
        gdk_window = window.get_window()
        if gdk_window is None:
            return False
        display = window.get_display()
        monitor = display.get_monitor_at_window(gdk_window)
        hwnd = window_handle(gdk_window)
        if monitor is None or hwnd is None:
            return False
        bounds = monitor.get_geometry()
        snapshot = MonitorSnapshot(
            index=next(
                (
                    i
                    for i in range(display.get_n_monitors())
                    if display.get_monitor(i) == monitor
                ),
                -1,
            ),
            geometry=Rect(bounds.x, bounds.y, bounds.width, bounds.height),
            scale=monitor.get_scale_factor(),
        )
        mapping = WindowsMonitorGeometry().mapping(snapshot, display)
        constrain_window(hwnd, mapping.workarea)
        return False

    def show(self) -> None:
        self._show_requested = True
        self.start()
        if self._window is not None:
            self._window.show_all()
            self._window.present()
            self._schedule_fit()
            if self._engine is not None:
                self._engine.resize()
                self._engine.show(True)
        self._on_attention_cleared()

    def hide(self) -> None:
        self._show_requested = False
        if self._engine is not None:
            self._engine.show(False)
        if self._window is not None:
            self._window.hide()

    def _hide_idle(self) -> bool:
        self.hide()
        return False

    def _delete(self, _window, _event) -> bool:
        self.hide()
        return True

    def _attention(self) -> bool:
        self._on_attention_cleared()
        return False

    def reload(self) -> None:
        if self._engine is not None and self._ready and not self._clearing:
            self._set_phase(BrowserPhase.STARTING)
            self._engine.reload()
        else:
            self.start()

    def _navigation_starting(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        uri = engine.text(args, 3)
        target = navigation_target(uri, self.profile.host)
        if target is not NavigationTarget.INTERNAL:
            self._blocked_navigations.add(int(value(args, 9, ctypes.c_uint64).value))
            if len(self._blocked_navigations) > 128:
                self._blocked_navigations.clear()
            engine.put(args, 8, True)
            if (
                target is NavigationTarget.EXTERNAL
                and value(args, 4, ctypes.c_int32).value
            ):
                GLib.idle_add(self._open_external, uri)
            return

    def _content_loading(self) -> None:
        self._last_bridge_badge = self._last_bridge_status = None
        self._set_phase(BrowserPhase.STARTING)

    def _open_external(self, uri: str) -> bool:
        self._open_external_uri(uri)
        return False

    def _new_window(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        engine.put(args, 6, True)
        uri = engine.text(args, 3)
        if (
            navigation_target(uri, self.profile.host) is NavigationTarget.EXTERNAL
            and value(args, 8, ctypes.c_int32).value
        ):
            GLib.idle_add(self._open_external, uri)

    def _navigation_completed(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        identifier = int(value(args, 5, ctypes.c_uint64).value)
        if identifier in self._blocked_navigations:
            self._blocked_navigations.discard(identifier)
            return
        if not value(args, 3, ctypes.c_int32).value:
            if value(args, 4, ctypes.c_int32).value == 14:  # Operation canceled.
                return
            self._set_phase(
                BrowserPhase.ERROR,
                _("could not load {service}").format(service=self.profile.name),
            )
            return
        if (
            self._clear_pending is not None
            and engine.text(engine.core, 4) == "about:blank"
        ):
            if not self._clearing:
                self._clearing = True
                engine.clear(self._cleared)
            return
        if self._last_bridge_status is None:
            self._set_phase(BrowserPhase.SYNCING)
        self._title_changed()

    def _title_changed(self) -> None:
        if self._engine is not None:
            self._on_title(self._engine.text(self._engine.core, 48))

    def _accelerator(self, args) -> None:
        if (
            self._engine is not None
            and value(args, 3, ctypes.c_int32).value in (0, 2)
            and value(args, 4, ctypes.c_uint32).value == 0x1B
        ):
            self._engine.put(args, 8, True)
            GLib.idle_add(self._hide_idle)

    def _message(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        try:
            if not self.profile.allows_origin(engine.text(args, 3)):
                return
            raw = engine.text(args, 5, limit=4096)
        except OSError:
            log.debug("Rejected unreadable or oversized web-app page message")
            return
        event = parse_bridge_message(raw)
        if event is not None:
            self._apply_bridge_event(event)

    def _notification(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        if not self.profile.allows_origin(engine.text(args, 3)):
            engine.put(args, 5, True)
            return
        # WebView2's native UI owns notification lifetime and DOM click/close
        # delivery. The shared applet records activity while the view is hidden.
        self._on_notification()

    def _activate_notification(self) -> None:
        lifetime = self._lifetime

        def activate() -> bool:
            if self._started and lifetime == self._lifetime:
                self.show()
            return False

        # Leave WebView2's event stack before changing native focus/visibility.
        GLib.idle_add(activate)

    def _permission(self, args) -> None:
        engine = self._engine
        if engine is None:
            return
        engine.put(args, 7, 2)  # Deny until this exact origin/kind is accepted.
        if not self.profile.allows_origin(engine.text(args, 3)):
            return
        kind = int(value(args, 4, ctypes.c_int32).value)
        if kind == 4:  # Notifications: this applet explicitly owns this service.
            engine.put(args, 7, 1)
        elif kind in (1, 2) and self.visible:
            self._ask_media(args, kind)

    def _ask_media(self, args, kind: int) -> None:
        engine = self._engine
        if engine is None:
            return
        scope = ExitStack()
        method(args, 1, ctypes.c_uint32)(args)
        scope.callback(release, args)
        try:
            deferral = object_result(scope, args, 8)
        except OSError:
            scope.close()
            raise
        lifetime = self._lifetime
        token = id(scope)
        completed = False
        dialog: Gtk.MessageDialog | None = None

        def finish(allowed: bool) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            self._permission_requests.pop(token, None)
            self._permission_idles.pop(token, None)
            try:
                engine.put(args, 7, 1 if allowed else 2)
            finally:
                try:
                    check(method(deferral, 3, HRESULT)(deferral))
                finally:
                    scope.close()
                    if dialog is not None:
                        self._permission_dialogs.remove(dialog)
                        dialog.destroy()

        def ask() -> bool:
            nonlocal dialog
            self._permission_idles.pop(token, None)
            if not self._started or lifetime != self._lifetime:
                finish(False)
                return False
            device = _("microphone") if kind == 1 else _("camera")
            dialog = Gtk.MessageDialog(
                transient_for=self._window,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=_("Allow {service} to use your {device}?").format(
                    service=self.profile.name, device=device
                ),
            )
            self._permission_dialogs.append(dialog)
            dialog.add_button(_("Deny"), Gtk.ResponseType.CANCEL)
            dialog.add_button(_("Allow"), Gtk.ResponseType.OK)

            dialog.connect(
                "response",
                lambda _dialog, response: finish(
                    self._started
                    and lifetime == self._lifetime
                    and response == Gtk.ResponseType.OK
                ),
            )
            dialog.show_all()
            return False

        self._permission_requests[token] = lambda: finish(False)
        self._permission_idles[token] = GLib.idle_add(ask)

    def clear_session(self, on_complete: ClearCallback) -> None:
        if self._clear_pending is not None:
            return
        self._clear_pending = on_complete
        self._set_phase(BrowserPhase.STARTING)
        self.start()
        if self._engine is not None and self._ready:
            self._engine.navigate("about:blank")

    def _cleared(self, status: int) -> None:
        callback, self._clear_pending = self._clear_pending, None
        self._clearing = False
        if status < 0:
            error = _("Could not disconnect {service}").format(
                service=self.profile.name
            )
            self._set_phase(BrowserPhase.ERROR, error)
            if callback is not None:
                callback(False, error)
            return
        self._on_attention_cleared()
        self._on_title("")
        self._set_phase(BrowserPhase.STARTING)
        self._navigate_home()
        if callback is not None:
            callback(True, "")

    def _browser_failed(self, _error: Exception) -> None:
        self._set_phase(BrowserPhase.ERROR, _("browser process stopped"))
        if self._started and not self._recovery_id:
            self._recovery_id = GLib.timeout_add(1500, self._recover)

    def _recover(self) -> bool:
        self._recovery_id = 0
        if self._started:
            self._lifetime += 1
            self._close_runtime()
            self.start()
        return False

    def _close_runtime(self) -> None:
        if self._fit_source:
            GLib.source_remove(self._fit_source)
            self._fit_source = 0
        for source in tuple(self._permission_idles.values()):
            GLib.source_remove(source)
        self._permission_idles.clear()
        for deny in tuple(self._permission_requests.values()):
            try:
                deny()
            except OSError:
                log.debug("Could not complete a closed browser permission request")
        try:
            if self._engine is not None:
                self._engine.close()
        finally:
            self._engine = None
            self._ready = False
            self._clearing = False
            self._blocked_navigations.clear()
            if self._window is not None:
                self._window.destroy()
                self._window = None
            self._area = None

    def stop(self) -> None:
        self._started = False
        self._show_requested = False
        self._lifetime += 1
        self._preparing = False
        self._clear_pending = None
        if self._recovery_id:
            GLib.source_remove(self._recovery_id)
            self._recovery_id = 0
        self._close_runtime()
        self._set_phase(BrowserPhase.STOPPED)
