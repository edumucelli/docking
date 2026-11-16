"""GTK dock hints and placement shared by independent surface backends."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from docking.platform.backends.base import PlacementRequest, Rect

if TYPE_CHECKING:
    from gi.repository import Gtk


def apply_dock_hints(window: object, *, keep_above: bool = True) -> None:
    """Apply the toolkit's common dock-window hints."""
    # Surface services keep toolkit handles opaque until the GTK boundary.
    window = cast("Gtk.Window", window)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.stick()
    apply_dock_stacking(window, keep_above=keep_above)
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except (ImportError, ValueError):
        return
    window.set_type_hint(Gdk.WindowTypeHint.DOCK)


def apply_dock_stacking(window: object, *, keep_above: bool) -> None:
    """Clear the opposite stacking hint before applying the requested one."""
    window = cast("Gtk.Window", window)
    if keep_above:
        window.set_keep_below(False)
        window.set_keep_above(True)
    else:
        window.set_keep_above(False)
        window.set_keep_below(True)


def place_window(window: object, request: PlacementRequest) -> None:
    """Apply a placement request in GTK's logical coordinates."""
    window = cast("Gtk.Window", window)
    window.set_size_request(request.size.width, request.size.height)
    window.resize(request.size.width, request.size.height)
    window.move(request.x, request.y)


def logical_screen_rectangle(window: object, rect: Rect) -> Rect | None:
    window = cast("Gtk.Window", window)
    native = window.get_window()
    if native is None or not window.get_mapped():
        return None
    success, x, y = native.get_origin()
    if not success:
        return None
    return Rect(x + rect.x, y + rect.y, rect.width, rect.height)
