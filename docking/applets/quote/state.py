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

"""State and data helpers for the Quote applet."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from docking.applets.http import http_get_json
from docking.applets.quote import meta
from docking.log import get_logger, with_context

log = with_context(get_logger(name="quote"), applet_id=meta.id)

DEFAULT_SOURCE = "quotationspage"
DEFAULT_FETCH_LIMIT = 20

SOURCE_LABELS: dict[str, str] = {
    "quotationspage": "Quotationspage.com",
    "qdb": "Qdb.us",
    "danstonchat": "Danstonchat.com",
    "viedemerde": "Viedemerde.fr",
    "fmylife": "Fmylife.com",
    "vitadimerda": "Vitadimerda.it",
    "chucknorrisfactsfr": "Chucknorrisfacts.fr",
}


@dataclass(frozen=True)
class QuoteEntry:
    text: str
    author: str = ""


FALLBACK_QUOTES: dict[str, tuple[QuoteEntry, ...]] = {
    "quotationspage": (
        QuoteEntry("Simplicity is the soul of efficiency.", "Austin Freeman"),
        QuoteEntry("Well done is better than well said.", "Benjamin Franklin"),
        QuoteEntry("First, solve the problem. Then, write the code.", "John Johnson"),
    ),
    "qdb": (
        QuoteEntry("Never test for an error condition you don't know how to handle."),
        QuoteEntry("Debugging is archaeology with breakpoints."),
        QuoteEntry("Logs are a time machine for bugs."),
    ),
    "danstonchat": (
        QuoteEntry("I refactored everything, now nothing is where it was."),
        QuoteEntry("If it's stupid and it works, document it."),
        QuoteEntry("The deadline is tomorrow; the bug is today."),
    ),
    "viedemerde": (
        QuoteEntry("Today I fixed one bug and discovered three."),
        QuoteEntry("Today production taught us a lesson in humility."),
        QuoteEntry("Today I trusted a quick workaround."),
    ),
    "fmylife": (
        QuoteEntry("Today I said 'tiny change'."),
        QuoteEntry("Today cache invalidation won."),
        QuoteEntry("Today tests passed and runtime disagreed."),
    ),
    "vitadimerda": (
        QuoteEntry("Today CI was green until I looked at it."),
        QuoteEntry("Today I optimized the wrong thing."),
        QuoteEntry("Today I merged right before lunch."),
    ),
    "chucknorrisfactsfr": (
        QuoteEntry("Chuck Norris can unit test entire systems with one assert."),
        QuoteEntry(
            "Chuck Norris commits directly to production. Production says thanks."
        ),
        QuoteEntry("Chuck Norris does not need retries. The network retries itself."),
    ),
}


def normalize_quote(text: str) -> str:
    clean = html.unescape(text).replace("\n", " ").replace("\r", " ").strip()
    return " ".join(clean.split())


def format_quote(entry: QuoteEntry) -> str:
    """Render quote text for tooltip/clipboard."""
    if entry.author:
        return f'"{entry.text}" - {entry.author}'
    return entry.text


def _parse_zenquotes(data: Any, limit: int) -> list[QuoteEntry]:
    quotes: list[QuoteEntry] = []
    if not isinstance(data, list):
        return quotes
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_text = item.get("q")
        raw_author = item.get("a", "")
        if not isinstance(raw_text, str):
            continue
        text = normalize_quote(raw_text)
        author = normalize_quote(raw_author) if isinstance(raw_author, str) else ""
        if text:
            quotes.append(QuoteEntry(text=text, author=author))
        if len(quotes) >= limit:
            break
    return quotes


def _parse_jokeapi(data: Any, limit: int) -> list[QuoteEntry]:
    quotes: list[QuoteEntry] = []
    if not isinstance(data, dict):
        return quotes

    jokes = data.get("jokes")
    entries = jokes if isinstance(jokes, list) else [data]

    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_joke = item.get("joke")
        if not isinstance(raw_joke, str):
            continue
        text = normalize_quote(raw_joke)
        if text:
            quotes.append(QuoteEntry(text=text))
        if len(quotes) >= limit:
            break
    return quotes


def _parse_chuck(data: Any) -> list[QuoteEntry]:
    if not isinstance(data, dict):
        return []
    value = data.get("value")
    if not isinstance(value, str):
        return []
    text = normalize_quote(value)
    if not text:
        return []
    return [QuoteEntry(text=text)]


def fetch_quotes(source: str, limit: int = DEFAULT_FETCH_LIMIT) -> list[QuoteEntry]:
    """Fetch quotes for a source. Returns empty list on any failure."""
    try:
        if source == "quotationspage":
            data = http_get_json("https://zenquotes.io/api/quotes")
            return _parse_zenquotes(data=data, limit=limit)
        if source == "chucknorrisfactsfr":
            data = http_get_json("https://api.chucknorris.io/jokes/random")
            return _parse_chuck(data=data)
        data = http_get_json(
            f"https://v2.jokeapi.dev/joke/Any?type=single&amount={limit}"
        )
        return _parse_jokeapi(data=data, limit=limit)
    except Exception as exc:
        log.bind(action="fetch_quotes").debug(
            "Failed to fetch quotes for source=%s: %s",
            source,
            exc,
        )
        return []


def source_fallback(source: str) -> list[QuoteEntry]:
    quotes = FALLBACK_QUOTES.get(source) or FALLBACK_QUOTES[DEFAULT_SOURCE]
    return list(quotes)
