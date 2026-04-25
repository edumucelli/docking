"""Speedtest runner that uses the bundled pure-Python LibreSpeed client.

No external binary dependency. Call :func:`run_speedtest` from a background
thread; it never raises -- failures come back as :class:`SpeedtestError`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from docking.applets.speedtest import meta
from docking.applets.speedtest.librespeed import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DURATION_S,
    LibrespeedError,
    run_speedtest,
)
from docking.applets.speedtest.state import SpeedtestResult
from docking.log import get_logger, with_context

log = with_context(get_logger(name="speedtest.api"), applet_id=meta.id)


class SpeedtestError(NamedTuple):
    """Failure mode from a speed-test attempt."""

    message: str


def run_librespeed(
    *,
    duration: float = DEFAULT_DURATION_S,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> SpeedtestResult | SpeedtestError:
    """Run one full speed test against the LibreSpeed fleet.

    Returns a :class:`SpeedtestResult` on success, or a
    :class:`SpeedtestError` with a user-facing message on failure.
    """
    try:
        raw = run_speedtest(duration=duration, concurrency=concurrency)
    except LibrespeedError as exc:
        return SpeedtestError(message=str(exc))
    except (OSError, ValueError) as exc:
        return SpeedtestError(message=str(exc) or exc.__class__.__name__)
    except Exception as exc:
        log.bind(action="run").debug("Unexpected speedtest failure: %s", exc)
        return SpeedtestError(message=f"unexpected: {exc}")

    return SpeedtestResult(
        download_mbps=raw.download_mbps,
        upload_mbps=raw.upload_mbps,
        ping_ms=raw.ping_ms,
        jitter_ms=raw.jitter_ms,
        server=raw.server_name,
        timestamp=datetime.now(timezone.utc),
    )
