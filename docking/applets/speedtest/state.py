"""Pure state and formatting logic for speedtest applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NamedTuple

from docking.i18n import _


class SpeedtestResult(NamedTuple):
    """One completed speed-test measurement."""

    download_mbps: float
    upload_mbps: float
    ping_ms: float
    jitter_ms: float
    server: str
    timestamp: datetime  # UTC


@dataclass(frozen=True, slots=True)
class SpeedtestPrefs:
    """Persisted speedtest applet preferences."""

    last_result: SpeedtestResult | None = None


def format_speed(mbps: float) -> str:
    """Compact Mbps badge with a unit suffix (e.g. '250M', '1.2G')."""
    if mbps >= 1000:
        return f"{mbps / 1000:.1f}G"
    if mbps >= 100:
        return f"{round(mbps)}M"
    if mbps >= 10:
        return f"{mbps:.0f}M"
    return f"{mbps:.1f}M"


def speed_tier(mbps: float) -> str:
    """Coarse severity tier used to pick icon colors."""
    if mbps >= 100:
        return "fast"
    if mbps >= 25:
        return "medium"
    if mbps > 0:
        return "slow"
    return "none"


def format_timestamp(ts: datetime | None) -> str:
    """Render a timestamp as ``YYYY-MM-DD HH:MM`` local time."""
    if ts is None:
        return ""
    local = (
        ts.astimezone() if ts.tzinfo else ts.replace(tzinfo=timezone.utc).astimezone()
    )
    return local.strftime("%Y-%m-%d %H:%M")


def build_tooltip(
    *,
    result: SpeedtestResult | None,
    running: bool,
    error: str | None,
) -> str:
    """Multi-line tooltip summarizing the last run."""
    lines = [_("Speedtest")]
    if running:
        lines.append(_("Running..."))
        return "\n".join(lines)
    if error:
        lines.append(_("Error: {msg}").format(msg=error))
        return "\n".join(lines)
    if result is None:
        lines.append(_("Click to run a test"))
        return "\n".join(lines)
    lines.append(
        _("Down: {d:.1f} Mbps   Up: {u:.1f} Mbps").format(
            d=result.download_mbps, u=result.upload_mbps
        )
    )
    lines.append(
        _("Ping: {p:.1f} ms   Jitter: {j:.1f} ms").format(
            p=result.ping_ms, j=result.jitter_ms
        )
    )
    if result.server:
        lines.append(_("Server: {s}").format(s=result.server))
    when = format_timestamp(result.timestamp)
    if when:
        lines.append(_("At: {t}").format(t=when))
    return "\n".join(lines)


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> SpeedtestPrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return SpeedtestPrefs()
    raw = prefs.get("last_result")
    if not isinstance(raw, Mapping):
        return SpeedtestPrefs()
    try:
        ts_raw = raw.get("timestamp")
        ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else None
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is None:
            return SpeedtestPrefs()
        result = SpeedtestResult(
            download_mbps=float(raw.get("download_mbps", 0.0)),
            upload_mbps=float(raw.get("upload_mbps", 0.0)),
            ping_ms=float(raw.get("ping_ms", 0.0)),
            jitter_ms=float(raw.get("jitter_ms", 0.0)),
            server=str(raw.get("server", "")),
            timestamp=ts,
        )
    except (TypeError, ValueError):
        return SpeedtestPrefs()
    return SpeedtestPrefs(last_result=result)


def prefs_payload(*, result: SpeedtestResult | None) -> dict[str, Any]:
    """Build payload used by save_prefs()."""
    if result is None:
        return {}
    return {
        "last_result": {
            "download_mbps": result.download_mbps,
            "upload_mbps": result.upload_mbps,
            "ping_ms": result.ping_ms,
            "jitter_ms": result.jitter_ms,
            "server": result.server,
            "timestamp": result.timestamp.isoformat(),
        }
    }
