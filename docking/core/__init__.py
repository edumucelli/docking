"""Convenience exports for Docking's core domain layer.

What "core" means in this project

The ``core`` package contains runtime data structures and pure-ish helpers that
should make sense without GTK windows, X11 properties, or desktop-environment
APIs. In practice that includes configuration parsing, item models, theme data,
and the layout functions that decide where icons want to be.

Why re-export from here

A thin re-export module gives tests and higher-level code a stable import point
for the most common core types without forcing every caller to remember the
exact source file. That keeps imports readable while preserving the actual
implementation split across dedicated modules.

What should not leak into this layer

Platform integration, pointer tracking, GTK widgets, and rendering details do
not belong here. When those concerns appear in ``core``, the architectural
boundary becomes harder to reason about and the pure layout/config code becomes
less testable.
"""

from docking.core.config import Config  # noqa: F401
from docking.core.items import DockItem  # noqa: F401
from docking.core.layout import (  # noqa: F401
    NO_CURSOR_SENTINEL,
    OFFSET_PCT_SNAP,
    LayoutItem,
    compute_layout,
    content_bounds,
)
from docking.core.theme import RGB, RGBA, Theme  # noqa: F401
