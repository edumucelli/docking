"""Bounded input-runtime diagnostics and an owned desktop TSF monitor.

Only ``text_services`` initializes anything. Existing ctfmon monitors are left
alone; an inaccessible same-session monitor makes ownership ambiguous, so the
fixture skips initialization. Successful initialization is balanced on its
calling thread. No service, task, language default or registry entry is changed.
"""

from __future__ import annotations

import ctypes as c
import os
import struct
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath

# Microsoft SDK MsCtfMonitorApi.h: do not start a floating language bar.
# https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/MsCtfMonitorApi.h
_LANGUAGE_BAR_OFF = 0x00002
_TIP = "{C2CB2CF0-AF47-413E-9780-8BC3A3C16068}"
_PROFILES = (
    "{591AE943-56BE-48F6-8966-06B43915CC5A}",
    "{5FB02EC5-0A77-4684-B4FA-DEF8A2195628}",
)
_GUID = c.c_ubyte * 16
_THREAD_MANAGER_EX = _GUID.from_buffer_copy(
    uuid.UUID("3e90ade3-7594-4cb0-bb58-69628f5f458c").bytes_le
)
_KEYSTROKE_MANAGER = _GUID.from_buffer_copy(
    uuid.UUID("aa80e7f0-2021-11d2-93e0-0060b067b86e").bytes_le
)
_KNOWN_PROCESSES = frozenset(("ctfmon.exe", "textinputhost.exe", "tabtip.exe"))
_KNOWN_MODULES = frozenset(
    (
        "msctf.dll",
        "msctfmonitor.dll",
        "imm32.dll",
        "msimtf.dll",
        "imetip.dll",
        "inputhost.dll",
        "windows.ui.core.textinput.dll",
        "input-thread-64.dll",
        "input-thread-32.dll",
    )
)
_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
HRESULT = c.c_int32
_OWNED: dict[tuple, int] = {}
_OWNED_LOCK = threading.Lock()


class _ContextStatus(c.Structure):
    # TF_STATUS aliases TS_STATUS: two DWORDs, also on 64-bit Windows.
    # https://learn.microsoft.com/windows/win32/api/textstor/ns-textstor-ts_status
    _fields_ = [("dynamic", _U), ("static", _U)]


def bind(library, name, result, *arguments):
    # Standalone ABI boundary: importing docking here would preload the source
    # package before --installed-root acceptance selects its installed wheel.
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def method(pointer, index, result, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(_P))).contents
    return c.WINFUNCTYPE(result, _P, *arguments)(table[index])


def release(pointer):
    method(pointer, 2, _U)(pointer)


def _error(exc: OSError) -> dict:
    # OSError messages can contain private paths. Keep only its numeric code.
    return {"error": type(exc).__name__, "errno": exc.errno}


def _hr(value: int) -> str:
    return f"0x{value & 0xFFFFFFFF:08x}"


def _machine(path: Path) -> str:
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("Missing executable header")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= 16 * 1024 * 1024:
            raise ValueError("Executable header exceeds diagnostic bound")
        source.seek(offset)
        signature = source.read(6)
    if len(signature) != 6 or signature[:4] != b"PE\0\0":
        raise ValueError("Missing PE signature")
    machine = struct.unpack_from("<H", signature, 4)[0]
    return {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
        machine, f"0x{machine:04x}"
    )


