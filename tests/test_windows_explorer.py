"""Owned Explorer cleanup preserves the first failure and remains strict."""

from contextlib import ExitStack
from unittest.mock import Mock

import pytest

from tools.windows.visual import explorer


@pytest.fixture
def folder(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: SimpleNamespace(
                pid=pid, is_running=lambda: True, create_time=lambda: 123.0
            ),
        ),
    )
    native = Mock()
    owned = explorer.ExplorerFolder(native, tmp_path)
    owned.window = {
        "hwnd": 91,
        "pid": 42,
        "class": "CabinetWClass",
        "title": tmp_path.name,
        "rect": [100, 100, 500, 500],
    }
    native.windows.return_value = [owned.window.copy()]
    native.wait_for.side_effect = AssertionError("Owned folder stayed open")
    return owned


def test_cleanup_failure_cannot_mask_an_existing_document_launch_failure(folder):
    primary = AssertionError("Selected application did not report its arguments")
    with pytest.raises(AssertionError) as caught, ExitStack() as stack:
        stack.push(folder)
        raise primary
    assert caught.value is primary
    assert folder.cleanup_failure == {"class": "AssertionError", "errno": None}
    folder.native.user.PostMessageW.assert_called_once_with(91, 0x10, 0, 0)
    assert folder.window is not None


def test_cleanup_failure_alone_is_fatal(folder):
    with (
        pytest.raises(AssertionError, match="Owned folder stayed open"),
        ExitStack() as stack,
    ):
        stack.push(folder)
    assert folder.cleanup_failure == {"class": "AssertionError", "errno": None}


@pytest.mark.parametrize(
    "field,value", [("pid", 99), ("class", "Other"), ("title", "Other")]
)
def test_replaced_window_is_never_closed_or_reported_clean(folder, field, value):
    folder.native.windows.return_value = [{**folder.window, field: value}]
    with pytest.raises(AssertionError):
        folder.__exit__(None, None, None)
    folder.native.user.PostMessageW.assert_not_called()
    assert folder.cleanup_failure is not None and folder.window is not None


def test_normal_cleanup_confirms_the_exact_owned_window_is_gone(folder):
    def closed(predicate, _message):
        assert predicate()

    folder.native.wait_for.side_effect = closed
    folder.native.user.IsWindow.return_value = False
    folder.__exit__(None, None, None)
    assert folder.window is None and folder.cleanup_failure is None
    folder.native.user.PostMessageW.assert_called_once_with(91, 0x10, 0, 0)


def test_failed_entry_preserves_observation_error_even_when_cleanup_also_fails(
    folder, monkeypatch
):
    primary = OSError(5, "Owned window observation failed")
    row = folder.window.copy()
    folder.window = None
    folder.native.windows.side_effect = [[], [row]]
    folder.native.wait_for.side_effect = [
        row,
        AssertionError("Owned folder stayed open"),
    ]
    folder.focus = Mock(side_effect=primary)
    monkeypatch.setattr(explorer.os, "startfile", Mock(), raising=False)
    with pytest.raises(OSError) as caught:
        folder.__enter__()
    assert caught.value is primary
    assert folder.cleanup_failure == {"class": "AssertionError", "errno": None}
    folder.native.user.PostMessageW.assert_called_once_with(91, 0x10, 0, 0)


def activation_fixture(folder):
    state = {"cursor": [0, 0], "selected": False, "focused": False, "presses": []}

    def item(_path, *, selection):
        assert selection is True
        return {
            "x": 150,
            "y": 140,
            "width": 100,
            "height": 120,
            "name": "Owned.lnk",
            "selected": state["selected"],
            "focused": state["focused"],
        }

    def cursor(pointer):
        pointer._obj.x, pointer._obj.y = state["cursor"]
        return 1

    def click(x, y, *, before_press):
        state["cursor"] = [x, y]
        before_press()
        state["presses"].append((x, y))
        state.update(selected=True, focused=True)

    def wait(predicate, message):
        value = predicate()
        assert value, message
        return value

    folder.file_rectangle = Mock(side_effect=item)
    folder.native.click.side_effect = click
    folder.native.wait_for.side_effect = wait
    folder.native.user.GetCursorPos.side_effect = cursor
    folder.native.user.GetForegroundWindow.return_value = 91
    folder.native.window_at.return_value = 91
    return state


def test_independent_explorer_launch_selects_exact_item_then_physically_enters(folder):
    state = activation_fixture(folder)
    observed = {}
    folder.activate(folder.directory / "Owned.lnk", observed)
    assert state["presses"] == [(200, 200)]
    folder.native.key.assert_called_once_with(0x0D)
    folder.native.user.SetForegroundWindow.assert_not_called()
    assert observed["before_click"]["cursor"] == observed["point"] == [200, 200]
    assert observed["before_enter"]["item"]["selected"] is True
    assert observed["before_enter"]["item"]["focused"] is True
    assert observed["before_enter"]["foreground"] == 91
    assert observed["physical_enter"] is True


