"""Measure packaged startup and one bounded idle session, without a speed SLA."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from tools.windows.bundle import filesystem_path, payload_root, walk_tree
from tools.windows.release import (
    ProcessSample,
    SessionSample,
    archive_identity,
    digest,
    summarize_samples,
)
from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.smoke import dock_window

if TYPE_CHECKING:
    import psutil


_ISSUE_LIMIT = 16
_ISSUE_PATH_LIMIT = 512
_ISSUE_HASH_LIMIT = 1024 * 1024


def verify_payload(
    bundle: Path, archive: Path, *, journal: dict[str, object] | None = None
) -> dict[str, str | int]:
    """Keep strict payload verification and retain bounded relative diagnostics."""
    evidence = journal if journal is not None else {}
    issues: list[dict[str, object]] = []
    evidence.update(
        ok=False,
        complete=False,
        issues=issues,
        issue_count=0,
        issue_limit=_ISSUE_LIMIT,
        issues_omitted=0,
    )
    issue_count = 0

    def issue(
        name: str,
        reason: str,
        kind: str,
        *,
        path: Path | None = None,
        size: int | None = None,
        checksum: str | None = None,
        expected: str | None = None,
    ) -> None:
        nonlocal issue_count
        issue_count += 1
        evidence["issue_count"] = issue_count
        evidence["issues_omitted"] = max(0, issue_count - _ISSUE_LIMIT)
        if len(issues) >= _ISSUE_LIMIT:
            return
        row: dict[str, object] = {
            "path": name[:_ISSUE_PATH_LIMIT],
            "path_truncated": len(name) > _ISSUE_PATH_LIMIT,
            "reason": reason,
            "kind": kind,
        }
        issues.append(row)
        if size is not None:
            row["bytes"] = size
        if expected is not None:
            row["expected_sha256"] = expected
        if checksum is not None:
            row["sha256"] = checksum
        elif path is not None and kind == "file":
            # Unknown files are diagnostic only: bound both rows and bytes,
            # never read a link/reparse target or serialize file contents.
            with path.open("rb") as stream:
                content = stream.read(_ISSUE_HASH_LIMIT + 1)
            row["hashed_bytes"] = min(len(content), _ISSUE_HASH_LIMIT)
            if len(content) > _ISSUE_HASH_LIMIT:
                row["sha256_prefix"] = hashlib.sha256(
                    content[:_ISSUE_HASH_LIMIT]
                ).hexdigest()
            else:
                row["sha256"] = hashlib.sha256(content).hexdigest()

    identity = archive_identity(archive)
    # The portable inventory still identifies the expected payload when a
    # declared runtime metadata file is missing; installation roots continue
    # through the shared active-version selector.
    payload = filesystem_path(
        bundle if (bundle / "files.sha256.json").is_file() else payload_root(bundle)
    )
    inventory = payload / "files.sha256.json"
    try:
        state = inventory.lstat()
        if stat.S_ISLNK(state.st_mode) or (
            os.name == "nt"
            and state.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            issue("files.sha256.json", "linked-entry", "symlink")
            raise ValueError("Measured bundle contains a linked inventory")
        if not stat.S_ISREG(state.st_mode):
            issue("files.sha256.json", "non-regular-entry", "other")
            raise ValueError("Measured bundle contains a non-regular inventory")
        inventory_checksum = digest(inventory)
    except FileNotFoundError:
        issue("files.sha256.json", "missing", "missing")
        raise ValueError("Measured bundle is missing files.sha256.json") from None
    evidence["inventory_sha256"] = inventory_checksum
    if inventory_checksum != identity["payload_inventory_sha256"]:
        issue(
            "files.sha256.json",
            "inventory-changed",
            "file",
            checksum=inventory_checksum,
            expected=str(identity["payload_inventory_sha256"]),
        )
        raise ValueError("Measured bundle differs from the portable archive")
    declared = json.loads(inventory.read_bytes())
    evidence["declared_files"] = len(declared)
    observed: dict[str, str] = {}
    verified, bytecode, unlisted = 0, 0, 0
    try:
        for path in walk_tree(payload, follow_reparse=False):
            relative = path.relative_to(payload)
            name = relative.as_posix()
            state = path.lstat()
            reparse = os.name == "nt" and bool(
                state.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
            if stat.S_ISLNK(state.st_mode) or reparse:
                issue(
                    name,
                    "linked-entry",
                    "symlink" if stat.S_ISLNK(state.st_mode) else "reparse",
                )
                continue
            if stat.S_ISDIR(state.st_mode):
                continue
            if not stat.S_ISREG(state.st_mode):
                issue(name, "non-regular-entry", "other")
                continue
            if name == "files.sha256.json":
                continue
            if name in declared:
                checksum = digest(path)
                observed[name] = checksum
                if checksum == declared[name]:
                    verified += 1
                else:
                    issue(
                        name,
                        "changed-payload",
                        "file",
                        size=state.st_size,
                        checksum=checksum,
                        expected=declared[name],
                    )
            elif "__pycache__" in relative.parts and path.suffix == ".pyc":
                # This is the existing generated-bytecode policy; no new
                # config/cache/log/native-library exemption is permitted.
                bytecode += 1
            else:
                unlisted += 1
                issue(name, "unlisted-payload", "file", path=path, size=state.st_size)
        for name in sorted(declared.keys() - observed.keys()):
            issue(name, "missing-payload", "missing", expected=declared[name])
    finally:
        evidence.update(
            verified_files=verified,
            observed_declared_files=len(observed),
            generated_bytecode_files=bytecode,
            unlisted_files=unlisted,
            observed_inventory_sha256=hashlib.sha256(
                json.dumps(observed, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
    evidence["complete"] = True
    if issues:
        first = issues[0]
        reason = {
            "changed-payload": "changed payload bytes",
            "unlisted-payload": "an unlisted payload file",
            "linked-entry": "a symbolic link or reparse point",
            "missing-payload": "a missing payload file",
            "non-regular-entry": "a non-regular payload entry",
        }[str(first["reason"])]
        raise ValueError(f"Measured bundle contains {reason}: {first['path']}")
    evidence["ok"] = True
    return identity


def runtime_environment(output: Path) -> dict[str, str]:
    # The native launcher starts Python in the bundle directory. Mutable XDG
    # paths must stay attached to the caller-owned report directory.
    output = output.resolve()
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("PYTHON", "GTK_", "GDK_", "GI_", "GST_")) or name in {
            "MSYSTEM",
            "MINGW_PREFIX",
            "PYGI_DLL_PATH",
            "XDG_DATA_DIRS",
            "FONTCONFIG_PATH",
        }:
            environment.pop(name)
    environment.update(
        PATH=str(Path(os.environ["WINDIR"]) / "System32"),
        XDG_CONFIG_HOME=str(output / "config"),
        XDG_CACHE_HOME=str(output / "cache"),
        XDG_DATA_HOME=str(output / "data"),
        XDG_STATE_HOME=str(output / "state"),
        DOCKING_LOG_LEVEL="WARNING",
    )
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://" + name}
                    for name in (
                        "applications",
                        "clock",
                        "calendar",
                        "calculator",
                        "hydration",
                    )
                ],
                "hide_mode": "none",
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    return environment


class PackagedSession:
    """Own only the launcher and captured descendant process instances."""

    def __init__(self, bundle: Path, output: Path, desktop: WindowsDesktop):
        import psutil

        self._process_api = psutil
        self.bundle, self.payload, self.output = (
            bundle.resolve(),
            payload_root(bundle),
            output.resolve(),
        )
        self.desktop = desktop
        self.child: subprocess.Popen[bytes] | None = None
        self.owned: dict[tuple[int, float], psutil.Process] = {}
        self.window: int | None = None
        self.application: tuple[int, float] | None = None
        self.started = 0.0
        self.startup: dict[str, float] = {}
        user = desktop.user
        self._send = user.SendMessageTimeoutW
        self._send.restype = ctypes.c_ssize_t
        self._send.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_size_t),
        )
        self._gui = user.GetGuiResources
        self._gui.restype = ctypes.c_uint32
        self._gui.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._open = kernel.OpenProcess
        self._open.restype = ctypes.c_void_p
        self._open.argtypes = (ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32)
        self._close = kernel.CloseHandle
        self._close.restype = ctypes.c_int32
        self._close.argtypes = (ctypes.c_void_p,)

    def remember(self) -> list[tuple[tuple[int, float], psutil.Process]]:
        if self.child is not None and self.child.poll() is None:
            try:
                root = self._process_api.Process(self.child.pid)
                for process in (root, *root.children(recursive=True)):
                    self.owned[(process.pid, process.create_time())] = process
            except self._process_api.NoSuchProcess:
                pass
        live = []
        for key, process in self.owned.items():
            try:
                if process.is_running() and process.create_time() == key[1]:
                    live.append((key, process))
            except self._process_api.NoSuchProcess:
                continue
        return live

    def responsive(self) -> bool:
        if self.application is None or self.window is None:
            return False
        process = self.owned[self.application]
        try:
            if not process.is_running() or process.create_time() != self.application[1]:
                return False
        except self._process_api.NoSuchProcess:
            return False
        if not any(
            row["hwnd"] == self.window for row in self.desktop.windows(process.pid)
        ):
            return False
        result = ctypes.c_size_t()
        return bool(self._send(self.window, 0, 0, 0, 2, 200, ctypes.byref(result)))

    def start(self) -> dict[str, float]:
        environment = runtime_environment(self.output)
        with (self.output / "docking.log").open("w", encoding="utf-8") as log:
            self.started = time.monotonic()
            self.child = subprocess.Popen(
                [str(self.bundle / "Docking.exe")],
                cwd=self.output,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        until = self.started + 40
        interpreter = self.payload / "runtime/bin/pythonw.exe"
        while time.monotonic() < until:
            if self.child.poll() is not None:
                raise RuntimeError("Packaged launcher exited before readiness")
            for key, process in self.remember():
                try:
                    if not Path(process.exe()).samefile(interpreter):
                        continue
                except (FileNotFoundError, self._process_api.NoSuchProcess):
                    continue
                row = dock_window(self.desktop.windows(process.pid))
                if row is not None:
                    self.window, self.application = row["hwnd"], key
                    self.startup.setdefault(
                        "visible_seconds", time.monotonic() - self.started
                    )
                    if self.responsive():
                        self.startup["responsive_seconds"] = (
                            time.monotonic() - self.started
                        )
                        return dict(self.startup)
            time.sleep(0.05)
        raise TimeoutError("Packaged dock did not become responsive")

    def sample(self, elapsed: float) -> SessionSample:
        if self.child is None or self.child.poll() is not None or not self.responsive():
            raise RuntimeError("Packaged dock stopped responding during the session")
        rows: list[ProcessSample] = []
        for key, process in self.remember():
            try:
                memory, cpu = process.memory_info(), process.cpu_times()
                handle = self._open(0x1000, False, process.pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    counts = []
                    for flag in (0, 1):
                        ctypes.set_last_error(0)
                        count = self._gui(handle, flag)
                        error = ctypes.get_last_error()
                        if not count and error:
                            raise ctypes.WinError(error)
                        counts.append(count)
                finally:
                    if not self._close(handle):
                        raise ctypes.WinError(ctypes.get_last_error())
                rows.append(
                    {
                        "pid": key[0],
                        "created": key[1],
                        "rss_bytes": memory.rss,
                        "private_bytes": memory.private,
                        "cpu_seconds": cpu.user + cpu.system,
                        "threads": process.num_threads(),
                        "handles": process.num_handles(),
                        "gdi": counts[0],
                        "user": counts[1],
                    }
                )
            except self._process_api.NoSuchProcess:
                continue
        return {
            "elapsed_seconds": elapsed,
            "responsive": True,
            "processes": rows,
            "rss_bytes": sum(row["rss_bytes"] for row in rows),
            "private_bytes": sum(row["private_bytes"] for row in rows),
            "threads": sum(row["threads"] for row in rows),
            "handles": sum(row["handles"] for row in rows),
            "gdi": sum(row["gdi"] for row in rows),
            "user": sum(row["user"] for row in rows),
        }

    def stop(self) -> dict[str, object]:
        forced, errors, close_requested = False, [], False
        if self.child is not None and self.child.poll() is None:
            try:
                if self.window is not None and self.responsive():
                    close_requested = bool(
                        self.desktop.user.PostMessageW(self.window, 0x10, 0, 0)
                    )
            except (OSError, self._process_api.Error) as error:
                errors.append(type(error).__name__)
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                forced = True
        try:
            live = self.remember()
        except (OSError, self._process_api.Error) as error:
            errors.append(type(error).__name__)
            live = list(self.owned.items())
        if live:
            until = time.monotonic() + 3
            while live and time.monotonic() < until:
                time.sleep(0.05)
                try:
                    live = self.remember()
                except (OSError, self._process_api.Error) as error:
                    errors.append(type(error).__name__)
                    break
        for key, process in reversed(live):
            try:
                if not process.is_running() or process.create_time() != key[1]:
                    continue
                forced = True
                process.terminate()
                try:
                    process.wait(timeout=3)
                except self._process_api.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            except self._process_api.NoSuchProcess:
                continue
            except self._process_api.Error as error:
                errors.append(type(error).__name__)
        if self.child is not None and self.child.poll() is None:
            forced = True
            try:
                self.child.kill()
                self.child.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(type(error).__name__)
        try:
            remaining = [key[0] for key, _process in self.remember()]
        except (OSError, self._process_api.Error) as error:
            errors.append(type(error).__name__)
            remaining = [key[0] for key in self.owned]
        return {
            "normal_shutdown": close_requested
            and self.child is not None
            and self.child.returncode == 0
            and not forced,
            "close_requested": close_requested,
            "exit_code": self.child.returncode if self.child is not None else None,
            "forced": forced,
            "owned_remaining": remaining,
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an interactive native Windows desktop")
    if not (
        1 <= args.cycles <= 10
        and 5 <= args.seconds <= 3600
        and 0.1 <= args.interval <= args.seconds / 2
    ):
        parser.error(
            "Use 1–10 cycles, 5–3600 seconds and an interval "
            "producing at least 3 samples"
        )
    args.bundle = args.bundle.resolve()
    args.output = args.output.resolve()
    if args.output.is_relative_to(args.bundle):
        parser.error("Measurement output must be outside the bundle")
    args.output.mkdir(parents=True, exist_ok=False)
    startups: list[dict[str, object]] = []
    session: dict[str, object] = {}
    report: dict[str, object] = {
        "ok": False,
        "startups": startups,
        "session": session,
        "measurement": {
            "requested_seconds": args.seconds,
            "interval_seconds": args.interval,
            "startup_cycles": args.cycles,
            "cache_state": "uncontrolled-after-payload-verification",
            "workload": "idle applications/clock/calendar/calculator/hydration",
            "limits": "bounded session; cold boot/login/all-day behavior not measured",
        },
    }
    active = None
    payload_verification: dict[str, object] = {"schema": 1}
    report["payload_verification"] = payload_verification
    before: dict[str, object] = {}
    payload_verification["before"] = before
    try:
        bundle = args.bundle.resolve()
        identity = verify_payload(bundle, args.archive.resolve(), journal=before)
        report["acceptance"] = {
            "schema": 1,
            "implementation": "portable-bundle",
            **identity,
        }
        report["launcher_sha256"] = digest(bundle / "Docking.exe")
        desktop = WindowsDesktop()
        if dock_window(desktop.windows()) is not None:
            raise RuntimeError(
                "Preserve the existing Docking instance before acceptance"
            )
        for index in range(args.cycles):
            output = args.output / f"startup-{index + 1}"
            output.mkdir()
            active = PackagedSession(bundle, output, desktop)
            row: dict[str, object] = {}
            startups.append(row)
            try:
                row.update(active.start())
            finally:
                row.update(active.stop())
            if not row["normal_shutdown"] or row["owned_remaining"] or row["errors"]:
                raise RuntimeError("Startup cycle did not release its owned runtime")
            active = None
        output = args.output / "session"
        output.mkdir()
        active = PackagedSession(bundle, output, desktop)
        samples: list[SessionSample] = []
        session["samples"] = samples
        try:
            session["startup"] = active.start()
            started = time.monotonic()
            while True:
                elapsed = time.monotonic() - started
                samples.append(active.sample(elapsed))
                write_shared_json(args.output / "result.json", report)
                if elapsed >= args.seconds:
                    break
                time.sleep(min(args.interval, args.seconds - elapsed))
            session["summary"] = summarize_samples(samples)
        finally:
            session.update(active.stop())
        if (
            not session["normal_shutdown"]
            or session["owned_remaining"]
            or session["errors"]
        ):
            raise RuntimeError("Session did not release its owned runtime")
        active = None
        after: dict[str, object] = {}
        payload_verification["after"] = after
        report["ok"] = (
            verify_payload(bundle, args.archive.resolve(), journal=after) == identity
        )
    except BaseException as error:
        report["error"] = type(error).__name__
        raise
    finally:
        try:
            if active is not None:
                report["failure_cleanup"] = active.stop()
        finally:
            write_shared_json(args.output / "result.json", report)


if __name__ == "__main__":
    main()
