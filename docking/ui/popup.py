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

"""Shared popup anchoring types for dock-owned secondary UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.core.position import Position
from docking.ui.display import clamp_popup
from docking.ui.tooltip import compute_tooltip_position


@dataclass(frozen=True, slots=True)
class PopupAnchor:
    """Screen-space anchor for a popup tied to the dock or an item."""

    x: int
    y: int
    position: Position
    parent: Gtk.Window | None = None


class PopupAnchorProvider(Protocol):
    """Provider for the current dock-level popup anchor."""

    def popup_anchor(self) -> PopupAnchor | None:
        """Return the current popup anchor, or None when unavailable."""


def position_popup_near_anchor(
    *,
    window: Gtk.Window,
    anchor: PopupAnchor,
    gap_px: int,
) -> None:
    """Position a popup near an anchor, clamped to the current screen."""
    pref = window.get_preferred_size()[1]
    popup_w = max(pref.width, 1)
    popup_h = max(pref.height, 1)
    popup_x, popup_y = compute_tooltip_position(
        pos=anchor.position,
        anchor_x=anchor.x,
        anchor_y=anchor.y,
        tooltip_w=popup_w,
        tooltip_h=popup_h,
        gap=gap_px,
    )
    clamped = clamp_popup(window, popup_x, popup_y, popup_w, popup_h)
    window.move(clamped.x, clamped.y)
