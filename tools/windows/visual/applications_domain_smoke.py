"""Exercise owned Windows application documents, associations and launcher export."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import traceback
import uuid
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path, PureWindowsPath

CHECKS = (
    "documents-installed",
    "documents-generated",
    "documents-selected",
    "association-default",
    "association-explicit",
    "association-absence",
    "selected-launcher-export",
    "inventory-changes",
    "explorer-recovery",
)


def failure_details(error: Exception) -> dict[str, object]:
    return {
        "class": type(error).__name__,
        "errno": error.errno if isinstance(error, OSError) else None,
        "frames": [
            {
                "file": Path(frame.filename).name,
                "line": frame.lineno,
                "function": frame.name,
            }
            for frame in traceback.extract_tb(error.__traceback__)[-6:]
        ],
    }


def cleanup_snapshot(native, processes, owned_images, before_windows, baseline, shell):
    """Retain every existing cleanup guard's facts without changing its policy."""
    workarea, actual_shell = native.workarea(), native.explorer()
    owned = [
        process.pid
        for process in processes
        if process.info["exe"] and PureWindowsPath(process.info["exe"]) in owned_images
    ]
    windows = [
        {key: window[key] for key in ("hwnd", "pid", "class")}
        for window in native.windows()
        if window["hwnd"] not in before_windows
        and not (
            window["pid"] == shell["pid"]
            and window["class"]
            in {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
        )
    ]
    return {
        "workarea": workarea,
        "workarea_matches": workarea == baseline,
        "shell": actual_shell,
        "shell_matches": actual_shell == shell,
        "owned_pids": owned,
        "new_windows": windows,
        "safe": workarea == baseline
        and actual_shell == shell
        and not owned
        and not windows,
    }


def association_executable(query, extension: str, observed: dict[str, object]):
    """Read both AssocQueryString buffers, excluding the Unknown fallback."""
    import ctypes

    missing = {0x80070002, 0x80070003, 0x80070483}
    size = ctypes.c_uint32()
    # ASSOCF_INIT_IGNOREUNKNOWN: the SDK explicitly excludes the Unknown ProgID.
    flags = 0x400
    calls = []
    observed.update(flags=flags, calls=calls)
    status = query(flags, 2, extension, "open", None, ctypes.byref(size)) & 0xFFFFFFFF
    calls.append({"phase": "size", "hresult": status, "characters": size.value})
    if status in missing:
        return None
    if status not in (0, 1):
        raise OSError(status, "AssocQueryStringW size query failed")
    if not 0 < size.value <= 32768:
        raise ValueError("AssocQueryStringW returned an invalid buffer size")
    value = ctypes.create_unicode_buffer(size.value)
    status = query(flags, 2, extension, "open", value, ctypes.byref(size)) & 0xFFFFFFFF
    calls.append({"phase": "value", "hresult": status, "characters": size.value})
    if status in missing:
        return None
    if status != 0:
        raise OSError(status, "AssocQueryStringW value query failed")
    if not value.value:
        raise ValueError("AssocQueryStringW returned an empty executable")
    return value.value


def association_parent_state(registry):
    """Record parent presence/counts without recording unrelated user values."""
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER, r"Software\RegisteredApplications"
        ) as key:
            subkeys, values, _modified = registry.QueryInfoKey(key)
            return {"exists": True, "subkeys": subkeys, "values": values}
    except FileNotFoundError:
        return {"exists": False, "subkeys": 0, "values": 0}


