"""GTK interaction coverage for the global search palette."""

from __future__ import annotations

import os
import time
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, GdkPixbuf, Gtk

import docking.search.ui.window as window_mod
from docking.search.coordinator import SearchSnapshot
from docking.search.icons import SearchResultIcons
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


def _key_event(keyval: int, state: Gdk.ModifierType) -> Gdk.EventKey:
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.state = state
    key = event.key
    # EventKey borrows storage from the allocated event, including GDK's
    # private device data used by the input-method filter.
    key._event_owner = event
    return key


@pytest.fixture
def input_palette():
    activated = MagicMock()
    window = SearchWindow(
        icon_loader=MagicMock(load_icon=MagicMock(return_value=None)),
        on_query_changed=MagicMock(),
        on_result_selected=MagicMock(),
        on_result_activated=activated,
        on_action_activated=MagicMock(),
        on_hidden=MagicMock(),
        on_refine_requested=MagicMock(),
        dynamic_preview_loader=MagicMock(return_value=None),
        preview_resolver=MagicMock(return_value=None),
    )
    window.update(_snapshot())
    window.window.show_all()
    window.action_frame.hide()
    window.preview_frame.hide()
    window.window.set_focus(window.search_entry)
    try:
        yield window, activated
    finally:
        window.destroy()


@pytest.mark.parametrize("entry_name", ["search_entry", "action_filter"])
@pytest.mark.parametrize(
    "key", [Gdk.KEY_Return, Gdk.KEY_Escape, Gdk.KEY_Up, Gdk.KEY_Down]
)
def test_ime_gets_navigation_commit_and_cancel_before_palette_actions(
    input_palette, monkeypatch, entry_name, key
):
    window, activated = input_palette
    entry = (
        window.search_entry if entry_name == "search_entry" else window.action_filter
    )
    window.action_frame.set_visible(entry_name == "action_filter")
    window.window.set_focus(entry)
    calls = []

    def filter_key(widget, event):
        calls.append((widget, event.keyval))
        return True

    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", filter_key)
    assert window._on_key_press(window.window, _key_event(key, Gdk.ModifierType(0)))
    assert calls == [(entry, key)]
    assert window.visible
    activated.assert_not_called()


@pytest.mark.parametrize("key", [Gdk.KEY_Return, Gdk.KEY_Escape])
def test_native_ime_end_before_keypress_does_not_run_palette_action(
    input_palette, monkeypatch, key
):
    window, activated = input_palette
    entry = window.search_entry
    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", lambda *_: False)
    entry.emit("preedit-changed", "â")
    entry.set_text("â")
    entry.emit("preedit-changed", "")
    entry.emit("preedit-changed", "")
    event = _key_event(key, Gdk.ModifierType(0))
    assert window._on_key_press(window.window, event)
    assert window.visible and entry.get_text() == "â"
    activated.assert_not_called()
    # The IME owns one terminating key, not the next ordinary action.
    assert window._on_key_press(window.window, event)
    if key == Gdk.KEY_Return:
        activated.assert_called_once()
    else:
        assert not window.visible


@pytest.mark.parametrize("key", [Gdk.KEY_Return, Gdk.KEY_Escape])
def test_ime_end_after_keypress_does_not_suppress_next_action(
    input_palette, monkeypatch, key
):
    window, activated = input_palette
    entry = window.search_entry
    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", lambda *_: False)
    entry.emit("preedit-changed", "â")
    event = _key_event(key, Gdk.ModifierType(0))
    assert window._on_key_press(window.window, event)
    entry.emit("preedit-changed", "")
    window._on_key_release(window.window, event)
    assert window.visible
    activated.assert_not_called()
    assert window._on_key_press(window.window, event)
    if key == Gdk.KEY_Return:
        activated.assert_called_once()
    else:
        assert not window.visible


def test_ordinary_ime_commit_key_does_not_swallow_a_later_enter(
    input_palette, monkeypatch
):
    window, activated = input_palette
    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", lambda *_: False)
    window.search_entry.emit("preedit-changed", "â")
    window.search_entry.emit("preedit-changed", "")
    assert not window._on_key_press(
        window.window, _key_event(Gdk.KEY_space, Gdk.ModifierType(0))
    )
    window._on_key_press(window.window, _key_event(Gdk.KEY_Return, Gdk.ModifierType(0)))
    activated.assert_called_once()


@pytest.mark.parametrize("signal", ["activate", "stop-search"])
def test_entry_signals_respect_native_ime_commit_and_cancellation(
    input_palette, signal
):
    window, activated = input_palette
    entry = window.search_entry
    entry.emit("preedit-changed", "â")
    entry.emit("preedit-changed", "")
    entry.emit(signal)
    activated.assert_not_called()
    assert window.visible
    entry.emit(signal)
    if signal == "activate":
        activated.assert_called_once()
    else:
        assert not window.visible


