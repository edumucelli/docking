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

"""Rendering for workspaces applet icon."""

from __future__ import annotations

import cairo


def _render_grid(cr: cairo.Context, size: int, count: int, active_num: int) -> None:
    """Draw a grid of workspace rectangles with the active one highlighted."""
    if count <= 0:
        return

    # Grid layout: prefer 2xN
    cols = 2 if count > 1 else 1
    rows = (count + cols - 1) // cols

    margin = size * 0.12
    gap = size * 0.06
    grid_w = size - 2 * margin
    grid_h = size - 2 * margin
    cell_w = (grid_w - (cols - 1) * gap) / cols
    cell_h = (grid_h - (rows - 1) * gap) / rows
    radius = size * 0.04

    for idx in range(count):
        row = idx // cols
        col = idx % cols
        x = margin + col * (cell_w + gap)
        y = margin + row * (cell_h + gap)

        # Rounded rectangle
        cr.new_sub_path()
        cr.arc(x + cell_w - radius, y + radius, radius, -1.5708, 0)
        cr.arc(x + cell_w - radius, y + cell_h - radius, radius, 0, 1.5708)
        cr.arc(x + radius, y + cell_h - radius, radius, 1.5708, 3.1416)
        cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
        cr.close_path()

        if idx == active_num:
            cr.set_source_rgba(0.3, 0.6, 1.0, 0.9)
        else:
            cr.set_source_rgba(0.7, 0.7, 0.7, 0.5)
        cr.fill()
