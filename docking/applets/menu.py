# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Shared GTK menu helpers for applets."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

T = TypeVar("T")


def disabled_menu_item(label: str, *, gtk=Gtk) -> Gtk.MenuItem:
    """Build a non-interactive status/header row."""
    item = gtk.MenuItem(label=label)
    item.set_sensitive(False)
    return item


def menu_sections(
    *,
    status: Iterable[Gtk.MenuItem] = (),
    primary: Iterable[Gtk.MenuItem] = (),
    navigation: Iterable[Gtk.MenuItem] = (),
    refresh: Iterable[Gtk.MenuItem] = (),
    display: Iterable[Gtk.MenuItem] = (),
    manage: Iterable[Gtk.MenuItem] = (),
    destructive: Iterable[Gtk.MenuItem] = (),
    settings: Iterable[Gtk.MenuItem] = (),
    gtk=Gtk,
) -> list[Gtk.MenuItem]:
    """Return menu items in the standard applet section order.

    Order is status/header, primary open/run actions, navigation, refresh,
    display controls, manage/add actions, destructive actions, then settings.
    Separators are inserted only between non-empty sections.
    """
    ordered_sections = (
        tuple(status),
        tuple(primary),
        tuple(navigation),
        tuple(refresh),
        tuple(display),
        tuple(manage),
        tuple(destructive),
        tuple(settings),
    )
    visible_sections = [section for section in ordered_sections if section]
    separator_menu_item = getattr(gtk, "SeparatorMenuItem", Gtk.SeparatorMenuItem)
    items: list[Gtk.MenuItem] = []
    for index, section in enumerate(visible_sections):
        if index > 0:
            items.append(separator_menu_item())
        items.extend(section)
    return items


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
