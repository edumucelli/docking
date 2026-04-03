"""GTK lifecycle glue for the Gmail applet."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.gmail import meta, storage
from docking.applets.gmail.api import fetch_inbox_state
from docking.applets.gmail.auth import (
    GmailAuthError,
    GmailClientConfigError,
    GmailInvalidGrantError,
    parse_client_config_json,
    revoke_credentials,
    run_oauth_flow,
)
from docking.applets.gmail.render import create_icon
from docking.applets.gmail.state import (
    GMAIL_COMPOSE_URL,
    GMAIL_INBOX_URL,
    POLL_INTERVAL_OPTIONS,
    GmailAppletState,
    GmailPollResult,
    GmailPrefs,
    GmailStatus,
    build_tooltip,
    initial_applet_state,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.popup import wrap_popup
from docking.applets.worker import BackgroundWorker
from docking.i18n import _, ngettext
from docking.log import get_logger, with_context
from docking.ui.display import get_pointer_position

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="gmail"), applet_id=meta.id)

CONNECT_DIALOG_WIDTH_PX = 340
POPUP_WIDTH_PX = 360
POPUP_PADDING_PX = 12
POPUP_SPACING_PX = 8
POPUP_CURSOR_GAP_PX = 20
STARTUP_REFRESH_DELAY_S = 1


class GmailApplet(Applet):
    """Unread Gmail badge, tooltip, and popup applet."""

    id = meta.id
    name = _("Gmail")
    icon_name = "mail-unread"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._worker = BackgroundWorker(logger=log)
        self._timer_id = 0
        self._startup_refresh_id = 0
        self._popup: Gtk.Window | None = None
        self._connect_dialog: Gtk.Dialog | None = None
        self._storage_available = storage.secret_storage_available()
        self._prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )

        client_configured = False
        if self._storage_available:
            client_configured = self._has_client_config()
        self._state = initial_applet_state(
            prefs=self._prefs,
            client_configured=client_configured,
        )
        if not self._storage_available:
            self._state = GmailAppletState(
                status=GmailStatus.ERROR,
                info_text=_("Secret Service is unavailable"),
            )

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            status=self._state.status,
            unread_count=self._state.unread_count,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            state=self._state,
            max_rows=self._prefs.max_preview_rows,
        )

    def on_clicked(self) -> None:
        if self._state.status == GmailStatus.CONNECTING:
            return
        if not self._storage_available:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.ERROR,
                    info_text=_("Secret Service is unavailable"),
                )
            )
            return
        if self._state.status in {
            GmailStatus.UNCONFIGURED,
            GmailStatus.DISCONNECTED,
            GmailStatus.ERROR,
            GmailStatus.RECONNECT_REQUIRED,
        }:
            self._start_connect_flow()
            return
        if self._state.unread_count > 0 and self._prefs.show_popup_on_click:
            self._toggle_popup()
            return
        if self._prefs.open_on_click_when_empty:
            self._open_inbox()
            return
        self._toggle_popup()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        if not self._storage_available:
            unavailable = Gtk.MenuItem(label=_("Secret Service is unavailable"))
            unavailable.set_sensitive(False)
            return [unavailable]

        if self._state.account_email:
            header = Gtk.MenuItem(label=self._state.account_email)
            header.set_sensitive(False)
            items.append(header)

        if not self._state.client_configured:
            connect = Gtk.MenuItem(label=_("Connect Gmail..."))
            connect.connect("activate", lambda _w: self._start_connect_flow())
            items.append(connect)

            import_client = Gtk.MenuItem(label=_("Import OAuth Client JSON..."))
            import_client.connect(
                "activate",
                lambda _w: self._import_client_json_interactive(),
            )
            items.append(import_client)

            info = Gtk.MenuItem(
                label=_("Requires your own Google Desktop OAuth client JSON")
            )
            info.set_sensitive(False)
            items.append(info)
            return items

        if self._state.status in {
            GmailStatus.CONNECTED,
            GmailStatus.STALE,
            GmailStatus.RECONNECT_REQUIRED,
        }:
            open_gmail = Gtk.MenuItem(label=_("Open Gmail"))
            open_gmail.connect("activate", lambda _w: self._open_inbox())
            items.append(open_gmail)

            compose = Gtk.MenuItem(label=_("Compose"))
            compose.connect("activate", lambda _w: self._open_compose())
            items.append(compose)

            refresh = Gtk.MenuItem(label=_("Refresh Now"))
            refresh.connect("activate", lambda _w: self._refresh_now())
            items.append(refresh)

        reconnect = Gtk.MenuItem(label=_("Reconnect"))
        reconnect.connect("activate", lambda _w: self._reconnect())
        items.append(reconnect)

        disconnect = Gtk.MenuItem(label=_("Disconnect"))
        disconnect.connect("activate", lambda _w: self._disconnect())
        items.append(disconnect)

        items.append(self._build_poll_interval_menu())
        return items

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._restart_poll_timer()
        if self._prefs.connected and self._storage_available:
            self._startup_refresh_id = GLib.timeout_add_seconds(
                STARTUP_REFRESH_DELAY_S,
                self._run_startup_refresh,
            )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._startup_refresh_id:
            GLib.source_remove(self._startup_refresh_id)
            self._startup_refresh_id = 0
        self._hide_popup()
        self._destroy_connect_dialog()
        super().stop()

    def _run_startup_refresh(self) -> bool:
        self._startup_refresh_id = 0
        self._refresh_now()
        return False

    def _restart_poll_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add_seconds(
            self._prefs.poll_interval_s,
            self._tick,
        )

    def _tick(self) -> bool:
        self._refresh_now()
        return True

    def _refresh_now(self) -> None:
        if self._state.status == GmailStatus.CONNECTING or not self._storage_available:
            return
        self._worker.run_guarded(
            key="gmail-poll",
            name="gmail-poll",
            fn=self._poll_worker,
            on_result=self._on_poll_result,
            on_error=self._on_poll_error,
        )

    def _poll_worker(self) -> tuple[GmailPollResult, dict[str, object]]:
        credentials = storage.load_credentials(applet_id=self.desktop_id)
        if credentials is None:
            raise GmailAuthError("No Gmail credentials stored")
        return fetch_inbox_state(
            credentials_info=credentials,
            max_results=self._prefs.max_preview_rows,
        )

    def _on_poll_result(
        self,
        payload: tuple[GmailPollResult, dict[str, object]],
    ) -> bool:
        result, refreshed_credentials = payload
        storage.save_credentials(
            applet_id=self.desktop_id,
            credentials=refreshed_credentials,
        )
        self._prefs = GmailPrefs(
            account_email=result.account_email,
            connected=True,
            poll_interval_s=self._prefs.poll_interval_s,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email=result.account_email,
                unread_count=result.unread_count,
                messages=result.messages,
                history_id=result.history_id,
            )
        )
        return False

    def _on_poll_error(self, exc: Exception) -> bool:
        if isinstance(exc, GmailInvalidGrantError):
            storage.delete_credentials(applet_id=self.desktop_id)
            self._prefs = GmailPrefs(
                account_email=self._prefs.account_email,
                connected=False,
                poll_interval_s=self._prefs.poll_interval_s,
                max_preview_rows=self._prefs.max_preview_rows,
                open_on_click_when_empty=self._prefs.open_on_click_when_empty,
                show_popup_on_click=self._prefs.show_popup_on_click,
            )
            self._save_prefs()
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.RECONNECT_REQUIRED,
                    client_configured=True,
                    account_email=self._prefs.account_email,
                    unread_count=self._state.unread_count,
                    messages=self._state.messages,
                    history_id=self._state.history_id,
                    info_text=_("Google rejected the saved refresh token"),
                )
            )
            return False

        if isinstance(exc, GmailAuthError):
            self._prefs = GmailPrefs(
                account_email=self._prefs.account_email,
                connected=False,
                poll_interval_s=self._prefs.poll_interval_s,
                max_preview_rows=self._prefs.max_preview_rows,
                open_on_click_when_empty=self._prefs.open_on_click_when_empty,
                show_popup_on_click=self._prefs.show_popup_on_click,
            )
            self._save_prefs()
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.DISCONNECTED
                    if self._state.client_configured
                    else GmailStatus.UNCONFIGURED,
                    client_configured=self._state.client_configured,
                    account_email=self._prefs.account_email,
                    info_text=str(exc),
                )
            )
            return False

        previous = self._state
        if previous.status in {GmailStatus.CONNECTED, GmailStatus.STALE}:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.STALE,
                    client_configured=previous.client_configured,
                    account_email=previous.account_email,
                    unread_count=previous.unread_count,
                    messages=previous.messages,
                    history_id=previous.history_id,
                    info_text=str(exc),
                )
            )
            return False

        self._set_state(
            GmailAppletState(
                status=GmailStatus.ERROR,
                client_configured=self._state.client_configured,
                account_email=self._state.account_email,
                info_text=str(exc),
            )
        )
        return False

    def _start_connect_flow(self) -> None:
        if not self._storage_available:
            return
        if (
            not self._state.client_configured
            and not self._import_client_json_interactive()
        ):
            return

        if self._state.status == GmailStatus.CONNECTING:
            return
        self._show_connect_dialog()
        self._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTING,
                client_configured=True,
                account_email=self._prefs.account_email,
                unread_count=self._state.unread_count,
                messages=self._state.messages,
            )
        )
        self._worker.run_guarded(
            key="gmail-connect",
            name="gmail-connect",
            fn=self._connect_worker,
            on_result=self._on_connect_result,
            on_error=self._on_connect_error,
        )

    def _connect_worker(self) -> tuple[GmailPollResult, dict[str, object]]:
        client_config = storage.load_client_config(applet_id=self.desktop_id)
        if client_config is None:
            raise GmailClientConfigError(_("No OAuth client JSON has been imported"))
        credentials = run_oauth_flow(
            client_config=client_config,
            open_authorization_url=self._open_authorization_url,
        )
        return fetch_inbox_state(
            credentials_info=credentials,
            max_results=self._prefs.max_preview_rows,
        )

    def _on_connect_result(
        self, payload: tuple[GmailPollResult, dict[str, object]]
    ) -> bool:
        self._destroy_connect_dialog()
        result, refreshed_credentials = payload
        storage.save_credentials(
            applet_id=self.desktop_id,
            credentials=refreshed_credentials,
        )
        self._prefs = GmailPrefs(
            account_email=result.account_email,
            connected=True,
            poll_interval_s=self._prefs.poll_interval_s,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email=result.account_email,
                unread_count=result.unread_count,
                messages=result.messages,
                history_id=result.history_id,
            )
        )
        return False

    def _on_connect_error(self, exc: Exception) -> bool:
        self._destroy_connect_dialog()
        self._prefs = GmailPrefs(
            account_email=self._prefs.account_email,
            connected=False,
            poll_interval_s=self._prefs.poll_interval_s,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._set_state(
            GmailAppletState(
                status=GmailStatus.ERROR,
                client_configured=self._has_client_config(),
                account_email=self._prefs.account_email,
                info_text=str(exc),
            )
        )
        return False

    def _reconnect(self) -> None:
        if not self._storage_available:
            return
        try:
            storage.delete_credentials(applet_id=self.desktop_id)
        except storage.SecretStorageError as exc:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.ERROR,
                    client_configured=self._state.client_configured,
                    account_email=self._prefs.account_email,
                    info_text=str(exc),
                )
            )
            return
        self._prefs = GmailPrefs(
            account_email=self._prefs.account_email,
            connected=False,
            poll_interval_s=self._prefs.poll_interval_s,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._start_connect_flow()

    def _disconnect(self) -> None:
        if not self._storage_available:
            return
        credentials = None
        try:
            credentials = storage.load_credentials(applet_id=self.desktop_id)
            storage.clear_all(applet_id=self.desktop_id)
        except storage.SecretStorageError as exc:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.ERROR,
                    client_configured=True,
                    account_email=self._prefs.account_email,
                    info_text=str(exc),
                )
            )
            return
        self._hide_popup()
        self._prefs = GmailPrefs(
            account_email="",
            connected=False,
            poll_interval_s=self._prefs.poll_interval_s,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._set_state(GmailAppletState(status=GmailStatus.UNCONFIGURED))
        if credentials is not None:
            self._worker.run(
                name="gmail-revoke",
                fn=lambda: revoke_credentials(credentials),
            )

    def _import_client_json_interactive(self) -> bool:
        if not self._storage_available:
            return False
        dialog = Gtk.FileChooserDialog(
            title=_("Import OAuth Client JSON"),
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN,
            Gtk.ResponseType.ACCEPT,
        )
        file_filter = Gtk.FileFilter()
        file_filter.set_name(_("JSON files"))
        file_filter.add_pattern("*.json")
        dialog.add_filter(file_filter)
        response = dialog.run()
        filename = (
            dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        )
        dialog.destroy()
        if not filename:
            return False
        return self._import_client_json_file(Path(filename))

    def _import_client_json_file(self, path: Path) -> bool:
        try:
            client_config = parse_client_config_json(path.read_text(encoding="utf-8"))
            storage.save_client_config(
                applet_id=self.desktop_id,
                client_config=client_config,
            )
        except (OSError, GmailAuthError, storage.SecretStorageError) as exc:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.ERROR,
                    client_configured=self._state.client_configured,
                    account_email=self._prefs.account_email,
                    info_text=str(exc),
                )
            )
            return False
        self._set_state(
            GmailAppletState(
                status=GmailStatus.DISCONNECTED,
                client_configured=True,
                account_email=self._prefs.account_email,
            )
        )
        return True

    def _build_poll_interval_menu(self) -> Gtk.MenuItem:
        root = Gtk.MenuItem(label=_("Poll Interval"))
        submenu = Gtk.Menu()
        group: Gtk.RadioMenuItem | None = None
        for seconds in POLL_INTERVAL_OPTIONS:
            label = _poll_label(seconds)
            item = Gtk.RadioMenuItem(label=label)
            if group is None:
                group = item
            else:
                item.join_group(group)
            item.set_active(self._prefs.poll_interval_s == seconds)
            item.connect("toggled", self._on_poll_interval_toggled, seconds)
            submenu.append(item)
        root.set_submenu(submenu)
        return root

    def _on_poll_interval_toggled(
        self,
        widget: Gtk.CheckMenuItem,
        seconds: int,
    ) -> None:
        if not widget.get_active() or seconds == self._prefs.poll_interval_s:
            return
        self._prefs = GmailPrefs(
            account_email=self._prefs.account_email,
            connected=self._prefs.connected,
            poll_interval_s=seconds,
            max_preview_rows=self._prefs.max_preview_rows,
            open_on_click_when_empty=self._prefs.open_on_click_when_empty,
            show_popup_on_click=self._prefs.show_popup_on_click,
        )
        self._save_prefs()
        self._restart_poll_timer()

    def _toggle_popup(self) -> None:
        if self._popup and self._popup.get_visible():
            self._popup.hide()
            return
        self._show_popup()

    def _show_popup(self) -> None:
        if self._popup is None:
            self._popup = Gtk.Window(type=Gtk.WindowType.POPUP)
            self._popup.set_decorated(False)
            self._popup.set_skip_taskbar_hint(True)
            self._popup.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
            self._popup.connect("focus-out-event", self._on_popup_focus_out)

        child = self._popup.get_child()
        if child:
            self._popup.remove(child)

        self._popup.add(wrap_popup(self._build_popup_content()))
        self._popup.show_all()
        self._position_popup()

    def _hide_popup(self) -> None:
        if self._popup:
            self._popup.hide()

    def _on_popup_focus_out(self, *_args) -> bool:
        self._hide_popup()
        return False

    def _position_popup(self) -> None:
        if self._popup is None:
            return
        display = Gdk.Display.get_default()
        pos = get_pointer_position(display)
        mouse_x = pos.x if pos is not None else 0
        mouse_y = pos.y if pos is not None else 0

        preferred = self._popup.get_preferred_size()[1]
        popup_w = max(preferred.width, POPUP_WIDTH_PX)
        popup_h = max(preferred.height, 1)
        screen = self._popup.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()

        popup_x = max(0, min(int(mouse_x - popup_w / 2), screen_w - popup_w))
        popup_y = max(
            0,
            min(int(mouse_y - popup_h - POPUP_CURSOR_GAP_PX), screen_h - popup_h),
        )
        self._popup.move(popup_x, popup_y)

    def _build_popup_content(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=POPUP_SPACING_PX)
        box.set_size_request(POPUP_WIDTH_PX, -1)
        box.set_margin_start(POPUP_PADDING_PX)
        box.set_margin_end(POPUP_PADDING_PX)
        box.set_margin_top(POPUP_PADDING_PX)
        box.set_margin_bottom(POPUP_PADDING_PX)

        header = Gtk.Label()
        header.set_xalign(0.0)
        header.set_markup(
            f"<b>{GLib.markup_escape_text(self._state.account_email or _('Gmail'))}</b>"
        )
        box.pack_start(header, False, False, 0)

        unread_label = Gtk.Label()
        unread_label.set_xalign(0.0)
        unread_label.set_text(
            ngettext(
                "{n} unread inbox message",
                "{n} unread inbox messages",
                self._state.unread_count,
            ).format(n=self._state.unread_count)
        )
        box.pack_start(unread_label, False, False, 0)

        for message in self._state.messages[: self._prefs.max_preview_rows]:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            from_label = Gtk.Label()
            from_label.set_xalign(0.0)
            from_label.set_markup(
                f"<b>{GLib.markup_escape_text(message.from_text)}</b>"
            )
            subject_label = Gtk.Label()
            subject_label.set_xalign(0.0)
            subject_label.set_line_wrap(True)
            subject_label.set_text(
                _("{subject} - {date}").format(
                    subject=message.subject,
                    date=message.date_text,
                )
            )
            row.pack_start(from_label, False, False, 0)
            row.pack_start(subject_label, False, False, 0)
            box.pack_start(row, False, False, 0)

        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=POPUP_SPACING_PX,
        )
        for label, callback in (
            (_("Open Gmail"), self._open_inbox),
            (_("Compose"), self._open_compose),
            (_("Refresh"), self._refresh_now),
            (_("Disconnect"), self._disconnect),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _w, fn=callback: fn())
            buttons.pack_start(button, True, True, 0)
        box.pack_start(buttons, False, False, 0)
        return box

    def _show_connect_dialog(self) -> None:
        self._destroy_connect_dialog()
        dialog = Gtk.Dialog(
            title=_("Connect Gmail"),
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.set_default_size(CONNECT_DIALOG_WIDTH_PX, -1)
        dialog.set_resizable(False)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        primary = Gtk.Label(
            label=_("Docking is opening Gmail sign-in in your browser.")
        )
        primary.set_xalign(0.0)
        primary.set_line_wrap(True)
        secondary = Gtk.Label(label=_("Complete sign-in there, then return here."))
        secondary.set_xalign(0.0)
        secondary.set_line_wrap(True)
        fallback = Gtk.Label(
            label=_(
                "If no browser opens or sign-in is blocked, this dialog will close "
                "with an error after a short timeout."
            )
        )
        fallback.set_xalign(0.0)
        fallback.set_line_wrap(True)
        content.pack_start(primary, False, False, 0)
        content.pack_start(secondary, False, False, 0)
        content.pack_start(fallback, False, False, 0)
        dialog.show_all()
        self._connect_dialog = dialog

    def _destroy_connect_dialog(self) -> None:
        if self._connect_dialog is not None:
            self._connect_dialog.destroy()
            self._connect_dialog = None

    def _open_inbox(self) -> None:
        self._launch_uri(GMAIL_INBOX_URL)

    def _open_compose(self) -> None:
        self._launch_uri(GMAIL_COMPOSE_URL)

    def _launch_uri(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc:
            log.warning("Failed to open Gmail URI %s: %s", uri, exc)

    def _open_authorization_url(self, uri: str) -> None:
        launch_error: list[Exception] = []
        launched = threading.Event()

        def launch() -> bool:
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except Exception as exc:
                launch_error.append(exc)
            finally:
                launched.set()
            return False

        GLib.idle_add(launch)
        launched.wait(timeout=5)
        if launch_error:
            raise GmailAuthError(
                _("Failed to open the browser for Gmail sign-in: {reason}").format(
                    reason=str(launch_error[0])
                )
            )

    def _has_client_config(self) -> bool:
        if not self._storage_available:
            return False
        try:
            return storage.load_client_config(applet_id=self.desktop_id) is not None
        except storage.SecretStorageError as exc:
            self._set_state(
                GmailAppletState(
                    status=GmailStatus.ERROR,
                    info_text=str(exc),
                )
            )
            return False

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                account_email=self._prefs.account_email,
                connected=self._prefs.connected,
                poll_interval_s=self._prefs.poll_interval_s,
                max_preview_rows=self._prefs.max_preview_rows,
                open_on_click_when_empty=self._prefs.open_on_click_when_empty,
                show_popup_on_click=self._prefs.show_popup_on_click,
            )
        )

    def _set_state(self, state: GmailAppletState) -> None:
        self._state = state
        self.present()


def _poll_label(seconds: int) -> str:
    if seconds < 60:
        return ngettext("{n} seconds", "{n} seconds", seconds).format(n=seconds)
    minutes = seconds // 60
    return ngettext("{n} minute", "{n} minutes", minutes).format(n=minutes)