@contextmanager
def association_registration(registry, extension, program, executable, lifecycle):
    """Register one owned association-only application, without changing defaults."""
    base = "Software\\Classes\\"
    capabilities = base + program + "\\Capabilities"
    registered = "Software\\RegisteredApplications"
    keys = (
        base + extension,
        base + program,
        base + program + "\\shell",
        base + program + "\\shell\\open",
        base + program + "\\shell\\open\\command",
        capabilities,
        capabilities + "\\FileAssociations",
    )
    lifecycle["association_restored"] = False
    parents = lifecycle["registration_parent"] = {
        "before": association_parent_state(registry)
    }
    for name in keys:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, name):
                raise RuntimeError("Unique association fixture unexpectedly exists")
        except FileNotFoundError:
            pass
    parent_existed = True
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, registered) as key:
            try:
                registry.QueryValueEx(key, program)
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError(
                    "Unique application registration unexpectedly exists"
                )
    except FileNotFoundError:
        parent_existed = False
    values = {
        keys[0]: {"": program},
        keys[1]: {"": "Docking owned document"},
        keys[4]: {"": '"' + str(executable).replace("/", "\\") + '" "%1"'},
        capabilities: {
            "ApplicationName": program,
            "ApplicationDescription": "Docking owned acceptance application",
        },
        keys[-1]: {extension: program},
    }
    created, value_attempted = [], False
    try:
        for name in keys:
            with registry.CreateKeyEx(
                registry.HKEY_CURRENT_USER, name, 0, registry.KEY_SET_VALUE
            ) as key:
                created.append(name)
                for field, value in values.get(name, {}).items():
                    registry.SetValueEx(key, field, 0, registry.REG_SZ, value)
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER, registered, 0, registry.KEY_SET_VALUE
        ) as key:
            value_attempted = True
            registry.SetValueEx(key, program, 0, registry.REG_SZ, capabilities)
        parents["registered"] = association_parent_state(registry)
        yield
    finally:
        if value_attempted:
            with (
                registry.OpenKey(
                    registry.HKEY_CURRENT_USER, registered, 0, registry.KEY_SET_VALUE
                ) as key,
                suppress(FileNotFoundError),
            ):
                registry.DeleteValue(key, program)
        for name in reversed(created):
            registry.DeleteKey(registry.HKEY_CURRENT_USER, name)
        if not parent_existed:
            try:
                with registry.OpenKey(registry.HKEY_CURRENT_USER, registered) as key:
                    subkeys, fields, _modified = registry.QueryInfoKey(key)
                if subkeys == fields == 0:
                    registry.DeleteKey(registry.HKEY_CURRENT_USER, registered)
            except FileNotFoundError:
                pass
        parents["after"] = association_parent_state(registry)
        lifecycle["association_restored"] = True


def record_check(checks, name, operation, *, cleanup_verified) -> bool:
    """Continue independent phases only after verifying their owned cleanup."""
    try:
        operation()
        checks[name].update(passed=True, status="passed")
    except Exception as error:
        checks[name].update(
            passed=False,
            status="failed",
            error=failure_details(error),
        )
    try:
        safe = cleanup_verified()
    except Exception as error:
        checks[name]["cleanup_error"] = failure_details(error)
        safe = False
    if not safe:
        checks[name].update(passed=False, status="failed", cleanup_verified=False)
    return safe


def phase_command(
    output: Path, *, installed_root: Path | None, wheel: Path | None
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "tools.windows.visual.runtime_pin_smoke",
        "--output",
        str(output),
        "--phase",
        "documents",
    ]
    if installed_root is not None:
        command.extend(("--installed-root", str(installed_root)))
    if wheel is not None:
        command.extend(("--wheel", str(wheel)))
    return command


def validate_document_phase(report: dict, route: str) -> None:
    if report.get("ok") is not True or report.get("normal_shutdown") is not True:
        raise ValueError("Application document phase did not finish normally")
    rows = report.get("document_drops")
    if not isinstance(rows, list) or [row.get("count") for row in rows] != [1, 2]:
        raise ValueError("Missing one-file or multiple-file physical drop")
    if report.get("physical_document_input") is not True or any(
        row.get("protocol") != "ole2" for row in rows
    ):
        raise ValueError("Selected files were not delivered by the native drag loop")
    if any(row.get("desktop_id") != report.get("id") for row in rows):
        raise ValueError("Document launch grouped under another application")
    if route not in ("installed", "generated", "selected"):
        raise ValueError("Unknown selected-document route")


def finish_program(
    native,
    facts_path: Path,
    executable: Path,
    argv: list[str],
    *,
    lifecycle=None,
    observed: dict[str, object] | None = None,
    file_arguments: tuple[int, ...] = (),
) -> dict:
    import psutil

    from tools.windows.visual.desktop_driver import read_shared_json

    native.wait_for(
        facts_path.is_file, "Owned application did not report its arguments", 30
    )
    facts = read_shared_json(facts_path)
    if observed is not None:
        observed["facts"] = facts
    assert Path(facts["executable"]).samefile(executable)
    process = psutil.Process(facts["pid"])
    assert Path(process.exe()).samefile(executable)
    try:
        actual, expected = list(facts["argv"]), list(argv)
        assert len(actual) == len(expected), "Owned application changed argument count"
        for index in file_arguments:
            assert 0 <= index < len(expected)
            # Gio's Windows URI expansion returns GFile's native path, which
            # uses forward separators on MSYS2. Preserve all other characters.
            actual[index] = actual[index].replace("/", "\\")
            expected[index] = expected[index].replace("/", "\\")
        assert actual == expected, "Owned application changed document arguments"
        native.wait_for(
            lambda: native.windows(process.pid), "Owned application did not map"
        )
    finally:
        for window in native.windows(process.pid):
            assert native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
        assert process.wait(20) == 0
        if lifecycle is not None:
            lifecycle["closed"] = True
    facts_path.unlink()
    return facts


