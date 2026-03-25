"""Dock interaction policy shared across raw events, menus, previews, and hover.

Why this module exists

The raw GTK events that hit the dock are lower-level than the behavior users
actually expect. A dock should react to concepts such as:

- "the pointer effectively entered the dock",
- "the pointer effectively left the dock",
- "a menu is open so the dock must stay alive",
- "a preview is visible, so leaving the icon does not immediately mean hide".

Those concepts cannot live comfortably in raw `enter-notify`, `leave-notify`,
and `motion-notify` handlers because the event stream alone is not enough.
The dock must reconcile events with geometry, autohide policy, previews, and
temporary UI state.

This module is that policy layer.

What this module owns

This coordinator owns:

- the public notion of `dock_hovered`,
- effective enter handling,
- effective leave handling,
- menu popup open/close policy,
- "is the pointer inside the current dock input region?" checks,
- leave behavior when previews are visible,
- "keep cursor identity" rules used for smooth hide animations.

This module does not own:

- raw GTK signal registration,
- geometry building,
- autohide animation math,
- tooltip rendering,
- preview rendering.

Think of it as the translation layer between:

    raw event stream
          +
    current dock frame
          +
    transient UI state
          =
    interaction decisions

The distinction between raw and effective enter/leave

GTK can report events that are technically true for the window but not true for
the dock as the user experiences it. For example:

- the pointer enters the GTK window but is still above the actual dock band,
- a popup or grab creates a crossing event,
- the pointer leaves an icon while a preview is still the intended target.

So the dock uses the idea of effective enter/leave:

    raw event
      |
      +-- inside current dock input region? -- no --> ignore as non-dock event
      |
      +-- yes --> effective enter/leave policy runs

This is how geometry and interaction are kept aligned. The dock should not hide
or show based on widget boundaries that do not match the actual active band.

Menu policy

Menus are special because the user is still actively interacting with the dock,
but the pointer may no longer be physically inside the dock's input region.
While a dock context menu is open:

- autohide is disabled,
- the dock remains available,
- closing the menu re-checks whether the pointer is back inside the dock.

That re-check is important:

    menu closes
      |
      +-- pointer inside dock --> remain effectively hovered
      |
      +-- pointer outside dock
            |
            +-- preview visible? schedule preview hide first
            +-- otherwise allow autohide leave

Preview-aware leave policy

Preview popups are intentionally not treated like ordinary tooltips. They are
the continuation of the same interaction. The intended user flow is:

    hover item
      |
      +-- preview appears
      |
      +-- pointer may move from dock toward preview

If leaving the dock immediately triggered autohide, the preview would become
unreachable or the dock would flicker underneath it. So this module applies the
rule:

    preview visible => schedule preview hide, do not immediately autohide

That creates one temporary interaction region:

    [ dock ] <---- intended movement ----> [ preview ]

ASCII view:

    pointer path
         \\
          \\        +-----------+
           +-----> | preview   |
    +-----------+  +-----------+
    | dock      |
    +-----------+

Leaving the dock while the preview is visible means:
"the user may be transitioning to the preview", not "interaction is over".

Keep-cursor policy

On leave, the dock sometimes preserves hover identity and cursor state briefly
instead of clearing them immediately. That sounds odd until you consider hide
animation:

    immediate clear on leave:
      icon snap -> then dock hides

    preserved cursor during hide:
      icon stays visually stable -> dock glides away

This coordinator decides when that preservation is appropriate:

- autohide enabled -> preserve during hide
- preview visible  -> preserve while preview policy is active
- neither          -> clear immediately

That is the purpose of `should_keep_cursor_on_leave(...)`.

What "pointer inside dock" really means

The dock does not use the full GTK window as its hover authority. It uses the
current input frame:

    _current_geometry_frame or _applied_input_frame

The reason for the fallback is practical:

- `_current_geometry_frame`
  freshest frame from the current event/draw cycle

- `_applied_input_frame`
  last frame that was actually installed as the live input mask

If the freshest frame is not available yet, the interaction layer can still ask
the correct question:

    "Would the active dock input region consider this pointer inside?"

That is a much better test than widget-level enter/leave.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.ui.geometry import current_input_rect, point_inside_input_rect
from docking.ui.runtime import get_pointer_position

if TYPE_CHECKING:
    from gi.repository import Gtk

    from docking.ui.dock_window import DockWindow

_log = get_logger(name="interaction")


def should_keep_cursor_on_leave(
    *, autohide_enabled: bool, preview_visible: bool
) -> bool:
    """Whether leave handling should preserve cursor/hover identity."""
    return autohide_enabled or preview_visible


class DockInteractionCoordinator:
    """Owns dock-hover state and effective enter/leave policy."""

    def __init__(self, window: DockWindow) -> None:
        self._window = window

    @property
    def dock_hovered(self) -> bool:
        return self._window.dock_hovered

    @dock_hovered.setter
    def dock_hovered(self, value: bool) -> None:
        self._window.dock_hovered = value

    def menu_popup_opened(self) -> None:
        """Track that a dock context menu popup is currently active."""
        self._window._menu_popup_visible = True
        if self._window.autohide and self._window.autohide.enabled:
            self._window.autohide.set_disabled(True, reason="menu-open")

    def menu_popup_closed(self) -> None:
        """Reconcile autohide state when the context menu closes."""
        if not self._window._menu_popup_visible:
            return
        self._window._menu_popup_visible = False

        if not self._window.autohide or not self._window.autohide.enabled:
            return
        pointer_inside = self.pointer_inside_input_rect()
        if pointer_inside:
            self._window.autohide.set_hovered(True)
            self._window.autohide.set_disabled(
                False, reason="menu-close-pointer-inside"
            )
            return

        self._window._hover.hovered_item = None
        self._window._hover.cancel()
        self._window.tooltip.hide()

        preview_visible = bool(
            self._window.preview and self._window.preview.get_visible()
        )
        if self._window.preview and preview_visible:
            self._window.preview.schedule_hide()

        self._window.update_input_region()
        self._window.drawing_area.queue_draw()
        self._window.autohide.set_hovered(False)
        self._window.autohide.set_disabled(False, reason="menu-close-pointer-outside")
        if not preview_visible:
            self._window.autohide.on_mouse_leave()

    def pointer_inside_input_rect(self) -> bool:
        """Return True when pointer is inside current dock input region."""
        frame = (
            self._window._current_geometry_frame or self._window._applied_input_frame
        )
        input_rect = current_input_rect(frame)
        if input_rect is None or not self._window.get_realized():
            return False
        display = self._window.get_display()
        if not display:
            return False
        pos = get_pointer_position(display)
        if pos is None:
            return False
        try:
            screen_x, screen_y = pos
            win_x, win_y = self._window.get_position()
        except Exception as exc:
            _log.debug(
                "Failed to query pointer/window position for dock hit test: %s",
                exc,
            )
            return False

        local_x = screen_x - win_x
        local_y = screen_y - win_y
        return point_inside_input_rect(frame, x=local_x, y=local_y)

    def on_effective_enter(self) -> None:
        if self._window.dock_hovered:
            return
        self._window.dock_hovered = True
        self._window.zoom_animator.on_enter()
        if self._window.autohide:
            self._window.autohide.on_mouse_enter()

    def on_effective_leave(self, widget: Gtk.DrawingArea) -> None:
        self._window.zoom_animator.on_leave()
        preview_visible = bool(
            self._window.preview and self._window.preview.get_visible()
        )
        if self._window.preview and preview_visible:
            self._window.preview.schedule_hide()

        autohide_on = bool(self._window.autohide and self._window.autohide.enabled)
        hovered_before = (
            self._window._hover.hovered_item.desktop_id
            if self._window._hover.hovered_item
            else "-"
        )
        keep_cursor = should_keep_cursor_on_leave(
            autohide_enabled=autohide_on,
            preview_visible=preview_visible,
        )
        self._window.dock_hovered = False
        if not keep_cursor:
            self._window._hover.hovered_item = None
            self._window.cursor_x = -1.0
            self._window.cursor_y = -1.0

        _log.debug(
            (
                "leave-policy: hovered_before=%s keep_cursor=%s "
                "preview_visible=%s autohide=%s hovered_after=%s "
                "cursor=(%.0f,%.0f)"
            ),
            hovered_before,
            keep_cursor,
            preview_visible,
            autohide_on,
            (
                self._window._hover.hovered_item.desktop_id
                if self._window._hover.hovered_item
                else "-"
            ),
            self._window.cursor_x,
            self._window.cursor_y,
        )

        self._window._hover.cancel()
        self._window.tooltip.hide()
        self._window.update_input_region()
        widget.queue_draw()
        if autohide_on and self._window.autohide and not preview_visible:
            self._window.autohide.on_mouse_leave()

    def is_pointer_inside_dock(self) -> bool:
        """Return True when the current pointer is inside the dock input area."""
        return self.pointer_inside_input_rect()

    def point_inside_event_frame(self, *, x: float, y: float) -> bool:
        frame = (
            self._window._current_geometry_frame or self._window._applied_input_frame
        )
        input_rect = current_input_rect(frame)
        if input_rect is None:
            return False
        return input_rect.contains(x, y)
