"""Native notification items and callbacks behind the shared tray interface."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from typing import Protocol

from docking.i18n import _
from docking.log import get_logger
from docking.platform.applications.windows_process import WindowsProcessBackend
from docking.platform.backends.base import Rect
from docking.platform.tray.state import TrayIconPixmap, TrayItem, TrayState
from docking.platform.tray.windows_host import WindowsTrayHost
from docking.platform.tray.windows_messages import (
    IconQuery,
    NotifyMessage,
    callback_parameters,
)

log = get_logger("tray.windows")


class TrayAPI(Protocol):
    def start(
        self,
        on_message: Callable[[NotifyMessage], bool],
        icon_bounds: Callable[[IconQuery], Rect | None] | None = None,
    ) -> None: ...
    def owner(self, window: int) -> int: ...
    def pixels(self, icon: int) -> TrayIconPixmap | None: ...
    def point(self) -> tuple[int, int]: ...
    def post(self, window: int, message: int, first: int, second: int) -> bool: ...
    def authorize(self, pid: int) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class _Item:
    window: int
    uid: int
    owner: int
    callback: int = 0
    version: int = 0
    tip: str = ""
    state: int = 0
    pixels: TrayIconPixmap | None = None


class WindowsTrayBackend:
    def __init__(self, *, api: TrayAPI | None = None) -> None:
        self._api = api if api is not None else WindowsTrayHost()
        self._items: dict[str, _Item] = {}
        self._anchors: dict[str, Rect] = {}
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._processes = WindowsProcessBackend()

    def _on_message(self, message: NotifyMessage) -> bool:
        with self._lock:
            if self._stopped.is_set():
                return False
            key = message.identifier
            if message.operation == 2:
                self._anchors.pop(key, None)
                return self._items.pop(key, None) is not None
            current = self._items.get(key)
            if message.operation == 4:
                if current is None or message.version not in (0, 3, 4):
                    return False
                self._items[key] = replace(current, version=message.version)
                return True
            if message.operation == 3:
                return current is not None
            # GUID-only modifications omit hWnd/uID. Keep the registered
            # callback identity and still validate its current process owner.
            window = message.window or (
                current.window
                if message.operation == 1 and message.guid and current is not None
                else 0
            )
            uid = (
                current.uid
                if message.guid and current is not None and message.operation == 1
                else message.uid
            )
            owner = self._api.owner(window)
            if not owner:
                return False
            if message.operation == 0:
                if (
                    current is None
                    or current.owner != owner
                    or current.window != message.window
                ):
                    self._anchors.pop(key, None)
                if key not in self._items and len(self._items) >= 1024:
                    return False
                current = _Item(message.window, message.uid, owner)
            if current is None or current.owner != owner:
                return False
            # Copy pixels during the synchronous Shell_NotifyIcon call. The
            # sender can destroy its borrowed HICON immediately after return.
            self._items[key] = replace(
                current,
                window=window,
                uid=uid,
                callback=message.callback if message.flags & 1 else current.callback,
                pixels=self._api.pixels(message.icon)
                if message.flags & 2
                else current.pixels,
                tip=message.tip if message.flags & 4 else current.tip,
                state=(current.state & ~message.state_mask)
                | (message.state & message.state_mask)
                if message.flags & 8
                else current.state,
            )
            return True

    def get_state(self) -> TrayState:
        if self._stopped.is_set():
            return TrayState(False, "unavailable", ())
        try:
            self._api.start(self._on_message, self._icon_bounds)
            with self._lock:
                items = []
                for key, item in tuple(self._items.items()):
                    if self._api.owner(item.window) != item.owner:
                        self._items.pop(key, None)
                        self._anchors.pop(key, None)
                        continue
                    if item.state & 1:
                        continue  # NIS_HIDDEN is an app request, not overflow.
                    executable = self._processes.executable(item.owner)
                    name = (
                        PureWindowsPath(str(executable)).stem
                        if executable
                        else _("Unknown")
                    )
                    lines = item.tip.splitlines()
                    title = lines[0] if lines else name
                    callback = 0x400 <= item.callback <= 0xFFFF
                    items.append(
                        TrayItem(
                            identifier=key,
                            title=title,
                            item_id=name.casefold(),
                            icon_pixmap=item.pixels,
                            tooltip_title=title,
                            tooltip_text="\n".join(lines[1:]),
                            can_activate=callback,
                            can_context_menu=callback,
                            can_double_click=callback,
                        )
                    )
                return TrayState(True, "host", tuple(items))
        except OSError as exc:
            log.debug("Windows notification inventory failed: %s", exc)
            return TrayState(False, "unavailable", (), _("System Tray unavailable"))

    def set_geometry(self, bounds: Mapping[str, Rect]) -> None:
        with self._lock:
            if not self._stopped.is_set():
                self._anchors = {
                    key: rect
                    for key, rect in bounds.items()
                    if key in self._items and rect.width > 0 and rect.height > 0
                }

    def _icon_bounds(self, query: IconQuery) -> Rect | None:
        with self._lock:
            item = self._items.get(query.identifier)
            if (
                self._stopped.is_set()
                or item is None
                or item.state & 1
                or self._api.owner(item.window) != item.owner
            ):
                return None
            return self._anchors.get(query.identifier)

    def _send(
        self,
        identifier: str,
        *,
        context: bool,
        double: bool = False,
        anchor: Rect | None = None,
    ) -> bool:
        with self._lock:
            item = self._items.get(identifier)
            if self._stopped.is_set() or item is None or item.state & 1:
                return False
            if (
                not 0x400 <= item.callback <= 0xFFFF
                or self._api.owner(item.window) != item.owner
            ):
                return False
            if anchor is not None and anchor.width > 0 and anchor.height > 0:
                # Retain the initiating icon after its GTK popup hides. The
                # provider may query its bounds asynchronously after PostMessage.
                self._anchors[identifier] = anchor
                x, y = anchor.x + anchor.width // 2, anchor.bottom
            else:
                x, y = self._api.point()
            self._api.authorize(item.owner)
            if double:
                events = (0x203,)
            elif context:
                events = (0x7B,) if item.version >= 3 else (0x204, 0x205)
            else:
                events = (0x400,) if item.version >= 3 else (0x201, 0x202)
            for event in events:
                first, second = callback_parameters(
                    version=item.version, uid=item.uid, event=event, x=x, y=y
                )
                if not self._api.post(item.window, item.callback, first, second):
                    return False
            return True

    def activate(self, identifier: str, *, anchor: Rect | None = None) -> bool:
        return self._send(identifier, context=False, anchor=anchor)

    def context_menu(self, identifier: str, *, anchor: Rect | None = None) -> bool:
        return self._send(identifier, context=True, anchor=anchor)

    def double_click(self, identifier: str, *, anchor: Rect | None = None) -> bool:
        return self._send(identifier, context=False, double=True, anchor=anchor)

    def menu_client(self, identifier: str) -> None:
        # Windows apps own their native context menus; the callback opens them.
        return None

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._api.close()
        with self._lock:
            self._items.clear()
            self._anchors.clear()
