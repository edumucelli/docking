"""Scoped IME mode setup for explicitly owned, focused acceptance windows.

Local operations run on the target UI thread through documented IMM functions.
Foreign editors use the legacy WM_IME_CONTROL compatibility setters, whose
modern Win32 contract is incomplete; independent input-thread readback is
mandatory. Unsupported setters, lost identity and incomplete restoration fail
the fixture. No production input preference or default profile is changed.
"""

from __future__ import annotations

import ctypes as c
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Protocol, TypedDict, TypeVar

from tools.windows.visual.input_environment import bind

_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
_T = TypeVar("_T")


class _Dispatch(Protocol):
    def __call__(self, operation: Callable[[], _T]) -> _T: ...


class _Identity(TypedDict):
    window: int
    root: int
    pid: int
    thread: int
    root_owner: list[int]
    ime_window: int
    layout: int


class _Gui(c.Structure):
    _fields_ = (
        [("size", _U), ("flags", _U)]
        + [
            (name, _P)
            for name in ("active", "focus", "capture", "menu", "move", "caret")
        ]
        + [("rect", c.c_int32 * 4)]
    )


@dataclass(frozen=True)
class IMEMode:
    open: bool
    conversion: int
    sentence: int


class _IME:
    def __init__(self, window: int, root: int, pid: int, thread: int) -> None:
        self.window, self.root, self.pid, self.thread = window, root, pid, thread
        self.local = pid == os.getpid()
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.imm = c.WinDLL("imm32", use_last_error=True)
        self.original: _Identity | None = None
        self.context: int | None = None

    def _owner(self, window: int) -> tuple[int, int]:
        pid = _U()
        thread = bind(self.user, "GetWindowThreadProcessId", _U, _P, c.POINTER(_U))(
            window, c.byref(pid)
        )
        return int(thread), pid.value

    def identity(self) -> _Identity:
        if self._owner(self.window) != (self.thread, self.pid):
            raise RuntimeError("Owned typing window identity changed")
        root_owner = self._owner(self.root)
        if not all(root_owner) or (
            self.window != self.root
            and not bind(self.user, "IsChild", _B, _P, _P)(self.root, self.window)
        ):
            raise RuntimeError("Owned typing window left its root")
        if bind(self.user, "GetForegroundWindow", _P)() != self.root:
            raise RuntimeError("Owned typing window lost foreground")
        info = _Gui()
        info.size = c.sizeof(info)
        if (
            not bind(self.user, "GetGUIThreadInfo", _B, _U, c.POINTER(_Gui))(
                root_owner[0], c.byref(info)
            )
            or info.focus != self.window
        ):
            raise RuntimeError("Owned typing window lost focus")
        if self.local and bind(self.kernel, "GetCurrentThreadId", _U)() != self.thread:
            raise RuntimeError("Local IMM setup requires the owned UI thread")
        ime = bind(self.imm, "ImmGetDefaultIMEWnd", _P, _P)(self.window)
        if not ime or self._owner(ime) != (self.thread, self.pid):
            raise RuntimeError("Owned default IME window is unavailable")
        result: _Identity = {
            "window": self.window,
            "root": self.root,
            "pid": self.pid,
            "thread": self.thread,
            "root_owner": list(root_owner),
            "ime_window": int(ime),
            "layout": int(
                bind(self.user, "GetKeyboardLayout", _P, _U)(self.thread) or 0
            ),
        }
        if self.original is not None and result != self.original:
            raise RuntimeError("Owned IME context identity changed")
        return result

    @contextmanager
    def _context(self) -> Iterator[int]:
        self.identity()
        context = bind(self.imm, "ImmGetContext", _P, _P)(self.window)
        if not context:
            raise RuntimeError("Owned input context is unavailable")
        try:
            if self.context is None:
                self.context = int(context)
            elif self.context != int(context):
                raise RuntimeError("Owned input context was replaced")
            yield int(context)
        finally:
            if not bind(self.imm, "ImmReleaseContext", _B, _P, _P)(
                self.window, context
            ):
                raise RuntimeError("Owned input context release failed")

    def read(self) -> IMEMode:
        with self._context() as context:
            opened = bool(bind(self.imm, "ImmGetOpenStatus", _B, _P)(context))
            conversion, sentence = _U(), _U()
            if not bind(
                self.imm, "ImmGetConversionStatus", _B, _P, c.POINTER(_U), c.POINTER(_U)
            )(context, c.byref(conversion), c.byref(sentence)):
                raise RuntimeError("Owned IME conversion read failed")
            return IMEMode(opened, conversion.value, sentence.value)

    def _control(self, command: int, value: int) -> None:
        identity = self.identity()
        result = c.c_size_t()
        c.set_last_error(0)
        # A foreign thread always uses a bounded send. Scalars need no custom
        # cross-architecture marshalling; no HWND_BROADCAST or shared pointer.
        sent = bind(
            self.user,
            "SendMessageTimeoutW",
            c.c_ssize_t,
            _P,
            _U,
            c.c_size_t,
            c.c_ssize_t,
            _U,
            _U,
            c.POINTER(c.c_size_t),
        )(identity["ime_window"], 0x283, command, value, 0x23, 1000, c.byref(result))
        if not sent:
            raise TimeoutError(
                f"Owned IME control failed or timed out ({c.get_last_error()})"
            )
        self.identity()

    def set_mode(self, *, opened: bool, conversion: int | None, sentence: int) -> None:
        if self.local:
            with self._context() as context:
                if not bind(self.imm, "ImmSetOpenStatus", _B, _P, _B)(context, opened):
                    raise RuntimeError("Owned IME open status change failed")
                if conversion is not None and not bind(
                    self.imm, "ImmSetConversionStatus", _B, _P, _U, _U
                )(context, conversion, sentence):
                    raise RuntimeError("Owned IME conversion change failed")
            self.identity()
        else:
            # SDK immdev.h names 2/4/6 for NI_CONTEXTUPDATED. The legacy
            # WM_IME_CONTROL setter behavior is a compatibility boundary here,
            # accepted only when independent in-target readback agrees.
            # https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-control
            self._control(6, int(opened))
            if conversion is not None:
                self._control(2, conversion)
                self._control(4, sentence)


