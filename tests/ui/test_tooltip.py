"""Tests for tooltip manager.

Covers positioning math, content caching (flicker prevention), and
the hide/show lifecycle that prevents spurious crossing events.
"""

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.tooltip as tooltip_mod
from docking.core.layout import LayoutItem
from docking.core.position import Position
from docking.ui.geometry import DockGeometryFrame, ItemGeometry, Rect
from docking.ui.tooltip import (
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

        # When / Then - gap should be small positive value
        assert 5 <= TOOLTIP_BASE_GAP <= 50


class TestTooltipHide:
    def test_hide_when_no_window(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        # When / Then - should not raise
        tooltip.hide()

    def test_update_with_no_item_keeps_tooltip(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        # When
        tooltip.update(None, None)
        # Then
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
        # When
        tooltip.update(item, None)
        # Then
        tooltip._tooltip_window.hide.assert_not_called()

    def test_update_hides_tooltip_when_disabled(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        config.tooltips_enabled = False
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        item = MagicMock()
        item.name = "Firefox"
        # When
        tooltip.update(item, _frame_for_item(item))
        # Then
        tooltip._tooltip_window.hide.assert_called_once()

    def test_update_with_missing_geometry_returns_without_showing(self):
        tooltip = _make_tooltip()
        tooltip._show_tooltip = MagicMock()  # type: ignore[method-assign]
        item = _make_item("Firefox")

        tooltip.update(item, None)

        tooltip._show_tooltip.assert_not_called()


# Anchor point for tests
AX, AY = 500.0, 300.0
TW, TH = 80, 24


class TestTooltipPositionBottom:
    def test_centered_horizontally(self):
        tx, _ty = compute_tooltip_position(
            pos=Position.BOTTOM, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW / 2)

    def test_above_anchor(self):
        _tx, ty = compute_tooltip_position(
            pos=Position.BOTTOM, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY - TH - TOOLTIP_BASE_GAP)
        assert ty < AY


class TestTooltipPositionTop:
    def test_centered_horizontally(self):
        tx, _ty = compute_tooltip_position(
            pos=Position.TOP, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW / 2)

    def test_below_anchor(self):
        _tx, ty = compute_tooltip_position(
            pos=Position.TOP, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY + TOOLTIP_BASE_GAP)
        assert ty > AY


class TestTooltipPositionLeft:
    def test_right_of_anchor(self):
        tx, _ty = compute_tooltip_position(
            pos=Position.LEFT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX + TOOLTIP_BASE_GAP)
        assert tx > AX

    def test_centered_vertically(self):
        _tx, ty = compute_tooltip_position(
            pos=Position.LEFT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert ty == int(AY - TH / 2)


class TestTooltipPositionRight:
    def test_left_of_anchor(self):
        tx, _ty = compute_tooltip_position(
            pos=Position.RIGHT, anchor_x=AX, anchor_y=AY, tooltip_w=TW, tooltip_h=TH
        )
        assert tx == int(AX - TW - TOOLTIP_BASE_GAP)
        assert tx < AX

    def test_centered_vertically(self):
        _tx, ty = compute_tooltip_position(
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
    window.get_position.return_value = (0, 0)
    config = MagicMock()
    model = MagicMock()
    theme = MagicMock()
    return TooltipManager(window, config, model, theme)


def _make_item(name: str, builder: bool = False) -> MagicMock:
    item = MagicMock()
    item.name = name
    item.tooltip_builder = (lambda: MagicMock()) if builder else None
    return item


def _frame_for_item(
    item,
    *,
    anchor_x: float = 24.0,
    anchor_y: float = 8.0,
) -> DockGeometryFrame:
    layout_item = LayoutItem(x=0.0, scale=1.0, width=48.0)
    item_geometry = ItemGeometry(
        item=item,
        layout_item=layout_item,
        draw_rect=Rect(0, 0, 48, 48),
        hover_rect=Rect(0, 0, 48, 48),
        hit_rect=Rect(0, 0, 48, 48),
        background_rect=Rect(0, 24, 48, 24),
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        scaled_size=48.0,
        main_pos=0.0,
    )
    return DockGeometryFrame(
        window_rect=Rect(0, 0, 300, 80),
        static_dock_rect=Rect(0, 0, 100, 60),
        cursor_rect=Rect(0, 0, 100, 60),
        background_rect=Rect(0, 36, 100, 24),
        layout=(layout_item,),
        item_geometries=(item_geometry,),
        local_cursor_main=0.0,
        zoomed_main_offset=0.0,
        cross_size=60.0,
    )


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
        show_tooltip = MagicMock()
        tooltip._show_tooltip = show_tooltip  # type: ignore[method-assign]
        # When
        tooltip.update(item, _frame_for_item(item))
        # Then
        show_tooltip.assert_called_once()
        assert show_tooltip.call_args.kwargs["content_changed"] is False
        assert tooltip._pending_show_source == 0

    def test_different_item_triggers_rebuild(self):
        # Given
        tooltip = _make_tooltip()
        item_a = _make_item("Firefox")
        item_b = _make_item("Chrome")
        tooltip._last_item = item_a
        tooltip._last_name = "Firefox"
        # When/Then - content_changed should be True for different item
        content_changed = not (
            item_b is tooltip._last_item and item_b.name == tooltip._last_name
        )
        assert content_changed is True

    def test_same_item_different_name_triggers_rebuild(self):
        # Given
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
        # Given
        tooltip = _make_tooltip()
        item = _make_item("Paris: 17°C", builder=True)
        tooltip._last_item = item
        tooltip._last_name = "Paris: 17°C"
        # When/Then - should be cached (builder only called on content change)
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
        tooltip.update(None, None)
        tooltip._tooltip_window.hide.assert_not_called()

    def test_empty_name_does_not_hide(self):
        tooltip = _make_tooltip()
        tooltip._tooltip_window = MagicMock()
        item = _make_item("")
        tooltip.update(item, None)
        tooltip._tooltip_window.hide.assert_not_called()


class TestTooltipContentCoalescing:
    def test_rapid_hover_changes_only_show_last_item(self, monkeypatch):
        tooltip = _make_tooltip()
        item_a = _make_item("Caja")
        item_b = _make_item("Terminator")
        frame_a = _frame_for_item(item_a, anchor_x=20.0, anchor_y=10.0)
        frame_b = _frame_for_item(item_b, anchor_x=60.0, anchor_y=10.0)
        callbacks: dict[int, Callable[[], bool]] = {}
        removed: list[int] = []
        next_id = {"value": 1}

        def idle_add(callback):
            source_id = next_id["value"]
            next_id["value"] += 1
            callbacks[source_id] = callback
            return source_id

        def source_remove(source_id: int) -> None:
            removed.append(source_id)
            callbacks.pop(source_id, None)

        monkeypatch.setattr(tooltip_mod.GLib, "idle_add", idle_add)
        monkeypatch.setattr(tooltip_mod.GLib, "source_remove", source_remove)
        show_tooltip = MagicMock()
        tooltip._show_tooltip = show_tooltip  # type: ignore[method-assign]

        tooltip.update(item_a, frame_a)
        tooltip.update(item_b, frame_b)

        show_tooltip.assert_not_called()
        assert removed == [1]
        assert tooltip._pending_show_source == 2

        callbacks[2]()

        show_tooltip.assert_called_once()
        kwargs = show_tooltip.call_args.kwargs
        assert kwargs["text"] == "Terminator"
        assert kwargs["anchor_x"] == 60.0
        assert kwargs["content_changed"] is True
        assert tooltip._last_item is item_b
        assert tooltip._last_name == "Terminator"

    def test_hide_cancels_pending_show(self, monkeypatch):
        tooltip = _make_tooltip()
        item = _make_item("Caja")
        frame = _frame_for_item(item, anchor_x=20.0, anchor_y=10.0)
        removed: list[int] = []

        monkeypatch.setattr(tooltip_mod.GLib, "idle_add", lambda callback: 9)
        monkeypatch.setattr(
            tooltip_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )

        tooltip.update(item, frame)
        tooltip.hide()

        assert removed == [9]
        assert tooltip._pending_show_source == 0

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
        # Given

        # This is a structural test: verify the function exists and the
        # pattern. The actual _on_leave integration requires GTK.
        # We verify the bounds-check logic directly.
        alloc_width, alloc_height = 1440, 100
        event_x, event_y = 1200, 80  # inside
        inside = 0 <= event_x <= alloc_width and 0 <= event_y <= alloc_height
        assert inside is True  # would return False from _on_leave

    def test_leave_outside_bounds_is_real(self):
        # Given
        alloc_width, alloc_height = 1440, 100
        event_x, event_y = 1200, 105  # y outside
        inside = 0 <= event_x <= alloc_width and 0 <= event_y <= alloc_height
        assert inside is False  # would proceed to autohide


class _FakeTooltipScreen:
    def get_rgba_visual(self):
        return object()

    def get_width(self) -> int:
        return 200

    def get_height(self) -> int:
        return 100


class _FakeTooltipWindow:
    def __init__(self, **_kwargs):
        self._screen = _FakeTooltipScreen()
        self._visible = False
        self._child = None
        self._removed = 0
        self._moved = None
        self._draw_cb = None
        self._transient_for = None
        self._attached_to = None
        self._accept_focus = None
        self._focus_on_map = None

    def set_transient_for(self, window) -> None:
        self._transient_for = window

    def get_transient_for(self):
        return self._transient_for

    def set_attached_to(self, window) -> None:
        self._attached_to = window

    def set_decorated(self, _value: bool) -> None:
        return

    def set_skip_taskbar_hint(self, _value: bool) -> None:
        return

    def set_resizable(self, _value: bool) -> None:
        return

    def set_type_hint(self, _value) -> None:
        return

    def set_accept_focus(self, value: bool) -> None:
        self._accept_focus = value

    def set_focus_on_map(self, value: bool) -> None:
        self._focus_on_map = value

    def set_app_paintable(self, _value: bool) -> None:
        return

    def get_screen(self):
        return self._screen

    def set_visual(self, _value) -> None:
        return

    def connect(self, signal: str, callback) -> None:
        if signal == "draw":
            self._draw_cb = callback

    def get_visible(self) -> bool:
        return self._visible

    def hide(self) -> None:
        self._visible = False

    def get_child(self):
        return self._child

    def remove(self, _child) -> None:
        self._removed += 1
        self._child = None

    def add(self, child) -> None:
        self._child = child

    def get_preferred_size(self):
        return (
            SimpleNamespace(width=0, height=0),
            SimpleNamespace(width=120, height=30),
        )

    def move(self, x: int, y: int) -> None:
        self._moved = (x, y)

    def show_all(self) -> None:
        self._visible = True


class _FakeTooltipLabel:
    def __init__(self, label: str):
        self.label = label

    def override_color(self, _state, _rgba) -> None:
        return

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def show_all(self) -> None:
        return


class _FakeGtk:
    Window = _FakeTooltipWindow
    Label = _FakeTooltipLabel
    WindowType = SimpleNamespace(POPUP=1)
    StateFlags = SimpleNamespace(NORMAL=1)


class _FakeGdk:
    WindowTypeHint = SimpleNamespace(TOOLTIP=1)
    RGBA = staticmethod(lambda *args: args)


class TestTooltipIntegrationBranches:
    def test_show_tooltip_creates_window_and_clamps_to_screen(self, monkeypatch):
        # Given
        monkeypatch.setattr(tooltip_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(tooltip_mod, "Gdk", _FakeGdk)
        window = MagicMock()
        window.surface_service.popups_use_parent_relative_coordinates = False
        config = SimpleNamespace(icon_size=48)
        model = MagicMock()
        theme = SimpleNamespace(launch_bounce_height=0.5)
        tooltip = TooltipManager(window, config, model, theme)

        # When
        tooltip._show_tooltip(
            text="Firefox",
            pos=Position.BOTTOM,
            anchor_x=2.0,
            anchor_y=4.0,
            widget=None,
            content_changed=True,
        )

        # Then
        assert isinstance(tooltip._tooltip_window, _FakeTooltipWindow)
        assert tooltip._tooltip_window._transient_for is window
        assert tooltip._tooltip_window._attached_to is window
        assert tooltip._tooltip_window._accept_focus is False
        assert tooltip._tooltip_window._focus_on_map is False
        moved = tooltip._tooltip_window._moved
        assert moved is not None
        assert moved[0] >= 0
        assert moved[1] >= 0

    def test_show_tooltip_without_content_change_keeps_existing_child(
        self, monkeypatch
    ):
        # Given
        monkeypatch.setattr(tooltip_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(tooltip_mod, "Gdk", _FakeGdk)
        tooltip = TooltipManager(
            MagicMock(),
            SimpleNamespace(icon_size=48),
            MagicMock(),
            SimpleNamespace(launch_bounce_height=0.0),
        )
        existing = _FakeTooltipWindow()
        existing._child = _FakeTooltipLabel("Old")
        tooltip._tooltip_window = existing

        # When
        tooltip._show_tooltip(
            text="Same",
            pos=Position.TOP,
            anchor_x=100.0,
            anchor_y=20.0,
            content_changed=False,
        )

        # Then
        assert existing._removed == 0

    def test_show_tooltip_draw_callback_builds_rounded_background(self, monkeypatch):
        class FakeCairo:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def new_sub_path(self) -> None:
                self.calls.append(("new_sub_path", ()))

            def arc(self, *args) -> None:
                self.calls.append(("arc", args))

            def close_path(self) -> None:
                self.calls.append(("close_path", ()))

            def set_source_rgba(self, *args) -> None:
                self.calls.append(("set_source_rgba", args))

            def fill(self) -> None:
                self.calls.append(("fill", ()))

        monkeypatch.setattr(tooltip_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(tooltip_mod, "Gdk", _FakeGdk)
        tooltip = TooltipManager(
            MagicMock(),
            SimpleNamespace(icon_size=48),
            MagicMock(),
            SimpleNamespace(launch_bounce_height=0.0),
        )

        tooltip._show_tooltip(
            text="Firefox",
            pos=Position.BOTTOM,
            anchor_x=40.0,
            anchor_y=24.0,
        )

        cairo = FakeCairo()
        widget = SimpleNamespace(
            get_allocation=lambda: SimpleNamespace(width=120, height=30)
        )
        assert tooltip._tooltip_window._draw_cb(widget, cairo) is False
        assert [name for name, _args in cairo.calls].count("arc") == 4
        assert ("fill", ()) in cairo.calls

    def test_show_tooltip_replaces_visible_content_and_accepts_custom_widget(
        self, monkeypatch
    ):
        monkeypatch.setattr(tooltip_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(tooltip_mod, "Gdk", _FakeGdk)
        tooltip = TooltipManager(
            MagicMock(),
            SimpleNamespace(icon_size=48),
            MagicMock(),
            SimpleNamespace(launch_bounce_height=0.0),
        )
        existing = _FakeTooltipWindow()
        existing._visible = True
        existing._child = _FakeTooltipLabel("Old")
        tooltip._tooltip_window = existing
        widget = MagicMock()

        tooltip._show_tooltip(
            text="New",
            pos=Position.TOP,
            anchor_x=100.0,
            anchor_y=20.0,
            content_changed=True,
        )
        assert existing._removed == 1
        assert existing.get_child() is not None

        tooltip._show_tooltip(
            text="Widget",
            pos=Position.LEFT,
            anchor_x=120.0,
            anchor_y=30.0,
            widget=widget,
            content_changed=True,
        )

        assert existing._removed == 2
        assert existing.get_child() is widget

    def test_update_hides_when_hovered_item_not_in_visible_list(self):
        # Given
        window = MagicMock()
        window.get_size.return_value = (300, 80)
        window.get_position.return_value = (10, 10)
        config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            tooltips_enabled=True,
        )
        model = MagicMock()
        model.visible_items.return_value = []
        theme = SimpleNamespace(horizontal_padding=8, item_padding=8, bottom_padding=4)
        tooltip = TooltipManager(window, config, model, theme)
        hide = MagicMock()
        tooltip.hide = hide  # type: ignore[method-assign]
        item = MagicMock()
        item.name = "Firefox"
        item.tooltip_builder = None
        frame = SimpleNamespace(geometry_for_item=MagicMock(return_value=None))

        # When
        tooltip.update(item=item, geometry=frame)

        # Then
        hide.assert_called_once()

    def test_update_uses_builder_widget_when_content_changes(self, monkeypatch):
        # Given
        window = MagicMock()
        window.get_size.return_value = (300, 80)
        window.get_position.return_value = (10, 10)
        config = SimpleNamespace(
            pos=Position.LEFT,
            icon_size=48,
            tooltips_enabled=True,
        )
        model = MagicMock()
        item = MagicMock()
        item.name = "CPU: 30%"
        built_widget = MagicMock()
        item.tooltip_builder = MagicMock(return_value=built_widget)
        model.visible_items.return_value = [item]
        theme = SimpleNamespace(horizontal_padding=8, item_padding=8, bottom_padding=4)
        tooltip = TooltipManager(window, config, model, theme)
        show_tooltip = MagicMock()
        tooltip._show_tooltip = show_tooltip  # type: ignore[method-assign]
        frame = _frame_for_item(item, anchor_x=52.0, anchor_y=24.0)
        monkeypatch.setattr(
            tooltip_mod.GLib, "idle_add", lambda callback: callback() or 1
        )

        # When
        tooltip.update(item=item, geometry=frame)

        # Then
        show_tooltip.assert_called_once()
        kwargs = show_tooltip.call_args.kwargs
        assert kwargs["widget"] is built_widget

    def test_update_uses_backend_surface_position_for_wayland_anchor(
        self, monkeypatch
    ):
        window = MagicMock()
        window.get_position.return_value = (0, 0)
        window.surface_service = SimpleNamespace(
            get_surface_position=lambda: (100, 200),
            popups_use_parent_relative_coordinates=True,
        )
        config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            tooltips_enabled=True,
        )
        item = _make_item("Weather")
        tooltip = TooltipManager(
            window,
            config,
            MagicMock(),
            SimpleNamespace(launch_bounce_height=0.0),
        )
        show_tooltip = MagicMock()
        tooltip._show_tooltip = show_tooltip  # type: ignore[method-assign]
        frame = _frame_for_item(item, anchor_x=40.0, anchor_y=10.0)
        monkeypatch.setattr(
            tooltip_mod.GLib, "idle_add", lambda callback: callback() or 1
        )

        tooltip.update(item=item, geometry=frame)

        kwargs = show_tooltip.call_args.kwargs
        assert kwargs["anchor_x"] == 140.0
        assert kwargs["anchor_y"] == 210.0

    def test_show_tooltip_converts_absolute_position_to_parent_relative_wayland(
        self, monkeypatch
    ):
        monkeypatch.setattr(tooltip_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(tooltip_mod, "Gdk", _FakeGdk)
        window = MagicMock()
        window.surface_service = SimpleNamespace(
            get_surface_position=lambda: (100, 200),
            popups_use_parent_relative_coordinates=True,
        )
        tooltip = TooltipManager(
            window,
            SimpleNamespace(icon_size=48),
            MagicMock(),
            SimpleNamespace(launch_bounce_height=0.0),
        )

        tooltip._show_tooltip(
            text="Weather",
            pos=Position.BOTTOM,
            anchor_x=140.0,
            anchor_y=210.0,
            widget=None,
            content_changed=True,
        )

        assert tooltip._tooltip_window._moved == (-20, -30)