@pytest.mark.parametrize("mismatch", ["bounds", "occlusion", "source"])
def test_explorer_selection_rejects_changed_target_before_any_physical_press(
    folder, mismatch
):
    state = activation_fixture(folder)
    original = folder.file_rectangle.side_effect
    calls = []

    def item(path, *, selection):
        current = original(path, selection=selection)
        calls.append(1)
        if len(calls) > 1:
            if mismatch == "bounds":
                current["x"] += 20
            elif mismatch == "occlusion":
                folder.native.window_at.return_value = 999
            else:
                folder.native.windows.return_value = [{**folder.window, "pid": 99}]
        return current

    folder.file_rectangle.side_effect = item
    with pytest.raises(AssertionError):
        folder.activate(folder.directory / "Owned.lnk", {})
    assert state["presses"] == []
    folder.native.key.assert_not_called()


@pytest.mark.parametrize("mismatch", ["foreground", "source", "selection", "focus"])
def test_explorer_enter_rechecks_owned_identity_selection_focus_and_foreground(
    folder, mismatch
):
    activation_fixture(folder)
    original = folder.file_rectangle.side_effect
    calls = []

    def item(path, *, selection):
        current = original(path, selection=selection)
        calls.append(1)
        if len(calls) == 4:  # After successful selection proof, just before Enter.
            if mismatch == "foreground":
                folder.native.user.GetForegroundWindow.return_value = 999
            elif mismatch == "source":
                folder.native.windows.return_value = [
                    {**folder.window, "class": "Other"}
                ]
            else:
                current["selected" if mismatch == "selection" else "focused"] = False
        return current

    folder.file_rectangle.side_effect = item
    observed = {}
    with pytest.raises(AssertionError):
        folder.activate(folder.directory / "Owned.lnk", observed)
    folder.native.key.assert_not_called()
    assert "physical_enter" not in observed


@pytest.mark.parametrize("selection", [False, True])
def test_selection_observation_is_read_only_and_does_not_change_existing_geometry_callers(
    folder, monkeypatch, selection
):
    import base64
    import json
    from types import SimpleNamespace

    expected = {"x": 150, "y": 140, "width": 100, "height": 120}
    if selection:
        expected.update(selected=True, focused=True)

    def run(command, **options):
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        assert "FromHandle([IntPtr]91)" in script
        assert ("SelectionItemPattern" in script) is selection
        assert ("HasKeyboardFocus" in script) is selection
        assert (
            ".Select(" not in script
            and ".Invoke(" not in script
            and ".SetFocus(" not in script
        )
        assert options["creationflags"] == 0x08000000
        return SimpleNamespace(stdout=json.dumps(expected))

    monkeypatch.setattr(
        explorer.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(explorer.subprocess, "run", run)
    assert (
        folder.file_rectangle(folder.directory / "Owned.lnk", selection=selection)
        == expected
    )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "missing",
        "replaced",
        "denied",
        "not-found",
        "stale",
        "unfocused",
        "async",
        "partial-press",
        "restore",
    ],
)
def test_owned_cabinet_foreground_preparation_never_targets_a_foreign_window(
    folder, monkeypatch, failure
):
    from types import SimpleNamespace

    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows import windows
    from tools.windows.visual import window_focus

    state = {"foreground": 999}
    snapshot = SimpleNamespace(id=SimpleNamespace(value=91), pid=42)
    folder.native.user.GetForegroundWindow.side_effect = lambda: state["foreground"]

    def wait(predicate, message):
        value = predicate()
        if failure == "async" and not value:
            state["foreground"] = 91
            value = predicate()
        assert value, message

    folder.native.wait_for.side_effect = wait

    def capture(hwnd, *, expected_pid):
        assert (hwnd, expected_pid) == (91, 42)
        if failure == "missing":
            return None
        if failure == "replaced":
            folder.native.windows.return_value = [{**folder.window, "pid": 99}]
        return snapshot

    def activate(target, action):
        assert target is snapshot and action == "activate"
        if failure == "not-found":
            return ActionResult.NOT_FOUND
        if failure in ("denied", "stale", "partial-press", "restore"):
            return ActionResult.FAILED
        if failure not in ("unfocused", "async"):
            state["foreground"] = 91
        return ActionResult.OK

    def assist(native, current, observed):
        assert native is folder.native and current()["hwnd"] == 91
        assert observed["result"] == ActionResult.FAILED.value
        if failure == "partial-press":
            observed["caption_press_started"] = True
            raise AssertionError("Caption input incomplete")
        if failure == "restore":
            observed["geometry_restored"] = False
            raise AssertionError("Caption geometry restore failed")
        state["foreground"] = 91

    physical = Mock(side_effect=assist)
    monkeypatch.setattr(window_focus, "focus_caption", physical)
    api = SimpleNamespace(
        capture_snapshot=capture,
        perform=Mock(side_effect=activate),
        matches=Mock(return_value=failure != "stale"),
    )
    monkeypatch.setattr(windows, "Win32WindowAPI", lambda: api)
    if failure in (
        "missing",
        "replaced",
        "not-found",
        "stale",
        "unfocused",
        "partial-press",
        "restore",
    ):
        with pytest.raises(AssertionError):
            folder.focus()
        if failure not in ("partial-press", "restore"):
            physical.assert_not_called()
        if failure in ("missing", "replaced"):
            api.perform.assert_not_called()
    else:
        folder.focus()
        api.perform.assert_called_once_with(snapshot, "activate")
        assert folder.foreground_observations[-1]["after"] == 91
        assert physical.call_count == (1 if failure == "denied" else 0)
    assert folder.cleanup_safe is (failure not in ("partial-press", "restore"))
    folder.native.click.assert_not_called()
    folder.native.key.assert_not_called()
    folder.native.user.SetForegroundWindow.assert_not_called()


