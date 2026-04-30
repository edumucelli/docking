"""Tests for shared applet menu helpers."""

from docking.applets.menu import (
    disabled_menu_item,
    menu_sections,
    radio_menu_items,
    radio_submenu,
)


def test_radio_menu_items_keep_active_choice_and_ignore_inactive_toggles():
    selected: list[str] = []

    items = radio_menu_items(
        choices=(("Day", "day"), ("Week", "week")),
        active_value="week",
        on_selected=lambda _widget, value: selected.append(value),
    )

    assert [item.get_label() for item in items] == ["Day", "Week"]
    assert items[0].get_active() is False
    assert items[1].get_active() is True

    callback, _args = items[0]._signals["toggled"][0]
    callback(items[0])
    assert selected == []

    items[0].set_active(True)
    callback(items[0])
    assert selected == ["day"]


def test_radio_submenu_builds_exclusive_children():
    root = radio_submenu(
        label="Mode",
        choices=(("Cost", "cost"), ("Tokens", "tokens")),
        active_value="cost",
        on_selected=lambda _widget, _value: None,
    )

    submenu = root.get_submenu()
    assert root.get_label() == "Mode"
    assert [item.get_label() for item in submenu.children] == ["Cost", "Tokens"]
    assert [item.get_active() for item in submenu.children] == [True, False]


def test_disabled_menu_item_builds_insensitive_status_row():
    item = disabled_menu_item("Status")

    assert item.get_label() == "Status"
    assert item.get_sensitive() is False


def test_menu_sections_orders_groups_and_separates_non_empty_sections():
    items = menu_sections(
        status=[disabled_menu_item("Status")],
        primary=[disabled_menu_item("Open")],
        refresh=[disabled_menu_item("Refresh")],
        display=[disabled_menu_item("Display")],
        destructive=[disabled_menu_item("Clear")],
    )

    labels = [item.get_label() for item in items if item.get_label()]
    separators = [item for item in items if not item.get_label()]

    assert labels == ["Status", "Open", "Refresh", "Display", "Clear"]
    assert len(separators) == 4


def test_menu_sections_skips_empty_groups_without_edge_separators():
    items = menu_sections(
        primary=[disabled_menu_item("Open")],
        settings=[disabled_menu_item("Settings")],
    )

    assert [item.get_label() for item in items] == ["Open", "", "Settings"]
