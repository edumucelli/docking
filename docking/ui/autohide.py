"""Autohide controller for the dock's visible/hidden state and motion.

Autohide from first principles

An edge dock has two conflicting jobs:

1. stay out of the way when the user is not using it,
2. appear quickly and predictably when the user approaches it.

If the dock hides too aggressively, it flickers when the pointer briefly arcs
out and back in. If it shows too aggressively, it fights menus, drags, previews,
or other temporary UI that should keep it present. If animation reversals are
not continuous, the dock appears to jump instead of glide.

This module exists to keep those concerns in one state machine.

What this module owns

This controller owns:

- whether the dock is logically visible, hiding, hidden, or showing,
- hide/show delays,
- the animation progress for transitions,
- the current hide offset used by the renderer and geometry,
- the current zoom progress while hidden or showing,
- policy inputs for:
  - pointer hovered/not hovered,
  - temporarily disabled/not disabled,
  - window_should_hide (from WindowDodgeMonitor for dodge modes).

Hide modes
----------
The controller supports six modes (see HideMode in config.py). Two inputs
drive the decision:

- AUTOHIDE: hide whenever not hovered (mouse-only).
- Dodge modes (INTELLIGENT, DODGE_ACTIVE, WINDOW_DODGE, DODGE_MAXIMIZED):
  hide only when ``window_should_hide`` is True (set by WindowDodgeMonitor).
  Hover or disabled always override to show.

This module does not own:

- the geometry of the dock,
- how hover is determined,
- menu logic,
- preview logic,
- tooltip logic,
- GTK event handling.

Those systems tell autohide whether the dock should currently be considered
"held open" or not. Autohide translates that into smooth state transitions.

The four states

The dock has four visible states:

    VISIBLE  -> fully shown, idle, ready to hide
    HIDING   -> moving from shown to hidden
    HIDDEN   -> collapsed to edge trigger only
    SHOWING  -> moving from hidden to shown

The usual path is:

    pointer enters
        V
    HIDDEN --> SHOWING --> VISIBLE

    pointer leaves
        V
    VISIBLE --> HIDING --> HIDDEN

But real use is more complicated. The user often changes their mind mid-flight:

    VISIBLE --> HIDING -- pointer returns --> SHOWING --> VISIBLE

That reversal must be continuous. The dock should resume from its current
position, not restart from a fully hidden or fully visible endpoint.

Animation outputs

Two outputs matter to the rest of the dock:

- hide_offset
  0.0 means fully visible
  1.0 means fully hidden

- zoom_progress
  1.0 means normal zoom behavior is fully active
  0.0 means no zoom while fully hidden

The renderer and geometry read these values to decide:

- how far the dock is shifted off-screen,
- whether the active input region is the full dock band or the thin trigger,
- how much hover zoom should still be visible during transitions.

Why disabled exists

Hovered alone is not enough. There are periods where the pointer may not be
strictly "on the dock", but the dock still must not hide:

- a context menu is open,
- a drag operation is active,
- another interaction temporarily owns pointer flow.

That is what disabled means:

    effective_should_show = hovered or disabled

When disabled is true, autohide behaves as if the dock is being actively held
open by a transient UI policy rather than by raw pointer presence.

Timer model

Hide/show changes are not always immediate. The controller can use:

- hide delay
- unhide delay
- animation ticks

There is also a small minimum hide grace used to absorb fast pointer arcs:

    pointer leaves briefly
         |
         +-- if it returns quickly, do not visibly start hiding

Without that grace, the dock flickers on short U-shaped movements that are not
meaningful leaves from the user's perspective.

Timeline example:

    t0   pointer leaves
    t1   hide grace expires
    t2   HIDING begins
    t3   pointer re-enters
    t4   SHOWING begins from current hide_offset

Not:

    t3   pointer re-enters
    t4   dock jumps to "almost hidden"
    t5   dock restarts show from the wrong baseline

That incorrect jump was a real bug fixed by preserving continuity when the
direction changes mid-animation.

Easing model

The dock uses cubic easing in each direction:

    hiding  -> ease_in_cubic
    showing -> ease_out_cubic

This produces:

- gentle start when hiding,
- quick recovery when showing,
- more natural motion than linear interpolation.

The inverse easing helpers matter because reversal continuity needs to answer:

    "Given the dock is already 37% hidden, what progress value would produce
    exactly that visual position if we now reverse direction?"

That is why this module has both:

- ease_* functions
- inverse_ease_* functions

Without the inverse functions, reversal code would restart the opposite
animation from progress 0.0 and the dock would visibly teleport.

Operational model

Consumers do not tell the controller "hide now" and "show now" directly.
They normally report higher-level facts:

- set_hovered(True/False)
- set_disabled(True/False)

The controller then reconciles those facts into motion.

That separation is important. It keeps timing and state ownership here instead
of spreading "should I hide immediately?" decisions across menu, drag, hover,
and event code.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import gi

from docking.core.config import HideMode
from docking.log import get_logger

gi.require_version("Gtk", "3.0")
from gi.repository import GLib

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.ui.dock_window import DockWindow

log = get_logger(name="autohide")

FRAME_INTERVAL_MS = 16  # ~60fps
MIN_HIDE_GRACE_MS = 60


class HideState(enum.Enum):
    VISIBLE = "visible"
    HIDING = "hiding"
    HIDDEN = "hidden"
    SHOWING = "showing"


def ease_in_cubic(t: float) -> float:
    """Cubic ease-in: slow start, accelerating."""
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, decelerating."""
    return 1.0 - (1.0 - t) ** 3


