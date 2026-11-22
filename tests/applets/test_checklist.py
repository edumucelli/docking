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

        entry.set_text(" Send invoice ")
        entry.activate()
        first = rows.get_children()[0]
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
        second = rows.get_children()[1]
        assert rows.get_children() == [first, second]
        first.set_active(True)
        assert Config.load(path).applet_prefs["checklist"]["items"] == [
            {"text": "Send invoice", "checked": True},
            {"text": "Buy stamps", "checked": False},
        ]
        clear.clicked()
        assert rows.get_children() == [second]
        assert "1 unchecked item" in applet.item.name

        applet.on_clicked()
        assert not applet.popup_visible
    finally:
        applet.stop()

    readded = ChecklistApplet(48, config=Config.load(path))
    try:
        readded.on_clicked()
        scrolled = readded._popup.get_child().get_child().get_children()[0]
        (remaining,) = scrolled.get_child().get_child().get_children()
        assert remaining.get_label() == "Buy stamps"
        assert not remaining.get_active()
    finally:
        readded.stop()
