"""Random Trivia applet public API."""

from __future__ import annotations

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
    "normalize_text",
]
