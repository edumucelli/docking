"""Gmail REST API helpers for the Gmail applet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from docking.applets.gmail.auth import (
    REQUESTS_PACKAGE,
    GmailDependencyError,
    GmailInvalidGrantError,
    GmailRefreshError,
    missing_dependency_message,
    refresh_credentials,
)
from docking.applets.gmail.state import (
    GmailPollResult,
    message_ids_from_list,
    parse_message_summary,
    unread_count_from_label,
)
from docking.log import get_logger

log = get_logger("gmail.api")

API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_TIMEOUT_S = 15
MESSAGE_METADATA_HEADERS = ("From", "Subject", "Date")


class GmailApiError(RuntimeError):
    """Raised when Gmail REST requests fail."""


def fetch_inbox_state(
    *,
    credentials_info: Mapping[str, Any],
    max_results: int,
) -> tuple[GmailPollResult, dict[str, Any]]:
    refreshed = _ensure_fresh_credentials(credentials_info)
    profile = _get_json(
        path="/profile",
        credentials_info=refreshed,
    )
    label = _get_json(
        path="/labels/INBOX",
        credentials_info=refreshed,
    )
    message_list = _get_json(
        path="/messages",
        credentials_info=refreshed,
        params=[
            ("labelIds", "INBOX"),
            ("labelIds", "UNREAD"),
            ("maxResults", str(max_results)),
        ],
    )

    messages = []
    for message_id in message_ids_from_list(message_list):
        message = _get_json(
            path=f"/messages/{message_id}",
            credentials_info=refreshed,
            params=[("format", "metadata")]
            + [("metadataHeaders", name) for name in MESSAGE_METADATA_HEADERS],
        )
        messages.append(parse_message_summary(message))

    return (
        GmailPollResult(
            account_email=str(profile.get("emailAddress", "") or ""),
            unread_count=unread_count_from_label(label),
            messages=tuple(messages),
            history_id=str(profile.get("historyId", "") or ""),
        ),
        refreshed,
    )


def _ensure_fresh_credentials(credentials_info: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return refresh_credentials(credentials_info)
    except GmailInvalidGrantError:
        raise
    except GmailRefreshError:
        raise
    except GmailDependencyError:
        raise


def _get_json(
    *,
    path: str,
    credentials_info: Mapping[str, Any],
    params: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    try:
        import requests
    except Exception as exc:
        log.warning("Gmail dependency import failed while calling API: %s", exc)
        raise GmailDependencyError(
            missing_dependency_message(REQUESTS_PACKAGE)
        ) from exc

    token = str(credentials_info.get("token", "") or "")
    if not token:
        raise GmailApiError("Missing Gmail access token")

    try:
        response = requests.get(
            f"{API_BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=list(params),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise GmailApiError(str(exc)) from exc

    if response.status_code >= 400:
        raise GmailApiError(
            f"Gmail API {path} failed with {response.status_code}: "
            f"{response.text[:200]}"
        )

    payload = response.json()
    if not isinstance(payload, dict):
        raise GmailApiError(f"Gmail API {path} returned a non-object response")
    return payload
