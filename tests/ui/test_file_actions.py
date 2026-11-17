"""Chooser and worker lifetime outlive menus, but never the dock."""

from unittest.mock import MagicMock, Mock

import pytest

from docking.platform.desktop.base import OpenWithMode, OpenWithResult
from docking.ui import file_actions


@pytest.fixture
def controller():
    result = file_actions.FileActionsController(
        parent=MagicMock(), target_service=MagicMock()
    )
    result._worker = Mock()
    result._error = Mock()
    return result


def test_chooser_preparation_is_deferred_and_duplicate_requests_coalesce(controller):
    controller.open_with("file:///owned/document.txt")
    controller.open_with("file:///owned/other.txt")
    controller._worker.run.assert_called_once()
    request = controller._worker.run.call_args.kwargs
    controller._targets.prepare_open_with.assert_not_called()
    request["fn"]()
    controller._targets.prepare_open_with.assert_called_once_with(
        "file:///owned/document.txt"
    )
    request["on_error"](OSError("missing"))
    assert not controller._pending
    controller._error.assert_called_once()


def test_shutdown_discards_preparation_results_without_opening_ui(controller):
    controller.open_with("file:///owned/document.txt")
    callback = controller._worker.run.call_args.kwargs["on_result"]
    controller.stop()
    callback(("file:///owned/document.txt", "text/plain"))
    controller._targets.request_open_with.assert_not_called()
    controller._targets.open_with_mode.assert_not_called()


def test_native_request_cancellation_on_stop_and_no_late_error(controller):
    controller._targets.open_with_mode.return_value = OpenWithMode.NATIVE
    cancel = Mock()
    controller._targets.request_open_with.return_value = cancel
    controller._error = file_actions.FileActionsController._error.__get__(controller)
    controller._pending = True
    controller._choose(("file:///owned/document.txt", "text/plain"))
    controller.stop()
    cancel.assert_called_once()
    controller._finished(OpenWithResult.FAILED)
    assert controller._error_dialog is None


def test_gtk_chooser_launches_exact_selected_handler_and_cancel_is_silent(
    controller, monkeypatch
):
    dialog = MagicMock()
    selected = object()
    dialog.get_app_info.return_value = selected
    constructor = Mock(return_value=dialog)
    monkeypatch.setattr(
        file_actions.Gtk.AppChooserDialog, "new_for_content_type", constructor
    )
    controller._targets.open_with_mode.return_value = OpenWithMode.GTK
    controller._choose(("file:///owned/document.txt", "text/plain"))
    assert constructor.call_args.args[2] == "text/plain"
    controller._chosen(
        dialog, file_actions.Gtk.ResponseType.OK, "file:///owned/document.txt"
    )
    controller._targets.launch_with_handler.assert_called_once_with(
        "file:///owned/document.txt", selected, on_result=controller._finished
    )
    dialog.destroy.assert_called_once()
    controller._finished(OpenWithResult.ACCEPTED)
    controller._chosen(
        dialog, file_actions.Gtk.ResponseType.CANCEL, "file:///owned/document.txt"
    )
    assert controller._targets.launch_with_handler.call_count == 1
    controller._error.assert_not_called()
