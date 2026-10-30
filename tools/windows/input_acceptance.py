"""Explicit Windows input coverage and exact candidate-wheel evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import TypedDict, cast


class Candidate(TypedDict):
    schema: int
    source_sha: str
    wheel_file: str
    wheel_sha256: str


class Coverage(TypedDict):
    schema: int
    profile: str
    composition: str


class Acceptance(Coverage):
    implementation: str
    source_sha: str
    wheel_sha256: str


PROFILES = ("server-api", "workstation-composition")
PROFILE_CODES = frozenset(
    "tsf:042a:c2cb2cf0-af47-413e-9780-8bc3a3c16068:" + identifier
    for identifier in (
        "591ae943-56be-48f6-8966-06b43915cc5a",
        "5fb02ec5-0a77-4684-b4fa-def8a2195628",
    )
)
CONTROL_OPERATIONS = (
    "initial-read",
    "classic-fr",
    "classic-us",
    "tsf-keyboard-fr",
    "tsf-keyboard-us",
    "final-read",
)


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Input evidence requires an object")
    return cast(dict[str, object], value)


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Input evidence requires explicit result rows")
    return cast(list[dict[str, object]], value)


def _true(record: dict[str, object], *names: str) -> None:
    if any(record.get(name) is not True for name in names):
        raise ValueError("Incomplete input evidence: " + ", ".join(names))


def _identity(wheel_sha256: str, source_sha: str) -> None:
    if (
        not isinstance(wheel_sha256, str)
        or not isinstance(source_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", wheel_sha256)
        or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
    ):
        raise ValueError(
            "Input evidence requires exact wheel and source SHA identities"
        )


def coverage(profile: str) -> Coverage:
    if profile not in PROFILES:
        raise ValueError("Unknown Windows input coverage profile")
    return {
        "schema": 1,
        "profile": profile,
        "composition": "required"
        if profile == "workstation-composition"
        else "not-requested",
    }


def candidate(wheel: Path, source_sha: str) -> Candidate:
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    _identity(checksum, source_sha)
    if wheel.suffix != ".whl":
        raise ValueError("Input candidate must be a wheel")
    return {
        "schema": 1,
        "source_sha": source_sha,
        "wheel_file": wheel.name,
        "wheel_sha256": checksum,
    }


def validate_candidate(value: object, *, wheel_sha256: str, source_sha: str) -> None:
    _identity(wheel_sha256, source_sha)
    value = _record(value)
    name = value.get("wheel_file")
    if (
        type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("source_sha") != source_sha
        or value.get("wheel_sha256") != wheel_sha256
        or not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name) is None
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValueError(
            "Input candidate identity does not match this source and wheel"
        )


def runtime_acceptance(
    root: Path, wheel: Path, *, source_sha: str, profile: str
) -> Acceptance:
    # This verifier is stdlib-only and compares all installed docking/* bytes,
    # rejecting missing/extra/changed files before any production import.
    from tools.windows.visual.tray_acceptance import verify_installed_wheel

    checksum = verify_installed_wheel(root, wheel)
    _identity(checksum, source_sha)
    return {
        **coverage(profile),
        "implementation": "installed-wheel",
        "source_sha": source_sha,
        "wheel_sha256": checksum,
    }


def _preedit(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(s, str) for s in value)
        and any(value)
    )


def _ended_preedit(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(s, str) for s in value)
        and bool(value[-1] == "")
    )


def _gtk_profile(value: dict[str, object], code: str, profile: str) -> None:
    if value.get("profile_readback") != code:
        raise ValueError("GTK input did not read back the exact requested profile")
    if profile == "server-api":
        _true(value, "activation_verified")
        if value.get("composition") != "not-requested":
            raise ValueError("Server API evidence must not claim composition")
        if any(
            key in value
            for key in ("text_after_commit", "preedit_before_commit", "ime_mode")
        ):
            raise ValueError("Server API evidence contains a composition claim")
        return
    _true(
        value,
        "enter_committed_preedit",
        "escape_cancelled_preedit",
        "visible_after_commit",
        "focused_after_commit",
        "visible_after_cancel",
        "focused_after_cancel",
    )
    if (
        value.get("text_after_commit") != "â"
        or not _preedit(value.get("preedit_before_commit"))
        or not _preedit(value.get("preedit_before_cancel"))
        or not _ended_preedit(value.get("preedit_after_commit"))
        or not _ended_preedit(value.get("preedit_after_cancel"))
    ):
        raise ValueError("Missing genuine GTK composition, Return or Escape evidence")


def _search(report: dict[str, object], profile: str) -> None:
    _true(
        report,
        "normal_shutdown",
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
    )
    mode = report.get("editor_mode")
    if mode == "owned-amd64":
        _true(report, "editor_closed")
    elif mode == "stock-notepad":
        _true(report, "notepad_closed")
    else:
        raise ValueError("Input evidence has no explicit owned-editor mode")
    direct = _record(report.get("direct_requests"))
    _true(direct, "controls_passed")
    controls = _rows(direct.get("controls"))
    if len(controls) != 2 or {c.get("architecture") for c in controls} != {32, 64}:
        raise ValueError("Input evidence requires both control architectures")
    for control in controls:
        _true(control, "ok", "closed")
        readiness = _record(control.get("readiness"))
        _true(readiness, "ready")
        attempts = _rows(readiness.get("attempts"))
        if (
            not 1 <= len(attempts) <= 3
            or attempts[-1].get("available") is not True
            or not _record(attempts[-1].get("snapshot"))
        ):
            raise ValueError("Input controls lack a successful owned readiness read")
        operations = _rows(control.get("operations"))
        if [op.get("name") for op in operations] != list(CONTROL_OPERATIONS):
            raise ValueError("Input evidence requires all twelve request controls")
        for operation in operations:
            _true(operation, "ok")
            snapshot = _record(operation.get("snapshot"))
            layout = snapshot.get("layout")
            if type(layout) is not int or layout <= 0 or "error" in operation:
                raise ValueError(
                    "Measured input control lacks a successful native readback"
                )
            name = operation["name"]
            if name != "initial-read":
                expected = 0x040C040C if str(name).endswith("-fr") else 0x04090409
                if layout & 0xFFFFFFFF != expected:
                    raise ValueError("Measured input control returned the wrong layout")
            if str(name).startswith("tsf-keyboard-"):
                keyboard = _record(snapshot.get("profile"))
                if keyboard.get("kind") != 2 or keyboard.get("layout") != layout:
                    raise ValueError(
                        "Measured TSF keyboard control lacks profile readback"
                    )
    characters = {"us": "q", "fr": "a", "fr-dead": "ê", "dvorak": "'"}
    if report.get("characters") != characters:
        raise ValueError("Missing native classic/dead-key layout evidence")
    search = _record(report.get("search_characters"))
    if any(
        _record(search.get(name)).get("text") != text
        for name, text in characters.items()
    ):
        raise ValueError("Missing Search classic/dead-key layout evidence")
    altgr = _record(report.get("altgr"))
    _true(altgr, "capture_ignored_text", "shortcut_resumed")
    if (
        altgr.get("stage") != "complete"
        or altgr.get("editor") != "€"
        or altgr.get("search") != "€"
    ):
        raise ValueError("Missing AltGr and shortcut recovery evidence")
    punctuation = _record(report.get("punctuation_keys"))
    if (
        set(punctuation) != {"us", "fr"}
        or any(
            type(key) is not int or not 0 < key <= 255 for key in punctuation.values()
        )
        or punctuation["us"] == punctuation["fr"]
        or report.get("recovered_character_key") != 0x32
    ):
        raise ValueError("Missing layout-dependent punctuation shortcut recovery")
    profiles = _record(report.get("search_profiles"))
    _true(profiles, "available", "restored")
    if not isinstance(profiles.get("original_enabled"), dict) or profiles[
        "original_enabled"
    ] != profiles.get("enabled_after"):
        raise ValueError("Search fixture profile state did not restore")
    rows = _rows(profiles.get("composition"))
    if len(rows) != 2 or {r.get("profile") for r in rows} != PROFILE_CODES:
        raise ValueError("Input evidence requires both plain-GTK and Search profiles")
    for row in rows:
        reference = _record(row.get("reference"))
        _gtk_profile(reference, cast(str, row["profile"]), profile)
        _gtk_profile(row, cast(str, row["profile"]), profile)
        if profile == "workstation-composition" and (
            row.get("text_after_cancel") != reference.get("text_after_cancel")
            or row.get("native_expected_after_cancel")
            != reference.get("text_after_cancel")
        ):
            raise ValueError("Search cancellation differs from the plain GTK reference")


def _native(report: dict[str, object], profile: str) -> None:
    _true(
        report,
        "editors_closed",
        "profiles_restored",
        "text_service_references_released",
        "owned_classic_released",
        "loaded_set_restored",
        "caller_layout_restored",
        "preferences_restored",
        "independent_loaded_set_restored",
    )
    stages = _rows(report.get("stages"))
    if len(stages) != 2 or {s.get("architecture") for s in stages} != {32, 64}:
        raise ValueError("Input evidence requires both native architectures")
    for stage in stages:
        _true(stage, "synthetic_layout_hidden", "returned_to_classic")
        switches = _rows(stage.get("classic_switches"))
        if len(switches) != 2 or any(
            not row.get("requested")
            or row.get("requested") != row.get("readback")
            or row.get("caller_unchanged") is not True
            for row in switches
        ):
            raise ValueError("Missing direct classic-switch evidence")
        profiles = _rows(stage.get("profiles"))
        if (
            len(profiles) != 2
            or {p.get("requested") for p in profiles} != PROFILE_CODES
        ):
            raise ValueError("Input evidence requires both native text profiles")
        for selected in profiles:
            _true(selected, "passed")
            variants = _rows(selected.get("variants"))
            routes = (
                {"scan-code", "mapped-vk"}
                if profile == "workstation-composition"
                else {"activation"}
            )
            if (
                len(variants) != len(routes)
                or {v.get("routing") for v in variants} != routes
            ):
                raise ValueError("Input evidence omitted a required profile variant")
            for variant in variants:
                _true(variant, "passed")
                selected_profile = _record(
                    _record(variant.get("readback")).get("profile")
                )
                code = (
                    f"tsf:{selected_profile.get('language', 0):04x}:"
                    f"{selected_profile.get('clsid')}:{selected_profile.get('identifier')}"
                )
                classic = _record(variant.get("classic_reset"))
                if (
                    selected_profile.get("kind") != 1
                    or code != selected["requested"]
                    or classic.get("layout") != 0x04090409
                    or _record(classic.get("profile")).get("kind") != 2
                ):
                    raise ValueError(
                        "Native profile selection/classic-return readback mismatch"
                    )
                if profile == "workstation-composition":
                    if (
                        variant.get("expected") != "â"
                        or str(variant.get("text", "")).strip() != "â"
                    ):
                        raise ValueError("Native input did not compose genuine â")
                    _true(_record(variant.get("ime_mode")), "restored")
                elif (
                    variant.get("composition") != "not-requested"
                    or variant.get("activation_verified") is not True
                ):
                    raise ValueError(
                        "Server evidence must explicitly prove activation only"
                    )
                if profile == "server-api" and any(
                    key in variant
                    for key in ("text", "expected", "ime_mode", "after_restore")
                ):
                    raise ValueError(
                        "Server API evidence contains a composition attempt"
                    )
    for key in ("queued_cancellation", "queued_classic_cancellation"):
        _true(_record(report.get(key)), "posted", "cancelled", "unchanged_after_resume")
    if report.get("composition_failures") != []:
        raise ValueError("Input report contains failed composition attempts")


def _validate_input_report(
    value: object, *, kind: str, profile: str, wheel_sha256: str, source_sha: str
) -> None:
    _identity(wheel_sha256, source_sha)
    report = _record(value)
    _true(report, "ok")
    expected = {
        **coverage(profile),
        "implementation": "installed-wheel",
        "source_sha": source_sha,
        "wheel_sha256": wheel_sha256,
    }
    accepted = _record(report.get("acceptance"))
    selected = _record(report.get("coverage"))
    if (
        type(accepted.get("schema")) is not int
        or type(selected.get("schema")) is not int
        or accepted != expected
        or selected != coverage(profile)
    ):
        raise ValueError("Input coverage does not match the required candidate/profile")
    if report.get("error") is not None:
        raise ValueError("Input report retains an error")
    runtime = _record(report.get("runtime"))
    if (
        runtime.get("implementation") != "installed-wheel"
        or runtime.get("preloaded_application_modules") != []
    ):
        raise ValueError("Input report does not prove an isolated installed runtime")
    origins = _record(runtime.get("module_origins"))
    required = {
        "docking",
        "docking.applets.keyboardlayout.windows",
        "docking.platform.backends.windows.input_thread",
        "docking.platform.backends.windows.input_service",
    }
    if not required.issubset(origins) or any(
        not isinstance(path, str)
        or not path.startswith("docking/")
        or ".." in path.split("/")
        or "\\" in path
        for path in origins.values()
    ):
        raise ValueError("Input report lacks installed module origins")
    helpers = _rows(runtime.get("helpers"))
    if len(helpers) != 2 or {row.get("machine") for row in helpers} != {
        "0x8664",
        "0x014c",
    }:
        raise ValueError("Input evidence lacks both native helper architectures")
    for row in helpers:
        bits = 64 if row["machine"] == "0x8664" else 32
        checksum = row.get("sha256")
        if (
            row.get("present") is not True
            or row.get("file")
            != f"docking/platform/backends/windows/input-thread-{bits}.dll"
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise ValueError("Input helper identity is incomplete")
    cleanup = _rows(report.get("cleanup"))
    if not cleanup:
        raise ValueError("Input evidence lacks cleanup results")
    for row in cleanup:
        _true(row, "ok")
    if kind == "search-layouts":
        _search(report, profile)
    elif kind == "input-profiles":
        _native(report, profile)
    else:
        raise ValueError("Unknown input evidence kind")


def validate_input_report(
    value: object, *, kind: str, profile: str, wheel_sha256: str, source_sha: str
) -> None:
    try:
        _validate_input_report(
            value,
            kind=kind,
            profile=profile,
            wheel_sha256=wheel_sha256,
            source_sha=source_sha,
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Malformed Windows input evidence") from error


def validate_input_artifact(
    directory: Path,
    *,
    wheel_sha256: str,
    source_sha: str,
    profile: str = "workstation-composition",
) -> None:
    """Require the collector and both original reports, never a summary alone."""

    def read(name: str):
        try:
            return json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Incomplete Windows input artifact: " + name) from error

    validate_candidate(
        read("input-candidate.json"), wheel_sha256=wheel_sha256, source_sha=source_sha
    )
    if profile == "workstation-composition":
        platform = _record(read("client-environment.json"))
        build = platform.get("windows_build")
        if (
            type(platform.get("windows_product_type")) is not int
            or platform["windows_product_type"] != 1
            or type(build) is not int
            or build < 22000
            or platform.get("actual_process_machine") != 0x8664
            or platform.get("pointer_bits") != 64
            or platform.get("msystem") != "UCRT64"
        ):
            raise ValueError(
                "Composition acceptance requires a Windows 11 AMD64 runtime"
            )
        preparation = _record(read("desktop-preparation.json"))
        _true(preparation, "ok", "prepared", "collector_started", "collector_stopped")
        policy = _record(preparation.get("policy"))
        _true(policy, "applied", "restored")
        guard = _record(preparation.get("guard"))
        session = guard.get("session")
        if (
            "error" in preparation
            or preparation.get("cancelled", False) is not False
            or preparation.get("desktop_guard") != "main-thread"
            or type(preparation.get("input_status")) is not int
            or preparation["input_status"] != 0
            or type(guard.get("product_type")) is not int
            or guard.get("product_type") != platform["windows_product_type"]
            or guard.get("build") != build
            or guard.get("native_machine") != 0xAA64
            or guard.get("native_machine") != platform.get("native_machine")
            or type(session) is not int
            or session <= 0
            or "previous" not in policy
            or "error" in policy
            or "restore_error" in policy
        ):
            raise ValueError("Hosted desktop preparation/cleanup did not complete")
    domain = _record(read("input-domain.json"))
    if (
        type(_record(domain.get("coverage")).get("schema")) is not int
        or domain.get("coverage") != coverage(profile)
        or domain.get("wheel_sha256") != wheel_sha256
        or domain.get("source_sha") != source_sha
    ):
        raise ValueError(
            "Input collector identity/coverage does not match the candidate"
        )
    _true(domain, "ok")
    if domain.get("cancelled") is not False:
        raise ValueError("Cancelled input acceptance")
    checks = _rows(domain.get("checks"))
    if [c.get("name") for c in checks] != ["search-layouts", "input-profiles"]:
        raise ValueError("Input domain omitted a required report")
    for check in checks:
        _true(check, "passed")
        if type(check.get("returncode")) is not int or check["returncode"] != 0:
            raise ValueError("Input child did not exit successfully")
        name = cast(str, check["name"])
        validate_input_report(
            read(name + "/result.json"),
            kind=name,
            profile=profile,
            wheel_sha256=wheel_sha256,
            source_sha=source_sha,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    record = candidate(args.wheel, args.source_sha)
    if args.verify:
        validate_candidate(
            json.loads(args.output.read_text(encoding="utf-8")),
            wheel_sha256=record["wheel_sha256"],
            source_sha=args.source_sha,
        )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
