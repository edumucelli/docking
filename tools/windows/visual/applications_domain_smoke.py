"""Exercise owned Windows application documents, associations and launcher export."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

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


def record_check(checks, name, operation, *, cleanup_verified) -> bool:
    """Continue independent phases only after verifying their owned cleanup."""
    try:
        operation()
        checks[name].update(passed=True, status="passed")
    except Exception as error:
        checks[name].update(
            passed=False,
            status="failed",
            error={
                "class": type(error).__name__,
                "errno": error.errno if isinstance(error, OSError) else None,
            },
        )
    try:
        safe = cleanup_verified()
    except Exception as error:
        checks[name]["cleanup_error"] = {"class": type(error).__name__}
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
    native, facts_path: Path, executable: Path, argv: list[str], *, lifecycle=None
) -> dict:
    import psutil

    from tools.windows.visual.desktop_driver import read_shared_json

    native.wait_for(
        facts_path.is_file, "Owned application did not report its arguments", 30
    )
    facts = read_shared_json(facts_path)
    assert Path(facts["executable"]).samefile(executable)
    process = psutil.Process(facts["pid"])
    assert Path(process.exe()).samefile(executable)
    try:
        assert facts["argv"] == argv
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
    with (output / "selected-export.log").open("wb") as log:
        subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240
        )
    child = read_shared_json(case / "result.json")
    lifecycle["closed"] = child.get("normal_shutdown") is True
    assert child["ok"] and child["normal_shutdown"]
    copied = Path(child["export"]["path"])
    assert (
        copied.parent.samefile(directory)
        and copied.read_bytes() == selected.read_bytes()
    )
    assert not Path(child["export"]["source"]).exists()
    facts_path = output / "facts-Second-documents.json"
    facts_path.unlink(missing_ok=True)
    with ExplorerFolder(native, directory) as folder:
        rectangle = folder.file_rectangle(copied)
        lifecycle["closed"] = False
        native.double_click(
            rectangle["x"] + rectangle["width"] / 2, rectangle["y"] + 20
        )
        facts = finish_program(
            native, facts_path, fixture.executables["Second"], argv, lifecycle=lifecycle
        )
        assert Path(facts["cwd"]).samefile(expected_cwd)
    return {
        "passed": True,
        "actual_explorer_copy": True,
        "independent_explorer_launch": True,
        "source_cache_removed": True,
        "binary_preserved": True,
        "facts": facts,
    }


def association_checks(
    native, fixture, output: Path, report: dict, *, mode: str, lifecycle
) -> dict:
    """Use an entirely new extension/ProgID; never change an existing default."""
    import ctypes
    import winreg

    from docking.platform.applications.identity import LaunchProvenanceStore
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.targets import open_target

    token = uuid.uuid4().hex
    extension, program = ".docking-" + token, "Docking.Acceptance." + token
    base = "Software\\Classes\\"
    executable = fixture.executables["Other"]
    facts_path = output / "facts-Other-documents.json"
    keys = (
        base + extension,
        base + program,
        base + program + "\\shell",
        base + program + "\\shell\\open",
        base + program + "\\shell\\open\\command",
    )
    for name in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name):
                raise RuntimeError("Unique association fixture unexpectedly exists")
        except FileNotFoundError:
            pass
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    changed = bind(
        shell,
        "SHChangeNotify",
        None,
        ctypes.c_int32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    created = []

    def completed():
        return finish_program(
            native,
            facts_path,
            executable,
            [str(document).replace("/", "\\")],
            lifecycle=lifecycle,
        )

    document = fixture.root / ("Association résumé 日本語" + extension)
    document.write_text("Owned association document", encoding="utf-8")
    report["associations_restored"] = False
    try:
        values = {
            keys[0]: program,
            keys[1]: "Docking owned document",
            keys[-1]: '"' + str(executable).replace("/", "\\") + '" "%1"',
        }
        for name in keys:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_SET_VALUE
            ) as key:
                created.append(name)
                if name in values:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, values[name])
        changed(0x08000000, 0, None, None)
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

        def oracle():
            size = ctypes.c_uint32()
            status = query(0, 2, extension, "open", None, ctypes.byref(size))
            if status not in (0, 1) or not 0 < size.value < 32768:
                return None
            value = ctypes.create_unicode_buffer(size.value)
            return (
                value.value
                if query(0, 2, extension, "open", value, ctypes.byref(size)) == 0
                else None
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
        registry.refresh()
        selected = native.wait_for(
            lambda: registry.default_for_content_type(extension),
            "Gio association-only client did not resolve",
        )
        assert selected.has_gio_source
        if mode == "explicit":
            facts_path.unlink(missing_ok=True)
            lifecycle["closed"] = False
            assert ApplicationLauncher(
                registry, LaunchProvenanceStore()
            ).launch_app_uris(selected.desktop_id, [document.as_uri()])
            return {"passed": True, "facts": completed()}
        assert mode == "absence"
        assert registry.default_for_content_type(".docking-absent-" + token) is None
        return {"passed": True, "lookup_only": True}
    finally:
        for name in reversed(created):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
        changed(0x08000000, 0, None, None)
        for name in keys:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name):
                    raise RuntimeError("Owned association key survived cleanup")
            except FileNotFoundError:
                pass
        report["associations_restored"] = True


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
            from pathlib import PureWindowsPath

            import psutil

            can_continue = True
            expected_shell = explorer
            owned_images = {
                PureWindowsPath(str(path)) for path in fixture.executables.values()
            }

            def context_safe(before_windows):
                if native.workarea() != baseline or native.explorer() != expected_shell:
                    return False
                for process in psutil.process_iter(["exe"]):
                    image = process.info["exe"]
                    if image and PureWindowsPath(image) in owned_images:
                        return False
                for window in native.windows():
                    if window["hwnd"] in before_windows:
                        continue
                    if window["pid"] == expected_shell["pid"] and window["class"] in {
                        "Shell_TrayWnd",
                        "Shell_SecondaryTrayWnd",
                        "Progman",
                        "WorkerW",
                    }:
                        continue
                    return False
                return True

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
        report["error"] = {
            "class": type(error).__name__,
            "errno": error.errno if isinstance(error, OSError) else None,
        }
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
