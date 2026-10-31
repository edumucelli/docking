"""Unix XEmbed tray presentation, selected only on supported platforms."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from docking.applets.menu import disabled_menu_item
from docking.applets.popup import PopupAnchor
from docking.core.position import Position
from docking.i18n import _
from docking.platform.tray.state import TrayState

from .xembed import XEmbedTrayHost


class UnixTrayPresentation:
    def __init__(
        self,
        *,
        icon_size: int,
        changed: Callable[[], None],
        state: Callable[[], TrayState],
        anchor: Callable[[], PopupAnchor | None],
        refresh: Callable[[], None],
    ) -> None:
        self._changed, self._state, self._anchor, self._refresh = (
            changed,
            state,
            anchor,
            refresh,
        )
        self._host = XEmbedTrayHost(
            icon_size=min(32, max(22, icon_size)), on_changed=changed
        )

    @property
    def active(self) -> bool:
        return self._host.active

    @property
    def count(self) -> int:
        return len(self._host.icons)

    def tooltip(self) -> str:
        return _("System Tray: {n} legacy item(s)").format(n=self.count)

    def clicked(self) -> bool:
        if not self.active:
            return False
        x, y, position = self._legacy_position()
        self._host.toggle_visible(anchor_x=x, anchor_y=y, position=position)
        return True

    def stop(self) -> None:
        self._host.stop()

    def empty_message(self) -> str:
        if self.active:
            return (
                _("Legacy X11 tray is active with {n} item(s).").format(n=self.count)
                if self.count
                else _("Legacy X11 tray is active and waiting for tray apps.")
            )
        owner = self._state().legacy_owner
        if owner:
            return _(
                "No StatusNotifier apps are registered. Legacy tray icons are "
                "currently owned by {owner}. Use Take Over Legacy Tray from the "
                "applet menu if you want Docking to host them."
            ).format(owner=owner)
        return _(
            "No tray applications are registered. Use Start Legacy Tray from "
            "the applet menu for older X11 tray apps."
        )

    def menu_items(self) -> list[Gtk.MenuItem]:
        if self._host.active:
            release = Gtk.MenuItem(label=_("Release Legacy Tray"))
            release.connect("activate", lambda _w: self._release_legacy_tray())
            return [
                disabled_menu_item(
                    _("Legacy X11 tray active: {n} item(s)").format(
                        n=len(self._host.icons),
                    ),
                    gtk=Gtk,
                ),
                release,
            ]

        if self._state().legacy_owner:
            take_over = Gtk.MenuItem(label=_("Take Over Legacy Tray"))
            take_over.connect("activate", lambda _w: self._confirm_take_over_legacy())
            return [
                disabled_menu_item(
                    _("Legacy X11 tray owned by {owner}").format(
                        owner=self._state().legacy_owner,
                    ),
                    gtk=Gtk,
                ),
                take_over,
            ]

        start = Gtk.MenuItem(label=_("Start Legacy Tray"))
        start.connect("activate", lambda _w: self._start_legacy_tray())
        return [start]

    def _start_legacy_tray(self, *, force: bool = False) -> None:
        anchor_x, anchor_y, position = self._legacy_position()
        if self._host.start(
            force=force,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            position=position,
        ):
            self._changed()
            self._refresh()
            return

        message = self._host.unavailable_reason or _("Unknown X11 tray error")
        dialog = Gtk.MessageDialog(
            transient_for=self._anchor().parent if self._anchor() else None,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=_("Could not start the legacy tray"),
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _confirm_take_over_legacy(self) -> None:
        owner = self._state().legacy_owner or _("another tray")
        dialog = Gtk.MessageDialog(
            transient_for=self._anchor().parent if self._anchor() else None,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_("Take over the legacy tray?"),
        )
        dialog.add_button(_("Take Over"), Gtk.ResponseType.OK)
        dialog.format_secondary_text(
            _(
                "Docking will replace {owner} as the X11 system tray host. "
                "Existing legacy tray apps may need to be restarted before they "
                "dock into Docking."
            ).format(owner=owner),
        )
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._start_legacy_tray(force=True)

    def _release_legacy_tray(self) -> None:
        self._host.stop()
        self._changed()
        self._refresh()

    def _legacy_position(self) -> tuple[int, int, Position | None]:
        anchor = self._anchor()
        if anchor is None:
            return (0, 0, None)
        return (anchor.x, anchor.y, anchor.position)

    def position(self) -> None:
        if not self._host.active:
            return
        anchor_x, anchor_y, position = self._legacy_position()
        self._host.position_near(
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            position=position,
        )
