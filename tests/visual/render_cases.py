"""Deterministic rendered states used by screenshot regression tests."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.core.items import FOLDER_KIND, DockItem
from docking.core.position import Position
from docking.core.theme import Theme
from docking.platform.backends.base import PreviewImage, WindowId, WindowSnapshot
from docking.search.coordinator import SearchSnapshot
from docking.search.types import (
    SearchAction,
    SearchIdentity,
    SearchPreview,
    SearchQuery,
    SearchResult,
)
from docking.ui.autohide import HideState
from docking.ui.folder.stack import FolderStackController
from docking.ui.geometry import build_geometry_frame
from docking.ui.menu import MenuHandler
from docking.ui.preview import THUMB_H, THUMB_W, PreviewPopup
from docking.ui.renderer import DockRenderer, RenderState
from docking.ui.search_window import SearchWindow
from docking.ui.stack import StackContent, StackEntry
from docking.ui.tooltip import TooltipManager

DOCK_CASES = (
    "dock-bottom-idle",
    "dock-bottom-hovered",
    "dock-bottom-hidden",
    "dock-bottom-drag-insert-gap",
    "dock-bottom-click-frame",
    "dock-bottom-launch-frame",
    "dock-bottom-urgent-bounce-frame",
    "dock-bottom-urgent-hidden",
)
FOLDER_STACK_CASES = (
    "folder-stack-open-bottom",
    "folder-stack-hover-item-bottom",
)
SHORT_STACK_CASES = (
    "item-stack-two-items-bottom",
    "item-stack-three-items-bottom",
)
POPUP_CASES = (
    "tooltip-open-bottom",
    "preview-popup-open-bottom",
)
SEARCH_CASES = (
    "search-palette-results",
    "search-palette-actions",
    "search-palette-preview",
    "search-palette-image-preview",
)
VISUAL_CASES = (
    DOCK_CASES + FOLDER_STACK_CASES + SHORT_STACK_CASES + POPUP_CASES + SEARCH_CASES
)

DOCK_WIDTH = 420
DOCK_HEIGHT = 90
STACK_WIDTH = 360
STACK_HEIGHT = 540
ICON_SIZE = 48


class _WidgetAllocation:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height


class _FakeWidget:
    def __init__(self, width: int, height: int) -> None:
        self._allocation = _WidgetAllocation(width=width, height=height)

    def get_allocation(self) -> _WidgetAllocation:
        return self._allocation


def _rgba_fill(red: int, green: int, blue: int, alpha: int = 255) -> int:
    return (
        (red & 0xFF) << 24 | (green & 0xFF) << 16 | (blue & 0xFF) << 8 | (alpha & 0xFF)
    )


def _pixbuf(size: int, *, red: int, green: int, blue: int) -> GdkPixbuf.Pixbuf:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    pixbuf.fill(_rgba_fill(red, green, blue))
    return pixbuf


def _renderer_config() -> SimpleNamespace:
    return SimpleNamespace(
        pos=Position.BOTTOM,
        icon_size=ICON_SIZE,
        zoom_percent=2.0,
        zoom_enabled=True,
        additional_distance_from_edge=0,
        show_window_count_numbers=False,
        show_launcher_badges=True,
        show_launcher_progress=True,
        applet_prefs={},
    )


def _renderer_items() -> list[DockItem]:
    return [
        DockItem(
            desktop_id="firefox.desktop",
            name="Firefox",
            icon=_pixbuf(ICON_SIZE, red=235, green=94, blue=55),
            is_running=True,
            is_active=True,
            instance_count=2,
        ),
        DockItem(
            desktop_id="code.desktop",
            name="Code",
            icon=_pixbuf(ICON_SIZE, red=60, green=132, blue=241),
            is_running=True,
            instance_count=1,
        ),
        DockItem(
            desktop_id="music.desktop",
            name="Music",
            icon=_pixbuf(ICON_SIZE, red=60, green=187, blue=96),
        ),
    ]


def _draw_renderer_case(case_name: str) -> cairo.ImageSurface:
    theme = Theme.load("default", ICON_SIZE)
    config = _renderer_config()
    items = _renderer_items()
    dock_height = DOCK_HEIGHT
    hovered_id = ""
    hide_state: HideState | None = None
    hide_offset = 0.0
    zoom_progress = 1.0
    drop_insert_index = -1
    frame_count = 2
    now_us = 1_000_000
    click_duration_us = theme.click_time_ms * 1000
    launch_duration_us = theme.launch_bounce_time_ms * 1000
    urgent_duration_us = theme.urgent_bounce_time_ms * 1000

    if case_name == "dock-bottom-hovered":
        hovered_id = "code.desktop"
        frame_count = 8
    elif case_name == "dock-bottom-hidden":
        hide_state = HideState.HIDDEN
        hide_offset = 1.0
        zoom_progress = 0.0
    elif case_name == "dock-bottom-drag-insert-gap":
        drop_insert_index = 1
    elif case_name == "dock-bottom-click-frame":
        items[1].last_clicked = now_us - click_duration_us // 2
    elif case_name == "dock-bottom-launch-frame":
        items[1].last_launched = now_us - launch_duration_us // 4
    elif case_name == "dock-bottom-urgent-bounce-frame":
        items[0].is_urgent = True
        items[0].last_urgent = now_us - urgent_duration_us // 2
        dock_height = int(
            ICON_SIZE * config.zoom_percent
            + theme.top_padding
            + theme.bottom_padding
            + ICON_SIZE * theme.urgent_bounce_height
        )
    elif case_name == "dock-bottom-urgent-hidden":
        hide_state = HideState.HIDDEN
        hide_offset = 1.0
        zoom_progress = 0.0
        items[0].last_urgent = 95_000
    else:
        assert case_name == "dock-bottom-idle"

    cursor_main = 210.0 if hovered_id else -1_000_000.0
    render_state = RenderState(
        hide_offset=hide_offset,
        drop_insert_index=drop_insert_index,
        hovered_id=hovered_id,
        cursor_main=cursor_main,
    )
    frame = build_geometry_frame(
        items=items,
        config=config,
        theme=theme,
        window_w=DOCK_WIDTH,
        window_h=dock_height,
        cursor_main=cursor_main,
        autohide_state=hide_state,
        zoom_progress=zoom_progress,
        hide_offset=hide_offset,
        drop_insert_index=drop_insert_index,
    )
    renderer = DockRenderer()
    widget = _FakeWidget(width=DOCK_WIDTH, height=dock_height)

    def _paint_frame() -> cairo.ImageSurface:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, DOCK_WIDTH, dock_height)
        cr = cairo.Context(surface)
        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=config,
            theme=theme,
            state=render_state,
        )
        return surface

    with patch("docking.ui.renderer.GLib.get_monotonic_time", return_value=now_us):
        surface = _paint_frame()
        for _ in range(frame_count - 1):
            surface = _paint_frame()
    return surface


def _folder_stack_handler() -> MenuHandler:
    config = SimpleNamespace(
        lock_icons=False,
        hide_mode="autohide",
        previews_enabled=True,
        tooltips_enabled=True,
        monitor_index=-1,
        active_display=False,
        current_workspace_only=False,
        anchor_applets=False,
        anchor_files=False,
        theme="default",
        icon_size=ICON_SIZE,
        pos=Position.BOTTOM,
        position=Position.BOTTOM,
        item_prefs={},
        save=MagicMock(),
    )
    launcher = MagicMock()
    launcher.default_directory_app_name.return_value = "Caja"
    runtime = MagicMock()
    folder_stack = FolderStackController(
        config=config,
        runtime=runtime,
        launcher=launcher,
        dock_window=runtime.window,
    )
    handler = MenuHandler(
        about=MagicMock(),
        settings=MagicMock(),
        runtime=runtime,
        model=MagicMock(),
        config=config,
        window_tracker=MagicMock(),
        preview_service=MagicMock(),
        folder_stack=folder_stack,
        diagnostics=MagicMock(),
        launcher=launcher,
        dock_window=MagicMock(),
    )
    handler._folder_stack._folder_stack_position_value = "bottom"
    handler._folder_stack._browser.target_state = lambda _target: "ok"
    return handler


def _folder_stack_rows() -> list[dict[str, object]]:
    names = [
        ("Documents", (235, 94, 55)),
        ("Design", (60, 132, 241)),
        ("Notes", (60, 187, 96)),
        ("Invoices", (201, 162, 39)),
        ("Sketches", (168, 85, 247)),
        ("Trips", (36, 153, 170)),
        ("Ideas", (237, 100, 166)),
        ("Photos", (244, 114, 182)),
        ("doc", (89, 89, 233)),
        ("Archive", (90, 90, 90)),
        ("Exports", (240, 150, 60)),
    ]
    rows: list[dict[str, object]] = []
    for name, (red, green, blue) in names:
        rows.append(
            {
                "target": f"file:///tmp/docs/{name.lower()}",
                "name": name,
                "is_dir": True,
                "icon": _pixbuf(ICON_SIZE, red=red, green=green, blue=blue),
            }
        )
    return rows


def _draw_folder_stack_case(case_name: str) -> cairo.ImageSurface:
    handler = _folder_stack_handler()
    folder_item = DockItem(
        desktop_id="file:///tmp/docs",
        kind=FOLDER_KIND,
        target="file:///tmp/docs",
    )
    rows = _folder_stack_rows()
    if case_name in SHORT_STACK_CASES:
        count = 2 if case_name == "item-stack-two-items-bottom" else 3
        content = StackContent(
            entries=tuple(
                StackEntry(
                    key=str(row["target"]),
                    label=str(row["name"]),
                    icon=row["icon"],
                    activate=lambda: None,
                )
                for row in rows[:count]
            )
        )
        cards, popup_w, popup_h = handler._folder_stack._stack_cards_for_content(
            content
        )
    else:
        handler._folder_stack._list_directory_rows = lambda **_kwargs: rows
        cards, popup_w, popup_h = handler._folder_stack._folder_stack_cards_for_item(
            folder_item
        )
    handler._folder_stack._folder_stack_cards = cards
    if case_name == "folder-stack-hover-item-bottom":
        hover_target = next(
            card.target for card in cards if card.target and card.label == "Notes"
        )
        handler._folder_stack._folder_stack_hover_values[hover_target] = 1.0
    elif case_name not in ("folder-stack-open-bottom", *SHORT_STACK_CASES):
        raise AssertionError(f"Unknown folder stack case {case_name}")

    surface = cairo.ImageSurface(
        cairo.FORMAT_ARGB32,
        max(popup_w, STACK_WIDTH),
        max(popup_h, 180 if case_name in SHORT_STACK_CASES else STACK_HEIGHT),
    )
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    now_us = 500_000
    total_cards = len(cards)
    for draw_index, card in enumerate(cards):
        handler._folder_stack._draw_stack_card(
            cr=cr,
            card=card,
            sequence_index=total_cards - 1 - draw_index,
            now_us=now_us,
        )
    return surface


def _flush_gtk() -> None:
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def _pixbuf_surface(pixbuf: GdkPixbuf.Pixbuf) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(
        cairo.FORMAT_ARGB32,
        pixbuf.get_width(),
        pixbuf.get_height(),
    )
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return surface


def _capture_window_surface(window: Gtk.Window) -> cairo.ImageSurface:
    _flush_gtk()
    allocation = window.get_allocation()
    width = max(allocation.width, 1)
    height = max(allocation.height, 1)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    window.draw(cr)
    return surface


def _draw_tooltip_case() -> cairo.ImageSurface:
    manager = TooltipManager(
        window=SimpleNamespace(
            get_position=lambda: (100, 200),
            get_window=lambda: None,
        ),
        config=SimpleNamespace(
            tooltips_enabled=True,
            pos=Position.BOTTOM,
            icon_size=ICON_SIZE,
        ),
        model=MagicMock(),
        theme=SimpleNamespace(launch_bounce_height=0.5),
    )
    try:
        manager._show_tooltip(
            text="Firefox Developer Edition",
            pos=Position.BOTTOM,
            anchor_x=180.0,
            anchor_y=220.0,
            content_changed=True,
        )
        assert manager._tooltip_window is not None
        return _capture_window_surface(manager._tooltip_window)
    finally:
        if manager._tooltip_window is not None:
            manager._tooltip_window.destroy()
            _flush_gtk()


def _draw_preview_case() -> cairo.ImageSurface:
    tracker = MagicMock()
    tracker.list_preview_windows.return_value = [
        WindowSnapshot(
            id=WindowId.x11(101),
            desktop_id="firefox.desktop",
            title="Firefox - Docking Visual Regression",
            can_activate=True,
            can_preview=True,
        ),
        WindowSnapshot(
            id=WindowId.x11(102),
            desktop_id="firefox.desktop",
            title="Docs - Feature Review",
            can_activate=True,
            can_preview=True,
        ),
    ]
    tracker.icon_name_for_desktop.return_value = "firefox"
    preview_service = MagicMock()
    preview_service.capture.side_effect = [
        PreviewImage(
            image=_pixbuf(max(THUMB_W, THUMB_H), red=235, green=94, blue=55),
            width=THUMB_W,
            height=THUMB_H,
        ),
        PreviewImage(
            image=_pixbuf(max(THUMB_W, THUMB_H), red=60, green=132, blue=241),
            width=THUMB_W,
            height=THUMB_H,
        ),
    ]
    popup = PreviewPopup(window_tracker=tracker, preview_service=preview_service)
    try:
        popup.show_for_item(
            desktop_id="firefox.desktop",
            anchor_x=140.0,
            icon_w=48.0,
            anchor_y=320.0,
            position=Position.BOTTOM,
        )
        return _capture_window_surface(popup)
    finally:
        popup.destroy()
        _flush_gtk()


def _draw_search_case(*, panel: str | None) -> cairo.ImageSurface:
    class _Launcher:
        @staticmethod
        def load_icon(*, icon_name: str, size: int):
            colors = {
                "firefox": (235, 94, 55),
                "org.gnome.Terminal": (70, 74, 86),
                "document-open-recent": (60, 132, 241),
            }
            red, green, blue = colors.get(icon_name, (120, 120, 120))
            return _pixbuf(size, red=red, green=green, blue=blue)

    firefox = SearchIdentity("applications", "firefox.desktop")
    terminal = SearchIdentity("windows", "terminal-window")
    recent = SearchIdentity("recent-files", "file:///tmp/Proposal.pdf")
    image_path: Path | None = None
    firefox_preview = SearchPreview(
        title="Firefox",
        body=(
            "Browse the Web\n\nDesktop entry: firefox.desktop\nState: Running · Pinned"
        ),
    )
    if panel == "image":
        image_path = Path(tempfile.gettempdir()) / "docking-search-visual-preview.png"
        image = GdkPixbuf.Pixbuf.new(
            GdkPixbuf.Colorspace.RGB,
            False,
            8,
            400,
            220,
        )
        image.fill(_rgba_fill(58, 112, 202))
        image.savev(str(image_path), "png", [], [])
        firefox_preview = SearchPreview(
            title="Screenshot.png",
            body=str(image_path),
            kind="image",
            target=str(image_path),
        )
    snapshot = SearchSnapshot(
        generation=1,
        query=SearchQuery("fi"),
        results=(
            SearchResult(
                identity=firefox,
                title="Firefox",
                description="Browse the Web",
                score=750,
                icon_name="firefox",
                source="Applications",
                state="Running · Pinned",
                actions=(
                    SearchAction(
                        SearchIdentity(
                            "applications",
                            "firefox.desktop\x1ffocus",
                        ),
                        "Focus",
                    ),
                    SearchAction(
                        SearchIdentity(
                            "applications",
                            "firefox.desktop\x1fnew-window",
                        ),
                        "Open New Window",
                    ),
                    SearchAction(
                        SearchIdentity(
                            "applications",
                            "firefox.desktop\x1funpin",
                        ),
                        "Remove from Dock",
                        verb="remove",
                    ),
                ),
                preview=firefox_preview,
            ),
            SearchResult(
                identity=terminal,
                title="Project — Terminal",
                description="org.gnome.Terminal.desktop",
                score=520,
                icon_name="org.gnome.Terminal",
                source="Windows",
                actions=(
                    SearchAction(
                        SearchIdentity("windows", "terminal-window\x1factivate"),
                        "Activate Window",
                    ),
                ),
            ),
            SearchResult(
                identity=recent,
                title="Proposal.pdf",
                description="file:///tmp/Proposal.pdf",
                score=320,
                icon_name="document-open-recent",
                source="Recent Files",
                state="Recent",
                actions=(
                    SearchAction(
                        SearchIdentity(
                            "recent-files",
                            "file:///tmp/Proposal.pdf\x1fopen",
                        ),
                        "Open",
                    ),
                ),
            ),
        ),
        selected_identity=firefox,
        pending_provider_ids=(),
        errors=(),
    )
    window = SearchWindow(
        launcher=_Launcher(),
        on_query_changed=lambda _query: None,
        on_result_selected=lambda _identity: None,
        on_result_activated=lambda _result: None,
        on_action_activated=lambda _result, _action: None,
        on_hidden=lambda: None,
    )
    root = window.window.get_child()
    window.window.remove(root)
    window.window.destroy()
    offscreen = Gtk.OffscreenWindow()
    offscreen.set_default_size(680, 470)
    root.set_size_request(680, 470)
    offscreen.add(root)
    window.window = offscreen
    try:
        window.set_query("fi")
        window.update(snapshot)
        window.window.show_all()
        window.action_frame.hide()
        window.preview_frame.hide()
        if panel == "actions":
            window.toggle_actions()
        elif panel in {"preview", "image"}:
            window.toggle_preview()
        else:
            window.window.set_focus(None)
        if panel == "image":
            for _ in range(20):
                _flush_gtk()
                time.sleep(0.01)
        return _capture_window_surface(window.window)
    finally:
        window.destroy()
        _flush_gtk()
        if image_path is not None:
            image_path.unlink(missing_ok=True)


def render_case(case_name: str) -> cairo.ImageSurface:
    """Render one deterministic visual regression case."""
    if case_name in DOCK_CASES:
        return _draw_renderer_case(case_name=case_name)
    if case_name in (*FOLDER_STACK_CASES, *SHORT_STACK_CASES):
        return _draw_folder_stack_case(case_name=case_name)
    if case_name == "tooltip-open-bottom":
        return _draw_tooltip_case()
    if case_name == "preview-popup-open-bottom":
        return _draw_preview_case()
    if case_name == "search-palette-results":
        return _draw_search_case(panel=None)
    if case_name == "search-palette-actions":
        return _draw_search_case(panel="actions")
    if case_name == "search-palette-preview":
        return _draw_search_case(panel="preview")
    if case_name == "search-palette-image-preview":
        return _draw_search_case(panel="image")
    raise AssertionError(f"Unknown visual case {case_name}")
