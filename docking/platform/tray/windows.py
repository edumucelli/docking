"""Native notification items and callbacks behind the shared tray interface."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from typing import Protocol

from docking.i18n import _
from docking.log import get_logger
from docking.platform.applications.windows.process import WindowsProcessBackend
from docking.platform.backends.base import Rect
from docking.platform.tray.state import (
    TrayFocusRequest,
    TrayIconPixmap,
    TrayItem,
    TrayState,
)
from docking.platform.tray.windows_host import WindowsTrayHost
from docking.platform.tray.windows_messages import (
    IconQuery,
    NotifyMessage,
    callback_parameters,
)

log = get_logger("tray.windows")

# NIM_SETFOCUS carries no interaction token. Consume immediate repeats of our
# completed interaction, then let later Explorer interactions use Explorer.
_FOCUS_RETURN_GRACE_S = 1.0
_FOCUS_INTERACTION_MAX_S = 300.0
_HOVER_WATCH_S = 0.1


class TrayAPI(Protocol):
    def start(
        self,
        on_message: Callable[[NotifyMessage], bool],
        icon_bounds: Callable[[IconQuery], Rect | None] | None = None,
        *,
        shell_changed: Callable[[], None] | None = None,
    ) -> None: ...
    def owner(self, window: int) -> int: ...
    def pixels(self, icon: int) -> TrayIconPixmap | None: ...
    def point(self) -> tuple[int, int]: ...
    def post(self, window: int, message: int, first: int, second: int) -> bool: ...
    def authorize(self, pid: int) -> None: ...
    def foreground(self) -> tuple[int, int] | None: ...
    def hover_settings(self) -> tuple[int, int, int]: ...
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
    show_tip: bool = False
    generation: int = 0


@dataclass(frozen=True)
class _Hover:
    identifier: str
    generation: int
    point: tuple[int, int]
    origin: tuple[int, int]
    settings: tuple[int, int, int]
    opened: bool = False


@dataclass(frozen=True)
class _Action:
    identifier: str
    generation: int
    serial: int
    origin: tuple[int, int] | None


@dataclass(frozen=True)
class _LocalFocus:
    window: int
    owner: int
    expires: float
    active: bool = True


class WindowsTrayBackend:
    def __init__(
        self,
        *,
        api: TrayAPI | None = None,
        timer_factory: Callable[
            [float, Callable[[], None]], threading.Timer
        ] = threading.Timer,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api if api is not None else WindowsTrayHost()
        self._items: dict[str, _Item] = {}
        self._anchors: dict[str, Rect] = {}
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._processes = WindowsProcessBackend()
        self._generation = 0
        self._hover: _Hover | None = None
        self._timer_factory = timer_factory
        self._clock = clock
        self._hover_timer: threading.Timer | None = None
        self._hover_watch: threading.Timer | None = None
        self._hover_serial = 0
        self._focus_handler: Callable[[TrayFocusRequest], None] | None = None
        self._focus_epoch = 0
        self._action_serial = 0
        self._pending_action: _Action | None = None
        self._local_focus: dict[str, _LocalFocus] = {}

    def _next_generation(self) -> int:
        self._generation += 1
        return self._generation

    def _live(self, item: _Item) -> bool:
        return not self._stopped.is_set() and self._api.owner(item.window) == item.owner

    def _post_event(self, item: _Item, event: int, point: tuple[int, int]) -> bool:
        if not self._live(item) or not 0x400 <= item.callback <= 0xFFFF:
            return False
        first, second = callback_parameters(
            version=item.version, uid=item.uid, event=event, x=point[0], y=point[1]
        )
        return self._api.post(item.window, item.callback, first, second)

    def _end_hover(self) -> None:
        self._hover_serial += 1
        if self._hover_timer is not None:
            self._hover_timer.cancel()
            self._hover_timer = None
        if self._hover_watch is not None:
            self._hover_watch.cancel()
            self._hover_watch = None
        hover, self._hover = self._hover, None
        if hover is not None and hover.opened:
            item = self._items.get(hover.identifier)
            if item is not None and item.generation == hover.generation:
                self._post_event(item, 0x407, hover.point)  # NIN_POPUPCLOSE.

    def _invalidate_item(self, identifier: str) -> None:
        if self._hover is not None and self._hover.identifier == identifier:
            self._end_hover()
        if (
            self._pending_action is not None
            and self._pending_action.identifier == identifier
        ):
            self._pending_action = None
        self._finish_local_focus(identifier)

    def _finish_local_focus(self, identifier: str) -> None:
        local = self._local_focus.get(identifier)
        if local is None:
            return
        now = self._clock()
        if local.expires <= now:
            self._local_focus.pop(identifier)
        elif local.active:
            self._local_focus[identifier] = replace(
                local, expires=now + _FOCUS_RETURN_GRACE_S, active=False
            )

    def _finish_action(self) -> None:
        action, self._pending_action = self._pending_action, None
        if action is not None:
            self._finish_local_focus(action.identifier)

    def _shell_changed(self) -> None:
        with self._lock:
            self._end_hover()
            self._finish_action()
            self._focus_epoch += 1

    def _on_message(self, message: NotifyMessage) -> bool:
        if message.operation == 3:
            return self._return_focus(message)
        with self._lock:
            if self._stopped.is_set():
                return False
            key = message.identifier
            if message.operation == 2:
                self._invalidate_item(key)
                self._anchors.pop(key, None)
                return self._items.pop(key, None) is not None
            current = self._items.get(key)
            if message.operation == 4:
                if (
                    current is None
                    or not self._live(current)
                    or message.version not in (0, 3, 4)
                    or message.window not in (0, current.window)
                ):
                    return False
                if current.version != message.version:
                    self._invalidate_item(key)
                    self._items[key] = replace(
                        current,
                        version=message.version,
                        generation=self._next_generation(),
                    )
                return True
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
                # Explorer rejects another live callback window claiming the
                # same GUID, even for a second process of the same executable.
                # Same-window reseeding after TaskbarCreated remains accepted.
                if (
                    current is not None
                    and self._live(current)
                    and (current.owner != owner or current.window != message.window)
                ):
                    return False
                self._invalidate_item(key)
                if (
                    current is None
                    or current.owner != owner
                    or current.window != message.window
                ):
                    self._anchors.pop(key, None)
                if key not in self._items and len(self._items) >= 1024:
                    return False
                local = self._local_focus.get(key)
                if local is not None and (local.window, local.owner) != (
                    message.window,
                    owner,
                ):
                    self._local_focus.pop(key)
                current = _Item(
                    message.window,
                    message.uid,
                    owner,
                    generation=self._next_generation(),
                )
            if current is None or current.owner != owner:
                return False
            # Copy pixels during the synchronous Shell_NotifyIcon call. The
            # sender can destroy its borrowed HICON immediately after return.
            updated = replace(
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
                show_tip=bool(message.flags & 0x80),
            )
            if (updated.window, updated.uid, updated.callback, updated.state & 1) != (
                current.window,
                current.uid,
                current.callback,
                current.state & 1,
            ):
                self._invalidate_item(key)
                updated = replace(updated, generation=self._next_generation())
            elif (
                updated.show_tip != current.show_tip
                and self._hover is not None
                and self._hover.identifier == key
            ):
                self._end_hover()
            self._items[key] = updated
            return True

    def get_state(self) -> TrayState:
        if self._stopped.is_set():
            return TrayState(False, "unavailable", ())
        try:
            self._api.start(
                self._on_message, self._icon_bounds, shell_changed=self._shell_changed
            )
            with self._lock:
                items = []
                for key, item in tuple(self._items.items()):
                    if self._api.owner(item.window) != item.owner:
                        self._invalidate_item(key)
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
                            tooltip_title=lines[0] if lines else "",
                            tooltip_text="\n".join(lines[1:]),
                            can_activate=callback,
                            can_context_menu=callback,
                            can_double_click=callback,
                            show_tooltip=bool(item.tip)
                            and (item.version < 4 or item.show_tip),
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
                if (
                    self._hover is not None
                    and self._hover.identifier not in self._anchors
                ):
                    self._end_hover()

    def _schedule_hover(self) -> None:
        hover = self._hover
        if hover is None:
            return
        self._hover_serial += 1
        serial = self._hover_serial
        if self._hover_timer is not None:
            self._hover_timer.cancel()

        def opened() -> None:
            with self._lock:
                current = self._hover
                if serial != self._hover_serial or current is None:
                    return
                self._hover_timer = None
                item = self._items.get(current.identifier)
                if (
                    item is None
                    or item.generation != current.generation
                    or item.state & 1
                    or item.version != 4
                    or item.show_tip
                    or not self._cursor_in_hover(current)
                ):
                    self._end_hover()
                    return
                if self._post_event(item, 0x406, current.point):  # NIN_POPUPOPEN.
                    self._hover = replace(current, opened=True)
                    self._watch_hover()
                else:
                    self._end_hover()

        timer = self._timer_factory(hover.settings[0] / 1000, opened)
        timer.daemon = True
        self._hover_timer = timer
        timer.start()

    def _cursor_in_hover(self, hover: _Hover) -> bool:
        anchor = self._anchors.get(hover.identifier)
        if anchor is None:
            # Callers without published native geometry still terminate hover
            # explicitly through pointer(None); no rectangle can be invented.
            return True
        try:
            x, y = self._api.point()
        except OSError as error:
            log.debug("Could not sample notification hover pointer: %s", error)
            return False
        return anchor.x <= x < anchor.right and anchor.y <= y < anchor.bottom

    def _watch_hover(self) -> None:
        hover = self._hover
        if (
            hover is None
            or not hover.opened
            or self._hover_watch is not None
            or hover.identifier not in self._anchors
        ):
            return
        serial = self._hover_serial

        def checked() -> None:
            with self._lock:
                current = self._hover
                if serial != self._hover_serial or current is None:
                    return
                self._hover_watch = None
                item = self._items.get(current.identifier)
                if (
                    item is None
                    or item.generation != current.generation
                    or not self._live(item)
                    or not self._cursor_in_hover(current)
                ):
                    self._end_hover()
                    return
                self._watch_hover()

        # Win32 can omit a GTK child crossing when the real cursor moves
        # straight to another process. A scoped native watch pairs CLOSE with
        # OPEN even when no further GTK motion/leave callback reaches us.
        timer = self._timer_factory(_HOVER_WATCH_S, checked)
        timer.daemon = True
        self._hover_watch = timer
        timer.start()

    def pointer(
        self,
        identifier: str | None,
        *,
        point: tuple[int, int] | None = None,
        anchor: Rect | None = None,
    ) -> bool:
        with self._lock:
            if self._stopped.is_set():
                return False
            item = self._items.get(identifier) if identifier is not None else None
            if identifier is None:
                self._end_hover()
                return True
            if item is None or item.state & 1 or not self._live(item):
                self._end_hover()
                return False
            if anchor is not None and anchor.width > 0 and anchor.height > 0:
                self._anchors[identifier] = anchor
            position = point if point is not None else self._api.point()
            hover = self._hover
            if hover is None or (hover.identifier, hover.generation) != (
                identifier,
                item.generation,
            ):
                self._end_hover()
                settings = self._api.hover_settings()
                hover = _Hover(
                    identifier, item.generation, position, position, settings
                )
                self._hover = hover
            if not self._post_event(item, 0x200, position):  # WM_MOUSEMOVE.
                self._end_hover()
                return False
            outside = abs(position[0] - hover.origin[0]) * 2 > max(
                1, hover.settings[1]
            ) or abs(position[1] - hover.origin[1]) * 2 > max(1, hover.settings[2])
            self._hover = replace(
                hover, point=position, origin=position if outside else hover.origin
            )
            if (
                item.version == 4
                and not item.show_tip
                and not hover.opened
                and (self._hover_timer is None or outside)
            ):
                self._schedule_hover()
            return True

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
        keyboard: bool = False,
    ) -> bool:
        with self._lock:
            self._end_hover()
            self._action_serial += 1
            self._finish_action()
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
                x, y = (
                    (anchor.x, anchor.y)
                    if keyboard
                    else (anchor.x + anchor.width // 2, anchor.bottom)
                )
            else:
                x, y = self._api.point()
            self._api.authorize(item.owner)
            if self._focus_handler is not None:
                self._pending_action = _Action(
                    identifier,
                    item.generation,
                    self._action_serial,
                    self._api.foreground(),
                )
            if double:
                events = (0x203,)
            elif context:
                events = (0x7B,) if item.version >= 3 else (0x204, 0x205)
            else:
                events = (
                    ((0x401 if keyboard else 0x400),)
                    if item.version >= 3
                    else ((0x204, 0x205) if keyboard else (0x201, 0x202))
                )
            for event in events:
                first, second = callback_parameters(
                    version=item.version, uid=item.uid, event=event, x=x, y=y
                )
                if not self._api.post(item.window, item.callback, first, second):
                    self._finish_action()
                    return False
                if self._focus_handler is not None:
                    self._remember_local_focus(identifier, item)
            return True

    def activate(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool:
        return self._send(identifier, context=False, anchor=anchor, keyboard=keyboard)

    def context_menu(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool:
        return self._send(identifier, context=True, anchor=anchor, keyboard=keyboard)

    def double_click(self, identifier: str, *, anchor: Rect | None = None) -> bool:
        return self._send(identifier, context=False, double=True, anchor=anchor)

    def menu_client(self, identifier: str) -> None:
        # Windows apps own their native context menus; the callback opens them.
        return None

    def set_focus_handler(
        self, handler: Callable[[TrayFocusRequest], None] | None
    ) -> None:
        with self._lock:
            self._focus_handler = handler
            self._focus_epoch += 1
            self._finish_action()

    def _remember_local_focus(self, identifier: str, item: _Item) -> None:
        if identifier not in self._local_focus and len(self._local_focus) >= 1024:
            for key, local in tuple(self._local_focus.items()):
                if (
                    key not in self._items
                    or self._api.owner(local.window) != local.owner
                    or local.expires <= self._clock()
                ):
                    self._local_focus.pop(key)
            if len(self._local_focus) >= 1024:
                self._local_focus.pop(next(iter(self._local_focus)))
        self._local_focus[identifier] = _LocalFocus(
            item.window, item.owner, self._clock() + _FOCUS_INTERACTION_MAX_S
        )

    def _return_focus(self, message: NotifyMessage) -> bool:
        identifier = message.identifier
        with self._lock:
            local = self._local_focus.get(identifier)
            if local is None or message.window not in (0, local.window):
                return False
            if local.expires <= self._clock():
                self._local_focus.pop(identifier)
                if (
                    self._pending_action is not None
                    and self._pending_action.identifier == identifier
                ):
                    self._pending_action = None
                return False
            action, handler = self._pending_action, self._focus_handler
            item = self._items.get(identifier)
            if (
                action is None
                or action.identifier != identifier
                or handler is None
                or item is None
                or item.generation != action.generation
                or item.state & 1
                or not self._live(item)
            ):
                # Only the bounded duplicate grace belongs to Docking once its
                # interaction ends. Identical future packets cannot identify
                # whether a later interaction originated in Explorer.
                self._finish_local_focus(identifier)
                return True
            # Detach immediately: another NIM_SETFOCUS cannot queue a duplicate
            # while GTK is still waiting to consume this handoff.
            self._finish_action()
            foreground = self._api.foreground()
            if foreground is None or not (
                foreground[1] == item.owner
                or (foreground == action.origin and foreground[1] == os.getpid())
            ):
                return True
            epoch, used = self._focus_epoch, False

            def validate() -> bool:
                with self._lock:
                    current = self._items.get(identifier)
                    return (
                        epoch == self._focus_epoch
                        and self._focus_handler is handler
                        and action.serial == self._action_serial
                        and current is not None
                        and current.generation == action.generation
                        and not current.state & 1
                        and self._live(current)
                        and self._api.foreground() == foreground
                    )

            def claim() -> bool:
                nonlocal used
                with self._lock:
                    if used:
                        return False
                    used = True
                    return validate()

            request = TrayFocusRequest(identifier, claim, validate)
        handler(request)
        return True

    def close(self) -> None:
        with self._lock:
            if self._stopped.is_set():
                return
            self._end_hover()
            self._pending_action = None
            self._focus_handler = None
            self._focus_epoch += 1
            self._stopped.set()
            self._items.clear()
            self._anchors.clear()
        self._api.close()
        with self._lock:
            self._local_focus.clear()