def export_check(
    native,
    fixture,
    output: Path,
    selected: Path,
    identifier: str,
    argv: list[str],
    *,
    expected_cwd: Path,
    lifecycle,
    installed_root: Path | None,
    wheel: Path | None,
    observed: dict[str, object],
) -> dict:
    from tools.windows.visual.desktop_driver import read_shared_json
    from tools.windows.visual.explorer import ExplorerFolder

    directory = fixture.root / "Copied launcher résumé 日本語"
    directory.mkdir()
    case = output / "selected-export"
    command = [
        sys.executable,
        "-m",
        "tools.windows.visual.applications_smoke",
        "--output",
        str(case),
        "--application-id",
        identifier,
        "--application-name",
        fixture.labels["Second"],
        "--launcher-source",
        str(selected),
        "--explorer-export",
        "--export-directory",
        str(directory),
    ]
    if installed_root is not None:
        command.extend(("--installed-root", str(installed_root)))
    if wheel is not None:
        command.extend(("--wheel", str(wheel)))
    lifecycle["closed"] = False
    observed["stage"] = "child-export"
    with (output / "selected-export.log").open("wb") as log:
        subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240
        )
    child = read_shared_json(case / "result.json")
    lifecycle["closed"] = child.get("normal_shutdown") is True
    observed["child_shutdown"] = lifecycle["closed"]
    assert child["ok"] and child["normal_shutdown"]
    observed["stage"] = "copied-launcher"
    copied = Path(child["export"]["path"])
    original, exported = selected.read_bytes(), copied.read_bytes()
    observed.update(
        same_directory=copied.parent.samefile(directory),
        binary_preserved=exported == original,
        source_bytes=len(original),
        copied_bytes=len(exported),
        source_sha256=hashlib.sha256(original).hexdigest(),
        copied_sha256=hashlib.sha256(exported).hexdigest(),
        source_cache_removed=not Path(child["export"]["source"]).exists(),
    )
    assert observed["same_directory"], "Copied launcher used another directory"
    assert observed["binary_preserved"], "Copied launcher changed the selected options"
    assert observed["source_cache_removed"], (
        "Source export cache survived child cleanup"
    )
    facts_path = output / "facts-Second-documents.json"
    facts_path.unlink(missing_ok=True)
    observed["stage"] = "independent-explorer"
    with ExplorerFolder(native, directory) as folder:
        lifecycle["closed"] = False
        observed["stage"] = "independent-launch"
        try:
            folder.activate(copied, observed.setdefault("explorer_activation", {}))
            facts = finish_program(
                native,
                facts_path,
                fixture.executables["Second"],
                argv,
                lifecycle=lifecycle,
                observed=observed,
            )
        except Exception:
            if not observed["explorer_activation"].get("physical_enter_started"):
                # A rejected selection cannot have started the PE. The folder
                # context and outer native-window guard still verify cleanup.
                lifecycle["closed"] = True
            from PIL import ImageGrab

            try:
                ImageGrab.grab(all_screens=True).save(
                    output / "independent-launch-failure.png"
                )
                observed["failure_capture"] = "independent-launch-failure.png"
            except OSError as error:
                observed["capture_error"] = failure_details(error)
            raise
        observed["stage"] = "launched-working-directory"
        observed["working_directory_preserved"] = Path(facts["cwd"]).samefile(
            expected_cwd
        )
        assert observed["working_directory_preserved"], (
            "Independent launcher changed working directory"
        )
    return {
        "passed": True,
        "actual_explorer_copy": True,
        "independent_explorer_launch": True,
        "source_cache_removed": True,
        "binary_preserved": True,
        "facts": facts,
    }


def association_changed():
    """Invalidate Shell association caches after owned registration changes."""
    import ctypes

    from docking.platform.backends.windows.ffi import bind

    bind(
        ctypes.WinDLL("shell32", use_last_error=True),
        "SHChangeNotify",
        None,
        ctypes.c_int32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )(0x08000000, 0, None, None)


