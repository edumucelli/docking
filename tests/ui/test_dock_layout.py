"""Draft ordering and GTK responses for the Dock Layout Editor."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.core.config import Config, PinnedEntry
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, DockItem
from docking.platform.applications.types import ApplicationOrigin
from docking.platform.model import LayoutConflictError
from docking.ui.dock_layout import DockLayoutEditor, _LayoutDraft


def test_anchored_draft_reorders_within_sections_and_keeps_unanchored_slots():
    app = PinnedEntry(APP_KIND, "editor.desktop")
    first = PinnedEntry(APPLET_KIND, "applet://separator#0")
    file = PinnedEntry(FILE_KIND, "file:///tmp/document.txt")
    last = PinnedEntry(APPLET_KIND, "applet://separator#1")
    config = Config(
        pinned=[first, app, file, last], anchor_applets=True, anchor_files=True
    )
    draft = _LayoutDraft(config)

    assert draft.display_order() == [1, 2, 0, 3]
    assert draft.neighbor(0, -1) is None
    assert draft.neighbor(0, 1) == 3
    assert draft.neighbor(2, 1) is None
    assert draft.add(app, None) is None
    folder = PinnedEntry(FOLDER_KIND, "file:///tmp/folder")
    assert draft.add(folder, 0) == 3
    assert draft.entries == [first, app, file, folder, last]
    assert config.pinned == [first, app, file, last]


@pytest.fixture
def editor():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required")
    parent = Gtk.Window()
    config = Config(pinned=[PinnedEntry(APP_KIND, "editor.desktop")])
    model = MagicMock(pinned_items=[])
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(name="Editor")
    dialog = DockLayoutEditor(parent, config, model, registry)
    try:
        yield dialog, config, model, registry
    finally:
        parent.destroy()


@pytest.mark.parametrize("apply", [False, True])
def test_gtk_draft_editing_and_response(editor, monkeypatch, apply):
    layout, config, model, _registry = editor
    (original,) = config.pinned
    model.apply_pinned_layout.return_value = ()
    first = PinnedEntry(APPLET_KIND, "applet://separator#0")
    second = PinnedEntry(APPLET_KIND, "applet://separator#1")

    def respond(dialog):
        button = dialog.get_widget_for_response(Gtk.ResponseType.APPLY)
        assert not button.get_sensitive()
        layout._add_separator()
        layout._add_separator()
        layout._up.clicked()
        assert layout._draft.entries == [original, second, first]
        assert config.pinned == [original]
        assert button.get_sensitive()
        return Gtk.ResponseType.APPLY if apply else Gtk.ResponseType.CANCEL

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    assert layout.run() is apply
    if apply:
        model.apply_pinned_layout.assert_called_once_with(
            [original, second, first],
            expected_pins=(original,),
            expected_anchors=(False, False),
        )
    else:
        model.apply_pinned_layout.assert_not_called()


def test_conflict_keeps_draft_until_explicit_reload(editor, monkeypatch):
    layout, config, model, _registry = editor
    model.apply_pinned_layout.side_effect = LayoutConflictError
    replacement = PinnedEntry(APP_KIND, "new.desktop")

    def respond(dialog):
        if model.apply_pinned_layout.call_count == 0:
            layout._add_separator()
            return Gtk.ResponseType.APPLY
        assert len(layout._draft.entries) == 2
        assert layout._reload.get_visible()
        assert not dialog.get_widget_for_response(
            Gtk.ResponseType.APPLY
        ).get_sensitive()
        config.pinned = [replacement]
        layout._reload.clicked()
        assert layout._draft.entries == [replacement]
        assert not layout._reload.get_visible()
        return Gtk.ResponseType.CANCEL

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    assert not layout.run()


def test_applet_rows_use_catalog_names_and_live_moves_are_canceled(editor, monkeypatch):
    layout, config, model, _registry = editor
    original = (PinnedEntry(APPLET_KIND, "applet://clock"), config.pinned[0])
    config.pinned = list(original)
    model.pinned_items = [
        DockItem("applet://clock", kind=APPLET_KIND, name="Saturday\nAlarm at 09:00")
    ]
    layout._reload.clicked()

    def respond(_dialog):
        row = layout._list.get_row_at_index(0)
        labels = [
            widget.get_text()
            for widget in row.get_child().get_children()
            if isinstance(widget, Gtk.Label)
        ]
        assert labels == ["Clock"]
        assert row.get_tooltip_text() is None
        layout._down.clicked()
        model.preview_pinned_order.assert_called_with(
            list(reversed(original)),
            expected_pins=original,
            expected_anchors=(False, False),
        )
        return Gtk.ResponseType.CANCEL

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    assert not layout.run()
    model.clear_pinned_order_preview.assert_called_once_with()
    model.apply_pinned_layout.assert_not_called()


def test_file_picker_cancel_never_changes_draft(editor, monkeypatch):
    layout, config, model, _registry = editor
    chooser = MagicMock()
    chooser.run.return_value = Gtk.ResponseType.CANCEL
    monkeypatch.setattr(Gtk, "FileChooserNative", lambda **_kwargs: chooser)

    layout._add_file(folder=False)

    assert layout._draft.entries == config.pinned
    chooser.destroy.assert_called_once_with()
    chooser.get_filename.assert_not_called()
    model.apply_pinned_layout.assert_not_called()


def test_failed_activation_keeps_saved_applet_visible(editor, monkeypatch):
    layout, config, model, _registry = editor
    failed = PinnedEntry(APPLET_KIND, "applet://separator#0")

    def save(entries, **_expected):
        config.pinned = entries[:]
        return (failed,)

    def respond(dialog):
        if model.apply_pinned_layout.call_count == 0:
            layout._add_separator()
            return Gtk.ResponseType.APPLY
        assert "Layout saved" in layout._status.get_text()
        row = layout._list.get_row_at_index(1)
        assert any(
            isinstance(widget, Gtk.Label) and widget.get_text() == "Unavailable"
            for widget in row.get_child().get_children()
        )
        assert not dialog.get_widget_for_response(
            Gtk.ResponseType.APPLY
        ).get_sensitive()
        return Gtk.ResponseType.CANCEL

    model.apply_pinned_layout.side_effect = save
    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    assert layout.run()
    assert failed in config.pinned


def test_application_picker_stages_an_installed_application(editor, monkeypatch):
    layout, config, model, registry = editor
    original = config.pinned[:]
    registry.snapshot.return_value = [
        SimpleNamespace(
            name="Editor",
            desktop_id="editor.desktop",
            origin=ApplicationOrigin.INSTALLED,
        ),
        SimpleNamespace(
            name="Browser",
            desktop_id="browser.desktop",
            origin=ApplicationOrigin.INSTALLED,
        ),
    ]

    def pick(dialog):
        scroller = next(
            child
            for child in dialog.get_content_area().get_children()
            if isinstance(child, Gtk.ScrolledWindow)
        )
        view = scroller.get_child()
        assert len(view.get_model()) == 1
        view.get_selection().select_path(Gtk.TreePath.new_from_indices([0]))
        assert dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).get_sensitive()
        return Gtk.ResponseType.ACCEPT

    monkeypatch.setattr(Gtk.Dialog, "run", pick)
    layout._add_catalog(APP_KIND)

    assert config.pinned == original
    assert layout._draft.entries == [
        *original,
        PinnedEntry(APP_KIND, "browser.desktop"),
    ]
    model.apply_pinned_layout.assert_not_called()
    registry.subscribe.return_value.assert_called_once_with()
