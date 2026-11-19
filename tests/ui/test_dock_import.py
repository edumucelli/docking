"""Exercise source selection and the live Preferences import entry point."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.core.config import Config, PinnedEntry
from docking.platform.dock_import import DockImportSource, SourcePin
from docking.ui import dock_import, settings


def _source(name, applet, **preferences):
    return DockImportSource(
        name,
        pins=[SourcePin("applet", applet, (applet,))],
        settings=preferences,
    )


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
@pytest.mark.parametrize("accept", [True, False])
def test_review_requires_source_selection_and_respects_cancel(monkeypatch, accept):
    sources = [
        _source("Plank", "clock", position="top"),
        _source("Cairo-Dock", "trash", position="left"),
    ]

    def respond(dialog):
        combo = next(
            w
            for w in dialog.get_content_area().get_children()
            if isinstance(w, Gtk.ComboBoxText)
        )
        response = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        assert not response.get_sensitive()
        combo.set_active(1)
        assert response.get_sensitive()
        return Gtk.ResponseType.OK if accept else Gtk.ResponseType.CANCEL

    monkeypatch.setattr(dock_import, "_read_sources", lambda _kind: sources)
    # Avoid dispatching GLib sources left pending by other GUI tests.
    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    result = dock_import.review_dock_import(None, Config(), MagicMock(), first_run=True)
    if accept:
        assert result is not None
        assert result.config.pinned == ["applet://trash"]
        assert result.config.position == "left"
    else:
        assert result is None


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
@pytest.mark.parametrize(
    ("kind", "source_name", "missing_message"),
    [
        (
            "plank",
            "Plank / Plank Reloaded",
            "No Plank or Plank Reloaded setup was found.",
        ),
        ("cairo-dock", "Cairo-Dock", "No Cairo-Dock setup was found."),
    ],
)
@pytest.mark.parametrize("available", [True, False])
def test_preferences_review_opens_selected_dock(
    monkeypatch, kind, source_name, missing_message, available
):
    source = _source(source_name, "trash")
    read = MagicMock(return_value=[source] if available else [])
    monkeypatch.setattr(dock_import, "_read_sources", read)

    def respond(dialog):
        children = dialog.get_content_area().get_children()
        assert not any(isinstance(widget, Gtk.ComboBoxText) for widget in children)
        scroller = next(
            widget for widget in children if isinstance(widget, Gtk.ScrolledWindow)
        )
        buffer = scroller.get_child().get_buffer()
        text = buffer.get_text(*buffer.get_bounds(), False)
        response = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        assert response.get_sensitive() is available
        if available:
            assert text.startswith(source_name)
            return Gtk.ResponseType.OK
        assert text == missing_message
        return Gtk.ResponseType.CANCEL

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    review = dock_import.review_dock_import(
        None, Config(), MagicMock(), first_run=False, kind=kind
    )
    read.assert_called_once_with(kind)
    if available:
        assert review is not None
        assert review.config.pinned == ["applet://trash"]
    else:
        assert review is None


def test_preferences_import_saves_then_refreshes_live_config(tmp_path, monkeypatch):
    path = tmp_path / "dock.json"
    existing = PinnedEntry("applet", "applet://clock")
    Config(pinned=[existing]).save(path)
    config = Config.load(path)
    actions, model, tab, registry = (MagicMock() for _ in range(4))
    controller = settings.SettingsWindowController(
        parent=MagicMock(),
        actions=actions,
        model=model,
        config=config,
        application_registry=registry,
    )
    controller._window = MagicMock()
    changed = MagicMock()
    controller._bindings = [SimpleNamespace(config_attr="position", on_change=changed)]
    monkeypatch.setattr(controller, "_sync_widgets", MagicMock())
    monkeypatch.setattr(controller, "_rebuild_applet_tab", MagicMock())
    monkeypatch.setattr(
        settings,
        "review_dock_import",
        lambda *_args, **_kwargs: dock_import.prepare_dock_import(
            _source("Cairo-Dock", "trash", position="left", hide_delay_ms=25),
            config,
            registry,
            connectors=set(),
            theme_gap=0,
            first_run=False,
        ),
    )
    model.append_imported_pins.side_effect = lambda _pins: (
        Config.load(path).position == config.position == "left"
        or pytest.fail("Config was not committed before the model was updated")
    )

    controller._import_dock(tab, "cairo-dock")

    assert Config.load(path).pinned == [existing, "applet://trash"]
    assert Config.load(tmp_path / "dock.json.bak").pinned == [existing]
    model.append_imported_pins.assert_called_once_with(
        [PinnedEntry("applet", "applet://trash")]
    )
    changed.assert_called_once_with()
    actions.on_hide_mode_changed.assert_called_once_with()
