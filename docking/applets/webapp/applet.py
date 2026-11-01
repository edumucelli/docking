"""Shared Docking lifecycle and GTK actions for persistent web applets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.platform import AppletPlatformServices
from docking.applets.webapp.profile import BrowserProfile
from docking.applets.webapp.state import BrowserPhase, WebAppState
from docking.i18n import _
from docking.platform import targets

if TYPE_CHECKING:
    from docking.core.config import Config

STARTUP_DELAY_S = 2


class WebAppApplet(PlatformServicesApplet):
    """Run one persistent browser session from a dock applet."""

    profile: ClassVar[BrowserProfile]
    state_factory = WebAppState
    web_url = ""
    open_label = ""
    hide_label = ""
    disconnect_label = ""
    disconnect_question = ""
    disconnect_description = ""
    disconnect_error = ""

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._state = self.state_factory()
        self._startup_source_id = 0
        self._running = False
        self._browser = platform_services.backends.browser()(
            profile=self.profile,
            on_phase=self._on_browser_phase,
            on_title=self._on_browser_title,
            on_badge=self._on_browser_badge,
            on_notification=self._on_browser_notification,
            on_attention_cleared=self._clear_notification_attention,
        )
        super().__init__(
            icon_size=icon_size, config=config, platform_services=platform_services
        )
        self.present()

    def _tooltip_text(self) -> str:
        raise NotImplementedError

    def refresh_tooltip(self) -> None:
        self.item.name = self._tooltip_text()
        self.item.badge_count = self._state.badge_count
        self.item.badge_visible = self._state.badge_count > 0

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._running = True
        self._startup_source_id = GLib.timeout_add_seconds(
            STARTUP_DELAY_S,
            self._start_browser,
        )

    def stop(self) -> None:
        self._running = False
        if self._startup_source_id:
            GLib.source_remove(self._startup_source_id)
            self._startup_source_id = 0
        self._browser.stop()
        super().stop()

    def on_clicked(self) -> None:
        self._cancel_startup_delay()
        opening = not self._browser.visible
        self._browser.toggle()
        if opening:
            self._clear_notification_attention()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [disabled_menu_item(self._tooltip_text(), gtk=Gtk)]

        toggle_label = self.hide_label if self._browser.visible else self.open_label
        toggle = Gtk.MenuItem(label=toggle_label)
        toggle.connect("activate", lambda _item: self.on_clicked())

        reload_item = Gtk.MenuItem(label=_("Reload"))
        reload_item.set_sensitive(
            self._state.phase not in {BrowserPhase.UNAVAILABLE, BrowserPhase.STOPPED}
        )
        reload_item.connect("activate", lambda _item: self._browser.reload())

        open_browser = Gtk.MenuItem(label=_("Open in Browser"))
        open_browser.connect("activate", lambda _item: self._open_in_browser())

        disconnect = Gtk.MenuItem(label=self.disconnect_label)
        disconnect.set_sensitive(
            self._state.phase not in {BrowserPhase.UNAVAILABLE, BrowserPhase.STOPPED}
        )
        disconnect.connect("activate", lambda _item: self._confirm_disconnect())

        return menu_sections(
            status=status,
            primary=[toggle, reload_item, open_browser],
            manage=[disconnect],
            gtk=Gtk,
        )

    def _start_browser(self) -> bool:
        self._startup_source_id = 0
        self._browser.start()
        return False

    def _cancel_startup_delay(self) -> None:
        if self._startup_source_id:
            GLib.source_remove(self._startup_source_id)
            self._startup_source_id = 0

    def _on_browser_phase(self, phase: BrowserPhase, error: str) -> None:
        state = (
            self._state.reset_page_badge()
            if phase is BrowserPhase.STARTING
            else self._state
        )
        self._state = replace(state, phase=phase, error=error)
        self.present()

    def _on_browser_title(self, title: str) -> None:
        self._state = self._state.with_title(title)
        self.present()

    def _on_browser_badge(self, count: int | None, visible: bool) -> None:
        self._state = self._state.with_api_badge(count, visible=visible)
        self.present()

    def _on_browser_notification(self) -> None:
        if self._browser.visible:
            return
        self._state = self._state.with_notification()
        self.present()

    def _clear_notification_attention(self) -> None:
        state = self._state.clear_notification_fallback()
        if state is self._state:
            return
        self._state = state
        self.present()

    def _open_in_browser(self) -> None:
        targets.open_target(self.web_url)

    def _confirm_disconnect(self) -> None:
        parent = self.popup_anchor.parent if self.popup_anchor else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=self.disconnect_question,
        )
        dialog.format_secondary_text(self.disconnect_description)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Disconnect"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._browser.clear_session(self._on_session_cleared)

    def _on_session_cleared(self, success: bool, error: str) -> None:
        if not self._running:
            return
        if success:
            self._browser.show()
            return
        parent = self.popup_anchor.parent if self.popup_anchor else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=self.disconnect_error,
        )
        dialog.format_secondary_text(error or _("Unknown browser error"))
        dialog.run()
        dialog.destroy()
