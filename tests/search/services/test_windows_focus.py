"""Bounded shell handoff completion must respect subsequent user focus."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.search.services.implementations.windows import focus as module


@pytest.fixture
def focus(monkeypatch):
    sources = {}
    sequence = iter(range(1, 100))

    def idle(callback):
        value = next(sequence)
        sources[value] = callback
        return value

    monkeypatch.setattr(module.GLib, "idle_add", idle)
    monkeypatch.setattr(
        module.GLib, "timeout_add", lambda _ms, callback: idle(callback)
    )
    monkeypatch.setattr(module.GLib, "source_remove", sources.pop)
    native = MagicMock()
    native.foreground.return_value = origin = (10, 100)
    native.target.return_value = target = (20, 200)
    native.shell_origin.return_value = True
    native.shell_return.side_effect = lambda identity: identity == (30, 300)
    native.input_time.return_value = 1234
    value = module.WindowsSearchFocus(native=native)
    return SimpleNamespace(
        value=value, native=native, sources=sources, origin=origin, target=target
    )


def begin(focus, visible=lambda: True):
    focus.value.before_present()
    focus.value.after_present(title="Docking Search", visible=visible)


def test_shell_returns_foreground_then_idle_completes_original_activation(focus):
    begin(focus)
    focus.native.restore.assert_not_called()
    assert len(focus.sources) == 1  # Expiration only, no timing-based focus loop.
    focus.native.foreground.return_value = explorer = (30, 300)
    changed = focus.native.start.call_args.args[0]
    changed(explorer)
    changed(explorer)
    assert len(focus.sources) == 2
    focus.sources.pop(focus.value._idle)()
    focus.native.restore.assert_called_once_with(focus.target, explorer)
    assert focus.value._pending
    assert list(focus.sources) == [focus.value._expiry]


def test_foreground_event_for_search_does_not_count_until_native_ownership_matches(
    focus,
):
    begin(focus)
    changed = focus.native.start.call_args.args[0]
    changed(focus.target)  # Shell still owns foreground despite GTK focus-in.
    assert focus.value._pending
    focus.native.foreground.return_value = focus.target
    changed(focus.target)
    assert focus.value._pending
    assert list(focus.sources) == [focus.value._expiry]
    focus.native.restore.assert_not_called()


def test_another_application_taking_focus_cancels_pending_handoff(focus):
    begin(focus)
    changed = focus.native.start.call_args.args[0]
    focus.native.foreground.return_value = other = (40, 400)
    changed(other)
    assert not focus.value._pending
    assert focus.sources == {}
    focus.native.foreground.return_value = (30, 300)
    changed((30, 300))
    focus.native.restore.assert_not_called()


@pytest.mark.parametrize("reason", ["other-app", "hidden", "cancelled"])
def test_queued_completion_cannot_override_later_focus_or_hidden_window(focus, reason):
    shown = [True]
    begin(focus, lambda: shown[0])
    focus.native.foreground.return_value = (30, 300)
    focus.value._changed((30, 300))
    callback = focus.sources.pop(focus.value._idle)
    focus.value._idle = 0
    if reason == "other-app":
        focus.native.foreground.return_value = (40, 400)
    elif reason == "hidden":
        shown[0] = False
    else:
        focus.value.cancel()
    callback()
    focus.native.restore.assert_not_called()
    assert focus.sources == {}


def test_expiry_releases_native_observer_without_forcing_focus(focus):
    begin(focus)
    focus.sources.pop(focus.value._expiry)()
    assert focus.sources == {}
    assert not focus.value._pending
    focus.native.restore.assert_not_called()


def test_first_foreground_success_keeps_the_bounded_shell_observer(focus):
    focus.value.before_present()
    focus.native.foreground.return_value = focus.target
    focus.value.after_present(title="Docking Search", visible=lambda: True)
    assert list(focus.sources) == [focus.value._expiry]
    focus.native.restore.assert_not_called()
    focus.native.foreground.return_value = focus.origin
    focus.value._changed(focus.origin)
    focus.sources.pop(focus.value._idle)()
    focus.native.restore.assert_called_once_with(focus.target, focus.origin)
    focus.sources.pop(focus.value._expiry)()
    assert not focus.value._pending
    assert not focus.sources


def test_non_shell_origin_needs_no_repair(focus):
    focus.native.start.reset_mock()
    focus.native.shell_origin.return_value = False
    begin(focus)
    focus.native.start.assert_not_called()


@pytest.mark.parametrize("shell", ["origin", "explorer"])
def test_new_user_input_cancels_repair_after_the_first_success(focus, shell):
    focus.value.before_present()
    focus.native.foreground.return_value = focus.target
    focus.value.after_present(title="Docking Search", visible=lambda: True)
    focus.native.input_time.return_value = 1235
    focus.native.foreground.return_value = (
        focus.origin if shell == "origin" else (30, 300)
    )
    focus.value._changed(focus.native.foreground.return_value)
    assert not focus.value._pending
    assert not focus.sources
    focus.native.restore.assert_not_called()


def test_queued_repair_checks_for_input_that_arrives_before_dispatch(focus):
    begin(focus)
    focus.native.foreground.return_value = (30, 300)
    focus.value._changed((30, 300))
    callback = focus.sources.pop(focus.value._idle)
    focus.value._idle = 0
    focus.native.input_time.return_value = 1235
    callback()
    focus.native.restore.assert_not_called()
    assert not focus.value._pending


def test_unknown_last_input_cannot_authorize_shell_reactivation(focus):
    focus.native.input_time.return_value = None
    begin(focus)
    focus.native.foreground.return_value = (30, 300)
    focus.value._changed((30, 300))
    assert not focus.value._pending
    focus.native.restore.assert_not_called()


def test_native_last_input_reads_a_sized_win32_structure():
    native = module.WindowsShellFocus.__new__(module.WindowsShellFocus)

    def read(pointer):
        assert pointer._obj.size == 8
        pointer._obj.tick = 0xFFFFFFFF
        return True

    native._last_input = read
    assert native.input_time() == 0xFFFFFFFF
    native._last_input = lambda _pointer: False
    assert native.input_time() is None


@pytest.mark.parametrize(
    ("class_name", "process", "origin", "returned"),
    [
        ("Shell_TrayWnd", "explorer", True, True),
        ("Progman", "explorer", True, True),
        ("Windows.UI.Core.CoreWindow", "startmenuexperiencehost", True, False),
        ("Windows.UI.Core.CoreWindow", "searchhost", True, False),
        ("Windows.UI.Core.CoreWindow", "ordinary-app", False, False),
        ("Shell_TrayWnd", "ordinary-app", False, False),
    ],
)
def test_only_verified_shell_processes_are_eligible_for_handoff(
    class_name, process, origin, returned
):
    native = module.WindowsShellFocus.__new__(module.WindowsShellFocus)
    native._class = object()
    native._name = lambda *_args: class_name
    native._process = lambda _pid: process
    assert native.shell_origin((10, 100)) is origin
    assert native.shell_return((10, 100)) is returned


@pytest.mark.parametrize(
    ("pid", "title", "expected"),
    [
        (os.getpid(), "Docking Search", (20, os.getpid())),
        (os.getpid(), "Preferences", None),
        (0, "Docking Search", None),
    ],
)
def test_target_must_be_this_process_and_the_requested_window(pid, title, expected):
    native = module.WindowsShellFocus.__new__(module.WindowsShellFocus)
    native._title = object()
    native.active = lambda: (20, pid)
    native._name = lambda *_args: title
    assert native.target("Docking Search") == expected
