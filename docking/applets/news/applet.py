"""GTK lifecycle and source-picker UI for the News RSS applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.news import meta
from docking.applets.news.catalog import (
    CachedNewsCatalog,
    NewsCatalog,
    load_cached_catalog,
    refresh_catalog,
)
from docking.applets.news.countries import (
    country_code_for_locale,
    country_name,
)
from docking.applets.news.render import render_icon
from docking.applets.news.state import (
    MAX_SOURCES,
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    NewsArticle,
    NewsSource,
    add_source,
    article_age,
    build_tooltip,
    fetch_news_articles,
    normalize_active_index,
    prefs_from_mapping,
    prefs_payload,
    remove_source,
    source_detail,
    source_label,
)
from docking.applets.popup import prepare_dialog_content
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.ui.tooltip import parse_timestamp

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="news"), applet_id=meta.id)

PICKER_WIDTH_PX = 760
PICKER_HEIGHT_PX = 500
PICKER_MARGIN_PX = 12
PICKER_SPACING_PX = 10
COUNTRY_PANE_WIDTH_PX = 230


class _CountryRow(Gtk.ListBoxRow):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code
        label = Gtk.Label(label=country_name(code))
        label.set_xalign(0.0)
        label.set_margin_start(8)
        label.set_margin_end(8)
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        self.add(label)


class _SourceRow(Gtk.ListBoxRow):
    def __init__(self, source: NewsSource) -> None:
        super().__init__()
        self.source = source
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        primary = Gtk.Label(label=source_label(source))
        primary.set_xalign(0.0)
        primary.set_line_wrap(True)
        secondary = Gtk.Label(label=_source_row_detail(source))
        secondary.set_xalign(0.0)
        secondary.set_line_wrap(True)
        secondary.get_style_context().add_class("dim-label")
        box.pack_start(primary, False, False, 0)
        box.pack_start(secondary, False, False, 0)
        self.add(box)


class NewsApplet(Applet):
    """Browse RSS headlines from country-organized news publications."""

    id = meta.id
    name = _("News")
    icon_name = "internet-news-reader"

    def __init__(self, icon_size: int, config: Config) -> None:
        prefs = prefs_from_mapping(config.applet_prefs.get(meta.id, {}))
        self._sources = list(prefs.sources)
        self._active_source_index = prefs.active_source_index
        self._articles = list(prefs.articles)
        self._active_article_index = prefs.active_article_index
        self._fetched_at = parse_timestamp(prefs.fetched_at)
        self._loading = False
        self._error = ""
        self._refresh_timer_id = 0
        self._startup_fetch_timer_id = 0
        self._fetch_request_id = 0
        self._worker = BackgroundWorker(logger=log)

        self._source_dialog: Gtk.Dialog | None = None
        self._country_list: Gtk.ListBox | None = None
        self._source_list: Gtk.ListBox | None = None
        self._country_search: Gtk.SearchEntry | None = None
        self._source_search: Gtk.SearchEntry | None = None
        self._catalog_status: Gtk.Label | None = None
        self._add_source_button: Gtk.Widget | None = None
        self._catalog: NewsCatalog | None = None
        self._selected_catalog_source: NewsSource | None = None

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _current_source(self) -> NewsSource | None:
        if not self._sources:
            return None
        self._active_source_index = normalize_active_index(
            index=self._active_source_index,
            count=len(self._sources),
        )
        return self._sources[self._active_source_index]

    @property
    def _current_article(self) -> NewsArticle | None:
        if not self._articles:
            return None
        self._active_article_index = normalize_active_index(
            index=self._active_article_index,
            count=len(self._articles),
        )
        return self._articles[self._active_article_index]

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            index=self._active_article_index,
            count=len(self._articles),
            loading=self._loading,
            error=bool(self._error),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            source=self._current_source,
            article=self._current_article,
            index=self._active_article_index,
            count=len(self._articles),
            loading=self._loading,
            error=self._error,
            fetched_at=self._fetched_at,
            cadence_seconds=REFRESH_INTERVAL_S,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._refresh_timer_id = GLib.timeout_add_seconds(
            REFRESH_INTERVAL_S,
            self._refresh_tick,
        )
        if self._current_source is not None:
            self._startup_fetch_timer_id = GLib.timeout_add_seconds(
                STARTUP_FETCH_DELAY_S,
                self._run_startup_fetch,
            )

    def stop(self) -> None:
        for attr in ("_refresh_timer_id", "_startup_fetch_timer_id"):
            timer_id = getattr(self, attr, 0)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, attr, 0)
        if self._source_dialog is not None:
            self._source_dialog.destroy()
            self._source_dialog = None
        super().stop()

    def on_clicked(self) -> None:
        if self._current_source is None:
            self._show_source_picker()
        elif self._current_article is not None:
            self._open_current_article()
        else:
            self._fetch_async()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._articles:
            return
        step = -1 if direction_up else 1
        self._set_active_article(self._active_article_index + step)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        source = self._current_source
        current = self._current_article
        status: list[Gtk.MenuItem] = []
        primary: list[Gtk.MenuItem] = []
        navigation: list[Gtk.MenuItem] = []
        refresh: list[Gtk.MenuItem] = []
        display: list[Gtk.MenuItem] = []
        destructive: list[Gtk.MenuItem] = []

        if source is None:
            status.append(disabled_menu_item(_("No news source selected"), gtk=Gtk))
        else:
            status.extend(
                [
                    disabled_menu_item(source_label(source), gtk=Gtk),
                    disabled_menu_item(source_detail(source), gtk=Gtk),
                    disabled_menu_item(
                        cadence_label(
                            seconds=REFRESH_INTERVAL_S,
                            verb=_("Refreshes"),
                        ),
                        gtk=Gtk,
                    ),
                ]
            )
            if current is not None:
                status.append(disabled_menu_item(current.title, gtk=Gtk))
                age = article_age(article=current)
                if age:
                    status.append(disabled_menu_item(age, gtk=Gtk))

                open_headline = Gtk.MenuItem(label=_("Open Headline"))
                open_headline.connect(
                    "activate",
                    lambda _widget: self._open_current_article(),
                )
                primary.append(open_headline)

            if source.publication_url:
                open_publication = Gtk.MenuItem(label=_("Open Publication"))
                open_publication.connect(
                    "activate",
                    lambda _widget: self._open_uri(source.publication_url),
                )
                primary.append(open_publication)

            state_status = self._live_status()
            state_text = live_state_label(state_status)
            if state_text:
                status.append(disabled_menu_item(state_text, gtk=Gtk))
            error = live_state_error(status=state_status, error=self._error)
            if error:
                status.append(
                    disabled_menu_item(
                        _("Error: {msg}").format(msg=error),
                        gtk=Gtk,
                    )
                )

            previous = Gtk.MenuItem(label=_("Previous Headline"))
            previous.set_sensitive(
                bool(self._articles) and self._active_article_index > 0
            )
            previous.connect(
                "activate",
                lambda _widget: self._set_active_article(
                    self._active_article_index - 1
                ),
            )
            next_item = Gtk.MenuItem(label=_("Next Headline"))
            next_item.set_sensitive(
                bool(self._articles)
                and self._active_article_index < len(self._articles) - 1
            )
            next_item.connect(
                "activate",
                lambda _widget: self._set_active_article(
                    self._active_article_index + 1
                ),
            )
            navigation.extend((previous, next_item))

            refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
            refresh_item.connect("activate", lambda _widget: self._fetch_async())
            refresh.append(refresh_item)

            if len(self._sources) > 1:
                display.append(
                    radio_submenu(
                        label=_("Source"),
                        choices=tuple(
                            (_source_menu_label(item), index)
                            for index, item in enumerate(self._sources)
                        ),
                        active_value=self._active_source_index,
                        on_selected=lambda _widget, index: self._set_source(index),
                        gtk=Gtk,
                    )
                )

            remove = Gtk.MenuItem(label=_("Remove Current News Source"))
            remove.connect("activate", lambda _widget: self._remove_current_source())
            destructive.append(remove)

        add = Gtk.MenuItem(label=_("Add News Source..."))
        add.set_sensitive(len(self._sources) < MAX_SOURCES)
        add.connect("activate", lambda _widget: self._show_source_picker())

        return menu_sections(
            status=status,
            primary=primary,
            navigation=navigation,
            refresh=refresh,
            display=display,
            manage=[add],
            destructive=destructive,
            gtk=Gtk,
        )

    def _live_status(self):
        return resolve_live_status(
            has_data=self._current_article is not None,
            loading=self._loading,
            error=self._error,
            updated_at=self._fetched_at,
            stale_after_seconds=REFRESH_INTERVAL_S * 2,
        )

    def _refresh_tick(self) -> bool:
        if self._current_source is not None:
            self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _set_active_article(self, index: int) -> None:
        normalized = normalize_active_index(index=index, count=len(self._articles))
        if normalized == self._active_article_index:
            return
        self._active_article_index = normalized
        self._save_prefs()
        self.present()

    def _set_source(self, index: int) -> None:
        normalized = normalize_active_index(index=index, count=len(self._sources))
        if normalized == self._active_source_index:
            return
        self._active_source_index = normalized
        self._source_changed()

    def _source_changed(self) -> None:
        self._fetch_request_id += 1
        self._articles = []
        self._active_article_index = 0
        self._fetched_at = None
        self._loading = False
        self._error = ""
        self._save_prefs()
        self.present()
        if self._current_source is not None:
            self._fetch_async()

    def _fetch_async(self) -> None:
        source = self._current_source
        if source is None:
            return
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        self._loading = True
        self._error = ""
        self.present()
        self._worker.run(
            name="news-rss-fetch",
            fn=lambda: fetch_news_articles(source=source),
            on_result=lambda articles: self._on_fetch_result(
                request_id=request_id,
                feed_url=source.feed_url,
                articles=articles,
            ),
            on_error=lambda exc: self._on_fetch_error(
                request_id=request_id,
                exc=exc,
            ),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        feed_url: str,
        articles: tuple[NewsArticle, ...],
    ) -> bool:
        source = self._current_source
        if (
            request_id != self._fetch_request_id
            or source is None
            or source.feed_url != feed_url
        ):
            return False
        self._loading = False
        if not articles:
            self._error = _("No news articles returned")
            self.present()
            return False
        current_id = self._current_article.id if self._current_article else ""
        previous_index = self._active_article_index
        self._articles = list(articles)
        self._active_article_index = self._refreshed_article_index(
            current_id=current_id,
            previous_index=previous_index,
        )
        self._fetched_at = dt.datetime.now(dt.timezone.utc)
        self._error = ""
        self._save_prefs()
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._error = str(exc) or exc.__class__.__name__
        log.bind(action="fetch_error").debug("News RSS fetch failed: %s", exc)
        self.present()
        return False

    def _refreshed_article_index(self, *, current_id: str, previous_index: int) -> int:
        if current_id:
            for index, article in enumerate(self._articles):
                if article.id == current_id:
                    return index
        return normalize_active_index(index=previous_index, count=len(self._articles))

    def _open_current_article(self) -> None:
        article = self._current_article
        if article is not None:
            self._open_uri(article.url)

    @staticmethod
    def _open_uri(uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            log.bind(action="open_url").warning("Failed to open URL: %s", exc)

    def _add_and_select_source(self, source: NewsSource) -> None:
        updated = add_source(self._sources, source=source)
        if source.feed_url not in {item.feed_url for item in updated}:
            return
        self._sources = list(updated)
        self._active_source_index = next(
            index
            for index, item in enumerate(self._sources)
            if item.feed_url == source.feed_url
        )
        self._source_changed()

    def _remove_current_source(self) -> None:
        current = self._current_source
        if current is None:
            return
        self._sources = list(remove_source(self._sources, feed_url=current.feed_url))
        self._active_source_index = min(
            self._active_source_index,
            max(0, len(self._sources) - 1),
        )
        self._source_changed()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                sources=self._sources,
                active_source_index=self._active_source_index,
                articles=self._articles,
                active_article_index=self._active_article_index,
                fetched_at=self._fetched_at,
            )
        )

    def _show_source_picker(self) -> None:
        if self._source_dialog is not None:
            self._source_dialog.present()
            return
        dialog = Gtk.Dialog(
            title=_("Choose News Source"),
            transient_for=self.popup_anchor.parent if self.popup_anchor else None,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.add_button(_("Update List"), Gtk.ResponseType.APPLY)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Add"), Gtk.ResponseType.OK)
        dialog.connect("response", self._on_picker_response)
        dialog.connect("destroy", self._on_picker_destroyed)
        content = prepare_dialog_content(
            dialog=dialog,
            width=PICKER_WIDTH_PX,
            height=PICKER_HEIGHT_PX,
            spacing=PICKER_SPACING_PX,
            margin=PICKER_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
            resizable=True,
        )
        intro = Gtk.Label(
            label=_("Choose a country, then select a publication or edition."),
        )
        intro.set_xalign(0.0)
        content.pack_start(intro, False, False, 0)
        content.pack_start(self._build_picker_panes(), True, True, 0)

        self._catalog_status = Gtk.Label()
        self._catalog_status.set_xalign(0.0)
        self._catalog_status.set_line_wrap(True)
        content.pack_start(self._catalog_status, False, False, 0)

        self._source_dialog = dialog
        self._add_source_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        self._sync_picker_add_button()
        dialog.show_all()

        cached = load_cached_catalog()
        if cached is not None:
            self._apply_catalog(cached)
            if cached.stale:
                self._refresh_catalog_async()
        else:
            self._set_catalog_status(_("Loading news sources..."))
            self._refresh_catalog_async()

    def _build_picker_panes(self) -> Gtk.Paned:
        pane = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        pane.set_position(COUNTRY_PANE_WIDTH_PX)

        country_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        country_title = Gtk.Label(label=_("Country"))
        country_title.set_xalign(0.0)
        country_box.pack_start(country_title, False, False, 0)
        self._country_search = Gtk.SearchEntry()
        self._country_search.set_placeholder_text(_("Search countries"))
        self._country_search.connect("search-changed", self._on_country_search)
        country_box.pack_start(self._country_search, False, False, 0)
        self._country_list = Gtk.ListBox()
        self._country_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._country_list.set_filter_func(self._filter_country_row)
        self._country_list.connect("row-selected", self._on_country_selected)
        country_scroll = Gtk.ScrolledWindow()
        country_scroll.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        country_scroll.add(self._country_list)
        country_box.pack_start(country_scroll, True, True, 0)
        pane.pack1(country_box, resize=False, shrink=False)

        source_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        source_box.set_margin_start(10)
        source_title = Gtk.Label(label=_("Publication or Edition"))
        source_title.set_xalign(0.0)
        source_box.pack_start(source_title, False, False, 0)
        self._source_search = Gtk.SearchEntry()
        self._source_search.set_placeholder_text(_("Search publications"))
        self._source_search.connect("search-changed", self._on_source_search)
        source_box.pack_start(self._source_search, False, False, 0)
        self._source_list = Gtk.ListBox()
        self._source_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._source_list.set_filter_func(self._filter_source_row)
        self._source_list.connect("row-selected", self._on_catalog_source_selected)
        self._source_list.connect("row-activated", self._on_catalog_source_activated)
        source_scroll = Gtk.ScrolledWindow()
        source_scroll.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        source_scroll.add(self._source_list)
        source_box.pack_start(source_scroll, True, True, 0)
        pane.pack2(source_box, resize=True, shrink=False)
        return pane

    def _on_picker_response(self, dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.APPLY:
            self._refresh_catalog_async()
            return
        if response_id == Gtk.ResponseType.OK:
            source = self._selected_catalog_source
            if source is None or self._source_already_added(source):
                return
            self._add_and_select_source(source)
        dialog.destroy()

    def _on_picker_destroyed(self, _dialog: Gtk.Dialog) -> None:
        self._source_dialog = None
        self._country_list = None
        self._source_list = None
        self._country_search = None
        self._source_search = None
        self._catalog_status = None
        self._add_source_button = None
        self._selected_catalog_source = None

    def _refresh_catalog_async(self) -> None:
        self._set_catalog_status(_("Updating news sources..."))
        self._worker.run_guarded(
            key="news-source-catalog",
            name="news-source-catalog",
            fn=refresh_catalog,
            on_result=self._on_catalog_result,
            on_error=self._on_catalog_error,
        )

    def _on_catalog_result(self, cached: CachedNewsCatalog) -> bool:
        self._apply_catalog(cached)
        return False

    def _on_catalog_error(self, exc: Exception) -> bool:
        if self._catalog is None:
            message = _("Could not load news sources: {error}").format(error=exc)
        else:
            message = _(
                "Could not update news sources; using cached list: {error}"
            ).format(
                error=exc,
            )
        self._set_catalog_status(message)
        return False

    def _apply_catalog(self, cached: CachedNewsCatalog) -> None:
        self._catalog = cached.catalog
        if self._source_dialog is None:
            return
        self._populate_countries()
        timestamp = cached.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        self._set_catalog_status(
            _("{count} sources - updated {time}").format(
                count=cached.catalog.source_count,
                time=timestamp,
            )
        )

    def _populate_countries(self) -> None:
        if self._country_list is None or self._catalog is None:
            return
        for child in list(self._country_list.get_children()):
            self._country_list.remove(child)
        rows: dict[str, _CountryRow] = {}
        for code in self._catalog.country_codes:
            row = _CountryRow(code)
            self._country_list.add(row)
            rows[code] = row
        self._country_list.show_all()

        current = self._current_source
        preferred = (
            current.country_code if current is not None else country_code_for_locale()
        )
        if preferred not in rows:
            preferred = "GLOBAL" if "GLOBAL" in rows else next(iter(rows), None)
        if preferred is not None:
            self._country_list.select_row(rows[preferred])

    def _populate_sources(self, country_code: str) -> None:
        if self._source_list is None or self._catalog is None:
            return
        for child in list(self._source_list.get_children()):
            self._source_list.remove(child)
        sources = sorted(
            self._catalog.sources_by_country.get(country_code, ()),
            key=lambda source: (
                source.publication_name.casefold(),
                source.category.casefold(),
                source.language_name.casefold(),
                source.feed_url.casefold(),
            ),
        )
        matching_row: _SourceRow | None = None
        current = self._current_source
        for source in sources:
            row = _SourceRow(source)
            self._source_list.add(row)
            if current is not None and source.feed_url == current.feed_url:
                matching_row = row
        self._selected_catalog_source = None
        self._source_list.show_all()
        if matching_row is not None:
            self._source_list.select_row(matching_row)
        self._sync_picker_add_button()

    def _on_country_search(self, _entry: Gtk.SearchEntry) -> None:
        if self._country_list is not None:
            self._country_list.invalidate_filter()

    def _on_source_search(self, _entry: Gtk.SearchEntry) -> None:
        if self._source_list is not None:
            self._source_list.invalidate_filter()

    def _filter_country_row(self, row: Gtk.ListBoxRow) -> bool:
        if not isinstance(row, _CountryRow):
            return True
        query = (
            self._country_search.get_text().strip().casefold()
            if self._country_search is not None
            else ""
        )
        return not query or query in country_name(row.code).casefold()

    def _filter_source_row(self, row: Gtk.ListBoxRow) -> bool:
        if not isinstance(row, _SourceRow):
            return True
        query = (
            self._source_search.get_text().strip().casefold()
            if self._source_search is not None
            else ""
        )
        if not query:
            return True
        source = row.source
        haystack = " ".join(
            (
                source.publication_name,
                source.category,
                source.language_name,
                source.feed_url,
            )
        ).casefold()
        return query in haystack

    def _on_country_selected(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if isinstance(row, _CountryRow):
            if self._source_search is not None:
                self._source_search.set_text("")
            self._populate_sources(row.code)

    def _on_catalog_source_selected(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        self._selected_catalog_source = (
            row.source if isinstance(row, _SourceRow) else None
        )
        self._sync_picker_add_button()

    def _on_catalog_source_activated(
        self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow
    ) -> None:
        if (
            isinstance(row, _SourceRow)
            and not self._source_already_added(row.source)
            and len(self._sources) < MAX_SOURCES
            and self._source_dialog is not None
        ):
            self._selected_catalog_source = row.source
            self._source_dialog.response(Gtk.ResponseType.OK)

    def _source_already_added(self, source: NewsSource) -> bool:
        return source.feed_url in {item.feed_url for item in self._sources}

    def _sync_picker_add_button(self) -> None:
        if self._add_source_button is None:
            return
        source = self._selected_catalog_source
        enabled = (
            source is not None
            and len(self._sources) < MAX_SOURCES
            and not self._source_already_added(source)
        )
        self._add_source_button.set_sensitive(enabled)

    def _set_catalog_status(self, message: str) -> None:
        if self._catalog_status is not None:
            self._catalog_status.set_text(message)


def _source_row_detail(source: NewsSource) -> str:
    parts: list[str] = []
    if source.language_name:
        parts.append(source.language_name)
    parts.append(_compact_feed_location(source.feed_url))
    return " - ".join(parts)


def _source_menu_label(source: NewsSource) -> str:
    label = source_label(source)
    if source.category:
        return label
    return _("{source} - {feed}").format(
        source=label,
        feed=_compact_feed_location(source.feed_url),
    )


def _compact_feed_location(url: str) -> str:
    parsed = urlsplit(url)
    location = f"{parsed.netloc}{parsed.path}"
    if parsed.query:
        location = f"{location}?{parsed.query}"
    location = location.rstrip("/") or url
    return f"{location[:117]}..." if len(location) > 120 else location
