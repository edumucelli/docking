"""Windows media sessions and thumbnails, owned by one MTA worker."""

from __future__ import annotations

import ctypes
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass

from docking.log import get_logger
from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    release,
    value,
)
from docking.platform.backends.windows.winrt import (
    POINTER,
    WindowsRuntime,
    check,
)

from .state import MusicState, unavailable_state

log = get_logger("music.windows_media")
_MANAGER_STATICS = GUID.parse("2050c4ee-11a0-57de-aed7-c97c70338245")
_UNKNOWN = GUID.parse("00000000-0000-0000-c000-000000000046")
_RANDOM_ACCESS = GUID.parse("905a0fe1-bc53-11df-8c49-001e4fc686da")
_BUFFER_FACTORY = GUID.parse("71af914d-c10f-484b-bc50-14bc623b3a27")
_BUFFER_BYTES = GUID.parse("905a0fef-bc53-11df-8c49-001e4fc686da")
_CLOSABLE = GUID.parse("30d5a829-7fa4-4026-83bb-d75bae4ea99e")
_MAX_ART_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class MediaSession:
    token: str
    pointer: ctypes.c_void_p
    application_id: str


class WindowsMediaAPI:
    """Retain real COM session identities so actions cannot retarget another app."""

    def __init__(self, cancelled: threading.Event) -> None:
        self._owner = threading.get_ident()
        self._cancelled = cancelled
        self._resources = ExitStack()
        self._sessions: dict[int, MediaSession] = {}
        self._last_token = ""
        self._art_key: tuple[str, ...] = ()
        self._art = b""
        self._art_at = 0.0
        try:
            self._runtime = self._resources.enter_context(WindowsRuntime())
            with ExitStack() as request:
                factory = self._runtime.factory(
                    request,
                    "Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager",
                    _MANAGER_STATICS,
                )
                operation = object_result(request, factory, 6)
                self._runtime.wait(request, operation, cancelled)
                self._manager = object_result(self._resources, operation, 8)
                if not self._manager.value:
                    raise OSError("Media session manager was not returned")
        except Exception:
            self._resources.close()
            raise

    def _thread(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("Media interfaces used on another thread")

    def close(self) -> None:
        self._thread()
        for session in self._sessions.values():
            release(session.pointer)
        self._sessions.clear()
        self._art, self._art_key = b"", ()
        self._resources.close()

    def _refresh_sessions(self) -> tuple[list[MediaSession], str]:
        self._thread()
        live, current_token = set(), ""
        result = []
        with ExitStack() as scope:
            current = object_result(scope, self._manager, 6)
            current_identity = (
                query(scope, current, _UNKNOWN).value if current.value else None
            )
            sessions = object_result(scope, self._manager, 7)
            count = value(sessions, 7, ctypes.c_uint32).value
            if count > 64:
                raise OSError("Media session count exceeded its limit")
            for index in range(count):
                if self._cancelled.is_set():
                    raise OSError("Media read cancelled")
                with ExitStack() as item:
                    session = object_result(item, sessions, 6, ctypes.c_uint32, index)
                    identity = query(item, session, _UNKNOWN).value
                    if identity is None:
                        continue
                    application = self._runtime.text(session, 6)
                    previous = self._sessions.get(identity)
                    if previous is None or previous.application_id != application:
                        method(session, 1, ctypes.c_uint32)(session)
                        selected = MediaSession(
                            uuid.uuid4().hex, POINTER(session.value), application
                        )
                        self._sessions[identity] = selected
                        if previous is not None:
                            release(previous.pointer)
                    else:
                        selected = previous
                    live.add(identity)
                    result.append(selected)
                    if identity == current_identity:
                        current_token = selected.token
        for identity in set(self._sessions) - live:
            release(self._sessions.pop(identity).pointer)
        return result, current_token

    @staticmethod
    def _playback(session: MediaSession) -> tuple[str, bool, bool, bool, int]:
        with ExitStack() as scope:
            info = object_result(scope, session.pointer, 9)
            controls = object_result(scope, info, 6)
            raw = value(info, 7, ctypes.c_int32).value
            status = {
                0: "Stopped",
                1: "Stopped",
                2: "Unknown",
                3: "Stopped",
                4: "Playing",
                5: "Paused",
            }.get(raw, "Unknown")
            play = bool(value(controls, 6, ctypes.c_ubyte).value)
            pause = bool(value(controls, 7, ctypes.c_ubyte).value)
            toggle = bool(value(controls, 16, ctypes.c_ubyte).value)
            mode = 20 if toggle else 11 if raw == 4 else 10
            can_toggle = toggle or (pause if raw == 4 else play)
            return (
                status,
                can_toggle,
                bool(value(controls, 12, ctypes.c_ubyte).value),
                bool(value(controls, 13, ctypes.c_ubyte).value),
                mode,
            )

    def poll(self) -> MusicState:
        sessions, current = self._refresh_sessions()
        if not sessions:
            self._last_token = ""
            self._art, self._art_key = b"", ()
            return unavailable_state()
        states = [(session, self._playback(session)) for session in sessions]
        playing = [row for row in states if row[1][0] == "Playing"]
        pool = playing or states
        selected, playback = next(
            (row for row in pool if row[0].token == current),
            next((row for row in pool if row[0].token == self._last_token), pool[0]),
        )
        status, can_toggle, can_next, can_previous, _mode = playback
        self._last_token = selected.token
        title = artist = album = ""
        art = b""
        with ExitStack() as scope:
            operation = object_result(scope, selected.pointer, 7)
            try:
                self._runtime.wait(scope, operation, self._cancelled)
                properties = object_result(scope, operation, 8)
            except OSError as exc:
                if exc.errno not in (0x80070015, 0x8000000A):
                    raise
                properties = POINTER()
            if properties.value:
                title = self._runtime.text(properties, 6)
                artist = self._runtime.text(properties, 9)
                album = self._runtime.text(properties, 10)
                key = (
                    selected.token,
                    title,
                    artist,
                    album,
                    str(value(properties, 11, ctypes.c_int32).value),
                )
                if key == self._art_key and time.monotonic() - self._art_at < 5:
                    art = self._art
                else:
                    reference = object_result(scope, properties, 15)
                    try:
                        art = self._thumbnail(reference) if reference.value else b""
                    except OSError as exc:
                        if self._cancelled.is_set():
                            raise
                        log.debug("Media thumbnail unavailable: %s", exc)
                        art = b""
                    self._art_key, self._art, self._art_at = key, art, time.monotonic()
        return MusicState(
            available=True,
            player_name=selected.application_id,
            player_application_id=selected.application_id,
            player_id=selected.token,
            playback_status=status,
            title=title,
            artist=artist,
            album=album,
            can_play_pause=can_toggle,
            can_go_next=can_next,
            can_go_previous=can_previous,
            art_data=art,
        )

    @staticmethod
    def _close_stream(pointer) -> None:
        check(method(pointer, 6, HRESULT)(pointer))

    def _thumbnail(self, reference) -> bytes:
        with ExitStack() as scope:
            operation = object_result(scope, reference, 6)
            self._runtime.wait(scope, operation, self._cancelled)
            stream = object_result(scope, operation, 8)
            random = query(scope, stream, _RANDOM_ACCESS)
            closable = query(scope, stream, _CLOSABLE)
            scope.callback(self._close_stream, closable)
            size = value(random, 6, ctypes.c_uint64).value
            if not 0 < size <= _MAX_ART_BYTES:
                return b""
            input_stream = object_result(scope, random, 8, ctypes.c_uint64, 0)
            factory = self._runtime.factory(
                scope, "Windows.Storage.Streams.Buffer", _BUFFER_FACTORY
            )
            buffer = object_result(scope, factory, 6, ctypes.c_uint32, size)
            read = object_result(
                scope,
                input_stream,
                6,
                POINTER,
                buffer,
                ctypes.c_uint32,
                size,
                ctypes.c_int32,
                0,
            )
            self._runtime.wait(scope, read, self._cancelled)
            loaded = object_result(scope, read, 10)  # IAsyncOperationWithProgress
            count = value(loaded, 7, ctypes.c_uint32).value
            if count != size:
                raise OSError("Incomplete media thumbnail")
            access = query(scope, loaded, _BUFFER_BYTES)
            data = value(access, 3, POINTER).value
            if not data:
                raise OSError("Missing media thumbnail bytes")
            return ctypes.string_at(data, count)

    def contains(self, state: MusicState) -> bool:
        sessions, _current = self._refresh_sessions()
        return any(
            row.token == state.player_id
            and row.application_id == state.player_application_id
            for row in sessions
        )

    def control(self, state: MusicState, action: str) -> bool:
        sessions, _current = self._refresh_sessions()
        selected = next(
            (
                row
                for row in sessions
                if row.token == state.player_id
                and row.application_id == state.player_application_id
            ),
            None,
        )
        if selected is None:
            return False
        _status, can_toggle, can_next, can_previous, toggle = self._playback(selected)
        if action == "play_pause" and can_toggle:
            index = toggle
        elif action == "next" and can_next:
            index = 16
        elif action == "previous" and can_previous:
            index = 17
        else:
            return False
        with ExitStack() as scope:
            operation = object_result(scope, selected.pointer, index)
            self._runtime.wait(scope, operation, self._cancelled)
            return bool(value(operation, 8, ctypes.c_ubyte).value)
