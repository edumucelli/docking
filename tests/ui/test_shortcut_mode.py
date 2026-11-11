"""Deliberate reveal, pointer dismissal, and registration recovery."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.config import Config
from docking.core.items import DockItem
from docking.core.theme import Theme
from docking.search.services.global_shortcuts import (
    GlobalShortcutActivation,
    GlobalShortcutBinding,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
)
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.dock_shortcut import DockShortcutController
from docking.ui.geometry import Rect, build_geometry_frame


@pytest.fixture
def window(monkeypatch):
    from docking.ui import autohide, dock_shortcut

    monkeypatch.setattr(autohide.GLib, "timeout_add", lambda *_: 42)
    monkeypatch.setattr(autohide, "_clear_source", lambda **_: 0)
    monkeypatch.setattr(dock_shortcut.GLib, "source_remove", lambda *_: True)
    config = Config(hide_mode="shortcut-only", dock_shortcut="CTRL+ALT+d")
    config.save = MagicMock()
    window = SimpleNamespace(
        config=config, queue_redraw=MagicMock(), on_hide_mode_changed=MagicMock()
    )
    window.autohide = AutoHideController(window, config)
    return window


def test_hidden_mode_ignores_hover_overlap_and_interaction_holds(window):
    controller = window.autohide
    controller.set_shortcut_available(True)
    controller.on_mouse_enter()
    controller.set_window_should_hide(False)
    controller.set_disabled(True)
    assert controller.state is HideState.HIDDEN
    assert controller.hide_offset == 1
    assert not controller.pointer_reveal_enabled


def test_reveal_waits_for_pointer_then_uses_hide_delay(window):
    controller = window.autohide
    window.config.unhide_delay_ms = 2000
    window.config.hide_delay_ms = 500
    controller.set_shortcut_available(True)
    controller.toggle_shortcut()
    assert controller.state is HideState.SHOWING
    assert not controller._unhide_timer_id
    controller.state = HideState.VISIBLE
    controller.hide_offset = 0
    controller.reconcile()
    assert not controller._hide_timer_id
    controller.on_mouse_enter()
    controller.on_mouse_leave()
    assert controller._hide_timer_id
    controller.on_mouse_enter()
    assert not controller._hide_timer_id
    controller.on_mouse_leave()
    controller._start_hiding()
    controller._anim_progress = 1
    controller._animation_tick()
    assert controller.state is HideState.HIDDEN
    controller.on_mouse_enter()
    assert controller.state is HideState.HIDDEN


def test_shortcut_dismisses_without_pointer_entry_and_reverses_animation(window):
    controller = window.autohide
    controller.set_shortcut_available(True)
    controller.toggle_shortcut()
    controller.hide_offset = 0.6
    controller.toggle_shortcut()
    assert controller.state is HideState.HIDING
    assert controller.hide_offset == 0.6
    controller.toggle_shortcut()
    assert controller.state is HideState.SHOWING
    assert controller.hide_offset == 0.6


def test_manual_dismissal_waits_for_active_interaction(window):
    controller = window.autohide
    controller.set_shortcut_available(True)
    controller.toggle_shortcut()
    controller.state = HideState.VISIBLE
    controller.hide_offset = 0
    controller.set_disabled(True)
    controller.toggle_shortcut()
    assert controller.state is HideState.VISIBLE
    controller.set_disabled(False)
    assert controller.state is HideState.HIDING


@pytest.mark.parametrize("position", ["top", "bottom", "left", "right"])
def test_hidden_shortcut_mode_has_no_input_strip(position):
    kwargs = {
        "items": [DockItem(desktop_id="fixture.desktop")],
        "config": Config(position=position),
        "theme": Theme.load("default", 48),
        "window_w": 1280,
        "window_h": 720,
        "cursor_main": -1,
        "autohide_state": HideState.HIDDEN,
        "hide_offset": 1,
    }
    hidden = build_geometry_frame(**kwargs, pointer_reveal_enabled=False)
    fallback = build_geometry_frame(**kwargs, pointer_reveal_enabled=True)
    assert hidden.cursor_rect == Rect(0, 0, 0, 0)
    assert fallback.cursor_rect.w > 0 and fallback.cursor_rect.h > 0


@pytest.fixture
def shortcuts(window, monkeypatch):
    monkeypatch.setattr(
        DockShortcutController, "_new_shortcut_service", lambda _: MagicMock()
    )
    monkeypatch.setattr(
        DockShortcutController, "_new_shortcut_fallback", lambda _: None
    )
    controller = DockShortcutController(window, MagicMock())
    controller.start()
    yield controller
    controller.stop()


def test_mode_requires_assignment_and_binding(window, shortcuts):
    window.config.hide_mode = "none"
    window.config.dock_shortcut = ""
    shortcuts.request_mode()
    assert window.config.hide_mode == "none"
    assert "Assign a shortcut" in shortcuts.status_text()
    window.config.dock_shortcut = "CTRL+ALT+d"
    shortcuts.request_mode()
    assert window.config.hide_mode == "none"
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D"),
        )
    )
    assert window.config.hide_mode == "shortcut-only"
    window.config.save.assert_called_once()
    assert window.autohide.shortcut_only


def test_registration_loss_restores_edge_reveal_without_overwriting_mode(
    window, shortcuts
):
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D"),
        )
    )
    assert window.autohide.shortcut_only
    shortcuts._on_shortcut_status(GlobalShortcutsStatus(GlobalShortcutsState.ERROR))
    assert window.config.hide_mode == "shortcut-only"
    assert window.autohide.pointer_reveal_enabled
    assert shortcuts.status_text().startswith("Using Auto-hide:")
    window.autohide.on_mouse_enter()
    assert window.autohide.state is HideState.SHOWING
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D"),
        )
    )
    assert window.autohide.shortcut_only


def test_capture_and_stale_activation_do_not_change_visibility(window, shortcuts):
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D"),
        )
    )
    shortcuts.suspend_shortcuts()
    shortcuts._on_shortcut_status(GlobalShortcutsStatus(GlobalShortcutsState.STOPPED))
    assert window.autohide.shortcut_only
    event = GlobalShortcutActivation("toggle-dock", 1, None)
    shortcuts._on_global_shortcut(event)
    assert window.autohide.state is HideState.HIDDEN
    shortcuts.resume_shortcuts()
    shortcuts.stop()
    shortcuts._on_global_shortcut(event)
    assert window.autohide.state is HideState.HIDDEN


def test_leaving_mode_cancels_pending_setup(window, shortcuts):
    window.config.hide_mode = "none"
    shortcuts.request_mode()
    assert shortcuts.pending_mode
    shortcuts.mode_changed()
    assert not shortcuts.pending_mode
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D"),
        )
    )
    assert window.config.hide_mode == "none"


def test_empty_portal_assignment_does_not_enable_shortcut_only(window, shortcuts):
    window.config.hide_mode = "none"
    shortcuts.request_mode()
    shortcuts._on_shortcut_status(
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=GlobalShortcutBinding("toggle-dock", "Dock", ""),
        )
    )
    assert window.config.hide_mode == "none"
    assert not shortcuts.shortcut_active
    assert "not assigned" in shortcuts.status_text()


def test_saved_mode_without_a_shortcut_explains_its_fallback(window, shortcuts):
    window.config.dock_shortcut = ""
    shortcuts.refresh_settings()
    assert shortcuts.status_text().startswith("Using Auto-hide:")
    assert window.autohide.pointer_reveal_enabled
