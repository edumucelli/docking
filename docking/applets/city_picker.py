"""City selection dialog shared by Weather and Sunrise."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.cities import cached_cities, search_cities
from docking.applets.popup import prepare_dialog_content
from docking.i18n import _

CITY_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
CITY_SEARCH_MIN_CHARS = 2
CITY_SEARCH_RESULT_LIMIT = 10


def show_city_dialog(on_selected: Callable[[str, float, float], None]) -> None:
    dialog = Gtk.Dialog(
        title=_("Search for the city"),
        modal=True,
        destroy_with_parent=True,
    )
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.connect("response", lambda dlg, _response: dlg.destroy())
    box = prepare_dialog_content(
        dialog=dialog,
        width=CITY_DIALOG_WIDTH_PX,
        spacing=DIALOG_CONTENT_SPACING_PX,
        margin=DIALOG_HORIZONTAL_MARGIN_PX,
    )

    entry = Gtk.Entry()
    entry.set_placeholder_text(_("Type city name..."))
    box.pack_start(entry, False, False, 0)

    completion = Gtk.EntryCompletion()
    store = Gtk.ListStore(str, float, float)  # display, lat, lng
    completion.set_model(store)
    completion.set_text_column(0)
    completion.set_minimum_key_length(CITY_SEARCH_MIN_CHARS)

    def on_changed(changed_entry: Gtk.Entry) -> None:
        text = changed_entry.get_text()
        store.clear()
        if len(text) < CITY_SEARCH_MIN_CHARS:
            return
        for city in search_cities(
            text,
            cached_cities(),
            limit=CITY_SEARCH_RESULT_LIMIT,
        ):
            store.append([city.display, city.lat, city.lng])

    def on_match_selected(
        _completion: Gtk.EntryCompletion,
        model: Gtk.TreeModel,
        tree_iter: Gtk.TreeIter,
    ) -> bool:
        display = model.get_value(tree_iter, 0)
        lat = model.get_value(tree_iter, 1)
        lng = model.get_value(tree_iter, 2)
        on_selected(display, lat, lng)

        def destroy_dialog() -> bool:
            dialog.destroy()
            return False

        GLib.idle_add(destroy_dialog)
        return True

    entry.connect("changed", on_changed)
    completion.connect("match-selected", on_match_selected)
    entry.set_completion(completion)

    dialog.show_all()
    entry.grab_focus()
