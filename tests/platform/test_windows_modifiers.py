"""AltGr transitions never consume input and failures cross the callback safely."""

import ctypes
from unittest.mock import Mock

import pytest

from docking.platform.native.windows.modifiers import AltGrGuard, _Key


def guard():
    native = AltGrGuard.__new__(AltGrGuard)
    native._hook = 42
    native._callback = None
    native._on_press = Mock()
    native._down = False
    native._error = None
    native._next = Mock(return_value=123)
    native._remove = Mock(return_value=True)
    return native


@pytest.mark.parametrize("key,flags", [(0xA5, 0), (0x12, 1)])
def test_right_alt_transitions_notify_the_owner_and_always_pass_input(key, flags):
    native = guard()
    event = _Key(virtual_key=key, flags=flags)
    address = ctypes.addressof(event)
    assert native._event(0, 0x104, address) == 123
    assert native.active
    native._on_press.assert_called_once_with()
    assert native._event(0, 0x105, address) == 123
    assert not native.active
    assert native._next.call_count == 2


@pytest.mark.parametrize("key,flags", [(0x41, 0), (0xA4, 0), (0x12, 0), (0xA2, 0)])
def test_typing_and_left_modifiers_do_not_trigger_altgr_suspension(key, flags):
    native = guard()
    event = _Key(virtual_key=key, flags=flags)
    assert native._event(0, 0x100, ctypes.addressof(event)) == 123
    assert not native.active
    native._on_press.assert_not_called()


def test_negative_hook_code_is_forwarded_without_dereferencing_the_event():
    native = guard()
    assert native._event(-1, 0x104, 0) == 123
    native._on_press.assert_not_called()


def test_callback_failure_is_reported_later_without_stopping_native_input():
    native = guard()
    native._on_press.side_effect = RuntimeError("Fixture failure")
    event = _Key(virtual_key=0xA5)
    assert native._event(0, 0x104, ctypes.addressof(event)) == 123
    with pytest.raises(OSError, match="modifier callback"):
        native.check()
    native.check()


def test_close_removes_only_the_owned_hook_and_drops_its_callback():
    native = guard()
    native.close()
    native.close()
    native._remove.assert_called_once_with(42)
    assert native._hook is None and native._callback is None and not native.active
