"""Application-domain evidence must represent actual selected-file workflows."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual.applications_domain_smoke import (
    cleanup_snapshot,
    phase_command,
    record_check,
    settle_cleanup,
    validate_document_phase,
)
from tools.windows.visual.runtime_pin_smoke import (
    cancel_document_drag,
    document_application,
    document_icon_rectangle,
    document_release_target,
    guard_document_release,
)


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


@pytest.mark.parametrize(
    "failure",
    [
        "binary",
        "independent-launch",
        "selection",
        "enter-dispatch",
        "entry",
        "unsafe-entry",
    ],
)
def test_export_diagnostics_distinguish_copy_failure_from_later_independent_launch(
    tmp_path, monkeypatch, failure
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from PIL import ImageGrab

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

    def activate(_copied, evidence):
        if failure == "selection":
            raise AssertionError("Owned Explorer item is covered")
        evidence["physical_enter_started"] = True
        if failure == "enter-dispatch":
            raise AssertionError("Owned Enter input was incomplete")

    activated = Mock(side_effect=activate)

    class Folder:
        def __init__(self, _native, directory):
            assert directory == copied.parent
            self.foreground_observations = []
            self.failure_capture = None
            self.cleanup_safe = True
            self.activate = activated

        def __enter__(self):
            assert observed["explorer_foreground"] is self.foreground_observations
            assert self.failure_capture.name == "independent-explorer-entry-failure.png"
            if failure in ("entry", "unsafe-entry"):
                self.foreground_observations.append({"result": "failed"})
                self.cleanup_safe = failure != "unsafe-entry"
                raise AssertionError("Owned caption focus failed")
            return self

        def __exit__(self, _kind, _error, _traceback):
            return False

    folder = Folder

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(explorer, "ExplorerFolder", folder)
    monkeypatch.setattr(
        ImageGrab,
        "grab",
        lambda **_options: SimpleNamespace(
            save=lambda path: path.write_bytes(b"owned fixture capture")
        ),
    )
    monkeypatch.setattr(
        smoke,
        "finish_program",
        Mock(
            side_effect=AssertionError("Owned application did not report its arguments")
        ),
    )
    native = SimpleNamespace()
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
        "copied-launcher"
        if failure == "binary"
        else "independent-explorer"
        if failure in ("entry", "unsafe-entry")
        else "independent-launch"
    )
    assert lifecycle["closed"] is (
        failure not in ("independent-launch", "enter-dispatch", "unsafe-entry")
    )
    assert activated.call_count == (
        0 if failure in ("binary", "entry", "unsafe-entry") else 1
    )
    if failure in ("entry", "unsafe-entry"):
        assert observed["explorer_foreground"] == [{"result": "failed"}]
        assert observed["explorer_cleanup_safe"] is (failure != "unsafe-entry")


def _document_target(**changed):
    return {
        "point": [100, 200],
        "cursor": [100, 200],
        "target": "Owned.App",
        "reported_target": "Owned.App",
        "rendered_target": "Owned.App",
        "native_window": 12,
        "dock_window": 12,
        "live_item": True,
        "slide_offset": 0.0,
        **changed,
    }


def test_document_release_tracks_changed_geometry_before_one_physical_release():
    now, calls, moved = [0.0], [], []
    observations = iter(
        (
            _document_target(cursor=[90, 200]),
            _document_target(point=[110, 200], cursor=[100, 200]),
            *[_document_target(point=[110, 200], cursor=[110, 200])] * 3,
        )
    )

    def observe():
        calls.append("observe")
        return next(observations)

    def move(x, y):
        moved.append((x, y))
        return 1

    def wait(predicate, _message, seconds):
        assert seconds == 5
        for _ in range(4):
            now[0] += 0.5
            value = predicate()
            if value:
                return value
        pytest.fail("Target did not settle")

    record = {}
    document_release_target(
        observe=observe,
        move=move,
        wait_for=wait,
        identifier="Owned.App",
        record=record,
        clock=lambda: now[0],
    )
    assert moved == [(100, 200), (110, 200)]
    assert len(calls) == 5
    assert record["before_release"]["point"] == [110, 200]


@pytest.mark.parametrize(
    "changed",
    [
        {"live_item": False},
        {"target": "Other.App"},
        {"reported_target": "Other.App"},
        {"rendered_target": None},
        {"native_window": 99},
    ],
)
def test_document_release_rejects_replaced_missed_or_occluded_target(changed):
    def wait(predicate, message, _seconds):
        if not predicate():
            raise AssertionError(message)

    with pytest.raises(AssertionError):
        document_release_target(
            observe=lambda: _document_target(**changed),
            move=lambda _x, _y: pytest.fail("Settled point should not move"),
            wait_for=wait,
            identifier="Owned.App",
            record={},
        )


@pytest.mark.parametrize("changed", ({"native_window": 99}, {"slide_offset": -0.62}))
def test_document_release_rechecks_live_target_after_settling(changed):
    current = [_document_target()]

    def wait(predicate, _message, _seconds):
        assert not predicate()
        settled = dict(current[0])
        current[0] = _document_target(**changed)
        return settled

    with pytest.raises(AssertionError, match="changed before release"):
        document_release_target(
            observe=lambda: current[0],
            move=lambda _x, _y: pytest.fail("Already positioned"),
            wait_for=wait,
            identifier="Owned.App",
            record={},
        )


def test_document_release_waits_for_animation_to_finish_before_stability():
    now = [0.0]
    observations = iter(
        [_document_target(slide_offset=-0.62)] * 3 + [_document_target()] * 3
    )

    def wait(predicate, _message, seconds):
        assert seconds == 5
        # The unchanged nonzero offset persists beyond the 0.4s stability
        # period; it must never become a releasable target in that state.
        for index, value in enumerate((0.0, 0.5, 1.0, 1.5, 2.0)):
            now[0] = value
            settled = predicate()
            if index < 4:
                assert not settled
            else:
                assert settled["slide_offset"] == 0.0
                return settled
        pytest.fail("Animation did not settle")

    record = {}
    document_release_target(
        observe=lambda: next(observations),
        move=lambda _x, _y: pytest.fail("The physical target never moved"),
        wait_for=wait,
        identifier="Owned.App",
        record=record,
        clock=lambda: now[0],
    )
    assert record["before_release"] == _document_target()
    assert [row["slide_offset"] for row in record["observations"]] == [-0.62, 0.0]


def test_document_release_permanent_animation_keeps_deadline_and_cancellation():
    now, events, record = [0.0], [], {}

    def wait(predicate, message, seconds):
        assert seconds == 5
        for value in (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0):
            now[0] = value
            assert not predicate()
        raise TimeoutError(message)

    with pytest.raises(TimeoutError, match="did not settle"):
        guard_document_release(
            lambda: document_release_target(
                observe=lambda: _document_target(slide_offset=-0.62),
                move=lambda _x, _y: pytest.fail("The physical target never moved"),
                wait_for=wait,
                identifier="Owned.App",
                record=record,
                clock=lambda: now[0],
            ),
            cancel=lambda: events.append("cancel-owned-gesture"),
            reject=lambda: events.append("reject-drop"),
            record=record,
        )
    assert events == ["reject-drop", "cancel-owned-gesture"]
    assert "before_release" not in record


def test_document_release_failure_cancels_then_retains_both_errors():
    original = AssertionError("Target changed")
    events, record = [], {}

    def check():
        raise original

    def cancel():
        events.append("cancel")
        raise OSError(5, "Owned cancellation fixture error")

    with pytest.raises(AssertionError) as failure:
        guard_document_release(
            check, cancel, lambda: events.append("reject-late-drop"), record
        )
    assert failure.value is original
    assert events == ["reject-late-drop", "cancel"]
    assert record["cancellation_error"] == {"class": "OSError", "errno": 5}


def test_successful_document_release_does_not_cancel_or_reject_input():
    guard_document_release(
        lambda: None,
        lambda: pytest.fail("Successful gesture should not cancel"),
        lambda: pytest.fail("Successful gesture should use the production handler"),
        {},
    )


@pytest.mark.parametrize("mismatch", ["foreground", "source", None])
def test_document_cancellation_requires_current_owned_foreground_source(mismatch):
    source = {"hwnd": 100, "pid": 12, "class": "CabinetWClass", "title": "Owned"}
    current = {**source, "pid": 13} if mismatch == "source" else source
    native = SimpleNamespace(
        windows=lambda _pid: [current],
        user=SimpleNamespace(
            GetForegroundWindow=lambda: 999 if mismatch == "foreground" else 100
        ),
        key=Mock(),
    )

    def wait(predicate, _message, seconds):
        assert seconds == 3 and predicate()
        assert native.key.call_args.args == (0x1B,)

    native.wait_for = wait
    record = {}
    if mismatch:
        with pytest.raises(AssertionError):
            cancel_document_drag(native, source, lambda: True, record)
        native.key.assert_not_called()
        assert "escape_sent" not in record
    else:
        cancel_document_drag(native, source, lambda: True, record)
        assert record == {
            "foreground": 100,
            "escape_sent": True,
            "target_cleared": True,
        }


@pytest.mark.parametrize("horizontal", [False, True])
def test_document_icon_uses_current_item_and_rendered_gap_shift(horizontal):
    before = SimpleNamespace(desktop_id="Before")
    wanted = SimpleNamespace(desktop_id="Owned.App")
    frame = SimpleNamespace(
        item_geometries=[
            SimpleNamespace(
                item=item, draw_rect=SimpleNamespace(x=10, y=20, w=64, h=64)
            )
            for item in (before, wanted)
        ]
    )
    item, rect = document_icon_rectangle(frame, "Owned.App", 1, 72, horizontal)
    assert item is wanted
    assert rect == ((82, 20, 64, 64) if horizontal else (10, 92, 64, 64))
    assert document_icon_rectangle(frame, "Owned.App", 2, 72, horizontal)[1] == (
        10,
        20,
        64,
        64,
    )


@pytest.mark.parametrize(
    "reason",
    ["process", "window", "workarea", "shell-pid", "shell-window", "shell-style", None],
)
def test_cleanup_evidence_keeps_existing_rejection_policy_and_exposes_reason(reason):
    baseline = [0, 0, 1280, 752]
    shell = {"pid": 77, "window": 1000, "style": 136}
    rows = [
        {"hwnd": 1, "pid": 99, "class": "Existing", "rect": [0, 0, 100, 100]},
        {"hwnd": 2, "pid": 77, "class": "WorkerW", "rect": [0, 0, 1280, 752]},
    ]
    if reason == "window":
        rows.append(
            {
                "hwnd": 3,
                "pid": 77,
                "class": "Xaml_WindowedPopupClass",
                "rect": [20, 30, 220, 130],
            }
        )

    def cursor(target):
        target._obj.x, target._obj.y = 576, 713
        return 1

    native = SimpleNamespace(
        workarea=lambda: [0, 0, 1280, 700] if reason == "workarea" else baseline,
        explorer=lambda: (
            {**shell, reason.removeprefix("shell-"): 78}
            if reason is not None and reason.startswith("shell-")
            else shell
        ),
        windows=lambda: rows,
        user=SimpleNamespace(GetCursorPos=cursor, GetForegroundWindow=lambda: 1000),
    )
    processes = [
        SimpleNamespace(
            pid=9,
            info={
                "exe": r"C:\owned\fixture.exe"
                if reason == "process"
                else r"C:\unrelated\normal.exe"
            },
        )
    ]
    snapshot = cleanup_snapshot(
        native,
        processes,
        {PureWindowsPath(r"C:\owned\fixture.exe")},
        {1},
        baseline,
        shell,
    )
    assert snapshot["safe"] is (reason is None)
    assert snapshot["owned_pids"] == ([9] if reason == "process" else [])
    assert snapshot["new_windows"] == (
        [{key: rows[-1][key] for key in ("hwnd", "pid", "class")}]
        if reason == "window"
        else []
    )
    assert snapshot["new_window_rects"] == (
        [{key: rows[-1][key] for key in ("hwnd", "pid", "rect")}]
        if reason == "window"
        else []
    )
    assert snapshot["cursor"] == [576, 713] and snapshot["foreground"] == 1000
    assert snapshot["workarea_matches"] is (reason != "workarea")
    assert snapshot["shell_matches"] is (
        reason is None or not reason.startswith("shell-")
    )


def test_cleanup_window_diagnostics_are_bounded_without_dropping_rejections():
    def cursor(_target):
        return 1

    rows = [
        {"hwnd": index, "pid": 77, "class": "Unknown", "rect": [0, 0, 100, 100]}
        for index in range(17)
    ]
    shell = {"pid": 77, "window": 1000, "style": 136}
    native = SimpleNamespace(
        workarea=lambda: [0, 0, 1280, 752],
        explorer=lambda: shell,
        windows=lambda: rows,
        user=SimpleNamespace(GetCursorPos=cursor, GetForegroundWindow=lambda: 1000),
    )
    snapshot = cleanup_snapshot(native, [], set(), set(), native.workarea(), shell)
    assert snapshot["safe"] is False
    assert len(snapshot["new_windows"]) == 17
    assert len(snapshot["new_window_rects"]) == 16


@pytest.fixture
def cleanup_clock(monkeypatch):
    from tools.windows.visual import applications_domain_smoke

    clock = SimpleNamespace(now=0.0, pauses=[])

    def pause(seconds):
        assert 0 < seconds <= 0.1
        clock.pauses.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(
        applications_domain_smoke,
        "time",
        SimpleNamespace(monotonic=lambda: clock.now, sleep=pause),
    )
    return clock


def test_cleanup_waits_for_two_consecutive_strict_safe_snapshots(
    cleanup_clock, tmp_path
):
    safe = {"safe": True, "cursor": [576, 713], "new_windows": []}
    popup = {
        **safe,
        "safe": False,
        "new_windows": [
            {"hwnd": 2622620, "pid": 5360, "class": "Xaml_WindowedPopupClass"}
        ],
    }
    rows = iter((popup, safe, popup, safe, safe))
    observed = {}
    assert settle_cleanup(lambda: next(rows), observed, tmp_path / "failure.png")
    assert observed["initial"] is popup and observed["final"] is safe
    assert observed["samples"] == 5 and observed["transitions"] == 3
    assert observed["stable_safe_samples"] == 2
    assert observed["elapsed_ms"] == 400 and not observed["timed_out"]
    assert cleanup_clock.pauses == [0.1] * 4
    assert not (tmp_path / "failure.png").exists()


@pytest.mark.parametrize(
    "remaining",
    [
        {"new_windows": [{"hwnd": 8, "pid": 77, "class": "Xaml_WindowedPopupClass"}]},
        {"owned_pids": [9]},
        {"workarea_matches": False},
        {"shell": {"pid": 78, "window": 1000, "style": 136}, "shell_matches": False},
        {"shell": {"pid": 77, "window": 1001, "style": 136}, "shell_matches": False},
        {"shell": {"pid": 77, "window": 1000, "style": 0}, "shell_matches": False},
    ],
)
def test_cleanup_timeout_keeps_every_original_rejection_and_captures(
    cleanup_clock, tmp_path, monkeypatch, remaining
):
    from PIL import ImageGrab

    capture = Mock()
    monkeypatch.setattr(ImageGrab, "grab", capture)
    facts = {"safe": False, **remaining}
    observed = {}
    destination = tmp_path / "cleanup-association-default-failure.png"
    assert not settle_cleanup(lambda: facts, observed, destination)
    assert observed["initial"] is facts and observed["final"] is facts
    assert observed["safe"] is False and observed["timed_out"] is True
    assert observed["transitions"] == 0 and observed["stable_safe_samples"] == 0
    assert observed["elapsed_ms"] == 5000 and cleanup_clock.now == 5
    assert 50 <= observed["samples"] <= 52
    assert observed["failure_capture"] == destination.name
    capture.assert_called_once_with(all_screens=True)
    capture.return_value.save.assert_called_once_with(destination)


def test_cleanup_timeout_capture_error_preserves_operation_failure(
    cleanup_clock, tmp_path, monkeypatch
):
    from PIL import ImageGrab

    monkeypatch.setattr(
        ImageGrab, "grab", Mock(side_effect=OSError(5, "Capture failed"))
    )
    observed = {}
    checks = {"first": {"passed": False}}

    def operation():
        raise ValueError("Original failure")

    assert not record_check(
        checks,
        "first",
        operation,
        cleanup_verified=lambda: settle_cleanup(
            lambda: {"safe": False}, observed, tmp_path / "failure.png"
        ),
    )
    assert observed["timed_out"] and not observed["safe"]
    capture_error = observed["capture_error"]
    assert isinstance(capture_error, dict) and capture_error["class"] == "OSError"
    assert "failure_capture" not in observed
    phase_error = checks["first"]["error"]
    assert isinstance(phase_error, dict) and phase_error["class"] == "ValueError"
    assert checks["first"]["cleanup_verified"] is False
    assert "cleanup_error" not in checks["first"]


def test_single_safe_sample_at_deadline_does_not_claim_settled_cleanup(
    cleanup_clock, tmp_path, monkeypatch
):
    from PIL import ImageGrab

    monkeypatch.setattr(ImageGrab, "grab", Mock())
    observed = {}
    assert not settle_cleanup(
        lambda: {"safe": cleanup_clock.now >= 5},
        observed,
        tmp_path / "failure.png",
    )
    assert observed["final"]["safe"] is True
    assert observed["stable_safe_samples"] == 1
    assert observed["timed_out"] is True and observed["safe"] is False


def test_cleanup_observation_failure_and_cancellation_are_not_retried(
    cleanup_clock, tmp_path
):
    for error in (OSError(5, "Snapshot failed"), KeyboardInterrupt()):
        snapshot = Mock(side_effect=error)
        with pytest.raises(type(error)) as caught:
            settle_cleanup(snapshot, {}, tmp_path / "failure.png")
        assert caught.value is error
        snapshot.assert_called_once_with()
    assert cleanup_clock.pauses == []


@pytest.mark.parametrize("popup", ["none", "delayed", "transient", "permanent"])
def test_whole_domain_guard_observes_full_existing_budget_after_anchor_close(
    cleanup_clock, monkeypatch, tmp_path, popup
):
    from PIL import ImageGrab

    capture = Mock()
    monkeypatch.setattr(ImageGrab, "grab", capture)

    def snapshot():
        pending = (
            popup == "permanent"
            or (popup == "delayed" and cleanup_clock.now >= 0.4)
            or (popup == "transient" and 0.4 <= cleanup_clock.now < 1.2)
        )
        return {
            "safe": not pending,
            "new_windows": [{"hwnd": 9, "class": "Xaml_WindowedPopupClass"}]
            if pending
            else [],
        }

    observed = {}
    clean = settle_cleanup(snapshot, observed, tmp_path / "final.png", full_window=True)
    assert clean is (popup in ("none", "transient"))
    assert cleanup_clock.now == 5 and observed["elapsed_ms"] == 5000
    assert observed["samples"] >= 50
    if popup == "delayed":
        assert observed["initial"]["safe"] and not observed["final"]["safe"]
        assert observed["transitions"] == 1 and observed["timed_out"]
    elif popup == "transient":
        assert observed["transitions"] == 2 and observed["stable_safe_samples"] > 2
    assert capture.call_count == (1 if popup in ("delayed", "permanent") else 0)


@pytest.mark.parametrize("replaced", [False, True])
def test_document_snapshot_uses_live_gap_and_physical_scale(replaced):
    from docking.core.position import Position
    from tools.windows.visual.runtime_pin_smoke import document_target_snapshot

    item = SimpleNamespace(desktop_id="Owned.App")
    frame = SimpleNamespace(
        item_geometries=[
            SimpleNamespace(
                item=item, draw_rect=SimpleNamespace(x=100, y=20, w=64, h=64)
            )
        ]
    )

    def bounds(hwnd, target):
        assert hwnd == 123
        target._obj.left, target._obj.top = 10, 20
        target._obj.right, target._obj.bottom = 2000, 1000
        return 1

    def cursor(target):
        target._obj.x, target._obj.y = 450, 124
        return 1

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    def drop(frame_seen, *, x, y, accepted_kinds):
        assert frame_seen is frame and (x, y) == (220, 52)
        assert accepted_kinds == ("app",)
        return item

    dnd = SimpleNamespace(
        drop_insert_index=0, drop_target_id="Owned.App", _drop_target_item_at_point=drop
    )
    window = SimpleNamespace(
        geometry=SimpleNamespace(build_frame=lambda **_kwargs: frame),
        _current_or_build_geometry_frame=lambda **_kwargs: frame,
        config=SimpleNamespace(pos=Position.BOTTOM, icon_size=64),
        theme=SimpleNamespace(item_padding=8),
        get_scale_factor=lambda: 2,
        model=SimpleNamespace(
            find_by_desktop_id=lambda _identifier: object() if replaced else item
        ),
        renderer=SimpleNamespace(slide_offsets={}),
    )
    native = SimpleNamespace(
        user=SimpleNamespace(
            GetWindowRect=bounds,
            GetCursorPos=Function(cursor),
            GetForegroundWindow=lambda: 321,
        ),
        window_at=lambda x, y: 123 if (x, y) == (450, 124) else 999,
    )
    state = document_target_snapshot(
        native,
        SimpleNamespace(window=window, input_controller=SimpleNamespace(dnd=dnd)),
        "Owned.App",
        item,
        123,
    )
    assert state["point"] == state["cursor"] == [450, 124]
    assert state["rect"] == state["painted_rect"] == [172, 20, 64, 64]
    assert (
        state["target"]
        == state["rendered_target"]
        == state["reported_target"]
        == "Owned.App"
    )
    assert state["native_window"] == state["dock_window"] == 123
    assert state["foreground"] == 321
    assert state["live_item"] is (not replaced)


@pytest.mark.parametrize("initial_pending", [False, True])
@pytest.mark.parametrize("status", ["pending", "ready", "unavailable"])
def test_association_child_never_confuses_pending_or_unavailable_with_absence(
    status, initial_pending
):
    from docking.platform.applications.associations import (
        AssociationResolution,
        AssociationStatus,
    )
    from tools.windows.visual.applications_domain_smoke import association_selection

    result = AssociationResolution(AssociationStatus(status))
    registry = SimpleNamespace(
        default_for_content_type=Mock(return_value=result),
        initial_discovery_pending=initial_pending,
    )
    observed = {}
    if status == "unavailable":
        with pytest.raises(AssertionError, match="unavailable"):
            association_selection(registry, ".owned", observed)
    else:
        selected = association_selection(registry, ".owned", observed)
        assert selected is (
            result if status == "ready" and not initial_pending else None
        )
    assert observed["lookup_status"] == status
    registry.default_for_content_type.assert_called_once_with(".owned")


def test_association_child_pumps_owned_apcs_and_bounded_main_context_before_refresh():
    from tools.windows.visual.applications_domain_smoke import (
        refresh_association_registry,
    )

    events, observed = [], {}
    registry = SimpleNamespace(refresh=lambda: events.append("refresh"))
    context = SimpleNamespace(
        pending=lambda: True,
        iteration=lambda block: events.append(("iteration", block)),
    )

    def alertable(milliseconds, enabled):
        assert milliseconds == 0 and enabled is True
        events.append("alertable")
        return 0xC0

    refresh_association_registry(registry, context, alertable, observed)
    assert events == ["alertable", *([("iteration", False)] * 32), "refresh"]
    assert observed["alertable_status"] == 0xC0


def test_association_child_rejects_unexpected_apc_result_without_refresh():
    from tools.windows.visual.applications_domain_smoke import (
        refresh_association_registry,
    )

    registry, context = Mock(), Mock()
    with pytest.raises(AssertionError, match="SleepEx"):
        refresh_association_registry(registry, context, lambda *_args: 87, {})
    context.pending.assert_not_called()
    registry.refresh.assert_not_called()


@pytest.mark.parametrize("failure", [None, "start", "body", "both"])
def test_association_registry_lifetime_closes_before_registration_teardown_and_keeps_original(
    failure,
):
    from tools.windows.visual.applications_domain_smoke import (
        running_association_registry,
    )

    original, cleanup = ValueError("Owned original"), OSError(5, "Owned cleanup")
    events = []

    def start():
        events.append("start")
        if failure == "start":
            raise original

    def stop():
        events.append("stop")
        if failure == "both":
            raise cleanup

    registry = SimpleNamespace(start=start, stop=stop)
    try:
        with running_association_registry(registry) as selected:
            assert selected is registry
            events.append("body")
            if failure in ("body", "both"):
                raise original
    except ValueError as caught:
        assert failure is not None and caught is original
        if failure == "both":
            assert caught.__cause__ is cleanup
    else:
        assert failure is None
    events.append("registration-teardown")
    assert events[-2:] == ["stop", "registration-teardown"]
