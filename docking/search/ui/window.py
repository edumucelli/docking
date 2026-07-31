# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Focusable GTK presentation for Docking's unified search results."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from docking.i18n import _
from docking.platform.launcher import Launcher
from docking.search.coordinator import ProviderError, SearchSnapshot
from docking.search.types import (
    SearchAction,
    SearchIdentity,
    SearchPreview,
    SearchResult,
)
from docking.search.ui.thumbnails import LoadedSearchImage, SearchImageCache
from docking.ui.popup_surface import (
    configure_transparent_startup_popup_window,
    wrap_startup_popup_content,
)

SEARCH_WINDOW_WIDTH = 680
SEARCH_WINDOW_HEIGHT = 470
SEARCH_ICON_SIZE = 32
ACTION_PANEL_WIDTH = 320
PREVIEW_PANEL_WIDTH = 320
PREVIEW_IMAGE_MAX_WIDTH = 280
PREVIEW_IMAGE_MAX_HEIGHT = 250


def _format_file_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class _ResultRow(Gtk.ListBoxRow):
    def __init__(
        self,
        result: SearchResult,
        *,
        launcher: Launcher,
    ) -> None:
        super().__init__()
        self.result = result
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_border_width(8)
        icon = launcher.load_icon(
            icon_name=result.icon_name or "system-search",
            size=SEARCH_ICON_SIZE,
        )
        image = Gtk.Image.new_from_pixbuf(icon) if icon is not None else Gtk.Image()
        image.set_pixel_size(SEARCH_ICON_SIZE)
        image.set_size_request(SEARCH_ICON_SIZE, SEARCH_ICON_SIZE)
        self.image = image
        box.pack_start(image, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=result.title)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        labels.pack_start(title, False, False, 0)
        subtitle_text = result.description
        if result.state:
            subtitle_text = (
                f"{result.state} · {subtitle_text}" if subtitle_text else result.state
            )
        subtitle = Gtk.Label(label=subtitle_text)
        subtitle.set_xalign(0.0)
        subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle.get_style_context().add_class("dim-label")
        labels.pack_start(subtitle, False, False, 0)
        box.pack_start(labels, True, True, 0)

        source = Gtk.Label(label=result.source)
        source.get_style_context().add_class("dim-label")
        box.pack_end(source, False, False, 0)
        self.add(box)
        self.get_accessible().set_name(result.title)
        self.get_accessible().set_description(subtitle_text)


class _ActionRow(Gtk.ListBoxRow):
    def __init__(self, action: SearchAction) -> None:
        super().__init__()
        self.action = action
        label = Gtk.Label(label=action.label)
        label.set_xalign(0.0)
        label.set_margin_start(8)
        label.set_margin_end(8)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        if action.verb in {"close", "remove"}:
            label.get_style_context().add_class("error")
        self.add(label)
        self.get_accessible().set_name(action.label)


