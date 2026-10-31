"""Qt-owned browser objects with Windows toast IO on one native worker."""

from __future__ import annotations

import queue
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtWebEngineCore import QWebEngineNotification

from docking.platform.backends.windows.toasts import ToastPublisher

from ..notification_backend import NotificationBackend
from ..profile import BrowserProfile


@dataclass(frozen=True)
class _Command:
    kind: Literal["show", "hide"]
    token: str
    title: str = ""
    body: str = ""
    replaces: str | None = None


class DesktopNotifier(NotificationBackend):
    def __init__(
        self,
        profile: BrowserProfile,
        directory: Path,
        *,
        post: Callable[[Callable[[], None]], None],
        on_activate: Callable[[], None],
        on_shown: Callable[[], None],
    ) -> None:
        self._profile, self._directory = profile, directory
        self._post, self._on_activate, self._on_shown = post, on_activate, on_shown
        self._notifications: dict[str, QWebEngineNotification] = {}
        self._commands: queue.Queue[_Command | None] = queue.Queue()
        self._pending_slots = threading.BoundedSemaphore(128)
        self._stopped = False
        self._completion: Callable[[], None] = lambda: None
        threading.Thread(
            target=self._serve, name="webapp-native-toasts", daemon=False
        ).start()

    def _serve(self) -> None:
        publisher = ToastPublisher(
            self._profile.name,
            str(self._directory),
            lambda event, token: self._post(lambda: self._event(event, token)),
        )
        try:
            while (command := self._commands.get()) is not None:
                token = command.token
                if command.kind == "show":
                    try:
                        try:
                            accepted = publisher.show(
                                token,
                                command.title,
                                command.body,
                                False,
                                command.replaces,
                            )
                        except OSError:
                            accepted = False
                    finally:
                        self._pending_slots.release()
                    self._post(
                        lambda token=token, accepted=accepted: self._published(
                            token, accepted
                        )
                    )
                elif command.kind == "hide":
                    with suppress(OSError):
                        publisher.hide(token)
        finally:
            try:
                publisher.close()
            finally:
                self._post(self._completion)

    def show(self, notification: QWebEngineNotification) -> bool:
        if (
            self._stopped
            or not self._profile.allows_origin(notification.origin().toString())
            or not self._pending_slots.acquire(blocking=False)
        ):
            notification.close()
            notification.deleteLater()
            return False
        replaces = next(
            (
                token
                for token, existing in self._notifications.items()
                if notification.tag() and notification.matches(existing)
            ),
            None,
        )
        token = uuid.uuid4().hex
        if replaces is not None:
            # Qt releases the superseded object independently; it does not need
            # WebView2's retained same-tag chain to preserve close routing.
            previous = self._notifications.pop(replaces)
            previous.deleteLater()
        if len(self._notifications) >= 128:
            self._finish(next(iter(self._notifications)), report=True)
        self._notifications[token] = notification
        notification.closed.connect(lambda: self._finish(token, report=False))
        # Qt's displayed-notification query synchronizes Chromium's persistent
        # database. Register platform acceptance before the presenter returns;
        # delaying this until async WinRT completion can discard that DB entry.
        notification.show()
        self._commands.put(
            _Command(
                "show",
                token,
                notification.title()[:256],
                notification.message()[:2048],
                replaces,
            )
        )
        return True

    def _published(self, token: str, accepted: bool) -> None:
        if token not in self._notifications or self._stopped:
            return
        if accepted:
            self._on_shown()
        else:
            self._finish(token, report=True)

    def _event(self, event: str, token: str) -> None:
        notification = self._notifications.get(token)
        if notification is None or self._stopped:
            return
        if event == "clicked":
            # The real engine dispatches notificationclick to its worker. A
            # synthetic page event cannot stand in for that persistent route.
            notification.click()
            self._on_activate()
        self._finish(token, report=event != "clicked")

    def _finish(self, token: str, *, report: bool) -> None:
        notification = self._notifications.pop(token, None)
        if notification is None:
            return
        self._commands.put(_Command("hide", token))
        if report:
            notification.close()
        notification.deleteLater()

    def clear(self) -> None:
        for token in tuple(self._notifications):
            self._finish(token, report=True)

    def stop(self, completion: Callable[[], None] | None = None) -> None:
        if self._stopped:
            return
        self.clear()
        self._completion = completion or (lambda: None)
        self._stopped = True
        self._commands.put(None)
