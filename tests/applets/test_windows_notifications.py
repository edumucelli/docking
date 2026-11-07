"""Notification permission, effective DND, identity and clear boundaries."""

import ctypes
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.notifications.implementations.windows import backend as mod
from docking.applets.notifications.state import NotificationEntry


@pytest.fixture
def native_notification(monkeypatch):
    failures = {}
    graph = {
        (1, 7): 2,
        (2, 8): 3,
        (1, 6): 4,
        (4, 8): 5,
        (5, 9): 6,
        (6, 11): 7,
        (7, 6, 0): 8,
        (7, 6, 1): 9,
    }

    def object_result(scope, pointer, index, *args):
        key = (pointer.value, index)
        if key in failures:
            raise failures[key]
        if pointer.value == 7 and index == 6:
            key += (args[-1],)
        return ctypes.c_void_p(graph[key])

    def value(pointer, index, kind):
        return kind({(1, 8): 42, (1, 9): 123456, (7, 7): 2}[(pointer.value, index)])

    @contextmanager
    def string(text):
        assert text == "ToastGeneric"
        yield ctypes.c_void_p(10)

    text = {3: "Known sender", 8: "Generated title", 9: "Résumé body"}
    runtime = SimpleNamespace(
        string=string, text=lambda pointer, index: text[pointer.value]
    )
    monkeypatch.setattr(mod, "object_result", object_result)
    monkeypatch.setattr(mod, "value", value)

    def read():
        with ExitStack() as scope:
            return mod.NotificationListener._notification(
                runtime, scope, ctypes.c_void_p(1)
            )

    return failures, read


def test_native_notification_retains_sender_text_and_identity(native_notification):
    _failures, read = native_notification
    assert read() == (
        "42",
        123456,
        NotificationEntry("Known sender", "Generated title", "Résumé body"),
    )


def test_unimplemented_sender_metadata_does_not_hide_notification(native_notification):
    failures, read = native_notification
    failures[(1, 7)] = OSError(0x80004001, "Unpackaged sender has no IAppInfo")
    assert read() == (
        "42",
        123456,
        NotificationEntry("", "Generated title", "Résumé body"),
    )


@pytest.mark.parametrize(
    "key,error",
    [
        ((1, 7), OSError(0x80070005, "Permission denied")),
        ((2, 8), OSError(0x80004001, "Display service failed")),
        ((1, 6), OSError(0x80004001, "Notification unavailable")),
    ],
)
def test_other_native_notification_failures_still_propagate(
    native_notification, key, error
):
    failures, read = native_notification
    failures[key] = error
    with pytest.raises(OSError) as result:
        read()
    assert result.value is error


@pytest.fixture
def backend():
    backend = mod.WindowsNotificationsBackend()
    backend._quiet = Mock()
    backend._quiet.selected_profile.return_value = mod._UNRESTRICTED
    backend._quiet.effective_profile.return_value = 0
    backend._quiet.set_profile.return_value = True
    backend._listener = Mock()
    backend._listener.read.return_value = (1, ())
    backend._listener.remove.return_value = True
    yield backend
    backend.stop()


def row(identifier="1", created=100, summary="First"):
    return identifier, created, NotificationEntry("Docking Fixture", summary, "Body")


def test_permission_revocation_is_unknown_count_and_regrant_is_readable(backend):
    backend._listener.read.side_effect = [(1, (row(),)), (2, ()), (1, (row(),))]
    first, revoked, restored = backend.poll(), backend.poll(), backend.poll()
    assert first.state.pending == 1 and first.state.pending_known
    assert len(first.events) == 1
    assert revoked.state.available and revoked.state.access_required
    assert not revoked.state.pending_known and not revoked.state.can_clear
    assert restored.events == first.events and restored.state.pending_known


def test_unavailable_quiet_hours_does_not_hide_available_history(backend):
    backend._quiet.selected_profile.side_effect = OSError("Unknown ABI")
    backend._listener.read.return_value = (1, (row(),))
    result = backend.poll()
    assert result.state.available and result.state.pending == 1
    assert not result.state.paused_known and not result.state.can_pause


