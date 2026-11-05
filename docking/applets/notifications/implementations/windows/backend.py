"""Native notification listener and shell quiet-hours implementation.

The listener uses the public WinRT ABI. Quiet-hours control uses the shell's
private IQuietHoursSettings interface and WNF effective-profile value, validated
on Windows 11 25H2. Unknown profiles and ABI failures disable that capability;
we never infer Do Not Disturb from presentation mode or modify registry blobs.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import replace

from docking.applets.notifications.backend import NotificationsBackend
from docking.applets.notifications.state import (
    HISTORY_LIMIT,
    NotificationEntry,
    NotificationsSnapshot,
    NotificationsState,
)
from docking.log import get_logger
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    object_result,
    release,
    value,
)
from docking.platform.native.windows.ffi import bind
from docking.platform.native.windows.winrt import WindowsRuntime, check

log = get_logger("notifications.windows")
_POINTER = ctypes.c_void_p
_QUIET_CLASS = GUID.parse("f53321fa-34f8-4b7f-b9a3-361877cb94cf")
_QUIET_INTERFACE = GUID.parse("6bff4732-81ec-4ffb-ae67-b6c1bc29631f")
_LISTENER_STATICS = GUID.parse("ff6123cf-4386-4aa3-b73d-b804e5b63b23")
_UNRESTRICTED = "Microsoft.QuietHoursProfile.Unrestricted"
_PRIORITY = "Microsoft.QuietHoursProfile.PriorityOnly"
_ALARMS = "Microsoft.QuietHoursProfile.AlarmsOnly"
_PROFILES = (_UNRESTRICTED, _PRIORITY, _ALARMS)
_MAX_NOTIFICATIONS = 4096


def _revision(rows: tuple[tuple[str, int, NotificationEntry], ...]) -> str:
    payload = [
        (key, created, entry.app_name, entry.summary, entry.body)
        for key, created, entry in sorted(rows)
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


class QuietHours:
    @contextmanager
    def _settings(self):
        with apartment(multithreaded=True) as ole, ExitStack() as scope:
            pointer = _POINTER()
            status = bind(
                ole,
                "CoCreateInstance",
                HRESULT,
                ctypes.POINTER(GUID),
                _POINTER,
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
                ctypes.POINTER(_POINTER),
            )(
                ctypes.byref(_QUIET_CLASS),
                None,
                4,
                ctypes.byref(_QUIET_INTERFACE),
                ctypes.byref(pointer),
            )
            if pointer.value:
                scope.callback(release, pointer)
            check(status)
            if not pointer.value:
                raise OSError("Missing quiet-hours interface")
            yield ole, pointer

    def selected_profile(self) -> str:
        with self._settings() as (ole, pointer):
            result = _POINTER()
            try:
                check(
                    method(pointer, 3, HRESULT, ctypes.POINTER(_POINTER))(
                        pointer, ctypes.byref(result)
                    )
                )
                profile = ctypes.wstring_at(result) if result.value else ""
            finally:
                if result.value:
                    bind(ole, "CoTaskMemFree", None, _POINTER)(result)
        if profile not in _PROFILES:
            raise OSError("Unknown quiet-hours profile")
        return profile

    def effective_profile(self) -> int:
        # WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED. Read the effective value,
        # including automatic rules, rather than equating it to user selection.
        name = ctypes.c_uint64(0x0D83063EA3BF1C75)
        stamp, state, size = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32(4)
        status = bind(
            ctypes.WinDLL("ntdll"),
            "NtQueryWnfStateData",
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_uint64),
            _POINTER,
            _POINTER,
            ctypes.POINTER(ctypes.c_uint32),
            _POINTER,
            ctypes.POINTER(ctypes.c_uint32),
        )(
            ctypes.byref(name),
            None,
            None,
            ctypes.byref(stamp),
            ctypes.byref(state),
            ctypes.byref(size),
        )
        if status < 0 or size.value != 4 or state.value not in (0, 1, 2):
            raise OSError("Effective quiet-hours profile unavailable")
        return state.value

    def set_profile(self, profile: str) -> bool:
        if profile not in _PROFILES:
            return False
        # Validate this implementation before calling an undocumented setter.
        self.selected_profile()
        self.effective_profile()
        with self._settings() as (_ole, pointer):
            check(method(pointer, 4, HRESULT, ctypes.c_wchar_p)(pointer, profile))
        # The shell publishes selection asynchronously. The backend verifies
        # both selection and effective state after this accepted request.
        return True


class NotificationListener:
    """Thread-scoped COM objects; only owned text and IDs leave this reader."""

    @contextmanager
    def _listener(self):
        with WindowsRuntime() as runtime, ExitStack() as scope:
            factory = runtime.factory(
                scope,
                "Windows.UI.Notifications.Management.UserNotificationListener",
                _LISTENER_STATICS,
            )
            listener = object_result(scope, factory, 6)
            yield runtime, scope, listener

    @staticmethod
    def _notification(
        runtime, scope, notification
    ) -> tuple[str, int, NotificationEntry]:
        identifier = int(value(notification, 8, ctypes.c_uint32).value)
        created = int(value(notification, 9, ctypes.c_int64).value)
        try:
            application = object_result(scope, notification, 7)
        except OSError as exc:
            if exc.errno != 0x80004001:  # E_NOTIMPL.
                raise
            # Some unpackaged senders have no IAppInfo. Their toast text and
            # stable notification ID remain readable without an application.
            name = ""
        else:
            # IAppInfo.DisplayInfo -> IAppDisplayInfo.DisplayName.
            display = object_result(scope, application, 8)
            name = runtime.text(display, 6)
        source = object_result(scope, notification, 6)
        visual = object_result(scope, source, 8)
        with runtime.string("ToastGeneric") as template:
            binding = object_result(scope, visual, 9, _POINTER, template)
        text = []
        if binding.value:
            elements = object_result(scope, binding, 11)
            count = value(elements, 7, ctypes.c_uint32).value
            if count > 128:
                raise OSError("Notification text count exceeded its limit")
            for index in range(count):
                element = object_result(scope, elements, 6, ctypes.c_uint32, index)
                text.append(runtime.text(element, 6)[:4096])
        return (
            str(identifier),
            created,
            NotificationEntry(
                name, text[0] if text else "", "\n".join(text[1:])[:16384]
            ),
        )

    def read(
        self, cancelled: threading.Event
    ) -> tuple[int, tuple[tuple[str, int, NotificationEntry], ...]]:
        with self._listener() as (runtime, scope, listener):
            access = int(value(listener, 7, ctypes.c_int32).value)
            if access != 1:
                return access, ()
            operation = object_result(scope, listener, 10, ctypes.c_int32, 1)
            runtime.wait(scope, operation, cancelled)
            notifications = object_result(scope, operation, 8)
            count = value(notifications, 7, ctypes.c_uint32).value
            if count > _MAX_NOTIFICATIONS:
                raise OSError("Notification count exceeded its limit")
            rows = []
            for index in range(count):
                if cancelled.is_set():
                    raise OSError("Notification read cancelled")
                # Release each notification's graph before reading the next.
                with ExitStack() as item_scope:
                    notification = object_result(
                        item_scope, notifications, 6, ctypes.c_uint32, index
                    )
                    rows.append(self._notification(runtime, item_scope, notification))
            # Revocation can silently produce an empty list. Never call it zero
            # pending notifications without checking permission again.
            access = int(value(listener, 7, ctypes.c_int32).value)
            return (access, tuple(rows)) if access == 1 else (access, ())

    def remove(
        self,
        rows: tuple[tuple[str, int, NotificationEntry], ...],
        cancelled: threading.Event,
    ) -> bool:
        with self._listener() as (_runtime, _scope, listener):
            if value(listener, 7, ctypes.c_int32).value != 1:
                return False
            # Remove the displayed IDs individually: a notification arriving
            # after validation must never be included in a global Clear call.
            for identifier, created, _entry in rows:
                if cancelled.is_set() or value(listener, 7, ctypes.c_int32).value != 1:
                    return False
                with ExitStack() as scope:
                    current = object_result(
                        scope, listener, 11, ctypes.c_uint32, int(identifier)
                    )
                    if int(value(current, 9, ctypes.c_int64).value) != created:
                        return False
                    check(
                        method(listener, 13, HRESULT, ctypes.c_uint32)(
                            listener, int(identifier)
                        )
                    )
            return int(value(listener, 7, ctypes.c_int32).value) == 1


class WindowsNotificationsBackend(NotificationsBackend):
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._quiet = QuietHours()
        self._listener = NotificationListener()
        self._seen: dict[str, tuple[int, NotificationEntry]] = {}
        self._resume_profile = _PRIORITY

    def poll(self) -> NotificationsSnapshot:
        if self._cancelled.is_set():
            return NotificationsSnapshot()
        state = NotificationsState(backend="windows")
        try:
            self._quiet.selected_profile()
            effective = self._quiet.effective_profile()
            state = replace(
                state,
                available=True,
                paused=effective != 0,
                paused_known=True,
                can_pause=True,
            )
        except OSError as exc:
            log.debug("Quiet-hours status unavailable: %s", exc)
        events: tuple[NotificationEntry | None, ...] = ()
        try:
            access, rows = self._listener.read(self._cancelled)
            if access == 1:
                # Oldest first lets the shared history insert newest at front.
                ordered = sorted(rows, key=lambda row: (row[1], row[0]))
                events = tuple(
                    entry
                    for key, created, entry in ordered
                    if self._seen.get(key) != (created, entry)
                )[-HISTORY_LIMIT:]
                self._seen = {key: (created, entry) for key, created, entry in rows}
                state = replace(
                    state,
                    available=True,
                    pending=len(rows),
                    pending_known=True,
                    can_clear=0 < len(rows) <= HISTORY_LIMIT,
                    revision=_revision(rows),
                )
            else:
                self._seen.clear()
                state = replace(state, access_required=True)
        except OSError as exc:
            log.debug("Notification history unavailable: %s", exc)
        return NotificationsSnapshot(state, events)

    def set_paused(self, paused: bool) -> bool:
        if self._cancelled.is_set():
            return False
        current = self._quiet.selected_profile()
        if current in (_PRIORITY, _ALARMS):
            self._resume_profile = current
        target = self._resume_profile if paused else _UNRESTRICTED
        if not self._quiet.set_profile(target):
            return False
        for _ in range(20):
            if self._cancelled.is_set():
                return False
            if (
                self._quiet.selected_profile() == target
                and (self._quiet.effective_profile() != 0) == paused
            ):
                return True
            self._cancelled.wait(0.05)
        return False

    def clear_notifications(self, state: NotificationsState) -> bool:
        if self._cancelled.is_set():
            return False
        # The UI must have displayed every currently pending entry. A new or
        # changed notification between poll and click prevents a global clear.
        access, rows = self._listener.read(self._cancelled)
        if (
            access != 1
            or not rows
            or len(rows) > HISTORY_LIMIT
            or _revision(rows) != state.revision
        ):
            return False
        return self._listener.remove(rows, self._cancelled)

    def stop(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        self._seen.clear()
