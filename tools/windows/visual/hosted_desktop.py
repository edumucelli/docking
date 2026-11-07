"""Verify hosted Windows input readiness without changing host setup or policy.

An unprepared, locked or interrupted runner fails acceptance. Provisioning and
host-managed setup must be completed through the supported runner setup process.
Only the owned input collector and its descendants are cleaned up by this tool.
"""

from __future__ import annotations

import argparse
import ctypes as c
import os
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.input_environment import bind, runtime_snapshot

_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
_SETUP_NAMES = frozenset(
    (
        "useroobebroker.exe",
        "wwahost.exe",
        "startmenuexperiencehost.exe",
        "systempropertiesperformance.exe",
        "msiexec.exe",
        "consent.exe",
        "setuphost.exe",
        "wsl.exe",
    )
)


class PreparationError(RuntimeError):
    def __init__(self, operation: str, code: int = 13, *, blockers=()):
        super().__init__(f"{operation} ({code})")
        self.operation, self.code = operation, code
        self.blockers = list(blockers)


def _stamp():
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
    }


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
        or facts["session"] <= 0
    ):
        raise PreparationError(
            "Requires an interactive Windows 11 client on native Arm64", 50
        )


class NativeDesktop:
    def __init__(self):
        self.desktop = WindowsDesktop()
        self.user = self.desktop.user
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.session = self.session_of(os.getpid())

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

    def input_desktop(self):
        handle = bind(self.user, "OpenInputDesktop", _P, _U, _B, _U)(0, False, 1)
        self.check(
            handle, "Read input desktop; complete runner unlock/setup before acceptance"
        )
        try:
            value, needed = c.create_unicode_buffer(256), _U()
            self.check(
                bind(
                    self.user,
                    "GetUserObjectInformationW",
                    _B,
                    _P,
                    c.c_int,
                    _P,
                    _U,
                    c.POINTER(_U),
                )(handle, 2, value, c.sizeof(value), c.byref(needed)),
                "Read input desktop name",
            )
            return value.value
        finally:
            self.check(
                bind(self.user, "CloseDesktop", _B, _P)(handle),
                "Close observed desktop handle",
            )

    def blockers(self):
        import psutil

        blocked = []
        for process in psutil.process_iter(("pid", "name")):
            name = process.info["name"]
            if name is None or name.casefold() not in _SETUP_NAMES:
                continue
            name = name.casefold()
            try:
                if self.session_of(process.pid) != self.session:
                    continue
                if name == "wsl.exe" and any(
                    arg in ("--install", "--update") for arg in process.cmdline()[1:]
                ):
                    blocked.append(
                        {
                            "pid": process.pid,
                            "name": name,
                            "reason": "setup-or-update-running",
                        }
                    )
                    continue
                for window in self.desktop.windows(process.pid, include_immersive=True):
                    if not bind(self.user, "IsIconic", _B, _P)(window["hwnd"]):
                        blocked.append(
                            {"pid": process.pid, "name": name, "class": window["class"]}
                        )
            except psutil.NoSuchProcess:
                continue
            except PreparationError as error:
                if (
                    error.code != 87
                ):  # The observed process exited before session lookup.
                    raise
            except psutil.AccessDenied as error:
                raise PreparationError("Cannot verify runner setup state", 5) from error
        return blocked

    def check_ready(self):
        desktop = self.input_desktop()
        if desktop.casefold() != "default":
            raise PreparationError(
                "Runner input desktop is not ready; complete its normal unlock/setup"
            )
        if not bind(self.user, "GetShellWindow", _P)():
            raise PreparationError(
                "Runner shell is not ready; complete normal runner provisioning"
            )
        blockers = self.blockers()
        if blockers:
            raise PreparationError(
                "Runner setup or update UI blocks input acceptance; "
                "complete supported provisioning",
                blockers=blockers,
            )
        return {"input_desktop": desktop, "shell_ready": True, "blockers": []}

    def preflight(self):
        snapshot = runtime_snapshot()
        desktop = snapshot.get("desktop", {})
        context = snapshot.get("session_context", {})
        if (
            not snapshot.get("available")
            or desktop.get("session") != self.session
            or desktop.get("window_station") != "WinSta0"
            or desktop.get("desktop") != "Default"
            or context.get("wts_connection_state") != 0
        ):
            raise PreparationError("Requires an active interactive runner session")
        readiness = self.check_ready()
        # This diagnostic does not install, update or configure WSL. A missing
        # package must be provisioned by the runner administrator/provider.
        result = subprocess.run(
            [str(Path(os.environ["SYSTEMROOT"]) / "System32" / "wsl.exe"), "--version"],
            capture_output=True,
            timeout=30,
            creationflags=0x08000000,
        )
        if result.returncode:
            raise PreparationError(
                "Runner WSL package is not provisioned; "
                "complete supported setup before acceptance",
                result.returncode,
            )
        return {**readiness, "session_ready": True, "wsl_ready": True}

    def capture(self, path):
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(path)


def _error(error):
    if isinstance(error, PreparationError):
        return {"operation": error.operation, "code": error.code}
    code = error.winerror if sys.platform == "win32" else None
    return {
        "operation": type(error).__name__,
        "code": code if code is not None else error.errno or 0,
    }


@contextmanager
def collector(output, installed, journal, *, wheel, source_sha):
    # The child waits for permission before it can spawn a smoke or fixture.
    # This also contains every descendant if preparation later fails/cancels.
    from docking.platform.native.windows.process_job import ProcessJob

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


def run_prepared(api, output, collect, journal):
    journal.update(
        ok=False,
        mode="verify-only",
        policy_changed=False,
        actions=[],
        blockers=[],
        collector_started=False,
        collector_stopped=True,
        desktop_guard="main-thread",
        readiness_checks=0,
    )

    def save():
        write_shared_json(output / "desktop-preparation.json", journal)

    def observe():
        journal["last_desktop"] = api.check_ready()
        journal["readiness_checks"] += 1

    try:
        api.capture(output / "desktop-preparation-before.png")
        journal["checks"] = api.preflight()
        journal["prepared"] = True
        observe()
        save()
        with collect() as process:
            while process.poll() is None:
                observe()
                save()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.1)
            status = process.returncode
        if not journal["collector_stopped"]:
            raise PreparationError("Owned input collector did not stop", 1460)
        journal["input_status"] = status
        observe()
        api.capture(output / "desktop-preparation-after.png")
        journal["ok"] = status == 0
        return status
    except KeyboardInterrupt:
        journal["cancelled"] = True
        return 130
    except (PreparationError, OSError, subprocess.TimeoutExpired) as error:
        if isinstance(error, PreparationError):
            journal["blockers"] = error.blockers
        journal["error"] = (
            _error(error)
            if isinstance(error, (PreparationError, OSError))
            else {"operation": "Runner prerequisite query timed out", "code": 1460}
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
