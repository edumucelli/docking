"""Optional OS associations between hung application windows and DWM ghosts."""

from __future__ import annotations

import ctypes

from .ffi import bind


class WindowsGhostWindows:
    def __init__(self, user) -> None:
        self._exists = bind(user, "IsWindow", ctypes.c_int32, ctypes.c_void_p)
        self._class = bind(
            user,
            "GetClassNameW",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int32,
        )
        # These named user32 exports are outside the documented Win32 contract.
        # Missing exports disable association; never infer it from titles/PIDs.
        try:
            self._original = bind(
                user, "HungWindowFromGhostWindow", ctypes.c_void_p, ctypes.c_void_p
            )
        except AttributeError:
            self._original = None
        try:
            self._presentation = bind(
                user, "GhostWindowFromHungWindow", ctypes.c_void_p, ctypes.c_void_p
            )
        except AttributeError:
            self._presentation = None

    def original(self, ghost: int) -> int | None:
        if self._original is None or not self._exists(ghost):
            return None
        name = ctypes.create_unicode_buffer(256)
        self._class(ghost, name, len(name))
        if name.value != "Ghost":
            return None
        original = self._original(ghost)
        return (
            int(original)
            if original and original != ghost and self._exists(original)
            else None
        )

    def presentation(self, original: int) -> int | None:
        ghost = self._presentation(original) if self._presentation is not None else 0
        return int(ghost) if ghost and self.original(ghost) == original else None
