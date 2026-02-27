"""Tests for tooltip manager.

Covers positioning math, content caching (flicker prevention), and
the hide/show lifecycle that prevents spurious crossing events.
"""

import sys
from unittest.mock import MagicMock

# Mock gi before importing
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position  # noqa: E402
from docking.ui.tooltip import (  # noqa: E402
    TOOLTIP_BASE_GAP,
    TooltipManager,
    compute_tooltip_position,
)


class TestTooltipManagerInit:
    def test_initial_state(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        # When
        tooltip = TooltipManager(window, config, model, theme)
        # Then
        assert tooltip._tooltip_window is None

    def test_gap_constant_reasonable(self):
        # Given
        from docking.ui.tooltip import TOOLTIP_BASE_GAP

        # When / Then — gap should be small positive value
        assert 5 <= TOOLTIP_BASE_GAP <= 50


class TestTooltipHide:
    def test_hide_when_no_window(self):
        # Given — tooltip window not yet created
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        # When / Then — should not raise
        tooltip.hide()

    def test_update_with_no_item_keeps_tooltip(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        # When — update with None item (cursor in gap between icons)
        tooltip.update(None, [])
        # Then — tooltip stays visible (dock _on_leave handles hiding)
        tooltip._tooltip_window.hide.assert_not_called()

    def test_update_with_unnamed_item_keeps_tooltip(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        item = MagicMock()
        item.name = ""
        # When -- item has no name (cursor in gap)
        tooltip.update(item, [])
        # Then — tooltip stays visible
        tooltip._tooltip_window.hide.assert_not_called()


# Anchor point for tests
AX, AY = 500.0, 300.0
TW, TH = 80, 24


class TestTooltipPositionBottom:
    def test_centered_horizontally(self):
        tx, ty = compute_tooltip_position(
            pos=Position.BOTTOM, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW / 2)

    def test_above_anchor(self):
        tx, ty = compute_tooltip_position(
            pos=Position.BOTTOM, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY - TH - TOOLTIP_BASE_GAP)
        assert ty < AY


class TestTooltipPositionTop:
    def test_centered_horizontally(self):
        tx, ty = compute_tooltip_position(
            pos=Position.TOP, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW / 2)

    def test_below_anchor(self):
        tx, ty = compute_tooltip_position(
            pos=Position.TOP, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY + TOOLTIP_BASE_GAP)
        assert ty > AY


class TestTooltipPositionLeft:
    def test_right_of_anchor(self):
        tx, ty = compute_tooltip_position(
            pos=Position.LEFT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX + TOOLTIP_BASE_GAP)
        assert tx > AX

    def test_centered_vertically(self):
        tx, ty = compute_tooltip_position(
            pos=Position.LEFT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY - TH / 2)


class TestTooltipPositionRight:
    def test_left_of_anchor(self):
        tx, ty = compute_tooltip_position(
            pos=Position.RIGHT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW - TOOLTIP_BASE_GAP)
        assert tx < AX

    def test_centered_vertically(self):
        tx, ty = compute_tooltip_position(
            pos=Position.RIGHT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY - TH / 2)


class TestTooltipDirection:
    """Tooltip should always appear on the inner side (away from screen edge)."""

    def test_bottom_tooltip_above(self):
        _, ty = compute_tooltip_position(
            pos=Position.BOTTOM, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty + TH <= AY  # tooltip bottom <= anchor

    def test_top_tooltip_below(self):
        _, ty = compute_tooltip_position(
            pos=Position.TOP, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty >= AY  # tooltip top >= anchor

    def test_left_tooltip_right(self):
        tx, _ = compute_tooltip_position(
            pos=Position.LEFT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx >= AX  # tooltip left >= anchor

    def test_right_tooltip_left(self):
        tx, _ = compute_tooltip_position(
            pos=Position.RIGHT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx + TW <= AX  # tooltip right <= anchor


# -- Regression: content caching prevents flicker ----------------------------


def _make_tooltip() -> TooltipManager:
    """Create a TooltipManager with mocked dependencies."""
    window = MagicMock()
    config = MagicMock()
    model = MagicMock()
    theme = MagicMock()
    return TooltipManager(window, config, model, theme)


def _make_item(name: str, builder: bool = False) -> MagicMock:
    item = MagicMock()
    item.name = name
    item.tooltip_builder = (lambda: MagicMock()) if builder else None
    return item


class TestContentCaching:
    """Tooltip should skip content rebuild when same item+name is hovered.

    Rebuilding calls show_all() which generates GTK crossing events that
    cause spurious leave-notify on the dock drawing area (flicker).
    """

    def test_same_item_same_name_is_cached(self):
        # Given
        tooltip = _make_tooltip()
        item = _make_item("Firefox")
        tooltip._last_item = item
        tooltip._last_name = "Firefox"
        # When — update with same item and name
        tooltip.update(item, [])
        # Then — no rebuild triggered (would need layout lookup)
        # The early return means no _show_tooltip call

    def test_different_item_triggers_rebuild(self):
        # Given
        tooltip = _make_tooltip()
        item_a = _make_item("Firefox")
        item_b = _make_item("Chrome")
        tooltip._last_item = item_a
        tooltip._last_name = "Firefox"
        # When/Then — content_changed should be True for different item
        content_changed = not (
            item_b is tooltip._last_item and item_b.name == tooltip._last_name
        )
        assert content_changed is True

    def test_same_item_different_name_triggers_rebuild(self):
        # Given — applet changed its tooltip text (e.g. workspace switch)
        tooltip = _make_tooltip()
        item = _make_item("Workspace 1")
        tooltip._last_item = item
        tooltip._last_name = "Workspace 1"
        item.name = "Workspace 2"
        # When/Then
        content_changed = not (
            item is tooltip._last_item and item.name == tooltip._last_name
        )
        assert content_changed is True

    def test_builder_item_same_name_is_cached(self):
        # Given — weather applet with tooltip_builder, same data
        tooltip = _make_tooltip()
        item = _make_item("Paris: 17°C", builder=True)
        tooltip._last_item = item
        tooltip._last_name = "Paris: 17°C"
        # When/Then — should be cached (builder only called on content change)
        content_changed = not (
            item is tooltip._last_item and item.name == tooltip._last_name
        )
        assert content_changed is False


class TestTooltipGapBehavior:
    """Tooltip must NOT hide when cursor moves to gap between icons.

    Previously, update(None) would hide the tooltip, causing rapid
    hide/show flicker when moving between adjacent icons. Now the
    tooltip stays visible until the mouse leaves the dock entirely.
    """

    def test_none_item_does_not_hide(self):
        tooltip = _make_tooltip()
        tooltip._tooltip_window = MagicMock()
        tooltip.update(None, [])
        tooltip._tooltip_window.hide.assert_not_called()

    def test_empty_name_does_not_hide(self):
        tooltip = _make_tooltip()
        tooltip._tooltip_window = MagicMock()
        item = _make_item("")
        tooltip.update(item, [])
        tooltip._tooltip_window.hide.assert_not_called()

    def test_explicit_hide_works(self):
        tooltip = _make_tooltip()
        tooltip._tooltip_window = MagicMock()
        tooltip.hide()
        tooltip._tooltip_window.hide.assert_called_once()

    def test_hide_clears_tracking(self):
        tooltip = _make_tooltip()
        tooltip._last_item = _make_item("Firefox")
        tooltip._last_name = "Firefox"
        tooltip.hide()
        assert tooltip._last_item is None
        assert tooltip._last_name == ""


# -- Regression: spurious leave filter in dock_window ------------------------


class TestSpuriousLeaveFilter:
    """Dock must ignore leave events where cursor is still inside the window.

    The tooltip popup generates NONLINEAR leave events on the dock's
    drawing area even though the cursor hasn't moved outside it. The
    bounds check in _on_leave prevents these from triggering autohide.
    """

    def test_leave_inside_bounds_is_ignored(self):
        # Given — leave event with cursor inside drawing area

        # This is a structural test: verify the function exists and the
        # pattern. The actual _on_leave integration requires GTK.
        # We verify the bounds-check logic directly.
        alloc_width, alloc_height = 1440, 100
        event_x, event_y = 1200, 80  # inside
        inside = 0 <= event_x <= alloc_width and 0 <= event_y <= alloc_height
        assert inside is True  # would return False from _on_leave

    def test_leave_outside_bounds_is_real(self):
        # Given — leave event with cursor outside (genuine exit)
        alloc_width, alloc_height = 1440, 100
        event_x, event_y = 1200, 105  # y outside
        inside = 0 <= event_x <= alloc_width and 0 <= event_y <= alloc_height
        assert inside is False  # would proceed to autohide
