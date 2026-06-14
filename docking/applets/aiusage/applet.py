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

"""GTK lifecycle glue for AI usage tracker applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.aiusage import meta
from docking.applets.aiusage.backends import BACKENDS
from docking.applets.aiusage.render import render_icon
from docking.applets.aiusage.state import (
    DisplayMode,
    Provider,
    _format_cost,
    _format_tokens,
    _short_model,
    _today_entry,
    cost_for_usage,
    prefs_from_state,
    provider_cost,
    provider_for_model,
    provider_tokens,
    reset_today,
    set_session,
    state_from_prefs,
    tooltip_text,
    total_tokens,
    week_cost,
    week_tokens,
)
from docking.applets.aiusage.store import read_prefs_from_disk
from docking.applets.base import Applet
from docking.applets.menu import menu_sections, radio_menu_items
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="aiusage"), applet_id=meta.id)

REFRESH_INTERVAL_S = 60
PREFS_KEY = "aiusage"
PREFS_KEY_LEGACY = "claude"


def _read_prefs_from_disk() -> dict | None:
    return read_prefs_from_disk()


class AiUsageApplet(Applet):
    """Tracks Claude Code and Codex CLI token usage and cost."""

    id = meta.id
    name = _("AI Usage")
    icon_name = "utilities-terminal"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = None
        if config:
            prefs = config.applet_prefs.get(PREFS_KEY)
            if not prefs:
                prefs = config.applet_prefs.get(PREFS_KEY_LEGACY)
        self._state = state_from_prefs(prefs=prefs)
        self._timer_id: int = 0
        self._selected_provider: Provider | None = None
        self._display_mode: DisplayMode = DisplayMode.COST
        self._poll_errors: dict[Provider, str] = {}

        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            state=self._state,
            selected_provider=self._selected_provider,
            display_mode=self._display_mode,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(
            state=self._state, provider=self._selected_provider
        )
        self.item.tooltip_builder = self._build_tooltip_widget

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        for backend in BACKENDS:
            backend.register_hooks()
        self._tick()  # Immediate first poll (merges OpenCode etc.).
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        pass

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through providers: Auto -> Claude -> Codex -> OpenCode."""
        choices: list[Provider | None] = [
            None,
            Provider.CLAUDE,
            Provider.CODEX,
            Provider.OPENCODE,
        ]
        idx = choices.index(self._selected_provider)
        idx = (idx + (1 if direction_up else -1)) % len(choices)
        self._selected_provider = choices[idx]
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        provider_items = radio_menu_items(
            choices=(
                (_("Auto"), None),
                (_("Claude"), Provider.CLAUDE),
                (_("Codex"), Provider.CODEX),
                (_("OpenCode"), Provider.OPENCODE),
            ),
            active_value=self._selected_provider,
            on_selected=lambda _widget, value: self._set_provider(provider=value),
            gtk=Gtk,
        )

        display_items = radio_menu_items(
            choices=(
                (_("Show Cost"), DisplayMode.COST),
                (_("Show Tokens"), DisplayMode.TOKENS),
            ),
            active_value=self._display_mode,
            on_selected=lambda _widget, value: self._set_display_mode(mode=value),
            gtk=Gtk,
        )

        mi = Gtk.MenuItem(label=_("Reset Today"))
        mi.connect("activate", lambda _w: self._reset_today())
        return menu_sections(
            display=[*provider_items, Gtk.SeparatorMenuItem(), *display_items],
            destructive=[mi],
            gtk=Gtk,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_provider(self, provider: Provider | None) -> None:
        self._selected_provider = provider
        self.present()

    def _set_display_mode(self, mode: DisplayMode) -> None:
        self._display_mode = mode
        self.present()

    def _tick(self) -> bool:
        prefs = _read_prefs_from_disk()
        new_state = state_from_prefs(prefs=prefs)

        for backend in BACKENDS:
            try:
                sessions = backend.poll_today()
                self._poll_errors.pop(backend.provider, None)
                for sid, model_usage in sessions.items():
                    new_state = set_session(
                        state=new_state,
                        session_id=sid,
                        model_usage=model_usage,
                    )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error != self._poll_errors.get(backend.provider):
                    self._poll_errors[backend.provider] = error
                    log.bind(action=f"poll_{backend.provider.value}").warning(
                        "Failed to poll %s usage: %s",
                        backend.provider.value,
                        error,
                    )

        if new_state != self._state:
            self._state = new_state
            self.present()
        return True

    def _reset_today(self) -> None:
        self._state = reset_today(state=self._state)
        self.save_prefs(prefs=prefs_from_state(state=self._state))
        self.present()

    def _build_tooltip_widget(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sel = self._selected_provider
        entry = _today_entry(state=self._state)

        if not entry or not entry.by_model:
            name = sel.value.capitalize() if sel else "AI Usage"
            label = Gtk.Label(label=_("{name}: no usage today").format(name=name))
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        # Filter models to selected provider and skip empty rows.
        models = [
            (m, u)
            for m, u in entry.by_model
            if (sel is None or provider_for_model(model=m) == sel)
            and (cost_for_usage(model=m, usage=u) > 0 or total_tokens(u) > 0)
        ]

        if not models:
            name = sel.value.capitalize() if sel else "AI Usage"
            label = Gtk.Label(label=_("{name}: no usage today").format(name=name))
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        show_tokens = self._display_mode == DisplayMode.TOKENS
        name = sel.value.capitalize() if sel else "Today"

        if show_tokens:
            tokens = sum(total_tokens(u) for _, u in models)
            header_value = _format_tokens(tokens=tokens)
        else:
            header_value = _format_cost(
                cost=sum(cost_for_usage(model=m, usage=u) for m, u in models)
            )
        header = Gtk.Label()
        header.set_markup(
            _("<b>{name}: {value}</b>").format(
                name=name,
                value=GLib.markup_escape_text(header_value),
            )
        )
        header.set_xalign(0.5)
        header.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(header, False, False, 0)

        # Per-model breakdown (aggregate by display name).
        display_raw: dict[str, float | int] = {}
        for model, usage in models:
            key = _short_model(model=model)
            if show_tokens:
                display_raw[key] = int(display_raw.get(key, 0)) + total_tokens(usage)
            else:
                display_raw[key] = float(display_raw.get(key, 0.0)) + cost_for_usage(
                    model=model, usage=usage
                )
        for display_name, raw in display_raw.items():
            if show_tokens:
                formatted = _format_tokens(tokens=int(raw))
            else:
                formatted = _format_cost(cost=float(raw))
            row = Gtk.Label(label=f"  {display_name}: {formatted}")
            row.set_xalign(0.5)
            row.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.7))
            box.pack_start(row, False, False, 0)

        # Week total (filtered).
        if len(self._state.days) > 1:
            days = self._state.days
            if show_tokens:
                if sel:
                    wk = sum(provider_tokens(entry=d, provider=sel) for d in days)
                else:
                    wk = week_tokens(state=self._state)
                wk_val = _format_tokens(tokens=wk)
            else:
                if sel:
                    wk = sum(provider_cost(entry=d, provider=sel) for d in days)
                else:
                    wk = week_cost(state=self._state)
                wk_val = _format_cost(cost=wk)
            week_lbl = Gtk.Label(label=_("This week: {value}").format(value=wk_val))
            week_lbl.set_xalign(0.5)
            week_lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.9))
            box.pack_start(week_lbl, False, False, 0)

        return box
