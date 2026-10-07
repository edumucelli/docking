"""GTK lifecycle glue for the public Reddit RSS applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import (
    disabled_menu_item,
    menu_sections,
    radio_submenu,
)
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.applets.reddit import meta
from docking.applets.reddit.render import render_icon
from docking.applets.reddit.state import (
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    RedditPost,
    RedditSort,
    RedditTopPeriod,
    add_subreddit,
    build_tooltip,
    fetch_reddit_posts,
    normalize_active_index,
    normalize_subreddit,
    prefs_from_mapping,
    prefs_payload,
    remove_subreddit,
    sort_label,
    source_label,
    top_period_label,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform import targets
from docking.ui.tooltip import parse_timestamp

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="reddit"), applet_id=meta.id)

ADD_DIALOG_WIDTH_PX = 360
ADD_DIALOG_MARGIN_PX = 12


class RedditApplet(Applet):
    """Browse public Reddit RSS posts without API credentials."""

    id = meta.id
    name = _("Reddit")
    icon_name = "internet-news-reader"

    def __init__(self, icon_size: int, config: Config) -> None:
        prefs = prefs_from_mapping(config.applet_prefs.get(meta.id, {}))
        self._subreddits = list(prefs.subreddits)
        self._active_subreddit_index = prefs.active_subreddit_index
        self._sort = prefs.sort
        self._top_period = prefs.top_period
        self._posts = list(prefs.posts)
        self._active_post_index = prefs.active_post_index
        self._fetched_at = parse_timestamp(prefs.fetched_at)
        self._loading = False
        self._error = ""
        self._refresh_timer_id = 0
        self._startup_fetch_timer_id = 0
        self._fetch_request_id = 0
        self._worker = BackgroundWorker(logger=log)

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _current_subreddit(self) -> str:
        self._active_subreddit_index = normalize_active_index(
            index=self._active_subreddit_index,
            count=len(self._subreddits),
        )
        return self._subreddits[self._active_subreddit_index]

    @property
    def _current_post(self) -> RedditPost | None:
        if not self._posts:
            return None
        self._active_post_index = normalize_active_index(
            index=self._active_post_index,
            count=len(self._posts),
        )
        return self._posts[self._active_post_index]

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            index=self._active_post_index,
            count=len(self._posts),
            loading=self._loading,
            error=bool(self._error),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            post=self._current_post,
            subreddit=self._current_subreddit,
            index=self._active_post_index,
            count=len(self._posts),
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
        super().stop()

    def on_clicked(self) -> None:
        if self._current_post is None:
            self._show_add_subreddit_dialog()
            return
        self._open_current_post()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._posts:
            return
        step = -1 if direction_up else 1
        self._set_active_post(self._active_post_index + step)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        source = source_label(
            subreddit=self._current_subreddit,
            sort=self._sort,
            top_period=self._top_period,
        )
        status: list[Gtk.MenuItem] = [
            disabled_menu_item(source, gtk=Gtk),
            disabled_menu_item(
                cadence_label(seconds=REFRESH_INTERVAL_S, verb=_("Refreshes")),
                gtk=Gtk,
            ),
        ]
        current = self._current_post
        primary: list[Gtk.MenuItem] = []
        if current is not None:
            status.append(disabled_menu_item(current.title, gtk=Gtk))
            if current.author:
                status.append(
                    disabled_menu_item(
                        _("Posted by u/{author}").format(author=current.author),
                        gtk=Gtk,
                    )
                )
            open_post = Gtk.MenuItem(label=_("Open Post"))
            open_post.connect("activate", lambda _widget: self._open_current_post())
            primary.append(open_post)

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
        previous.set_sensitive(bool(self._posts) and self._active_post_index > 0)
        previous.connect(
            "activate",
            lambda _widget: self._set_active_post(self._active_post_index - 1),
        )
        next_item = Gtk.MenuItem(label=_("Next Headline"))
        next_item.set_sensitive(
            bool(self._posts) and self._active_post_index < len(self._posts) - 1
        )
        next_item.connect(
            "activate",
            lambda _widget: self._set_active_post(self._active_post_index + 1),
        )

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _widget: self._fetch_async())

        display = [
            radio_submenu(
                label=_("Subreddit"),
                choices=tuple(
                    (f"r/{subreddit}", index)
                    for index, subreddit in enumerate(self._subreddits)
                ),
                active_value=self._active_subreddit_index,
                on_selected=lambda _widget, index: self._set_subreddit(index=index),
                gtk=Gtk,
            ),
            radio_submenu(
                label=_("Sort"),
                choices=tuple((sort_label(value), value) for value in RedditSort),
                active_value=self._sort,
                on_selected=lambda _widget, value: self._set_sort(sort=value),
                gtk=Gtk,
            ),
        ]
        if self._sort is RedditSort.TOP:
            display.append(
                radio_submenu(
                    label=_("Top Period"),
                    choices=tuple(
                        (top_period_label(value), value) for value in RedditTopPeriod
                    ),
                    active_value=self._top_period,
                    on_selected=lambda _widget, value: self._set_top_period(
                        period=value
                    ),
                    gtk=Gtk,
                )
            )

        add = Gtk.MenuItem(label=_("Add Subreddit..."))
        add.connect(
            "activate",
            lambda _widget: self._show_add_subreddit_dialog(),
        )
        remove = Gtk.MenuItem(
            label=_("Remove r/{subreddit}").format(subreddit=self._current_subreddit)
        )
        remove.set_sensitive(len(self._subreddits) > 1)
        remove.connect("activate", lambda _widget: self._remove_current_subreddit())

        return menu_sections(
            status=status,
            primary=primary,
            navigation=[previous, next_item],
            refresh=[refresh],
            display=display,
            manage=[add],
            destructive=[remove],
            gtk=Gtk,
        )

    def _live_status(self):
        return resolve_live_status(
            has_data=self._current_post is not None,
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

    def _set_active_post(self, index: int) -> None:
        normalized = normalize_active_index(index=index, count=len(self._posts))
        if normalized == self._active_post_index:
            return
        self._active_post_index = normalized
        self._save_prefs()
        self.present()

    def _set_subreddit(self, *, index: int) -> None:
        normalized = normalize_active_index(
            index=index,
            count=len(self._subreddits),
        )
        if normalized == self._active_subreddit_index:
            return
        self._active_subreddit_index = normalized
        self._source_changed()

    def _set_sort(self, *, sort: RedditSort) -> None:
        if sort is self._sort:
            return
        self._sort = sort
        self._source_changed()

    def _set_top_period(self, *, period: RedditTopPeriod) -> None:
        if period is self._top_period:
            return
        self._top_period = period
        self._source_changed()

    def _source_changed(self) -> None:
        self._posts = []
        self._active_post_index = 0
        self._fetched_at = None
        self._error = ""
        self._save_prefs()
        self.present()
        self._fetch_async()

    def _fetch_async(self) -> None:
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        subreddit = self._current_subreddit
        sort = self._sort
        top_period = self._top_period
        self._loading = True
        self._error = ""
        self.present()
        self._worker.run(
            name="reddit-rss-fetch",
            fn=lambda: fetch_reddit_posts(
                subreddit=subreddit,
                sort=sort,
                top_period=top_period,
            ),
            on_result=lambda posts: self._on_fetch_result(
                request_id=request_id,
                subreddit=subreddit,
                posts=posts,
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
        subreddit: str,
        posts: tuple[RedditPost, ...],
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        if subreddit != self._current_subreddit:
            return False
        if not posts:
            self._error = _("No Reddit posts returned")
            self.present()
            return False

        current_id = self._current_post.id if self._current_post is not None else ""
        previous_index = self._active_post_index
        self._posts = list(posts)
        self._active_post_index = self._refreshed_post_index(
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
        log.bind(action="fetch_error").debug("Reddit RSS fetch failed: %s", exc)
        self.present()
        return False

    def _refreshed_post_index(
        self,
        *,
        current_id: str,
        previous_index: int,
    ) -> int:
        if current_id:
            for index, post in enumerate(self._posts):
                if post.id == current_id:
                    return index
        return normalize_active_index(
            index=previous_index,
            count=len(self._posts),
        )

    def _open_current_post(self) -> None:
        post = self._current_post
        if post is None:
            return
        targets.open_target(post.url)

    def _show_add_subreddit_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Add Subreddit"),
            modal=True,
            destroy_with_parent=True,
        )
        add_cancel_ok_buttons(
            dialog=dialog,
            ok_label=_("Add"),
            cancel_label=_("Cancel"),
        )
        content = prepare_dialog_content(
            dialog=dialog,
            width=ADD_DIALOG_WIDTH_PX,
            margin=ADD_DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
            resizable=False,
        )
        label = Gtk.Label(label=_("Subreddit name or URL"))
        label.set_xalign(0.0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("linux")
        entry.set_activates_default(True)
        error = Gtk.Label()
        error.set_xalign(0.0)
        error.get_style_context().add_class("error")
        content.pack_start(label, False, False, 0)
        content.pack_start(entry, False, False, 0)
        content.pack_start(error, False, False, 0)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if response_id != Gtk.ResponseType.OK:
                dialog.destroy()
                return
            subreddit = normalize_subreddit(entry.get_text())
            if subreddit is None:
                error.set_text(_("Enter a valid subreddit name."))
                return
            self._add_and_select_subreddit(subreddit=subreddit)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()
        entry.grab_focus()

    def _add_and_select_subreddit(self, *, subreddit: str) -> None:
        updated = add_subreddit(self._subreddits, subreddit=subreddit)
        if subreddit not in updated:
            return
        self._subreddits = list(updated)
        self._active_subreddit_index = self._subreddits.index(subreddit)
        self._source_changed()

    def _remove_current_subreddit(self) -> None:
        if len(self._subreddits) <= 1:
            return
        current = self._current_subreddit
        self._subreddits = list(remove_subreddit(self._subreddits, subreddit=current))
        self._active_subreddit_index = min(
            self._active_subreddit_index,
            len(self._subreddits) - 1,
        )
        self._source_changed()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                subreddits=self._subreddits,
                active_subreddit_index=self._active_subreddit_index,
                sort=self._sort,
                top_period=self._top_period,
                posts=self._posts,
                active_post_index=self._active_post_index,
                fetched_at=self._fetched_at,
            )
        )
