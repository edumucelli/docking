"""Recognize web targets and define supported search engines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

DEFAULT_WEB_ENGINE = "duckduckgo"


@dataclass(frozen=True, slots=True)
class WebEngine:
    id: str
    name: str
    search_url: str

    def url_for(self, query: str) -> str:
        return self.search_url.format(query=quote_plus(query.strip()))


_WEB_ENGINES: tuple[WebEngine, ...] = (
    WebEngine(
        id="duckduckgo",
        name="DuckDuckGo",
        search_url="https://duckduckgo.com/?q={query}",
    ),
    WebEngine(
        id="google",
        name="Google",
        search_url="https://www.google.com/search?q={query}",
    ),
    WebEngine(
        id="brave",
        name="Brave Search",
        search_url="https://search.brave.com/search?q={query}",
    ),
    WebEngine(
        id="bing",
        name="Bing",
        search_url="https://www.bing.com/search?q={query}",
    ),
)
_WEB_ENGINE_BY_ID = {engine.id: engine for engine in _WEB_ENGINES}

_DOMAIN_RE = re.compile(
    r"^(?:localhost(?::\d+)?|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?:[/:?#].*)?$"
)
_IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:[/?#].*)?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_QUESTION_START_RE = re.compile(
    r"^(?:what|who|where|when|why|how|which|can|could|do|does|is|are|should|would)\b",
    re.IGNORECASE,
)


def get_web_engine(engine_id: str) -> WebEngine:
    return _WEB_ENGINE_BY_ID.get(
        engine_id,
        _WEB_ENGINE_BY_ID[DEFAULT_WEB_ENGINE],
    )


def is_likely_web_question(text: str) -> bool:
    """Return whether ordinary text has a clear question form."""
    value = " ".join(text.split())
    return bool(value and (value.endswith("?") or _QUESTION_START_RE.match(value)))


def normalize_web_target(text: str) -> str | None:
    """Return an openable URI when text unambiguously resembles one."""
    value = text.strip()
    if not value or any(character.isspace() for character in value):
        return None
    parsed = urlparse(value)
    if parsed.scheme.casefold() == "mailto" and _EMAIL_RE.fullmatch(parsed.path):
        return value
    if _EMAIL_RE.fullmatch(value):
        return f"mailto:{value}"
    if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
        return value
    if _DOMAIN_RE.fullmatch(value) or _IP_RE.fullmatch(value):
        return (
            f"http://{value}" if value.startswith("localhost") else f"https://{value}"
        )
    return None


__all__ = [
    "DEFAULT_WEB_ENGINE",
    "WebEngine",
    "get_web_engine",
    "is_likely_web_question",
    "normalize_web_target",
]
