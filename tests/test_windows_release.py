"""Release staging binds Windows packages to the native acceptance artifact."""

import hashlib
import json
import zipfile

import pytest

from tools.windows import release

WHEEL_SHA256 = hashlib.sha256(b"accepted wheel fixture").hexdigest()


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
                "installer_sha256": hashlib.sha256(setup.read_bytes()).hexdigest(),
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
    for profile in ("explorer", "backend", "gtk-v0-v4", "gtk-v3-v4-guid"):
        path = tray_path(directory, profile)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(tray_report(profile)), encoding="utf-8")
    return directory


def test_release_versions_aliases_and_checksums_retain_accepted_bytes(
    incoming, tmp_path
):
    destination = tmp_path / "release"
    destination.mkdir()
    other_platform = destination / "existing-bsd.pkg"
    other_platform.write_bytes(b"retain another platform")
    release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
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
    ],
)
def test_each_installer_acceptance_phase_must_have_passed(incoming, tmp_path, check):
    report = incoming / "artifacts/windows/installer/result.json"
    data = json.loads(report.read_text())
    data[check] = False
    report.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination)
    assert not destination.exists()


def test_invalid_release_version_does_not_create_destination(incoming, tmp_path):
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="version"):
        release.stage(incoming, "../1.2.3", destination)
    assert not destination.exists()


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
        release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
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
        release.stage(incoming, "1.2.3", destination)
    assert not destination.exists()


def test_tray_rejection_preserves_existing_release_assets(incoming, tmp_path):
    tray_path(incoming, "gtk-v3-v4-guid").unlink()
    destination = tmp_path / "release"
    destination.mkdir()
    asset = destination / "existing-bsd.pkg"
    asset.write_bytes(b"previously accepted bytes")
    with pytest.raises(ValueError, match="tray"):
        release.stage(incoming, "1.2.3", destination)
    assert [path.name for path in destination.iterdir()] == [asset.name]
    assert asset.read_bytes() == b"previously accepted bytes"