def _registered_association_checks(
    native,
    fixture,
    output: Path,
    report: dict,
    *,
    mode: str,
    lifecycle,
    token: str | None = None,
    borrowed_registration: bool = False,
) -> dict:
    """Use an entirely new extension/ProgID; never change an existing default."""
    import ctypes
    import winreg

    from docking.platform.applications.identity import LaunchProvenanceStore
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.targets import open_target

    token = token or uuid.uuid4().hex
    extension, program = ".docking-" + token, "Docking.Acceptance." + token
    expected_id = "HKEY_CURRENT_USER\\Software\\Classes\\" + program
    executable = fixture.executables["Other"]
    facts_path = output / "facts-Other-documents.json"

    def completed():
        return finish_program(
            native,
            facts_path,
            executable,
            [str(document).replace("/", "\\")],
            lifecycle=lifecycle,
            file_arguments=(0,) if mode == "explicit" else (),
        )

    document = fixture.root / ("Association résumé 日本語" + extension)
    document.write_text("Owned association document", encoding="utf-8")
    report["associations_restored"] = False
    try:
        with (
            nullcontext()
            if borrowed_registration
            else association_registration(
                winreg, extension, program, executable, lifecycle
            )
        ):
            association_changed()
            query = bind(
                ctypes.WinDLL("shlwapi", use_last_error=True),
                "AssocQueryStringW",
                ctypes.c_int32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint32),
            )
            observed = report["checks"]["association-" + mode].setdefault(
                "observed", {}
            )
            observed["expected_id"] = expected_id

            def oracle():
                return association_executable(
                    query, extension, observed.setdefault("native_query", {})
                )

            resolved = native.wait_for(
                oracle, "Independent Shell association did not appear"
            )
            assert Path(resolved).samefile(executable)
            if mode == "default":
                facts_path.unlink(missing_ok=True)
                lifecycle["closed"] = False
                assert open_target(document.as_uri())
                return {"passed": True, "facts": completed()}
            registry = ApplicationRegistry()
            alertable = bind(
                ctypes.WinDLL("kernel32", use_last_error=True),
                "SleepEx",
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_int32,
            )

            def refresh():
                # Gio's Win32 registry watch uses APCs on its initializing
                # thread. This controller has no GTK/GLib main loop to service
                # them; enter an alertable state before reading its cache.
                status = observed["alertable_status"] = alertable(0, True)
                assert status in (0, 0xC0), "Unexpected SleepEx result"
                registry.refresh()

            refresh()
            if mode == "absence":
                absent = ".docking-absent-" + token
                evidence = observed.setdefault("native_absence_query", {})
                observed["native_absent"] = (
                    association_executable(query, absent, evidence) is None
                )
                observed["native_absence_status"] = evidence["calls"][-1]["hresult"]
                assert observed["native_absent"], (
                    "Native Shell did not confirm the absent association"
                )
                observed["absent"] = registry.default_for_content_type(absent) is None
                assert observed["absent"], (
                    "Gio returned a handler for an absent association"
                )
                return {"passed": True, "lookup_only": True}

            def retained_handler():
                refresh()
                selected = registry.default_for_content_type(extension)
                observed.update(
                    resolved=selected is not None,
                    registered=selected is not None
                    and registry.get(selected.desktop_id) is not None,
                    has_gio_source=bool(selected and selected.has_gio_source),
                    canonical_id=selected.desktop_id if selected else None,
                    id_matches=bool(
                        selected
                        and selected.desktop_id.casefold() == expected_id.casefold()
                    ),
                )
                return selected if observed["registered"] else None

            selected = native.wait_for(
                retained_handler, "Registered Gio association client did not resolve"
            )
            assert observed["id_matches"], (
                "Association selected another registered application"
            )
            assert selected.has_gio_source, "Association lost its retained Gio handle"
            assert registry._gio_handle_for(selected.desktop_id) is not None, (
                "Registered association has no launch handle"
            )
            if mode == "explicit":
                facts_path.unlink(missing_ok=True)
                lifecycle["closed"] = False
                assert ApplicationLauncher(
                    registry, LaunchProvenanceStore()
                ).launch_app_uris(selected.desktop_id, [document.as_uri()])
                return {"passed": True, "facts": completed()}
            raise ValueError("Unknown association acceptance mode")
    finally:
        association_changed()
        report["associations_restored"] = lifecycle["association_restored"]


