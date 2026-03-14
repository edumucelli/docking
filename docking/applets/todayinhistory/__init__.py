"""Today in History applet public API."""

from __future__ import annotations

from .applet import TodayInHistoryApplet
from .state import (
    HistoryEvent,
    _http_get_json,
    fallback_today_in_history,
    format_history_event,
)
from .state import fetch_today_in_history as _fetch_today_in_history


def fetch_today_in_history(month: int, day: int, limit: int = 20) -> list[HistoryEvent]:
    return _fetch_today_in_history(
        month=month,
        day=day,
        limit=limit,
        http_get_json=_http_get_json,
    )


__all__ = [
    "HistoryEvent",
    "TodayInHistoryApplet",
    "_http_get_json",
    "fallback_today_in_history",
    "fetch_today_in_history",
    "format_history_event",
]
