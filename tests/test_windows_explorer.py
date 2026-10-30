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