@pytest.mark.parametrize("failure", [None, "source", "foreground"])
def test_entry_checks_owned_identity_and_foreground_before_view_keys(
    folder, monkeypatch, failure
):
    row = folder.window.copy()
    folder.window = None
    calls = []

    def inventory(_pid=None):
        calls.append(1)
        return [] if len(calls) == 1 else [row.copy()]

    def wait(predicate, message):
        value = predicate()
        assert value, message
        return value

    def focus():
        if failure == "source":
            row["pid"] = 99

    folder.focus = Mock(side_effect=focus)
    folder.native.windows.side_effect = inventory
    folder.native.wait_for.side_effect = wait
    folder.native.user.GetForegroundWindow.return_value = (
        999 if failure == "foreground" else 91
    )
    folder.native.user.IsWindow.return_value = False
    monkeypatch.setattr(explorer.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(explorer.time, "sleep", lambda _seconds: None)
    if failure:
        with pytest.raises(AssertionError):
            folder.__enter__()
        folder.native.key.assert_not_called()
    else:
        assert folder.__enter__() is folder
        folder.native.key.assert_called_once_with(ord("2"), control=True, shift=True)
        folder.close()
    folder.native.click.assert_not_called()


def test_enter_dispatch_failure_is_marked_before_input_can_launch(folder):
    activation_fixture(folder)
    folder.native.key.side_effect = AssertionError("Owned Enter input was incomplete")
    observed = {}
    with pytest.raises(AssertionError, match="Enter input was incomplete"):
        folder.activate(folder.directory / "Owned.lnk", observed)
    assert observed["physical_enter_started"] is True
    assert "physical_enter" not in observed


def test_reused_explorer_process_is_never_focused_or_closed(folder):
    from types import SimpleNamespace

    folder._process = SimpleNamespace(pid=42, is_running=lambda: False)
    with pytest.raises(AssertionError, match="exited or was replaced"):
        folder.focus()
    with pytest.raises(AssertionError, match="process was replaced"):
        folder.__exit__(None, None, None)
    assert folder.cleanup_safe is False
    folder.native.click.assert_not_called()
    folder.native.user.PostMessageW.assert_not_called()


@pytest.mark.parametrize("capture_fails", [False, True])
def test_entry_failure_capture_precedes_cleanup_and_preserves_original(
    folder, monkeypatch, tmp_path, capture_fails
):
    from types import SimpleNamespace

    from PIL import ImageGrab

    primary = AssertionError("Owned caption request failed")
    events = []
    row = folder.window.copy()
    folder.window = None
    folder.failure_capture = tmp_path / "owned-entry.png"
    folder.native.windows.side_effect = [[], [row]]
    folder.native.wait_for.side_effect = [row, None]

    def fail():
        folder.foreground_observations.append(
            {"result": "failed", "bounds": row["rect"]}
        )
        raise primary

    def save(path):
        assert path == folder.failure_capture
        events.append("capture")
        if capture_fails:
            raise OSError(5, "Owned capture unavailable")
        path.write_bytes(b"owned capture fixture")

    folder.focus = fail
    folder.native.user.PostMessageW.side_effect = lambda *_args: (
        events.append("close") or 1
    )
    monkeypatch.setattr(explorer.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(
        ImageGrab, "grab", lambda **_options: SimpleNamespace(save=save)
    )
    with pytest.raises(AssertionError) as caught:
        folder.__enter__()
    assert caught.value is primary and events == ["capture", "close"]
    assert folder.foreground_observations[0]["result"] == "failed"
    capture = folder.foreground_observations[1]
    assert capture["failure_capture"] == "owned-entry.png"
    if capture_fails:
        assert capture["capture_error"] == {"class": "OSError", "errno": 5}
    else:
        assert folder.failure_capture.is_file()