def test_notification_failure_does_not_claim_zero_pending(backend):
    backend._listener.read.side_effect = OSError("Service offline")
    result = backend.poll()
    assert result.state.available and result.state.paused_known
    assert not result.state.pending_known and not result.state.can_clear


def test_actual_effective_profile_overrides_manual_selection(backend):
    backend._quiet.effective_profile.return_value = 1
    assert backend.poll().state.paused
    backend._cancelled.wait = Mock()
    assert not backend.set_paused(False)
    backend._quiet.set_profile.assert_called_once_with(mod._UNRESTRICTED)
    assert backend.poll().state.paused


def test_preexisting_alarms_profile_is_preserved_when_reenabling(backend):
    backend._quiet.selected_profile.side_effect = [
        mod._ALARMS,
        mod._UNRESTRICTED,
        mod._UNRESTRICTED,
        mod._ALARMS,
    ]
    backend._quiet.effective_profile.side_effect = [0, 2]
    assert backend.set_paused(False)
    assert backend.set_paused(True)
    assert [call.args[0] for call in backend._quiet.set_profile.call_args_list] == [
        mod._UNRESTRICTED,
        mod._ALARMS,
    ]


def test_shell_selection_may_arrive_after_accepted_setter(backend):
    backend._quiet.selected_profile.side_effect = [
        mod._UNRESTRICTED,
        mod._UNRESTRICTED,
        mod._PRIORITY,
    ]
    backend._quiet.effective_profile.return_value = 1
    backend._cancelled.wait = Mock()
    assert backend.set_paused(True)
    backend._cancelled.wait.assert_called_once_with(0.05)


def test_notifications_deduplicate_but_replacements_and_reused_ids_are_seen(backend):
    backend._listener.read.side_effect = [
        (1, (row("2", 200, "Second"), row())),
        (1, (row(), row("2", 200, "Second"))),
        (1, (row(summary="Changed"),)),
        (1, ()),
        (1, (row(created=300),)),
    ]
    first = backend.poll()
    assert [event.summary for event in first.events] == ["First", "Second"]
    assert backend.poll().events == ()
    assert backend.poll().events == (row(summary="Changed")[2],)
    assert backend.poll().state.pending == 0
    assert backend.poll().events == (row(created=300)[2],)


def test_clear_is_rejected_when_notifications_change_since_display(backend):
    backend._listener.read.return_value = (1, (row(),))
    state = backend.poll().state
    backend._listener.read.return_value = (1, (row(), row("2")))
    assert not backend.clear_notifications(state)
    backend._listener.remove.assert_not_called()


def test_clear_passes_only_the_displayed_snapshot_to_targeted_removal(backend):
    rows = (row(), row("2"))
    backend._listener.read.return_value = (1, rows)
    state = backend.poll().state
    assert backend.clear_notifications(state)
    backend._listener.remove.assert_called_once_with(rows, backend._cancelled)


def test_large_history_has_no_global_clear_capability(backend):
    backend._listener.read.return_value = (1, tuple(row(str(i)) for i in range(41)))
    state = backend.poll().state
    assert state.pending == 41 and not state.can_clear
    assert not backend.clear_notifications(state)


def test_stop_prevents_all_further_io(backend):
    backend.stop()
    assert not backend.poll().state.available
    assert not backend.set_paused(True)
    assert not backend.clear_notifications(mod.NotificationsState())
    backend._quiet.selected_profile.assert_not_called()
    backend._listener.read.assert_not_called()


def test_unknown_shell_profile_never_invokes_setter(monkeypatch):
    quiet = mod.QuietHours()
    monkeypatch.setattr(quiet, "_settings", Mock(side_effect=AssertionError("setter")))
    assert not quiet.set_profile("future-profile")


def test_clear_requires_the_displayed_revision_even_after_a_new_worker_read(backend):
    backend._listener.read.return_value = (1, (row(),))
    shown = backend.poll().state
    backend._listener.read.return_value = (1, (row(), row("2")))
    backend.poll()
    assert not backend.clear_notifications(shown)
    backend._listener.remove.assert_not_called()


def test_empty_or_revoked_history_cannot_be_cleared(backend):
    state = backend.poll().state
    assert not backend.clear_notifications(state)
    backend._listener.read.return_value = (2, ())
    assert not backend.clear_notifications(replace(state, can_clear=True))
