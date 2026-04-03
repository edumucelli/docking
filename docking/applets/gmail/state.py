"""Pure state helpers for the Gmail applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from enum import Enum
from typing import Any

from docking.i18n import _, ngettext

DEFAULT_POLL_INTERVAL_S = 60
DEFAULT_MAX_PREVIEW_ROWS = 10
POLL_INTERVAL_OPTIONS: tuple[int, ...] = (30, 60, 300, 900)
MAX_BADGE_COUNT = 99
GMAIL_INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"
GMAIL_COMPOSE_URL = "https://mail.google.com/mail/u/0/#inbox?compose=new"


class GmailStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STALE = "stale"
    ERROR = "error"
    RECONNECT_REQUIRED = "reconnect_required"


@dataclass(frozen=True, slots=True)
class GmailMessageSummary:
    id: str
    from_text: str
    subject: str
    date_text: str


@dataclass(frozen=True, slots=True)
class GmailPollResult:
    account_email: str
    unread_count: int
    messages: tuple[GmailMessageSummary, ...]
    history_id: str = ""


@dataclass(frozen=True, slots=True)
class GmailPrefs:
    account_email: str = ""
    connected: bool = False
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S
    max_preview_rows: int = DEFAULT_MAX_PREVIEW_ROWS
    open_on_click_when_empty: bool = True
    show_popup_on_click: bool = True


@dataclass(frozen=True, slots=True)
class GmailAppletState:
    status: GmailStatus = GmailStatus.UNCONFIGURED
    client_configured: bool = False
    account_email: str = ""
    unread_count: int = 0
    messages: tuple[GmailMessageSummary, ...] = ()
    history_id: str = ""
    info_text: str = ""


def prefs_from_mapping(raw: Mapping[str, Any] | None) -> GmailPrefs:
    if not isinstance(raw, Mapping):
        return GmailPrefs()

    poll_interval = _coerce_int(
        raw.get("poll_interval_s"), default=DEFAULT_POLL_INTERVAL_S
    )
    if poll_interval not in POLL_INTERVAL_OPTIONS:
        poll_interval = DEFAULT_POLL_INTERVAL_S

    max_preview_rows = max(
        1,
        min(
            DEFAULT_MAX_PREVIEW_ROWS,
            _coerce_int(
                raw.get("max_preview_rows"),
                default=DEFAULT_MAX_PREVIEW_ROWS,
            ),
        ),
    )

    return GmailPrefs(
        account_email=str(raw.get("account_email", "") or ""),
        connected=bool(raw.get("connected", False)),
        poll_interval_s=poll_interval,
        max_preview_rows=max_preview_rows,
        open_on_click_when_empty=bool(raw.get("open_on_click_when_empty", True)),
        show_popup_on_click=bool(raw.get("show_popup_on_click", True)),
    )


def prefs_payload(
    *,
    account_email: str,
    connected: bool,
    poll_interval_s: int,
    max_preview_rows: int,
    open_on_click_when_empty: bool,
    show_popup_on_click: bool,
) -> dict[str, Any]:
    return {
        "account_email": account_email,
        "connected": connected,
        "poll_interval_s": poll_interval_s,
        "max_preview_rows": max_preview_rows,
        "open_on_click_when_empty": open_on_click_when_empty,
        "show_popup_on_click": show_popup_on_click,
    }


def initial_applet_state(
    *,
    prefs: GmailPrefs,
    client_configured: bool,
) -> GmailAppletState:
    if prefs.connected:
        return GmailAppletState(
            status=GmailStatus.STALE,
            client_configured=client_configured,
            account_email=prefs.account_email,
        )
    if client_configured:
        return GmailAppletState(
            status=GmailStatus.DISCONNECTED,
            client_configured=True,
            account_email=prefs.account_email,
        )
    return GmailAppletState(status=GmailStatus.UNCONFIGURED, client_configured=False)


def unread_badge_text(unread_count: int) -> str:
    count = max(0, unread_count)
    if count > MAX_BADGE_COUNT:
        return f"{MAX_BADGE_COUNT}+"
    return str(count)


def build_tooltip(*, state: GmailAppletState, max_rows: int) -> str:
    if state.status == GmailStatus.UNCONFIGURED:
        return _("Gmail is not connected")
    if state.status == GmailStatus.CONNECTING:
        return _("Gmail is connecting...")
    if state.status == GmailStatus.DISCONNECTED:
        return _("Gmail is ready to connect")
    if state.status == GmailStatus.RECONNECT_REQUIRED:
        if state.info_text:
            return _("Gmail needs to reconnect: {reason}").format(
                reason=state.info_text
            )
        return _("Gmail needs to reconnect")
    if state.status == GmailStatus.ERROR:
        if state.info_text:
            return _("Gmail error: {reason}").format(reason=state.info_text)
        return _("Gmail could not refresh")

    lines: list[str] = [state.account_email or _("Gmail")]
    if state.status == GmailStatus.STALE:
        lines.append(_("Last refresh failed; showing previous inbox state"))

    if state.unread_count <= 0:
        lines.append(_("No unread inbox messages"))
        return "\n".join(lines)

    lines.append(
        ngettext(
            "{n} unread inbox message",
            "{n} unread inbox messages",
            state.unread_count,
        ).format(n=state.unread_count)
    )
    for message in state.messages[: max(1, max_rows)]:
        lines.append(
            _("{from_text}: {subject} ({date})").format(
                from_text=message.from_text,
                subject=message.subject,
                date=message.date_text,
            )
        )
    return "\n".join(lines)


def unread_count_from_label(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    value = payload.get("messagesUnread")
    return max(0, _coerce_int(value, default=0))


def message_ids_from_list(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        return ()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ()
    result: list[str] = []
    for entry in messages:
        if not isinstance(entry, Mapping):
            continue
        message_id = entry.get("id")
        if isinstance(message_id, str) and message_id:
            result.append(message_id)
    return tuple(result)


def parse_message_summary(payload: Mapping[str, Any] | None) -> GmailMessageSummary:
    if not isinstance(payload, Mapping):
        return GmailMessageSummary(
            id="",
            from_text=_("Unknown sender"),
            subject=_("(no subject)"),
            date_text="",
        )

    message_id = str(payload.get("id", "") or "")
    from_header = _header_value(payload, "From")
    subject_header = _header_value(payload, "Subject")
    date_header = _header_value(payload, "Date")

    return GmailMessageSummary(
        id=message_id,
        from_text=_display_sender(from_header),
        subject=subject_header or _("(no subject)"),
        date_text=_format_header_date(date_header),
    )


def _header_value(payload: Mapping[str, Any], header_name: str) -> str:
    body = payload.get("payload")
    if not isinstance(body, Mapping):
        return ""
    headers = body.get("headers")
    if not isinstance(headers, list):
        return ""
    wanted = header_name.lower()
    for header in headers:
        if not isinstance(header, Mapping):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and name.lower() == wanted and isinstance(value, str):
            return value.strip()
    return ""


def _display_sender(from_header: str) -> str:
    display_name, address = parseaddr(from_header)
    if display_name:
        return display_name.strip('"') or address or _("Unknown sender")
    if address:
        return address
    return _("Unknown sender")


def _format_header_date(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return value
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    today = datetime.now(dt.tzinfo).date()
    if dt.date() == today:
        return dt.strftime("%H:%M")
    if dt.year == today.year:
        return dt.strftime("%b %d").replace(" 0", " ")
    return dt.strftime("%Y-%m-%d")


def _coerce_int(value: object, *, default: int) -> int:
    if not isinstance(value, (bool, int, float, str, bytes, bytearray)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
