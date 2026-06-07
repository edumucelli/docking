# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Release update checker and popup UI."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from docking import __version__
from docking.core.position import is_horizontal
from docking.core.updates import (
    PROJECT_RELEASES_URL,
    ReleaseInfo,
    UpdateState,
    decide_update_popup,
    fetch_latest_release,
    load_state,
    save_state,
    should_check_for_updates,
    utc_now_iso,
)
from docking.i18n import _
from docking.log import get_logger
from docking.ui.display import clamp_popup
from docking.ui.tooltip import compute_tooltip_position

if TYPE_CHECKING:
    from docking.core.config import Config

log = get_logger("updates")

UPDATE_CHECK_DELAY_S = 8
UPDATE_POPUP_GAP_PX = 16
UPDATE_POPUP_SPACING_PX = 10
UPDATE_POPUP_MARGIN_PX = 12
REMIND_LATER_HOURS = 24


class UpdateCheckController:
    """Schedules release checks and presents update notifications."""

    def __init__(
        self,
        *,
        window: Gtk.Window,
        config: Config,
    ) -> None:
        self._window = window
        self._config = config
        self._start_source_id: int = 0
        self._popup: Gtk.Window | None = None
        self._latest_release: ReleaseInfo | None = None

    def start(self) -> None:
        """Schedule an automatic update check if user preferences allow it."""
        if self._start_source_id or not self._config.update_check_enabled:
            return
        state = load_state()
        if not should_check_for_updates(
            enabled=self._config.update_check_enabled,
            interval_hours=self._config.update_check_interval_hours,
            state=state,
            now=datetime.now(timezone.utc),
        ):
            return
        self._start_source_id = GLib.timeout_add_seconds(
            UPDATE_CHECK_DELAY_S,
            self._on_startup_delay_elapsed,
        )

    def stop(self) -> None:
        """Cancel scheduled work and close any visible popup."""
        if self._start_source_id:
            GLib.source_remove(self._start_source_id)
            self._start_source_id = 0
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None

    def check_now(self) -> None:
        """Run a manual update check immediately."""
        self._run_check_in_thread()

    def open_releases_page(self) -> None:
        """Open the project releases page."""
        self._open_url(PROJECT_RELEASES_URL)

    def _on_startup_delay_elapsed(self) -> bool:
        self._start_source_id = 0
        self._run_check_in_thread()
        return False

    def _run_check_in_thread(self) -> None:
        thread = threading.Thread(target=self._check_worker, daemon=True)
        thread.start()

    def _check_worker(self) -> None:
        release: ReleaseInfo | None = None
        error = ""
        try:
            release = fetch_latest_release()
        except Exception as exc:
            error = str(exc)
            log.debug("Update check failed: %s", exc)
        GLib.idle_add(self._on_check_finished, release, error)

    def _on_check_finished(
        self,
        release: ReleaseInfo | None,
        error: str,
    ) -> bool:
        now = datetime.now(timezone.utc)
        current_state = load_state()
        state = UpdateState(
            ignored_version=current_state.ignored_version,
            last_checked_at=utc_now_iso(now),
            last_error=error,
            last_result="error" if error else "ok",
            last_seen_version=release.version
            if release
            else current_state.last_seen_version,
            remind_after=current_state.remind_after,
        )
        decision = decide_update_popup(
            current_version=__version__,
            release=release,
            state=state,
            now=now,
        )
        save_state(state)
        if decision.should_show and decision.release is not None:
            self._show_popup(release=decision.release)
        return False

    def _show_popup(self, *, release: ReleaseInfo) -> None:
        if not self._window.get_realized():
            log.debug("Skipping update popup because dock window is not realized")
            return
        self._latest_release = release
        if self._popup is None:
            popup = Gtk.Window(type=Gtk.WindowType.POPUP)
            popup.set_decorated(False)
            popup.set_skip_taskbar_hint(True)
            popup.set_resizable(False)
            popup.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            popup.set_transient_for(self._window)
            self._popup = popup
        else:
            child = self._popup.get_child()
            if child is not None:
                self._popup.remove(child)

        self._popup.add(self._build_popup_content(release=release))
        self._popup.show_all()
        self._position_popup()

    def _build_popup_content(self, *, release: ReleaseInfo) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.OUT)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=UPDATE_POPUP_SPACING_PX,
        )
        box.set_border_width(UPDATE_POPUP_MARGIN_PX)

        title = Gtk.Label(
            label=_("Docking {version} is available").format(version=release.version)
        )
        title.set_xalign(0.0)
        box.pack_start(title, False, False, 0)

        detail = Gtk.Label(
            label=_("You are using {version}.").format(version=__version__)
        )
        detail.set_xalign(0.0)
        box.pack_start(detail, False, False, 0)

        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=UPDATE_POPUP_SPACING_PX,
        )
        view = Gtk.Button(label=_("View Release"))
        later = Gtk.Button(label=_("Later"))
        ignore = Gtk.Button(label=_("Ignore"))
        view.connect("clicked", self._on_view_release)
        later.connect("clicked", self._on_later)
        ignore.connect("clicked", self._on_ignore)
        buttons.pack_start(view, False, False, 0)
        buttons.pack_start(later, False, False, 0)
        buttons.pack_start(ignore, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        frame.add(box)
        return frame

    def _position_popup(self) -> None:
        if self._popup is None:
            return
        win_x, win_y = self._window.get_position()
        win_w, win_h = self._window.get_size()
        pref = self._popup.get_preferred_size()[1]
        popup_w = max(pref.width, 1)
        popup_h = max(pref.height, 1)
        pos = self._window.config.pos
        if is_horizontal(pos):
            anchor_x = win_x + win_w / 2
            anchor_y = win_y if pos.value == "bottom" else win_y + win_h
        else:
            anchor_x = win_x + win_w if pos.value == "left" else win_x
            anchor_y = win_y + win_h / 2
        popup_x, popup_y = compute_tooltip_position(
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            tooltip_w=popup_w,
            tooltip_h=popup_h,
            gap=UPDATE_POPUP_GAP_PX,
        )
        clamped = clamp_popup(self._popup, popup_x, popup_y, popup_w, popup_h)
        self._popup.move(clamped.x, clamped.y)

    def _on_view_release(self, _button: Gtk.Button) -> None:
        if self._latest_release is not None:
            self._open_url(self._latest_release.url)
        self._hide_popup()

    def _on_later(self, _button: Gtk.Button) -> None:
        release = self._latest_release
        state = load_state()
        remind_after = datetime.now(timezone.utc) + timedelta(hours=REMIND_LATER_HOURS)
        save_state(
            UpdateState(
                ignored_version=state.ignored_version,
                last_checked_at=state.last_checked_at,
                last_error=state.last_error,
                last_result=state.last_result,
                last_seen_version=release.version
                if release
                else state.last_seen_version,
                remind_after=utc_now_iso(remind_after),
            ),
        )
        self._hide_popup()

    def _on_ignore(self, _button: Gtk.Button) -> None:
        release = self._latest_release
        state = load_state()
        save_state(
            UpdateState(
                ignored_version=release.version if release else state.ignored_version,
                last_checked_at=state.last_checked_at,
                last_error=state.last_error,
                last_result=state.last_result,
                last_seen_version=release.version
                if release
                else state.last_seen_version,
                remind_after="",
            ),
        )
        self._hide_popup()

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def _open_url(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except Exception as exc:
            log.warning("Failed to open release URL: %s", exc)
