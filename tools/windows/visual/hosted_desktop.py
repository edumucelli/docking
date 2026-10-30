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
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.input_environment import bind

_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\OOBE"
_VALUE = "DisablePrivacyExperience"
_CLOUD = "Microsoft.Windows.CloudExperienceHost_cw5n1h2txyewy"
_START = "Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"
_TERMINAL = "Microsoft.WindowsTerminal_8wekyb3d8bbwe"
_NAMES = frozenset(
    (
        "useroobebroker.exe",
        "wwahost.exe",
        "startmenuexperiencehost.exe",
        "systempropertiesperformance.exe",
        "windowsterminal.exe",
    )
)


class PreparationError(RuntimeError):
    def __init__(self, operation: str, code: int = 13):
        super().__init__(f"{operation} ({code})")
        self.operation, self.code = operation, code


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

    def journal(self):
        return {
            "pid": self.pid,
            "session": self.session,
            "created": self.created,
            "name": self.name,
            "family": self.family,
            "package_error": self.package_error,
        }


def action_for(identity, window, *, root, session, foreground):
    """Authorize narrowly identified first-login UI; never infer from a PID/name."""
    if identity.session != session:
        return None
    if PureWindowsPath(identity.path).name.casefold() != identity.name:
        raise PreparationError("Candidate process name differs from its image path")
    system = PureWindowsPath(root) / "System32"
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
    if window is None or window["minimized"] or window["cloaked"]:
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
                times = [_FileTime() for _ in range(4)]
                self.check(
                    bind(
                        self.kernel,
                        "GetProcessTimes",
                        _B,
                        _P,
                        *([c.POINTER(_FileTime)] * 4),
                    )(handle, *(c.byref(value) for value in times)),
                    "GetProcessTimes",
                )
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
                rows.append(
                    Identity(
                        handle,
                        process.pid,
                        self.session,
                        times[0].low | (times[0].high << 32),
                        name,
                        path.value,
                        family,
                        error,
                    )
                )
            yield rows

    def windows(self, pid):
        rows = self.desktop.windows(pid)
        for row in rows:
            cloaked = _U()
            status = bind(self.dwm, "DwmGetWindowAttribute", c.c_int32, _P, _U, _P, _U)(
                row["hwnd"], 14, c.byref(cloaked), c.sizeof(cloaked)
            )
            if status < 0:
                raise PreparationError("DwmGetWindowAttribute", status & 0xFFFFFFFF)
            row["cloaked"] = bool(cloaked.value)
            row["minimized"] = bool(bind(self.user, "IsIconic", _B, _P)(row["hwnd"]))
        return rows

    def foreground(self):
        return self.user.GetForegroundWindow()

    def perform(self, identity, action, window):
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
            bind(self.user, "ShowWindow", _B, _P, c.c_int32)(
                hwnd, 7
            )  # SW_SHOWMINNOACTIVE.
        self.desktop.wait_for(
            lambda: (
                not any(
                    row["hwnd"] == hwnd and not row["cloaked"] and not row["minimized"]
                    for row in self.windows(identity.pid)
                )
            ),
            "First-login window was not dismissed",
            2,
        )
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


def sweep(api, journal):
    count = 0
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
        # Retain the complete known inventory before a rejected classification
        # or an action can obscure the other first-login UI identities.
        for identity, windows in inventory:
            for window in windows or [None]:
                action = action_for(
                    identity,
                    window,
                    root=api.root,
                    session=api.session,
                    foreground=api.foreground(),
                )
                if action is None:
                    continue
                entry = {**identity.journal(), "action": action, "before": "identified"}
                journal["actions"].append(entry)
                entry["after"] = api.perform(identity, action, window) or "dismissed"
                entry["foreground_after"] = api.foreground()
                count += 1
                if action == "terminate":
                    break
    return count


def _error(error):
    if isinstance(error, PreparationError):
        return {"operation": error.operation, "code": error.code}
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
def collector(output, installed, journal):
    # The child waits for permission before it can spawn a smoke or fixture.
    # This also contains every descendant if preparation later fails/cancels.
    from docking.platform.backends.windows.process_job import ProcessJob

    job, process = ProcessJob(), None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.windows.visual.hosted_desktop",
                "--collect",
                "--installed-root",
                str(installed.resolve()),
                "--output",
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[3],
            stdin=subprocess.PIPE,
        )
        journal["collector_started"] = True
        journal["collector_stopped"] = False
        job.assign(int(process._handle))
        if process.stdin is None:
            raise PreparationError("Collector permission pipe is unavailable")
        process.stdin.write(b"1")
        process.stdin.close()
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
            for _ in range(4):
                if not sweep(api, journal):
                    break
            else:
                raise PreparationError("First-login desktop did not settle", 1460)
            api.capture(output / "desktop-preparation-ready.png")
            journal["prepared"] = True
            save()
            with collect() as process:
                while process.poll() is None:
                    sweep(api, journal)
                    save()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=0.5)
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
            owned_amd64_editor=True,
        )
    return run_prepared(
        api,
        winreg,
        output,
        lambda: collector(output, args.installed_root, journal),
        journal,
    )


if __name__ == "__main__":
    raise SystemExit(main())