class _Environment:
    def __init__(self) -> None:
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.user = c.WinDLL("user32", use_last_error=True)

    def _name(self, handle: int) -> str | None:
        if not handle:
            return None
        value, needed = c.create_unicode_buffer(256), _U()
        if not bind(
            self.user,
            "GetUserObjectInformationW",
            _B,
            _P,
            c.c_int,
            _P,
            _U,
            c.POINTER(_U),
        )(handle, 2, value, c.sizeof(value), c.byref(needed)):
            return None
        return value.value

    def desktop(self) -> dict:
        thread = bind(self.kernel, "GetCurrentThreadId", _U)()
        session = _U()
        if not bind(self.kernel, "ProcessIdToSessionId", _B, _U, c.POINTER(_U))(
            os.getpid(), c.byref(session)
        ):
            raise c.WinError(c.get_last_error())
        desktop = bind(self.user, "GetThreadDesktop", _P, _U)(thread)
        station = bind(self.user, "GetProcessWindowStation", _P)()
        return {
            "session": session.value,
            "thread": thread,
            "desktop": self._name(desktop),
            "window_station": self._name(station),
        }

    def session_context(self, session: int) -> dict[str, object]:
        """Worker-only connection facts: local WTS queries may perform RPC.

        Only WTSConnectState is queried, never user/client names or addresses.
        https://learn.microsoft.com/windows/win32/api/wtsapi32/ne-wtsapi32-wts_info_class
        """
        facts: dict[str, object] = {"session": session}
        try:
            facts["remote_session"] = bool(
                bind(self.user, "GetSystemMetrics", c.c_int32, c.c_int32)(0x1000)
            )
        except OSError as exc:
            facts["remote_session_error"] = _error(exc)
        try:
            console = bind(self.kernel, "WTSGetActiveConsoleSessionId", _U)()
            facts["active_console_session"] = None if console == 0xFFFFFFFF else console
            facts["matches_active_console"] = console == session
        except OSError as exc:
            facts["active_console_error"] = _error(exc)
        try:
            wts = c.WinDLL("wtsapi32", use_last_error=True)
            buffer, length = _P(), _U()
            try:
                sent = bind(
                    wts,
                    "WTSQuerySessionInformationW",
                    _B,
                    _P,
                    _U,
                    c.c_int32,
                    c.POINTER(_P),
                    c.POINTER(_U),
                )(None, session, 8, c.byref(buffer), c.byref(length))
                if not sent:
                    facts["wts_error"] = {
                        "error": "WTSQuerySessionInformationW",
                        "winerror": c.get_last_error(),
                    }
                elif not buffer.value or length.value != c.sizeof(c.c_int32):
                    facts["wts_error"] = {
                        "error": "invalid-connect-state-buffer",
                        "bytes": length.value,
                    }
                else:
                    state = c.cast(buffer, c.POINTER(c.c_int32)).contents.value
                    names = (
                        "WTSActive",
                        "WTSConnected",
                        "WTSConnectQuery",
                        "WTSShadow",
                        "WTSDisconnected",
                        "WTSIdle",
                        "WTSListen",
                        "WTSReset",
                        "WTSDown",
                        "WTSInit",
                    )
                    facts.update(
                        wts_connection_state=state,
                        wts_connection_name=names[state]
                        if 0 <= state < len(names)
                        else "unknown",
                    )
            finally:
                if buffer.value:
                    bind(wts, "WTSFreeMemory", None, _P)(buffer)
        except OSError as exc:
            facts["wts_error"] = _error(exc)
        return facts

    def _system_path(self, path: str) -> tuple[Path | None, str]:
        # Registry paths are untrusted. Never inspect UNC/external files, or
        # publish their spelling. All input components of interest ship in Windows.
        expanded = os.path.expandvars(path).strip('"')
        candidate = PureWindowsPath(expanded)
        root = PureWindowsPath(os.environ["SYSTEMROOT"])
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None, "<outside-system-directory>"
        if ".." in relative.parts:
            return None, "<outside-system-directory>"
        return Path(expanded), "%SystemRoot%\\" + "\\".join(relative.parts)

    def _version(self, path: Path) -> str | None:
        version = c.WinDLL("version", use_last_error=True)
        size = bind(version, "GetFileVersionInfoSizeW", _U, c.c_wchar_p, _P)(
            str(path), None
        )
        if not size or size > 1024 * 1024:
            return None
        data = c.create_string_buffer(size)
        if not bind(version, "GetFileVersionInfoW", _B, c.c_wchar_p, _U, _U, _P)(
            str(path), 0, size, data
        ):
            return None
        pointer, length = _P(), _U()
        if (
            not bind(
                version,
                "VerQueryValueW",
                _B,
                _P,
                c.c_wchar_p,
                c.POINTER(_P),
                c.POINTER(_U),
            )(data, "\\", c.byref(pointer), c.byref(length))
            or length.value < 16
        ):
            return None
        signature, _structure, high, low = struct.unpack(
            "<4I", c.string_at(pointer, 16)
        )
        if signature != 0xFEEF04BD:
            return None
        return ".".join(
            str(n) for n in (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)
        )

    def file(self, value: str, *, view: str | None = None) -> dict:
        path, label = self._system_path(value)
        result = {"path": label}
        if path is None:
            return result
        if view == "x86":
            root = Path(os.environ["SYSTEMROOT"])
            try:
                relative = path.relative_to(root / "System32")
            except ValueError:
                pass
            else:
                result["registered_path"] = label
                path = root / "SysWOW64" / relative
                result["path"] = "%SystemRoot%\\SysWOW64\\" + "\\".join(relative.parts)
        try:
            result.update(machine=_machine(path), version=self._version(path))
        except OSError as exc:
            result.update(_error(exc))
        except ValueError:
            result["error"] = "invalid-PE-header"
        return result

    def processes(self) -> dict:
        import psutil

        items, complete = [], True
        for index, process in enumerate(psutil.process_iter(("pid", "name"))):
            if index >= 8192:
                complete = False
                break
            name = process.info["name"]
            if name is None:
                complete = False
                continue
            if name.casefold() not in _KNOWN_PROCESSES:
                continue
            item = {"pid": process.pid, "name": name.casefold()}
            try:
                session = _U()
                if not bind(self.kernel, "ProcessIdToSessionId", _B, _U, c.POINTER(_U))(
                    process.pid, c.byref(session)
                ):
                    raise c.WinError(c.get_last_error())
                item.update(session=session.value, **self.file(process.exe()))
                desktops = set()
                threads = process.threads()
                item["thread_count"] = len(threads)
                # psutil can report a running ctfmon with no visible threads.
                # That is an inspection limit, not proof of desktop absence.
                item["desktop_scan_complete"] = bool(threads) and len(threads) <= 256
                for thread in threads[:256]:
                    handle = bind(self.user, "GetThreadDesktop", _P, _U)(thread.id)
                    desktop = self._name(handle)
                    if desktop is None:
                        item["desktop_scan_complete"] = False
                    else:
                        desktops.add(desktop)
                item["desktops"] = sorted(desktops)
            except psutil.Error as exc:
                item["error"] = type(exc).__name__
                item["desktop_scan_complete"] = False
            except OSError as exc:
                item.update(_error(exc))
                item["desktop_scan_complete"] = False
            items.append(item)
        return {"items": items, "complete": complete}

    def services(self) -> list[dict]:
        import psutil

        result = []
        for name in (
            "TextInputManagementService",
            "TabletInputService",
            "BrokerInfrastructure",
        ):
            item = {"name": name}
            try:
                service = psutil.win_service_get(name)
                item.update(status=service.status(), start_type=service.start_type())
            except psutil.Error as exc:
                item["error"] = type(exc).__name__
            result.append(item)
        return result

    def registration(self) -> list[dict]:
        import winreg

        result = []
        for name, view in (
            ("x64", winreg.KEY_WOW64_64KEY),
            ("x86", winreg.KEY_WOW64_32KEY),
        ):
            item = {"view": name}
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT,
                    f"CLSID\\{_TIP}\\InprocServer32",
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    value, _kind = winreg.QueryValueEx(key, "")
                if isinstance(value, str):
                    item.update(self.file(value, view=name))
                else:
                    item["error"] = "invalid-registration-type"
            except OSError as exc:
                item.update(_error(exc))
            profiles = {}
            for profile in _PROFILES:
                key_path = (
                    f"SOFTWARE\\Microsoft\\CTF\\TIP\\{_TIP}"
                    f"\\LanguageProfile\\0x0000042a\\{profile}"
                )
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view
                    ):
                        profiles[profile] = {"registered": True}
                except OSError as exc:
                    profiles[profile] = {"registered": False, **_error(exc)}
            item["profiles"] = profiles
            result.append(item)
        return result

    def modules(self, pid: int) -> dict:
        result = {"pid": pid, "modules": []}
        process = bind(self.kernel, "OpenProcess", _P, _U, _B, _U)(0x410, False, pid)
        if not process:
            return {**result, **_error(c.WinError(c.get_last_error()))}
        try:
            machine, native = c.c_uint16(), c.c_uint16()
            if bind(
                self.kernel,
                "IsWow64Process2",
                _B,
                _P,
                c.POINTER(c.c_uint16),
                c.POINTER(c.c_uint16),
            )(process, c.byref(machine), c.byref(native)):
                value = machine.value or native.value
                result["machine"] = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
                    value, f"0x{value:04x}"
                )
            session = _U()
            if bind(self.kernel, "ProcessIdToSessionId", _B, _U, c.POINTER(_U))(
                pid, c.byref(session)
            ):
                result["session"] = session.value
            known = _KNOWN_MODULES | {
                PureWindowsPath(item["path"]).name.casefold()
                for item in self.registration()
                if item.get("path", "").startswith("%SystemRoot%\\")
            }
            handles, needed = (_P * 4096)(), _U()
            if not bind(
                self.kernel,
                "K32EnumProcessModulesEx",
                _B,
                _P,
                _P,
                _U,
                c.POINTER(_U),
                _U,
            )(process, handles, c.sizeof(handles), c.byref(needed), 3):
                raise c.WinError(c.get_last_error())
            result["complete"] = needed.value <= c.sizeof(handles)
            for handle in handles[: min(needed.value // c.sizeof(_P), len(handles))]:
                value = c.create_unicode_buffer(32768)
                if not bind(
                    self.kernel,
                    "K32GetModuleFileNameExW",
                    _U,
                    _P,
                    _P,
                    c.c_wchar_p,
                    _U,
                )(process, handle, value, len(value)):
                    result["complete"] = False
                    continue
                if self._input_module(value.value, known):
                    result["modules"].append(self.file(value.value))
        except OSError as exc:
            result.update(_error(exc))
        finally:
            bind(self.kernel, "CloseHandle", _B, _P)(process)
        return result

    def _input_module(self, value: str, known: frozenset[str] | set[str]) -> bool:
        if PureWindowsPath(value).name.casefold() in known:
            return True  # file() redacts and never inspects non-system files.
        path, label = self._system_path(value)
        if path is None:
            return False
        parts = tuple(part.casefold() for part in PureWindowsPath(label).parts[1:])
        # Modern TSF engines need not expose legacy HKCR InprocServer32 keys.
        # Observe already-loaded Windows input DLLs instead of guessing a COM
        # package mapping or walking package/user directories.
        return PureWindowsPath(value).suffix.casefold() == ".dll" and (
            parts[:1] == ("ime",)
            or parts[:2]
            in (
                ("system32", "inputmethod"),
                ("system32", "ime"),
                ("syswow64", "inputmethod"),
                ("syswow64", "ime"),
            )
        )

    def thread(self, window: int) -> dict:
        facts = self.desktop()
        owner = _U()
        target_thread = bind(
            self.user, "GetWindowThreadProcessId", _U, _P, c.POINTER(_U)
        )(window, c.byref(owner))
        layout = bind(self.user, "GetKeyboardLayout", _P, _U)(0)
        facts.update(
            window=window,
            window_thread=target_thread,
            window_pid=owner.value,
            window_matches_thread=(
                target_thread == facts["thread"] and owner.value == os.getpid()
            ),
            hkl=f"{int(layout or 0) & 0xFFFFFFFF:08x}",
        )
        imm = c.WinDLL("imm32", use_last_error=True)
        facts["imm_is_ime"] = bool(bind(imm, "ImmIsIME", _B, _P)(layout))
        if facts["window_matches_thread"]:
            context = bind(imm, "ImmGetContext", _P, _P)(window)
            facts["imm_context"] = bool(context)
            if context:
                try:
                    conversion, sentence = _U(), _U()
                    facts["imm_open"] = bool(
                        bind(imm, "ImmGetOpenStatus", _B, _P)(context)
                    )
                    if bind(
                        imm,
                        "ImmGetConversionStatus",
                        _B,
                        _P,
                        c.POINTER(_U),
                        c.POINTER(_U),
                    )(context, c.byref(conversion), c.byref(sentence)):
                        facts.update(
                            imm_conversion=conversion.value, imm_sentence=sentence.value
                        )
                finally:
                    bind(imm, "ImmReleaseContext", _B, _P, _P)(window, context)
        manager = _P()
        msctf = c.WinDLL("msctf", use_last_error=True)
        status = bind(msctf, "TF_GetThreadMgr", HRESULT, c.POINTER(_P))(
            c.byref(manager)
        )
        facts.update(tsf_hresult=_hr(status), tsf_manager=bool(manager.value))
        with ExitStack() as scope:
            if manager.value:
                scope.callback(release, manager)
            if status < 0 or not manager.value:
                return facts
            # SDK msctf.h includes IUnknown's three vtable slots. Only getters
            # on the existing manager/context are used; no keys are tested or
            # dispatched and no TSF focus, sink or compartment is changed.
            # https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/msctf.h
            focused = _B()
            status = method(manager, 10, HRESULT, c.POINTER(_B))(
                manager, c.byref(focused)
            )
            facts["tsf_thread_focus_hresult"] = _hr(status)
            if status == 0:
                facts["tsf_thread_focus"] = bool(focused.value)
            keys = _P()
            status = method(manager, 0, HRESULT, c.POINTER(_GUID), c.POINTER(_P))(
                manager, c.byref(_KEYSTROKE_MANAGER), c.byref(keys)
            )
            facts.update(
                tsf_keystroke_hresult=_hr(status),
                tsf_keystroke_manager=bool(keys.value),
            )
            if keys.value:
                scope.callback(release, keys)
            if status >= 0 and keys.value:
                clsid = _GUID()
                status = method(keys, 5, HRESULT, c.POINTER(_GUID))(
                    keys, c.byref(clsid)
                )
                facts["tsf_foreground_hresult"] = _hr(status)
                if status == 0:
                    facts["tsf_foreground_clsid"] = str(
                        uuid.UUID(bytes_le=bytes(clsid))
                    )
                elif status == 1:  # S_FALSE explicitly means no foreground TIP.
                    facts["tsf_foreground_clsid"] = None
            extended = _P()
            status = method(manager, 0, HRESULT, c.POINTER(_GUID), c.POINTER(_P))(
                manager, c.byref(_THREAD_MANAGER_EX), c.byref(extended)
            )
            facts["tsf_flags_hresult"] = _hr(status)
            if extended.value:
                scope.callback(release, extended)
            if status >= 0 and extended.value:
                flags = _U()
                status = method(extended, 15, HRESULT, c.POINTER(_U))(
                    extended, c.byref(flags)
                )
                facts["tsf_flags_hresult"] = _hr(status)
                if status >= 0:
                    facts["tsf_flags"] = flags.value
            document = _P()
            status = method(manager, 7, HRESULT, c.POINTER(_P))(
                manager, c.byref(document)
            )
            facts.update(
                tsf_focus_hresult=_hr(status), tsf_document=bool(document.value)
            )
            if document.value:
                scope.callback(release, document)
            if status >= 0 and document.value:
                context = _P()
                status = method(document, 6, HRESULT, c.POINTER(_P))(
                    document, c.byref(context)
                )
                facts.update(
                    tsf_context_hresult=_hr(status), tsf_context=bool(context.value)
                )
                if context.value:
                    scope.callback(release, context)
                if status >= 0 and context.value:
                    context_status = _ContextStatus()
                    status = method(context, 11, HRESULT, c.POINTER(_ContextStatus))(
                        context, c.byref(context_status)
                    )
                    facts["tsf_status_hresult"] = _hr(status)
                    if status == 0:
                        facts.update(
                            tsf_dynamic_flags=context_status.dynamic,
                            tsf_static_flags=context_status.static,
                        )
                    view = _P()
                    status = method(context, 9, HRESULT, c.POINTER(_P))(
                        context, c.byref(view)
                    )
                    facts.update(
                        tsf_view_hresult=_hr(status), tsf_view=bool(view.value)
                    )
                    if view.value:
                        scope.callback(release, view)
                    if status >= 0 and view.value:
                        handle = _P()
                        status = method(view, 6, HRESULT, c.POINTER(_P))(
                            view, c.byref(handle)
                        )
                        facts["tsf_view_window_hresult"] = _hr(status)
                        if status == 0:
                            # A windowless document may validly return NULL.
                            facts.update(
                                tsf_view_window=handle.value,
                                tsf_view_matches_window=bool(handle.value)
                                and handle.value == window,
                            )
        return facts

    def initialize(self) -> int:
        return bind(self._monitor_library(), "InitLocalMsCtfMonitor", HRESULT, _U)(
            _LANGUAGE_BAR_OFF
        )

    def uninitialize(self) -> int:
        return bind(self._monitor_library(), "UninitLocalMsCtfMonitor", HRESULT)()

    def _monitor_library(self):
        # Learn lists Msctf.dll, but the Windows exports and Microsoft's SDK
        # binding use MsCtfMonitor.dll. Load its system path explicitly.
        # https://github.com/microsoft/windows-rs/blob/0.58.0/crates/libs/windows/src/Windows/Win32/UI/TextServices/mod.rs#L9
        path = (
            PureWindowsPath(os.environ["SYSTEMROOT"]) / "System32" / "MsCtfMonitor.dll"
        )
        return c.WinDLL(str(path), use_last_error=True)


def runtime_snapshot() -> dict:
    """Read bounded OS facts on a worker, excluding command lines/private paths."""
    if sys.platform != "win32":
        return {"available": False, "reason": "requires-native-windows"}
    environment = _Environment()
    result = {"available": True}
    for name, read in (
        ("desktop", environment.desktop),
        ("processes", environment.processes),
        ("services", environment.services),
        ("registration", environment.registration),
    ):
        try:
            result[name] = read()
        except OSError as exc:
            result[name] = _error(exc)
    if "session" in result["desktop"]:
        result["session_context"] = environment.session_context(
            result["desktop"]["session"]
        )
    else:
        result["session_context"] = {"error": "desktop-session-unavailable"}
    result["modules"] = environment.modules(os.getpid())
    return result


def target_modules(pid: int) -> dict:
    """Read only known input modules in an explicitly identified native target."""
    if sys.platform != "win32":
        return {"available": False, "reason": "requires-native-windows"}
    return _Environment().modules(pid)


def thread_snapshot(window: int) -> dict:
    """Observe the current UI thread; never create/activate a TSF manager."""
    if sys.platform != "win32":
        return {"available": False, "reason": "requires-native-windows"}
    try:
        return {"available": True, **_Environment().thread(window)}
    except OSError as exc:
        return {"available": False, **_error(exc)}


def _monitor_reason(desktop: dict, processes: dict) -> str | None:
    if desktop["window_station"] != "WinSta0" or not desktop["desktop"]:
        return "noninteractive-or-unknown-desktop"
    if not processes["complete"]:
        return "process-inventory-incomplete"
    for item in processes["items"]:
        if item["name"] != "ctfmon.exe":
            continue
        if "session" not in item:
            return "monitor-identity-incomplete"
        if item["session"] != desktop["session"]:
            continue
        if "machine" not in item:
            return "monitor-identity-incomplete"
        if item["machine"] != "x64":
            continue
        desktops = item.get("desktops", ())
        if desktop["desktop"] in desktops:
            return "existing-desktop-monitor"
        if not desktops or not item.get("desktop_scan_complete", False):
            return "monitor-desktop-unknown"
    return None


@contextmanager
def text_services() -> Iterator[dict]:
    """Own a local monitor only when no matching desktop monitor is observed.

    The mutable journal receives cleanup facts on context exit. Callers save it
    after exit, including on failure. Initialization failures are recorded so
    the full composition diagnostic can still run; cleanup failures propagate.
    """
    journal = {"initialized": False, "cleanup_completed": False}
    if sys.platform != "win32":
        journal.update(
            available=False, reason="requires-native-windows", cleanup_completed=True
        )
        yield journal
        return
    caller = threading.get_ident()
    environment = _Environment()
    journal["before"] = environment.desktop()
    journal["processes_before"] = environment.processes()
    reason = _monitor_reason(journal["before"], journal["processes_before"])
    if reason is not None:
        journal.update(available=True, reason=reason, cleanup_completed=True)
        yield journal
        return
    journal["available"] = True
    identity = tuple(
        journal["before"][key] for key in ("session", "window_station", "desktop")
    )
    with _OWNED_LOCK:
        owner = _OWNED.get(identity)
        if owner is not None and owner != caller:
            raise RuntimeError("TSF fixture monitor belongs to another thread")
        if owner is None:
            _OWNED[identity] = caller
    if owner is not None:
        journal.update(reason="existing-fixture-monitor", cleanup_completed=True)
        yield journal
        return
    try:
        try:
            status = environment.initialize()
            journal["initialize_hresult"] = _hr(status)
            journal["initialized"] = status >= 0
        except (OSError, AttributeError) as exc:
            # Missing exports are an external Windows-version compatibility boundary.
            journal["initialize_error"] = (
                _error(exc)
                if isinstance(exc, OSError)
                else {"error": "missing-monitor-export"}
            )
        if not journal["initialized"]:
            with _OWNED_LOCK:
                del _OWNED[identity]
        try:
            yield journal
        finally:
            journal["after"] = environment.desktop()
            if journal["initialized"]:
                if threading.get_ident() != caller:
                    journal["cleanup_error"] = "caller-thread-changed"
                    raise RuntimeError("TSF monitor must close on its calling thread")
                if any(
                    journal["before"][key] != journal["after"][key]
                    for key in ("session", "window_station", "desktop")
                ):
                    journal["cleanup_error"] = "caller-desktop-changed"
                    raise RuntimeError(
                        "TSF monitor must close before switching desktop"
                    )
                try:
                    status = environment.uninitialize()
                except (OSError, AttributeError) as exc:
                    journal["cleanup_error"] = (
                        _error(exc)
                        if isinstance(exc, OSError)
                        else {"error": "missing-monitor-export"}
                    )
                    raise
                journal["uninitialize_hresult"] = _hr(status)
                if status < 0:
                    raise OSError(f"TSF monitor cleanup failed: {_hr(status)}")
            journal["cleanup_completed"] = True
    finally:
        if journal["initialized"]:
            with _OWNED_LOCK:
                del _OWNED[identity]
