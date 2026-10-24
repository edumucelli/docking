"""Gmail preferences and presentation state, independent of GTK and Google SDKs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from docking.i18n import _

REFRESH_SECONDS = 60


@dataclass(frozen=True, slots=True)
class Account:
    subject: str
    email: str
    credential_id: str


@dataclass(frozen=True, slots=True)
class GmailPrefs:
    accounts: tuple[Account, ...] = ()
    selected: str = ""


@dataclass(frozen=True, slots=True)
class Snapshot:
    unread: int | None = None
    error: str = ""
    reconnect: bool = False
    paused: bool = False
    failures: int = 0
    next_refresh: float = 0


def _valid_text(value: Any, limit: int = 320) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def prefs_from_mapping(raw: Any) -> GmailPrefs:
    if not isinstance(raw, dict):
        return GmailPrefs()
    accounts: list[Account] = []
    seen: set[str] = set()
    entries = raw.get("accounts", [])
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        subject, email, key = (
            item.get("subject"),
            item.get("email"),
            item.get("credential_id"),
        )
        if (
            not _valid_text(subject, 255)
            or not _valid_text(email)
            or "@" not in email
            or not _valid_text(key, 128)
            or subject in seen
        ):
            continue
        accounts.append(Account(subject, email, key))
        seen.add(subject)
    selected = raw.get("selected", "")
    if not isinstance(selected, str) or selected not in seen:
        selected = accounts[0].subject if accounts else ""
    return GmailPrefs(tuple(accounts), selected)


def prefs_payload(prefs: GmailPrefs) -> dict[str, Any]:
    return {
        "accounts": [
            {"subject": a.subject, "email": a.email, "credential_id": a.credential_id}
            for a in prefs.accounts
        ],
        "selected": prefs.selected,
    }


def inbox_url(account: Account) -> str:
    return (
        "https://mail.google.com/mail/?"
        + urlencode({"authuser": account.email})
        + "#inbox"
    )


def account_label(account: Account, snapshot: Snapshot) -> str:
    count = str(snapshot.unread) if snapshot.unread is not None else _("unknown")
    text = _("{email}: {count} unread").format(email=account.email, count=count)
    if snapshot.error:
        text += " — " + snapshot.error
        if snapshot.unread is not None:
            text += " (" + _("last known count") + ")"
    return text


def tooltip(prefs: GmailPrefs, snapshots: dict[str, Snapshot], error: str = "") -> str:
    lines = [_("Gmail")]
    lines.extend(
        account_label(a, snapshots.get(a.subject, Snapshot())) for a in prefs.accounts
    )
    if not prefs.accounts and not error:
        lines.append(_("Sign in with Google"))
    if error:
        lines.append(error)
    if prefs.accounts and any(
        snapshots.get(a.subject, Snapshot()).unread is None
        or snapshots.get(a.subject, Snapshot()).error
        for a in prefs.accounts
    ):
        lines.append(_("Counts may be incomplete or out of date"))
    return "\n".join(lines)


def badge_count(prefs: GmailPrefs, snapshots: dict[str, Snapshot]) -> int:
    return sum(snapshots.get(a.subject, Snapshot()).unread or 0 for a in prefs.accounts)


def retry_delay(failures: int, server_delay: float = 0) -> float:
    return max(
        min(REFRESH_SECONDS * (1 << min(max(failures - 1, 0), 4)), 900), server_delay
    )