def _association_child(request_path: str) -> None:
    """Fresh Gio lookup with its parent already registered; close before exit."""
    import struct
    import winreg
    from types import SimpleNamespace

    from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
    from tools.windows.visual.tray_acceptance import runtime_origins, select_runtime

    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    mode = request["mode"]
    report = {"ok": False, "pid": os.getpid(), "checks": {"association-" + mode: {}}}
    lifecycle = {"closed": True, "association_restored": False}
    try:
        assert not any(
            name == "docking" or name.startswith("gi.repository.Gio")
            for name in sys.modules
        )
        report["registration_parent_before_gio"] = association_parent_state(winreg)
        assert report["registration_parent_before_gio"]["exists"]
        assert struct.calcsize("P") * 8 == request["pointer_bits"]
        runtime = select_runtime(
            Path(request["installed_root"]) if request["installed_root"] else None,
            Path(request["wheel"]) if request["wheel"] else None,
        )
        assert runtime.wheel_sha256 == request["wheel_sha256"]
        import docking
        from docking.platform.applications import launcher, registry

        report["runtime"] = {
            "wheel_sha256": runtime.wheel_sha256,
            "origins": runtime_origins(
                runtime.root,
                {
                    "docking": docking.__file__,
                    "launcher": launcher.__file__,
                    "registry": registry.__file__,
                },
            ),
        }
        fixture = SimpleNamespace(
            root=Path(request["fixture_root"]),
            executables={"Other": Path(request["executable"])},
        )
        result = _registered_association_checks(
            WindowsDesktop(),
            fixture,
            Path(request["output"]),
            report,
            mode=mode,
            lifecycle=lifecycle,
            token=request["token"],
            borrowed_registration=True,
        )
        report.update(result, ok=True)
    except Exception as error:
        report["error"] = failure_details(error)
    finally:
        report["normal_shutdown"] = lifecycle["closed"]
        write_shared_json(Path(request["result"]), report)
    raise SystemExit(0 if report["ok"] and report["normal_shutdown"] else 1)


