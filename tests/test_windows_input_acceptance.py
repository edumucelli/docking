"""Input release proof has explicit coverage and exact candidate identities."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.windows import input_acceptance as acceptance

SOURCE_SHA = "a" * 40
WHEEL_SHA = hashlib.sha256(b"accepted wheel fixture").hexdigest()


def input_report(kind, profile="workstation-composition", *, wheel_sha=WHEEL_SHA):
    report = {
        "ok": True,
        "editor_mode": "owned-amd64",
        "coverage": acceptance.coverage(profile),
        "acceptance": {
            **acceptance.coverage(profile),
            "implementation": "installed-wheel",
            "source_sha": SOURCE_SHA,
            "wheel_sha256": wheel_sha,
        },
        "runtime": {
            "implementation": "installed-wheel",
            "preloaded_application_modules": [],
            "module_origins": {
                name: "docking/__init__.py"
                if name == "docking"
                else name.replace(".", "/") + ".py"
                for name in (
                    "docking",
                    "docking.applets.keyboardlayout.implementations.windows.backend",
                    "docking.platform.native.windows.input_thread",
                    "docking.platform.native.windows.input_service",
                )
            },
            "helpers": [
                {
                    "file": f"docking/platform/native/windows/input-thread-{bits}.dll",
                    "present": True,
                    "machine": machine,
                    "sha256": "b" * 64,
                }
                for bits, machine in ((64, "0x8664"), (32, "0x014c"))
            ],
        },
        "cleanup": [{"name": "owned-cleanup", "ok": True}],
    }
    if kind == "input-profiles":
        report.update(
            dict.fromkeys(
                (
                    "editors_closed",
                    "profiles_restored",
                    "text_service_references_released",
                    "owned_classic_released",
                    "loaded_set_restored",
                    "caller_layout_restored",
                    "preferences_restored",
                    "independent_loaded_set_restored",
                ),
                True,
            )
        )
        report.update(composition_failures=[], stages=[])
        for bits in (64, 32):
            stage = {
                "architecture": bits,
                "synthetic_layout_hidden": True,
                "returned_to_classic": True,
                "classic_switches": [
                    {"requested": value, "readback": value, "caller_unchanged": True}
                    for value in ("040c040c", "04090409")
                ],
                "profiles": [],
            }
            report["stages"].append(stage)
            for code in sorted(acceptance.PROFILE_CODES):
                selected = {"requested": code, "passed": True, "variants": []}
                stage["profiles"].append(selected)
                for route in (
                    ("scan-code", "mapped-vk")
                    if profile == "workstation-composition"
                    else ("activation",)
                ):
                    variant = {
                        "routing": route,
                        "passed": True,
                        "readback": {
                            "profile": {
                                "kind": 1,
                                "language": 1066,
                                "clsid": code.split(":")[2],
                                "identifier": code.split(":")[3],
                            }
                        },
                        "classic_reset": {"layout": 0x04090409, "profile": {"kind": 2}},
                    }
                    if profile == "server-api":
                        variant.update(
                            composition="not-requested", activation_verified=True
                        )
                    else:
                        variant.update(
                            expected="â", text="â ", ime_mode={"restored": True}
                        )
                    selected["variants"].append(variant)
        for key in ("queued_cancellation", "queued_classic_cancellation"):
            report[key] = {
                "posted": True,
                "cancelled": True,
                "unchanged_after_resume": True,
            }
    else:
        report.update(
            dict.fromkeys(
                (
                    "normal_shutdown",
                    "editor_closed",
                    "editor_layout_restored",
                    "caller_layout_restored",
                    "owned_layouts_released",
                    "loaded_set_restored",
                    "preferences_restored",
                    "independent_loaded_set_restored",
                    "search_shortcuts_released",
                    "unavailable_layout_recovered",
                    "conflict_recovered",
                    "shutdown_with_unavailable_layout",
                ),
                True,
            )
        )
        report["direct_requests"] = {
            "controls_passed": True,
            "controls": [
                {
                    "architecture": bits,
                    "ok": True,
                    "closed": True,
                    "readiness": {
                        "ready": True,
                        "attempts": [
                            {"available": True, "snapshot": {"layout": 0x04090409}}
                        ],
                    },
                    "operations": [
                        {
                            "name": name,
                            "ok": True,
                            "snapshot": {
                                "layout": 0x040C040C
                                if name.endswith("-fr")
                                else 0x04090409,
                                "profile": {
                                    "kind": 2,
                                    "layout": 0x040C040C
                                    if name.endswith("-fr")
                                    else 0x04090409,
                                },
                            },
                        }
                        for name in acceptance.CONTROL_OPERATIONS
                    ],
                }
                for bits in (64, 32)
            ],
        }
        report["characters"] = {"us": "q", "fr": "a", "fr-dead": "ê", "dvorak": "'"}
        report["search_characters"] = {
            name: {"text": value} for name, value in report["characters"].items()
        }
        report["punctuation_keys"] = {"us": 186, "fr": 190}
        report["recovered_character_key"] = 0x32
        report["altgr"] = {
            "stage": "complete",
            "editor": "€",
            "search": "€",
            "capture_ignored_text": True,
            "shortcut_resumed": True,
        }
        report["search_profiles"] = {
            "original_enabled": {"fixture": False},
            "enabled_after": {"fixture": False},
            "available": True,
            "restored": True,
            "composition": [],
        }
        for code in sorted(acceptance.PROFILE_CODES):
            evidence = {"profile_readback": code}
            if profile == "server-api":
                evidence.update(activation_verified=True, composition="not-requested")
            else:
                evidence.update(
                    dict.fromkeys(
                        (
                            "enter_committed_preedit",
                            "escape_cancelled_preedit",
                            "visible_after_commit",
                            "focused_after_commit",
                            "visible_after_cancel",
                            "focused_after_cancel",
                        ),
                        True,
                    )
                )
                evidence.update(
                    text_after_commit="â",
                    text_after_cancel="âa",
                    native_expected_after_cancel="âa",
                    preedit_before_commit=["a", "â"],
                    preedit_before_cancel=["a"],
                    preedit_after_commit=["a", "â", ""],
                    preedit_after_cancel=["a", ""],
                )
            report["search_profiles"]["composition"].append(
                {
                    "profile": code,
                    **copy.deepcopy(evidence),
                    "reference": copy.deepcopy(evidence),
                }
            )
    return report


def write_input_artifact(
    directory: Path, *, wheel_sha=WHEEL_SHA, profile="workstation-composition"
):
    directory.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    save(
        "input-candidate.json",
        {
            "schema": 1,
            "source_sha": SOURCE_SHA,
            "wheel_sha256": wheel_sha,
            "wheel_file": "candidate.whl",
        },
    )
    save(
        "client-environment.json",
        {
            "windows_product_type": 1,
            "windows_build": 26200,
            "actual_process_machine": 0x8664,
            "native_machine": 0xAA64,
            "pointer_bits": 64,
            "msystem": "UCRT64",
        },
    )
    save(
        "desktop-preparation.json",
        {
            "guard": {
                "product_type": 1,
                "build": 26200,
                "native_machine": 0xAA64,
                "session": 2,
            },
            "ok": True,
            "prepared": True,
            "collector_started": True,
            "collector_stopped": True,
            "desktop_guard": "main-thread",
            "input_status": 0,
            "policy": {"previous": None, "applied": True, "restored": True},
        },
    )
    save(
        "input-domain.json",
        {
            "ok": True,
            "cancelled": False,
            "coverage": acceptance.coverage(profile),
            "wheel_sha256": wheel_sha,
            "source_sha": SOURCE_SHA,
            "checks": [
                {"name": name, "passed": True, "returncode": 0}
                for name in ("search-layouts", "input-profiles")
            ],
        },
    )
    for kind in ("search-layouts", "input-profiles"):
        save(kind + "/result.json", input_report(kind, profile, wheel_sha=wheel_sha))


@pytest.mark.parametrize("profile", acceptance.PROFILES)
@pytest.mark.parametrize("kind", ["search-layouts", "input-profiles"])
def test_explicit_profile_requires_all_its_own_results(kind, profile):
    acceptance.validate_input_report(
        input_report(kind, profile),
        kind=kind,
        profile=profile,
        wheel_sha256=WHEEL_SHA,
        source_sha=SOURCE_SHA,
    )
    with pytest.raises(ValueError, match="coverage"):
        acceptance.validate_input_report(
            input_report(kind, profile),
            kind=kind,
            profile=next(p for p in acceptance.PROFILES if p != profile),
            wheel_sha256=WHEEL_SHA,
            source_sha=SOURCE_SHA,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "control",
        "missing-readback",
        "preedit",
        "raw-text",
        "cleanup",
        "origin",
        "profile",
        "schema",
        "stale-source",
        "wrong-wheel",
    ],
)
def test_workstation_evidence_rejects_missing_tampered_or_stale_phase(mutation):
    report = input_report("search-layouts")
    if mutation == "control":
        report["direct_requests"]["controls"][1]["operations"].pop()
    elif mutation == "missing-readback":
        report["direct_requests"]["controls"][0]["operations"][0]["snapshot"] = None
    elif mutation == "preedit":
        report["search_profiles"]["composition"][0]["reference"][
            "preedit_before_commit"
        ] = []
    elif mutation == "raw-text":
        report["search_profiles"]["composition"][0]["text_after_commit"] = "a\u0309"
    elif mutation == "cleanup":
        report["cleanup"][0]["ok"] = False
    elif mutation == "origin":
        report["runtime"]["module_origins"]["docking"] = "/source/docking/__init__.py"
    elif mutation == "profile":
        report["search_profiles"]["composition"].pop()
    elif mutation == "schema":
        report["acceptance"]["schema"] = True
    elif mutation == "stale-source":
        report["acceptance"]["source_sha"] = "b" * 40
    elif mutation == "wrong-wheel":
        report["acceptance"]["wheel_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        acceptance.validate_input_report(
            report,
            kind="search-layouts",
            profile="workstation-composition",
            wheel_sha256=WHEEL_SHA,
            source_sha=SOURCE_SHA,
        )


@pytest.mark.parametrize(
    "mutation", ["missing-cell", "raw-telex", "raw-vni", "cancel", "restore"]
)
def test_native_matrix_cannot_hide_any_failed_cell(mutation):
    report = input_report("input-profiles")
    variant = report["stages"][1]["profiles"][0]["variants"][0]
    if mutation == "missing-cell":
        report["stages"][1]["profiles"][0]["variants"].pop()
    elif mutation == "raw-telex":
        variant["text"] = "aa "
    elif mutation == "raw-vni":
        variant["text"] = "a\u0309 "
    elif mutation == "cancel":
        report["queued_cancellation"]["unchanged_after_resume"] = False
    elif mutation == "restore":
        variant["ime_mode"]["restored"] = False
    with pytest.raises(ValueError):
        acceptance.validate_input_report(
            report,
            kind="input-profiles",
            profile="workstation-composition",
            wheel_sha256=WHEEL_SHA,
            source_sha=SOURCE_SHA,
        )


def test_artifact_requires_both_original_reports_and_exact_candidate(tmp_path):
    write_input_artifact(tmp_path)
    acceptance.validate_input_artifact(
        tmp_path, wheel_sha256=WHEEL_SHA, source_sha=SOURCE_SHA
    )
    with pytest.raises(ValueError):
        acceptance.validate_input_artifact(
            tmp_path, wheel_sha256="c" * 64, source_sha=SOURCE_SHA
        )
    with pytest.raises(ValueError):
        acceptance.validate_input_artifact(
            tmp_path, wheel_sha256=WHEEL_SHA, source_sha="b" * 40
        )
    (tmp_path / "input-profiles/result.json").unlink()
    with pytest.raises(ValueError, match="Incomplete"):
        acceptance.validate_input_artifact(
            tmp_path, wheel_sha256=WHEEL_SHA, source_sha=SOURCE_SHA
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("windows_product_type", 3),
        ("windows_build", 20348),
        ("actual_process_machine", 0xAA64),
    ],
)
def test_server_or_wrong_process_cannot_claim_workstation_proof(tmp_path, field, value):
    write_input_artifact(tmp_path)
    path = tmp_path / "client-environment.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Windows 11"):
        acceptance.validate_input_artifact(
            tmp_path, wheel_sha256=WHEEL_SHA, source_sha=SOURCE_SHA
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("source_sha", "b" * 40),
        ("wheel_sha256", "c" * 64),
        ("wheel_file", "../outside.whl"),
    ],
)
def test_candidate_rejects_stale_tampered_and_path_ambiguous_metadata(
    tmp_path, field, value
):
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"accepted wheel fixture")
    record = acceptance.candidate(wheel, SOURCE_SHA)
    record[field] = value
    with pytest.raises(ValueError):
        acceptance.validate_candidate(
            record, wheel_sha256=WHEEL_SHA, source_sha=SOURCE_SHA
        )


def test_input_validation_imports_no_application_gui_or_windows_process_dependency():
    script = (
        "import sys; import tools.windows.input_acceptance; "
        "assert not any(n == 'docking' or n.startswith('docking.') for n in sys.modules); "
        "assert 'gi' not in sys.modules and 'psutil' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, timeout=10
    )


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "unprepared",
        "failed",
        "collector-running",
        "input-failed",
        "policy-unrestored",
        "policy-unapplied",
        "policy-before-missing",
        "guard-error",
        "guard-mismatch",
    ],
)
def test_workstation_requires_original_guard_completion_and_exact_policy_cleanup(
    tmp_path, failure
):
    write_input_artifact(tmp_path)
    path = tmp_path / "desktop-preparation.json"
    report = json.loads(path.read_text())
    if failure == "missing":
        path.unlink()
    elif failure == "unprepared":
        report["prepared"] = False
    elif failure == "failed":
        report["ok"] = False
    elif failure == "collector-running":
        report["collector_stopped"] = False
    elif failure == "input-failed":
        report["input_status"] = 1
    elif failure == "policy-unrestored":
        report["policy"]["restored"] = False
    elif failure == "policy-unapplied":
        report["policy"]["applied"] = False
    elif failure == "policy-before-missing":
        del report["policy"]["previous"]
    elif failure == "guard-error":
        report["error"] = {"operation": "Privacy policy restoration failed", "code": 5}
    elif failure == "guard-mismatch":
        report["guard"]["build"] = 26100
    if failure != "missing":
        path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        acceptance.validate_input_artifact(
            tmp_path, wheel_sha256=WHEEL_SHA, source_sha=SOURCE_SHA
        )
