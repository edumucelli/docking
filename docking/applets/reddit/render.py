"""Cairo rendering helpers for the Reddit RSS applet."""

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
    """Render a source-specific RSS reader tile without external artwork."""
    return render_reader_tile(
        size=size,
        index=index,
        count=count,
        loading=loading,
        error=error,
        mark="r/",
        normal_rgba=(0.93, 0.25, 0.08, 0.97),
        loading_rgba=(0.88, 0.31, 0.09, 0.76),
    )
