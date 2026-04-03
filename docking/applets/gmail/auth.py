"""OAuth helpers for the Gmail applet."""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from docking.i18n import _
from docking.log import get_logger

log = get_logger("gmail.auth")

GMAIL_METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GOOGLE_AUTH_PACKAGES = ("google-auth", "google-auth-oauthlib")
REQUESTS_PACKAGE = "requests"
OAUTH_CALLBACK_TIMEOUT_S = 180


class GmailAuthError(RuntimeError):
    """Base Gmail OAuth error."""


class GmailDependencyError(GmailAuthError):
    """Raised when optional Google auth dependencies are unavailable."""


class GmailClientConfigError(GmailAuthError):
    """Raised for invalid OAuth client JSON."""


class GmailRefreshError(GmailAuthError):
    """Raised when token refresh fails."""


class GmailInvalidGrantError(GmailRefreshError):
    """Raised when Google rejected the refresh token and reconnect is required."""


def missing_dependency_message(*packages: str) -> str:
    package_list = ", ".join(packages)
    return _("Missing Gmail dependencies: install {packages}").format(
        packages=package_list
    )


def parse_client_config_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GmailClientConfigError(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise GmailClientConfigError("Client config must be a JSON object")
    return validate_client_config(payload)


def validate_client_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    installed = raw.get("installed")
    if not isinstance(installed, Mapping):
        raise GmailClientConfigError(
            "OAuth client JSON must contain an 'installed' object"
        )
    required = (
        "client_id",
        "client_secret",
        "auth_uri",
        "token_uri",
        "redirect_uris",
    )
    for key in required:
        value = installed.get(key)
        if key == "redirect_uris":
            if not isinstance(value, list) or not all(
                isinstance(entry, str) for entry in value
            ):
                raise GmailClientConfigError(
                    "OAuth client JSON has invalid redirect_uris"
                )
            continue
        if not isinstance(value, str) or not value:
            raise GmailClientConfigError(
                f"OAuth client JSON is missing a valid {key!r}"
            )
    return {
        "installed": {
            "client_id": str(installed["client_id"]),
            "client_secret": str(installed["client_secret"]),
            "auth_uri": str(installed["auth_uri"]),
            "token_uri": str(installed["token_uri"]),
            "redirect_uris": [str(entry) for entry in installed["redirect_uris"]],
        }
    }


def run_oauth_flow(
    *,
    client_config: Mapping[str, Any],
    scopes: Sequence[str] = (GMAIL_METADATA_SCOPE,),
    open_authorization_url: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    _credentials_mod, flow_mod, _request_mod = _google_modules()
    flow = flow_mod.InstalledAppFlow.from_client_config(
        validate_client_config(client_config),
        scopes=list(scopes),
    )
    wsgi_app = flow_mod._RedirectWSGIApp(
        "Docking Gmail is connected. You can close this tab."
    )
    local_server = flow_mod.wsgiref.simple_server.make_server(
        "127.0.0.1",
        0,
        wsgi_app,
        handler_class=flow_mod._WSGIRequestHandler,
    )
    try:
        flow.redirect_uri = f"http://127.0.0.1:{local_server.server_port}/"
        auth_url, _state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        if open_authorization_url is not None:
            open_authorization_url(auth_url)
        else:
            webbrowser.open(auth_url, new=1, autoraise=True)

        local_server.timeout = OAUTH_CALLBACK_TIMEOUT_S
        local_server.handle_request()
        try:
            authorization_response = wsgi_app.last_request_uri.replace(
                "http",
                "https",
                1,
            )
        except AttributeError as exc:
            raise GmailAuthError(
                _("Timed out waiting for Gmail sign-in to complete")
            ) from exc
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
    finally:
        local_server.server_close()
    return credentials_to_dict(credentials)


def refresh_credentials(credentials_info: Mapping[str, Any]) -> dict[str, Any]:
    credentials_mod, _flow_mod, request_mod = _google_modules()
    try:
        credentials = credentials_mod.Credentials.from_authorized_user_info(
            dict(credentials_info)
        )
        credentials.refresh(request_mod.Request())
    except Exception as exc:
        text = str(exc).lower()
        if "invalid_grant" in text:
            raise GmailInvalidGrantError(str(exc)) from exc
        raise GmailRefreshError(str(exc)) from exc
    return credentials_to_dict(credentials)


def credentials_to_dict(credentials: Any) -> dict[str, Any]:
    raw = json.loads(credentials.to_json())
    if not isinstance(raw, dict):
        raise GmailAuthError("Serialized credentials are not a JSON object")
    return raw


def revoke_credentials(credentials_info: Mapping[str, Any]) -> None:
    try:
        import requests
    except Exception as exc:
        log.warning("Gmail dependency import failed while revoking token: %s", exc)
        raise GmailDependencyError(
            missing_dependency_message(REQUESTS_PACKAGE)
        ) from exc

    token = str(
        credentials_info.get("refresh_token") or credentials_info.get("token") or ""
    )
    if not token:
        return
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.warning("Failed to revoke Gmail token: %s", exc)
        return
    if response.status_code not in {200, 400}:
        log.warning(
            "Unexpected Gmail token revoke status %s: %s",
            response.status_code,
            response.text[:200],
        )


def _google_modules():
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import credentials as google_credentials
        from google_auth_oauthlib import flow as oauth_flow
    except Exception as exc:
        log.warning("Gmail dependency import failed: %s", exc)
        raise GmailDependencyError(
            missing_dependency_message(*GOOGLE_AUTH_PACKAGES)
        ) from exc
    return google_credentials, oauth_flow, google_requests
