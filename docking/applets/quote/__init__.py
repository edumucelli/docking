"""Quote applet package."""

from __future__ import annotations

from .applet import QuoteApplet
from .state import (
    DEFAULT_SOURCE,
    FALLBACK_QUOTES,
    SOURCE_LABELS,
    QuoteEntry,
    _http_get_json,
    format_quote,
    source_fallback,
)
from .state import (
    fetch_quotes as _fetch_quotes,
)


def fetch_quotes(source: str, limit: int = 20) -> list[QuoteEntry]:
    # Keep compatibility with tests and existing monkeypatch paths:
    # patching docking.applets.quote._http_get_json affects this wrapper.
    return _fetch_quotes(source=source, limit=limit, http_get_json=_http_get_json)


__all__ = [
    "DEFAULT_SOURCE",
    "FALLBACK_QUOTES",
    "SOURCE_LABELS",
    "QuoteApplet",
    "QuoteEntry",
    "_http_get_json",
    "fetch_quotes",
    "format_quote",
    "source_fallback",
]
