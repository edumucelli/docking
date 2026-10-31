"""Write-ahead policy recovery, existing restrictions and external changes."""

from dataclasses import replace

import pytest

from docking.applets.camshield.windows_helper import CameraTransitions
from docking.applets.camshield.windows_journal import RecordedPolicy, _decode
from docking.applets.camshield.windows_policy import CameraPolicyState


class Journal:
    def __init__(self, events):
        self.record = None
        self.events = events

    def read(self):
        return self.record

    def write(self, record):
        self.record = record
        self.events.append(("save", record))


class Policy:
    def __init__(self, events):
        self.state = CameraPolicyState(None, True)
        self.events = events
        self.fail = None
        self.apply = True

    def snapshot(self):
        return self.state

    def set_value(self, value):
        self.events.append(("policy", value))
        if self.fail:
            raise self.fail
        if self.apply:
            self.state = replace(self.state, value=value)
        return self.apply


@pytest.fixture
def camera():
    events = []
    policy, journal = Policy(events), Journal(events)
    return CameraTransitions(policy=policy, journal=journal), policy, journal, events


@pytest.mark.parametrize("original", [None, 1])
def test_lock_journals_before_restriction_and_restores_exact_previous_value(
    camera, original
):
    control, policy, journal, events = camera
    policy.state = replace(policy.state, value=original)
    assert control.lock()
    assert events[:2] == [("save", RecordedPolicy(original)), ("policy", 0)]
    assert control.lock()  # Repetition preserves the original baseline.
    assert journal.record == RecordedPolicy(original)
    assert control.unlock() and journal.record is None
    assert policy.state.value == original


def test_existing_policy_restriction_is_never_adopted_or_removed(camera):
    control, policy, journal, events = camera
    policy.state = replace(policy.state, value=0)
    assert not control.lock() and not control.unlock()
    assert not events and journal.record is None and policy.state.blocked


def test_interrupted_lock_recovers_prepared_intent(camera):
    _control, policy, journal, _ = camera
    journal.write(RecordedPolicy(None))
    policy.state = replace(policy.state, value=0)
    restarted = CameraTransitions(policy=policy, journal=journal)
    assert restarted.unlock() and journal.record is None
    assert policy.state.value is None


def test_unconfirmed_write_keeps_intent_and_recovery_avoids_unnecessary_write(camera):
    control, policy, journal, events = camera
    policy.apply = False
    assert not control.lock() and journal.record == RecordedPolicy(None)
    before = len([e for e in events if e[0] == "policy"])
    assert control.unlock() and journal.record is None
    assert len([e for e in events if e[0] == "policy"]) == before


@pytest.mark.parametrize("external", [None, 1])
def test_external_replacement_is_preserved_during_unlock(camera, external):
    control, policy, journal, events = camera
    assert control.lock()
    policy.state = replace(policy.state, value=external)
    events.clear()
    assert control.unlock() and journal.record is None
    assert policy.state.value == external and events == [("save", None)]


def test_failed_restore_retains_recovery(camera):
    control, policy, journal, _ = camera
    assert control.lock()
    policy.apply = False
    assert not control.unlock() and journal.record is not None
    assert policy.state.blocked
    policy.fail = OSError("access denied")
    with pytest.raises(OSError):
        control.unlock()
    assert journal.record is not None


def test_unsupported_edition_does_not_change_policy_or_create_recovery(camera):
    control, policy, journal, events = camera
    policy.state = replace(policy.state, supported=False)
    assert not control.lock() and not control.unlock()
    assert not events and journal.record is None


def test_failed_durable_recovery_write_prevents_policy_mutation(camera, monkeypatch):
    control, policy, journal, events = camera

    def fail(record):
        raise OSError("registry flush failed")

    monkeypatch.setattr(journal, "write", fail)
    with pytest.raises(OSError):
        control.lock()
    assert not events and not policy.state.blocked


@pytest.mark.parametrize(
    "value",
    [
        "broken",
        "[]",
        '{"version":2,"policy":null}',
        '{"version":1,"policy":{"original":0}}',
        '{"version":1,"policy":{"original":true}}',
        '{"version":1,"policy":{"original":"1"}}',
        '{"version":1,"policy":{"original":1,"path":"arbitrary"}}',
        '{"version":1,"cameras":[]}',
        '{"version":true,"policy":null}',
        '{"version":1,"cameras":[{"id":"old-device","name":"camera"}]}',
    ],
)
def test_invalid_recovery_data_is_never_accepted(value):
    with pytest.raises(OSError):
        _decode(value)


def test_recovery_distinguishes_absent_original_from_no_pending_restore():
    assert _decode('{"version":1,"policy":null}') is None
    assert _decode('{"version":1,"policy":{"original":null}}') == RecordedPolicy(None)
    assert _decode('{"version":1,"policy":{"original":1}}') == RecordedPolicy(1)
