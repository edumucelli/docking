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

"""GTK lifecycle and popup UI for the System Tray applet."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from docking.applets.base import Applet, load_theme_icon
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.applets.systemtray import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.platform.status_notifier import (
    StatusNotifierBackend,
    StatusTrayState,
    TrayItem,
)
from docking.platform.status_notifier.dbusmenu import DBusMenuClient, DBusMenuItem

from .render import create_status_tray_icon
from .state import tooltip_text
from .xembed import XEmbedTrayHost

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.position import Position

POLL_INTERVAL_S = 3


class SystemTrayApplet(Applet):
    """System tray host for StatusNotifier/AppIndicator items."""

    id = meta.id
    name = _("System Tray")
    icon_name = "application-x-executable"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = StatusNotifierBackend()
        self._state = self._backend.get_state()
        self._timer_id: int = 0
        self._popup: Gtk.Window | None = None
        self._item_menu: Gtk.Menu | None = None
        self._worker = BackgroundWorker()
        self._legacy_host = XEmbedTrayHost(
            icon_size=min(32, max(22, icon_size)),
            on_changed=self._on_legacy_changed,
        )
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        item_count = len(self._state.items)
        if self._legacy_host.active:
            item_count += len(self._legacy_host.icons)
        return create_status_tray_icon(
            size=size,
            available=self._state.available,
            item_count=item_count,
        )

    def refresh_tooltip(self) -> None:
        if self._legacy_host.active:
            self.item.name = _("System Tray: {n} legacy item(s)").format(
                n=len(self._legacy_host.icons),
            )
            return
        self.item.name = tooltip_text(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
        self._legacy_host.stop()
        self._backend.close()
        super().stop()

    def on_clicked(self) -> None:
        if self._popup is not None and self._popup.get_visible():
            self._popup.hide()
            return
        if self._legacy_host.active:
            anchor_x, anchor_y, position = self._legacy_position()
            self._legacy_host.toggle_visible(
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                position=position,
            )
            return
        self._refresh_now()
        self._position_legacy_host()
        self._show_popup()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = [
            disabled_menu_item(self._menu_header(), gtk=Gtk),
        ]
        legacy: list[Gtk.MenuItem] = self._legacy_menu_items()
        primary: list[Gtk.MenuItem] = []
        for item in self._state.items[:12]:
            root = Gtk.MenuItem(label=item.display_title)
            submenu = Gtk.Menu()
            activate = Gtk.MenuItem(label=_("Activate"))
            activate.connect(
                "activate",
                lambda _w, identifier=item.identifier: self._on_activate(identifier),
            )
            context = Gtk.MenuItem(label=_("Context Menu"))
            context.connect(
                "activate",
                lambda _w, identifier=item.identifier: self._show_item_menu(identifier),
            )
            submenu.append(activate)
            submenu.append(context)
            root.set_submenu(submenu)
            primary.append(root)

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        return menu_sections(
            status=status,
            primary=primary,
            refresh=[refresh],
            manage=legacy,
        )

    def _tick(self) -> bool:
        self._worker.run_guarded(
            key="poll",
            name="systemtray-poll",
            fn=self._backend.get_state,
            on_result=self._on_state_result,
        )
        return True

    def _refresh_now(self) -> None:
        self._on_state_result(self._backend.get_state())

    def _on_state_result(self, state: StatusTrayState) -> bool:
        if state != self._state:
            self._state = state
            self.present()
            if self._popup is not None and self._popup.get_visible():
                self._show_popup()
        return False

    def _show_popup(self) -> None:
        self._position_legacy_host()
        if self._popup is None:
            self._popup = create_popup_window()
        show_wrapped_popup(
            window=self._popup,
            content=self._build_popup_content(),
            anchor=self.popup_anchor,
        )

    def _build_popup_content(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        header = Gtk.Label(label=self._menu_header())
        header.set_xalign(0)
        header.get_style_context().add_class("dim-label")
        box.pack_start(header, False, False, 0)

        if not self._state.available:
            label = Gtk.Label(label=self._state.error or _("D-Bus unavailable"))
            label.set_xalign(0)
            box.pack_start(label, False, False, 0)
            return box

        if not self._state.items:
            if self._legacy_host.active:
                legacy_count = len(self._legacy_host.icons)
                if legacy_count:
                    text = _("Legacy X11 tray is active with {n} item(s).").format(
                        n=legacy_count,
                    )
                else:
                    text = _("Legacy X11 tray is active and waiting for tray apps.")
                label = Gtk.Label(label=text)
                label.set_xalign(0)
                label.set_line_wrap(True)
                box.pack_start(label, False, False, 0)
                return box
            if self._state.legacy_tray_owner:
                text = _(
                    "No StatusNotifier apps are registered. Legacy tray icons are "
                    "currently owned by {owner}. Use Take Over Legacy Tray from the "
                    "applet menu if you want Docking to host them."
                ).format(owner=self._state.legacy_tray_owner)
            else:
                text = _(
                    "No tray applications are registered. Use Start Legacy Tray from "
                    "the applet menu for older X11 tray apps."
                )
            label = Gtk.Label(label=text)
            label.set_xalign(0)
            label.set_line_wrap(True)
            box.pack_start(label, False, False, 0)
            return box

        for item in self._state.items:
            box.pack_start(self._build_item_row(item), False, False, 0)
        return box

    def _build_item_row(self, item: TrayItem) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        image = Gtk.Image()
        pixbuf = _load_item_icon(item=item, size=24)
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)
        else:
            image.set_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
        row.pack_start(image, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=item.display_title)
        title.set_xalign(0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        labels.pack_start(title, False, False, 0)
        detail_text = item.tooltip_text or item.status
        if detail_text:
            detail = Gtk.Label(label=detail_text)
            detail.set_xalign(0)
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            detail.get_style_context().add_class("dim-label")
            labels.pack_start(detail, False, False, 0)
        row.pack_start(labels, True, True, 0)

        activate = Gtk.Button(label=_("Open"))
        activate.connect(
            "clicked",
            lambda _w, identifier=item.identifier: self._on_activate(identifier),
        )
        row.pack_start(activate, False, False, 0)

        menu = Gtk.Button(label=_("Menu"))
        menu.connect(
            "clicked",
            lambda _w, identifier=item.identifier: self._show_item_menu(identifier),
        )
        row.pack_start(menu, False, False, 0)
        return row

    def _on_activate(self, identifier: str) -> None:
        self._backend.activate(identifier)
        if self._popup is not None:
            self._popup.hide()
        self._refresh_now()

    def _show_item_menu(self, identifier: str) -> None:
        client = self._backend.menu_client(identifier)
        if client is None:
            self._on_context_menu(identifier)
            return
        client.about_to_show(0)
        layout = client.get_layout()
        if layout is None:
            self._on_context_menu(identifier)
            return

        if layout.root.item_id != 0 and client.about_to_show(layout.root.item_id):
            layout = client.get_layout() or layout
        menu = self._gtk_menu_from_dbus_menu(root=layout.root, client=client)
        if menu is None:
            self._on_context_menu(identifier)
            return

        if self._popup is not None:
            self._popup.hide()
        self._item_menu = menu
        menu.connect("hide", lambda _w: setattr(self, "_item_menu", None))
        menu.show_all()
        menu.popup_at_pointer(None)

    def _on_context_menu(self, identifier: str) -> None:
        self._backend.context_menu(identifier)
        if self._popup is not None:
            self._popup.hide()
        self._refresh_now()

    def _legacy_menu_items(self) -> list[Gtk.MenuItem]:
        if self._legacy_host.active:
            release = Gtk.MenuItem(label=_("Release Legacy Tray"))
            release.connect("activate", lambda _w: self._release_legacy_tray())
            return [
                disabled_menu_item(
                    _("Legacy X11 tray active: {n} item(s)").format(
                        n=len(self._legacy_host.icons),
                    ),
                    gtk=Gtk,
                ),
                release,
            ]

        if self._state.legacy_tray_owner:
            take_over = Gtk.MenuItem(label=_("Take Over Legacy Tray"))
            take_over.connect("activate", lambda _w: self._confirm_take_over_legacy())
            return [
                disabled_menu_item(
                    _("Legacy X11 tray owned by {owner}").format(
                        owner=self._state.legacy_tray_owner,
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
        if self._legacy_host.start(
            force=force,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            position=position,
        ):
            self.present()
            self._refresh_now()
            return

        message = self._legacy_host.unavailable_reason or _("Unknown X11 tray error")
        dialog = Gtk.MessageDialog(
            transient_for=self.popup_anchor.parent if self.popup_anchor else None,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=_("Could not start the legacy tray"),
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _confirm_take_over_legacy(self) -> None:
        owner = self._state.legacy_tray_owner or _("another tray")
        dialog = Gtk.MessageDialog(
            transient_for=self.popup_anchor.parent if self.popup_anchor else None,
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
        self._legacy_host.stop()
        self.present()
        self._refresh_now()

    def _legacy_position(self) -> tuple[int, int, Position | None]:
        anchor = self.popup_anchor
        if anchor is None:
            return (0, 0, None)
        return (anchor.x, anchor.y, anchor.position)

    def _position_legacy_host(self) -> None:
        if not self._legacy_host.active:
            return
        anchor_x, anchor_y, position = self._legacy_position()
        self._legacy_host.position_near(
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            position=position,
        )

    def _on_legacy_changed(self) -> None:
        self._position_legacy_host()
        self.present()
        if self._popup is not None and self._popup.get_visible():
            self._show_popup()

    def _gtk_menu_from_dbus_menu(
        self,
        *,
        root: DBusMenuItem,
        client: DBusMenuClient,
    ) -> Gtk.Menu | None:
        menu = Gtk.Menu()
        for child in root.children:
            menu_item = self._gtk_menu_item_from_dbus_item(item=child, client=client)
            if menu_item is not None:
                menu.append(menu_item)
        if not menu.get_children():
            return None
        return menu

    def _gtk_menu_item_from_dbus_item(
        self,
        *,
        item: DBusMenuItem,
        client: DBusMenuClient,
    ) -> Gtk.MenuItem | None:
        if not item.visible:
            return None
        if item.is_separator:
            return Gtk.SeparatorMenuItem()

        label = item.label or _("Menu Item")
        if item.toggle_type in {"checkmark", "radio"}:
            menu_item: Gtk.MenuItem = Gtk.CheckMenuItem(label=label)
            if isinstance(menu_item, Gtk.CheckMenuItem):
                menu_item.set_active(item.toggle_state == 1)
        else:
            menu_item = Gtk.ImageMenuItem(label=label)

        menu_item.set_sensitive(item.enabled)
        if item.icon_name:
            pixbuf = _load_menu_icon(item=item, size=16)
            if pixbuf is not None and isinstance(menu_item, Gtk.ImageMenuItem):
                menu_item.set_image(Gtk.Image.new_from_pixbuf(pixbuf))
                menu_item.set_always_show_image(True)

        if item.children:
            submenu = Gtk.Menu()
            for child in item.children:
                child_item = self._gtk_menu_item_from_dbus_item(
                    item=child,
                    client=client,
                )
                if child_item is not None:
                    submenu.append(child_item)
            menu_item.set_submenu(submenu)
            submenu.connect(
                "show",
                lambda _w, item_id=item.item_id: client.about_to_show(item_id),
            )
        else:
            menu_item.connect(
                "activate",
                lambda _w, item_id=item.item_id: self._on_dbus_menu_activate(
                    client=client,
                    item_id=item_id,
                ),
            )
        return menu_item

    def _on_dbus_menu_activate(self, *, client: DBusMenuClient, item_id: int) -> None:
        client.event(item_id)
        self._refresh_now()

    def _menu_header(self) -> str:
        if not self._state.available:
            return _("System Tray unavailable")
        if self._state.watcher_mode == "host":
            return _("System Tray host: {n} item(s)").format(n=len(self._state.items))
        return _("System Tray: {n} item(s)").format(n=len(self._state.items))


def _load_item_icon(*, item: TrayItem, size: int) -> GdkPixbuf.Pixbuf | None:
    icon_name = item.effective_icon_name
    if not icon_name:
        return _load_item_pixmap(item=item, size=size)
    pixbuf = _load_icon_name_or_path(icon_name=icon_name, size=size)
    if pixbuf is not None:
        return pixbuf
    if item.icon_theme_path:
        themed_path = Path(item.icon_theme_path) / icon_name
        pixbuf = _load_icon_name_or_path(icon_name=str(themed_path), size=size)
        if pixbuf is not None:
            return pixbuf
    return _load_item_pixmap(item=item, size=size)


def _load_item_pixmap(*, item: TrayItem, size: int) -> GdkPixbuf.Pixbuf | None:
    if item.icon_pixmap is None:
        return None
    bytes_ = GLib.Bytes.new(item.icon_pixmap.rgba)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        bytes_,
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        item.icon_pixmap.width,
        item.icon_pixmap.height,
        item.icon_pixmap.width * 4,
    )
    if item.icon_pixmap.width == size and item.icon_pixmap.height == size:
        return pixbuf
    return pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)


def _load_menu_icon(*, item: DBusMenuItem, size: int) -> GdkPixbuf.Pixbuf | None:
    if item.icon_data:
        try:
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(item.icon_data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except GLib.Error:
            pixbuf = None
        if pixbuf is not None:
            return pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    if item.icon_name:
        return _load_icon_name_or_path(icon_name=item.icon_name, size=size)
    return None


def _load_icon_name_or_path(*, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    path = Path(icon_name)
    if path.is_absolute() and path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path),
                size,
                size,
                True,
            )
        except GLib.Error:
            return None
    return load_theme_icon(icon_name, size)
