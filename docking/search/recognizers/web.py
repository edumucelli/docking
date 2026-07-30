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
    keywords: tuple[str, ...]

    def url_for(self, query: str) -> str:
        return self.search_url.format(query=quote_plus(query.strip()))


_WEB_ENGINES: tuple[WebEngine, ...] = (
    WebEngine(
        id="duckduckgo",
        name="DuckDuckGo",
        search_url="https://duckduckgo.com/?q={query}",
        keywords=("ddg", "duckduckgo"),
    ),
    WebEngine(
        id="google",
        name="Google",
        search_url="https://www.google.com/search?q={query}",
        keywords=("g", "google"),
    ),
    WebEngine(
        id="brave",
        name="Brave Search",
        search_url="https://search.brave.com/search?q={query}",
        keywords=("brave",),
    ),
    WebEngine(
        id="bing",
        name="Bing",
        search_url="https://www.bing.com/search?q={query}",
        keywords=("bing",),
    ),
    WebEngine(
        id="github",
        name="GitHub",
        search_url="https://github.com/search?q={query}",
        keywords=("gh", "github"),
    ),
)
_WEB_ENGINE_BY_ID = {engine.id: engine for engine in _WEB_ENGINES}
_WEB_ENGINE_BY_KEYWORD = {
    keyword: engine for engine in _WEB_ENGINES for keyword in engine.keywords
}

_DOMAIN_RE = re.compile(
    r"^(?:localhost(?::\d+)?|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?:[/:?#].*)?$"
)
_IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:[/?#].*)?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def get_web_engine(engine_id: str) -> WebEngine:
    return _WEB_ENGINE_BY_ID.get(
        engine_id,
        _WEB_ENGINE_BY_ID[DEFAULT_WEB_ENGINE],
    )


def get_web_engine_by_keyword(keyword: str) -> WebEngine | None:
    return _WEB_ENGINE_BY_KEYWORD.get(keyword.casefold())


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
    "get_web_engine_by_keyword",
    "normalize_web_target",
]
