"""GTK and WebKit runtime for the single-account WhatsApp Web session."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from functools import cache
from importlib import import_module
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from docking.applets.whatsapp.bridge import (
    BRIDGE_HANDLER,
    BRIDGE_SCRIPT,
    BridgeEvent,
    parse_bridge_message,
)
from docking.applets.whatsapp.notifications import DesktopNotifier
from docking.applets.whatsapp.state import (
    WHATSAPP_WEB_URL,
    BrowserPhase,
    NavigationTarget,
    navigation_target,
)
from docking.core.paths import ensure_dir
from docking.i18n import _
from docking.log import get_logger
from docking.platform.environment.xdg import docking_cache_dir, docking_data_dir

log = get_logger("whatsapp.browser")

DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 720
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 480
RECOVERY_DELAY_MS = 1500

PhaseCallback = Callable[[BrowserPhase, str], None]
TitleCallback = Callable[[str], None]
BadgeCallback = Callable[[int | None, bool], None]
NotificationCallback = Callable[[], None]
ClearCallback = Callable[[bool, str], None]


class WebKitUnavailableError(RuntimeError):
    """Raised when neither supported WebKitGTK namespace is installed."""


@cache
def load_webkit() -> Any:
    """Load WebKitGTK lazily, preferring the current libsoup 3 namespace."""
    errors: list[str] = []
    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            return import_module("gi.repository.WebKit2")
        except (ImportError, ValueError) as exc:
            errors.append(f"{version}: {exc}")
    raise WebKitUnavailableError("; ".join(errors))


def webkit_available() -> bool:
    """Return whether a supported WebKitGTK typelib can be imported."""
    try:
        load_webkit()
    except WebKitUnavailableError:
        return False
    return True


class WhatsAppBrowser:
    """Own one persistent WhatsApp Web view and its top-level GTK window."""

    def __init__(
        self,
        *,
        on_phase: PhaseCallback,
        on_title: TitleCallback,
        on_badge: BadgeCallback,
        on_notification: NotificationCallback,
        on_attention_cleared: NotificationCallback,
    ) -> None:
        self._on_phase = on_phase
        self._on_title = on_title
        self._on_badge = on_badge
        self._on_notification = on_notification
        self._on_attention_cleared = on_attention_cleared
        self._webkit: Any | None = None
        self._manager: Any | None = None
        self._content_manager: Any | None = None
        self._context: Any | None = None
        self._view: Any | None = None
        self._window: Gtk.Window | None = None
        self._phase = BrowserPhase.STOPPED
        self._started = False
        self._recovery_source_id = 0
        self._last_bridge_badge: tuple[int | None, bool] | None = None
        self._last_bridge_status: tuple[bool, str] | None = None
        self._notifier = DesktopNotifier(
            on_activate=self._activate_notification,
            on_shown=self._on_notification,
        )

    @property
    def phase(self) -> BrowserPhase:
        return self._phase

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.get_visible())

    def start(self) -> bool:
        """Start the hidden browser session so unread state remains live."""
        self._started = True
        return self._ensure_runtime()

    def show(self) -> None:
        """Create if needed, then present the WhatsApp window."""
        self._started = True
        if not self._ensure_runtime() or self._window is None:
            self._show_unavailable_dialog()
            return
        self._window.show_all()
        self._window.present()

    def hide(self) -> None:
        if self._window is not None:
            self._window.hide()

    def toggle(self) -> None:
        if self.visible:
            self.hide()
        else:
            self.show()

    def reload(self) -> None:
        if not self._ensure_runtime() or self._view is None:
            return
        self._set_phase(BrowserPhase.STARTING)
        self._view.reload()

    def clear_session(self, on_complete: ClearCallback) -> None:
        """Clear the dedicated website store, then return to the QR login page."""
        if not self._ensure_runtime() or self._manager is None:
            on_complete(False, _("WebKitGTK is unavailable"))
            return
        self._set_phase(BrowserPhase.STARTING)
        if self._view is not None:
            self._view.stop_loading()
            self._view.load_uri("about:blank")
        try:
            self._manager.clear(
                self._webkit.WebsiteDataTypes.ALL,
                0,
                None,
                self._on_session_cleared,
                on_complete,
            )
        except Exception as exc:
            message = _error_message(exc)
            self._set_phase(BrowserPhase.ERROR, message)
            on_complete(False, message)

    def stop(self) -> None:
        """Destroy UI and web objects without deleting the persisted session."""
        self._started = False
        if self._recovery_source_id:
            GLib.source_remove(self._recovery_source_id)
            self._recovery_source_id = 0
        self._notifier.stop()
        if self._content_manager is not None:
            with suppress(Exception):
                self._content_manager.unregister_script_message_handler(BRIDGE_HANDLER)
        if self._view is not None:
            with suppress(Exception):
                self._view.stop_loading()
        window = self._window
        self._window = None
        self._view = None
        self._context = None
        self._manager = None
        self._content_manager = None
        if window is not None:
            window.destroy()
        self._set_phase(BrowserPhase.STOPPED)

    def _ensure_runtime(self) -> bool:
        if self._view is not None:
            return True
        try:
            self._webkit = load_webkit()
            data_dir, cache_dir = _session_directories()
            manager = self._webkit.WebsiteDataManager(
                base_data_directory=str(data_dir),
                base_cache_directory=str(cache_dir),
            )
            cookies = manager.get_cookie_manager()
            cookies.set_persistent_storage(
                str(data_dir / "cookies.sqlite"),
                self._webkit.CookiePersistentStorage.SQLITE,
            )
            manager.set_persistent_credential_storage_enabled(True)

            context = self._webkit.WebContext.new_with_website_data_manager(manager)
            context.set_cache_model(self._webkit.CacheModel.DOCUMENT_BROWSER)
            context.connect(
                "initialize-notification-permissions",
                self._on_initialize_notification_permissions,
            )
            context.connect("download-started", self._on_download_started)

            view = self._webkit.WebView(web_context=context)
            self._configure_settings(view.get_settings())
            content_manager = view.get_user_content_manager()
            content_manager.connect(
                f"script-message-received::{BRIDGE_HANDLER}",
                self._on_script_message,
            )
            if not content_manager.register_script_message_handler(BRIDGE_HANDLER):
                raise RuntimeError("could not register WhatsApp page bridge")
            content_manager.add_script(
                self._webkit.UserScript.new(
                    BRIDGE_SCRIPT,
                    self._webkit.UserContentInjectedFrames.TOP_FRAME,
                    self._webkit.UserScriptInjectionTime.START,
                    [f"{WHATSAPP_WEB_URL}*"],
                    None,
                )
            )
            view.connect("notify::title", self._on_title_changed)
            view.connect("load-changed", self._on_load_changed)
            view.connect("load-failed", self._on_load_failed)
            view.connect("decide-policy", self._on_decide_policy)
            view.connect("create", self._on_create)
            view.connect("permission-request", self._on_permission_request)
            view.connect("show-notification", self._on_show_notification)
            view.connect("web-process-terminated", self._on_web_process_terminated)

            self._manager = manager
            self._content_manager = content_manager
            self._context = context
            self._view = view
            self._window = self._build_window(view)
            self._set_phase(BrowserPhase.STARTING)
            view.load_uri(WHATSAPP_WEB_URL)
        except WebKitUnavailableError as exc:
            log.info("WhatsApp applet unavailable: %s", exc)
            self._set_phase(BrowserPhase.UNAVAILABLE, str(exc))
            return False
        except Exception as exc:
            message = _error_message(exc)
            log.warning("Could not start WhatsApp Web: %s", message, exc_info=True)
            self._set_phase(BrowserPhase.ERROR, message)
            return False
        return True

    def _build_window(self, view: Any) -> Gtk.Window:
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title(_("WhatsApp"))
        window.set_default_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        window.set_size_request(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        window.set_skip_taskbar_hint(True)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_role("docking-whatsapp")
        window.connect("delete-event", self._on_window_delete)
        window.connect("key-press-event", self._on_window_key_press)

        header = Gtk.HeaderBar()
        header.set_title(_("WhatsApp"))
        header.set_subtitle(_("Docking applet"))
        header.set_show_close_button(True)
        refresh = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic",
            Gtk.IconSize.BUTTON,
        )
        refresh.set_tooltip_text(_("Reload WhatsApp Web"))
        refresh.connect("clicked", lambda _button: self.reload())
        header.pack_start(refresh)
        window.set_titlebar(header)
        window.add(view)
        view.show()
        return window

    def _configure_settings(self, settings: Any) -> None:
        settings.set_enable_javascript(True)
        for method_name in (
            "set_enable_media",
            "set_enable_media_stream",
            "set_enable_mediasource",
            "set_enable_webaudio",
            "set_enable_webrtc",
            "set_enable_webgl",
        ):
            method = getattr(settings, method_name, None)
            if callable(method):
                method(True)
        developer_extras = getattr(settings, "set_enable_developer_extras", None)
        if callable(developer_extras):
            developer_extras(False)

    def _set_phase(self, phase: BrowserPhase, error: str = "") -> None:
        if phase == self._phase and not error:
            return
        self._phase = phase
        self._on_phase(phase, error)

    def _on_window_delete(self, window: Gtk.Window, _event: object) -> bool:
        window.hide()
        return True

    def _on_window_key_press(self, window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            window.hide()
            return True
        return False

    def _on_title_changed(self, view: Any, _property: object) -> None:
        title = str(view.get_title() or "")
        log.debug("WhatsApp Web page title changed: %r", title[:160])
        self._on_title(title)

    def _on_load_changed(self, view: Any, event: Any) -> None:
        if event == self._webkit.LoadEvent.STARTED:
            self._last_bridge_badge = None
            self._last_bridge_status = None
            self._set_phase(BrowserPhase.STARTING)
        elif event == self._webkit.LoadEvent.FINISHED:
            if self._last_bridge_status is None:
                self._set_phase(BrowserPhase.SYNCING)
            title = str(view.get_title() or "")
            log.debug("WhatsApp Web finished loading with title: %r", title[:160])
            self._on_title(title)

    def _on_script_message(self, _manager: Any, result: Any) -> None:
        try:
            raw = str(result.get_js_value().to_string())
        except Exception as exc:
            log.debug("Could not read WhatsApp page bridge message: %s", exc)
            return
        event = parse_bridge_message(raw)
        if event is None:
            log.debug("Rejected invalid WhatsApp page bridge message")
            return
        if event.kind == "badge":
            current = (event.badge_count, event.badge_visible)
            if current != self._last_bridge_badge:
                self._last_bridge_badge = current
                self._on_badge(event.badge_count, event.badge_visible)
            return
        self._apply_status_event(event)

    def _apply_status_event(self, event: BridgeEvent) -> None:
        if event.online is None:
            return
        current = (event.online, event.auth)
        if current == self._last_bridge_status:
            return
        self._last_bridge_status = current
        if not event.online:
            self._set_phase(BrowserPhase.OFFLINE)
        elif event.auth == "login_required":
            self._set_phase(BrowserPhase.LOGIN_REQUIRED)
        elif event.auth == "ready":
            self._set_phase(BrowserPhase.READY)
        else:
            self._set_phase(BrowserPhase.SYNCING)

    def _on_load_failed(
        self,
        _view: Any,
        _event: Any,
        _uri: str,
        error: Exception,
    ) -> bool:
        message = _error_message(error)
        self._set_phase(BrowserPhase.ERROR, message)
        return False

    def _on_web_process_terminated(self, _view: Any, reason: Any) -> None:
        message = _("browser process stopped")
        log.warning("WhatsApp Web process terminated: %s", reason)
        self._set_phase(BrowserPhase.ERROR, message)
        if self._started and not self._recovery_source_id:
            self._recovery_source_id = GLib.timeout_add(
                RECOVERY_DELAY_MS,
                self._recover_web_process,
            )

    def _recover_web_process(self) -> bool:
        self._recovery_source_id = 0
        if self._started and self._view is not None:
            self._set_phase(BrowserPhase.STARTING)
            self._view.load_uri(WHATSAPP_WEB_URL)
        return False

    def _on_decide_policy(
        self,
        _view: Any,
        decision: Any,
        decision_type: Any,
    ) -> bool:
        if decision_type == self._webkit.PolicyDecisionType.RESPONSE:
            return False
        request = getattr(decision, "get_request", lambda: None)()
        uri = str(request.get_uri() or "") if request is not None else ""
        target = navigation_target(uri)
        if target is NavigationTarget.INTERNAL:
            if decision_type == self._webkit.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()
                return True
            decision.use()
            return True

        action = getattr(decision, "get_navigation_action", lambda: None)()
        user_gesture = bool(
            action is not None and getattr(action, "is_user_gesture", lambda: False)()
        )
        decision.ignore()
        if target is NavigationTarget.EXTERNAL and user_gesture:
            _open_external_uri(uri)
        return True

    def _on_create(self, _view: Any, navigation_action: Any) -> None:
        request = navigation_action.get_request()
        uri = str(request.get_uri() or "")
        if (
            navigation_target(uri) is NavigationTarget.EXTERNAL
            and navigation_action.is_user_gesture()
        ):
            _open_external_uri(uri)

    def _on_initialize_notification_permissions(self, context: Any) -> None:
        try:
            origin = self._webkit.SecurityOrigin.new_for_uri(WHATSAPP_WEB_URL)
            context.initialize_notification_permissions([origin], [])
        except Exception as exc:
            log.debug("Could not initialize WhatsApp notification permission: %s", exc)

    def _on_permission_request(self, _view: Any, request: Any) -> bool:
        request_name = type(request).__name__
        if request_name in {
            "NotificationPermissionRequest",
            "WebsiteDataAccessPermissionRequest",
        }:
            request.allow()
            return True
        if request_name == "UserMediaPermissionRequest":
            if self._confirm_media_permission(request):
                request.allow()
            else:
                request.deny()
            return True
        request.deny()
        return True

    def _confirm_media_permission(self, request: Any) -> bool:
        wants_audio = bool(
            self._webkit.user_media_permission_is_for_audio_device(request)
        )
        wants_video = bool(
            self._webkit.user_media_permission_is_for_video_device(request)
        )
        if wants_audio and wants_video:
            device = _("camera and microphone")
        elif wants_video:
            device = _("camera")
        else:
            device = _("microphone")
        dialog = Gtk.MessageDialog(
            transient_for=self._window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Allow WhatsApp to use your {device}?").format(device=device),
        )
        dialog.add_button(_("Deny"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Allow"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _on_show_notification(self, _view: Any, notification: Any) -> bool:
        return self._notifier.show(notification)

    def _activate_notification(self) -> None:
        self.show()
        self._on_attention_cleared()

    def _on_download_started(self, _context: Any, download: Any) -> None:
        download.connect("decide-destination", self._on_decide_download_destination)
        download.connect(
            "failed",
            lambda _download, error: log.warning(
                "WhatsApp download failed: %s",
                _error_message(error),
            ),
        )

    def _on_decide_download_destination(
        self,
        download: Any,
        suggested_filename: str,
    ) -> bool:
        destination = _unique_download_path(suggested_filename)
        download.set_destination(destination.as_uri())
        return True

    def _on_session_cleared(
        self,
        manager: Any,
        result: Gio.AsyncResult,
        on_complete: ClearCallback,
    ) -> None:
        try:
            manager.clear_finish(result)
        except Exception as exc:
            message = _error_message(exc)
            self._set_phase(BrowserPhase.ERROR, message)
            on_complete(False, message)
            return
        self._notifier.clear()
        self._on_attention_cleared()
        self._on_title("")
        self._set_phase(BrowserPhase.STARTING)
        if self._view is not None:
            self._view.load_uri(WHATSAPP_WEB_URL)
        on_complete(True, "")

    def _show_unavailable_dialog(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=_("WhatsApp requires WebKitGTK"),
        )
        dialog.format_secondary_text(
            _(
                "Install the WebKitGTK 4.1 or 4.0 introspection package, then "
                "restart Docking."
            )
        )
        dialog.run()
        dialog.destroy()


def _session_directories() -> tuple[Path, Path]:
    data_dir = ensure_dir(docking_data_dir() / "whatsapp")
    cache_dir = ensure_dir(docking_cache_dir() / "whatsapp")
    for directory in (data_dir, cache_dir):
        with suppress(OSError):
            directory.chmod(0o700)
    return data_dir, cache_dir


def _unique_download_path(suggested_filename: str) -> Path:
    raw_name = Path(suggested_filename or "download").name
    filename = raw_name if raw_name not in {"", ".", ".."} else "download"
    special_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    downloads = ensure_dir(
        Path(special_dir) if special_dir else Path.home() / "Downloads"
    )
    candidate = downloads / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem or "download"
    suffix = candidate.suffix
    for index in range(1, 10_000):
        alternative = downloads / f"{stem} ({index}){suffix}"
        if not alternative.exists():
            return alternative
    return downloads / f"{stem}-{GLib.get_monotonic_time()}{suffix}"


def _open_external_uri(uri: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as exc:
        log.warning("Could not open external WhatsApp link %s: %s", uri, exc)


def _error_message(error: object) -> str:
    message = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return message[:160] or _("unknown browser error")