class SearchWindow:
    """Own one reusable search toplevel and its keyboard interaction."""

    def __init__(
        self,
        *,
        launcher: Launcher,
        on_query_changed: Callable[[str], None],
        on_result_selected: Callable[[SearchIdentity | None], None],
        on_result_activated: Callable[[SearchResult], None],
        on_action_activated: Callable[[SearchResult, SearchAction], None],
        on_hidden: Callable[[], None],
        on_refine_requested: Callable[[SearchResult | None], None] | None = None,
        on_completion_requested: Callable[[], bool] | None = None,
        dynamic_preview_loader: (
            Callable[[SearchPreview, int, int], LoadedSearchImage | None] | None
        ) = None,
        preview_resolver: Callable[[SearchResult], SearchPreview | None] | None = None,
    ) -> None:
        self._launcher = launcher
        self._on_query_changed = on_query_changed
        self._on_result_selected = on_result_selected
        self._on_result_activated = on_result_activated
        self._on_action_activated = on_action_activated
        self._on_hidden = on_hidden
        self._on_refine_requested = on_refine_requested
        self._on_completion_requested = on_completion_requested
        self._dynamic_preview_loader = dynamic_preview_loader
        self._preview_resolver = preview_resolver
        self._results: tuple[SearchResult, ...] = ()
        self._selected_identity: SearchIdentity | None = None
        self._syncing_query = False
        self._syncing_results = False
        self._query_hint = ""
        self._actions_result: SearchResult | None = None
        self._image_cache = SearchImageCache()
        self._thumbnail_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="docking-search-thumbnail",
        )
        self._pending_thumbnails: set[tuple[str, int, int]] = set()
        self._result_generation = 0

        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title(_("Docking Search"))
        window.set_default_size(SEARCH_WINDOW_WIDTH, SEARCH_WINDOW_HEIGHT)
        window.set_decorated(False)
        window.set_resizable(True)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_accept_focus(True)
        window.set_focus_on_map(True)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        configure_transparent_startup_popup_window(window)
        window.get_accessible().set_name(_("Docking Search"))
        window.connect("delete-event", self._on_delete_event)
        window.connect("key-press-event", self._on_key_press)
        window.connect("hide", self._handle_hidden)
        self.window = window

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(
            _("Search apps, files, calculations, or the web...")
        )
        self.search_entry.set_margin_start(14)
        self.search_entry.set_margin_end(14)
        self.search_entry.set_margin_top(12)
        self.search_entry.set_margin_bottom(12)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.activate_selected())
        self.search_entry.connect("stop-search", lambda *_: self.hide())
        outer.pack_start(self.search_entry, False, False, 0)
        outer.pack_start(Gtk.Separator(), False, False, 0)

        overlay = Gtk.Overlay()
        self.results_list = Gtk.ListBox()
        self.results_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.empty_label = Gtk.Label()
        self.empty_label.get_style_context().add_class("dim-label")
        self.empty_label.set_margin_top(48)
        self.empty_label.set_valign(Gtk.Align.START)
        self.results_list.set_placeholder(self.empty_label)
        self.results_list.connect("row-selected", self._on_row_selected)
        self.results_list.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.results_list)
        overlay.add(scroller)

        self.action_frame = Gtk.Frame()
        self.action_frame.set_halign(Gtk.Align.END)
        self.action_frame.set_valign(Gtk.Align.END)
        self.action_frame.set_margin_end(10)
        self.action_frame.set_margin_bottom(10)
        self.action_frame.set_size_request(ACTION_PANEL_WIDTH, -1)
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        action_box.set_border_width(8)
        action_title = Gtk.Label(label=_("Actions"))
        action_title.set_xalign(0.0)
        action_box.pack_start(action_title, False, False, 0)
        self.action_filter = Gtk.SearchEntry()
        self.action_filter.set_placeholder_text(_("Filter actions..."))
        self.action_filter.connect("search-changed", self._filter_actions)
        self.action_filter.connect("activate", self._activate_filtered_action)
        action_box.pack_start(self.action_filter, False, False, 0)
        self.actions_list = Gtk.ListBox()
        self.actions_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.actions_list.connect("row-activated", self._on_action_row_activated)
        action_box.pack_start(self.actions_list, False, False, 0)
        self.action_frame.add(action_box)
        overlay.add_overlay(self.action_frame)

        self.preview_frame = Gtk.Frame()
        self.preview_frame.set_halign(Gtk.Align.END)
        self.preview_frame.set_valign(Gtk.Align.FILL)
        self.preview_frame.set_margin_top(10)
        self.preview_frame.set_margin_end(10)
        self.preview_frame.set_margin_bottom(10)
        self.preview_frame.set_size_request(PREVIEW_PANEL_WIDTH, -1)
        self.preview_frame.get_style_context().add_class("view")
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        preview_box.set_border_width(10)
        self.preview_title = Gtk.Label()
        self.preview_title.set_xalign(0.0)
        self.preview_title.set_line_wrap(True)
        preview_box.pack_start(self.preview_title, False, False, 0)
        preview_box.pack_start(Gtk.Separator(), False, False, 0)
        self.preview_image = Gtk.Image()
        self.preview_image.set_halign(Gtk.Align.CENTER)
        self.preview_image.set_valign(Gtk.Align.START)
        self.preview_image.set_no_show_all(True)
        preview_box.pack_start(self.preview_image, False, False, 0)
        self.preview_metadata = Gtk.Label()
        self.preview_metadata.set_xalign(0.0)
        self.preview_metadata.get_style_context().add_class("dim-label")
        self.preview_metadata.set_no_show_all(True)
        preview_box.pack_start(self.preview_metadata, False, False, 0)
        self.preview_body = Gtk.Label()
        self.preview_body.set_xalign(0.0)
        self.preview_body.set_yalign(0.0)
        self.preview_body.set_line_wrap(True)
        self.preview_body.set_selectable(True)
        preview_scroller = Gtk.ScrolledWindow()
        preview_scroller.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        preview_scroller.add(self.preview_body)
        preview_box.pack_start(preview_scroller, True, True, 0)
        self.preview_frame.add(preview_box)
        overlay.add_overlay(self.preview_frame)
        outer.pack_start(overlay, True, True, 0)

        outer.pack_start(Gtk.Separator(), False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_border_width(8)
        self.status_label = Gtk.Label()
        self.status_label.set_xalign(0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        footer.pack_start(self.status_label, True, True, 0)
        self.primary_button = Gtk.Button()
        self.primary_button.connect("clicked", lambda *_: self.activate_selected())
        footer.pack_end(self.primary_button, False, False, 0)
        self.actions_button = Gtk.Button(label=_("Actions  Ctrl+J"))
        self.actions_button.set_tooltip_text(
            _(
                "Press Tab to complete a keyword or Ctrl+Right to refine "
                "the selected result."
            )
        )
        self.actions_button.connect("clicked", lambda *_: self.toggle_actions())
        footer.pack_end(self.actions_button, False, False, 0)
        self.preview_button = Gtk.Button(label=_("Preview  Ctrl+P"))
        self.preview_button.connect("clicked", lambda *_: self.toggle_preview())
        footer.pack_end(self.preview_button, False, False, 0)
        outer.pack_end(footer, False, False, 0)
        self.surface = wrap_startup_popup_content(outer)
        window.add(self.surface)

    @property
    def visible(self) -> bool:
        return bool(self.window.get_visible())

    def present(
        self,
        *,
        initial_query: str = "",
        activation_context: dict[str, object] | None = None,
    ) -> None:
        context = activation_context or {}
        startup_id = str(
            context.get("XDG_ACTIVATION_TOKEN")
            or context.get("DESKTOP_STARTUP_ID")
            or ""
        ).strip()
        if startup_id:
            self.window.set_startup_id(startup_id)
        timestamp_value = context.get("timestamp", 0)
        timestamp = (
            timestamp_value
            if isinstance(timestamp_value, int)
            and not isinstance(timestamp_value, bool)
            and timestamp_value > 0
            else 0
        )
        self.set_query(initial_query)
        self.window.show_all()
        self.action_frame.hide()
        self.preview_frame.hide()
        self.search_entry.grab_focus()
        self.search_entry.set_position(-1)
        if timestamp:
            self.window.present_with_time(timestamp)
        else:
            self.window.present()

    def hide(self) -> None:
        self.window.hide()

    def destroy(self) -> None:
        self._result_generation += 1
        self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)
        self._image_cache.clear()
        self.window.destroy()

    def set_query(self, text: str) -> None:
        if self.search_entry.get_text() == text:
            return
        self._syncing_query = True
        try:
            self.search_entry.set_text(text)
        finally:
            self._syncing_query = False

    def set_query_hint(self, hint: str) -> None:
        self._query_hint = hint

    def update(self, snapshot: SearchSnapshot) -> None:
        self._results = snapshot.results
        self._selected_identity = snapshot.selected_identity
        self._result_generation += 1
        result_generation = self._result_generation
        self.empty_label.set_label(
            _("Try app, win, file, web, cmd, or 10 km to mi")
            if snapshot.query.is_empty
            else _("No matching results")
        )
        self._syncing_results = True
        try:
            for child in list(self.results_list.get_children()):
                self.results_list.remove(child)
            selected_row: _ResultRow | None = None
            for result in self._results:
                row = _ResultRow(
                    result,
                    launcher=self._launcher,
                )
                self.results_list.add(row)
                if (
                    result.preview is not None
                    and result.preview.kind == "image"
                    and result.preview.target
                ):
                    self._request_row_thumbnail(
                        row=row,
                        path=result.preview.target,
                        generation=result_generation,
                    )
                if result.identity == snapshot.selected_identity:
                    selected_row = row
            self.results_list.show_all()
            if selected_row is not None:
                self.results_list.select_row(selected_row)
        finally:
            self._syncing_results = False
        self._sync_actions()
        if self.preview_frame.get_visible():
            self._sync_preview()
        self._sync_status(
            snapshot.pending_provider_ids,
            snapshot.errors,
            query_empty=snapshot.query.is_empty,
        )

    def _request_row_thumbnail(
        self,
        *,
        row: _ResultRow,
        path: str,
        generation: int,
    ) -> None:
        pending_key = (path, SEARCH_ICON_SIZE, generation)
        if pending_key in self._pending_thumbnails:
            return
        self._pending_thumbnails.add(pending_key)
        future = self._thumbnail_executor.submit(
            self._image_cache.load,
            path=path,
            max_width=SEARCH_ICON_SIZE,
            max_height=SEARCH_ICON_SIZE,
        )
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._apply_row_thumbnail,
                completed,
                row,
                pending_key,
                generation,
            )
        )

    def _apply_row_thumbnail(
        self,
        future: Future[LoadedSearchImage | None],
        row: _ResultRow,
        pending_key: tuple[str, int, int],
        generation: int,
    ) -> bool:
        self._pending_thumbnails.discard(pending_key)
        if generation != self._result_generation or row.get_parent() is None:
            return False
        try:
            loaded = future.result()
        except Exception:
            return False
        if loaded is not None:
            row.image.set_from_pixbuf(loaded.pixbuf)
        return False

    def selected_result(self) -> SearchResult | None:
        row = self.results_list.get_selected_row()
        return row.result if isinstance(row, _ResultRow) else None

    def activate_selected(self) -> None:
        result = self.selected_result()
        if result is not None:
            self._on_result_activated(result)

    def toggle_actions(self) -> None:
        if self.action_frame.get_visible():
            self._hide_actions(focus_search=True)
            return
        result = self.selected_result()
        if result is None or not result.actions:
            return
        self.show_actions_for(result)

    def show_actions_for(self, result: SearchResult) -> None:
        self._hide_preview()
        self._sync_actions(result)
        self.action_frame.show_all()
        self.action_filter.grab_focus()

    def _hide_actions(self, *, focus_search: bool) -> None:
        self.action_frame.hide()
        self.action_filter.set_text("")
        self._actions_result = None
        if focus_search:
            self.search_entry.grab_focus()

    def toggle_preview(self) -> None:
        if self.preview_frame.get_visible():
            self._hide_preview()
            return
        if self.selected_result() is None:
            return
        self._hide_actions(focus_search=False)
        self.preview_frame.show_all()
        self._sync_preview()
        self.search_entry.grab_focus()

    def _hide_preview(self) -> None:
        self.preview_frame.hide()

    def _sync_preview(self) -> None:
        result = self.selected_result()
        if result is None:
            self.preview_title.set_label("")
            self.preview_body.set_label("")
            self.preview_image.hide()
            self.preview_metadata.hide()
            return
        resolved_preview = (
            self._preview_resolver(result)
            if self._preview_resolver is not None
            else None
        )
        preview = (
            resolved_preview
            or result.preview
            or SearchPreview(
                title=result.title,
                body=result.description or result.state or result.source,
            )
        )
        self.preview_title.set_label(preview.title)
        self.preview_body.set_label(preview.body)
        preview_body_style = self.preview_body.get_style_context()
        preview_body_style.remove_class("monospace")
        if preview.kind == "source":
            preview_body_style.add_class("monospace")
        self.preview_image.hide()
        self.preview_metadata.hide()
        if preview.kind == "image" and preview.target:
            self._sync_image_preview(preview.target)
        elif self._dynamic_preview_loader is not None:
            loaded = self._dynamic_preview_loader(
                preview,
                PREVIEW_IMAGE_MAX_WIDTH,
                PREVIEW_IMAGE_MAX_HEIGHT,
            )
            if loaded is not None:
                self._show_loaded_preview(loaded)

    def _sync_image_preview(self, path: str) -> None:
        loaded = self._image_cache.load(
            path=path,
            max_width=PREVIEW_IMAGE_MAX_WIDTH,
            max_height=PREVIEW_IMAGE_MAX_HEIGHT,
        )
        if loaded is None:
            self.preview_metadata.set_label(_("Unable to decode image preview"))
            self.preview_metadata.show()
            return
        self._show_loaded_preview(loaded)

    def _show_loaded_preview(self, loaded: LoadedSearchImage) -> None:
        self.preview_image.set_from_pixbuf(loaded.pixbuf)
        self.preview_image.show()
        if loaded.file_size >= 0:
            metadata = _("{width} × {height} · {format} · {size}").format(
                width=loaded.width,
                height=loaded.height,
                format=loaded.format_name,
                size=_format_file_size(loaded.file_size),
            )
        else:
            metadata = _("{width} × {height} · {format}").format(
                width=loaded.width,
                height=loaded.height,
                format=loaded.format_name,
            )
        self.preview_metadata.set_label(metadata)
        self.preview_metadata.show()

    def _sync_actions(self, result: SearchResult | None = None) -> None:
        for child in list(self.actions_list.get_children()):
            self.actions_list.remove(child)
        result = result or self.selected_result()
        self._actions_result = result
        if result is not None:
            for result_action in result.actions:
                self.actions_list.add(_ActionRow(result_action))
        self.actions_list.show_all()
        first = self.actions_list.get_row_at_index(0)
        if first is not None:
            self.actions_list.select_row(first)
        self._filter_actions(self.action_filter)
        self.primary_button.set_label(
            result.actions[0].label if result and result.actions else _("Open")
        )
        self.primary_button.set_sensitive(bool(result and result.actions))
        self.actions_button.set_sensitive(bool(result and result.actions))

    def _sync_status(
        self,
        pending_provider_ids: Sequence[str],
        errors: Sequence[ProviderError],
        *,
        query_empty: bool,
    ) -> None:
        if self._results:
            text = _("{count} results").format(count=len(self._results))
        elif query_empty:
            text = _("Type to search")
        else:
            text = _("No results")
        if pending_provider_ids:
            text = f"{text} · " + _("Searching...")
        elif errors:
            text = f"{text} · " + _("Some sources failed")
        if self._query_hint:
            text = f"{self._query_hint} · {text}"
        self.status_label.set_label(text)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if not self._syncing_query:
            self._on_query_changed(entry.get_text())

    def _on_row_selected(
        self,
        _list_box: Gtk.ListBox,
        row: Gtk.ListBoxRow | None,
    ) -> None:
        identity = row.result.identity if isinstance(row, _ResultRow) else None
        self._selected_identity = identity
        if self._syncing_results:
            return
        self._on_result_selected(identity)
        self._sync_actions()
        if self.preview_frame.get_visible():
            self._sync_preview()

    def _on_row_activated(
        self,
        _list_box: Gtk.ListBox,
        row: Gtk.ListBoxRow,
    ) -> None:
        if isinstance(row, _ResultRow):
            self._on_result_activated(row.result)

    def _on_action_row_activated(
        self,
        _list_box: Gtk.ListBox,
        row: Gtk.ListBoxRow,
    ) -> None:
        result = self._actions_result or self.selected_result()
        if result is not None and isinstance(row, _ActionRow):
            self._on_action_activated(result, row.action)

    def _filter_actions(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().casefold()
        first_visible = None
        for row in self.actions_list.get_children():
            if isinstance(row, _ActionRow):
                visible = not query or query in row.action.label.casefold()
                row.set_visible(visible)
                if visible and first_visible is None:
                    first_visible = row
        if first_visible is not None:
            self.actions_list.select_row(first_visible)
        else:
            self.actions_list.unselect_all()

    def _activate_filtered_action(self, *_args: object) -> None:
        row = self.actions_list.get_selected_row()
        if row is not None and row.get_visible():
            self._on_action_row_activated(self.actions_list, row)

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.action_frame.get_visible():
                self._hide_actions(focus_search=True)
            elif self.preview_frame.get_visible():
                self._hide_preview()
                self.search_entry.grab_focus()
            else:
                self.hide()
            return True
        if (
            event.keyval in {Gdk.KEY_j, Gdk.KEY_J}
            and event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            self.toggle_actions()
            return True
        if (
            event.keyval in {Gdk.KEY_p, Gdk.KEY_P}
            and event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            self.toggle_preview()
            return True
        if event.keyval == Gdk.KEY_Tab:
            if (
                not event.state & Gdk.ModifierType.SHIFT_MASK
                and self.search_entry.is_focus()
                and self._on_completion_requested is not None
            ):
                return self._on_completion_requested()
            return False
        if (
            event.keyval == Gdk.KEY_Right
            and event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            result = self.selected_result()
            if result is not None and self._on_refine_requested is not None:
                self._on_refine_requested(result)
                return True
        if event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            if self.action_frame.get_visible():
                row = self.actions_list.get_selected_row()
                if row is not None and row.get_visible():
                    self._on_action_row_activated(self.actions_list, row)
            else:
                self.activate_selected()
            return True
        if event.keyval in {Gdk.KEY_Up, Gdk.KEY_Down}:
            target = (
                self.actions_list
                if self.action_frame.get_visible()
                else self.results_list
            )
            rows = [
                row
                for row in target.get_children()
                if isinstance(row, Gtk.ListBoxRow) and row.get_visible()
            ]
            current = target.get_selected_row()
            index = rows.index(current) if current in rows else -1
            delta = -1 if event.keyval == Gdk.KEY_Up else 1
            next_index = min(max(index + delta, 0), len(rows) - 1)
            if rows:
                target.select_row(rows[next_index])
            return True
        return False

    def _on_delete_event(self, *_args: object) -> bool:
        self.hide()
        return True

    def _handle_hidden(self, *_args: object) -> None:
        self._hide_actions(focus_search=False)
        self._hide_preview()
        self._on_hidden()


__all__ = ["SearchWindow"]
