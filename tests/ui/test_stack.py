"""Tests for the reusable curved item-stack popup."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.applets.popup import PopupAnchor
from docking.core.position import Position
from docking.ui import stack as stack_mod
from docking.ui.stack import (
    FOLDER_STACK_MAX_VISIBLE_ROWS,
    StackAction,
    StackContent,
    StackEntry,
    StackPopupController,
)


def _controller() -> StackPopupController:
    return StackPopupController(
        config=SimpleNamespace(icon_size=48, pos="bottom"),
        runtime=MagicMock(),
        dock_window=MagicMock(),
    )


def _entry(key: str, label: str | None = None) -> StackEntry:
    return StackEntry(
        key=key,
        label=label or key,
        icon=None,
        activate=MagicMock(),
    )


def test_empty_content_builds_centered_message():
    controller = _controller()

    layout = controller._stack_layout(
        owner_id="applet://devices",
        content=StackContent(empty_label="No devices"),
    )

    assert len(layout.cards) == 1
    assert layout.cards[0].target is None
    assert layout.cards[0].label == "No devices"
    assert layout.cards[0].centered is True


def test_entries_and_optional_action_build_clickable_cards():
    controller = _controller()
    content = StackContent(
        entries=(_entry("usb", "USB Drive"), _entry("disk", "Backup Disk")),
        action=StackAction(
            key="settings",
            label="Open Disks",
            activate=MagicMock(),
        ),
    )

    layout = controller._stack_layout(
        owner_id="applet://devices",
        content=content,
    )

    assert [card.target for card in layout.cards] == ["settings", "usb", "disk"]
    assert layout.cards[0].action is True
    assert all(card.icon is None for card in layout.cards)


def test_layout_limits_provider_entries(caplog):
    controller = _controller()
    entries = tuple(
        _entry(f"device-{index}") for index in range(FOLDER_STACK_MAX_VISIBLE_ROWS + 2)
    )

    layout = controller._stack_layout(
        owner_id="applet://devices",
        content=StackContent(entries=entries),
    )

    assert len(layout.cards) == FOLDER_STACK_MAX_VISIBLE_ROWS
    assert "displaying the first" in caplog.text


def test_thumbnail_style_invalidates_cached_layout_and_refresh_signature():
    controller = _controller()
    original = StackContent(entries=(_entry("photo"),))
    styled = replace(
        original,
        entries=(replace(original.entries[0], thumbnail_style=True),),
    )
    first = controller._stack_layout(owner_id="recentfiles", content=original)
    second = controller._stack_layout(owner_id="recentfiles", content=styled)

    assert controller._stack_content_signature(original) != (
        controller._stack_content_signature(styled)
    )
    assert not first.cards[0].thumbnail_style
    assert second.cards[0].thumbnail_style
    assert controller._stack_layout(owner_id="recentfiles", content=styled) is second


def test_documented_curve_samples_for_48px_icons():
    controller = _controller()
    samples: dict[int, list[tuple[int, float]]] = {}
    for count in range(1, 6):
        layout = controller._stack_layout(
            owner_id=f"stack-{count}",
            content=StackContent(
                entries=tuple(_entry(str(index)) for index in range(count))
            ),
        )
        icons = [card for card in layout.cards if card.icon_size > 0]
        samples[count] = [
            (card.icon_x, round(card.stack_progress, 3)) for card in icons
        ]

    assert samples == {
        1: [(180, 0.0)],
        2: [(187, 0.333), (180, 0.0)],
        3: [(201, 0.667), (187, 0.333), (180, 0.0)],
        4: [(220, 1.0), (201, 0.667), (187, 0.333), (180, 0.0)],
        5: [
            (220, 1.0),
            (205, 0.75),
            (193, 0.5),
            (185, 0.25),
            (180, 0.0),
        ],
    }


def test_short_stack_action_aligns_with_top_entry_curve_position():
    controller = _controller()
    layout = controller._stack_layout(
        owner_id="short-with-action",
        content=StackContent(
            entries=(_entry("one"), _entry("two")),
            action=StackAction(
                key="open",
                label="Open Folder",
                activate=MagicMock(),
            ),
        ),
    )

    assert layout.cards[0].action is True
    assert layout.cards[0].stack_progress == layout.cards[1].stack_progress


def test_activation_uses_latest_callback_and_closes_popup():
    controller = _controller()
    activate = MagicMock()
    controller._stack_content = StackContent(
        entries=(
            StackEntry(
                key="usb",
                label="USB Drive",
                icon=None,
                activate=activate,
            ),
        )
    )
    controller._close_stack = MagicMock()

    controller._activate_stack_key("usb")

    activate.assert_called_once()
    controller._close_stack.assert_called_once()


def test_refresh_reloads_visible_provider_content():
    controller = _controller()
    first = StackContent(entries=(_entry("first"),))
    second = StackContent(entries=(_entry("second"),))
    provider = MagicMock(return_value=second)
    window = MagicMock()
    window.get_visible.return_value = True
    controller._folder_stack_window = window
    controller._stack_owner_id = "applet://devices"
    controller._stack_provider = provider
    controller._stack_content = first
    controller._replace_stack_content = MagicMock()
    controller._restart_stack_animation = MagicMock()
    controller._position_stack_window = MagicMock()

    assert controller.refresh(owner_id="applet://devices") is True

    assert controller._stack_content is second
    controller._replace_stack_content.assert_called_once_with(content=second)
    controller._position_stack_window.assert_called_once()
    window.show_all.assert_called_once()


def test_refresh_updates_callbacks_without_rebuilding_unchanged_content():
    controller = _controller()
    old_activate = MagicMock()
    new_activate = MagicMock()
    first = StackContent(
        entries=(
            StackEntry(
                key="usb",
                label="USB Drive",
                icon=None,
                activate=old_activate,
            ),
        )
    )
    second = StackContent(
        entries=(
            StackEntry(
                key="usb",
                label="USB Drive",
                icon=None,
                activate=new_activate,
            ),
        )
    )
    provider = MagicMock(return_value=second)
    window = MagicMock()
    window.get_visible.return_value = True
    controller._folder_stack_window = window
    controller._stack_owner_id = "applet://devices"
    controller._stack_provider = provider
    controller._stack_content = first
    controller._replace_stack_content = MagicMock()
    controller._restart_stack_animation = MagicMock()
    controller._position_stack_window = MagicMock()

    assert controller.refresh(owner_id="applet://devices") is True

    assert controller._stack_content is second
    controller._replace_stack_content.assert_not_called()
    controller._restart_stack_animation.assert_not_called()
    controller._position_stack_window.assert_not_called()
    window.show_all.assert_not_called()

    controller._close_stack = MagicMock()
    controller._activate_stack_key("usb")
    new_activate.assert_called_once()
    old_activate.assert_not_called()


def _anchor() -> PopupAnchor:
    return PopupAnchor(x=10, y=20, position=Position.BOTTOM)


def _open_controller(owner_id: str = "applet://devices"):
    """Return a controller whose stack window is visible and already open."""
    controller = _controller()
    window = MagicMock()
    window.get_visible.return_value = True
    # Keep the fake honest: hiding the window makes it report as hidden, which
    # is what makes a second close a no-op.
    window.hide.side_effect = lambda: setattr(window.get_visible, "return_value", False)
    controller._folder_stack_window = window
    controller._ensure_stack_window = MagicMock(return_value=window)
    controller._folder_stack_revealer = MagicMock()
    controller._replace_stack_content = MagicMock()
    controller._restart_stack_animation = MagicMock()
    controller._position_stack_window = MagicMock()
    controller._stack_owner_id = owner_id
    return controller


def test_show_stack_toggles_the_same_owner_closed():
    controller = _open_controller()
    controller._close_stack = MagicMock()
    provider = MagicMock()

    result = controller.show_stack(
        owner_id="applet://devices", provider=provider, anchor=_anchor()
    )

    assert result is True
    controller._close_stack.assert_called_once()
    # A toggle must not rebuild the content it is closing.
    provider.assert_not_called()
    controller._replace_stack_content.assert_not_called()


def test_show_stack_refuses_when_the_provider_has_no_content():
    controller = _open_controller()
    controller._folder_stack_window.get_visible.return_value = False

    result = controller.show_stack(
        owner_id="applet://other",
        provider=MagicMock(return_value=None),
        anchor=_anchor(),
    )

    assert result is False
    controller._replace_stack_content.assert_not_called()
    controller._folder_stack_window.show_all.assert_not_called()


def test_show_stack_closes_the_previous_stack_before_taking_over_the_popup():
    controller = _open_controller()
    content = StackContent(entries=(_entry("first"),))
    calls: list[str] = []
    controller._close_stack = MagicMock(
        side_effect=lambda: calls.append("close_previous")
    )
    controller._runtime.hide_hover_ui.side_effect = lambda: calls.append("hide_hover")
    controller._runtime.menu_popup_opened.side_effect = lambda: calls.append("popup")

    result = controller.show_stack(
        owner_id="applet://other",
        provider=MagicMock(return_value=content),
        anchor=_anchor(),
        on_closed=MagicMock(),
    )

    assert result is True
    assert calls == ["close_previous", "hide_hover", "popup"]
    assert controller._stack_owner_id == "applet://other"
    assert controller._stack_content is content
    assert controller._folder_stack_anchor_x == 10
    assert controller._folder_stack_anchor_y == 20
    controller._replace_stack_content.assert_called_once_with(content=content)
    controller._folder_stack_revealer.set_reveal_child.assert_called_once_with(True)
    controller._folder_stack_window.show_all.assert_called_once()


def test_open_owner_id_reports_only_a_visible_stack():
    controller = _controller()

    assert controller.open_owner_id() is None

    controller._folder_stack_window = MagicMock()
    controller._folder_stack_window.get_visible.return_value = False
    controller._stack_owner_id = "applet://devices"
    assert controller.open_owner_id() is None

    controller._folder_stack_window.get_visible.return_value = True
    assert controller.open_owner_id() == "applet://devices"


def test_prewarm_builds_a_layout_without_opening_a_window():
    controller = _controller()
    content = StackContent(entries=(_entry("first"),))
    provider = MagicMock(return_value=content)
    controller._stack_layout = MagicMock()

    controller.prewarm(owner_id="applet://devices", provider=provider)

    controller._stack_layout.assert_called_once_with(
        owner_id="applet://devices", content=content
    )
    assert controller._folder_stack_window is None

    # A provider with nothing to show must not warm an empty layout.
    controller._stack_layout.reset_mock()
    controller.prewarm(
        owner_id="applet://devices", provider=MagicMock(return_value=None)
    )
    controller._stack_layout.assert_not_called()


def test_refresh_early_outs_leave_the_stack_untouched():
    controller = _open_controller()
    provider = MagicMock()
    controller._stack_provider = provider

    # A refresh aimed at a different owner is not ours to service.
    assert controller.refresh(owner_id="applet://other") is False
    provider.assert_not_called()

    # Nothing is open yet.
    controller._stack_provider = None
    assert controller.refresh() is False
    assert controller.refresh(owner_id="applet://devices") is False

    # The provider exists but the popup is hidden.
    controller._folder_stack_window.get_visible.return_value = False
    controller._stack_provider = provider
    assert controller.refresh() is False
    provider.assert_not_called()


def test_refresh_closes_the_stack_when_content_disappears():
    controller = _open_controller()
    controller._stack_provider = MagicMock(return_value=None)
    controller.close = MagicMock()

    assert controller.refresh() is False

    controller.close.assert_called_once()
    controller._replace_stack_content.assert_not_called()


def test_close_on_a_hidden_stack_is_a_no_op():
    controller = _controller()
    on_closed = MagicMock()
    controller._stack_closed = on_closed
    window = MagicMock()
    window.get_visible.return_value = False
    controller._folder_stack_window = window

    controller.close()

    window.hide.assert_not_called()
    controller._runtime.menu_popup_closed.assert_not_called()
    on_closed.assert_not_called()


def test_close_hides_notifies_and_reports_closure_once():
    controller = _open_controller()
    on_closed = MagicMock()
    controller._stack_closed = on_closed
    window = controller._folder_stack_window

    controller.close()
    controller.close()

    controller._folder_stack_revealer.set_reveal_child.assert_called_once_with(False)
    window.hide.assert_called_once()
    controller._runtime.menu_popup_closed.assert_called_once()
    on_closed.assert_called_once()
    assert controller._stack_owner_id is None
    assert controller._stack_provider is None
    assert controller._stack_content is None


def test_cleanup_stops_the_running_animation(monkeypatch):
    removed: list[int] = []
    monkeypatch.setattr(stack_mod.GLib, "source_remove", removed.append)
    controller = _open_controller()
    controller._folder_stack_anim_source = 4242

    controller._cleanup_stack()

    assert removed == [4242]
    assert controller._folder_stack_anim_source == 0


def test_activate_stack_key_ignores_unknown_keys():
    controller = _open_controller()
    controller._stack_content = StackContent(entries=(_entry("first"),))
    controller._close_stack = MagicMock()

    controller._activate_stack_key("absent")

    controller._close_stack.assert_not_called()


def test_activate_stack_key_closes_even_when_the_callback_raises():
    controller = _open_controller()
    entry = _entry("first")
    entry.activate.side_effect = RuntimeError("launch failed")
    controller._stack_content = StackContent(entries=(entry,))
    controller._close_stack = MagicMock()

    controller._activate_stack_key("first")

    entry.activate.assert_called_once()
    controller._close_stack.assert_called_once()


@pytest.mark.skipif(not stack_mod.Gtk.init_check()[0], reason="GTK display required")
def test_ensure_stack_window_builds_the_popup_once():
    """The popup is created lazily and then reused for every later stack."""
    parent = stack_mod.Gtk.Window()
    controller = StackPopupController(
        config=SimpleNamespace(icon_size=48, pos="bottom"),
        runtime=MagicMock(),
        dock_window=parent,
    )

    try:
        window = controller._ensure_stack_window()

        assert window.get_window_type() is stack_mod.Gtk.WindowType.POPUP
        assert window.get_type_hint() is stack_mod.Gdk.WindowTypeHint.TOOLTIP
        # Wayland compositors need a transient parent to place the popup
        # relative to the dock instead of at the origin.
        assert window.get_transient_for() is parent
        assert window is controller._ensure_stack_window()

        revealer = controller._folder_stack_revealer
        assert revealer is not None
        assert revealer.get_reveal_child() is False
    finally:
        window = controller._folder_stack_window
        if window is not None:
            window.destroy()
        parent.destroy()
