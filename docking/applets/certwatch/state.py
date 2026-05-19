# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Pure state and formatting logic for certwatch applet."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, NamedTuple

from docking.applets.live_state import (
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
)
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _

DEFAULT_HTTPS_PORT = 443

# Threshold ranges: days remaining -> status.
CRITICAL_THRESHOLD_DAYS = 7
WARN_THRESHOLD_DAYS = 30


class DomainPref(NamedTuple):
    """A persisted domain entry to watch."""

    host: str
    port: int = DEFAULT_HTTPS_PORT


class CertInfo(NamedTuple):
    """Resolved certificate data for a watched domain."""

    host: str
    port: int
    not_after: datetime | None
    subject: str
    issuer: str
    error: str | None


class CertStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    EXPIRED = "expired"
    ERROR = "error"
    UNKNOWN = "unknown"


_STATUS_SEVERITY = {
    CertStatus.OK: 0,
    CertStatus.UNKNOWN: 1,
    CertStatus.WARN: 2,
    CertStatus.ERROR: 3,
    CertStatus.CRITICAL: 4,
    CertStatus.EXPIRED: 5,
}


@dataclass(frozen=True, slots=True)
class CertwatchPrefs:
    """Persisted certwatch applet preferences."""

    domains: tuple[DomainPref, ...] = ()


