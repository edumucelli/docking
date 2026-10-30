"""Owned Explorer cleanup preserves the first failure and remains strict."""

from contextlib import ExitStack
from unittest.mock import Mock

import pytest

from tools.windows.visual import explorer


@pytest.fixture
def folder(tmp_path):
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
    folder.native.click.side_effect = primary
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
