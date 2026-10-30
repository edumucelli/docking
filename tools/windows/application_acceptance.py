"""Validate complete installed-wheel application/Explorer acceptance without GI."""

from __future__ import annotations

import re
from typing import cast

_APPLICATION_CHECKS = frozenset(
    (
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
)


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Windows application acceptance requires an object")
    return cast(dict[str, object], value)


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Windows application acceptance requires checkpoint rows")
    return [_record(row) for row in value]


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _application_runtime(
    value: object, wheel_sha256: str, origins: dict[str, str]
) -> None:
    runtime = _record(value)
    if (
        runtime.get("implementation") != "installed-wheel"
        or runtime.get("wheel_sha256") != wheel_sha256
        or runtime.get("origins") != origins
    ):
        raise ValueError(
            "Application evidence did not use the candidate installed wheel"
        )


def validate_applications_report(value: object, *, wheel_sha256: str) -> None:
    """Bind all nine application workflows and their native child evidence."""
    report = _record(value)
    checks = _record(report.get("checks"))
    acceptance = _record(report.get("acceptance"))
    if (
        re.fullmatch("[0-9a-f]{64}", wheel_sha256) is None
        or type(report.get("schema")) is not int
        or report["schema"] != 1
        or "error" in report
        or any(
            report.get(key) is not True
            for key in (
                "ok",
                "normal_shutdown",
                "fixture_cleanup",
                "associations_restored",
                "workarea_restored",
                "explorer_recovered",
            )
        )
        or _record(report.get("options")).get("restart_explorer") is not True
        or type(acceptance.get("schema")) is not int
        or acceptance
        != {
            "schema": 1,
            "implementation": "installed-wheel",
            "wheel_sha256": wheel_sha256,
            "profile": "applications-explorer",
        }
        or set(checks) != _APPLICATION_CHECKS
        or any(
            _record(check).get("passed") is not True or "error" in _record(check)
            for check in checks.values()
        )
    ):
        raise ValueError("Windows applications lack complete matching acceptance")
    _application_runtime(
        report.get("runtime"),
        wheel_sha256,
        {
            "docking": "docking/__init__.py",
            "launcher": "docking/platform/applications/launcher.py",
            "catalog": "docking/platform/backends/windows/catalog.py",
            "documents": "docking/platform/backends/windows/documents.py",
        },
    )
    for route in ("installed", "generated", "selected"):
        check = _record(checks["documents-" + route])
        document = _record(check.get("result"))
        identifier = document.get("id")
        rows = _rows(document.get("document_drops"))
        if (
            check.get("evidence") != route + "/documents.json"
            or document.get("phase") != "documents"
            or "error" in document
            or any(
                document.get(key) is not True
                for key in ("ok", "normal_shutdown", "physical_document_input")
            )
            or not isinstance(identifier, str)
            or not identifier
            or [row.get("count") for row in rows] != [1, 2]
            or any(
                type(row.get("count")) is not int
                or row.get("protocol") != "ole2"
                or row.get("desktop_id") != identifier
                for row in rows
            )
        ):
            raise ValueError(
                "Windows applications lack physical selected-file acceptance"
            )
        _application_runtime(
            document.get("runtime"), wheel_sha256, {"app": "docking/app.py"}
        )
    exported = _record(checks["selected-launcher-export"])
    if (
        any(
            exported.get(key) is not True
            for key in (
                "actual_explorer_copy",
                "independent_explorer_launch",
                "source_cache_removed",
                "binary_preserved",
            )
        )
        or _record(checks["association-absence"]).get("lookup_only") is not True
    ):
        raise ValueError(
            "Windows application export/association evidence is incomplete"
        )
    inventory_check = _record(checks["inventory-changes"])
    inventory = _record(inventory_check.get("result"))
    phases = _rows(inventory.get("phases"))
    if (
        inventory_check.get("evidence") != "inventory/result.json"
        or _record(checks["explorer-recovery"]).get("evidence")
        != "inventory/result.json"
        or type(inventory.get("schema")) is not int
        or inventory["schema"] != 1
        or "error" in inventory
        or "shell_cleanup_error" in inventory
        or any(
            inventory.get(key) is not True
            for key in (
                "ok",
                "observer_stopped",
                "owned_shortcuts_removed",
                "workarea_restored",
                "explorer_recovered",
            )
        )
        or inventory.get("forced_refresh") is not False
        or _record(inventory.get("options")).get("restart_explorer") is not True
        or not _positive(inventory.get("native_notifications"))
        or [phase.get("action") for phase in phases] != ["rename", "delete", "recreate"]
        or any(
            phase.get("passed") is not True
            or not _positive(phase.get("native_notifications"))
            for phase in phases
        )
    ):
        raise ValueError("Windows application inventory lacks native change acceptance")
    _application_runtime(
        inventory.get("runtime"),
        wheel_sha256,
        {"registry": "docking/platform/applications/registry.py"},
    )
    recovery = _record(inventory.get("explorer_recovery"))
    before, after = _record(recovery.get("before")), _record(recovery.get("after"))
    if (
        any(
            recovery.get(key) is not True
            for key in (
                "passed",
                "held_actual_read",
                "cached_source_activation",
                "after_restart_change",
                "old_process_exited",
                "replacement_owned",
                "shell_recovered",
            )
        )
        or any(
            not _positive(shell.get("pid"))
            or not _positive(shell.get("window"))
            or type(shell.get("style")) is not int
            for shell in (before, after)
        )
        or before["pid"] == after["pid"]
        or before["style"] != after["style"]
        or report.get("explorer_before") != before
        or report.get("explorer_after") != after
    ):
        raise ValueError("Windows application Explorer replacement lacks exact lineage")
