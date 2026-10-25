"""Win32 hotkey adapter preserves native flags, layouts and event identity."""

from unittest.mock import MagicMock

import pytest

from docking.platform.backends.windows.hotkeys import (
    HOTKEY_ID,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    WindowsHotkeyAPI,
)


def _api():
    native = WindowsHotkeyAPI.__new__(WindowsHotkeyAPI)
    native._register = MagicMock(return_value=True)
    native._unregister = MagicMock(return_value=True)
    native._peek = MagicMock(return_value=False)
    native._foreground = MagicMock(return_value=44)
    native._window_thread = MagicMock(return_value=7)
    native._layout = MagicMock(return_value=1033)
    native._key_scan = MagicMock(return_value=ord("K"))
    return native


def test_native_registration_suppresses_repeat_and_uses_thread_owned_id():
    native = _api()
    native.register(MOD_CONTROL | MOD_ALT, 0x20)
    native._register.assert_called_once_with(
        None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20
    )
    native._key_scan.assert_not_called()
    native.unregister()
    native._unregister.assert_called_once_with(None, HOTKEY_ID)


@pytest.mark.parametrize(
    "translated,expected",
    [
        (0x01BB, MOD_CONTROL | MOD_SHIFT),
        (0x06BA, MOD_CONTROL | MOD_ALT),
    ],
)
def test_character_translation_keeps_layout_required_modifiers(translated, expected):
    native = _api()
    native._key_scan.return_value = translated
    native.register(MOD_CONTROL, ";")
    native._layout.assert_called_once_with(7)
    native._key_scan.assert_called_once_with(";", 1033)
    native._register.assert_called_once_with(
        None, HOTKEY_ID, expected | MOD_NOREPEAT, translated & 0xFF
    )


@pytest.mark.parametrize("translated", [-1, 0x0841])
def test_untranslatable_or_unsupported_layout_state_does_not_register(translated):
    native = _api()
    native._key_scan.return_value = translated
    with pytest.raises(ValueError):
        native.register(MOD_CONTROL, "é")
    native._register.assert_not_called()


@pytest.mark.parametrize("event_id,expected", [(HOTKEY_ID, 1234), (99, None)])
def test_only_the_registered_hotkey_id_produces_an_activation(event_id, expected):
    native = _api()

    def event(pointer, hwnd, first, last, remove):
        assert hwnd is None and (first, last, remove) == (WM_HOTKEY, WM_HOTKEY, 1)
        pointer._obj.wparam = event_id
        pointer._obj.time = 1234
        return True

    native._peek = event
    assert native.activation() == expected
