"""Application-domain evidence must represent actual selected-file workflows."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.windows.visual.applications_domain_smoke import (
    phase_command,
    record_check,
    validate_document_phase,
)
from tools.windows.visual.runtime_pin_smoke import document_application


def test_document_fixture_waits_for_completed_catalog_before_requiring_exact_pin():
    selected = SimpleNamespace(document_target=r"C:\cache\original-options.lnk")
    observations = iter(((True, None), (True, selected), (False, selected)))
    report = {}

    class Native:
        def wait_for(self, predicate, _message, seconds):
            assert seconds == 60
            assert not predicate()
            assert not predicate()
            assert predicate()

    assert (
        document_application(Native(), lambda: next(observations), "installed", report)
        is selected
    )
    state = report["catalog_readiness"]
    assert {
        key: state[key] for key in ("pending", "resolved", "has_document_target")
    } == {
        "pending": False,
        "resolved": True,
        "has_document_target": True,
    }
    assert isinstance(state["elapsed_ms"], int) and state["elapsed_ms"] >= 0


@pytest.mark.parametrize("missing", ["pin", "document-source", "pending"])
def test_document_fixture_reports_catalog_state_and_keeps_missing_identity_fatal(
    missing,
):
    selected = None if missing == "pin" else SimpleNamespace(document_target="")
    report = {}

    class Native:
        def wait_for(self, predicate, message, _seconds):
            if not predicate():
                raise AssertionError(message)

    with pytest.raises(AssertionError):
        document_application(
            Native(), lambda: (missing == "pending", selected), "installed", report
        )
    state = report["catalog_readiness"]
    assert state["pending"] is (missing == "pending")
    assert state["resolved"] is (missing != "pin")
    assert not state["has_document_target"]
    assert isinstance(state["elapsed_ms"], int)


def test_cleaned_failure_collects_the_next_independent_phase():
    checks = {name: {"passed": False} for name in ("first", "second")}
    called = []

    def first():
        raise ValueError("Observed native mismatch")

    assert record_check(checks, "first", first, cleanup_verified=lambda: True)
    assert record_check(
        checks, "second", lambda: called.append("second"), cleanup_verified=lambda: True
    )
    assert (
        not checks["first"]["passed"]
        and checks["first"]["error"]["class"] == "ValueError"
    )
    assert checks["second"]["passed"] and called == ["second"]


def test_unverified_cleanup_stops_dependent_ui_without_hiding_original_error():
    checks = {"first": {"passed": False}}

    def operation():
        raise ValueError("Observed mismatch")

    def cleanup():
        raise OSError(5, "Cleanup failed")

    assert not record_check(checks, "first", operation, cleanup_verified=cleanup)
    assert checks["first"]["error"]["class"] == "ValueError"
    assert checks["first"]["cleanup_error"]["class"] == "OSError"
    assert checks["first"]["cleanup_verified"] is False


def test_cancellation_is_preserved_instead_of_collecting_more_phases():
    checks = {"first": {"passed": False}}

    def cancelled():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        record_check(checks, "first", cancelled, cleanup_verified=lambda: True)


def _phase():
    return {
        "ok": True,
        "normal_shutdown": True,
        "physical_document_input": True,
        "id": "Owned.App",
        "document_drops": [
            {"count": count, "protocol": "ole2", "desktop_id": "Owned.App"}
            for count in (1, 2)
        ],
    }


@pytest.mark.parametrize("route", ["installed", "generated", "selected"])
def test_document_evidence_requires_both_physical_file_counts_and_exact_group(route):
    validate_document_phase(_phase(), route)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate-count",
        "wrong-application",
        "synthetic-protocol",
        "no-input",
        "unclean",
    ],
)
def test_incomplete_document_evidence_cannot_pass(mutation):
    evidence = deepcopy(_phase())
    if mutation == "missing":
        evidence["document_drops"].pop()
    elif mutation == "duplicate-count":
        evidence["document_drops"][1]["count"] = 1
    elif mutation == "wrong-application":
        evidence["document_drops"][1]["desktop_id"] = "Other.App"
    elif mutation == "synthetic-protocol":
        evidence["document_drops"][0]["protocol"] = "local"
    elif mutation == "no-input":
        evidence["physical_document_input"] = False
    else:
        evidence["normal_shutdown"] = False
    with pytest.raises(ValueError):
        validate_document_phase(evidence, "selected")


def test_phase_preserves_installed_runtime_and_candidate_wheel(tmp_path):
    command = phase_command(
        tmp_path / "selected",
        installed_root=tmp_path / "installed",
        wheel=tmp_path / "candidate.whl",
    )
    assert command[:3] == [
        sys.executable,
        "-m",
        "tools.windows.visual.runtime_pin_smoke",
    ]
    assert command[-4:] == [
        "--installed-root",
        str(tmp_path / "installed"),
        "--wheel",
        str(tmp_path / "candidate.whl"),
    ]


def test_tool_import_does_not_select_or_import_a_docking_runtime():
    tool = (
        Path(__file__).resolve().parents[1]
        / "tools/windows/visual/applications_domain_smoke.py"
    )
    code = "import runpy,sys,json; runpy.run_path(sys.argv[1]); print(json.dumps([name for name in sys.modules if name == 'docking' or name.startswith('docking.')]))"
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(tool)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_failure_diagnostics_keep_source_location_without_exception_paths_or_messages():
    from tools.windows.visual.applications_domain_smoke import failure_details

    try:
        raise ValueError("private document C:/private/hidden.txt")
    except ValueError as error:
        details = failure_details(error)
    assert details["class"] == "ValueError" and details["errno"] is None
    assert (
        details["frames"][-1]["function"]
        == "test_failure_diagnostics_keep_source_location_without_exception_paths_or_messages"
    )
    assert details["frames"][-1]["file"] == Path(__file__).name
    assert details["frames"][-1]["line"] > 0
    assert "private" not in json.dumps(details)


@pytest.mark.parametrize("failure", ["binary", "independent-launch"])
def test_export_diagnostics_distinguish_copy_failure_from_later_independent_launch(
    tmp_path, monkeypatch, failure
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock

    from tools.windows.visual import applications_domain_smoke as smoke
    from tools.windows.visual import explorer

    selected = tmp_path / "Selected.lnk"
    selected.write_bytes(b"literal original shortcut options")
    copied = tmp_path / "Copied launcher résumé 日本語" / "Selected.lnk"
    observed, lifecycle = {}, {"closed": True}

    def run(_command, **_options):
        copied.write_bytes(
            b"different options" if failure == "binary" else selected.read_bytes()
        )
        case = tmp_path / "selected-export"
        case.mkdir()
        (case / "result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "normal_shutdown": True,
                    "export": {
                        "path": str(copied),
                        "source": str(tmp_path / "removed-cache.lnk"),
                    },
                }
            )
        )

    @contextmanager
    def folder(_native, directory):
        assert directory == copied.parent
        yield SimpleNamespace(
            file_rectangle=lambda _path: {"x": 1, "y": 2, "width": 100}
        )

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(explorer, "ExplorerFolder", folder)
    monkeypatch.setattr(
        smoke,
        "finish_program",
        Mock(
            side_effect=AssertionError("Owned application did not report its arguments")
        ),
    )
    native = SimpleNamespace(double_click=Mock())
    fixture = SimpleNamespace(
        root=tmp_path,
        labels={"Second": "Selected"},
        executables={"Second": tmp_path / "Owned.exe"},
    )
    with pytest.raises(AssertionError):
        smoke.export_check(
            native,
            fixture,
            tmp_path,
            selected,
            "Owned.App",
            [],
            expected_cwd=tmp_path,
            lifecycle=lifecycle,
            installed_root=None,
            wheel=None,
            observed=observed,
        )
    assert observed["child_shutdown"] and observed["source_cache_removed"]
    assert observed["binary_preserved"] is (failure != "binary")
    assert observed["stage"] == (
        "copied-launcher" if failure == "binary" else "independent-launch"
    )
    assert lifecycle["closed"] is (failure == "binary")
    assert native.double_click.call_count == (0 if failure == "binary" else 1)
