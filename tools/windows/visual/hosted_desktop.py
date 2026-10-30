"""Prepare only disposable GitHub-hosted Windows 11 Arm desktops for input CI.

The image's first-logon UI and recurring WSL terminal obscure physical input:
https://github.com/actions/runner-images/issues/14069
https://github.com/electron/electron/pull/54535
Only DisablePrivacyExperience is changed, and its exact previous value is
restored. This tool is never part of Docking's normal application startup.
"""

from __future__ import annotations

import argparse
import ctypes as c
import os
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager, suppress
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.input_environment import bind

_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\OOBE"
_VALUE = "DisablePrivacyExperience"
_CLOUD = "Microsoft.Windows.CloudExperienceHost_cw5n1h2txyewy"
_START = "Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"
_TERMINAL = "Microsoft.WindowsTerminal_8wekyb3d8bbwe"
_WSL_UPDATE_ARGS = ("--update", "--confirm", "--prompt-before-exit")
_NAMES = frozenset(
    (
        "useroobebroker.exe",
        "wwahost.exe",
        "startmenuexperiencehost.exe",
        "systempropertiesperformance.exe",
        "windowsterminal.exe",
        "wsl.exe",
    )
)


class PreparationError(RuntimeError):
    def __init__(self, operation: str, code: int = 13):
        super().__init__(f"{operation} ({code})")
        self.operation, self.code = operation, code
        self.candidate = None


def _stamp():
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
    }


class WslDismissalPendingError(PreparationError):
    def __init__(self):
        super().__init__("Verified WSL terminal minimization is pending", 1460)