def test_hiding_removes_the_pending_preedit_end_idle_source(input_palette):
    window, _activated = input_palette
    entry = window.search_entry
    entry.emit("preedit-changed", "â")
    entry.emit("preedit-changed", "")
    source = window._preedit_ended[entry]
    from gi.repository import GLib

    assert GLib.MainContext.default().find_source_by_id(source) is not None
    window.hide()
    assert GLib.MainContext.default().find_source_by_id(source) is None
    assert not window._preediting and not window._preedit_ended


def test_action_filter_activation_respects_native_preedit_end(input_palette):
    window, activated = input_palette
    window.toggle_actions()
    entry = window.action_filter
    action = window._on_action_activated
    entry.emit("preedit-changed", "â")
    entry.emit("preedit-changed", "")
    entry.emit("activate")
    action.assert_not_called()
    activated.assert_not_called()
    assert window.visible and window.action_frame.get_visible()
    entry.emit("activate")
    action.assert_called_once()


def test_preedit_end_without_a_key_expires_before_a_later_action(
    input_palette, monkeypatch
):
    from gi.repository import GLib

    window, activated = input_palette
    entry = window.search_entry
    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", lambda *_: False)
    # Dispatch the actual lifecycle callback without draining native GTK
    # callbacks retained by unrelated fixtures on the global default context.
    context = GLib.MainContext.new()

    def idle_add(callback, *args, priority):
        source = GLib.idle_source_new()
        source.set_priority(priority)
        source.set_callback(lambda *_data: callback(*args))
        return source.attach(context)

    def source_remove(identifier):
        source = context.find_source_by_id(identifier)
        if source is not None:
            source.destroy()

    monkeypatch.setattr(
        window_mod,
        "GLib",
        SimpleNamespace(
            idle_add=idle_add,
            source_remove=source_remove,
            PRIORITY_HIGH_IDLE=GLib.PRIORITY_HIGH_IDLE,
        ),
    )
    try:
        entry.emit("preedit-changed", "â")
        entry.emit("preedit-changed", "")
        source = window._preedit_ended[entry]
        assert context.find_source_by_id(source) is not None
        deadline = time.monotonic() + 2
        while window._preedit_ended and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.005)
        assert not window._preedit_ended, (
            "Preedit end did not expire at a GTK idle boundary"
        )
        assert context.find_source_by_id(source) is None
        window._on_key_press(
            window.window, _key_event(Gdk.KEY_Return, Gdk.ModifierType(0))
        )
        activated.assert_called_once()
    finally:
        window._clear_input_methods()


@pytest.mark.parametrize(
    "state",
    [
        Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK,
        Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD5_MASK,
    ],
)
@pytest.mark.parametrize("key", [Gdk.KEY_j, Gdk.KEY_p, Gdk.KEY_Right])
def test_altgr_does_not_trigger_palette_control_shortcuts(input_palette, key, state):
    window, activated = input_palette
    assert not window._on_key_press(window.window, _key_event(key, state))
    assert not window.action_frame.get_visible()
    assert not window.preview_frame.get_visible()
    activated.assert_not_called()


