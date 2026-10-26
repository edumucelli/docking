"""STA browser notifications backed by worker-owned native Windows toasts."""

from __future__ import annotations

import ctypes
import uuid
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.applets.worker import SerialWorker
from docking.log import get_logger
from docking.platform.backends.windows.com import HRESULT, method, object_result, value
from docking.platform.backends.windows.com_callback import ComCallback
from docking.platform.backends.windows.toasts import ToastPublisher
from docking.platform.backends.windows.webview import WebView2Host
from docking.platform.backends.windows.winrt import check

from .notification_backend import NotificationBackend

log = get_logger("webapp.windows_notifications")


@dataclass
class _Notification:
    scope: ExitStack
    pointer: ctypes.c_void_p
    tag: str
    deferral: tuple[ExitStack, ctypes.c_void_p] | None = None
    superseded: list[_Notification] = field(default_factory=list)

    def complete(self) -> None:
        deferral, self.deferral = self.deferral, None
        if deferral is not None:
            scope, pointer = deferral
            try:
                check(method(pointer, 3, HRESULT)(pointer))
            finally:
                scope.close()

    def close(self) -> None:
        # Keep this flat: a busy conversation can replace a tag thousands
        # of times before it closes, exceeding Python's recursion limit.
        with ExitStack() as cleanup:
            for notification in self.superseded:
                cleanup.callback(notification.scope.close)
            self.superseded.clear()
            self.scope.close()


class DesktopNotifier(NotificationBackend):
    def __init__(
        self,
        *,
        on_activate: Callable[[], None],
        service_name: str,
        profile_directory: Path,
        on_shown: Callable[[], None],
    ) -> None:
        self._on_activate, self._on_shown = on_activate, on_shown
        self._publisher = ToastPublisher(
            service_name, str(profile_directory), self._native_event
        )
        self._worker = SerialWorker(on_close=self._publisher.close)
        self._notifications: dict[str, _Notification] = {}
        self._closing: dict[str, tuple[_Notification, bool]] = {}
        self._stopped = False

    def show(self, notification: tuple[WebView2Host, ctypes.c_void_p]) -> bool:
        if self._stopped:
            return False
        engine, args = notification
        engine.put(args, 5, True)
        scope = ExitStack()
        token = uuid.uuid4().hex
        replaces = None
        try:
            pointer = object_result(scope, args, 4)
            deferral_scope = ExitStack()
            scope.callback(deferral_scope.close)
            deferral = object_result(deferral_scope, args, 7)
            active = _Notification(scope, pointer, "", (deferral_scope, deferral))
            scope.callback(active.complete)
            title = engine.text(pointer, 13, limit=4096)
            body = engine.text(pointer, 8, limit=16384)
            tag = engine.text(pointer, 11, limit=4096)
            silent = bool(value(pointer, 18, ctypes.c_int32).value)
            if tag:
                for previous, existing in tuple(self._notifications.items()):
                    if existing.tag == tag:
                        replaces = previous
                        break
            handler = ComCallback(
                "47c32d23-1e94-4733-85f1-d9bf4acd0974",
                (ctypes.c_void_p, ctypes.c_void_p),
                lambda *_args: self._native_event("web-closed", token),
                on_error=lambda _error: self._native_event("closed", token),
                agile=True,
            )
            scope.callback(handler.close)
            registration = ctypes.c_int64()
            check(
                method(
                    pointer, 3, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64)
                )(pointer, handler.pointer, ctypes.byref(registration))
            )

            def remove() -> None:
                with suppress(OSError):
                    check(
                        method(pointer, 4, HRESULT, ctypes.c_int64)(
                            pointer, registration.value
                        )
                    )

            scope.callback(remove)
        except OSError:
            scope.close()
            log.debug("Could not prepare a native web-app notification")
            return False
        active.tag = tag
        if replaces is not None:
            previous = self._notifications.pop(replaces)
            with suppress(OSError):
                previous.complete()
            # WebView2 155 loses CloseRequested for the current notification
            # when ANY superseded same-tag native object is destroyed. Keep
            # the whole chain alive until this tag closes. The runtime already
            # closes the superseded DOM objects; ReportClosed would close the
            # new notification, which shares its native routing identifier.
            active.superseded = previous.superseded
            previous.superseded = []
            active.superseded.append(previous)
        if len(self._notifications) >= 128:
            self._finish(next(iter(self._notifications)), report=True)
        self._notifications[token] = active
        self._worker.run(
            name="webapp-toast",
            fn=lambda: self._publisher.show(token, title, body, silent, replaces),
            on_result=lambda accepted: self._published(token, accepted),
            on_error=lambda _error: self._published(token, False),
        )
        return True

    def _published(self, token: str, accepted: bool) -> None:
        notification = self._notifications.get(token)
        if notification is None:
            return
        if not accepted:
            self._finish(token, report=True)
            return
        try:
            check(method(notification.pointer, 5, HRESULT)(notification.pointer))
            notification.complete()
        except OSError:
            self._finish(token, report=False)
            return
        self._on_shown()

    def _native_event(self, event: str, token: str) -> None:
        # RPC/WinRT callbacks carry no browser interfaces across apartments.
        GLib.idle_add(self._event, event, token)

    def _event(self, event: str, token: str) -> bool:
        notification = self._notifications.get(token)
        if notification is None or self._stopped:
            return False
        if event == "clicked":
            try:
                check(method(notification.pointer, 6, HRESULT)(notification.pointer))
                self._on_activate()
            except OSError:
                log.debug("Could not report a closed browser notification click")
        self._finish(token, report=event != "web-closed")
        return False

    def _finish(self, token: str, *, report: bool) -> None:
        notification = self._notifications.pop(token, None)
        if notification is None:
            return
        self._closing[token] = (notification, report)
        self._worker.run(
            name="webapp-toast-close",
            fn=lambda: self._publisher.hide(token),
            on_result=lambda _result: self._released(token),
            on_error=lambda _error: self._released(token, report=False),
        )

    def _released(self, token: str, *, report: bool = True) -> None:
        closed = self._closing.pop(token, None)
        if closed is None:
            return
        notification, should_report = closed
        try:
            if should_report and report:
                with suppress(OSError):
                    check(
                        method(notification.pointer, 7, HRESULT)(notification.pointer)
                    )
        finally:
            notification.close()

    def clear(self) -> None:
        for token in tuple(self._notifications):
            self._finish(token, report=True)

    def stop(self) -> None:
        if self._stopped:
            return
        self.clear()
        self._stopped = True
        # Release STA interfaces before the browser controller is destroyed.
        # Late worker replies then find no browser objects to access.
        for token in tuple(self._closing):
            self._released(token)
        self._worker.stop()
