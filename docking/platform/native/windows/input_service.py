"""Enabled text-service profiles and bounded per-application input operations."""

from __future__ import annotations

import threading
from contextlib import ExitStack
from dataclasses import replace

from docking.platform.native.windows.com import apartment
from docking.platform.native.windows.input_context import InputContext
from docking.platform.native.windows.input_profiles import (
    KEYBOARD_CATEGORY,
    InputCatalog,
    InputProfile,
    TextProfiles,
)
from docking.platform.native.windows.input_thread import InputSnapshot, InputThread


class WindowsInputService:
    def __init__(self) -> None:
        self._thread = InputThread()

    def catalog(self, loaded: tuple[int, ...] = ()) -> InputCatalog:
        with apartment(), ExitStack() as scope:
            service = TextProfiles(scope)
            records = service.profiles()
            keyboards = {p.code: p for p in records if p.kind == 2}
            for handle in loaded:
                code = f"win32:{handle & 0xFFFFFFFF:08x}"
                if code not in keyboards:
                    profile = service.for_layout(handle)
                    if profile is not None:
                        keyboards[code] = profile
            return InputCatalog(
                tuple(
                    # EnumProfiles flags can lag EnableLanguageProfile on an
                    # existing thread. Use the dedicated query for TIP state.
                    replace(p, flags=p.flags | 2)
                    for p in records
                    if p.kind == 1
                    and p.category == KEYBOARD_CATEGORY
                    and service.is_enabled(p)
                ),
                tuple(keyboards.values()),
            )

    def profiles(self) -> tuple[InputProfile, ...]:
        return self.catalog().profiles

    def read(
        self, context: InputContext, cancelled: threading.Event
    ) -> InputSnapshot | None:
        return self._thread.request(context, cancelled)

    def activate(
        self, context: InputContext, profile: InputProfile, cancelled: threading.Event
    ) -> InputSnapshot | None:
        if cancelled.is_set():
            return None
        catalog = self.catalog((profile.layout,) if profile.kind == 2 else ())
        allowed = {p.code for p in (*catalog.profiles, *catalog.keyboards) if p.enabled}
        if profile.code not in allowed:
            return None
        return self._thread.request(context, cancelled, profile)
