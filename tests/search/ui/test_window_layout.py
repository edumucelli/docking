"""Search layout acceptance with an isolated native GTK event loop."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

import docking.search.ui.window as window_mod
from docking.platform.backends.base import Rect
from docking.search.types import SearchAction, SearchIdentity, SearchPreview
from docking.search.ui.window import SearchWindow
from docking.ui.display import ScreenPosition
from tests.search.ui.test_window import _snapshot

pytestmark = pytest.mark.skipif(
    not Gtk.init_check()[0], reason="GTK display is unavailable"
)


@pytest.mark.parametrize(
    "case",
    ["small", "negative-origin", "restore-size", "user-resize", "actions", "image"],
)
def test_palette_layout(case, tmp_path):
    # Dispatch real GTK events outside the suite's toolkit doubles, as in the
    # native stack-scroll tests. They share the default main context otherwise.
    display = Gdk.Display.get_default()
    assert display is not None
    backend = display.__gtype__.name.removeprefix("Gdk").removesuffix("Display").lower()
    environment = {**os.environ, "GDK_BACKEND": backend}
    if backend == "x11":
        environment["DISPLAY"] = display.get_name()
    elif backend == "wayland":
        environment["WAYLAND_DISPLAY"] = display.get_name()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from tests.search.ui.test_window_layout import _check_layout; "
                "_check_layout(sys.argv[1], Path(sys.argv[2]))"
            ),
            case,
            str(tmp_path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _check_layout(case, tmp_path):
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        _fitted_palette(monkeypatch) as palette,
    ):
        if case in {"small", "negative-origin"}:
            bounds = (
                Rect(0, 0, 640, 349) if case == "small" else Rect(-640, 28, 600, 340)
            )
            _fits_workarea(palette, bounds)
        elif case == "restore-size":
            _recovers_preferred_size(palette)
        elif case == "user-resize":
            _keeps_user_resize(palette)
        elif case == "actions":
            _scrolls_actions(palette)
        elif case == "image":
            _scrolls_image(palette, tmp_path)
        else:
            raise AssertionError(case)


@contextmanager
def _fitted_palette(monkeypatch):
    bounds = [Rect(0, 0, 640, 349)]
    positions = []
    window = SearchWindow(
        icon_loader=MagicMock(load_icon=MagicMock(return_value=None)),
        on_query_changed=MagicMock(),
        on_result_selected=MagicMock(),
        on_result_activated=MagicMock(),
        on_action_activated=MagicMock(),
        on_hidden=MagicMock(),
        on_refine_requested=MagicMock(),
        dynamic_preview_loader=MagicMock(return_value=None),
        preview_resolver=MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        window_mod,
        "get_pointer_position",
        lambda _display: ScreenPosition(bounds[0].x + 30, bounds[0].y + 30),
    )
    monkeypatch.setattr(
        window_mod, "popup_workarea", lambda *_args, **_kwargs: bounds[0]
    )
    monkeypatch.setattr(window.window, "move", lambda x, y: positions.append((x, y)))
    window.update(_snapshot())

    def settle(expected=None, *, until=None):
        deadline = time.monotonic() + 1
        while True:
            window.window.get_display().sync()
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            size_matches = (
                expected is None or tuple(window.window.get_size()) == expected
            )
            if size_matches and (until is None or until()):
                return
            assert time.monotonic() < deadline, "GTK layout did not settle"
            time.sleep(0.005)

    try:
        yield SimpleNamespace(
            window=window, bounds=bounds, positions=positions, settle=settle
        )
    finally:
        window.destroy()


def _fits_workarea(fitted_palette, bounds):
    palette = fitted_palette
    palette.bounds[0] = bounds
    palette.window.present()
    palette.settle()
    width, height = palette.window.window.get_size()
    x, y = palette.positions[-1]
    margin = window_mod.SEARCH_WINDOW_MARGIN
    assert bounds.x + margin <= x
    assert bounds.y + margin <= y
    assert x + width <= bounds.right - margin
    assert y + height <= bounds.bottom - margin
    assert palette.window.window.get_focus() is palette.window.search_entry


def _recovers_preferred_size(fitted_palette):
    palette = fitted_palette
    palette.window.present()
    palette.settle()
    assert palette.window.window.get_size().width < window_mod.SEARCH_WINDOW_WIDTH
    palette.window.hide()
    palette.bounds[0] = Rect(0, 0, 1100, 800)
    palette.window.present()
    palette.settle()
    assert tuple(palette.window.window.get_size()) == (
        window_mod.SEARCH_WINDOW_WIDTH,
        window_mod.SEARCH_WINDOW_HEIGHT,
    )


def _keeps_user_resize(fitted_palette):
    palette = fitted_palette
    palette.window.present()
    palette.settle()
    palette.window.window.resize(480, 280)
    palette.settle((480, 280))
    requested = tuple(palette.window.window.get_size())
    assert requested == (480, 280)
    palette.window.hide()
    palette.window.present()
    palette.settle()
    assert tuple(palette.window.window.get_size()) == requested


def _scrolls_actions(fitted_palette):
    palette = fitted_palette
    palette.bounds[0] = Rect(0, 0, 640, 323)
    snapshot = _snapshot()
    actions = tuple(
        SearchAction(
            SearchIdentity("applications", f"firefox.desktop/action-{i}"), f"Action {i}"
        )
        for i in range(12)
    )
    result = replace(snapshot.results[0], actions=actions)
    palette.window.update(replace(snapshot, results=(result,)))
    palette.window.present()
    palette.settle()
    palette.window.toggle_actions()
    palette.settle()
    panel = palette.window.action_frame
    parent = panel.get_parent()
    _, y = panel.translate_coordinates(parent, 0, 0)
    assert y >= 0
    assert y + panel.get_allocated_height() <= parent.get_allocated_height()
    assert len(palette.window.actions_list.get_children()) == len(actions)
    scroller = palette.window.actions_list.get_ancestor(Gtk.ScrolledWindow)
    assert scroller is not None
    adjustment = scroller.get_vadjustment()
    assert adjustment.get_upper() > adjustment.get_page_size()


def _scrolls_image(fitted_palette, tmp_path):
    palette = fitted_palette
    palette.bounds[0] = Rect(0, 0, 640, 323)
    path = tmp_path / "generated-preview.png"
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 300, 400)
    image.fill(0x2288CCFF)
    image.savev(str(path), "png", [], [])
    snapshot = _snapshot()
    result = replace(
        snapshot.results[0],
        preview=SearchPreview(
            title="Generated image",
            body="Generated preview",
            kind="image",
            target=str(path),
        ),
    )
    palette.window.update(replace(snapshot, results=(result,)))
    palette.window.present()
    palette.settle()
    palette.window.toggle_preview()
    palette.settle()
    panel = palette.window.preview_frame
    parent = panel.get_parent()
    _, y = panel.translate_coordinates(parent, 0, 0)
    assert y >= 0
    assert y + panel.get_allocated_height() <= parent.get_allocated_height()
    scroller = palette.window.preview_image.get_ancestor(Gtk.ScrolledWindow)
    assert scroller is not None
    adjustment = scroller.get_vadjustment()
    palette.settle(until=lambda: adjustment.get_upper() > adjustment.get_page_size())
    assert adjustment.get_upper() > adjustment.get_page_size()
