"""Bounded access to TSF on a validated application's actual input thread."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

from docking.log import get_logger

from .com import GUID, HRESULT
from .ffi import bind, process_machine
from .input_context import InputContext
from .input_profiles import InputProfile, NativeProfile, profile_value
from .process_job import ProcessJob

log = get_logger("windows.input")
_DWORD = ctypes.c_uint32
_P = ctypes.c_void_p


class WireProfile(ctypes.Structure):
    _fields_ = [
        ("kind", _DWORD),
        ("language", ctypes.c_uint16),
        ("padding", ctypes.c_uint16),
        ("clsid", GUID),
        ("profile", GUID),
        ("category", GUID),
        ("substitute", ctypes.c_uint64),
        ("capabilities", _DWORD),
        ("padding2", _DWORD),
        ("layout", ctypes.c_uint64),
        ("flags", _DWORD),
        ("padding3", _DWORD),
    ]


class Request(ctypes.Structure):
    _fields_ = (
        [(name, _DWORD) for name in ("magic", "version", "size", "operation")]
        + [(name, _DWORD) for name in ("pid", "thread", "root_pid", "root_thread")]
        + [(name, ctypes.c_uint64) for name in ("window", "root", "deadline", "nonce")]
        + [("state", ctypes.c_int32), ("cancelled", ctypes.c_int32)]
        + [("result", HRESULT), ("profile_known", _DWORD)]
        + [("requested", WireProfile), ("active", WireProfile)]
        + [("observed", ctypes.c_uint64), ("composition_bytes", ctypes.c_int32)]
        + [
            (name, _DWORD)
            for name in (
                "ime_flags",
                "conversion",
                "sentence",
                "machine",
                "authorized",
                "parent_pid",
                "phase",
                "atom",
                "dispatched",
            )
        ]
        + [("language_tag", ctypes.c_uint16 * 96)]
    )


@dataclass(frozen=True)
class InputSnapshot:
    profile: InputProfile | None
    layout: int
    language_tag: str
    ime_known: bool
    ime_open: bool
    composition_bytes: int
    conversion: int
    sentence: int


@dataclass(frozen=True)
class InputRequestDiagnostic:
    """Bounded protocol evidence; contains no typed text or window titles."""

    operation: int
    machine: int
    state: int
    phase: int
    hresult: int
    dispatched: bool
    authorized: bool
    requested_layout: int
    observed_layout: int

    @classmethod
    def read(cls, value: Request) -> InputRequestDiagnostic:
        return cls(
            value.operation,
            value.machine,
            value.state,
            value.phase,
            value.result & 0xFFFFFFFF,
            bool(value.dispatched),
            bool(value.authorized),
            value.requested.layout,
            value.observed,
        )


class InputRequestError(OSError):
    """A native request failed, retaining the exact protocol boundary."""

    def __init__(
        self, code: int | None, message: str, diagnostic: InputRequestDiagnostic
    ):
        super().__init__(code, message)
        self.diagnostic = diagnostic


def _profile(value: WireProfile) -> InputProfile:
    native = NativeProfile()
    native.kind, native.language = value.kind, value.language
    native.clsid, native.profile, native.category = (
        value.clsid,
        value.profile,
        value.category,
    )
    native.substitute, native.layout, native.flags = (
        value.substitute,
        value.layout,
        value.flags,
    )
    return profile_value(native)


def _snapshot(value: Request) -> InputSnapshot:
    tag = bytes(value.language_tag).decode("utf-16-le", errors="replace").split("\0")[0]
    return InputSnapshot(
        _profile(value.active) if value.profile_known else None,
        value.observed,
        tag,
        bool(value.ime_flags & 1),
        bool(value.ime_flags & 2),
        value.composition_bytes,
        value.conversion,
        value.sentence,
    )


@lru_cache(maxsize=2)
def _runtime(bits: int) -> Path:
    """Foreign processes must load a local DLL with only system dependencies."""
    source = Path(__file__).with_name(f"input-thread-{bits}.dll")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    directory = Path(tempfile.gettempdir()) / "Docking" / "input-runtime" / digest
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return target
    temporary = directory / f"{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@contextmanager
def _mapping(kernel, name: str) -> Iterator[Request]:
    handle = bind(
        kernel,
        "CreateFileMappingW",
        _P,
        _P,
        _P,
        _DWORD,
        _DWORD,
        _DWORD,
        ctypes.c_wchar_p,
    )(_P(-1), None, 4, 0, ctypes.sizeof(Request), name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        address = bind(
            kernel, "MapViewOfFile", _P, _P, _DWORD, _DWORD, _DWORD, ctypes.c_size_t
        )(handle, 6, 0, 0, ctypes.sizeof(Request))
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield Request.from_address(address)
        finally:
            bind(kernel, "UnmapViewOfFile", ctypes.c_int32, _P)(address)
    finally:
        bind(kernel, "CloseHandle", ctypes.c_int32, _P)(handle)


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL:
    # One process-wide reference keeps callbacks valid through unhooking and
    # avoids retaining another module reference after every applet recreation.
    return cast(ctypes.CDLL, ctypes.WinDLL(str(_runtime(64))))


def _reap_host(process: subprocess.Popen[bytes], *, terminate: bool) -> None:
    # A failed job assignment leaves an owned, unauthorized host outside the
    # job. Reap it directly rather than waiting for its native request deadline.
    if terminate and process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)


class InputThread:
    """A request owns its atom, mapping, hook, and optional x86 host process."""

    def __init__(self) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user = ctypes.WinDLL("user32", use_last_error=True)

    def _machine(self, pid: int) -> int:
        kernel = self._kernel
        handle = bind(kernel, "OpenProcess", _P, _DWORD, ctypes.c_int32, _DWORD)(
            0x1000,
            False,
            pid,  # PROCESS_QUERY_LIMITED_INFORMATION
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            machine = process_machine(kernel, handle)
            if machine not in (0x14C, 0x8664):
                raise OSError("Unsupported input-thread architecture")
            return machine
        finally:
            bind(kernel, "CloseHandle", ctypes.c_int32, _P)(handle)

    def request(
        self,
        context: InputContext,
        cancelled: threading.Event,
        profile: InputProfile | None = None,
        *,
        layout: int | None = None,
    ) -> InputSnapshot | None:
        if profile is not None and layout is not None:
            raise ValueError("Specify an input profile or a keyboard layout")
        if layout is not None and layout & 0xFFFFFFFF <= 1:
            raise ValueError("An exact loaded keyboard layout is required")
        if cancelled.is_set():
            return None
        machine = self._machine(context.pid)
        bits = 32 if machine == 0x14C else 64
        path = _runtime(bits)
        name = "Local\\DockingInput." + uuid.uuid4().hex
        with ExitStack() as scope:
            value = scope.enter_context(_mapping(self._kernel, name))
            return self._dispatch(
                context,
                cancelled,
                profile,
                machine,
                bits,
                path,
                name,
                value,
                layout=layout,
            )

    def _dispatch(
        self,
        context: InputContext,
        cancelled: threading.Event,
        profile: InputProfile | None,
        machine: int,
        bits: int,
        path: Path,
        name: str,
        value: Request,
        *,
        layout: int | None = None,
    ) -> InputSnapshot | None:
        if cancelled.is_set():
            return None
        kernel, user = self._kernel, self._user
        size = ctypes.sizeof(Request)
        atom = hook = None
        process = job = None
        assigned = False
        deadline = time.monotonic() + 7
        try:
            value.magic, value.version, value.size = 0x44494E50, 1, size
            value.operation = 3 if layout is not None else (2 if profile else 1)
            value.pid, value.thread = context.pid, context.thread
            value.root_pid = context.root_pid or context.pid
            value.root_thread = context.root_thread or context.thread
            value.window, value.root = context.window, context.root
            ticks = bind(kernel, "GetTickCount64", ctypes.c_uint64)
            value.deadline = ticks() + 7000
            # Fits both LPARAM widths; the mapping name has 128 random bits.
            value.nonce = uuid.uuid4().int & 0x7FFFFFFF
            value.machine, value.parent_pid = machine, os.getpid()
            if profile is not None:
                value.requested.kind, value.requested.language = (
                    profile.kind,
                    profile.language,
                )
                value.requested.clsid = GUID.parse(profile.clsid)
                value.requested.profile = GUID.parse(profile.identifier)
                value.requested.layout = profile.layout
            elif layout is not None:
                value.requested.kind, value.requested.language = 2, layout & 0xFFFF
                value.requested.layout = layout & 0xFFFFFFFF
            value.phase = 200  # Controller atom/host setup.
            atom = bind(kernel, "GlobalAddAtomW", ctypes.c_uint16, ctypes.c_wchar_p)(
                name
            )
            if not atom:
                raise ctypes.WinError(ctypes.get_last_error())
            value.atom = atom
            if bits == 32:
                host = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
                job = ProcessJob()
                # rundll32 quotes the DLL filename before the comma, rather
                # than parsing an MSVCRT argument containing both components.
                executable = host / "SysWOW64/rundll32.exe"
                process = subprocess.Popen(
                    f'"{executable}" "{path}",Dispatch {atom}',
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                job.assign(int(process._handle))
                assigned = True
                if cancelled.is_set() or time.monotonic() >= deadline:
                    return None
                value.authorized = 1
            else:
                library = _library()
                if bind(library, "ProtocolSize", _DWORD)() != size:
                    raise OSError("Incompatible input-thread helper")
                if cancelled.is_set() or time.monotonic() >= deadline:
                    return None
                value.authorized = 1
                value.phase = 201  # Controller hook installation.
                hook = bind(
                    user, "SetWindowsHookExW", _P, ctypes.c_int, _P, _P, _DWORD
                )(
                    3,
                    ctypes.cast(library.InputHook, _P),
                    library._handle,
                    context.thread,
                )
                if not hook:
                    raise ctypes.WinError(ctypes.get_last_error())
                value.phase = 202  # Registered-message lookup.
                message = bind(
                    user, "RegisterWindowMessageW", _DWORD, ctypes.c_wchar_p
                )("Docking.Input.Profile.v1")
                if cancelled.is_set() or time.monotonic() >= deadline:
                    return None
                value.phase = (
                    203  # Set before posting: native execution may start immediately.
                )
                if not message or not bind(
                    user,
                    "PostMessageW",
                    ctypes.c_int32,
                    _P,
                    _DWORD,
                    ctypes.c_size_t,
                    ctypes.c_ssize_t,
                )(context.window, message, atom, value.nonce):
                    raise ctypes.WinError(ctypes.get_last_error())
                value.dispatched = 1
            while value.state != 2:
                if cancelled.wait(0.005):
                    return None
                if time.monotonic() >= deadline:
                    log.debug(
                        "Input-thread request timed out: state=%s phase=%s",
                        value.state,
                        value.phase,
                    )
                    return None
                if (
                    process is not None
                    and process.poll() is not None
                    and value.state != 2
                ):
                    raise OSError(
                        "Input-thread host exited before completing its request"
                    )
            if cancelled.is_set():
                return None
            if value.result < 0:
                raise InputRequestError(
                    value.result & 0xFFFFFFFF,
                    "Input-thread request failed: "
                    f"phase={value.phase} kind={value.requested.kind} "
                    f"flags=0x{value.requested.flags:08x}",
                    InputRequestDiagnostic.read(value),
                )
            return _snapshot(Request.from_buffer_copy(bytes(value)))
        except OSError as error:
            if isinstance(error, InputRequestError):
                raise
            # WinError.errno is a translated C errno. Keep the actual Win32
            # code at this external exception boundary when one is available.
            try:
                code = error.winerror
            except AttributeError:
                code = None
            if code is None:
                code = error.errno
            raise InputRequestError(
                code, str(error), InputRequestDiagnostic.read(value)
            ) from error
        finally:
            value.cancelled = 1
            # Each release must still run if an earlier close/wait fails.
            with ExitStack() as cleanup:
                if atom:
                    cleanup.callback(
                        bind(
                            kernel, "GlobalDeleteAtom", ctypes.c_uint16, ctypes.c_uint16
                        ),
                        atom,
                    )
                if process is not None:
                    cleanup.callback(_reap_host, process, terminate=not assigned)
                if job is not None:
                    cleanup.callback(job.close)
                if hook:
                    cleanup.callback(
                        bind(user, "UnhookWindowsHookEx", ctypes.c_int32, _P), hook
                    )
