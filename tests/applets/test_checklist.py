"""Tests for the Checklist applet."""

import pytest
from gi.repository import Gdk, Gtk, Pango

from docking.applets import load_applet_class
from docking.applets.base import load_catalog_icon
from docking.applets.checklist.applet import ChecklistApplet
from docking.applets.checklist.state import ChecklistItem, items_from_prefs
from docking.core.config import Config


def test_catalog_and_saved_items():
    assert load_applet_class("checklist") is ChecklistApplet
    assert load_catalog_icon(applet_id="checklist", size=32) is not None
    assert items_from_prefs(
        [
            None,
            {"text": " "},
            {"text": " Send invoice ", "checked": True},
            {"text": "Buy stamps"},
        ]
    ) == [ChecklistItem("Buy stamps"), ChecklistItem("Send invoice", checked=True)]


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
        first, text = first_row.get_children()
        item_text = text.get_visible_child()
        label = item_text.get_child()
        assert label.get_text() == "Send invoice"
        assert entry.get_text() == ""
        assert "1 unchecked item" in applet.item.name
        assert applet.item.icon.get_pixels() != empty_icon

        first.set_active(True)
        assert first.get_visible()
        assert label.get_attributes().get_iterator().get(Pango.AttrType.STRIKETHROUGH)
        assert applet.item.icon.get_pixels() == empty_icon
        assert clear.get_sensitive()
        first.set_active(False)
        assert not clear.get_sensitive()
        assert (
            not label.get_attributes().get_iterator().get(Pango.AttrType.STRIKETHROUGH)
        )

        entry.set_text("Buy stamps")
        add.clicked()
        second_row = rows.get_children()[1]
        assert rows.get_children() == [first_row, second_row]
        first.set_active(True)
        assert rows.get_children() == [second_row, first_row]

        item_text.clicked()
        field = text.get_visible_child()
        assert popup.get_focus() is field
        assert field.get_text() == "Send invoice"
        field.set_text(" ")
        field.activate()
        assert text.get_visible_child() is field
        field.set_text("Discard this edit")
        escape = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        escape.keyval = Gdk.KEY_Escape
        assert field.emit("key-press-event", escape)
        assert text.get_visible_child() is item_text
        assert label.get_text() == "Send invoice"

        item_text.clicked()
        field.set_text(" Send final invoice ")
        field.activate()
        assert text.get_visible_child() is item_text
        assert label.get_text() == "Send final invoice"
        assert first.get_active()
        assert rows.get_children() == [second_row, first_row]
        assert "1 unchecked item" in applet.item.name
        assert Config.load(path).applet_prefs["checklist"]["items"] == [
            {"text": "Buy stamps", "checked": False},
            {"text": "Send final invoice", "checked": True},
        ]

        entry.set_text("Call supplier")
        add.clicked()
        third_row = rows.get_children()[1]
        assert rows.get_children() == [second_row, third_row, first_row]
        second, _text = second_row.get_children()
        second.set_active(True)
        assert rows.get_children() == [third_row, first_row, second_row]
        second.set_active(False)
        assert rows.get_children() == [third_row, second_row, first_row]
        second.set_active(True)
        clear.clicked()
        assert rows.get_children() == [third_row]
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
        remaining, remaining_text = remaining_row.get_children()
        assert (
            remaining_text.get_visible_child().get_child().get_text() == "Call supplier"
        )
        assert not remaining.get_active()
    finally:
        readded.stop()