def require_hosted(platform, environment, facts=None):
    if (
        platform != "win32"
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise PreparationError("Requires a disposable GitHub-hosted Windows runner", 50)
    if facts is not None and (
        facts["product_type"] != 1
        or facts["build"] < 22000
        or facts["native_machine"] != 0xAA64
    ):
        raise PreparationError("Requires Windows 11 client on native Arm64", 50)


def _path(value):
    return str(PureWindowsPath(value)).casefold()


def matches_wsl_update(root, image, arguments):
    expected = PureWindowsPath(root) / "System32/wsl.exe"
    return (
        _path(image) == _path(expected)
        and len(arguments) == 4
        and _path(arguments[0]) in ("wsl.exe", _path(expected))
        and tuple(arguments[1:]) == _WSL_UPDATE_ARGS
    )


@dataclass(frozen=True)
class Identity:
    handle: int
    pid: int
    session: int
    created: int
    name: str
    path: str
    family: str | None = None
    package_error: int = 0
    wsl_update: bool = False

    def journal(self):
        result = {
            "pid": self.pid,
            "session": self.session,
            "created": self.created,
            "name": self.name,
            "family": self.family,
            "package_error": self.package_error,
        }
        if self.name == "wsl.exe":
            result["update_arguments_verified"] = self.wsl_update
        return result


def action_for(identity, window, *, root, session, foreground):
    """Authorize narrowly identified first-login UI; never infer from a PID/name."""
    if identity.session != session:
        return None
    if PureWindowsPath(identity.path).name.casefold() != identity.name:
        raise PreparationError("Candidate process name differs from its image path")
    system = PureWindowsPath(root) / "System32"
    if identity.name == "wsl.exe":
        return (
            "stop-wsl-update"
            if identity.wsl_update and _path(identity.path) == _path(system / "wsl.exe")
            else None
        )
    expected = {
        "useroobebroker.exe": system / "oobe/UserOOBEBroker.exe",
        "wwahost.exe": system / "WWAHost.exe",
        "systempropertiesperformance.exe": system / "SystemPropertiesPerformance.exe",
        "startmenuexperiencehost.exe": PureWindowsPath(root)
        / "SystemApps"
        / _START
        / "StartMenuExperienceHost.exe",
    }
    if identity.name in expected and _path(identity.path) != _path(
        expected[identity.name]
    ):
        raise PreparationError("Unexpected first-login executable path")
    if identity.name == "useroobebroker.exe":
        return "terminate"
    if identity.name == "wwahost.exe":
        if identity.family != _CLOUD:
            raise PreparationError(
                "Unproven WWAHost package", identity.package_error or 13
            )
        return "terminate"
    if window is None or window["cloaked"]:
        return None
    if window["minimized"] and not (
        identity.name == "windowsterminal.exe" and window["hwnd"] == foreground
    ):
        return None
    if identity.name == "systempropertiesperformance.exe":
        if window["class"] == "#32770":
            return "close"
        raise PreparationError("Unproven Performance Options window")
    if identity.name == "startmenuexperiencehost.exe":
        if window["hwnd"] != foreground:
            return None
        if identity.family != _START:
            raise PreparationError(
                "Unproven Start package", identity.package_error or 13
            )
        if window["class"] == "Windows.UI.Core.CoreWindow":
            return "escape"
        raise PreparationError("Unproven Start window")
    if identity.name == "windowsterminal.exe" and _path(window["title"]) == _path(
        system / "wsl.exe"
    ):
        if identity.family != _TERMINAL:
            raise PreparationError(
                "Unproven WSL terminal package", identity.package_error or 13
            )
        return "minimize"
    return None


class _FileTime(c.Structure):
    _fields_ = [("low", _U), ("high", _U)]


class NativeDesktop:
    def __init__(self):
        self.desktop = WindowsDesktop()
        self.user = self.desktop.user
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.dwm = c.WinDLL("dwmapi", use_last_error=True)
        directory = c.create_unicode_buffer(32768)
        self.check(
            bind(self.kernel, "GetWindowsDirectoryW", _U, c.c_wchar_p, _U)(
                directory, len(directory)
            ),
            "GetWindowsDirectoryW",
        )
        self.root = directory.value
        self.session = self.session_of(os.getpid())
        self.exited = []
        self.unverified_background = {}
        self.unverified_background_dropped = 0

    @staticmethod
    def check(result, operation):
        if not result:
            raise PreparationError(operation, c.get_last_error())
        return result

    def facts(self):
        guest, native = c.c_uint16(), c.c_uint16()
        current = bind(self.kernel, "GetCurrentProcess", _P)()
        self.check(
            bind(
                self.kernel,
                "IsWow64Process2",
                _B,
                _P,
                c.POINTER(c.c_uint16),
                c.POINTER(c.c_uint16),
            )(current, c.byref(guest), c.byref(native)),
            "IsWow64Process2",
        )
        version = sys.getwindowsversion()
        return {
            "product_type": version.product_type,
            "build": version.build,
            "native_machine": native.value,
            "session": self.session,
        }

    def session_of(self, pid):
        value = _U()
        self.check(
            bind(self.kernel, "ProcessIdToSessionId", _B, _U, c.POINTER(_U))(
                pid, c.byref(value)
            ),
            "ProcessIdToSessionId",
        )
        return value.value

    def package(self, handle):
        query = bind(
            self.kernel,
            "GetPackageFamilyName",
            c.c_int32,
            _P,
            c.POINTER(_U),
            c.c_wchar_p,
        )
        needed = _U()
        status = query(handle, c.byref(needed), None)
        if status != 122 or not 1 <= needed.value <= 512:
            return None, status
        value = c.create_unicode_buffer(needed.value)
        status = query(handle, c.byref(needed), value)
        return (value.value, 0) if status == 0 else (None, status)

    def _image(self, handle):
        path, length = c.create_unicode_buffer(32768), _U(32768)
        self.check(
            bind(
                self.kernel,
                "QueryFullProcessImageNameW",
                _B,
                _P,
                _U,
                c.c_wchar_p,
                c.POINTER(_U),
            )(handle, 0, path, c.byref(length)),
            "QueryFullProcessImageNameW",
        )
        return path.value

    def _created(self, handle):
        times = [_FileTime() for _ in range(4)]
        self.check(
            bind(self.kernel, "GetProcessTimes", _B, _P, *([c.POINTER(_FileTime)] * 4))(
                handle, *(c.byref(value) for value in times)
            ),
            "GetProcessTimes",
        )
        return times[0].low | (times[0].high << 32)

    def _snapshot(self, handle, pid, name):
        values = []
        for stage, query in (("image", self._image), ("creation", self._created)):
            try:
                values.append(query(handle))
            except PreparationError as error:
                if self._unavailable_query(handle, pid, name, stage, error):
                    return None
                raise
        return values[0], values[1]

    def _background_only(self, handle, pid):
        """Observe no visible top-level window and no foreground PID match.

        This authorizes leaving an unreadable candidate alone, never any action
        against it. It does not claim that the process owns no child windows.
        """
        assert bind(self.kernel, "GetProcessId", _U, _P)(handle) == pid, (
            "Unverified candidate identity changed"
        )
        owner = bind(
            self.user,
            "GetWindowThreadProcessId",
            _U,
            _P,
            c.POINTER(wintypes.DWORD),
        )

        def foreground_owner():
            hwnd = self.foreground()
            value = wintypes.DWORD()
            self.check(hwnd, "Foreground window unavailable")
            self.check(owner(hwnd, c.byref(value)), "Foreground owner unavailable")
            self.check(value.value, "Foreground process unavailable")
            return {"window": hwnd, "pid": value.value}

        before = foreground_owner()
        if before["pid"] == pid:
            return None
        visible = bind(self.user, "IsWindowVisible", _B, _P)
        is_window = bind(self.user, "IsWindow", _B, _P)
        state = {"count": 0, "visible": False, "error": None}

        @self.desktop._callback
        def visit(hwnd, _data):
            state["count"] += 1
            if state["count"] > 4096:
                state["error"] = PreparationError("Window enumeration exceeds bound")
                return False
            value = wintypes.DWORD()
            if not owner(hwnd, c.byref(value)) or not value.value:
                error = c.get_last_error()
                if not is_window(hwnd):
                    return True  # A destroyed HWND cannot currently obscure input.
                state["error"] = PreparationError("Window owner unavailable", error)
                return False
            if value.value == pid and visible(hwnd):
                state["visible"] = True
                return False
            return True

        completed = bind(
            self.user, "EnumWindows", _B, self.desktop._callback, wintypes.LPARAM
        )(visit, 0)
        if state["error"] is not None:
            raise state["error"]
        if state["visible"]:
            return None
        self.check(completed, "Window enumeration unavailable")
        after = foreground_owner()
        if after["pid"] == pid:
            return None
        return {
            "visible_top_level_windows": 0,
            "foreground_before": before,
            "foreground_after": after,
            "enumeration_completed": True,
        }

    def _unavailable_query(self, handle, pid, name, stage, error):
        """Skip only proved rundown or an unverified, invisible background target."""
        if error.code not in (5, 87):
            return False
        row = {"pid": pid, "name": name, "stage": stage, "code": error.code}
        wait = bind(self.kernel, "WaitForSingleObject", _U, _P, _U)
        status = wait(handle, 0)
        if status == 258:
            row["rundown_wait_ms"] = 50
            status = wait(handle, 50)
        row["wait_status"] = status
        if status == 0:
            self.exited.append(row)
            return True
        error.candidate = row
        if status != 258:
            if status == 0xFFFFFFFF:
                row["wait_error"] = c.get_last_error()
            return False
        try:
            proof = self._background_only(handle, pid)
        except (PreparationError, AssertionError) as failure:
            row["background_verification_error"] = (
                _error(failure)
                if isinstance(failure, PreparationError)
                else {"operation": "Candidate identity changed", "code": 13}
            )
            return False
        if proof is None:
            row["background_verified"] = False
            return False
        status = wait(handle, 0)
        row["wait_status"] = status
        if status == 0:
            self.exited.append(row)
            return True
        if status != 258:
            if status == 0xFFFFFFFF:
                row["wait_error"] = c.get_last_error()
            return False
        row.update(
            state="unverified-background",
            action="none",
            policy="no-visible-top-level-window-and-not-foreground",
            verification=proof,
            observed_at=_stamp(),
        )
        key = (pid, name, stage)
        if key in self.unverified_background or len(self.unverified_background) < 64:
            self.unverified_background[key] = row
        else:
            self.unverified_background_dropped += 1
        return True

    def _same_process(self, handle, pid, created, session):
        status = bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(handle, 0)
        if status == 0:
            return False
        if status != 258:
            raise PreparationError("Ancestry process wait", c.get_last_error())
        return (
            bind(self.kernel, "GetProcessId", _U, _P)(handle) == pid
            and self._created(handle) == created
            and self.session_of(pid) == session
        )

    def _ancestor_label(self, handle):
        path = self._image(handle)
        names = {
            "cmd.exe": "System32/cmd.exe",
            "powershell.exe": "System32/WindowsPowerShell/v1.0/powershell.exe",
            "taskeng.exe": "System32/taskeng.exe",
            "taskhostw.exe": "System32/taskhostw.exe",
            "svchost.exe": "System32/svchost.exe",
            "conhost.exe": "System32/conhost.exe",
            "wsl.exe": "System32/wsl.exe",
            "explorer.exe": "explorer.exe",
        }
        name = PureWindowsPath(path).name.casefold()
        relative = names.get(name)
        if relative is not None and _path(path) == _path(
            PureWindowsPath(self.root) / relative
        ):
            return name
        if name == "windowsterminal.exe" and self.package(handle) == (_TERMINAL, 0):
            return name
        return "opaque"

    def ancestry(self, identity):
        """Read at most four pinned parents of a verified interference target."""
        import psutil

        result = {"limit": 4, "parents": []}
        if not (
            (identity.name == "wsl.exe" and identity.wsl_update)
            or (identity.name == "windowsterminal.exe" and identity.family == _TERMINAL)
        ):
            return {**result, "unavailable": "unverified-target"}
        handle, pid, created, session = (
            identity.handle,
            identity.pid,
            identity.created,
            identity.session,
        )
        seen = {pid}

        def close(handle):
            self.check(
                bind(self.kernel, "CloseHandle", _B, _P)(handle),
                "Close ancestry process handle",
            )

        with ExitStack() as handles:
            for _ in range(result["limit"]):
                try:
                    if not self._same_process(handle, pid, created, session):
                        result["unavailable"] = "process-exited-or-replaced"
                        break
                    parent = psutil.Process(pid).ppid()
                    if not self._same_process(handle, pid, created, session):
                        result["unavailable"] = "process-exited-or-replaced"
                        break
                    if not parent or parent in seen:
                        result["end"] = "no-parent" if not parent else "cycle"
                        break
                    seen.add(parent)
                    parent_handle = bind(self.kernel, "OpenProcess", _P, _U, _B, _U)(
                        0x101000, False, parent
                    )
                    if not parent_handle:
                        result["unavailable"] = {
                            "parent_pid": parent,
                            "code": c.get_last_error(),
                        }
                        break
                    handles.callback(close, parent_handle)
                    parent_created = self._created(parent_handle)
                    if parent_created > created:
                        result["unavailable"] = "parent-pid-reused"
                        break
                    parent_session = self.session_of(parent)
                    label = self._ancestor_label(parent_handle)
                    if not self._same_process(
                        parent_handle, parent, parent_created, parent_session
                    ):
                        result["unavailable"] = "parent-exited-or-replaced"
                        break
                    result["parents"].append(
                        {
                            "pid": parent,
                            "created": parent_created,
                            "session": parent_session,
                            "label": label,
                        }
                    )
                    handle, pid, created, session = (
                        parent_handle,
                        parent,
                        parent_created,
                        parent_session,
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    result["unavailable"] = "parent-query-unavailable"
                    break
                except PreparationError as error:
                    result["unavailable"] = _error(error)
                    break
        return result

    def _live_wsl(self, identity, handle):
        status = bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(handle, 0)
        if status == 0:
            return False
        if status != 258:
            raise PreparationError("WSL updater process wait", c.get_last_error())
        if (
            bind(self.kernel, "GetProcessId", _U, _P)(handle) != identity.pid
            or self.session_of(identity.pid) != self.session
            or identity.session != self.session
            or self._created(handle) != identity.created
            or _path(self._image(handle)) != _path(identity.path)
            or _path(identity.path)
            != _path(PureWindowsPath(self.root) / "System32/wsl.exe")
        ):
            raise PreparationError("WSL updater process identity changed")
        return True

    def _wsl_arguments(self, pid):
        import psutil

        try:
            arguments = psutil.Process(pid).cmdline()
        except psutil.NoSuchProcess:
            raise PreparationError("WSL updater command line unavailable", 87) from None
        except psutil.AccessDenied:
            raise PreparationError("WSL updater command line unavailable", 5) from None
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise PreparationError("Invalid WSL updater command line")
        return arguments

    def _is_wsl_update(self, identity, handle):
        if not self._live_wsl(identity, handle):
            return False
        try:
            arguments = self._wsl_arguments(identity.pid)
        except PreparationError:
            if not self._live_wsl(identity, handle):
                return False
            raise
        if not self._live_wsl(identity, handle):
            return False
        return matches_wsl_update(self.root, identity.path, arguments)

    def _stop_wsl_update(self, identity, record):
        if (
            action_for(
                identity, None, root=self.root, session=self.session, foreground=0
            )
            != "stop-wsl-update"
        ):
            raise PreparationError("Unproven WSL updater")
        if not self._is_wsl_update(identity, identity.handle):
            if not self._live_wsl(identity, identity.handle):
                return "already-exited"
            raise PreparationError("WSL updater arguments changed")
        # Read-only classification precedes requesting termination rights.
        handle = bind(self.kernel, "OpenProcess", _P, _U, _B, _U)(
            0x101001, False, identity.pid
        )
        if not handle:
            error = c.get_last_error()
            if not self._live_wsl(identity, identity.handle):
                return "already-exited"
            raise PreparationError("Open verified WSL updater", error)
        try:
            if not self._live_wsl(identity, identity.handle):
                return "already-exited"
            if not self._is_wsl_update(identity, handle):
                if not self._live_wsl(identity, handle):
                    return "already-exited"
                raise PreparationError("WSL updater arguments changed")
            if not self._live_wsl(identity, identity.handle):
                return "already-exited"
            if not bind(self.kernel, "TerminateProcess", _B, _P, _U)(handle, 0):
                error = c.get_last_error()
                if bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(handle, 0) == 0:
                    return "already-exited"
                raise PreparationError("Terminate verified WSL updater", error)
            if bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(handle, 2000) != 0:
                raise PreparationError("Verified WSL updater did not exit", 1460)
            if record is not None:
                record["updater_stopped"] = True
            return "dismissed"
        finally:
            self.check(
                bind(self.kernel, "CloseHandle", _B, _P)(handle),
                "Close WSL updater handle",
            )

    @contextmanager
    def processes(self):
        import psutil

        with ExitStack() as handles:
            rows = []
            for process in psutil.process_iter(("pid", "name")):
                name = process.info["name"]
                if name is None or name.casefold() not in _NAMES:
                    continue
                name = name.casefold()
                try:
                    if self.session_of(process.pid) != self.session:
                        continue
                except PreparationError as error:
                    if error.code != 87:
                        raise
                    self.exited.append({"pid": process.pid, "name": name, "code": 87})
                    continue
                handle = bind(self.kernel, "OpenProcess", _P, _U, _B, _U)(
                    0x101000
                    | (1 if name in ("useroobebroker.exe", "wwahost.exe") else 0),
                    False,
                    process.pid,
                )
                if not handle and c.get_last_error() == 87:
                    self.exited.append({"pid": process.pid, "name": name, "code": 87})
                    continue
                self.check(handle, "OpenProcess first-login candidate")
                handles.callback(bind(self.kernel, "CloseHandle", _B, _P), handle)
                if self.session_of(process.pid) != self.session:
                    continue
                snapshot = self._snapshot(handle, process.pid, name)
                if snapshot is None:
                    continue
                path, created = snapshot
                family, error = (
                    self.package(handle)
                    if name
                    in (
                        "wwahost.exe",
                        "startmenuexperiencehost.exe",
                        "windowsterminal.exe",
                    )
                    else (None, 0)
                )
                discovered = Identity(
                    handle,
                    process.pid,
                    self.session,
                    created,
                    name,
                    path,
                    family,
                    error,
                )
                if name == "wsl.exe" and _path(path) == _path(
                    PureWindowsPath(self.root) / "System32/wsl.exe"
                ):
                    try:
                        verified = self._is_wsl_update(discovered, handle)
                    except PreparationError as error:
                        stage = {
                            "QueryFullProcessImageNameW": "image",
                            "GetProcessTimes": "creation",
                            "WSL updater command line unavailable": "command-line",
                        }.get(error.operation)
                        if stage is not None and self._unavailable_query(
                            handle, process.pid, name, stage, error
                        ):
                            continue
                        raise
                    discovered = replace(discovered, wsl_update=verified)
                rows.append(discovered)
            yield rows

    def windows(self, pid):
        rows = []
        is_window = bind(self.user, "IsWindow", _B, _P)
        for row in self.desktop.windows(pid):
            if not is_window(row["hwnd"]):
                continue
            cloaked = _U()
            status = bind(self.dwm, "DwmGetWindowAttribute", c.c_int32, _P, _U, _P, _U)(
                row["hwnd"], 14, c.byref(cloaked), c.sizeof(cloaked)
            )
            if status < 0:
                if status & 0xFFFFFFFF == 0x80070006 and not is_window(row["hwnd"]):
                    continue  # WM_CLOSE can destroy an enumerated window mid-query.
                raise PreparationError("DwmGetWindowAttribute", status & 0xFFFFFFFF)
            minimized = bool(bind(self.user, "IsIconic", _B, _P)(row["hwnd"]))
            if not is_window(row["hwnd"]):
                continue
            row["cloaked"] = bool(cloaked.value)
            row["minimized"] = minimized
            rows.append(row)
        return rows

    def foreground(self):
        return self.user.GetForegroundWindow()

    def _verify_pending_wsl(self, identity, window, command, record):
        hwnd = window["hwnd"]
        current = next(
            (row for row in self.windows(identity.pid) if row["hwnd"] == hwnd), None
        )
        foreground = self.foreground()
        if record is not None:
            record["window_state"] = (
                "disappeared"
                if current is None
                else ("minimized" if current["minimized"] else "visible")
            )
        if (current is None or current["minimized"]) and (
            command != 6 or foreground != hwnd
        ):
            return True
        if current is None:
            raise PreparationError("Closed WSL terminal retained foreground", 1460)
        if (
            bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(identity.handle, 0)
            != 258
            or bind(self.kernel, "GetProcessId", _U, _P)(identity.handle)
            != identity.pid
        ):
            raise PreparationError("Pending WSL process identity changed")
        # WindowsDesktop shares this DLL function and uses DWORD owner buffers.
        owner = wintypes.DWORD()
        self.check(
            bind(
                self.user,
                "GetWindowThreadProcessId",
                wintypes.DWORD,
                wintypes.HWND,
                c.POINTER(wintypes.DWORD),
            )(hwnd, c.byref(owner)),
            "Read pending WSL window owner",
        )
        family, error = self.package(identity.handle)
        if owner.value != identity.pid or family != _TERMINAL:
            raise PreparationError("Pending WSL window identity changed", error or 13)
        if (
            current["class"] != window["class"]
            or action_for(
                identity,
                current,
                root=self.root,
                session=self.session,
                foreground=foreground,
            )
            != "minimize"
        ):
            raise PreparationError("Pending WSL window identity changed")
        if record is not None:
            record.update(pending_window=hwnd, pending_identity_verified=True)
        return False

    def perform(self, identity, action, window, *, record=None):
        # Keep the opened process object, not merely a recyclable numeric PID.
        status = bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(
            identity.handle, 0
        )
        if status == 0:
            return "already-exited"
        if status != 258:
            raise PreparationError("First-login process wait", c.get_last_error())
        if bind(self.kernel, "GetProcessId", _U, _P)(identity.handle) != identity.pid:
            raise PreparationError("First-login process identity changed")
        if action == "stop-wsl-update":
            return self._stop_wsl_update(identity, record)
        if action == "terminate":
            self.check(
                bind(self.kernel, "TerminateProcess", _B, _P, _U)(identity.handle, 0),
                "TerminateProcess first-login UI",
            )
            if (
                bind(self.kernel, "WaitForSingleObject", _U, _P, _U)(
                    identity.handle, 2000
                )
                != 0
            ):
                raise PreparationError("First-login process did not exit", 1460)
            return "dismissed"
        current = next(
            (
                row
                for row in self.windows(identity.pid)
                if row["hwnd"] == window["hwnd"]
            ),
            None,
        )
        if (
            current is None
            or action_for(
                identity,
                current,
                root=self.root,
                session=self.session,
                foreground=self.foreground(),
            )
            != action
        ):
            raise PreparationError("First-login window identity changed")
        hwnd = current["hwnd"]
        command = None
        if action == "escape":
            if self.foreground() != hwnd:
                raise PreparationError("Start foreground changed before Escape")
            self.desktop.key(0x1B)
        elif action == "close":
            self.check(
                bind(self.user, "PostMessageW", _B, _P, _U, c.c_size_t, c.c_ssize_t)(
                    hwnd, 0x10, 0, 0
                ),
                "Close Performance Options",
            )
        else:
            before = self.foreground()
            show = bind(self.user, "ShowWindow", _B, _P, c.c_int32)
            commands = []
            if record is not None:
                record["show_commands"] = commands
            if current["minimized"] and before == hwnd:
                # Native proof: minimizing an already-iconic foreground window
                # is a no-op. Restore without activation before the Z-order handoff.
                restoration = {"foreground_before": before}
                if record is not None:
                    record["restore"] = restoration
                commands.append(4)  # SW_SHOWNOACTIVATE.
                show(hwnd, 4)

                def restored():
                    row = next(
                        (
                            row
                            for row in self.windows(identity.pid)
                            if row["hwnd"] == hwnd
                        ),
                        None,
                    )
                    restoration["window_state"] = (
                        "disappeared"
                        if row is None
                        else ("minimized" if row["minimized"] else "visible")
                    )
                    return row is None or (not row["minimized"] and not row["cloaked"])

                try:
                    self.desktop.wait_for(restored, "WSL terminal did not restore", 2)
                finally:
                    restoration["foreground_after"] = self.foreground()
                current = next(
                    (row for row in self.windows(identity.pid) if row["hwnd"] == hwnd),
                    None,
                )
                if current is None:
                    self.desktop.wait_for(
                        lambda: self.foreground() != hwnd,
                        "Closed WSL terminal retained foreground",
                        2,
                    )
                    if record is not None:
                        record.update(
                            window_state="disappeared",
                            foreground_after=self.foreground(),
                        )
                    return "dismissed"
                if (
                    bind(self.kernel, "GetProcessId", _U, _P)(identity.handle)
                    != identity.pid
                    or current["minimized"]
                    or current["cloaked"]
                    or action_for(
                        identity,
                        current,
                        root=self.root,
                        session=self.session,
                        foreground=self.foreground(),
                    )
                    != "minimize"
                ):
                    raise PreparationError("Restored WSL terminal identity changed")
                before = self.foreground()
            # SW_MINIMIZE lets Windows activate the next window in Z order.
            # SW_SHOWMINNOACTIVE can leave an iconic terminal as foreground.
            command = 6 if before == hwnd else 7
            if record is not None:
                record.update(show_command=command, foreground_before=before)
            commands.append(command)
            show(hwnd, command)

        def dismissed():
            rows = self.windows(identity.pid)
            if action == "minimize":
                current = next((row for row in rows if row["hwnd"] == hwnd), None)
                if record is not None:
                    record["window_state"] = (
                        "disappeared"
                        if current is None
                        else ("minimized" if current["minimized"] else "visible")
                    )
                return (current is None or current["minimized"]) and (
                    command != 6 or self.foreground() != hwnd
                )
            return not any(
                row["hwnd"] == hwnd and not row["cloaked"] and not row["minimized"]
                for row in rows
            )

        try:
            if action == "minimize":
                # Only this explicit deadline can produce a deferrable outcome;
                # assertions and native errors from readback remain fatal.
                deadline = time.monotonic() + 2
                while not dismissed():
                    if time.monotonic() >= deadline:
                        if self._verify_pending_wsl(identity, window, command, record):
                            return "dismissed"
                        raise WslDismissalPendingError()
                    time.sleep(0.1)
            else:
                self.desktop.wait_for(
                    dismissed, "First-login window was not dismissed", 2
                )
        finally:
            if record is not None:
                record["foreground_after"] = self.foreground()
        return "dismissed"

    def capture(self, path):
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(path)


@contextmanager
def privacy_policy(registry, journal):
    access = registry.KEY_READ | registry.KEY_SET_VALUE | registry.KEY_WOW64_64KEY
    created = False
    try:
        key = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, _POLICY, 0, access)
    except FileNotFoundError:
        key = registry.CreateKeyEx(registry.HKEY_LOCAL_MACHINE, _POLICY, 0, access)
        created = True

    def read(opened):
        try:
            return registry.QueryValueEx(opened, _VALUE)
        except FileNotFoundError:
            return None

    try:
        with key as opened:
            previous = read(opened)
            data = previous[0] if previous is not None else None
            journal["previous"] = (
                None
                if previous is None
                else {
                    "type": previous[1],
                    "data": {"binary_hex": data.hex()}
                    if isinstance(data, bytes)
                    else data,
                }
            )
            desired = (1, registry.REG_DWORD)
            try:
                if previous != desired:
                    registry.SetValueEx(opened, _VALUE, 0, registry.REG_DWORD, 1)
                if read(opened) != desired:
                    raise PreparationError("Privacy policy write was not verified")
                journal["applied"] = True
                yield
            finally:
                current = read(opened)
                if current != previous:
                    if current != desired:
                        journal["restored"] = False
                        raise PreparationError(
                            "Privacy policy changed externally; left untouched"
                        )
                    if previous is None:
                        registry.DeleteValue(opened, _VALUE)
                    else:
                        registry.SetValueEx(opened, _VALUE, 0, previous[1], previous[0])
                journal["restored"] = read(opened) == previous
                if not journal["restored"]:
                    raise PreparationError("Privacy policy restoration failed")
    finally:
        if created and journal.get("restored") is True:
            with registry.OpenKey(
                registry.HKEY_LOCAL_MACHINE, _POLICY, 0, access
            ) as opened:
                empty = registry.QueryInfoKey(opened)[:2] == (0, 0)
            if empty:
                registry.DeleteKeyEx(
                    registry.HKEY_LOCAL_MACHINE, _POLICY, registry.KEY_WOW64_64KEY, 0
                )


def sweep(api, journal, *, allow_pending_wsl=False):
    count = 0
    journal["known_visible_blockers"] = []
    with api.processes() as identities:
        inventory = []
        for identity in identities:
            windows = api.windows(identity.pid)
            inventory.append((identity, windows))
            journal.setdefault("observed", {})[f"{identity.pid}:{identity.created}"] = {
                **identity.journal(),
                "windows": [
                    {
                        "hwnd": row["hwnd"],
                        "class": row["class"],
                        "foreground": row["hwnd"] == api.foreground(),
                        "minimized": row["minimized"],
                        "cloaked": row["cloaked"],
                    }
                    for row in windows
                ],
            }
        journal["exited"] = list(api.exited)
        journal["unverified_background"] = list(api.unverified_background.values())
        journal["unverified_background_dropped"] = api.unverified_background_dropped
        # Retain the complete known inventory before a rejected classification
        # or an action can obscure the other first-login UI identities.
        updater_stopped = False
        for identity, windows in sorted(
            inventory, key=lambda row: not row[0].wsl_update
        ):
            if updater_stopped and identity.name == "windowsterminal.exe":
                windows = api.windows(identity.pid)
            for window in windows or [None]:
                action = action_for(
                    identity,
                    window,
                    root=api.root,
                    session=api.session,
                    foreground=api.foreground(),
                )
                if (
                    window is not None
                    and not window["minimized"]
                    and not window["cloaked"]
                    and (
                        action is not None
                        or (
                            identity.name == "startmenuexperiencehost.exe"
                            and identity.family == _START
                            and window["class"] == "Windows.UI.Core.CoreWindow"
                        )
                    )
                ):
                    journal["known_visible_blockers"].append(
                        {
                            "pid": identity.pid,
                            "hwnd": window["hwnd"],
                            "name": identity.name,
                        }
                    )
                if action is None:
                    continue
                entry = {**identity.journal(), "action": action, "before": "identified"}
                journal["actions"].append(entry)
                timed = action in ("stop-wsl-update", "minimize")
                if timed:
                    entry["started"] = _stamp()
                    if window is not None:
                        entry["window"] = window["hwnd"]
                try:
                    if timed:
                        entry["ancestry"] = api.ancestry(identity)
                    entry["after"] = (
                        api.perform(identity, action, window, record=entry)
                        or "dismissed"
                    )
                except WslDismissalPendingError as error:
                    if not allow_pending_wsl or action != "minimize":
                        raise
                    entry.update(after="pending", verification=_error(error))
                finally:
                    if timed:
                        entry["finished"] = _stamp()
                entry["foreground_after"] = api.foreground()
                count += 1
                if action == "stop-wsl-update":
                    updater_stopped = True
                if action in ("terminate", "stop-wsl-update"):
                    break
    return count


def settle_desktop(api, journal, *, clock=time.monotonic, pause=time.sleep):
    quiet, deadline = 0, clock() + 10
    while clock() < deadline:
        actions = sweep(api, journal)
        quiet = (
            quiet + 1 if not actions and not journal["known_visible_blockers"] else 0
        )
        journal["quiet_sweeps"] = quiet
        if quiet == 2:
            return
        pause(0.1)
    raise PreparationError("First-login desktop did not settle", 1460)


def _error(error):
    if isinstance(error, PreparationError):
        result = {"operation": error.operation, "code": error.code}
        if error.candidate is not None:
            result["candidate"] = error.candidate
        return result
    # Native errors expose winerror; Python/CRT pipe and file errors can omit
    # it or set it to None, including on Windows. This is an external boundary.
    try:
        code = error.winerror
    except AttributeError:
        code = None
    if code is None:
        code = error.errno or 0
    return {
        "operation": type(error).__name__,
        "code": code,
    }


@contextmanager
def collector(output, installed, journal, *, wheel, source_sha):
    # The child waits for permission before it can spawn a smoke or fixture.
    # This also contains every descendant if preparation later fails/cancels.
    from docking.platform.backends.windows.process_job import ProcessJob

    job, process = ProcessJob(), None
    try:
        journal["collector_spawn_started"] = _stamp()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.windows.visual.hosted_desktop",
                "--collect",
                "--wheel",
                str(wheel.resolve()),
                "--source-sha",
                source_sha,
                "--installed-root",
                str(installed.resolve()),
                "--output",
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[3],
            stdin=subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        journal["collector_started_at"] = _stamp()
        journal["collector_started"] = True
        journal["collector_stopped"] = False
        job.assign(int(process._handle))
        if process.stdin is None:
            raise PreparationError("Collector permission pipe is unavailable")
        process.stdin.write(b"1")
        process.stdin.close()
        journal["collector_permitted_at"] = _stamp()
        yield process
    finally:
        try:
            if (
                process is not None
                and process.stdin is not None
                and not process.stdin.closed
            ):
                process.stdin.close()
        finally:
            # Even a broken permission pipe cannot skip descendant cleanup.
            try:
                job.close()
            finally:
                if process is not None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    journal["collector_stopped"] = process.poll() is not None
                else:
                    journal["collector_stopped"] = True


def run_prepared(api, registry, output, collect, journal):
    journal.update(
        ok=False,
        actions=[],
        policy={},
        collector_stopped=True,
        collector_started=False,
        desktop_guard="main-thread",
    )

    def save():
        write_shared_json(output / "desktop-preparation.json", journal)

    try:
        with privacy_policy(registry, journal["policy"]):
            api.capture(output / "desktop-preparation-before.png")
            settle_desktop(api, journal)
            api.capture(output / "desktop-preparation-ready.png")
            journal["prepared"] = True
            save()
            with collect() as process:
                while process.poll() is None:
                    sweep(api, journal, allow_pending_wsl=True)
                    save()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=0.1)
                status = process.returncode
            # No UI watcher survives this point: sweeps and restoration both
            # run on this thread, and the owned collector job is already closed.
            if not journal["collector_stopped"]:
                raise PreparationError("Collector did not stop", 1460)
            journal["input_status"] = status
            api.capture(output / "desktop-preparation-after.png")
        journal["ok"] = status == 0
        return status
    except KeyboardInterrupt:
        journal["cancelled"] = True
        return 130
    except (PreparationError, OSError, AssertionError) as error:
        journal["error"] = (
            _error(error)
            if not isinstance(error, AssertionError)
            else {"operation": "Dismissal verification", "code": 1460}
        )
        return 1
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require_hosted(sys.platform, os.environ)
    api = NativeDesktop()
    facts = api.facts()
    require_hosted(sys.platform, os.environ, facts)
    import winreg

    from tools.windows.ci import input_domain

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    journal = {"guard": facts}
    if args.collect:
        if sys.stdin.buffer.read(1) != b"1":
            raise PreparationError("Collector start was not authorized")
        return input_domain(
            Path(__file__).resolve().parents[3],
            output,
            args.installed_root,
            args.wheel,
            source_sha=args.source_sha,
            owned_amd64_editor=True,
        )
    return run_prepared(
        api,
        winreg,
        output,
        lambda: collector(
            output,
            args.installed_root,
            journal,
            wheel=args.wheel,
            source_sha=args.source_sha,
        ),
        journal,
    )


if __name__ == "__main__":
    raise SystemExit(main())
