"""Exercise real GTK clipboard callbacks and image menu ownership on X11."""

import gc
import os
import time
import weakref
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

import docking.applets.clippy.applet as clippy_mod
from docking.applets.clippy.applet import ClippyApplet
from docking.applets.clippy.state import Clip
from docking.applets.popup import PopupAnchor
from docking.core.config import Config
from docking.core.position import Position
from docking.ui.menu import MenuHandler
from docking.ui.stack import StackPopupController


def wait_until(predicate):
    deadline = time.monotonic() + 5
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("GTK clipboard did not complete its asynchronous request")


def test_native_text_image_and_menu_lifetime(monkeypatch):
    if not os.environ.get("DISPLAY") or not Gtk.init_check(None)[0]:
        pytest.skip("Requires an isolated X11 display")
    monkeypatch.setattr(clippy_mod, "DEBOUNCE_MS", 1)
    applet = ClippyApplet(48, Config())
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    menu = Gtk.Menu()
    applet.start(lambda: None)
    try:
        clipboard.set_text("native clipboard fixture", -1)
        wait_until(lambda: bool(applet._clips))
        assert applet._clips[-1].text == "native clipboard fixture"

        original = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 3, 2)
        original.fill(0x123456FF)
        clipboard.set_image(original)
        wait_until(lambda: bool(applet._clips[-1].pixels))
        clip = applet._clips[-1]
        assert (clip.width, clip.height) == (3, 2)
        assert clip.pixels == bytes.fromhex("123456") * 6

        applet.on_clicked()
        copied = []
        clipboard.request_image(lambda _clipboard, image: copied.append(image))
        wait_until(lambda: bool(copied))
        assert (copied[0].get_width(), copied[0].get_height()) == (3, 2)

        clip = applet._clips[-1]
        stack_content = applet.stack_content(48)
        assert stack_content.entries[0].thumbnail_style
        stack_content.entries[0].activate()
        rows = applet.get_menu_items()
        for row in rows:
            menu.append(row)
        image_row = rows[0].get_submenu().get_children()[0]
        assert isinstance(image_row.get_child(), Gtk.Box)
        shown = image_row.get_child().get_children()[0].get_pixbuf()
        assert (shown.get_width(), shown.get_height()) == (24, 16)
        menu.show_all()
        retained = weakref.ref(clip)
        applet._retain_clip(replace(clip))
        del clip
        gc.collect()
        assert retained() is None
        applet._clear()
        copy = MagicMock()
        monkeypatch.setattr(applet, "_copy_to_clipboard", copy)
        image_row.activate()
        copy.assert_not_called()
    finally:
        applet.stop()
        menu.destroy()
        clipboard.clear()


def test_native_menu_releases_widgets_after_selected_action():
    if not os.environ.get("DISPLAY") or not Gtk.init_check(None)[0]:
        pytest.skip("Requires an isolated X11 display")
    handler = MenuHandler.__new__(MenuHandler)
    handler._open_menus = set()
    handler._runtime = SimpleNamespace(
        menu_popup_opened=MagicMock(), menu_popup_closed=MagicMock()
    )
    handler._cleanup_folder_menu_tree = MagicMock()
    events = []
    menu = handler._new_popup_menu()
    selected = Gtk.MenuItem(label="Clipboard fixture")
    selected.connect("activate", lambda _item: events.append("activate"))
    menu.connect("destroy", lambda _menu: events.append("destroy"))
    menu.append(selected)
    menu.show_all()
    try:
        menu.emit("deactivate")
        menu.emit("hide")
        handler._runtime.menu_popup_closed.assert_called_once()
        assert not events
        selected.activate()
        assert events == ["activate"]
        wait_until(lambda: "destroy" in events)
        assert events == ["activate", "destroy"]
    finally:
        menu.destroy()


@pytest.mark.parametrize("count,icon_size", [(15, 48), (500, 128)])
def test_native_history_stack_scrolls_to_oldest_and_copies_before_closing(
    count, icon_size
):
    if not os.environ.get("DISPLAY") or not Gtk.init_check(None)[0]:
        pytest.skip("Requires an isolated X11 display")
    config = Config(
        icon_size=icon_size, applet_prefs={"clippy": {"max_entries": count}}
    )
    applet = ClippyApplet(icon_size, config)
    applet._clipboard = clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    for index in range(count - 1):
        applet.add_clip(
            f"file:///Clipboard%20fixtures/Folder-{index}/report-{index}.txt"
        )
    image = Clip.from_image(
        pixels=b"\x12\x34\x56" * 6, width=3, height=2, rowstride=9, alpha=False
    )
    applet._retain_clip(image)
    parent = Gtk.Window()
    runtime = SimpleNamespace(
        menu_popup_opened=MagicMock(),
        menu_popup_closed=MagicMock(),
        hide_hover_ui=MagicMock(),
    )
    stack = StackPopupController(config=config, runtime=runtime, dock_window=parent)
    anchor = PopupAnchor(x=640, y=700, position=Position.BOTTOM, parent=parent)
    try:
        clipboard.set_text("Original clipboard fixture", -1)
        assert stack.show_stack(
            owner_id="applet://clippy",
            provider=applet.stack_content,
            anchor=anchor,
        )
        wait_until(lambda: stack._folder_stack_window.get_mapped())
        assert len(stack._folder_stack_cards) == count
        before = []
        clipboard.request_text(lambda _clipboard, text: before.append(text))
        wait_until(lambda: bool(before))
        assert before == ["Original clipboard fixture"]
        scroller = stack._folder_stack_revealer.get_child()
        assert isinstance(scroller, Gtk.ScrolledWindow)
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        wait_until(lambda: adjustment.get_value() > 0)
        oldest = stack._folder_stack_cards[-1]
        assert oldest.label == "report-0.txt"
        area = stack._folder_stack_area
        x, y = oldest.label_x + oldest.label_w / 2, oldest.label_y + oldest.label_h / 2
        _, popup_y = area.translate_coordinates(
            stack._folder_stack_window, int(x), int(y)
        )
        assert 0 <= popup_y < stack._folder_stack_window.get_size()[1]
        stack._folder_stack_show_started_us = 0
        for kind, signal in (
            (Gdk.EventType.BUTTON_PRESS, "button-press-event"),
            (Gdk.EventType.BUTTON_RELEASE, "button-release-event"),
        ):
            event = Gdk.Event.new(kind)
            event.button, event.x, event.y = 1, x, y
            area.emit(signal, event)
        assert not stack._folder_stack_window.get_visible()
        copied = []
        clipboard.request_text(lambda _clipboard, text: copied.append(text))
        wait_until(lambda: bool(copied))
        assert copied == ["file:///Clipboard%20fixtures/Folder-0/report-0.txt"]
        runtime.menu_popup_closed.assert_called_once()
        assert stack.show_stack(
            owner_id="applet://clippy", provider=applet.stack_content, anchor=anchor
        )
        applet._clear()
        assert stack.refresh(owner_id="applet://clippy") is False
        assert not stack._folder_stack_window.get_visible()
        assert runtime.menu_popup_closed.call_count == 2
    finally:
        stack.close()
        if stack._folder_stack_window is not None:
            stack._folder_stack_window.destroy()
        parent.destroy()
        applet.stop()
        clipboard.clear()
