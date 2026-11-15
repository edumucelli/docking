"""Cairo rendering helpers for the News RSS applet."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.reader_tile import render_reader_tile


def render_icon(
    *,
    size: int,
    index: int = 0,
    count: int = 0,
    loading: bool = False,
    error: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render a neutral news-reader tile with headline position."""
    return render_reader_tile(
        size=size,
        index=index,
        count=count,
        loading=loading,
        error=error,
        mark="N",
        normal_rgba=(0.1, 0.38, 0.68, 0.97),
        loading_rgba=(0.2, 0.48, 0.72, 0.76),
    )
