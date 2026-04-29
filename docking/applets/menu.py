"""Shared GTK menu helpers for applets."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

T = TypeVar("T")


def radio_menu_items(
    *,
    choices: Iterable[tuple[str, T]],
    active_value: T,
    on_selected: Callable[[Gtk.RadioMenuItem, T], None],
    is_active: Callable[[T], bool] | None = None,
    gtk=Gtk,
) -> list[Gtk.RadioMenuItem]:
    """Build one radio group from ``(label, value)`` menu choices."""
    items: list[Gtk.RadioMenuItem] = []
    group_head: Gtk.RadioMenuItem | None = None
    for label, value in choices:
        item = gtk.RadioMenuItem(label=label)
        if group_head is None:
            group_head = item
        else:
            item.join_group(group_head)
        item.set_active(is_active(value) if is_active else value == active_value)
        item.connect(
            "toggled",
            lambda widget, selected=value: _on_radio_toggled(
                widget=widget,
                value=selected,
                on_selected=on_selected,
            ),
        )
        items.append(item)
    return items


def radio_submenu(
    *,
    label: str,
    choices: Iterable[tuple[str, T]],
    active_value: T,
    on_selected: Callable[[Gtk.RadioMenuItem, T], None],
    is_active: Callable[[T], bool] | None = None,
    gtk=Gtk,
) -> Gtk.MenuItem:
    """Build a menu item whose submenu is one exclusive radio group."""
    root = gtk.MenuItem(label=label)
    menu = gtk.Menu()
    for item in radio_menu_items(
        choices=choices,
        active_value=active_value,
        on_selected=on_selected,
        is_active=is_active,
        gtk=gtk,
    ):
        menu.append(item)
    root.set_submenu(menu)
    return root


def _on_radio_toggled(
    *,
    widget: Gtk.RadioMenuItem,
    value: T,
    on_selected: Callable[[Gtk.RadioMenuItem, T], None],
) -> None:
    if not widget.get_active():
        return
    on_selected(widget, value)
