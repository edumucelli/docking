"""Review and confirm imports from an existing desktop dock."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets.worker import BackgroundWorker
from docking.core.theme import Theme
from docking.i18n import _
from docking.platform.dock_import import (
    DockImport,
    DockImportSource,
    Scalar,
    prepare_dock_import,
    read_dock_import_sources,
)
from docking.ui.display import monitor_connector

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.dock_import import DockImportKind


def _read_sources(kind: DockImportKind | None) -> list[DockImportSource] | None:
    loop = GLib.MainLoop()
    result: list[DockImportSource] | None = None

    def finished(sources: list[DockImportSource]) -> None:
        nonlocal result
        result = sources
        loop.quit()

    BackgroundWorker().run_guarded(
        key="dock-import",
        name="read_dock_import_sources",
        fn=lambda: read_dock_import_sources(kind),
        on_result=finished,
        on_error=lambda _error: loop.quit(),
    )
    loop.run()
    return result


def _setting_label(key: str) -> str:
    return {
        "position": _("Position"),
        "dock_alignment": _("Alignment"),
        "monitor_connector": _("Display"),
        "active_display": _("Follow Active Display"),
        "icon_size": _("Icon Size"),
        "zoom_enabled": _("Enable Zoom"),
        "zoom_percent": _("Zoom Level"),
        "hide_mode": _("Hide Mode"),
        "hide_delay_ms": _("Hide Delay"),
        "unhide_delay_ms": _("Unhide Delay"),
        "pressure_reveal_enabled": _("Pressure Reveal"),
        "lock_icons": _("Lock Icons"),
        "pinned_only": _("Pinned Only"),
        "current_workspace_only": _("Current Workspace Only"),
        "tooltips_enabled": _("Show Tooltips"),
        "window_preview_thumbnails_enabled": _("Show Window Previews"),
        "window_preview_thumbnail_width": _("Preview Width"),
        "additional_distance_from_edge": _("Additional Distance from Edge"),
        "anchor_applets": _("Anchor Applets"),
        "anchor_files": _("Anchor Files"),
    }[key]


def _setting_value(key: str, value: Scalar | None) -> str:
    if key == "hide_mode":
        return {
            "none": _("Don't Hide"),
            "always-on-top": _("Always on Top"),
            "always-behind": _("Always behind"),
            "autohide": _("Auto-hide"),
            "intelligent": _("Intelligent"),
            "dodge-active": _("Dodge Active"),
            "window-dodge": _("Dodge Windows"),
            "dodge-maximized": _("Dodge Maximized"),
        }[str(value)]
    if key == "position":
        return {
            "top": _("Top"),
            "bottom": _("Bottom"),
            "left": _("Left"),
            "right": _("Right"),
        }[str(value)]
    if key in ("zoom_percent", "dock_alignment") and isinstance(value, (int, float)):
        return f"{value:.0%}"
    if key in ("hide_delay_ms", "unhide_delay_ms"):
        return _("{value} ms").format(value=value)
    if key in (
        "icon_size",
        "window_preview_thumbnail_width",
        "additional_distance_from_edge",
    ):
        return _("{value} px").format(value=value)
    if isinstance(value, bool):
        return _("Enabled") if value else _("Disabled")
    return _("Primary Display") if value is None else str(value)


def _review_text(candidate: DockImport, *, empty: bool) -> str:
    sections: list[str] = []
    if candidate.labels:
        sections.append("\n".join([_("Pins to add"), *candidate.labels]))
    elif empty:
        sections.append(_("The source dock has no pinned items."))
    changes = candidate.changes
    if changes:
        keys = dict.fromkeys(
            "monitor_connector" if key == "monitor_index" else key for key in changes
        )
        lines = [_("Preferences to apply")]
        for key in keys:
            value = (
                candidate.config.monitor_connector
                if key == "monitor_connector"
                else changes[key]
            )
            lines.append(
                _("{setting}: {value}").format(
                    setting=_setting_label(key),
                    value=_setting_value(key, value),
                )
            )
        sections.append("\n".join(lines))
    if candidate.notes:
        sections.append("\n".join(candidate.notes))
    if not candidate.labels and not changes and not empty:
        sections.append(_("There are no changes to import."))
    return "\n\n".join(sections)


def review_dock_import(
    parent: Gtk.Window | None,
    base: Config,
    registry: ApplicationRegistry,
    *,
    first_run: bool,
    kind: DockImportKind | None = None,
) -> DockImport | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    sources = _read_sources(kind)
    if (first_run and not sources) or (parent is not None and not parent.get_visible()):
        return None

    connectors = {
        connector
        for index in range(display.get_n_monitors())
        if (monitor := display.get_monitor(index)) is not None
        and (connector := monitor_connector(monitor)) is not None
    }

    def prepare(source: DockImportSource) -> DockImport:
        return prepare_dock_import(
            source,
            base,
            registry,
            connectors=connectors,
            theme_gap=Theme.load(base.theme, base.icon_size).distance_from_edge,
            first_run=first_run,
        )

    reviews = [(source, prepare(source)) for source in sources or ()]
    if first_run and not any(
        source.empty_layout or candidate.added or candidate.changes
        for source, candidate in reviews
    ):
        return None

    dialog = Gtk.Dialog(
        title=_("Import from Another Dock"),
        transient_for=parent,
        modal=True,
        destroy_with_parent=True,
    )
    dialog.set_default_size(450, 360)
    dialog.add_button(_("Skip") if first_run else _("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(_("Import"), Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    area = dialog.get_content_area()
    area.set_border_width(12)
    area.set_spacing(8)
    source_labels = [
        _("{source} ({profile})").format(source=source.name, profile=source.profile)
        if source.profile
        else source.name
        for source, _candidate in reviews
    ]
    combo = Gtk.ComboBoxText()
    for label in source_labels:
        combo.append_text(label)
    if kind is None and reviews:
        combo.set_tooltip_text(_("Choose a dock to import."))
        area.pack_start(combo, False, False, 0)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    overview = Gtk.TextView(
        editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR
    )
    overview.set_margin_start(4)
    overview.set_margin_end(4)
    scroller.add(overview)
    area.pack_start(scroller, True, True, 0)

    def selected(_combo: Gtk.ComboBoxText) -> None:
        index = combo.get_active()
        can_import = False
        if index >= 0:
            source, candidate = reviews[index]
            empty = first_run and source.empty_layout
            can_import = bool(candidate.added or candidate.changes or empty)
            message = _review_text(candidate, empty=empty)
            if kind is not None:
                message = f"{source_labels[index]}\n\n{message}"
        elif reviews:
            message = _("Choose a dock to review its pins and preferences.")
        elif sources is None:
            message = _("Could not read the source dock settings.")
        elif kind == "plank":
            message = _("No Plank or Plank Reloaded setup was found.")
        elif kind == "cairo-dock":
            message = _("No Cairo-Dock setup was found.")
        else:
            message = _("No Plank or Cairo-Dock setup was found.")
        overview.get_buffer().set_text(message)
        dialog.set_response_sensitive(Gtk.ResponseType.OK, can_import)

    combo.connect("changed", selected)
    if len(reviews) == 1:
        combo.set_active(0)
    selected(combo)
    try:
        dialog.show_all()
        while (
            dialog.run() == Gtk.ResponseType.OK and (index := combo.get_active()) >= 0
        ):
            source, reviewed = reviews[index]
            current = prepare(source)
            if (current.added, current.changes, current.notes) == (
                reviewed.added,
                reviewed.changes,
                reviewed.notes,
            ):
                return current
            reviews[index] = (source, current)
            selected(combo)
        return None
    finally:
        dialog.destroy()
