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
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from docking.applets.base import Applet, load_theme_icon
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.applets.systemtray import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.platform.tray.menu import TrayMenuClient, TrayMenuItem, TrayMenuLayout
from docking.platform.tray.service import TrayService
from docking.platform.tray.state import TrayItem, TrayState

from .presentation import create_platform_presentation
from .render import create_status_tray_icon
from .state import tooltip_text

if TYPE_CHECKING:
    from docking.applets.services import AppletServices
    from docking.core.config import Config


class SystemTrayApplet(Applet):
    """System tray presentation backed by platform inventory and actions."""

    id = meta.id
    name = _("System Tray")
    icon_name = "application-x-executable"

    def __init__(self, icon_size: int, config: Config) -> None:
        # Replaced by the shared service through set_services; until then this
        # applet polls on its own so it stays usable standalone.
        self._service = TrayService()
        self._owns_service = True
        self._state = self._service.state
        self._active = False
        self._generation = 0
        self._popup: Gtk.Window | None = None
        self._item_menu: Gtk.Menu | None = None
        self._worker = BackgroundWorker()
        self._presentation = create_platform_presentation(
            icon_size=icon_size,
            changed=self._on_legacy_changed,
            state=lambda: self._state,
            anchor=lambda: self.popup_anchor,
            refresh=self._refresh_now,
        )
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def set_services(self, services: AppletServices) -> None:
        service = services.status_notifier
        if service is None or service is self._service:
            return
        self._generation += 1
        self._service.remove_listener(self._on_state_result)
        if self._owns_service:
            self._service.stop()
            self._owns_service = False
        self._service = service
        # Subscribing delivers the cached state, which is what repaints the icon
        # and tooltip. Assigning it here first would make that callback a no-op
        # and leave the applet showing the previous service's state.
        service.add_listener(self._on_state_result)

    def create_icon(self, size: int):
        item_count = len(self._state.items)
        if self._presentation is not None and self._presentation.active:
            item_count += self._presentation.count
        return create_status_tray_icon(
            size=size,
            available=self._state.available,
            item_count=item_count,
        )

    def refresh_tooltip(self) -> None:
        if self._presentation is not None and self._presentation.active:
            self.item.name = self._presentation.tooltip()
            return
        self.item.name = tooltip_text(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        self._generation += 1
        super().start(notify=notify)
        self._active = True
        # Subscribing here covers a private service that set_services never
        # replaced, and a restart after stop() unsubscribed.
        self._service.add_listener(self._on_state_result)
        if self._owns_service:
            self._service.start()

    def stop(self) -> None:
        self._active = False
        self._generation += 1
        self._service.remove_listener(self._on_state_result)
        if self._owns_service:
            self._service.stop()
        if self._item_menu is not None:
            self._item_menu.destroy()
            self._item_menu = None
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
        if self._presentation is not None:
            self._presentation.stop()
        super().stop()

    def on_clicked(self) -> None:
        if self._popup is not None and self._popup.get_visible():
            self._popup.hide()
            return
        if self._presentation is not None and self._presentation.clicked():
            return
        self._refresh_now()
        self._position_platform_presentation()
        self._show_popup()

    @property
    def popup_visible(self) -> bool:
        return self._popup is not None and self._popup.get_visible()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = [
            disabled_menu_item(self._menu_header(), gtk=Gtk),
        ]
        legacy = (
            self._presentation.menu_items() if self._presentation is not None else []
        )
        primary: list[Gtk.MenuItem] = []
        for item in self._state.items[:12]:
            root = Gtk.MenuItem(label=item.display_title)
            submenu = Gtk.Menu()
            activate = Gtk.MenuItem(label=_("Activate"))
            activate.set_sensitive(item.can_activate)
            activate.connect(
                "activate",
                lambda _w, identifier=item.identifier: self._on_activate(identifier),
            )
            context = Gtk.MenuItem(label=_("Context Menu"))
            context.set_sensitive(item.can_context_menu)
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

    def _request_current(self, service: TrayService, generation: int) -> bool:
        return (
            self._active and service is self._service and generation == self._generation
        )

    def _run_tray_action(
        self, *, name: str, action: Callable[[TrayService], object]
    ) -> None:
        """Run a blocking tray operation off the main thread, then re-poll."""
        if not self._active:
            return
        service, generation = self._service, self._generation
        self._worker.run(
            name=name,
            fn=lambda: (
                action(service) if self._request_current(service, generation) else None
            ),
            on_result=lambda _result: (
                service.refresh()
                if self._request_current(service, generation)
                else None
            ),
        )

    def _refresh_now(self) -> None:
        """Ask for a fresh poll; the result arrives through the listener."""
        self._service.refresh()

    def _on_state_result(self, state: TrayState) -> bool:
        if state != self._state:
            self._state = state
            self.present()
            if self._popup is not None and self._popup.get_visible():
                self._show_popup()
        return False

    def _show_popup(self) -> None:
        self._position_platform_presentation()
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
            label = Gtk.Label(label=self._state.error or _("System Tray unavailable"))
            label.set_xalign(0)
            box.pack_start(label, False, False, 0)
            return box

        if not self._state.items:
            text = (
                self._presentation.empty_message()
                if self._presentation is not None
                else _("System Tray: waiting for tray apps")
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
        if item.can_double_click:
            target = Gtk.EventBox()
            target.set_visible_window(False)
            target.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            target.add(image)
            target.connect(
                "button-press-event",
                lambda _w, event, identifier=item.identifier: self._on_icon_press(
                    identifier, event
                ),
            )
            row.pack_start(target, False, False, 0)
        else:
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
        activate.set_sensitive(item.can_activate)
        activate.connect(
            "clicked",
            lambda _w, identifier=item.identifier: self._on_activate(identifier),
        )
        row.pack_start(activate, False, False, 0)

        menu = Gtk.Button(label=_("Menu"))
        menu.set_sensitive(item.can_context_menu)
        menu.connect(
            "clicked",
            lambda _w, identifier=item.identifier: self._show_item_menu(identifier),
        )
        row.pack_start(menu, False, False, 0)
        return row

    def _on_activate(self, identifier: str) -> None:
        if self._popup is not None:
            self._popup.hide()
        self._run_tray_action(
            name="systemtray-activate",
            action=lambda service: service.activate(identifier),
        )

    def _on_icon_press(self, identifier: str, event: Gdk.EventButton) -> bool:
        if event.button != 1 or event.type != Gdk.EventType.DOUBLE_BUTTON_PRESS:
            return False
        if self._popup is not None:
            self._popup.hide()
        self._run_tray_action(
            name="systemtray-double-click",
            action=lambda service: service.double_click(identifier),
        )
        return True

    def _show_item_menu(self, identifier: str) -> None:
        if not self._active:
            return
        service, generation = self._service, self._generation
        client = self._service.menu_client(identifier)
        if client is None:
            self._on_context_menu(identifier)
            return

        def fetched(layout: TrayMenuLayout | None) -> None:
            if not self._request_current(service, generation):
                return
            self._on_menu_layout(
                layout=layout,
                client=client,
                identifier=identifier,
            )

        self._worker.run(
            name="systemtray-menu",
            fn=lambda: (
                self._fetch_menu_layout(client)
                if self._request_current(service, generation)
                else None
            ),
            on_result=fetched,
            on_error=lambda _exc: (
                self._on_context_menu(identifier)
                if self._request_current(service, generation)
                else None
            ),
        )

    def _fetch_menu_layout(self, client: TrayMenuClient) -> TrayMenuLayout | None:
        """Run off the main thread: menu handshake only, no GTK."""
        client.about_to_show(0)
        layout = client.get_layout()
        if (
            layout is not None
            and layout.root.item_id != 0
            and client.about_to_show(layout.root.item_id)
        ):
            layout = client.get_layout() or layout
        return layout

    def _on_menu_layout(
        self,
        *,
        layout: TrayMenuLayout | None,
        client: TrayMenuClient,
        identifier: str,
    ) -> None:
        # The layout arrives on a worker thread; the applet may be gone by now.
        if not self._active:
            return
        if layout is None:
            self._on_context_menu(identifier)
            return
        menu = self._gtk_menu_from_tray_menu(root=layout.root, client=client)
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
        if not self._active:
            return
        if self._popup is not None:
            self._popup.hide()
        self._run_tray_action(
            name="systemtray-context-menu",
            action=lambda service: service.context_menu(identifier),
        )

    def _position_platform_presentation(self) -> None:
        if self._presentation is not None:
            self._presentation.position()

    def _on_legacy_changed(self) -> None:
        self._position_platform_presentation()
        self.present()
        if self._popup is not None and self._popup.get_visible():
            self._show_popup()

    def _gtk_menu_from_tray_menu(
        self,
        *,
        root: TrayMenuItem,
        client: TrayMenuClient,
    ) -> Gtk.Menu | None:
        menu = Gtk.Menu()
        for child in root.children:
            menu_item = self._gtk_menu_item_from_tray_item(item=child, client=client)
            if menu_item is not None:
                menu.append(menu_item)
        if not menu.get_children():
            return None
        return menu

    def _gtk_menu_item_from_tray_item(
        self,
        *,
        item: TrayMenuItem,
        client: TrayMenuClient,
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
                child_item = self._gtk_menu_item_from_tray_item(
                    item=child,
                    client=client,
                )
                if child_item is not None:
                    submenu.append(child_item)
            menu_item.set_submenu(submenu)
            submenu.connect(
                "show",
                lambda _w, item_id=item.item_id: self._worker.run(
                    name="systemtray-submenu",
                    fn=lambda: client.about_to_show(item_id),
                ),
            )
        else:
            menu_item.connect(
                "activate",
                lambda _w, item_id=item.item_id: self._on_tray_menu_activate(
                    client=client,
                    item_id=item_id,
                ),
            )
        return menu_item

    def _on_tray_menu_activate(self, *, client: TrayMenuClient, item_id: int) -> None:
        self._run_tray_action(
            name="systemtray-menu-event",
            action=lambda _service: client.event(item_id),
        )

    def _menu_header(self) -> str:
        if not self._state.available:
            return _("System Tray unavailable")
        if self._state.mode == "host":
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


def _load_menu_icon(*, item: TrayMenuItem, size: int) -> GdkPixbuf.Pixbuf | None:
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
