"""Tests for the reusable curved item-stack popup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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
