"""GTK device selection, battery display, and explicit phone actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.kdeconnect import meta
from docking.applets.kdeconnect.backend import (
    ConnectError,
    KdeConnectBackend,
    open_settings,
)
from docking.applets.kdeconnect.render import render_icon
from docking.applets.kdeconnect.state import (
    RING_PLUGIN,
    SHARE_PLUGIN,
    ConnectState,
    Device,
    device_label,
    local_files,
    selected_device,
    selected_from_prefs,
    tooltip,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.applets.worker import BackgroundWorker
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

REFRESH_SECONDS = 30


class KdeconnectApplet(Applet):
    id = meta.id
    name = _("KDE Connect")
    icon_name = "smartphone"
    backend_factory = KdeConnectBackend

    def __init__(self, icon_size: int, config: Config) -> None:
        self._backend = self.backend_factory()
        self._worker = BackgroundWorker()
        self._state = ConnectState()
        self._selected = selected_from_prefs(config.applet_prefs.get(meta.id, {}))
        self._status = ""
        self._running = False
        self._epoch = 0
        self._refreshing = False
        self._refresh_again = False
        self._action_running = False
        self._timer = 0
        self._refresh_source = 0
        super().__init__(icon_size, config)
        self.present()

    def _device(self) -> Device | None:
        return selected_device(self._state, self._selected)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        device = self._device()
        return render_icon(
            size=size,
            connected=bool(device and device.reachable),
            charge=device.battery if device and device.reachable else None,
            charging=bool(device and device.reachable and device.charging),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip(self._state, self._selected, self._status)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._running = True
        self._epoch += 1
        self._backend.start(self._changed)
        self._timer = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self._changed()

    def stop(self) -> None:
        self._running = False
        self._epoch += 1
        self._backend.close()
        for source in (self._timer, self._refresh_source):
            if source:
                GLib.source_remove(source)
        self._timer = self._refresh_source = 0
        self._refreshing = self._refresh_again = self._action_running = False
        super().stop()

    def _tick(self) -> bool:
        self._changed()
        return self._running

    def _changed(self) -> None:
        if self._running and not self._refresh_source:
            # Device, plugin, and battery signals often arrive as a burst.
            self._refresh_source = GLib.timeout_add(75, self._refresh_idle)

    def _refresh_idle(self) -> bool:
        self._refresh_source = 0
        self._refresh()
        return False

    def _refresh(self) -> None:
        if not self._running:
            return
        if self._refreshing:
            self._refresh_again = True
            return
        epoch = self._epoch
        self._refreshing = True

        def updated(state: ConnectState) -> None:
            if not self._running or self._epoch != epoch:
                return
            self._refreshing = False
            self._state = state
            device = self._device()
            if not self._selected and device is not None:
                self._select(device.device_id)
            self.present()
            if self._refresh_again:
                self._refresh_again = False
                self._changed()

        def failed(_error: Exception) -> None:
            updated(ConnectState(error=_("Could not read KDE Connect devices")))

        self._worker.run(
            name="kdeconnect-refresh",
            fn=self._backend.snapshot,
            on_result=updated,
            on_error=failed,
        )

    def _select(self, device_id: str) -> None:
        self._selected = device_id
        self._status = ""
        self.save_prefs({"selected_device": device_id})
        self.present()

    def on_clicked(self) -> None:
        self._open_settings()

    def _open_settings(self) -> None:
        if not open_settings():
            self._status = _("Install KDE Connect to open its device settings")
            self.present()

    def _action(self, action: Callable[[], None], success: str) -> None:
        if not self._running or self._action_running:
            return
        epoch = self._epoch
        self._action_running = True
        self._status = _("Contacting selected device...")
        self.present()

        def done(message: str) -> None:
            if self._running and self._epoch == epoch:
                self._action_running = False
                self._status = message
                self.present()
                self._changed()

        self._worker.run(
            name="kdeconnect-action",
            fn=action,
            on_result=lambda _result: done(success),
            on_error=lambda error: done(
                str(error)
                if isinstance(error, ConnectError)
                else _("KDE Connect action failed; retry with the device online")
            ),
        )

    def _ring(self) -> None:
        device = self._device()
        if device is not None and device.supports(RING_PLUGIN):
            self._action(
                lambda: self._backend.ring(device.device_id),
                _("Ring request sent to {name}").format(name=device.name),
            )

    def accepts_drop_uris(self) -> bool:
        device = self._device()
        return bool(
            self._running
            and device
            and device.supports(SHARE_PLUGIN)
            and not self._action_running
        )

    def on_drop_uris(self, uris: list[str]) -> bool:
        return self._send_files(self._device(), uris)

    def _send_files(self, device: Device | None, uris: list[str]) -> bool:
        if not self._running:
            return False
        if device is None or not device.supports(SHARE_PLUGIN):
            self._status = _("Select an online paired device with file sharing enabled")
            self.present()
            return True
        if self._action_running:
            self._status = _("Wait for the current KDE Connect action to finish")
            self.present()
            return True
        try:
            paths = local_files(uris)
        except (ValueError, OSError) as error:
            self._status = (
                str(error)
                if isinstance(error, ValueError)
                else _("Could not read the selected files")
            )
            self.present()
            return True
        self._action(
            lambda: self._backend.send_files(device.device_id, paths),
            _("File transfer requested for {name}").format(name=device.name),
        )
        return True

    def _choose_files(self) -> None:
        device = self._device()
        if device is None or not device.supports(SHARE_PLUGIN):
            return
        dialog = Gtk.FileChooserDialog(
            title=_("Send Files with KDE Connect"),
            transient_for=self.popup_anchor.parent if self.popup_anchor else None,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Send"), Gtk.ResponseType.OK)
        dialog.set_modal(True)
        dialog.set_select_multiple(True)
        if dialog.run() == Gtk.ResponseType.OK:
            # Keep the recipient shown when the chooser opened, even if a
            # device signal changes the current selection during its GTK loop.
            self._send_files(device, dialog.get_uris())
        dialog.destroy()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        device = self._device()
        status = [
            disabled_menu_item(
                tooltip(self._state, self._selected, self._status), gtk=Gtk
            )
        ]
        devices = radio_menu_items(
            choices=((device_label(d), d.device_id) for d in self._state.devices),
            active_value=self._selected,
            on_selected=lambda _widget, device_id: self._select(device_id),
            gtk=Gtk,
        )
        ring = Gtk.MenuItem(label=_("Ring Phone"))
        ring.set_sensitive(
            bool(device and device.supports(RING_PLUGIN) and not self._action_running)
        )
        ring.connect("activate", lambda _widget: self._ring())
        send = Gtk.MenuItem(label=_("Send Files..."))
        send.set_sensitive(self.accepts_drop_uris())
        send.connect("activate", lambda _widget: self._choose_files())
        settings = Gtk.MenuItem(label=_("Open KDE Connect / Pair Device"))
        settings.connect("activate", lambda _widget: self._open_settings())
        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _widget: self._refresh())
        return menu_sections(
            status=status,
            primary=devices,
            navigation=[ring, send],
            refresh=[refresh],
            manage=[settings],
            gtk=Gtk,
        )
