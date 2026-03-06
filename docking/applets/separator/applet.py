"""Separator applet behavior and config wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import create_separator_icon
from .state import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    STEP,
    STYLE_LINE,
    STYLE_SPACE,
)

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="separator"), applet_id=str(AppletId.SEPARATOR))


class SeparatorApplet(Applet):
    """A thin transparent gap that can be inserted multiple times."""

    id = AppletId.SEPARATOR
    name = _("Separator")
    icon_name = "list-remove"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._gap = DEFAULT_SIZE
        self._style = STYLE_SPACE
        self._invert_color = False
        super().__init__(icon_size, config)
        self.item.main_size = self._gap
        self.item.allow_zoom = False

    def _prefs_key(self) -> str:
        """Per-instance prefs key (e.g. 'separator#0')."""
        return self.item.desktop_id.removeprefix("applet://")

    def load_instance_prefs(self) -> dict[str, Any]:
        if self._config:
            return dict(self._config.applet_prefs.get(self._prefs_key(), {}))
        return {}

    def save_instance_prefs(self, prefs: dict[str, Any]) -> None:
        if self._config:
            self._config.applet_prefs[self._prefs_key()] = prefs
            self._config.save()

    def apply_prefs(self) -> None:
        """Load persisted gap size after desktop_id is finalized."""
        prefs = self.load_instance_prefs()
        self._gap = _normalized_gap(value=prefs.get("gap", DEFAULT_SIZE))
        self._style = _normalized_style(value=prefs.get("style", STYLE_SPACE))
        self._invert_color = bool(prefs.get("invert_color", False))
        self.item.main_size = self._gap
        _log.bind(action="apply_prefs", desktop_id=self.item.desktop_id).debug(
            "Separator prefs applied: gap=%d style=%s invert_color=%s",
            self._gap,
            self._style,
            self._invert_color,
        )
        self.refresh_presentation()

    def _save_current_prefs(self) -> None:
        self.save_instance_prefs(
            prefs={
                "gap": self._gap,
                "style": self._style,
                "invert_color": self._invert_color,
            }
        )

    def _set_gap(self, gap: int) -> None:
        self._gap = _normalized_gap(value=gap)
        self.item.main_size = self._gap
        _log.bind(action="set_gap", desktop_id=self.item.desktop_id).debug(
            f"Separator size set to {self._gap}px"
        )
        self._save_current_prefs()
        self.refresh_presentation()

    def _set_style(self, style: str) -> None:
        style = _normalized_style(value=style)
        if style == self._style:
            return
        self._style = style
        self._save_current_prefs()
        self.refresh_presentation()

    def _set_invert_color(self, invert_color: bool) -> None:
        if invert_color == self._invert_color:
            return
        self._invert_color = invert_color
        self._save_current_prefs()
        self.refresh_presentation()

    def create_icon(self, size: int):
        return create_separator_icon(gap=self._gap, size=size)

    def on_scroll(self, direction_up: bool) -> None:
        self._set_gap(gap=self._gap + STEP if direction_up else self._gap - STEP)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        increase = Gtk.MenuItem(label=_("Increase Gap"))
        increase.connect("activate", lambda _: self._set_gap(gap=self._gap + STEP))
        decrease = Gtk.MenuItem(label=_("Decrease Gap"))
        decrease.connect("activate", lambda _: self._set_gap(gap=self._gap - STEP))
        style = Gtk.MenuItem(label=_("Style"))
        style_menu = Gtk.Menu()
        style.set_submenu(style_menu)

        line = Gtk.CheckMenuItem(label=_("Line"))
        line.set_draw_as_radio(True)
        line.set_active(self._style == STYLE_LINE)
        line.connect(
            "toggled",
            lambda widget: widget.get_active() and self._set_style(STYLE_LINE),
        )
        style_menu.append(line)

        space = Gtk.CheckMenuItem(label=_("Space"))
        space.set_draw_as_radio(True)
        space.set_active(self._style == STYLE_SPACE)
        space.connect(
            "toggled",
            lambda widget: widget.get_active() and self._set_style(STYLE_SPACE),
        )
        style_menu.append(space)

        invert = Gtk.CheckMenuItem(label=_("Invert Color"))
        invert.set_active(self._invert_color)
        invert.connect(
            "toggled",
            lambda widget: self._set_invert_color(widget.get_active()),
        )
        return [increase, decrease, Gtk.SeparatorMenuItem(), style, invert]


def _normalized_gap(*, value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SIZE
    return max(MIN_SIZE, min(MAX_SIZE, parsed))


def _normalized_style(*, value: object) -> str:
    if value == STYLE_LINE:
        return STYLE_LINE
    return STYLE_SPACE
