"""Public package surface for the Quote applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``QuoteApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="quote",
    name="Quote",
    category=AppletCategory.INFORMATION,
)

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
    "meta",
    "source_fallback",
]
