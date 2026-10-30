"""Keep native test-runner cleanup safe for long paths under MSYS2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking import launcher
from tools.windows import ci
from tools.windows.bundle import filesystem_path


@pytest.mark.skipif(
    sys.platform != "win32" or os.name != "nt", reason="Native Windows long-path APIs"
)
@pytest.mark.parametrize("status", [0, 7])
def test_runner_removes_long_path_stage_and_preserves_child_status(
    tmp_path, monkeypatch, status
):
    temporary = tmp_path / "runner-temp"
    temporary.mkdir()
    stages = []
    monkeypatch.setattr(ci.tempfile, "gettempdir", lambda: str(temporary))
    monkeypatch.setattr(launcher, "prepare_display", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["windows-ci", "--stage", "tests", "--output", str(tmp_path / "reports")],
    )

    def run(command, *, cwd):
        assert cwd == Path(ci.__file__).resolve().parents[2]
        base = next(
            value.removeprefix("--basetemp=")
            for value in command
            if value.startswith("--basetemp=")
        )
        # pytest receives an ordinary path; only filesystem operations retain
        # the extended prefix needed by MSYS2's long-path cleanup.
        assert not base.startswith("\\\\?\\")
        assert "/" not in base
        root = filesystem_path(Path(base))
        stage = root.parent
        assert stage.name.startswith("docking-windows-ci-")
        owned = root / "runtime/lib" / ("long-directory/" * 20 + "owned.txt")
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned subprocess fixture")
        assert len(str(owned)) > 260
        assert owned.read_bytes() == b"owned subprocess fixture"
        stages.append(stage)
        return subprocess.CompletedProcess(command, status)

    monkeypatch.setattr(ci.subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == status
    assert len(stages) == 1
    assert not stages[0].exists()
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("statuses", [(0, 0), (1, 0), (1, 3), (0, 2)])
def test_input_stage_runs_complete_domain_and_retains_both_results(
    tmp_path, monkeypatch, statuses
):
    root = Path(ci.__file__).resolve().parents[2]
    output = tmp_path / "reports with spaces"
    installed = tmp_path / "installed wheel"
    calls = []

    def run(command, *, cwd):
        assert cwd == root
        index = len(calls)
        calls.append(command)
        destination = Path(command[command.index("--output") + 1].replace("\\", "/"))
        destination.mkdir(parents=True)
        (destination / "result.json").write_text(
            json.dumps({"ok": statuses[index] == 0, "error": None}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, statuses[index])

    monkeypatch.setattr(ci.subprocess, "run", run)
    status = ci.input_domain(root, output, installed)
    assert status == (0 if statuses == (0, 0) else 1)
    assert len(calls) == 2
    assert calls[0] == [
        sys.executable,
        "-m",
        "tools.windows.visual.keyboardlayout_smoke",
        "--search-input",
        "--output",
        ci.command_path(output / "search-layouts"),
    ]
    assert calls[1] == [
        sys.executable,
        "-m",
        "tools.windows.visual.input_profile_smoke",
        "--installed-root",
        ci.command_path(installed.resolve()),
        "--output",
        ci.command_path(output / "input-profiles"),
    ]
    report = json.loads((output / "input-domain.json").read_text())
    assert report["ok"] is (statuses == (0, 0))
    assert not report["cancelled"]
    assert [row["returncode"] for row in report["checks"]] == list(statuses)
    assert [row["implementation"] for row in report["checks"]] == [
        "source",
        "installed-wheel",
    ]
    assert [row["module"] for row in report["checks"]] == [
        command[2] for command in calls
    ]
    assert [row["passed"] for row in report["checks"]] == [
        code == 0 for code in statuses
    ]


@pytest.mark.parametrize(
    "failure", ["launch", "missing-result", "invalid-json", "false-result"]
)
def test_input_stage_collects_reference_after_incomplete_first_check(
    tmp_path, monkeypatch, failure
):
    calls = []

    def run(command, *, cwd):
        calls.append(command)
        if len(calls) == 1 and failure == "launch":
            raise OSError(5, "Owned launch failure")
        destination = Path(command[command.index("--output") + 1].replace("\\", "/"))
        destination.mkdir(parents=True)
        if len(calls) != 1 or failure != "missing-result":
            (destination / "result.json").write_text(
                "invalid"
                if len(calls) == 1 and failure == "invalid-json"
                else json.dumps({"ok": len(calls) != 1}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(ci.subprocess, "run", run)
    assert ci.input_domain(tmp_path, tmp_path / "reports", tmp_path / "installed") == 1
    report = json.loads((tmp_path / "reports/input-domain.json").read_text())
    assert len(calls) == 2 and not report["ok"]
    assert report["checks"][0]["passed"] is False
    assert report["checks"][1]["passed"] is True


@pytest.mark.parametrize("cancellation", ["interrupt", 130, -1073741510])
def test_cancelled_input_stage_journals_and_does_not_start_another_desktop_tool(
    tmp_path, monkeypatch, cancellation
):
    calls = []

    def run(command, *, cwd):
        calls.append(command)
        if cancellation == "interrupt":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, cancellation)

    monkeypatch.setattr(ci.subprocess, "run", run)
    assert (
        ci.input_domain(tmp_path, tmp_path / "reports", tmp_path / "installed") == 130
    )
    report = json.loads((tmp_path / "reports/input-domain.json").read_text())
    assert len(calls) == 1 and report["cancelled"] and not report["ok"]


@pytest.mark.parametrize("stage", ["input", "tray"])
def test_domain_dispatch_does_not_preload_parent_launcher_or_gtk(
    tmp_path, monkeypatch, stage
):
    output = tmp_path / "reports"
    installed = tmp_path / "installed"
    monkeypatch.setattr(ci, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(ci.sys, "platform", "win32")
    wheel = tmp_path / "accepted.whl"
    command = [
        "windows-ci",
        "--stage",
        stage,
        "--installed-root",
        str(installed),
        "--output",
        str(output),
    ]
    if stage == "tray":
        command += ["--wheel", str(wheel)]
    monkeypatch.setattr(ci.sys, "argv", command)
    monkeypatch.setattr(
        launcher, "prepare_display", lambda: pytest.fail("Controller preloaded display")
    )
    calls = []

    def batch(*arguments):
        calls.append(arguments)
        return 1

    monkeypatch.setattr(ci, stage + "_domain", batch)
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == 1
    expected = (Path(ci.__file__).resolve().parents[2], output, installed)
    assert calls == [(*expected, wheel) if stage == "tray" else expected]


def test_input_stage_requires_explicit_installed_wheel_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(ci.sys, "platform", "win32")
    monkeypatch.setattr(
        ci.sys, "argv", ["windows-ci", "--stage", "input", "--output", str(tmp_path)]
    )
    monkeypatch.setattr(
        ci, "input_domain", lambda *args: pytest.fail("Missing installed selection")
    )
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == 2


@pytest.mark.parametrize("statuses", [(0, 0, 0, 0), (1, 0, 0, 0), (1, 3, 0, 2)])
def test_tray_stage_collects_all_api_and_gtk_results_before_failing(
    tmp_path, monkeypatch, statuses
):
    from tests.test_windows_release import tray_report

    root = Path(ci.__file__).resolve().parents[2]
    output, installed, wheel = (
        tmp_path / "reports",
        tmp_path / "installed wheel",
        tmp_path / "accepted.whl",
    )
    wheel.write_bytes(b"accepted wheel fixture")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    calls = []

    def run(command, *, cwd):
        assert cwd == root
        index = len(calls)
        calls.append(command)
        destination = Path(command[command.index("--output") + 1].replace("\\", "/"))
        destination.mkdir(parents=True)
        data = tray_report(destination.name)
        if "acceptance" in data:
            data["acceptance"]["wheel_sha256"] = digest
        (destination / "result.json").write_text(json.dumps(data), encoding="utf-8")
        return subprocess.CompletedProcess(command, statuses[index])

    monkeypatch.setattr(ci.subprocess, "run", run)
    assert ci.tray_domain(root, output, installed, wheel) == (
        0 if not any(statuses) else 1
    )
    assert len(calls) == 4
    assert calls[0][2:5] == [
        "tools.windows.visual.tray_provider_smoke",
        "--mode",
        "explorer",
    ]
    assert (
        "--all" in calls[0]
        and "--installed-root" not in calls[0]
        and "--wheel" not in calls[0]
    )
    assert calls[1][2:5] == [
        "tools.windows.visual.tray_provider_smoke",
        "--mode",
        "backend",
    ]
    assert "--all" in calls[1]
    assert all(command[2] == "tools.windows.visual.tray_smoke" for command in calls[2:])
    assert "--verify-geometry" in calls[2] and "--guid-only-updates" not in calls[2]
    assert (
        "--legacy-version" in calls[3]
        and calls[3][calls[3].index("--legacy-version") + 1] == "3"
    )
    assert "--guid-only-updates" in calls[3]
    for command in calls[1:]:
        assert command[command.index("--installed-root") + 1] == ci.command_path(
            installed.resolve()
        )
        assert command[command.index("--wheel") + 1] == ci.command_path(wheel.resolve())
    assert all("--verify-interactions" in command for command in calls[2:])
    report = json.loads((output / "tray-domain.json").read_text())
    assert [row["name"] for row in report["checks"]] == [
        "tray/explorer",
        "tray/backend",
        "tray/gtk-v0-v4",
        "tray/gtk-v3-v4-guid",
    ]
    assert [row["returncode"] for row in report["checks"]] == list(statuses)
    assert [row["passed"] for row in report["checks"]] == [
        status == 0 for status in statuses
    ]
    assert report["ok"] is (not any(statuses))


@pytest.mark.parametrize(
    "defect", ["api-catalog", "cleanup", "wheel", "gtk-profile", "gtk-checkpoints"]
)
def test_tray_stage_reuses_release_gate_and_continues_after_unaccepted_report(
    tmp_path, monkeypatch, defect
):
    from tests.test_windows_release import tray_report

    wheel = tmp_path / "accepted.whl"
    wheel.write_bytes(b"accepted wheel fixture")
    calls = []

    def run(command, *, cwd):
        calls.append(command)
        destination = Path(command[command.index("--output") + 1].replace("\\", "/"))
        destination.mkdir(parents=True)
        data = tray_report(destination.name)
        if len(calls) == 1 and defect == "api-catalog":
            data["cases"].pop(next(iter(data["cases"])))
        elif len(calls) == 1 and defect == "cleanup":
            data["cursor_restored"] = False
        elif len(calls) == 2 and defect == "wheel":
            data["acceptance"]["wheel_sha256"] = "0" * 64
        elif len(calls) == 3 and defect == "gtk-profile":
            data["options"]["legacy_version"] = 3
        elif len(calls) == 3 and defect == "gtk-checkpoints":
            data["interactions"]["hover"].pop()
        (destination / "result.json").write_text(json.dumps(data), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(ci.subprocess, "run", run)
    assert (
        ci.tray_domain(tmp_path, tmp_path / "reports", tmp_path / "installed", wheel)
        == 1
    )
    report = json.loads((tmp_path / "reports/tray-domain.json").read_text())
    assert len(calls) == 4
    assert sum(row["passed"] for row in report["checks"]) == 3
    assert sum("validation_error" in row for row in report["checks"]) == 1


def test_tray_stage_cancellation_stops_new_desktop_children(tmp_path, monkeypatch):
    wheel = tmp_path / "accepted.whl"
    wheel.write_bytes(b"accepted wheel fixture")
    calls = []

    def run(command, *, cwd):
        calls.append(command)
        raise KeyboardInterrupt

    monkeypatch.setattr(ci.subprocess, "run", run)
    assert (
        ci.tray_domain(tmp_path, tmp_path / "reports", tmp_path / "installed", wheel)
        == 130
    )
    report = json.loads((tmp_path / "reports/tray-domain.json").read_text())
    assert len(calls) == 1 and report["cancelled"] and not report["ok"]


@pytest.mark.parametrize("missing", ["--installed-root", "--wheel"])
def test_tray_stage_requires_both_explicit_runtime_inputs(
    tmp_path, monkeypatch, missing
):
    monkeypatch.setattr(ci, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(ci.sys, "platform", "win32")
    command = ["windows-ci", "--stage", "tray", "--output", str(tmp_path)]
    command += (
        ["--wheel", str(tmp_path / "fixture.whl")]
        if missing == "--installed-root"
        else ["--installed-root", str(tmp_path / "installed")]
    )
    monkeypatch.setattr(ci.sys, "argv", command)
    monkeypatch.setattr(
        ci, "tray_domain", lambda *args: pytest.fail("Unspecified runtime")
    )
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == 2
