"""TLS cert fetch for certwatch applet.

Uses only stdlib ``ssl`` and ``socket`` so it adds no new deps. One fetch is a
single TLS handshake with SNI; we read the peer certificate dates and identity
strings and close the connection.
"""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

from docking.applets.certwatch import meta
from docking.applets.certwatch.state import CertInfo
from docking.log import get_logger, with_context

log = with_context(get_logger(name="certwatch.api"), applet_id=meta.id)

DEFAULT_TIMEOUT_S = 10

# OpenSSL renders cert dates like "Jun 24 20:14:34 2026 GMT" with an English
# month abbreviation. strptime("%b") honors LC_TIME, so we parse the month
# manually to stay locale-independent.
_MONTH_ABBREV = {
    name: i
    for i, name in enumerate(
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        start=1,
    )
}


def _parse_cert_date(value: str) -> datetime | None:
    if not value:
        return None
    parts = value.split()
    # Expected shape: ["Mon", "D", "HH:MM:SS", "YYYY", "TZ"]
    if len(parts) != 5:
        return None
    month = _MONTH_ABBREV.get(parts[0])
    if month is None:
        return None
    try:
        day = int(parts[1])
        year = int(parts[3])
        hh, mm, ss = (int(x) for x in parts[2].split(":"))
    except ValueError:
        return None
    # OpenSSL emits cert times in GMT/UTC regardless of system timezone.
    try:
        return datetime(year, month, day, hh, mm, ss, tzinfo=timezone.utc)
    except ValueError:
        return None


def _flatten_name(parts: object) -> str:
    """Flatten a getpeercert() subject/issuer tuple into ``k=v, k=v``."""
    if not isinstance(parts, list | tuple):
        return ""
    pieces: list[str] = []
    for rdn in parts:
        if not isinstance(rdn, list | tuple):
            continue
        for attr in rdn:
            if isinstance(attr, list | tuple) and len(attr) == 2:
                pieces.append(f"{attr[0]}={attr[1]}")
    return ", ".join(pieces)


def fetch_cert(
    *,
    host: str,
    port: int,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> CertInfo:
    """Fetch the peer certificate for ``host:port``.

    Returns a CertInfo with either populated fields or a non-empty ``error``.
    Never raises -- all failures become an ``error`` string so the UI can show
    status without crashing a background thread.
    """
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            peer = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return CertInfo(
            host=host,
            port=port,
            not_after=None,
            subject="",
            issuer="",
            error=f"verify failed: {getattr(exc, 'verify_message', None) or exc}",
        )
    except TimeoutError:
        return CertInfo(
            host=host,
            port=port,
            not_after=None,
            subject="",
            issuer="",
            error="timeout",
        )
    except (socket.gaierror, OSError, ssl.SSLError) as exc:
        return CertInfo(
            host=host,
            port=port,
            not_after=None,
            subject="",
            issuer="",
            error=str(exc) or exc.__class__.__name__,
        )

    if not peer:
        return CertInfo(
            host=host,
            port=port,
            not_after=None,
            subject="",
            issuer="",
            error="no peer certificate",
        )

    raw_not_after = str(peer.get("notAfter", ""))
    not_after = _parse_cert_date(raw_not_after)
    subject = _flatten_name(peer.get("subject"))
    issuer = _flatten_name(peer.get("issuer"))
    if not_after is None:
        error = (
            "missing notAfter"
            if not raw_not_after
            else f"unparseable notAfter: {raw_not_after!r}"
        )
    else:
        error = None
    return CertInfo(
        host=host,
        port=port,
        not_after=not_after,
        subject=subject,
        issuer=issuer,
        error=error,
    )
