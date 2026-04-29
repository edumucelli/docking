"""Tests for shared applet menu helpers."""

from docking.applets.menu import radio_menu_items, radio_submenu


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
