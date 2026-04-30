"""GTK lifecycle glue for the Hacker News applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.freshness import cadence_label, parse_timestamp
from docking.applets.hackernews import meta
from docking.applets.hackernews.render import render_icon
from docking.applets.hackernews.state import (
    DEFAULT_FETCH_LIMIT,
    HN_SOURCE_LABEL,
    MAX_STORIES,
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    HackerNewsPage,
    HackerNewsStory,
    append_unique_stories,
    build_tooltip,
    fetch_hn_story_page,
    normalize_active_index,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="hackernews"), applet_id=meta.id)


class HackerNewsApplet(Applet):
    """Show Hacker News top headlines."""

    id = meta.id
    name = _("Hacker News")
    icon_name = "internet-news-reader"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._stories: list[HackerNewsStory] = list(prefs.stories)
        self._active_index = prefs.active_index
        self._next_story_offset = prefs.next_offset
        self._fetched_at = parse_timestamp(prefs.fetched_at)
        self._loading = False
        self._page_loading = False
        self._has_more_stories = (
            prefs.has_more_stories and len(self._stories) < MAX_STORIES
        )
        self._error = ""
        self._refresh_timer_id = 0
        self._startup_fetch_timer_id = 0
        self._fetch_request_id = 0
        self._worker = BackgroundWorker(logger=log)

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _current_story(self) -> HackerNewsStory | None:
        if not self._stories:
            return None
        self._active_index = normalize_active_index(
            index=self._active_index,
            count=len(self._stories),
        )
        return self._stories[self._active_index]

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            story=self._current_story,
            index=self._active_index,
            count=len(self._stories),
            loading=self._loading or self._page_loading,
            error=bool(self._error),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            story=self._current_story,
            index=self._active_index,
            count=len(self._stories),
            loading=self._loading,
            page_loading=self._page_loading,
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
        self._startup_fetch_timer_id = GLib.timeout_add_seconds(
            STARTUP_FETCH_DELAY_S,
            self._run_startup_fetch,
        )

    def stop(self) -> None:
        for attr in (
            "_refresh_timer_id",
            "_startup_fetch_timer_id",
        ):
            timer_id = getattr(self, attr, 0)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, attr, 0)
        super().stop()

    def on_clicked(self) -> None:
        self._open_current_story()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._stories:
            return
        step = -1 if direction_up else 1
        self._move_story(step=step)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = [
            disabled_menu_item(HN_SOURCE_LABEL, gtk=Gtk),
            disabled_menu_item(
                cadence_label(seconds=REFRESH_INTERVAL_S, verb=_("Refreshes")),
                gtk=Gtk,
            ),
        ]

        current = self._current_story
        primary: list[Gtk.MenuItem] = []
        if current is not None:
            status.extend(
                [
                    disabled_menu_item(current.title, gtk=Gtk),
                    disabled_menu_item(
                        _("{score} points, {comments} comments").format(
                            score=current.score,
                            comments=current.comments,
                        ),
                        gtk=Gtk,
                    ),
                ]
            )
            state_status = self._live_status()
            state_label = live_state_label(state_status)
            if state_label:
                status.append(disabled_menu_item(state_label, gtk=Gtk))
            error = live_state_error(status=state_status, error=self._error)
            if error:
                status.append(
                    disabled_menu_item(
                        _("Error: {msg}").format(msg=error),
                        gtk=Gtk,
                    )
                )

            open_story = Gtk.MenuItem(label=_("Open Story"))
            open_story.connect("activate", lambda _w: self._open_current_story())
            primary.append(open_story)

            open_comments = Gtk.MenuItem(label=_("Open Comments"))
            open_comments.connect("activate", lambda _w: self._open_current_comments())
            primary.append(open_comments)

        next_item = Gtk.MenuItem(label=_("Next Headline"))
        next_item.set_sensitive(bool(self._stories))
        next_item.connect("activate", lambda _w: self._advance_story())

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._fetch_async())

        return menu_sections(
            status=status,
            primary=primary,
            navigation=[next_item],
            refresh=[refresh],
            gtk=Gtk,
        )

    def _live_status(self):
        return resolve_live_status(
            has_data=self._current_story is not None,
            loading=self._loading,
            error=self._error,
            updated_at=self._fetched_at,
            stale_after_seconds=REFRESH_INTERVAL_S * 2,
        )

    def _refresh_tick(self) -> bool:
        self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _advance_story(self) -> None:
        if not self._stories:
            return
        self._move_story(step=1)

    def _move_story(self, *, step: int) -> None:
        if not self._stories:
            return
        last_index = len(self._stories) - 1
        if step > 0 and self._active_index >= last_index:
            if self._has_more_stories:
                self._fetch_next_page_async()
            else:
                self.present()
            return
        if step < 0 and self._active_index <= 0:
            self.present()
            return

        next_index = self._active_index + step
        self._set_active_index(next_index)
        if next_index == last_index:
            self._fetch_next_page_async()

    def _set_active_index(self, index: int) -> None:
        self._active_index = normalize_active_index(
            index=index,
            count=len(self._stories),
        )
        self._save_prefs()
        self.present()

    def _fetch_async(self) -> None:
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        self._loading = True
        self._page_loading = False
        self._error = ""
        self.present()

        self._worker.run(
            name="hackernews-fetch",
            fn=lambda: fetch_hn_story_page(
                limit=self._refresh_fetch_limit(),
                offset=0,
            ),
            on_result=lambda page: self._on_fetch_result(
                request_id=request_id,
                page=page,
            ),
            on_error=lambda exc: self._on_fetch_error(
                request_id=request_id,
                exc=exc,
            ),
        )

    def _refresh_fetch_limit(self) -> int:
        return min(
            MAX_STORIES,
            max(
                DEFAULT_FETCH_LIMIT,
                self._next_story_offset,
                len(self._stories),
            ),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        page: HackerNewsPage,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._page_loading = False
        if page.stories:
            current_story = self._current_story
            previous_index = self._active_index
            self._stories = list(page.stories[:MAX_STORIES])
            self._active_index = self._refreshed_active_index(
                current_story=current_story,
                previous_index=previous_index,
            )
            self._next_story_offset = page.next_offset
            self._has_more_stories = page.has_more and len(self._stories) < MAX_STORIES
            self._error = ""
            self._fetched_at = dt.datetime.now(dt.timezone.utc)
            self._save_prefs()
        elif not self._stories:
            self._error = _("No Hacker News stories")
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._page_loading = False
        self._error = str(exc) or exc.__class__.__name__
        log.bind(action="fetch_error").debug("Hacker News fetch failed: %s", exc)
        self.present()
        return False

    def _refreshed_active_index(
        self,
        *,
        current_story: HackerNewsStory | None,
        previous_index: int,
    ) -> int:
        if not self._stories:
            return 0
        if current_story is not None:
            for index, story in enumerate(self._stories):
                if story.id == current_story.id:
                    return index
        return normalize_active_index(index=previous_index, count=len(self._stories))

    def _fetch_next_page_async(self) -> None:
        if self._loading or self._page_loading or not self._has_more_stories:
            return
        if len(self._stories) >= MAX_STORIES:
            self._has_more_stories = False
            self.present()
            return

        offset = self._next_story_offset
        self._page_loading = True
        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        self.present()

        self._worker.run(
            name="hackernews-page-fetch",
            fn=lambda: fetch_hn_story_page(
                limit=DEFAULT_FETCH_LIMIT,
                offset=offset,
            ),
            on_result=lambda page: self._on_page_fetch_result(
                request_id=request_id,
                page=page,
            ),
            on_error=lambda exc: self._on_page_fetch_error(
                request_id=request_id,
                exc=exc,
            ),
        )

    def _on_page_fetch_result(
        self,
        *,
        request_id: int,
        page: HackerNewsPage,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._page_loading = False
        self._next_story_offset = page.next_offset
        if not page.stories:
            self._has_more_stories = page.has_more and len(self._stories) < MAX_STORIES
            self.present()
            return False

        merged = append_unique_stories(existing=self._stories, additions=page.stories)
        merged = merged[:MAX_STORIES]
        self._has_more_stories = page.has_more and len(merged) < MAX_STORIES
        if len(merged) == len(self._stories):
            self.present()
            return False

        self._stories = list(merged)
        self._fetched_at = dt.datetime.now(dt.timezone.utc)
        self._save_prefs()
        self.present()
        return False

    def _on_page_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._page_loading = False
        self._has_more_stories = False
        log.bind(action="page_fetch_error").debug(
            "Hacker News page fetch failed: %s",
            exc,
        )
        self.present()
        return False

    def _open_current_story(self) -> None:
        story = self._current_story
        if story is None:
            return
        self._open_url(story.url)

    def _open_current_comments(self) -> None:
        story = self._current_story
        if story is None:
            return
        self._open_url(story.hn_url)

    def _open_url(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            log.bind(action="open_url").warning("Failed to open URL: %s", exc)

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                stories=tuple(self._stories),
                active_index=self._active_index,
                next_offset=self._next_story_offset,
                has_more_stories=self._has_more_stories,
                fetched_at=self._fetched_at,
            )
        )
