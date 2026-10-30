"""Stage Windows packages accepted by the native CI job for GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import zipfile
from pathlib import Path
from typing import TypedDict, cast

from packaging.version import InvalidVersion, Version

from tools.windows.application_acceptance import validate_applications_report
from tools.windows.bundle import verify_archive
from tools.windows.visual.tray_provider import provider_cases

REQUIRED_CHECKS = (
    "in_use_upgrade_rejected",
    "in_use_uninstall_rejected",
    "upgrade_preserved_startup_and_previous_payload",
    "startup_disabled",
    "installed_desktop",
    "uninstall_preserved_preferences_and_unknown_files",
    "distinct_version_upgrade",
)

_TRAY_PROFILES = ("explorer", "backend", "gtk-v0-v4", "gtk-v3-v4-guid")


class ResourceTotals(TypedDict):
    rss_bytes: int
    private_bytes: int
    threads: int
    handles: int
    gdi: int
    user: int


class ProcessSample(ResourceTotals):
    pid: int
    created: float
    cpu_seconds: float


class SessionSample(ResourceTotals):
    elapsed_seconds: float
    responsive: bool
    processes: list[ProcessSample]


def archive_identity(path: Path) -> dict[str, str | int]:
    """Verify portable bytes without extraction and identify the tested payload."""
    archive_sha256 = digest(path)
    with zipfile.ZipFile(path) as archive:
        inventory, inventory_bytes = verify_archive(archive)
        if archive.getinfo("Docking/runtime/share/docking/runtime.json").file_size > (
            8 * 1024 * 1024
        ):
            raise ValueError("Windows runtime metadata exceeds its size limit")
        metadata = json.loads(
            archive.read("Docking/runtime/share/docking/runtime.json")
        )
    if not isinstance(metadata, dict):
        raise ValueError("Windows package lacks its version")
    version = metadata.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("Windows package lacks its version")
    wheel = metadata.get("wheel_sha256")
    if not isinstance(wheel, str) or not re.fullmatch("[0-9a-f]{64}", wheel):
        raise ValueError("Windows package lacks its wheel digest")
    if digest(path) != archive_sha256:
        raise ValueError("Windows archive changed during verification")
    return {
        "archive_sha256": archive_sha256,
        "payload_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "wheel_sha256": wheel,
        "version": version,
        "payload_files": len(inventory),
    }


def validate_installer_report(
    report: object, candidate: dict[str, str | int], installer_sha256: str
) -> None:
    """Bind native upgrade acceptance to both actual package identities."""
    expected = {**candidate, "installer_sha256": installer_sha256}
    if (
        not isinstance(report, dict)
        or "error" in report
        or type(report.get("acceptance_schema")) is not int
        or report["acceptance_schema"] != 1
        or report.get("installer_sha256") != installer_sha256
        or report.get("candidate") != expected
        or any(report.get(check) is not True for check in REQUIRED_CHECKS)
        or any(
            type(report.get(check)) is not int
            or report[check] != candidate["payload_files"]
            for check in ("verified_payload_files", "verified_final_payload_files")
        )
    ):
        raise ValueError("Windows packages lack matching installer acceptance")
    previous = report.get("previous")
    if (
        not isinstance(previous, dict)
        or not isinstance(previous.get("version"), str)
        or any(
            not isinstance(previous.get(key), str)
            or re.fullmatch("[0-9a-f]{64}", previous[key]) is None
            for key in (
                "archive_sha256",
                "installer_sha256",
                "payload_inventory_sha256",
                "wheel_sha256",
            )
        )
        or type(previous.get("payload_files")) is not int
        or previous["payload_files"] <= 0
        or type(report.get("previous_verified_payload_files")) is not int
        or report.get("previous_verified_payload_files") != previous["payload_files"]
        or any(
            previous[key] == expected[key]
            for key in (
                "archive_sha256",
                "installer_sha256",
                "payload_inventory_sha256",
                "wheel_sha256",
            )
        )
    ):
        raise ValueError("Windows installer lacks previous-version acceptance")
    try:
        older = Version(previous["version"]) < Version(str(candidate["version"]))
    except InvalidVersion as exc:
        raise ValueError("Windows installer upgrade versions are invalid") from exc
    if not older:
        raise ValueError("Windows installer did not upgrade a distinct older version")


def summarize_samples(samples: list[SessionSample]) -> dict[str, float | int]:
    """Summarize bounded observations without combining reused process IDs."""
    if len(samples) < 2:
        raise ValueError("Session measurements require at least two samples")
    previous = -1.0
    cpu: dict[tuple[int, float], float] = {}
    for sample in samples:
        elapsed = sample["elapsed_seconds"]
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed < 0
            or elapsed <= previous
        ):
            raise ValueError("Session sample times must increase")
        previous = elapsed
        seen = set()
        for process in sample["processes"]:
            key = process["pid"], process["created"]
            value = process["cpu_seconds"]
            if (
                type(key[0]) is not int
                or key[0] <= 0
                or type(key[1]) not in (int, float)
                or not math.isfinite(key[1])
                or key[1] <= 0
                or key in seen
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or value < cpu.get(key, 0)
            ):
                raise ValueError("Process CPU measurements regressed")
            seen.add(key)
            cpu[key] = value
    first, last = samples[0], samples[-1]
    elapsed = last["elapsed_seconds"] - first["elapsed_seconds"]
    observed_cpu = sum(cpu.values()) - sum(
        process["cpu_seconds"] for process in first["processes"]
    )
    return {
        "elapsed_seconds": elapsed,
        "observed_cpu_seconds": observed_cpu,
        "cpu_percent_one_core": observed_cpu / elapsed * 100,
        "rss_bytes_delta": last["rss_bytes"] - first["rss_bytes"],
        "private_bytes_delta": last["private_bytes"] - first["private_bytes"],
        "handles_delta": last["handles"] - first["handles"],
        "gdi_delta": last["gdi"] - first["gdi"],
        "user_delta": last["user"] - first["user"],
        "observed_process_instances": len(cpu),
    }


def validate_readiness_report(report: object, identity: dict[str, str | int]) -> None:
    """Require the declared three-startup/two-minute release coverage, not an SLA."""
    if (
        not isinstance(report, dict)
        or report.get("ok") is not True
        or "error" in report
        or not isinstance(report.get("acceptance"), dict)
        or type(report["acceptance"].get("schema")) is not int
        or report.get("acceptance")
        != {
            "schema": 1,
            "implementation": "portable-bundle",
            **identity,
        }
    ):
        raise ValueError("Windows packages lack matching startup/session acceptance")
    try:
        measurement, startups, session = (
            report["measurement"],
            report["startups"],
            report["session"],
        )
        if (
            type(measurement["startup_cycles"]) is not int
            or measurement["startup_cycles"] < 3
            or len(startups) != measurement["startup_cycles"]
            or not math.isfinite(measurement["requested_seconds"])
            or measurement["requested_seconds"] < 120
            or len(session["samples"]) < 3
        ):
            raise ValueError("Windows startup/session coverage is incomplete")
        for row in [*startups, session]:
            if (
                row["normal_shutdown"] is not True
                or row["close_requested"] is not True
                or row["forced"] is not False
                or type(row["exit_code"]) is not int
                or row["exit_code"] != 0
                or row["owned_remaining"] != []
                or row["errors"] != []
            ):
                raise ValueError("Windows startup/session cleanup was incomplete")
        for row in [*startups, session["startup"]]:
            if not (
                math.isfinite(row["visible_seconds"])
                and math.isfinite(row["responsive_seconds"])
                and 0 <= row["visible_seconds"] <= row["responsive_seconds"]
            ):
                raise ValueError("Invalid Windows startup timing")
        for sample in session["samples"]:
            if sample["responsive"] is not True or not sample["processes"]:
                raise ValueError("Windows session did not remain responsive")
            for key in (
                "rss_bytes",
                "private_bytes",
                "threads",
                "handles",
                "gdi",
                "user",
            ):
                if type(sample[key]) is not int or sample[key] < 0:
                    raise ValueError("Invalid Windows resource measurement")
                if any(
                    type(process[key]) is not int or process[key] < 0
                    for process in sample["processes"]
                ) or sample[key] != sum(
                    process[key] for process in sample["processes"]
                ):
                    raise ValueError("Windows resource totals differ from processes")
        summary = summarize_samples(session["samples"])
        if summary != session["summary"] or (
            summary["elapsed_seconds"] < measurement["requested_seconds"] - 0.1
        ):
            raise ValueError("Windows session duration or summary differs from samples")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Incomplete Windows startup/session acceptance") from exc


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Windows tray acceptance requires an object")
    return cast(dict[str, object], value)


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Windows tray acceptance requires checkpoint rows")
    return [_record(row) for row in value]


def _events(row: dict[str, object]) -> set[int]:
    events = set()
    for callback in _rows(row.get("callbacks")):
        event = callback.get("event")
        if type(event) is int:
            events.add(event)
    return events


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _tray_protocol(report: dict[str, object], mode: str) -> None:
    cases = _record(report.get("cases"))
    results = [_record(case) for case in cases.values()]
    if (
        report.get("mode") != mode
        or report.get("evidence") != "protocol-only"
        or report.get("routing_restored") is not True
        or report.get("cursor_restored") is not True
        or set(cases) != {case.name for case in provider_cases()}
        or any(
            case.get("protocol") != "passed"
            or case.get("cleanup_ok") is not True
            or "error" in case
            for case in results
        )
    ):
        raise ValueError("Windows tray API matrix lacks complete acceptance")


def _tray_gtk(report: dict[str, object], profile: str) -> None:
    legacy = 0 if profile == "gtk-v0-v4" else 3
    versions = {"legacy": legacy, "modern": 4}
    options = _record(report.get("options"))
    evidence = _record(report.get("interactions"))
    if (
        any(
            report.get(key) is not True
            for key in ("ok", "normal_shutdown", "host_thread_stopped")
        )
        or type(report.get("fixture_exit")) is not int
        or report["fixture_exit"] != 0
        or options.get("legacy_version") != legacy
        or type(options.get("legacy_version")) is not int
        or options.get("modern_version") != 4
        or type(options.get("modern_version")) is not int
        or options.get("verify_interactions") is not True
        or options.get("guid_only_updates") is not (legacy == 3)
        or type(options.get("verify_geometry")) is not bool
        or (legacy == 0 and options["verify_geometry"] is not True)
        or (legacy == 3 and report.get("guid_only_updates") is not True)
        or evidence.get("physical_input") != "passed"
        or evidence.get("visual") != "captured-not-reviewed"
    ):
        raise ValueError("Windows tray GTK profile lacks physical acceptance")
    hover = _rows(evidence.get("hover"))
    expected_hover = [
        (which, version, showtip)
        for which, version in versions.items()
        for showtip in (False, True, False)
    ]
    if [
        (row.get("which"), row.get("version"), row.get("showtip")) for row in hover
    ] != expected_hover or any(
        row.get("foreground_unchanged") is not True
        or type(row.get("showtip")) is not bool
        or type(row.get("version")) is not int
        or 0x200 not in _events(row)
        or (
            row["version"] == 4
            and row["showtip"] is False
            and not {0x406, 0x407} <= _events(row)
        )
        for row in hover
    ):
        raise ValueError("Windows tray GTK hover acceptance is incomplete")
    keyboard = _rows(evidence.get("keyboard"))
    if [
        (row.get("which"), row.get("version"), row.get("target")) for row in keyboard
    ] != [
        (which, version, target)
        for which, version in versions.items()
        for target in ("icon", "Open")
    ] or any(
        (0x205 if row["version"] == 0 else 0x401) not in _events(row)
        for row in keyboard
    ):
        raise ValueError("Windows tray GTK keyboard acceptance is incomplete")
    for phase in ("focus_return", "duplicate_focus"):
        rows = _record(evidence.get(phase))
        states = [_record(row) for row in rows.values()]
        if set(rows) != set(versions) or any(
            any(
                row.get(key) is not True
                for key in (
                    "native_query_ok",
                    "logical_selection",
                    "target_focus",
                    "row_focus",
                    "popup_visible",
                )
            )
            or not all(
                _positive(row.get(key))
                for key in (
                    "popup_window",
                    "foreground",
                    "native_focus_root",
                    "foreground_pid",
                    "native_focus_pid",
                )
            )
            or row["foreground"] != row["popup_window"]
            or row["native_focus_root"] != row["popup_window"]
            or row["native_focus_pid"] != row["foreground_pid"]
            for row in states
        ):
            raise ValueError("Windows tray GTK focus acceptance is incomplete")
    rejected = _record(evidence.get("ineligible_focus"))
    control = _record(evidence.get("never_local_focus"))
    calls = _rows(control.get("calls"))
    routes = _rows(evidence.get("focus_routes"))
    fixture = _record(report.get("fixture_before_dock"))
    window = fixture.get("window")
    if (
        type(window) is not int
        or window <= 0
        or not _positive(rejected.get("foreground"))
        or not _positive(rejected.get("control_pid"))
        or not any(
            call.get("operation") == 3
            and type(call.get("accepted")) is bool
            and _positive(call.get("window"))
            and any(
                type(route.get("window")) is int
                and route["window"] == call["window"]
                and route.get("forwarded") is True
                and type(route.get("result")) is int
                and bool(route["result"]) is call["accepted"]
                for route in routes
            )
            for call in calls
        )
        or sum(
            route.get("window") == window & 0xFFFFFFFF
            and route.get("forwarded") is False
            for route in routes
        )
        < 5
    ):
        raise ValueError("Windows tray GTK focus routing acceptance is incomplete")


def validate_tray_report(value: object, *, profile: str, wheel_sha256: object) -> None:
    """Apply the same complete tray acceptance contract in CI and release staging."""
    if profile not in _TRAY_PROFILES:
        raise ValueError("Unknown Windows tray acceptance profile")
    if not isinstance(wheel_sha256, str) or not re.fullmatch(
        "[0-9a-f]{64}", wheel_sha256
    ):
        raise ValueError("Windows package lacks its accepted wheel digest")
    report = _record(value)
    if "error" in report:
        raise ValueError("Windows tray report contains an acceptance error")
    if profile != "explorer":
        acceptance = _record(report.get("acceptance"))
        if (
            type(acceptance.get("schema")) is not int
            or acceptance["schema"] != 1
            or acceptance.get("implementation") != "installed-wheel"
            or report.get("runtime") == "source"
            or acceptance.get("wheel_sha256") != wheel_sha256
            or acceptance.get("profile")
            != ("api-matrix" if profile == "backend" else profile)
        ):
            raise ValueError(
                "Windows tray acceptance does not match the packaged wheel"
            )
    if profile in ("explorer", "backend"):
        _tray_protocol(report, profile)
    else:
        _tray_gtk(report, profile)


def _tray_acceptance(directory: Path, wheel_sha256: object) -> None:
    for profile in _TRAY_PROFILES:
        path = directory / "artifacts/windows/tray" / profile / "result.json"
        validate_tray_report(
            json.loads(path.read_text(encoding="utf-8")),
            profile=profile,
            wheel_sha256=wheel_sha256,
        )


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def stage(directory: Path, version: str, destination: Path) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable X.Y.Z project version")
    packages: list[tuple[Path, str, str]] = []
    try:
        for suffix in (".zip", "-setup.exe"):
            package = directory / "dist" / ("docking-windows-x86_64" + suffix)
            checksum = digest(package)
            companion = package.with_suffix(package.suffix + ".sha256")
            if companion.read_text(encoding="utf-8").strip() != (
                checksum + "  " + package.name
            ):
                raise ValueError(f"Windows package checksum mismatch: {package.name}")
            packages.append((package, suffix, checksum))
        identity = archive_identity(packages[0][0])
        report = json.loads(
            (directory / "artifacts/windows/installer/result.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError("Incomplete Windows release artifacts") from exc
    if identity["archive_sha256"] != packages[0][2]:
        raise ValueError("Windows archive changed after its checksum was checked")
    if identity["version"] != version:
        raise ValueError("Windows package does not match the release version")
    validate_installer_report(report, identity, packages[1][2])
    try:
        readiness = json.loads(
            (directory / "artifacts/windows/release-readiness/result.json").read_text(
                encoding="utf-8"
            )
        )
        validate_readiness_report(readiness, identity)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Incomplete Windows startup/session acceptance") from exc
    try:
        applications = json.loads(
            (
                directory / "artifacts/windows/applications-explorer/result.json"
            ).read_text(encoding="utf-8")
        )
        validate_applications_report(
            applications, wheel_sha256=str(identity["wheel_sha256"])
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Incomplete Windows applications acceptance") from exc
    try:
        _tray_acceptance(directory, identity["wheel_sha256"])
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Incomplete Windows tray acceptance artifacts") from exc
    # Preflight every input before adding anything to the shared release folder.
    destination.mkdir(parents=True, exist_ok=True)
    checksums = []
    for package, suffix, checksum in packages:
        for label in (version, "latest"):
            name = f"docking-{label}-windows-x86_64{suffix}"
            shutil.copy2(package, destination / name)
            checksums.append(f"{checksum}  {name}")
    (destination / "SHA256SUMS-windows").write_text(
        "\n".join(sorted(checksums)) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.directory, args.version, args.output)


if __name__ == "__main__":
    main()
