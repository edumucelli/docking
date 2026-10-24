"""Cancellable browser OAuth with fixed Google endpoints and no callback logging."""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit

from docking.applets.gmail import oauth_client
from docking.i18n import _

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
LABEL_SCOPE = "https://www.googleapis.com/auth/gmail.labels"
SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email", LABEL_SCOPE)
HTTP_TIMEOUT = 10
AUTH_TIMEOUT = 300
CALLBACK_PATH = "/oauth2callback"


def protect_auth_logs() -> None:
    """OAuth SDK debug logs include tokens; Docking enables root DEBUG logging."""
    for name in ("oauthlib", "requests_oauthlib", "google.auth", "google.oauth2"):
        logging.getLogger(name).setLevel(logging.WARNING)


class GmailError(Exception):
    """A deliberately sanitized error safe for the UI and worker logs."""

    def __init__(
        self,
        message: str,
        *,
        reconnect: bool = False,
        paused: bool = False,
        retry_after: float = 0,
    ) -> None:
        super().__init__(message)
        self.reconnect = reconnect
        self.paused = paused
        self.retry_after = retry_after


class CancelledError(GmailError):
    def __init__(self) -> None:
        super().__init__(_("Sign-in or refresh cancelled"))


class Operation:
    """Cancellation shared by worker requests, keyring calls, and the listener."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback()

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            self._callbacks.append(callback)
            cancelled = self._event.is_set()
        if cancelled:
            callback()

        def remove() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return remove

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError()


@dataclass(frozen=True, slots=True)
class ClientConfig:
    client_id: str
    client_secret: str = field(repr=False)

    def google_config(self) -> dict[str, dict[str, object]]:
        return {
            "installed": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": ["http://127.0.0.1"],
            }
        }


def load_client_config() -> ClientConfig:
    path = os.environ.get("DOCKING_GMAIL_OAUTH_CLIENT_FILE")
    client_id, secret = oauth_client.CLIENT_ID, oauth_client.CLIENT_SECRET
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            installed = data["installed"]
            client_id, secret = installed["client_id"], installed["client_secret"]
        except (OSError, ValueError, KeyError, TypeError):
            raise GmailError(
                _("Invalid Gmail desktop OAuth configuration"), paused=True
            ) from None
    if (
        not isinstance(client_id, str)
        or not client_id.endswith(".apps.googleusercontent.com")
        or not isinstance(secret, str)
        or not secret
    ):
        raise GmailError(
            _("Gmail sign-in is not configured in this Docking build"), paused=True
        )
    return ClientConfig(client_id, secret)


def authorize(
    config: ClientConfig,
    operation: Operation,
    open_url: Callable[[str], bool],
) -> Credentials:
    """Run OAuth off the GTK thread; never accept redirects supplied by a file."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise GmailError(
            _("Install the Google authentication dependencies to use Gmail"),
            paused=True,
        ) from None

    operation.check()
    protect_auth_logs()
    code = ""
    denied = False
    state = ""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # The default handler logs the authorization code in the URL.
            return

        def do_GET(self) -> None:
            nonlocal code, denied
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            provided = query.get("state", [])
            valid = (
                parsed.path == CALLBACK_PATH
                and len(provided) == 1
                and hmac.compare_digest(provided[0].encode(), state.encode())
                and not code
                and not denied
            )
            if valid:
                values = query.get("code", [])
                code = values[0] if len(values) == 1 else ""
                denied = bool(query.get("error")) or not code
            message = _("You can close this window and return to Docking.").encode()
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    class Server(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(1)
            return connection, address

    try:
        with Server(("127.0.0.1", 0), Handler) as server:
            server.timeout = 0.2
            redirect = f"http://127.0.0.1:{server.server_port}{CALLBACK_PATH}"
            flow = Flow.from_client_config(
                config.google_config(),
                scopes=SCOPES,
                redirect_uri=redirect,
                autogenerate_code_verifier=True,
            )
            url, state = flow.authorization_url(
                access_type="offline", prompt="consent select_account"
            )
            operation.check()
            if not open_url(url):
                raise GmailError(_("Could not open the browser for Google sign-in"))
            deadline = time.monotonic() + AUTH_TIMEOUT
            while not code and not denied:
                operation.check()
                if time.monotonic() >= deadline:
                    raise GmailError(_("Google sign-in timed out; try again"))
                server.handle_request()
            operation.check()
            if denied:
                raise GmailError(_("Google sign-in was cancelled or denied"))
        flow.fetch_token(code=code, timeout=HTTP_TIMEOUT)
        operation.check()
        granted = flow.oauth2session.token.get("scope", ())
        if isinstance(granted, str):
            granted = granted.split()
        if LABEL_SCOPE not in granted:
            raise GmailError(
                _("Gmail label permission was not granted"), reconnect=True
            )
        credentials = cast("Credentials", flow.credentials)
        if not credentials.refresh_token:
            raise GmailError(
                _("Google did not provide offline access; sign in again"),
                reconnect=True,
            )
        return credentials
    except GmailError:
        raise
    except Exception:
        # OAuth exceptions can contain codes, tokens, and raw provider replies.
        raise GmailError(_("Could not complete Google sign-in; try again")) from None