def parse_host_port(text: str) -> DomainPref | None:
    """Parse ``host[:port]`` into a DomainPref. Strips scheme and path."""
    raw = text.strip()
    if not raw:
        return None
    for prefix in ("https://", "http://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
            break
    raw = raw.split("/", 1)[0]
    if ":" in raw:
        host, _, port_text = raw.rpartition(":")
        host = host.strip()
        try:
            port = int(port_text)
        except ValueError:
            return None
        if not host or port <= 0 or port > 65535:
            return None
        return DomainPref(host=host, port=port)
    host = raw.strip()
    if not host:
        return None
    return DomainPref(host=host, port=DEFAULT_HTTPS_PORT)


def format_host(pref: DomainPref) -> str:
    """Render a DomainPref for display (hides default port)."""
    if pref.port == DEFAULT_HTTPS_PORT:
        return pref.host
    return f"{pref.host}:{pref.port}"


def days_until(
    not_after: datetime | None, *, now: datetime | None = None
) -> int | None:
    """Whole-day count until ``not_after``. Negative when already expired."""
    if not_after is None:
        return None
    current = now or datetime.now(timezone.utc)
    # Certificate times are UTC; coerce naive datetimes to UTC for consistency.
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = not_after - current
    return delta.days


def status_for(days_left: int | None, error: str | None) -> CertStatus:
    """Classify a single cert into a status."""
    if error:
        return CertStatus.ERROR
    if days_left is None:
        return CertStatus.UNKNOWN
    if days_left < 0:
        return CertStatus.EXPIRED
    if days_left < CRITICAL_THRESHOLD_DAYS:
        return CertStatus.CRITICAL
    if days_left < WARN_THRESHOLD_DAYS:
        return CertStatus.WARN
    return CertStatus.OK


def status_for_cert(cert: CertInfo, *, now: datetime | None = None) -> CertStatus:
    return status_for(
        days_left=days_until(cert.not_after, now=now),
        error=cert.error,
    )


def worst_status(
    certs: Iterable[CertInfo],
    *,
    now: datetime | None = None,
) -> CertStatus:
    """Pick the most severe status among the watched certs."""
    worst = CertStatus.UNKNOWN
    worst_rank = -1
    for cert in certs:
        status = status_for_cert(cert=cert, now=now)
        rank = _STATUS_SEVERITY[status]
        if rank > worst_rank:
            worst = status
            worst_rank = rank
    return worst


def min_days(
    certs: Iterable[CertInfo],
    *,
    now: datetime | None = None,
) -> int | None:
    """Return the smallest days-remaining across all certs, or None."""
    values = [
        days_until(cert.not_after, now=now)
        for cert in certs
        if cert.not_after is not None and cert.error is None
    ]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return min(values)


def icon_label(
    certs: Iterable[CertInfo],
    *,
    now: datetime | None = None,
) -> str:
    """Short label to overlay on the icon: days, '!', 'X', or ''."""
    certs_list = list(certs)
    if not certs_list:
        return ""
    status = worst_status(certs_list, now=now)
    if status is CertStatus.EXPIRED:
        return "X"
    if status is CertStatus.ERROR:
        return "!"
    days = min_days(certs_list, now=now)
    if days is None:
        return "?"
    if days > 999:
        return "999"
    return str(days)


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> CertwatchPrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return CertwatchPrefs()
    raw = prefs.get("domains", ())
    if not isinstance(raw, list | tuple):
        return CertwatchPrefs()
    domains: list[DomainPref] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        host = str(entry.get("host", "")).strip()
        if not host:
            continue
        try:
            port = int(entry.get("port", DEFAULT_HTTPS_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_HTTPS_PORT
        if port <= 0 or port > 65535:
            port = DEFAULT_HTTPS_PORT
        domains.append(DomainPref(host=host, port=port))
    return CertwatchPrefs(domains=tuple(domains))


def prefs_payload(*, domains: Iterable[DomainPref]) -> dict[str, Any]:
    """Build payload used by save_prefs()."""
    return {
        "domains": [{"host": d.host, "port": d.port} for d in domains],
    }


def status_label(status: CertStatus) -> str:
    """Human-readable short label per status."""
    return {
        CertStatus.OK: _("OK"),
        CertStatus.WARN: _("Warn"),
        CertStatus.CRITICAL: _("Critical"),
        CertStatus.EXPIRED: _("Expired"),
        CertStatus.ERROR: _("Error"),
        CertStatus.UNKNOWN: _("..."),
    }[status]


def tooltip_line(cert: CertInfo, *, now: datetime | None = None) -> str:
    """Single-line summary for one cert: ``host: <status>, Nd``."""
    host_label = format_host(DomainPref(host=cert.host, port=cert.port))
    status = status_for_cert(cert=cert, now=now)
    if status is CertStatus.ERROR:
        return _("{host}: error ({reason})").format(
            host=host_label, reason=cert.error or "unknown"
        )
    if status is CertStatus.UNKNOWN:
        return _("{host}: loading...").format(host=host_label)
    days = days_until(cert.not_after, now=now)
    if days is None:
        return _("{host}: unknown").format(host=host_label)
    if days < 0:
        return _("{host}: expired {days}d ago").format(host=host_label, days=-days)
    return _("{host}: {label}, {days}d left").format(
        host=host_label,
        label=status_label(status),
        days=days,
    )


def build_tooltip(
    *,
    domains: Iterable[DomainPref],
    certs: Iterable[CertInfo],
    loading: bool = False,
    error: str | None = None,
    now: datetime | None = None,
    updated_at: datetime | str | None = None,
    cadence_seconds: int | None = None,
) -> str:
    """Full tooltip text: header + one line per domain."""
    domain_list = list(domains)
    if not domain_list:
        return structured_tooltip(
            title=_("Cert Watch"),
            primary=_("No domains configured"),
        )

    cert_map = {(c.host, c.port): c for c in certs}
    has_data = bool(cert_map)
    status = resolve_live_status(
        has_data=has_data,
        loading=loading,
        error=error,
        updated_at=updated_at,
        stale_after_seconds=cadence_seconds * 2 if cadence_seconds else None,
        now=now,
    )
    details = []
    for pref in domain_list:
        cert = cert_map.get((pref.host, pref.port))
        if cert is None:
            if error and not loading:
                details.append(_("{host}: unavailable").format(host=format_host(pref)))
            else:
                details.append(_("{host}: loading...").format(host=format_host(pref)))
        else:
            details.append(tooltip_line(cert=cert, now=now))
    state_label = live_state_label(status)
    if state_label and has_data:
        details.append(state_label)
    return structured_tooltip(
        title=_("Cert Watch"),
        details=details,
        freshness=live_freshness_lines(
            status=status,
            updated_at=updated_at,
            cadence_seconds=cadence_seconds,
            cadence_verb=_("Checks"),
        ),
        error=live_state_error(status=status, error=error),
        recovery=refresh_recovery_label(status),
    )
