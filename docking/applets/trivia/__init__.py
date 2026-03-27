"""Public package surface for the Trivia applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``TriviaApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="trivia",
    name="Random Trivia",
    category=AppletCategory.INFORMATION,
)

from .applet import TriviaApplet
from .state import (
    TriviaEntry,
    _http_get_json,
    answer_entry,
    fallback_trivia,
    format_difficulty,
    format_trivia,
    normalize_text,
)
from .state import fetch_trivia as _fetch_trivia


def fetch_trivia(
    limit: int = 20,
    shuffle_answers=None,
) -> list[TriviaEntry]:
    return _fetch_trivia(
        limit=limit,
        http_get_json=_http_get_json,
        shuffle_answers=shuffle_answers,
    )


__all__ = [
    "TriviaApplet",
    "TriviaEntry",
    "_http_get_json",
    "answer_entry",
    "fallback_trivia",
    "fetch_trivia",
    "format_difficulty",
    "format_trivia",
    "meta",
    "normalize_text",
]
