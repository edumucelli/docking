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
from docking.core.config import Config
from docking.ui.menu import MenuHandler


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
        rows = applet.get_menu_items()
        image_row = rows[0]
        assert isinstance(image_row.get_child(), Gtk.Box)
        shown = image_row.get_child().get_children()[0].get_pixbuf()
        assert (shown.get_width(), shown.get_height()) == (24, 16)
        for row in rows:
            menu.append(row)
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
