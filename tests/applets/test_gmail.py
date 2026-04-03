"""Tests for the Gmail applet."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.gmail.api as gmail_api_mod
import docking.applets.gmail.applet as gmail_applet_mod
import docking.applets.gmail.auth as gmail_auth_mod
import docking.applets.gmail.storage as gmail_storage_mod
from docking.applets.gmail.applet import GmailApplet
from docking.applets.gmail.auth import (
    GOOGLE_AUTH_PACKAGES,
    GmailClientConfigError,
    GmailDependencyError,
    GmailInvalidGrantError,
    missing_dependency_message,
)
from docking.applets.gmail.state import (
    GmailAppletState,
    GmailMessageSummary,
    GmailPollResult,
    GmailPrefs,
    GmailStatus,
    build_tooltip,
    parse_message_summary,
    prefs_from_mapping,
    prefs_payload,
    unread_badge_text,
    unread_count_from_label,
)
from docking.core.config import Config

_CLIENT_CONFIG = {
    "installed": {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://127.0.0.1"],
    }
}


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, fn, on_result=None, on_error=None, **_kwargs) -> bool:
        self.run(fn=fn, on_result=on_result, on_error=on_error)
        return True


def _make_applet(
    monkeypatch,
    *,
    prefs: dict[str, object] | None = None,
    client_configured: bool = False,
    storage_available: bool = True,
) -> GmailApplet:
    monkeypatch.setattr(gmail_applet_mod, "BackgroundWorker", _ImmediateWorker)
    monkeypatch.setattr(
        gmail_applet_mod.storage,
        "secret_storage_available",
        lambda: storage_available,
    )
    monkeypatch.setattr(
        gmail_applet_mod.storage,
        "load_client_config",
        lambda **_kwargs: _CLIENT_CONFIG if client_configured else None,
    )
    monkeypatch.setattr(
        gmail_applet_mod.storage,
        "load_credentials",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        gmail_applet_mod.storage, "save_credentials", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        gmail_applet_mod.storage, "save_client_config", lambda **_kwargs: None
    )
    monkeypatch.setattr(gmail_applet_mod.storage, "clear_all", lambda **_kwargs: None)
    monkeypatch.setattr(
        gmail_applet_mod.storage,
        "delete_credentials",
        lambda **_kwargs: None,
    )
    config = Config(applet_prefs={"gmail": prefs or {}})
    return GmailApplet(48, config=config)


class TestStateHelpers:
    def test_prefs_round_trip(self):
        payload = prefs_payload(
            account_email="me@example.com",
            connected=True,
            poll_interval_s=300,
            max_preview_rows=6,
            open_on_click_when_empty=False,
            show_popup_on_click=False,
        )
        prefs = prefs_from_mapping(payload)
        assert prefs == GmailPrefs(
            account_email="me@example.com",
            connected=True,
            poll_interval_s=300,
            max_preview_rows=6,
            open_on_click_when_empty=False,
            show_popup_on_click=False,
        )

    def test_badge_caps_at_99_plus(self):
        assert unread_badge_text(7) == "7"
        assert unread_badge_text(100) == "99+"

    def test_unread_count_from_label(self):
        assert unread_count_from_label({"messagesUnread": 5}) == 5
        assert unread_count_from_label({"messagesUnread": "9"}) == 9
        assert unread_count_from_label({}) == 0

    def test_parse_message_summary(self):
        summary = parse_message_summary(
            {
                "id": "abc",
                "payload": {
                    "headers": [
                        {
                            "name": "From",
                            "value": "Example Sender <sender@example.com>",
                        },
                        {"name": "Subject", "value": "Invoice ready"},
                        {"name": "Date", "value": "Tue, 31 Mar 2026 09:45:00 +0000"},
                    ]
                },
            }
        )
        assert summary.id == "abc"
        assert summary.from_text == "Example Sender"
        assert summary.subject == "Invoice ready"
        assert summary.date_text

    def test_build_tooltip_for_connected_unread_state(self):
        text = build_tooltip(
            state=GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
                unread_count=2,
                messages=(
                    GmailMessageSummary(
                        id="1",
                        from_text="Alice",
                        subject="Hello",
                        date_text="09:45",
                    ),
                ),
            ),
            max_rows=10,
        )
        assert "me@example.com" in text
        assert "2 unread inbox messages" in text
        assert "Alice: Hello" in text


class TestAuthHelpers:
    def test_parse_client_config_json_validates_installed_client(self):
        parsed = gmail_auth_mod.parse_client_config_json(json.dumps(_CLIENT_CONFIG))
        assert parsed["installed"]["client_id"] == "client-id"

    def test_parse_client_config_json_rejects_invalid_json(self):
        try:
            gmail_auth_mod.parse_client_config_json("{")
        except GmailClientConfigError:
            return
        raise AssertionError("expected GmailClientConfigError")

    def test_refresh_credentials_invalid_grant(self, monkeypatch):
        class _Creds:
            @classmethod
            def from_authorized_user_info(cls, _raw):
                return cls()

            def refresh(self, _request):
                raise RuntimeError("invalid_grant")

        google_credentials = SimpleNamespace(Credentials=_Creds)
        google_requests = SimpleNamespace(Request=lambda: object())
        monkeypatch.setattr(
            gmail_auth_mod,
            "_google_modules",
            lambda: (google_credentials, object(), google_requests),
        )
        try:
            gmail_auth_mod.refresh_credentials({"token": "x"})
        except GmailInvalidGrantError:
            return
        raise AssertionError("expected GmailInvalidGrantError")

    def test_missing_dependency_message_names_google_packages(self):
        text = missing_dependency_message(*GOOGLE_AUTH_PACKAGES)
        assert "Missing Gmail dependencies" in text
        assert "google-auth" in text
        assert "google-auth-oauthlib" in text

    def test_run_oauth_flow_uses_callback_and_times_out_without_redirect(
        self, monkeypatch
    ):
        class _Flow:
            credentials = SimpleNamespace(to_json=lambda: '{"token": "abc"}')

            @classmethod
            def from_client_config(cls, _client_config, scopes):
                _ = scopes
                return cls()

            def authorization_url(self, **_kwargs):
                return ("https://accounts.google.com/o/oauth2/auth", "state")

            def fetch_token(self, authorization_response):
                _ = authorization_response

        class _Server:
            def __init__(self):
                self.server_port = 48875
                self.timeout = None

            def handle_request(self):
                return None

            def server_close(self):
                return None

        fake_flow_mod = SimpleNamespace(
            InstalledAppFlow=_Flow,
            _RedirectWSGIApp=lambda _message: SimpleNamespace(),
            _WSGIRequestHandler=object(),
            wsgiref=SimpleNamespace(
                simple_server=SimpleNamespace(
                    make_server=lambda *_args, **_kwargs: _Server()
                )
            ),
        )
        monkeypatch.setattr(
            gmail_auth_mod,
            "_google_modules",
            lambda: (object(), fake_flow_mod, object()),
        )
        opened: list[str] = []

        try:
            gmail_auth_mod.run_oauth_flow(
                client_config=_CLIENT_CONFIG,
                open_authorization_url=opened.append,
            )
        except gmail_auth_mod.GmailAuthError as exc:
            assert "Timed out waiting for Gmail sign-in to complete" in str(exc)
        else:
            raise AssertionError("expected GmailAuthError")

        assert opened == ["https://accounts.google.com/o/oauth2/auth"]


class TestStorageHelpers:
    def test_save_load_and_clear_secret(self, monkeypatch):
        stored: dict[tuple[str, str], str] = {}

        class _Schema:
            @staticmethod
            def new(name, flags, attrs):
                return (name, flags, attrs)

        class _Secret:
            COLLECTION_DEFAULT = "default"
            Schema = _Schema
            SchemaFlags = SimpleNamespace(NONE=0)
            SchemaAttributeType = SimpleNamespace(STRING="string")

            @staticmethod
            def password_store_sync(
                schema, attrs, collection, label, blob, cancellable
            ):
                _ = schema, collection, label, cancellable
                stored[(attrs["applet_id"], attrs["kind"])] = blob
                return True

            @staticmethod
            def password_lookup_sync(schema, attrs, cancellable):
                _ = schema, cancellable
                return stored.get((attrs["applet_id"], attrs["kind"]))

            @staticmethod
            def password_clear_sync(schema, attrs, cancellable):
                _ = schema, cancellable
                stored.pop((attrs["applet_id"], attrs["kind"]), None)
                return True

        monkeypatch.setattr(gmail_storage_mod, "_secret_module", lambda: _Secret)
        gmail_storage_mod.save_client_config(
            applet_id="applet://gmail",
            client_config=_CLIENT_CONFIG,
        )
        assert (
            gmail_storage_mod.load_client_config(applet_id="applet://gmail")
            == _CLIENT_CONFIG
        )
        gmail_storage_mod.delete_client_config(applet_id="applet://gmail")
        assert gmail_storage_mod.load_client_config(applet_id="applet://gmail") is None


class TestApiHelpers:
    def test_fetch_inbox_state(self, monkeypatch):
        monkeypatch.setattr(
            gmail_api_mod,
            "refresh_credentials",
            lambda raw: {"token": "token-1", "refresh_token": "refresh"},
        )

        def fake_get(url, headers, params, timeout):
            _ = headers, timeout
            if url.endswith("/profile"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "emailAddress": "me@example.com",
                        "historyId": "123",
                    },
                    text="",
                )
            if url.endswith("/labels/INBOX"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"messagesUnread": 4},
                    text="",
                )
            if url.endswith("/messages") and params:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"messages": [{"id": "m1"}]},
                    text="",
                )
            if url.endswith("/messages/m1"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "id": "m1",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "Alice <alice@example.com>"},
                                {"name": "Subject", "value": "Hello"},
                                {
                                    "name": "Date",
                                    "value": "Tue, 31 Mar 2026 09:45:00 +0000",
                                },
                            ]
                        },
                    },
                    text="",
                )
            raise AssertionError(f"Unexpected URL {url!r}")

        fake_requests = SimpleNamespace(get=fake_get, RequestException=RuntimeError)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        result, refreshed = gmail_api_mod.fetch_inbox_state(
            credentials_info={"refresh_token": "refresh"},
            max_results=10,
        )
        assert refreshed["token"] == "token-1"
        assert result.account_email == "me@example.com"
        assert result.unread_count == 4
        assert result.messages[0].from_text == "Alice"


class TestGmailApplet:
    def test_disconnected_click_starts_connect_flow(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        applet._start_connect_flow = MagicMock()
        applet.on_clicked()
        applet._start_connect_flow.assert_called_once_with()

    def test_connected_zero_unread_click_opens_inbox(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        applet._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
            )
        )
        applet._open_inbox = MagicMock()
        applet.on_clicked()
        applet._open_inbox.assert_called_once_with()

    def test_connected_unread_click_opens_popup(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        applet._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
                unread_count=3,
            )
        )
        applet._toggle_popup = MagicMock()
        applet.on_clicked()
        applet._toggle_popup.assert_called_once_with()

    def test_refresh_menu_action_calls_refresh(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        applet._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
            )
        )
        applet._refresh_now = MagicMock()
        items = applet.get_menu_items()
        refresh = next(item for item in items if item.get_label() == "Refresh Now")
        for callback, args in refresh._signals["activate"]:
            callback(refresh, *args)
        applet._refresh_now.assert_called_once_with()

    def test_disconnect_clears_state(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        monkeypatch.setattr(
            gmail_applet_mod.storage,
            "load_credentials",
            lambda **_kwargs: None,
        )
        cleared = MagicMock()
        monkeypatch.setattr(gmail_applet_mod.storage, "clear_all", cleared)
        applet._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
                unread_count=5,
            )
        )

        applet._disconnect()

        cleared.assert_called_once_with(applet_id=applet.desktop_id)
        assert applet._state.status == GmailStatus.UNCONFIGURED
        assert applet._prefs.connected is False
        assert applet._prefs.account_email == ""

    def test_invalid_grant_moves_to_reconnect_required(self, monkeypatch):
        applet = _make_applet(
            monkeypatch,
            prefs={"account_email": "me@example.com", "connected": True},
            client_configured=True,
        )
        applet._set_state(
            GmailAppletState(
                status=GmailStatus.CONNECTED,
                client_configured=True,
                account_email="me@example.com",
                unread_count=4,
            )
        )
        removed = MagicMock()
        monkeypatch.setattr(gmail_applet_mod.storage, "delete_credentials", removed)

        applet._on_poll_error(GmailInvalidGrantError("invalid_grant"))

        removed.assert_called_once_with(applet_id=applet.desktop_id)
        assert applet._state.status == GmailStatus.RECONNECT_REQUIRED
        assert applet._prefs.connected is False

    def test_import_client_json_file_saves_client_and_marks_disconnected(
        self,
        monkeypatch,
        tmp_path,
    ):
        applet = _make_applet(monkeypatch, client_configured=False)
        saved = MagicMock()
        monkeypatch.setattr(gmail_applet_mod.storage, "save_client_config", saved)
        path = tmp_path / "client.json"
        path.write_text(json.dumps(_CLIENT_CONFIG), encoding="utf-8")

        assert applet._import_client_json_file(path) is True
        saved.assert_called_once()
        assert applet._state.status == GmailStatus.DISCONNECTED
        assert applet._state.client_configured is True

    def test_connect_result_persists_credentials_and_updates_state(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        saved = MagicMock()
        monkeypatch.setattr(gmail_applet_mod.storage, "save_credentials", saved)
        applet._on_connect_result(
            (
                GmailPollResult(
                    account_email="me@example.com",
                    unread_count=2,
                    messages=(),
                    history_id="12",
                ),
                {"token": "abc"},
            )
        )
        saved.assert_called_once_with(
            applet_id=applet.desktop_id,
            credentials={"token": "abc"},
        )
        assert applet._state.status == GmailStatus.CONNECTED
        assert applet._prefs.connected is True

    def test_connect_error_shows_dependency_guidance(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)

        applet._on_connect_error(
            GmailDependencyError(missing_dependency_message(*GOOGLE_AUTH_PACKAGES))
        )

        assert applet._state.status == GmailStatus.ERROR
        assert "google-auth" in applet._state.info_text
        assert "google-auth-oauthlib" in applet._state.info_text

    def test_connect_worker_uses_applet_browser_opener(self, monkeypatch):
        applet = _make_applet(monkeypatch, client_configured=True)
        opener = MagicMock()
        monkeypatch.setattr(applet, "_open_authorization_url", opener)
        monkeypatch.setattr(
            gmail_applet_mod,
            "run_oauth_flow",
            lambda *, client_config, open_authorization_url: (
                open_authorization_url("https://accounts.google.com/o/oauth2/auth")
                or {"token": "abc", "refresh_token": "def"}
            ),
        )
        monkeypatch.setattr(
            gmail_applet_mod,
            "fetch_inbox_state",
            lambda **_kwargs: (
                GmailPollResult(
                    account_email="me@example.com",
                    unread_count=1,
                    messages=(),
                    history_id="h1",
                ),
                {"token": "abc", "refresh_token": "def"},
            ),
        )

        result, credentials = applet._connect_worker()

        opener.assert_called_once_with("https://accounts.google.com/o/oauth2/auth")
        assert result.account_email == "me@example.com"
        assert credentials["token"] == "abc"
