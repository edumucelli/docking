"""GTK interaction coverage for the global search palette."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, GdkPixbuf, Gtk

import docking.search.ui.window as window_mod
from docking.search.coordinator import SearchSnapshot
from docking.search.types import (
    SearchAction,
    SearchIdentity,
    SearchPreview,
    SearchQuery,
    SearchResult,
)
from docking.search.ui.thumbnails import LoadedSearchImage
from docking.search.ui.window import SearchWindow

pytestmark = pytest.mark.skipif(
    not Gtk.init_check()[0],
    reason="GTK display is unavailable",
)


class _ImmediateExecutor:
    def submit(self, callback, **kwargs):
        future = Future()
        try:
            future.set_result(callback(**kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **_kwargs) -> None:
        pass


def _snapshot() -> SearchSnapshot:
    identity = SearchIdentity("applications", "firefox.desktop")
    result = SearchResult(
        identity=identity,
        title="Firefox",
        description="Web Browser",
        icon_name="firefox",
        actions=(
            SearchAction(
                SearchIdentity("applications", "firefox.desktop\x1fopen"),
                "Open",
            ),
            SearchAction(
                SearchIdentity("applications", "firefox.desktop\x1fpin"),
                "Keep in Dock",
            ),
        ),
        preview=SearchPreview(
            title="Firefox",
            body="Application preview",
        ),
    )
    return SearchSnapshot(
        generation=1,
        query=SearchQuery("fire"),
        results=(result,),
        selected_identity=identity,
        pending_provider_ids=(),
        errors=(),
    )


def test_results_primary_action_and_action_panel() -> None:
    selected = MagicMock()
    activated = MagicMock()
    actioned = MagicMock()
    refined = MagicMock()
    window = SearchWindow(
        icon_loader=MagicMock(load_icon=MagicMock(return_value=None)),
        on_query_changed=MagicMock(),
        on_result_selected=selected,
        on_result_activated=activated,
        on_action_activated=actioned,
        on_hidden=MagicMock(),
        on_refine_requested=refined,
        dynamic_preview_loader=MagicMock(return_value=None),
        preview_resolver=MagicMock(return_value=None),
    )

    window.update(_snapshot())

    assert window.selected_result().title == "Firefox"
    assert window.primary_button.get_label() == "Open"
    assert window.status_label.get_label() == "1 results"
    window.activate_selected()
    activated.assert_called_once()

    window.window.show_all()
    window.action_frame.hide()
    window.preview_frame.hide()
    window.window.set_focus(window.search_entry)
    assert not window._on_key_press(
        window.window,
        SimpleNamespace(keyval=Gdk.KEY_Tab, state=Gdk.ModifierType(0)),
    )
    assert not window._on_key_press(
        window.window,
        SimpleNamespace(
            keyval=Gdk.KEY_ISO_Left_Tab,
            state=Gdk.ModifierType.SHIFT_MASK,
        ),
    )
    assert window._on_key_press(
        window.window,
        SimpleNamespace(
            keyval=Gdk.KEY_Right,
            state=Gdk.ModifierType.CONTROL_MASK,
        ),
    )
    refined.assert_called_once()

    window.toggle_preview()
    assert window.preview_frame.get_visible()
    assert window.preview_body.get_label() == "Application preview"

    window.toggle_actions()
    assert not window.preview_frame.get_visible()
    assert window.action_frame.get_visible()
    assert len(window.actions_list.get_children()) == 2
    window.destroy()


def test_activation_timestamp_is_used_for_window_manager_focus() -> None:
    window = SearchWindow.__new__(SearchWindow)
    window.window = MagicMock()
    window.action_frame = MagicMock()
    window.preview_frame = MagicMock()
    window.search_entry = MagicMock()
    window.search_entry.get_text.return_value = ""

    window.present(
        activation_context={
            "timestamp": 4321,
            "XDG_ACTIVATION_TOKEN": "token",
        }
    )

    window.window.set_startup_id.assert_called_once_with("token")
    window.window.present_with_time.assert_called_once_with(4321)
    window.window.present.assert_not_called()


def test_image_preview_shows_thumbnail_and_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        window_mod,
        "ThreadPoolExecutor",
        lambda **_kwargs: _ImmediateExecutor(),
    )
    monkeypatch.setattr(
        window_mod.GLib,
        "idle_add",
        lambda callback, *args: callback(*args),
    )
    image_path = tmp_path / "screenshot.png"
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        120,
        60,
    )
    pixbuf.fill(0x3366CCFF)
    pixbuf.savev(str(image_path), "png", [], [])
    identity = SearchIdentity("recent-files", image_path.as_uri())
    result = SearchResult(
        identity=identity,
        title=image_path.name,
        preview=SearchPreview(
            title=image_path.name,
            body=str(image_path),
            kind="image",
            target=str(image_path),
        ),
    )
    snapshot = SearchSnapshot(
        generation=1,
        query=SearchQuery("screenshot"),
        results=(result,),
        selected_identity=identity,
        pending_provider_ids=(),
        errors=(),
    )
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

    window.update(snapshot)
    row = window.results_list.get_row_at_index(0)
    assert row is not None
    row_image = row.get_child().get_children()[0]
    row_thumbnail = row_image.get_pixbuf()
    assert row_thumbnail is not None
    assert row_thumbnail.get_width() == 32
    assert row_thumbnail.get_height() == 16
    window.toggle_preview()

    thumbnail = window.preview_image.get_pixbuf()
    assert thumbnail is not None
    assert thumbnail.get_width() == 120
    assert thumbnail.get_height() == 60
    assert "120 × 60" in window.preview_metadata.get_label()
    assert "PNG" in window.preview_metadata.get_label()
    window.destroy()


def test_dynamic_window_preview_loader_populates_panel() -> None:
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        200,
        120,
    )
    loader = MagicMock(
        return_value=LoadedSearchImage(
            pixbuf=pixbuf,
            width=1280,
            height=720,
            format_name="Live Window",
            file_size=-1,
        )
    )
    identity = SearchIdentity("windows", "x11:42")
    result = SearchResult(
        identity=identity,
        title="Editor",
        preview=SearchPreview(
            title="Editor",
            body="editor.desktop",
            kind="window",
            target="x11:42",
        ),
    )
    snapshot = SearchSnapshot(
        generation=1,
        query=SearchQuery("editor"),
        results=(result,),
        selected_identity=identity,
        pending_provider_ids=(),
        errors=(),
    )
    window = SearchWindow(
        icon_loader=MagicMock(load_icon=MagicMock(return_value=None)),
        on_query_changed=MagicMock(),
        on_result_selected=MagicMock(),
        on_result_activated=MagicMock(),
        on_action_activated=MagicMock(),
        on_hidden=MagicMock(),
        on_refine_requested=MagicMock(),
        dynamic_preview_loader=loader,
        preview_resolver=MagicMock(return_value=None),
    )

    window.update(snapshot)
    window.toggle_preview()

    loader.assert_called_once()
    assert window.preview_image.get_pixbuf() is pixbuf
    assert window.preview_metadata.get_label() == "1280 × 720 · Live Window"
    loader.reset_mock()
    window.update(snapshot)
    loader.assert_called_once()
    window.destroy()


def test_partial_results_do_not_override_waiting_selection() -> None:
    snapshot = _snapshot()
    waiting = SearchSnapshot(
        generation=snapshot.generation,
        query=snapshot.query,
        results=snapshot.results,
        selected_identity=None,
        pending_provider_ids=("windows",),
        errors=(),
    )
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

    window.update(waiting)

    assert window.selected_result() is None
    window.destroy()