def inverse_ease_in_cubic(value: float) -> float:
    """Return t such that ease_in_cubic(t) ~= value."""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value ** (1.0 / 3.0)


def inverse_ease_out_cubic(value: float) -> float:
    """Return t such that ease_out_cubic(t) ~= value."""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return 1.0 - (1.0 - value) ** (1.0 / 3.0)


def _source_exists(source_id: int) -> bool:
    """Return True when a GLib source id is still active."""
    if source_id <= 0:
        return False
    try:
        ctx = GLib.MainContext.default()
        return bool(ctx and ctx.find_source_by_id(source_id))
    except Exception as exc:
        log.debug(f"Could not query GLib source id {source_id}: {exc}")
        # If runtime doesn't expose the check, fall back to best effort.
        return True


def _clear_source(source_id: int) -> int:
    """Safely remove a GLib source if it still exists and return zero id."""
    if _source_exists(source_id=source_id):
        GLib.source_remove(source_id)
    return 0


class AutoHideController:
    """Manages dock hide/show animation with configurable delays."""

    def __init__(self, window: DockWindow, config: Config) -> None:
        self._window = window
        self._config = config
        self.state = HideState.VISIBLE
        self.hide_offset: float = 0.0  # 0.0 = fully visible, 1.0 = fully hidden
        self.zoom_progress: float = 0.0  # 0.0 = no zoom, 1.0 = full zoom
        self._hovered: bool = False
        self._disabled: bool = False

        self._hide_timer_id: int = 0
        self._unhide_timer_id: int = 0
        self._anim_timer_id: int = 0
        self._anim_progress: float = 0.0
        self._hide_after_show: bool = False
        self._window_should_hide: bool = False

    @property
    def enabled(self) -> bool:
        return self._config.hide_mode not in ("none", "always-on-top")

    def reset(self) -> None:
        """Force dock visible -- call when auto-hide is toggled off."""
        self._cancel_hide_timer()
        self._cancel_unhide_timer()
        if self._anim_timer_id:
            self._anim_timer_id = _clear_source(source_id=self._anim_timer_id)
        self.state = HideState.VISIBLE
        self.hide_offset = 0.0
        self.zoom_progress = 0.0
        self._hovered = False
        self._disabled = False
        self._hide_after_show = False
        self._window_should_hide = False
        self._window.queue_redraw()

    def on_mouse_leave(self) -> None:
        """Called when mouse leaves the dock area."""
        self.set_hovered(hovered=False)

    def on_mouse_enter(self) -> None:
        """Called when mouse enters the dock area."""
        self.set_hovered(hovered=True)

    def set_hovered(self, hovered: bool) -> None:
        """Update dock hover state and reconcile hide/show policy."""
        if not self.enabled:
            return
        if self._hovered == hovered:
            return
        self._hovered = hovered
        log.debug(
            "set_hovered: hovered=%s disabled=%s state=%s",
            hovered,
            self._disabled,
            self.state.value,
        )
        self._update_hidden()

    def set_disabled(self, disabled: bool, *, reason: str = "unknown") -> None:
        """Disable or enable autohide reactions while menus/drags are active."""
        if not self.enabled:
            return
        if self._disabled == disabled:
            return
        self._disabled = disabled
        log.debug(
            "set_disabled: hovered=%s disabled=%s state=%s reason=%s",
            self._hovered,
            disabled,
            self.state.value,
            reason,
        )
        self._update_hidden()

    def set_window_should_hide(self, should_hide: bool) -> None:
        if self._window_should_hide == should_hide:
            return
        self._window_should_hide = should_hide
        self._update_hidden()

    def reconcile(self) -> None:
        """Re-run hide/show policy without changing current inputs."""
        if not self.enabled:
            return
        self._update_hidden()

    def _update_hidden(self) -> None:
        """Reconcile hover/disabled state into show-or-hide behavior."""
        if self._disabled or self._hovered:
            self._show()
        elif (
            self._config.hide_mode == HideMode.AUTOHIDE.value
            or self._window_should_hide
        ):
            self._hide()
        else:
            self._show()

    def _show(self) -> None:
        self.zoom_progress = 1.0
        self._cancel_hide_timer()
        self._hide_after_show = False
        if self.state in (HideState.HIDDEN, HideState.HIDING):
            delay = self._config.unhide_delay_ms
            if delay <= 0:
                self._start_showing()
            else:
                self._unhide_timer_id = GLib.timeout_add(delay, self._start_showing)

    def _hide(self) -> None:
        self._cancel_unhide_timer()

        if self.state == HideState.SHOWING:
            self._hide_after_show = True
            return

        if self.state == HideState.VISIBLE:
            self._schedule_hide()

    def _start_hiding(self) -> bool:
        """Begin hide animation."""
        self._hide_timer_id = 0
        self._hide_after_show = False
        self.state = HideState.HIDING
        if self.hide_offset > 0.0:
            self._anim_progress = inverse_ease_in_cubic(self.hide_offset)
        else:
            self._anim_progress = 0.0
        self._start_animation()
        return False

    def _start_showing(self) -> bool:
        """Begin show animation."""
        self._unhide_timer_id = 0
        if self.hide_offset <= 0.0:
            # Already fully visible - skip animation entirely.
            self.state = HideState.VISIBLE
            self.hide_offset = 0.0
            self.zoom_progress = 1.0
            self._window.queue_redraw()
            return False
        self.state = HideState.SHOWING
        self._anim_progress = inverse_ease_out_cubic(1.0 - self.hide_offset)
        self._start_animation()
        return False

    def _schedule_hide(self) -> None:
        delay = self._config.hide_delay_ms
        if delay <= 0 and self.state == HideState.VISIBLE:
            delay = MIN_HIDE_GRACE_MS

        if delay <= 0:
            self._start_hiding()
        else:
            self._hide_timer_id = GLib.timeout_add(delay, self._start_hiding)

    def _start_animation(self) -> None:
        """Start the animation tick loop."""
        if self._anim_timer_id:
            self._anim_timer_id = _clear_source(source_id=self._anim_timer_id)
        self._anim_timer_id = GLib.timeout_add(FRAME_INTERVAL_MS, self._animation_tick)

    # Autohide state machine:
    #
    #   ┌─────────┐  mouse   ┌────────┐  anim    ┌────────┐
    #   │ VISIBLE │──leave──->│ HIDING │──done───->│ HIDDEN │
    #   └─────────┘          └────────┘          └────────┘
    #       ^                                        │
    #       │                ┌─────────┐   mouse     │
    #       └───anim done────│ SHOWING │<-──enter─────┘
    #                        └─────────┘
    #
    # HIDING:  hide_offset animates 0->1 using ease_in_cubic (accelerating)
    #          zoom_progress decays in parallel
    # SHOWING: hide_offset animates 1->0 using ease_out_cubic (decelerating)
    # VISIBLE/HIDDEN: stable states, no animation running
    #
    # Each animation frame advances _anim_progress by a fixed step
    # (FRAME_INTERVAL_MS / hide_time_ms), giving consistent timing
    # regardless of how many frames actually render.

    def _animation_tick(self) -> bool:
        """Single animation frame."""
        duration = self._config.hide_time_ms
        step = FRAME_INTERVAL_MS / duration if duration > 0 else 1.0
        self._anim_progress = min(1.0, self._anim_progress + step)

        if self.state == HideState.HIDING:
            self.hide_offset = ease_in_cubic(t=self._anim_progress)
            # Zoom progress decay -- smooth zoom fadeout during hide.
            #
            # As the dock slides down (hide_offset goes 0.0 -> 1.0), we
            # simultaneously decay the zoom effect. The formula:
            #   zoom_progress *= (1.0 - hide_offset)
            #
            # This is a multiplicative decay that couples zoom to the
            # hide animation. Early in the hide (hide_offset ≈ 0.1),
            # zoom_progress drops by ~10%. Late in the hide
            # (hide_offset ≈ 0.9), it drops rapidly toward zero.
            #
            # The visual effect: icons gradually shrink back to their
            # rest size AS the dock slides away, rather than snapping
            # to unzoomed before the slide starts.
            # Plank's formula: direct linear decay, not compounding.
            # zoom_in_progress = zoom_progress * (1 - hide_progress)
            # We keep zoom_progress at its initial value (set to 1.0 on
            # mouse_enter) and let the renderer apply the decay.
            self.zoom_progress = 1.0 - self.hide_offset
            if self._anim_progress >= 1.0:
                self.state = HideState.HIDDEN
                self.hide_offset = 1.0
                self.zoom_progress = 0.0
                self._anim_timer_id = 0
                self._window.queue_redraw()
                return False

        elif self.state == HideState.SHOWING:
            self.hide_offset = 1.0 - ease_out_cubic(t=self._anim_progress)
            self.zoom_progress = 1.0 - self.hide_offset
            if self._anim_progress >= 1.0:
                self.state = HideState.VISIBLE
                self.hide_offset = 0.0
                self._anim_timer_id = 0
                if self._hide_after_show:
                    self._hide_after_show = False
                    self._schedule_hide()
                self._window.queue_redraw()
                return False

        self._window.queue_redraw()
        return True

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer_id:
            self._hide_timer_id = _clear_source(source_id=self._hide_timer_id)

    def _cancel_unhide_timer(self) -> None:
        if self._unhide_timer_id:
            self._unhide_timer_id = _clear_source(source_id=self._unhide_timer_id)
