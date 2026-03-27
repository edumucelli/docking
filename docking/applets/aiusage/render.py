"""Pure Cairo rendering for AI usage tracker icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.aiusage.state import (
    AiUsageState,
    Provider,
    _today_entry,
    dominant_provider,
    provider_cost,
    today_cost,
)
from docking.applets.base import draw_icon_label

# Claude brand tan/orange.
_CLAUDE_R, _CLAUDE_G, _CLAUDE_B = 0.82, 0.58, 0.38

# Codex/OpenAI green-teal.
_CODEX_R, _CODEX_G, _CODEX_B = 0.29, 0.73, 0.56

# OpenCode blue-purple.
_OC_R, _OC_G, _OC_B = 0.40, 0.45, 0.90

_PETALS = 6


def _draw_claude_logo(cr: cairo.Context, size: int) -> None:
    """Draw the Claude starburst/asterisk logo."""
    cx = size / 2
    cy = size / 2
    radius = size * 0.32
    petal_w = size * 0.09
    cap_r = petal_w * 0.95

    cr.set_source_rgb(_CLAUDE_R, _CLAUDE_G, _CLAUDE_B)

    for i in range(_PETALS):
        angle = math.tau * i / _PETALS - math.pi / 2
        tx = cx + math.cos(angle) * radius
        ty = cy + math.sin(angle) * radius
        cr.set_line_width(petal_w)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(cx, cy)
        cr.line_to(tx, ty)
        cr.stroke()

    cr.arc(cx, cy, cap_r, 0, math.tau)
    cr.fill()


def _draw_codex_logo(cr: cairo.Context, size: int) -> None:
    """Draw a hexagon logo for Codex/OpenAI."""
    cx = size / 2
    cy = size / 2
    radius = size * 0.36
    sides = 6

    cr.set_source_rgb(_CODEX_R, _CODEX_G, _CODEX_B)

    for i in range(sides):
        angle = math.tau * i / sides - math.pi / 2
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius
        if i == 0:
            cr.move_to(x, y)
        else:
            cr.line_to(x, y)
    cr.close_path()
    cr.fill()

    # Inner dot.
    cr.set_source_rgb(0.12, 0.12, 0.16)
    cr.arc(cx, cy, size * 0.10, 0, math.tau)
    cr.fill()


def _draw_opencode_logo(cr: cairo.Context, size: int) -> None:
    """Draw a diamond/rhombus logo for OpenCode."""
    cx = size / 2
    cy = size / 2
    r = size * 0.34

    cr.set_source_rgb(_OC_R, _OC_G, _OC_B)
    cr.move_to(cx, cy - r)
    cr.line_to(cx + r, cy)
    cr.line_to(cx, cy + r)
    cr.line_to(cx - r, cy)
    cr.close_path()
    cr.fill()

    # Inner chevron.
    cr.set_source_rgb(0.12, 0.12, 0.16)
    s = size * 0.11
    cr.set_line_width(size * 0.05)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.move_to(cx + s * 0.6, cy - s)
    cr.line_to(cx - s * 0.6, cy)
    cr.line_to(cx + s * 0.6, cy + s)
    cr.stroke()


def render_icon(
    *,
    size: int,
    state: AiUsageState,
    selected_provider: Provider | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render icon based on selected or dominant provider."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    provider = selected_provider or dominant_provider(state=state)
    if provider == Provider.CODEX:
        _draw_codex_logo(cr=cr, size=size)
    elif provider == Provider.OPENCODE:
        _draw_opencode_logo(cr=cr, size=size)
    else:
        _draw_claude_logo(cr=cr, size=size)

    if selected_provider:
        entry = _today_entry(state=state)
        cost = provider_cost(entry=entry, provider=selected_provider) if entry else 0.0
    else:
        cost = today_cost(state=state)
    if cost > 0:
        label = f"${cost:.0f}" if cost >= 1.0 else f"${cost:.2f}"
        draw_icon_label(cr=cr, text=label, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
