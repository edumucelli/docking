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
    "failure", ["binary", "independent-launch", "selection", "enter-dispatch"]
)
def test_export_diagnostics_distinguish_copy_failure_from_later_independent_launch(
    tmp_path, monkeypatch, failure
):
    from contextlib import contextmanager
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

    @contextmanager
    def folder(_native, directory):
        assert directory == copied.parent
        yield SimpleNamespace(activate=activated)

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
        "copied-launcher" if failure == "binary" else "independent-launch"
    )
    assert lifecycle["closed"] is (
        failure not in ("independent-launch", "enter-dispatch")
    )
    assert activated.call_count == (0 if failure == "binary" else 1)


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


def test_document_release_rechecks_live_target_after_settling():
    current = [_document_target()]

    def wait(predicate, _message, _seconds):
        assert not predicate()
        settled = dict(current[0])
        current[0] = _document_target(native_window=99)
        return settled

    with pytest.raises(AssertionError, match="changed before release"):
        document_release_target(
            observe=lambda: current[0],
            move=lambda _x, _y: pytest.fail("Already positioned"),
            wait_for=wait,
            identifier="Owned.App",
            record={},
        )


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


@pytest.mark.parametrize("reason", ["process", "window", "workarea", "shell", None])
def test_cleanup_evidence_keeps_existing_rejection_policy_and_exposes_reason(reason):
    baseline = [0, 0, 1280, 752]
    shell = {"pid": 77, "window": 1000, "style": 136}
    rows = [
        {"hwnd": 1, "pid": 99, "class": "Existing"},
        {"hwnd": 2, "pid": 77, "class": "WorkerW"},
    ]
    if reason == "window":
        rows.append({"hwnd": 3, "pid": 88, "class": "UnknownVisible"})
    native = SimpleNamespace(
        workarea=lambda: [0, 0, 1280, 700] if reason == "workarea" else baseline,
        explorer=lambda: {**shell, "pid": 78} if reason == "shell" else shell,
        windows=lambda: rows,
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
    assert snapshot["new_windows"] == ([rows[-1]] if reason == "window" else [])
    assert snapshot["workarea_matches"] is (reason != "workarea")
    assert snapshot["shell_matches"] is (reason != "shell")


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
