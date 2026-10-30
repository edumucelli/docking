"""Release staging binds Windows packages to the native acceptance artifact."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.test_windows_input_acceptance import SOURCE_SHA, write_input_artifact
from tools.windows import release

WHEEL_SHA256 = hashlib.sha256(b"accepted wheel fixture").hexdigest()


def applications_report():
    runtime = {"implementation": "installed-wheel", "wheel_sha256": WHEEL_SHA256}
    before = {"pid": 100, "window": 200, "style": 300}
    after = {"pid": 101, "window": 201, "style": 300}
    return {
        "schema": 1,
        "ok": True,
        "normal_shutdown": True,
        "fixture_cleanup": True,
        "associations_restored": True,
        "workarea_restored": True,
        "explorer_recovered": True,
        "explorer_before": before,
        "explorer_after": after,
        "options": {"restart_explorer": True},
        "acceptance": {"schema": 1, **runtime, "profile": "applications-explorer"},
        "runtime": {
            **runtime,
            "origins": {
                "docking": "docking/__init__.py",
                "launcher": "docking/platform/applications/launcher.py",
                "catalog": "docking/platform/backends/windows/catalog.py",
                "documents": "docking/platform/backends/windows/documents.py",
            },
        },
        "checks": {
            **{
                "documents-" + route: {
                    "passed": True,
                    "evidence": route + "/documents.json",
                    "result": {
                        "phase": "documents",
                        "ok": True,
                        "normal_shutdown": True,
                        "physical_document_input": True,
                        "id": "Owned." + route,
                        "runtime": {**runtime, "origins": {"app": "docking/app.py"}},
                        "document_drops": [
                            {
                                "count": count,
                                "protocol": "ole2",
                                "desktop_id": "Owned." + route,
                            }
                            for count in (1, 2)
                        ],
                    },
                }
                for route in ("installed", "generated", "selected")
            },
            "association-default": {"passed": True},
            "association-explicit": {"passed": True},
            "association-absence": {"passed": True, "lookup_only": True},
            "selected-launcher-export": {
                "passed": True,
                "actual_explorer_copy": True,
                "independent_explorer_launch": True,
                "source_cache_removed": True,
                "binary_preserved": True,
            },
            "inventory-changes": {
                "passed": True,
                "evidence": "inventory/result.json",
                "result": {
                    "schema": 1,
                    "ok": True,
                    "observer_stopped": True,
                    "owned_shortcuts_removed": True,
                    "workarea_restored": True,
                    "explorer_recovered": True,
                    "forced_refresh": False,
                    "options": {"restart_explorer": True},
                    "native_notifications": 3,
                    "runtime": {
                        **runtime,
                        "origins": {
                            "registry": "docking/platform/applications/registry.py"
                        },
                    },
                    "phases": [
                        {"action": action, "passed": True, "native_notifications": 1}
                        for action in ("rename", "delete", "recreate")
                    ],
                    "explorer_recovery": {
                        "passed": True,
                        "held_actual_read": True,
                        "cached_source_activation": True,
                        "after_restart_change": True,
                        "old_process_exited": True,
                        "replacement_owned": True,
                        "shell_recovered": True,
                        "before": before,
                        "after": after,
                    },
                },
            },
            "explorer-recovery": {"passed": True, "evidence": "inventory/result.json"},
        },
    }


def tray_report(profile):
    if profile in ("explorer", "backend"):
        report = {
            "mode": profile,
            "evidence": "protocol-only",
            "routing_restored": True,
            "cursor_restored": True,
            "cases": {
                case.name: {"protocol": "passed", "cleanup_ok": True}
                for case in release.provider_cases()
            },
        }
    else:
        legacy = 0 if profile == "gtk-v0-v4" else 3
        versions = {"legacy": legacy, "modern": 4}
        focus = {
            "popup_window": 30,
            "foreground": 30,
            "native_focus_root": 30,
            "foreground_pid": 100,
            "native_focus_pid": 100,
            "native_query_ok": True,
            "logical_selection": True,
            "target_focus": True,
            "row_focus": True,
            "popup_visible": True,
        }
        report = {
            "ok": True,
            "normal_shutdown": True,
            "host_thread_stopped": True,
            "fixture_exit": 0,
            "fixture_before_dock": {"window": 10},
            "options": {
                "legacy_version": legacy,
                "modern_version": 4,
                "verify_interactions": True,
                "verify_geometry": legacy == 0,
                "guid_only_updates": legacy == 3,
            },
            "interactions": {
                "physical_input": "passed",
                "visual": "captured-not-reviewed",
                "hover": [
                    {
                        "which": which,
                        "version": version,
                        "showtip": showtip,
                        "foreground_unchanged": True,
                        "callbacks": [
                            {"event": event} for event in (0x200, 0x406, 0x407)
                        ],
                    }
                    for which, version in versions.items()
                    for showtip in (False, True, False)
                ],
                "keyboard": [
                    {
                        "which": which,
                        "version": version,
                        "target": target,
                        "callbacks": [{"event": 0x205 if version == 0 else 0x401}],
                    }
                    for which, version in versions.items()
                    for target in ("icon", "Open")
                ],
                "focus_return": {which: dict(focus) for which in versions},
                "duplicate_focus": {which: dict(focus) for which in versions},
                "ineligible_focus": {"foreground": 30, "control_pid": 200},
                "never_local_focus": {
                    "calls": [{"operation": 3, "window": 20, "accepted": True}]
                },
                "focus_routes": [
                    *[{"window": 10, "forwarded": False} for _ in range(5)],
                    {"window": 20, "forwarded": True, "result": 1},
                ],
            },
        }
        if legacy == 3:
            report["guid_only_updates"] = True
    if profile != "explorer":
        report["acceptance"] = {
            "schema": 1,
            "implementation": "installed-wheel",
            "wheel_sha256": WHEEL_SHA256,
            "profile": "api-matrix" if profile == "backend" else profile,
        }
    return report


def tray_path(directory, profile):
    return directory / "artifacts/windows/tray" / profile / "result.json"


def checksum_file(path):
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )


def readiness_report(identity):
    shutdown = {
        "normal_shutdown": True,
        "close_requested": True,
        "exit_code": 0,
        "forced": False,
        "owned_remaining": [],
        "errors": [],
    }
    samples = [
        {
            "elapsed_seconds": elapsed,
            "responsive": True,
            "rss_bytes": 1000,
            "private_bytes": 800,
            "threads": 2,
            "handles": 20,
            "gdi": 5,
            "user": 5,
            "processes": [
                {
                    "pid": 7,
                    "created": 1.0,
                    "cpu_seconds": elapsed / 100,
                    "rss_bytes": 1000,
                    "private_bytes": 800,
                    "threads": 2,
                    "handles": 20,
                    "gdi": 5,
                    "user": 5,
                }
            ],
        }
        for elapsed in (0.0, 60.0, 120.0)
    ]
    return {
        "ok": True,
        "acceptance": {"schema": 1, "implementation": "portable-bundle", **identity},
        "payload_verification": {
            "schema": 1,
            **{
                phase: {
                    "ok": True,
                    "complete": True,
                    "inventory_sha256": identity["payload_inventory_sha256"],
                    "declared_files": identity["payload_files"],
                    "verified_files": identity["payload_files"],
                    "observed_declared_files": identity["payload_files"],
                    "observed_inventory_sha256": "1" * 64,
                    "issue_count": 0,
                    "issues_omitted": 0,
                    "issues": [],
                }
                for phase in ("before", "after")
            },
        },
        "measurement": {"startup_cycles": 3, "requested_seconds": 120},
        "startups": [
            {**shutdown, "visible_seconds": 1.0, "responsive_seconds": 1.1}
            for _ in range(3)
        ],
        "session": {
            **shutdown,
            "samples": samples,
            "startup": {"visible_seconds": 1.0, "responsive_seconds": 1.1},
            "summary": release.summarize_samples(samples),
        },
    }


def write_archive(path, *, version="1.2.3", inventory=None, wheel_sha256=WHEEL_SHA256):
    files = {
        "Docking.exe": b"portable unit fixture",
        "runtime/share/docking/runtime.json": json.dumps(
            {"version": version, "wheel_sha256": wheel_sha256}
        ).encode(),
    }
    if inventory is None:
        inventory = {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr("Docking/" + name, data)
        archive.writestr("Docking/files.sha256.json", json.dumps(inventory))
    checksum_file(path)


@pytest.fixture
def incoming(tmp_path):
    directory = tmp_path / "incoming"
    packages = directory / "dist"
    packages.mkdir(parents=True)
    write_archive(packages / "docking-windows-x86_64.zip")
    setup = packages / "docking-windows-x86_64-setup.exe"
    setup.write_bytes(b"installer unit fixture")
    checksum_file(setup)
    report = directory / "artifacts/windows/installer/result.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "acceptance_schema": 1,
                "installer_sha256": hashlib.sha256(setup.read_bytes()).hexdigest(),
                "candidate": {
                    **release.archive_identity(packages / "docking-windows-x86_64.zip"),
                    "installer_sha256": hashlib.sha256(setup.read_bytes()).hexdigest(),
                },
                "previous": {
                    "version": "1.2.2",
                    "payload_files": 3,
                    **{
                        key: hashlib.sha256(key.encode()).hexdigest()
                        for key in (
                            "archive_sha256",
                            "installer_sha256",
                            "payload_inventory_sha256",
                            "wheel_sha256",
                        )
                    },
                },
                "previous_verified_payload_files": 3,
                "distinct_version_upgrade": True,
                "verified_payload_files": 2,
                "in_use_upgrade_rejected": True,
                "in_use_uninstall_rejected": True,
                "upgrade_preserved_startup_and_previous_payload": True,
                "startup_disabled": True,
                "verified_final_payload_files": 2,
                "installed_desktop": True,
                "uninstall_preserved_preferences_and_unknown_files": True,
            }
        ),
        encoding="utf-8",
    )
    readiness = directory / "artifacts/windows/release-readiness/result.json"
    readiness.parent.mkdir(parents=True)
    readiness.write_text(
        json.dumps(
            readiness_report(
                release.archive_identity(packages / "docking-windows-x86_64.zip")
            )
        ),
        encoding="utf-8",
    )
    applications = directory / "artifacts/windows/applications-explorer/result.json"
    applications.parent.mkdir(parents=True)
    applications.write_text(json.dumps(applications_report()), encoding="utf-8")
    for profile in ("explorer", "backend", "gtk-v0-v4", "gtk-v3-v4-guid"):
        path = tray_path(directory, profile)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(tray_report(profile)), encoding="utf-8")
    write_input_artifact(directory / "artifacts/windows", profile="server-api")
    write_input_artifact(directory / "client-input/artifacts/windows")
    return directory


def test_release_versions_aliases_and_checksums_retain_accepted_bytes(
    incoming, tmp_path
):
    destination = tmp_path / "release"
    destination.mkdir()
    other_platform = destination / "existing-bsd.pkg"
    other_platform.write_bytes(b"retain another platform")
    release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert other_platform.read_bytes() == b"retain another platform"
    sums = (destination / "SHA256SUMS-windows").read_text().splitlines()
    assert len(sums) == 4
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    for suffix in (".zip", "-setup.exe"):
        assert (
            destination / ("docking-1.2.3-windows-x86_64" + suffix)
        ).read_bytes() == (
            incoming / "dist" / ("docking-windows-x86_64" + suffix)
        ).read_bytes()
        assert (
            destination / ("docking-latest-windows-x86_64" + suffix)
        ).read_bytes() == (
            destination / ("docking-1.2.3-windows-x86_64" + suffix)
        ).read_bytes()
    assert not list(destination.glob("*.json"))


@pytest.mark.parametrize(
    "defect",
    [
        "zip-checksum",
        "setup-checksum",
        "companion-filename",
        "version",
        "missing-package",
        "missing-report",
        "different-setup",
        "empty-inventory",
        "initial-count",
        "final-count",
        "error-report",
        "invalid-json",
    ],
)
def test_invalid_or_unaccepted_packages_do_not_enter_the_release(
    incoming, tmp_path, defect
):
    archive = incoming / "dist/docking-windows-x86_64.zip"
    setup = incoming / "dist/docking-windows-x86_64-setup.exe"
    report = incoming / "artifacts/windows/installer/result.json"
    data = json.loads(report.read_text())
    if defect == "zip-checksum":
        archive.write_bytes(archive.read_bytes() + b"changed")
    elif defect == "setup-checksum":
        setup.write_bytes(b"changed")
    elif defect == "companion-filename":
        setup.with_suffix(".exe.sha256").write_text(
            hashlib.sha256(setup.read_bytes()).hexdigest() + "  different.exe\n"
        )
    elif defect == "version":
        write_archive(archive, version="9.9.9")
    elif defect == "missing-package":
        setup.unlink()
    elif defect == "empty-inventory":
        write_archive(archive, inventory={})
    else:
        if defect == "different-setup":
            data["installer_sha256"] = "0" * 64
        elif defect == "initial-count":
            data["verified_payload_files"] = 1
        elif defect == "final-count":
            data["verified_final_payload_files"] = 1
        elif defect == "error-report":
            data["error"] = "Uninstall failed"
        report.write_text(json.dumps(data), encoding="utf-8")
        if defect == "missing-report":
            report.unlink()
        elif defect == "invalid-json":
            report.write_bytes(b"invalid JSON")
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize(
    "check",
    [
        "in_use_upgrade_rejected",
        "in_use_uninstall_rejected",
        "upgrade_preserved_startup_and_previous_payload",
        "startup_disabled",
        "installed_desktop",
        "uninstall_preserved_preferences_and_unknown_files",
        "distinct_version_upgrade",
    ],
)
def test_each_installer_acceptance_phase_must_have_passed(incoming, tmp_path, check):
    report = incoming / "artifacts/windows/installer/result.json"
    data = json.loads(report.read_text())
    data[check] = False
    report.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


def test_invalid_release_version_does_not_create_destination(incoming, tmp_path):
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="version"):
        release.stage(incoming, "../1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize(
    "field", ["archive_sha256", "payload_inventory_sha256", "wheel_sha256", "version"]
)
def test_installer_acceptance_binds_exact_candidate_not_only_count(
    incoming, tmp_path, field
):
    path = incoming / "artifacts/windows/installer/result.json"
    report = json.loads(path.read_text())
    report["candidate"][field] = "different"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="matching installer"):
        release.stage(incoming, "1.2.3", tmp_path / "release", source_sha=SOURCE_SHA)
    assert not (tmp_path / "release").exists()


@pytest.mark.parametrize("version", ["1.2.3", "1.2.4", "invalid"])
def test_reinstall_or_newer_fixture_cannot_claim_upgrade(incoming, tmp_path, version):
    path = incoming / "artifacts/windows/installer/result.json"
    report = json.loads(path.read_text())
    report["previous"]["version"] = version
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        release.stage(incoming, "1.2.3", tmp_path / "release", source_sha=SOURCE_SHA)
    assert not (tmp_path / "release").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "stale-archive",
        "short",
        "cycles",
        "unresponsive",
        "forced",
        "remaining",
        "summary",
        "resources",
        "cpu-regression",
        "duplicate-process",
        "schema-bool",
        "timing",
    ],
)
def test_startup_session_coverage_is_bound_and_recomputed(incoming, tmp_path, defect):
    path = incoming / "artifacts/windows/release-readiness/result.json"
    report = json.loads(path.read_text())
    if defect == "stale-archive":
        report["acceptance"]["archive_sha256"] = "0" * 64
    elif defect == "short":
        report["measurement"]["requested_seconds"] = 119
    elif defect == "cycles":
        report["startups"].pop()
    elif defect == "unresponsive":
        report["session"]["samples"][1]["responsive"] = False
    elif defect == "forced":
        report["startups"][0]["forced"] = True
    elif defect == "remaining":
        report["session"]["owned_remaining"] = [7]
    elif defect == "summary":
        report["session"]["summary"]["rss_bytes_delta"] = 1
    elif defect == "resources":
        report["session"]["samples"][1]["handles"] += 1
    elif defect == "cpu-regression":
        report["session"]["samples"][2]["processes"][0]["cpu_seconds"] = 0
    elif defect == "duplicate-process":
        row = report["session"]["samples"][1]["processes"]
        row.append(dict(row[0]))
    elif defect == "schema-bool":
        report["acceptance"]["schema"] = True
    elif defect == "timing":
        report["startups"][0]["responsive_seconds"] = 0.5
    path.write_text(json.dumps(report), encoding="utf-8")
    if defect == "missing":
        path.unlink()
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", tmp_path / "release", source_sha=SOURCE_SHA)
    assert not (tmp_path / "release").exists()


def test_session_cpu_summary_keeps_exited_children_and_distinguishes_pid_reuse():
    rows = []
    for elapsed, processes in (
        (0, [(7, 1, 10)]),
        (60, [(7, 1, 11), (8, 2, 2)]),
        (120, [(7, 1, 12), (8, 3, 1)]),
    ):
        rows.append(
            {
                "elapsed_seconds": elapsed,
                "processes": [
                    {"pid": pid, "created": created, "cpu_seconds": cpu}
                    for pid, created, cpu in processes
                ],
                **dict.fromkeys(
                    ("rss_bytes", "private_bytes", "handles", "gdi", "user"), 0
                ),
            }
        )
    summary = release.summarize_samples(rows)
    assert summary["observed_cpu_seconds"] == 5
    assert summary["observed_process_instances"] == 3
    assert summary["cpu_percent_one_core"] == pytest.approx(5 / 120 * 100)


@pytest.mark.parametrize(
    "name",
    [
        "documents-installed",
        "documents-generated",
        "documents-selected",
        "association-default",
        "association-explicit",
        "association-absence",
        "selected-launcher-export",
        "inventory-changes",
        "explorer-recovery",
    ],
)
def test_application_gate_requires_each_complete_named_workflow(name):
    report = applications_report()
    del report["checks"][name]
    with pytest.raises(ValueError, match="complete"):
        release.validate_applications_report(report, wheel_sha256=WHEEL_SHA256)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "candidate",
        "child-wheel",
        "source-origin",
        "no-physical",
        "wrong-id",
        "one-file-only",
        "unclean-child",
        "unclean-fixture",
        "association",
        "export",
        "no-notification",
        "incomplete-inventory",
        "same-shell",
        "foreign-shell",
        "old-alive",
        "unowned-replacement",
        "unrecovered",
        "dependency-skipped",
        "schema-bool",
    ],
)
def test_application_gate_rejects_stale_or_incomplete_evidence_before_staging(
    incoming, tmp_path, defect
):
    path = incoming / "artifacts/windows/applications-explorer/result.json"
    report = applications_report()
    check = report["checks"]
    document = check["documents-selected"]["result"]
    inventory = check["inventory-changes"]["result"]
    recovery = inventory["explorer_recovery"]
    if defect == "candidate":
        report["acceptance"]["wheel_sha256"] = "0" * 64
    elif defect == "child-wheel":
        document["runtime"]["wheel_sha256"] = "0" * 64
    elif defect == "source-origin":
        document["runtime"]["origins"]["app"] = "source/docking/app.py"
    elif defect == "no-physical":
        document["document_drops"][0]["protocol"] = "local"
    elif defect == "wrong-id":
        document["document_drops"][1]["desktop_id"] = "Another.App"
    elif defect == "one-file-only":
        document["document_drops"].pop()
    elif defect == "unclean-child":
        document["normal_shutdown"] = False
    elif defect == "unclean-fixture":
        report["fixture_cleanup"] = False
    elif defect == "association":
        report["associations_restored"] = False
    elif defect == "export":
        check["selected-launcher-export"]["binary_preserved"] = False
    elif defect == "no-notification":
        inventory["phases"][1]["native_notifications"] = 0
    elif defect == "incomplete-inventory":
        inventory["phases"].pop()
    elif defect == "same-shell":
        recovery["after"]["pid"] = recovery["before"]["pid"]
    elif defect == "foreign-shell":
        report["explorer_after"] = {"pid": 999, "window": 999, "style": 300}
    elif defect == "old-alive":
        recovery["old_process_exited"] = False
    elif defect == "unowned-replacement":
        recovery["replacement_owned"] = False
    elif defect == "unrecovered":
        recovery["shell_recovered"] = False
    elif defect == "dependency-skipped":
        check["association-default"] = {"passed": False, "status": "dependency-skipped"}
    elif defect == "schema-bool":
        report["schema"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    if defect == "missing":
        path.unlink()
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


def test_same_wheel_application_report_passes_shared_pure_consumer():
    release.validate_applications_report(
        applications_report(), wheel_sha256=WHEEL_SHA256
    )


def test_application_consumer_is_shared_and_imports_without_docking_or_build_tools():
    from tools.windows.application_acceptance import validate_applications_report

    assert release.validate_applications_report is validate_applications_report
    module = (
        Path(__file__).resolve().parents[1] / "tools/windows/application_acceptance.py"
    )
    script = (
        "import runpy,sys,json;runpy.run_path(sys.argv[1]);"
        "print(json.dumps([name for name in sys.modules if name.split('.')[0] "
        "in ('docking','gi','packaging')]))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(module)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(completed.stdout) == []


@pytest.mark.parametrize(
    "profile", ["explorer", "backend", "gtk-v0-v4", "gtk-v3-v4-guid"]
)
@pytest.mark.parametrize("defect", ["missing", "json", "error", "not-object"])
def test_each_tray_report_is_required_before_any_assets_are_staged(
    incoming, tmp_path, profile, defect
):
    path = tray_path(incoming, profile)
    if defect == "missing":
        path.unlink()
    elif defect == "json":
        path.write_text("unfinished", encoding="utf-8")
    elif defect == "error":
        data = json.loads(path.read_text())
        data["error"] = "Native tray fixture failed"
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_text("[]", encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="tray"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize("profile", ["explorer", "backend"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing-case",
        "extra-case",
        "failed-case",
        "cleanup",
        "routing",
        "cursor",
        "mode",
        "evidence",
    ],
)
def test_tray_api_acceptance_requires_complete_catalog_and_cleanup(
    incoming, tmp_path, profile, defect
):
    path = tray_path(incoming, profile)
    data = json.loads(path.read_text())
    first = next(iter(data["cases"]))
    if defect == "missing-case":
        data["cases"].pop(first)
    elif defect == "extra-case":
        data["cases"]["unrecognized-abi"] = {"protocol": "passed", "cleanup_ok": True}
    elif defect == "failed-case":
        data["cases"][first]["protocol"] = "failed"
    elif defect == "cleanup":
        data["cases"][first]["cleanup_ok"] = False
    elif defect == "routing":
        data["routing_restored"] = False
    elif defect == "cursor":
        data["cursor_restored"] = False
    elif defect == "mode":
        data["mode"] = "backend" if profile == "explorer" else "explorer"
    else:
        data["evidence"] = "physical-input"
    path.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize("profile", ["backend", "gtk-v0-v4", "gtk-v3-v4-guid"])
@pytest.mark.parametrize(
    "defect", ["missing", "wheel", "schema", "source", "profile", "source-runtime"]
)
def test_installed_tray_acceptance_must_match_the_packaged_wheel(
    incoming, tmp_path, profile, defect
):
    path = tray_path(incoming, profile)
    data = json.loads(path.read_text())
    if defect == "missing":
        data.pop("acceptance")
    elif defect == "source-runtime":
        data["runtime"] = "source"
    else:
        field, value = {
            "wheel": ("wheel_sha256", "0" * 64),
            "schema": ("schema", True),
            "source": ("implementation", "source"),
            "profile": ("profile", "different-profile"),
        }[defect]
        data["acceptance"][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize("profile", ["gtk-v0-v4", "gtk-v3-v4-guid"])
@pytest.mark.parametrize(
    "defect",
    [
        "options",
        "physical",
        "hover",
        "hover-foreground",
        "rich-close",
        "keyboard",
        "keyboard-callback",
        "focus-return",
        "duplicate-focus",
        "ineligible-focus",
        "never-local-focus",
        "local-routing",
        "control-routing",
        "shutdown",
        "thread",
        "fixture-exit",
        "boolean-exit",
        "guid-phase",
    ],
)
def test_tray_gtk_acceptance_requires_both_complete_interaction_profiles(
    incoming, tmp_path, profile, defect
):
    path = tray_path(incoming, profile)
    data = json.loads(path.read_text())
    evidence = data["interactions"]
    if defect == "options":
        data["options"]["legacy_version"] = 3 if profile == "gtk-v0-v4" else 0
    elif defect == "physical":
        evidence["physical_input"] = "not-run"
    elif defect == "hover":
        evidence["hover"].pop()
    elif defect == "hover-foreground":
        evidence["hover"][0]["foreground_unchanged"] = False
    elif defect == "rich-close":
        evidence["hover"][-1]["callbacks"] = [{"event": 0x200}, {"event": 0x406}]
    elif defect == "keyboard":
        evidence["keyboard"].pop()
    elif defect == "keyboard-callback":
        evidence["keyboard"][-1]["callbacks"] = [{"event": 0x400}]
    elif defect in ("focus-return", "duplicate-focus"):
        evidence[defect.replace("-", "_")]["modern"]["row_focus"] = False
    elif defect in ("ineligible-focus", "never-local-focus"):
        evidence.pop(defect.replace("-", "_"))
    elif defect == "local-routing":
        evidence["focus_routes"][0]["forwarded"] = True
    elif defect == "control-routing":
        evidence["focus_routes"][-1]["forwarded"] = False
    elif defect == "shutdown":
        data["normal_shutdown"] = False
    elif defect == "thread":
        data["host_thread_stopped"] = False
    elif defect == "fixture-exit":
        data["fixture_exit"] = 1
    elif defect == "boolean-exit":
        data["fixture_exit"] = False
    elif profile == "gtk-v3-v4-guid":
        data.pop("guid_only_updates")
    else:
        data["options"]["verify_geometry"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize("profile", ["gtk-v0-v4", "gtk-v3-v4-guid"])
@pytest.mark.parametrize("phase", ["focus_return", "duplicate_focus"])
@pytest.mark.parametrize("which", ["legacy", "modern"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("foreground", 31),
        ("native_focus_root", 31),
        ("popup_window", 0),
        ("popup_window", True),
        ("foreground", True),
        ("native_focus_root", True),
        ("native_query_ok", False),
        ("logical_selection", False),
        ("target_focus", False),
        ("foreground_pid", 0),
        ("native_focus_pid", 101),
    ],
)
def test_tray_focus_requires_exact_native_popup_and_widget_focus(
    incoming, tmp_path, profile, phase, which, field, value
):
    path = tray_path(incoming, profile)
    data = json.loads(path.read_text())
    data["interactions"][phase][which][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="focus acceptance"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize("profile", ["gtk-v0-v4", "gtk-v3-v4-guid"])
@pytest.mark.parametrize(("accepted", "result"), [(False, 0), (True, 1)])
def test_never_local_focus_preserves_the_actual_explorer_return(
    profile, accepted, result
):
    report = tray_report(profile)
    evidence = report["interactions"]
    # An unregistered independent control was correctly forwarded to Explorer,
    # which returned zero. Successful Shell handling is also a valid return.
    window = 54854358
    evidence["never_local_focus"]["calls"] = [
        {"operation": 3, "accepted": accepted, "window": window}
    ]
    evidence["focus_routes"][-1] = {
        "window": window,
        "forwarded": True,
        "result": result,
    }
    release.validate_tray_report(report, profile=profile, wheel_sha256=WHEEL_SHA256)


@pytest.mark.parametrize("profile", ["gtk-v0-v4", "gtk-v3-v4-guid"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing-accepted",
        "numeric-accepted",
        "missing-call-window",
        "missing-route-window",
        "wrong-window",
        "missing-result",
        "boolean-result",
        "mismatched-result",
        "missing-forwarded",
        "not-forwarded",
        "numeric-forwarded",
    ],
)
def test_never_local_focus_requires_matching_forwarded_result(profile, defect):
    report = tray_report(profile)
    evidence = report["interactions"]
    call = evidence["never_local_focus"]["calls"][0]
    route = evidence["focus_routes"][-1]
    call["accepted"], route["result"] = False, 0
    if defect == "missing-accepted":
        call.pop("accepted")
    elif defect == "numeric-accepted":
        call["accepted"] = 0
    elif defect == "missing-call-window":
        call.pop("window")
    elif defect == "missing-route-window":
        route.pop("window")
    elif defect == "wrong-window":
        route["window"] = 21
    elif defect == "missing-result":
        route.pop("result")
    elif defect == "boolean-result":
        route["result"] = False
    elif defect == "mismatched-result":
        route["result"] = 1
    elif defect == "missing-forwarded":
        route.pop("forwarded")
    elif defect == "not-forwarded":
        route["forwarded"] = False
    else:
        route["forwarded"] = 1
    with pytest.raises(ValueError, match="focus routing acceptance"):
        release.validate_tray_report(report, profile=profile, wheel_sha256=WHEEL_SHA256)


def test_missing_packaged_wheel_digest_cannot_reuse_unbound_tray_reports(
    incoming, tmp_path
):
    write_archive(incoming / "dist/docking-windows-x86_64.zip", wheel_sha256=None)
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="wheel digest"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


def test_tray_rejection_preserves_existing_release_assets(incoming, tmp_path):
    tray_path(incoming, "gtk-v3-v4-guid").unlink()
    destination = tmp_path / "release"
    destination.mkdir()
    asset = destination / "existing-bsd.pkg"
    asset.write_bytes(b"previously accepted bytes")
    with pytest.raises(ValueError, match="tray"):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert [path.name for path in destination.iterdir()] == [asset.name]
    assert asset.read_bytes() == b"previously accepted bytes"


@pytest.mark.parametrize(
    "failure",
    [
        "missing-client",
        "stale-client",
        "server-only",
        "failed-cell",
        "failed-server-cleanup",
    ],
)
def test_release_requires_separate_exact_candidate_workstation_proof(
    incoming, tmp_path, failure
):
    client = incoming / "client-input/artifacts/windows"
    path = client / "input-profiles/result.json"
    if failure == "missing-client":
        path.unlink()
    elif failure == "stale-client":
        report = json.loads(path.read_text())
        report["acceptance"]["source_sha"] = "b" * 40
        path.write_text(json.dumps(report))
    elif failure == "server-only":
        write_input_artifact(client, profile="server-api")
    elif failure == "failed-cell":
        report = json.loads(path.read_text())
        report["stages"][1]["profiles"][1]["variants"][1]["text"] = "aa "
        path.write_text(json.dumps(report))
    else:
        path = incoming / "artifacts/windows/search-layouts/result.json"
        report = json.loads(path.read_text())
        report["cleanup"][0]["ok"] = False
        path.write_text(json.dumps(report))
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "after-failed",
        "different-inventory",
        "omitted-issues",
        "unverified-file",
    ],
)
def test_release_requires_complete_equal_before_after_payload_proof(
    incoming, tmp_path, failure
):
    path = incoming / "artifacts/windows/release-readiness/result.json"
    report = json.loads(path.read_text())
    proof = report["payload_verification"]
    if failure == "missing":
        del report["payload_verification"]
    elif failure == "after-failed":
        proof["after"]["ok"] = False
    elif failure == "different-inventory":
        proof["after"]["observed_inventory_sha256"] = "2" * 64
    elif failure == "omitted-issues":
        proof["after"]["issues_omitted"] = 1
    elif failure == "unverified-file":
        proof["after"]["verified_files"] -= 1
    path.write_text(json.dumps(report))
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", destination, source_sha=SOURCE_SHA)
    assert not destination.exists()
