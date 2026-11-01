"""Tests for the preferences shortcut capture control."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, Gtk

from docking.search.ui.shortcut_capture import (
    ShortcutCaptureButton,
    shortcut_from_key_event,
    shortcut_label,
)


def test_key_events_use_xdg_shortcut_syntax() -> None:
    state = (
        Gdk.ModifierType.CONTROL_MASK
        | Gdk.ModifierType.MOD1_MASK
        | Gdk.ModifierType.MOD2_MASK
    )

    assert shortcut_from_key_event(Gdk.KEY_space, state) == "CTRL+ALT+space"
    assert shortcut_from_key_event(Gdk.KEY_Control_L, state) is None
    assert shortcut_from_key_event(Gdk.KEY_a, Gdk.ModifierType(0)) is None
    assert shortcut_label("CTRL+LOGO+space") == "Ctrl+Super+Space"


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display is unavailable")
def test_capture_button_commits_next_sequence() -> None:
    capture = ShortcutCaptureButton()
    changed = MagicMock()
    capture.connect("shortcut-changed", changed)
    capture.set_shortcut("CTRL+LOGO+space")

    capture.begin_capture()
    handled = capture._on_key_press(
        capture,
        SimpleNamespace(
            keyval=Gdk.KEY_k,
            state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK,
        ),
    )

    assert handled
    assert capture.get_shortcut() == "CTRL+SHIFT+k"
    assert capture.get_label() == "Ctrl+Shift+k"
    changed.assert_called_once()


@pytest.mark.parametrize(
    "key", [Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Mode_switch, Gdk.KEY_EuroSign]
)
def test_altgr_input_is_not_captured_as_a_global_shortcut(key):
    state = (
        Gdk.ModifierType.CONTROL_MASK
        | Gdk.ModifierType.MOD1_MASK
        | Gdk.ModifierType.MOD5_MASK
    )
    assert shortcut_from_key_event(key, state) is None


def test_windows_altgr_is_rejected_even_when_gdk_reports_only_ctrl_alt(monkeypatch):
    from docking.search.ui import shortcut_capture as mod

    monkeypatch.setattr(mod, "text_modifier_active", lambda: True)
    assert (
        shortcut_from_key_event(
            Gdk.KEY_EuroSign,
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK,
        )
        is None
    )
