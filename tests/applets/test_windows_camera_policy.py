"""Fixed camera policy scope, edition capabilities and exact value restoration."""

import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from docking.applets.camshield.implementations.windows.policy import (
    KEY,
    VALUE,
    WindowsCameraPolicy,
)


@pytest.fixture
def registry(monkeypatch):
    state = SimpleNamespace(edition="Professional", value=None, kind=4, events=[])

    @contextmanager
    def opened(hive, path, reserved=0, access=0):
        assert hive == 1 and access & 0x100
        state.events.append(("open", path, access))
        yield path

    def query(key, name):
        if name == "EditionID":
            return state.edition, 1
        assert key == KEY and name == VALUE
        if state.value is None:
            raise FileNotFoundError
        return state.value, state.kind

    def write(key, name, reserved, kind, value):
        assert key == KEY and name == VALUE and kind == 4
        state.events.append(("set", value))
        state.value, state.kind = value, kind

    def delete(key, name):
        assert key == KEY and name == VALUE
        if state.value is None:
            raise FileNotFoundError
        state.events.append(("delete",))
        state.value = None

    def flush(key):
        assert key == KEY
        state.events.append(("flush",))

    monkeypatch.setitem(
        sys.modules,
        "winreg",
        SimpleNamespace(
            HKEY_LOCAL_MACHINE=1,
            KEY_READ=0x20019,
            KEY_SET_VALUE=2,
            KEY_WOW64_64KEY=0x100,
            REG_DWORD=4,
            OpenKey=opened,
            CreateKeyEx=opened,
            QueryValueEx=query,
            SetValueEx=write,
            DeleteValue=delete,
            FlushKey=flush,
        ),
    )
    return state, WindowsCameraPolicy()


@pytest.mark.parametrize(
    "edition,supported",
    [
        ("Professional", True),
        ("EnterpriseS", True),
        ("Education", True),
        ("IoTEnterpriseS", True),
        ("Core", False),
        ("CoreSingleLanguage", False),
        ("unknown", False),
    ],
)
def test_camera_policy_is_only_offered_on_documented_editions(
    registry, edition, supported
):
    state, policy = registry
    state.edition = edition
    assert policy.snapshot().supported is supported
    assert not any(row[0] in ("set", "delete") for row in state.events)


def test_policy_write_flush_and_absent_value_restore_use_fixed_machine_scope(registry):
    state, policy = registry
    assert policy.snapshot().value is None
    assert policy.set_value(0) and policy.snapshot().blocked
    assert policy.set_value(None) and policy.snapshot().value is None
    assert [e for e in state.events if e[0] != "open"] == [
        ("set", 0),
        ("flush",),
        ("delete",),
        ("flush",),
    ]


@pytest.mark.parametrize("value,kind", [("0", 1), (2, 4), (False, 4)])
def test_unrecognized_existing_policy_is_rejected_instead_of_overwritten(
    registry, value, kind
):
    state, policy = registry
    state.value, state.kind = value, kind
    with pytest.raises(OSError):
        policy.snapshot()
    assert not any(e[0] in ("set", "delete") for e in state.events)
