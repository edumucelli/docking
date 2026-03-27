"""GTK lifecycle glue for AI usage tracker applet."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.aiusage import meta
from docking.applets.aiusage.render import render_icon
from docking.applets.aiusage.state import (
    Provider,
    _format_cost,
    _short_model,
    _today_entry,
    cost_for_usage,
    prefs_from_state,
    provider_cost,
    provider_for_model,
    query_opencode_today,
    reset_today,
    set_session,
    state_from_prefs,
    tooltip_text,
    week_cost,
)
from docking.applets.base import Applet
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="aiusage"), applet_id=meta.id)

REFRESH_INTERVAL_S = 60
PREFS_KEY = "aiusage"
PREFS_KEY_LEGACY = "claude"

_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
_CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


def _project_root() -> str:
    """Resolve the docking project root from this file's location."""
    # docking/applets/aiusage/applet.py -> 3 levels up
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def _hook_command_prefix() -> str:
    root = _project_root()
    return f"PYTHONPATH={root} {sys.executable} -m docking.applets.aiusage.hook"


def _read_prefs_from_disk() -> dict[str, Any] | None:
    """Read aiusage prefs directly from dock.json on disk."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    config_path = base / "docking" / "dock.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    prefs = config.get("applet_prefs", {})
    return prefs.get(PREFS_KEY) or prefs.get(PREFS_KEY_LEGACY)


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

        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            state=self._state,
            selected_provider=self._selected_provider,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(
            state=self._state, provider=self._selected_provider
        )
        self.item.tooltip_builder = self._build_tooltip_widget

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        _register_claude_hooks()
        _register_codex_hook()
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
        """Cycle through providers: Auto -> Claude -> Codex."""
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
        items: list[Gtk.MenuItem] = []

        for label, value in (
            (_("Auto"), None),
            (_("Claude"), Provider.CLAUDE),
            (_("Codex"), Provider.CODEX),
            (_("OpenCode"), Provider.OPENCODE),
        ):
            mi = Gtk.CheckMenuItem(label=label)
            mi.set_active(self._selected_provider == value)
            mi.connect(
                "toggled",
                lambda _w, v=value: self._set_provider(provider=v),
            )
            items.append(mi)

        items.append(Gtk.SeparatorMenuItem())

        mi = Gtk.MenuItem(label=_("Reset Today"))
        mi.connect("activate", lambda _w: self._reset_today())
        items.append(mi)
        return items

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_provider(self, provider: Provider | None) -> None:
        self._selected_provider = provider
        self.present()

    def _tick(self) -> bool:
        prefs = _read_prefs_from_disk()
        new_state = state_from_prefs(prefs=prefs)

        # Merge OpenCode sessions from SQLite (no hook, poll-based).
        try:
            oc_sessions = query_opencode_today()
            for sid, model_usage in oc_sessions.items():
                new_state = set_session(
                    state=new_state,
                    session_id=f"oc:{sid}",
                    model_usage=model_usage,
                )
        except Exception:
            pass  # SQLite errors shouldn't crash the dock.

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

        # Filter models to selected provider and skip zero-usage.
        models = [
            (m, u)
            for m, u in entry.by_model
            if (sel is None or provider_for_model(model=m) == sel)
            and cost_for_usage(model=m, usage=u) > 0
        ]

        if not models:
            name = sel.value.capitalize() if sel else "AI Usage"
            label = Gtk.Label(label=_("{name}: no usage today").format(name=name))
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        cost = sum(cost_for_usage(model=m, usage=u) for m, u in models)
        name = sel.value.capitalize() if sel else "Today"
        header = Gtk.Label()
        header.set_markup(
            _("<b>{name}: {cost}</b>").format(
                name=name,
                cost=GLib.markup_escape_text(_format_cost(cost=cost)),
            )
        )
        header.set_xalign(0.5)
        header.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(header, False, False, 0)

        # Per-model breakdown (aggregate by display name).
        display_costs: dict[str, float] = {}
        for model, usage in models:
            key = _short_model(model=model)
            display_costs[key] = display_costs.get(key, 0.0) + cost_for_usage(
                model=model, usage=usage
            )
        for display_name, model_cost in display_costs.items():
            row = Gtk.Label(label=f"  {display_name}: {_format_cost(cost=model_cost)}")
            row.set_xalign(0.5)
            row.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.7))
            box.pack_start(row, False, False, 0)

        # Week total (filtered).
        if len(self._state.days) > 1:
            if sel:
                wk = sum(provider_cost(entry=d, provider=sel) for d in self._state.days)
            else:
                wk = week_cost(state=self._state)
            week_lbl = Gtk.Label(
                label=_("This week: {cost}").format(cost=_format_cost(cost=wk))
            )
            week_lbl.set_xalign(0.5)
            week_lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.9))
            box.pack_start(week_lbl, False, False, 0)

        return box


# ------------------------------------------------------------------
# Claude hook registration
# ------------------------------------------------------------------


def _register_claude_hooks() -> None:
    """Ensure Claude Code hooks point to our CLI entry point."""
    try:
        if _CLAUDE_SETTINGS.exists():
            settings = json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))
        else:
            settings = {}
    except (OSError, json.JSONDecodeError):
        _log.bind(action="register_hooks").warning(
            "Could not read %s", _CLAUDE_SETTINGS
        )
        return

    hooks = settings.setdefault("hooks", {})
    changed = False
    prefix = _hook_command_prefix()

    # Remove stale claude.hook entries.
    for event_key in ("Stop", "SessionStart"):
        entries = hooks.get(event_key, [])
        cleaned = [
            e
            for e in entries
            if not any(
                "docking.applets.claude.hook" in h.get("command", "")
                for h in e.get("hooks", [])
            )
        ]
        if len(cleaned) != len(entries):
            hooks[event_key] = cleaned
            changed = True

    # Stop hook (needs matcher).
    stop_entries = hooks.get("Stop", [])
    if not _has_hook(entries=stop_entries, needle=prefix):
        stop_entries.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"{prefix} claude Stop"}],
            }
        )
        hooks["Stop"] = stop_entries
        changed = True

    # SessionStart hook.
    start_entries = hooks.get("SessionStart", [])
    if not _has_hook(entries=start_entries, needle=prefix):
        start_entries.append(
            {
                "hooks": [
                    {"type": "command", "command": f"{prefix} claude SessionStart"}
                ],
            }
        )
        hooks["SessionStart"] = start_entries
        changed = True

    if changed:
        try:
            _CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _CLAUDE_SETTINGS.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            _log.bind(action="register_hooks").warning(
                "Could not write %s", _CLAUDE_SETTINGS
            )


def _has_hook(entries: list[dict], needle: str) -> bool:
    for entry in entries:
        for h in entry.get("hooks", []):
            if needle in h.get("command", ""):
                return True
    return False


# ------------------------------------------------------------------
# Codex hook registration
# ------------------------------------------------------------------


def _register_codex_hook() -> None:
    """Ensure Codex CLI notify points to our hook."""
    try:
        if _CODEX_CONFIG.exists():
            content = _CODEX_CONFIG.read_text(encoding="utf-8")
        else:
            return  # Codex not installed.
    except OSError:
        return

    root = _project_root()
    our_toml = (
        f'notify = ["env", "PYTHONPATH={root}",'
        f' "{sys.executable}", "-m", "docking.applets.aiusage.hook", "codex"]'
    )

    notify_match = re.search(r"^notify\s*=\s*\[.*?\]", content, re.MULTILINE)
    if notify_match:
        existing = notify_match.group(0)
        if "PYTHONPATH" in existing and "docking.applets.aiusage.hook" in existing:
            return  # Already ours with correct PYTHONPATH.
        if "codex-sync" in existing:
            _log.bind(action="register_codex_hook").info(
                "Codex notify already set to codex-sync, not overwriting"
            )
            return
        content = (
            content[: notify_match.start()] + our_toml + content[notify_match.end() :]
        )
    else:
        # Insert before first [section].
        section_match = re.search(r"^\[", content, re.MULTILINE)
        if section_match:
            content = (
                content[: section_match.start()]
                + our_toml
                + "\n"
                + content[section_match.start() :]
            )
        else:
            content = content.rstrip() + "\n" + our_toml + "\n"

    try:
        _CODEX_CONFIG.write_text(content, encoding="utf-8")
    except OSError:
        _log.bind(action="register_codex_hook").warning(
            "Could not write %s", _CODEX_CONFIG
        )
