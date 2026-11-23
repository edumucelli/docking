"""Tests for the Checklist applet."""

import pytest
from gi.repository import Gtk

from docking.applets import load_applet_class
from docking.applets.base import load_catalog_icon
from docking.applets.checklist.applet import ChecklistApplet
from docking.applets.checklist.state import ChecklistItem, items_from_prefs
from docking.core.config import Config


def test_catalog_and_saved_items():
    assert load_applet_class("checklist") is ChecklistApplet
    assert load_catalog_icon(applet_id="checklist", size=32) is not None
    assert items_from_prefs(
        [None, {"text": " "}, {"text": " Send invoice ", "checked": True}]
    ) == [ChecklistItem("Send invoice", checked=True)]


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
def test_checklist_popup_saves_items_and_updates_count(tmp_path):
    path = tmp_path / "dock.json"
    config = Config.load(path)
    applet = ChecklistApplet(48, config=config)
    try:
        empty_icon = applet.item.icon.get_pixels()
        applet.on_clicked()
        assert applet.popup_visible
        popup = applet._popup
        scrolled, controls, clear = popup.get_child().get_child().get_children()
        entry, add = controls.get_children()
        rows = scrolled.get_child().get_child()
        assert popup.get_focus() is entry
        assert not clear.get_sensitive()
        assert clear.get_halign() == Gtk.Align.CENTER

        entry.set_text(" Send invoice ")
        entry.activate()
        first_row = rows.get_children()[0]
        first, edit = first_row.get_children()
        assert first.get_label() == "Send invoice"
        assert entry.get_text() == ""
        assert "1 unchecked item" in applet.item.name
        assert applet.item.icon.get_pixels() != empty_icon

        first.set_active(True)
        assert first.get_visible()
        assert applet.item.icon.get_pixels() == empty_icon
        assert clear.get_sensitive()
        first.set_active(False)
        assert not clear.get_sensitive()

        entry.set_text("Buy stamps")
        add.clicked()
        second_row = rows.get_children()[1]
        assert rows.get_children() == [first_row, second_row]
        first.set_active(True)

        def open_editor():
            edit.clicked()
            return next(
                window
                for window in Gtk.Window.list_toplevels()
                if isinstance(window, Gtk.Dialog)
                and window.get_transient_for() is popup
            )

        dialog = open_editor()
        field = dialog.get_focus()
        assert field.get_text() == "Send invoice"
        field.set_text(" ")
        assert not dialog.get_widget_for_response(Gtk.ResponseType.OK).get_sensitive()
        field.set_text("Discard this edit")
        dialog.response(Gtk.ResponseType.CANCEL)
        assert first.get_label() == "Send invoice"

        dialog = open_editor()
        field = dialog.get_focus()
        field.set_text(" Send final invoice ")
        field.activate()
        assert not dialog.get_visible()
        assert first.get_label() == "Send final invoice"
        assert first.get_active()
        assert rows.get_children() == [first_row, second_row]
        assert "1 unchecked item" in applet.item.name
        assert Config.load(path).applet_prefs["checklist"]["items"] == [
            {"text": "Send final invoice", "checked": True},
            {"text": "Buy stamps", "checked": False},
        ]
        clear.clicked()
        assert rows.get_children() == [second_row]
        assert "1 unchecked item" in applet.item.name

        applet.on_clicked()
        assert not applet.popup_visible
    finally:
        applet.stop()

    readded = ChecklistApplet(48, config=Config.load(path))
    try:
        readded.on_clicked()
        scrolled = readded._popup.get_child().get_child().get_children()[0]
        (remaining_row,) = scrolled.get_child().get_child().get_children()
        remaining, _edit = remaining_row.get_children()
        assert remaining.get_label() == "Buy stamps"
        assert not remaining.get_active()
    finally:
        readded.stop()