def association_checks(
    native,
    fixture,
    output: Path,
    report,
    *,
    mode: str,
    lifecycle,
    installed_root: Path | None = None,
    wheel: Path | None = None,
):
    if mode == "default":
        return _registered_association_checks(
            native, fixture, output, report, mode=mode, lifecycle=lifecycle
        )
    import struct
    import winreg

    from docking.platform.backends.windows.process_job import ProcessJob
    from tools.windows.visual.desktop_driver import read_shared_json, write_shared_json

    token = uuid.uuid4().hex
    name = "association-" + mode
    observed = report["checks"][name].setdefault("observed", {})
    request_path, result_path = (
        output / (name + "-request.json"),
        output / (name + "-result.json"),
    )
    request = {
        "mode": mode,
        "token": token,
        "fixture_root": str(fixture.root.resolve()),
        "executable": str(fixture.executables["Other"].resolve()),
        "output": str(output.resolve()),
        "result": str(result_path.resolve()),
        "installed_root": str(installed_root.resolve()) if installed_root else None,
        "wheel": str(wheel.resolve()) if wheel else None,
        "wheel_sha256": report["runtime"]["wheel_sha256"],
        "pointer_bits": struct.calcsize("P") * 8,
    }
    report["associations_restored"] = False
    try:
        with association_registration(
            winreg,
            ".docking-" + token,
            "Docking.Acceptance." + token,
            fixture.executables["Other"],
            lifecycle,
        ):
            observed["registration_parent"] = lifecycle["registration_parent"]
            write_shared_json(request_path, request)
            job, process = ProcessJob(), None
            lifecycle["closed"] = False
            try:
                source = str(Path(__file__).resolve().parents[3])
                code = (
                    "import sys; assert sys.stdin.buffer.read(1) == b'1'; "
                    "sys.path.insert(0, sys.argv[1]); "
                    "from tools.windows.visual.applications_domain_smoke "
                    "import _association_child; "
                    "_association_child(sys.argv[2])"
                )
                with (output / (name + "-child.log")).open("wb") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-I",
                            "-c",
                            code,
                            source,
                            str(request_path.resolve()),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=0x08000000 if sys.platform == "win32" else 0,
                    )
                    observed["child_pid"] = process.pid
                    job.assign(int(process._handle))
                    process.communicate(b"1", timeout=65)
                child = read_shared_json(result_path)
                observed["child"] = child
                assert child["pid"] == process.pid
                lifecycle["closed"] = child["normal_shutdown"] is True
                assert (
                    process.returncode == 0 and child["ok"] and lifecycle["closed"]
                ), "Fresh association child failed"
                assert child["runtime"]["wheel_sha256"] == request["wheel_sha256"]
                assert child["registration_parent_before_gio"]["exists"]
                return {
                    "passed": True,
                    **(
                        {"facts": child["facts"]}
                        if mode == "explicit"
                        else {"lookup_only": True}
                    ),
                }
            finally:
                active_error = sys.exc_info()[0] is not None
                try:
                    try:
                        if process is not None and process.stdin is not None:
                            process.stdin.close()
                    finally:
                        try:
                            job.close()
                        finally:
                            if process is not None:
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    observed["child_forced_cleanup"] = True
                                    process.kill()
                                    process.wait(timeout=5)
                                    raise
                except (OSError, subprocess.TimeoutExpired) as error:
                    observed["child_cleanup_error"] = failure_details(error)
                    lifecycle["closed"] = False
                    if not active_error:
                        raise
                finally:
                    observed["child_stopped"] = (
                        process is None or process.poll() is not None
                    )
                    observed["child_returncode"] = (
                        process.returncode if process is not None else None
                    )
    finally:
        report["associations_restored"] = lifecycle["association_restored"]
        observed["registration_parent"] = lifecycle["registration_parent"]
        association_changed()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--restart-explorer",
        action="store_true",
        help="Include exact-shell replacement on this owned test desktop",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned native Windows desktop")
    from tools.windows.visual.tray_acceptance import runtime_origins, select_runtime

    runtime = select_runtime(args.installed_root, args.wheel)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "controller-config")
    os.environ["XDG_CACHE_HOME"] = str(output / "controller-cache")

    import docking
    from docking.platform.applications import launcher
    from docking.platform.applications.windows.registration import (
        WindowsExecutableRegistration,
    )
    from docking.platform.backends.windows import catalog, documents
    from tools.windows.visual.classic_fixture import ClassicApplications, write_shortcut
    from tools.windows.visual.desktop_driver import (
        WindowsDesktop,
        read_shared_json,
        write_shared_json,
    )

    report = {
        "schema": 1,
        "ok": False,
        "checks": {name: {"passed": False, "status": "pending"} for name in CHECKS},
        "normal_shutdown": False,
        "fixture_cleanup": False,
        "associations_restored": False,
        "options": {"restart_explorer": args.restart_explorer},
        "runtime": {
            "implementation": "installed-wheel" if runtime.installed else "source",
            "wheel_sha256": runtime.wheel_sha256,
            "origins": runtime_origins(
                runtime.root,
                {
                    "docking": docking.__file__,
                    "launcher": launcher.__file__,
                    "catalog": catalog.__file__,
                    "documents": documents.__file__,
                },
            ),
        },
    }
    if runtime.wheel_sha256 is not None:
        report["acceptance"] = {
            "schema": 1,
            "implementation": "installed-wheel",
            "wheel_sha256": runtime.wheel_sha256,
            "profile": "applications-explorer",
        }
    native = WindowsDesktop()
    assert not any(window["title"] == "Docking" for window in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    report["explorer_before"] = explorer
    try:
        with ClassicApplications(output) as fixture:

            def owned_catalog():
                rows = {app.name: app for app in catalog.read_shell_applications()}
                return (
                    rows
                    if all(fixture.labels[key] in rows for key in ("First", "Second"))
                    else None
                )

            rows = native.wait_for(
                owned_catalog, "Owned installed applications did not appear", 45
            )
            files_root = fixture.root / "Documents résumé 日本語"
            files_root.mkdir()
            files = [
                files_root / name
                for name in ("First résumé & #100%.txt", "Second 日本語.txt")
            ]
            for path in files:
                path.write_text("Owned selected-document acceptance", encoding="utf-8")
            selected = fixture.root / (fixture.labels["Second"] + ".lnk")
            selected_cwd = fixture.root / "Selected profile working directory"
            selected_cwd.mkdir()
            selected_argv = [
                "chosen profile",
                "",
                'literal "quotes"',
                "résumé 日本語 #100%",
            ]
            write_shortcut(
                selected,
                executable=fixture.executables["Second"],
                argv=selected_argv,
                cwd=selected_cwd,
                application_id=rows[fixture.labels["Second"]].desktop_id,
            )
            import psutil

            can_continue = True
            expected_shell = explorer
            owned_images = {
                PureWindowsPath(str(path)) for path in fixture.executables.values()
            }

            def context_safe(before_windows):
                facts = cleanup_snapshot(
                    native,
                    psutil.process_iter(["exe"]),
                    owned_images,
                    before_windows,
                    baseline,
                    expected_shell,
                )
                report.setdefault("cleanup_observations", []).append(
                    {"stage": name, **facts}
                )
                return facts["safe"]

            for route, key in (
                ("installed", "First"),
                ("selected", "Second"),
                ("generated", "Other"),
            ):
                name = "documents-" + route
                if not can_continue:
                    continue
                lifecycle = {"closed": True}
                before_windows = {window["hwnd"] for window in native.windows()}

                def operation(route=route, key=key, lifecycle=lifecycle, name=name):
                    case = output / route
                    case.mkdir()
                    home = case / "config"
                    profile = home / "docking"
                    profile.mkdir(parents=True)
                    registration = WindowsExecutableRegistration(profile / "launchers")
                    identifier = (
                        rows[fixture.labels[key]].desktop_id
                        if route == "installed"
                        else registration.create(
                            selected
                            if route == "selected"
                            else fixture.executables[key],
                            startup_wm_class="",
                        )
                    )
                    assert identifier
                    facts = output / f"facts-{key}-documents.json"
                    (
                        fixture.executables[key].parent / "selected-documents.json"
                    ).write_text(
                        json.dumps({"facts": str(facts), "title": fixture.labels[key]}),
                        encoding="utf-8",
                    )
                    cwd = (
                        fixture.executables[key].parent / "working directory 0 résumé"
                        if route == "installed"
                        else selected_cwd
                        if route == "selected"
                        else fixture.executables[key].parent
                    )
                    argv = (
                        fixture.arguments(key)
                        if route == "installed"
                        else selected_argv
                        if route == "selected"
                        else []
                    )
                    (profile / "dock.json").write_text(
                        json.dumps(
                            {
                                "pinned": [
                                    {"kind": "app", "target": identifier},
                                    {"kind": "applet", "target": "applet://clock"},
                                ],
                                "icon_size": 64,
                                "hide_mode": "none",
                                "show_recent_apps": False,
                                "startup_tips_enabled": False,
                                "update_check_enabled": False,
                                "global_search_enabled": False,
                            }
                        ),
                        encoding="utf-8",
                    )
                    write_shared_json(
                        case / "manifest.json",
                        {
                            "identifier": identifier,
                            "executable": str(fixture.executables[key]),
                            "route": route,
                            "argv": argv,
                            "cwd": str(cwd),
                            "facts": str(facts),
                            "documents": [str(path) for path in files],
                        },
                    )
                    lifecycle["closed"] = False
                    with (case / "driver.log").open("wb") as log:
                        completed = subprocess.run(
                            phase_command(
                                case,
                                installed_root=args.installed_root,
                                wheel=args.wheel,
                            ),
                            env={
                                **os.environ,
                                "XDG_CONFIG_HOME": str(home),
                                "XDG_CACHE_HOME": str(case / "cache"),
                            },
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=240,
                        )
                    child = read_shared_json(case / "documents.json")
                    lifecycle["closed"] = child.get("normal_shutdown") is True
                    report["checks"][name]["result"] = child
                    validate_document_phase(child, route)
                    assert completed.returncode == 0
                    assert (
                        native.workarea() == baseline and native.explorer() == explorer
                    )
                    report["checks"]["documents-" + route] = {
                        "passed": True,
                        "evidence": route + "/documents.json",
                        "result": child,
                    }
                    write_shared_json(output / "result.json", report)

                can_continue = record_check(
                    report["checks"],
                    name,
                    operation,
                    cleanup_verified=lambda state=lifecycle, windows=before_windows: (
                        state["closed"] and context_safe(windows)
                    ),
                )
                write_shared_json(output / "result.json", report)
            for mode in ("default", "explicit", "absence"):
                name = "association-" + mode
                if not can_continue:
                    continue
                lifecycle = {"closed": True}
                before_windows = {window["hwnd"] for window in native.windows()}

                def operation(mode=mode, lifecycle=lifecycle, name=name):
                    report["checks"][name].update(
                        association_checks(
                            native,
                            fixture,
                            output,
                            report,
                            mode=mode,
                            lifecycle=lifecycle,
                            installed_root=runtime.root if runtime.installed else None,
                            wheel=args.wheel,
                        )
                    )

                can_continue = record_check(
                    report["checks"],
                    name,
                    operation,
                    cleanup_verified=lambda state=lifecycle, windows=before_windows: (
                        state["closed"]
                        and report["associations_restored"]
                        and context_safe(windows)
                    ),
                )
                write_shared_json(output / "result.json", report)
            if can_continue:
                name = "selected-launcher-export"
                lifecycle = {"closed": True}
                before_windows = {window["hwnd"] for window in native.windows()}

                def operation():
                    report["checks"][name].update(
                        export_check(
                            native,
                            fixture,
                            output,
                            selected,
                            rows[fixture.labels["Second"]].desktop_id,
                            selected_argv,
                            expected_cwd=selected_cwd,
                            lifecycle=lifecycle,
                            installed_root=args.installed_root,
                            wheel=args.wheel,
                            observed=report["checks"][name].setdefault("observed", {}),
                        )
                    )

                can_continue = record_check(
                    report["checks"],
                    name,
                    operation,
                    cleanup_verified=lambda state=lifecycle, windows=before_windows: (
                        state["closed"] and context_safe(windows)
                    ),
                )
                write_shared_json(output / "result.json", report)
            inventory = {}
            if can_continue:
                name = "inventory-changes"
                lifecycle = {"closed": False}
                before_windows = {window["hwnd"] for window in native.windows()}

                def operation():
                    nonlocal inventory, expected_shell
                    catalog_case = output / "inventory"
                    command = [
                        sys.executable,
                        "-m",
                        "tools.windows.visual.catalog_changes_smoke",
                        "--output",
                        str(catalog_case),
                    ]
                    if args.restart_explorer:
                        command.append("--restart-explorer")
                    if args.installed_root is not None:
                        command.extend(("--installed-root", str(args.installed_root)))
                    if args.wheel is not None:
                        command.extend(("--wheel", str(args.wheel)))
                    with (output / "inventory.log").open("wb") as log:
                        completed = subprocess.run(
                            command, stdout=log, stderr=subprocess.STDOUT, timeout=240
                        )
                    inventory = read_shared_json(catalog_case / "result.json")
                    report["checks"][name].update(
                        evidence="inventory/result.json", result=inventory
                    )
                    lifecycle["closed"] = (
                        inventory.get("observer_stopped") is True
                        and inventory.get("owned_shortcuts_removed") is True
                    )
                    recovery = inventory.get("explorer_recovery")
                    if (
                        recovery is not None
                        and recovery.get("replacement_owned") is True
                    ):
                        assert recovery["before"] == explorer
                        assert recovery["after"] == native.explorer()
                        expected_shell = recovery["after"]
                    assert inventory["workarea_restored"] and lifecycle["closed"]
                    assert [phase["action"] for phase in inventory["phases"]] == [
                        "rename",
                        "delete",
                        "recreate",
                    ]
                    assert all(
                        phase["passed"] and phase["native_notifications"] > 0
                        for phase in inventory["phases"]
                    )
                    if not args.restart_explorer:
                        assert completed.returncode == 0 and inventory["ok"]

                can_continue = record_check(
                    report["checks"],
                    name,
                    operation,
                    cleanup_verified=lambda state=lifecycle, windows=before_windows: (
                        state["closed"] and context_safe(windows)
                    ),
                )
                write_shared_json(output / "result.json", report)
                if args.restart_explorer and can_continue:
                    name = "explorer-recovery"

                    def operation():
                        recovery = inventory["explorer_recovery"]
                        report["checks"][name].update(evidence="inventory/result.json")
                        assert (
                            recovery["passed"]
                            and recovery["held_actual_read"]
                            and recovery["cached_source_activation"]
                            and recovery["after_restart_change"]
                        )
                        assert (
                            recovery["replacement_owned"]
                            and recovery["shell_recovered"]
                            and recovery["old_process_exited"]
                        )
                        assert inventory["ok"] and inventory["explorer_recovered"]

                    can_continue = record_check(
                        report["checks"],
                        name,
                        operation,
                        cleanup_verified=lambda: context_safe(before_windows),
                    )
            if not args.restart_explorer:
                report["checks"]["explorer-recovery"].update(status="not-requested")
        report["fixture_cleanup"] = True
        report["normal_shutdown"] = can_continue
        report["workarea_restored"] = native.workarea() == baseline
        report["explorer_preserved"] = native.explorer() == explorer
        report["explorer_recovered"] = bool(
            args.restart_explorer
            and inventory.get("explorer_recovery", {}).get("replacement_owned")
            and native.explorer() == inventory["explorer_recovery"]["after"]
        )
        report["explorer_after"] = native.explorer()
        required = CHECKS if args.restart_explorer else CHECKS[:-1]
        report["ok"] = (
            all(report["checks"][name]["passed"] for name in required)
            and can_continue
            and report["workarea_restored"]
        )
        if report["ok"] and args.restart_explorer and runtime.wheel_sha256 is not None:
            from tools.windows.application_acceptance import (
                validate_applications_report,
            )

            validate_applications_report(report, wheel_sha256=runtime.wheel_sha256)
    except Exception as error:
        report["ok"] = False
        report["error"] = failure_details(error)
    finally:
        for check in report["checks"].values():
            if check.get("status") == "pending":
                check.update(
                    passed=False,
                    status="dependency-skipped",
                    reason="Owned setup or cleanup was not verified",
                )
        report["explorer_after"] = native.explorer()
        write_shared_json(output / "result.json", report)
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