@contextmanager
def ime_conversion(
    *,
    window: int,
    root: int,
    pid: int,
    thread: int,
    journal: dict[str, object],
    dispatch: _Dispatch | None = None,
    readback: Callable[[], IMEMode] | None = None,
    validate: Callable[[], None] | None = None,
    conversion: int | None = None,
) -> Iterator[dict[str, object]]:
    """Open an owned fixture IME, then restore its exact prior mode.

    ``dispatch`` invokes local operations synchronously on the owned UI thread.
    Foreign ``readback`` must use the existing input-thread snapshot and reject
    unknown IME state or a changed selected profile. Conversion remains as the
    IME chooses on opening unless the fixture explicitly requests a value.
    ``validate`` checks the selected TSF profile on the controller thread,
    outside UI dispatch, so an input-thread read can service its UI callback.
    """
    native = _IME(window, root, pid, thread)
    journal.update(
        transport="local-imm" if native.local else "foreign-legacy-ime-control",
        conversion_requested=conversion,
        restored=False,
    )
    if not native.local and readback is None:
        raise ValueError("Foreign IME setup requires independent input-thread readback")

    def invoke(operation: Callable[[], _T]) -> _T:
        return dispatch(operation) if dispatch is not None else operation()

    def validate_target() -> None:
        if validate is not None:
            validate()

    def inspect() -> IMEMode:
        validate_target()
        invoke(native.identity)
        mode = readback() if readback is not None else invoke(native.read)
        invoke(native.identity)
        validate_target()
        return mode

    validate_target()
    native.original = invoke(native.identity)
    validate_target()
    journal["identity"] = native.original
    before = inspect()
    journal["before"] = asdict(before)
    touched = False

    def change(opened: bool, mode: int | None, sentence: int) -> None:
        validate_target()
        invoke(
            lambda: native.set_mode(opened=opened, conversion=mode, sentence=sentence)
        )
        validate_target()

    try:
        if not before.open or (
            conversion is not None and conversion != before.conversion
        ):
            touched = True  # A failing native setter can still have changed mode.
            change(True, conversion, before.sentence)
        after = inspect()
        journal["after"] = asdict(after)
        if not after.open or (
            conversion is not None and after.conversion != conversion
        ):
            raise RuntimeError("Owned IME did not enter the requested mode")
        yield journal
    except (OSError, RuntimeError) as error:
        journal["error"] = type(error).__name__
        raise
    finally:
        journal["restore_attempted"] = touched
        try:
            current = inspect()
            if current != before:
                journal["restore_attempted"] = True
                change(before.open, before.conversion, before.sentence)
            restored = inspect()
            journal["after_restore"] = asdict(restored)
            journal["restored"] = restored == before
            if not journal["restored"]:
                raise RuntimeError("Owned IME mode did not restore exactly")
        except (OSError, RuntimeError) as error:
            journal["restore_error"] = type(error).__name__
            raise