def test_return_still_activates_when_the_input_method_does_not_handle_it(
    input_palette, monkeypatch
):
    window, activated = input_palette
    monkeypatch.setattr(Gtk.Entry, "im_context_filter_keypress", lambda *_args: False)
    assert window._on_key_press(
        window.window, _key_event(Gdk.KEY_Return, Gdk.ModifierType(0))
    )
    activated.assert_called_once()


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
        _key_event(Gdk.KEY_Tab, Gdk.ModifierType(0)),
    )
    assert not window._on_key_press(
        window.window,
        _key_event(Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK),
    )
    assert window._on_key_press(
        window.window,
        _key_event(Gdk.KEY_Right, Gdk.ModifierType.CONTROL_MASK),
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
    window.window.get_display.return_value = None
    window._window_focus = None

    window.present(
        activation_context={
            "timestamp": 4321,
            "XDG_ACTIVATION_TOKEN": "token",
        }
    )

    window.window.set_startup_id.assert_called_once_with("token")
    window.window.present_with_time.assert_called_once_with(4321)
    window.window.present.assert_not_called()


def test_icon_completion_updates_current_rows_without_changing_query_selection_or_focus():
    def image(color):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
        pixbuf.fill(color)
        return pixbuf

    item = SimpleNamespace(icon=image(0x2288CCFF))
    model = MagicMock()
    model.find_by_desktop_id.return_value = item
    loader = MagicMock()
    resolver = SearchResultIcons(model=model, registry=MagicMock(), loader=loader)
    window = SearchWindow(
        icon_loader=loader,
        result_icons=resolver,
        on_query_changed=MagicMock(),
        on_result_selected=MagicMock(),
        on_result_activated=MagicMock(),
        on_action_activated=MagicMock(),
        on_hidden=MagicMock(),
        on_refine_requested=MagicMock(),
        dynamic_preview_loader=MagicMock(return_value=None),
        preview_resolver=MagicMock(return_value=None),
    )
    snapshot = _snapshot()
    result = replace(snapshot.results[0], metadata=(("desktop_id", "firefox.desktop"),))
    snapshot = replace(snapshot, results=(result,))
    window.update(snapshot)
    window.present(initial_query="unfinished query")
    window.window.set_focus(window.search_entry)
    row = window.results_list.get_children()[0]
    generation = window._result_generation
    assert row.image.get_pixbuf() is item.icon
    changed = loader.subscribe.call_args.args[0]
    previous = item.icon
    item.icon = image(0xCC4422FF)
    changed("unrelated.desktop")
    assert row.image.get_pixbuf() is previous
    changed("firefox.desktop")
    assert row.image.get_pixbuf() is item.icon
    assert window.results_list.get_children()[0] is row
    assert window.results_list.get_selected_row() is row
    assert window._result_generation == generation
    assert window.search_entry.get_text() == "unfinished query"
    assert window.window.get_focus() is window.search_entry
    replacement = replace(
        result,
        identity=SearchIdentity("applications", "other.desktop"),
        metadata=(("desktop_id", "other.desktop"),),
    )
    window.update(
        replace(
            snapshot, results=(replacement,), selected_identity=replacement.identity
        )
    )
    current_row = window.results_list.get_children()[0]
    previous = current_row.image.get_pixbuf()
    item.icon = image(0x33CC22FF)
    changed("firefox.desktop")
    assert current_row.image.get_pixbuf() is previous
    assert row.get_parent() is None
    window.destroy()
    loader.subscribe.return_value.assert_called_once()
    model.remove_change_listener.assert_called_once()
    model.remove_applet_change_listener.assert_called_once()
    changed("firefox.desktop")  # A late notification cannot touch destroyed rows.


@pytest.mark.parametrize("menu_grab", [True, False])
def test_present_cancels_menu_grab_before_showing_search(monkeypatch, menu_grab):
    window = SearchWindow.__new__(SearchWindow)
    window.window = MagicMock()
    window.action_frame = MagicMock()
    window.preview_frame = MagicMock()
    window.search_entry = MagicMock()
    window.search_entry.get_text.return_value = ""
    window.window.get_display.return_value = None
    window._window_focus = None
    grab = MagicMock(spec=Gtk.MenuShell if menu_grab else Gtk.Dialog)
    monkeypatch.setattr(Gtk, "grab_get_current", lambda: grab)
    window.window.show_all.side_effect = lambda: (
        grab.cancel.assert_called_once() if menu_grab else None
    )
    window.present()
    window.window.present.assert_called_once()
    if not menu_grab:
        assert not hasattr(grab, "cancel")


@pytest.mark.parametrize(
    "filename",
    [
        "screenshot.png",
        pytest.param(
            b"screenshot-\xff.png".decode("utf-8", errors="surrogateescape"),
            marks=pytest.mark.skipif(
                os.name == "nt", reason="Non-UTF8 byte filenames are POSIX-only"
            ),
        ),
    ],
)
def test_image_preview_shows_thumbnail_and_metadata(
    tmp_path, monkeypatch, filename
) -> None:
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
    image_path = image_path.rename(tmp_path / filename)
    identity = SearchIdentity("recent-files", image_path.as_uri())
    result = SearchResult(
        identity=identity,
        title="Screenshot",
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

    assert window.preview_title.get_label().encode("utf-8")
    assert window.preview_body.get_label().encode("utf-8")
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


def test_asynchronous_window_preview_arrives_after_initial_miss_and_stops_on_hide():
    from dataclasses import replace

    from gi.repository import GLib

    loader = MagicMock(return_value=None)
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
        refresh_dynamic_previews=True,
    )
    snapshot = _snapshot()
    preview = SearchPreview(
        title="Native window", body="Fixture", kind="window", target="windows:100"
    )
    snapshot = replace(
        snapshot, results=(replace(snapshot.results[0], preview=preview),)
    )
    try:
        window.update(snapshot)
        window.present()
        window.toggle_preview()
        source = window._dynamic_preview_source
        assert source and not window.preview_image.get_visible()
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 28, 20)
        loader.return_value = LoadedSearchImage(
            pixbuf=pixbuf, width=28, height=20, format_name="Live Window", file_size=-1
        )
        assert window._refresh_dynamic_preview()
        assert (
            window.preview_image.get_visible()
            and window.preview_image.get_pixbuf() is pixbuf
        )
        window.hide()
        assert window._dynamic_preview_source == 0
        assert GLib.MainContext.default().find_source_by_id(source) is None
    finally:
        window.destroy()
