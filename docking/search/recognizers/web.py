"""Recognize unambiguous web targets and construct search-engine URLs.

Direct target recognition is intentionally conservative. Only HTTP or HTTPS
URLs with a host, email addresses, domain-shaped values, valid-looking IPv4
targets, and localhost forms are accepted. Whitespace rejects direct routing,
and arbitrary URI schemes are not opened. Text that does not meet these rules
remains an ordinary query and can later receive the low-priority web fallback.

Search-engine definitions contain URL templates only. No request is performed
in this module, and unknown engine IDs fall back deterministically to
DuckDuckGo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

DEFAULT_WEB_ENGINE = "duckduckgo"


@dataclass(frozen=True, slots=True)
class WebEngine:
    """Immutable metadata and URL construction for one search engine."""

    id: str
    name: str
    search_url: str

    def url_for(self, query: str) -> str:
        """Encode a query and substitute it into this engine's URL template."""
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
    """Return a supported engine, falling back to the built-in default."""
    return _WEB_ENGINE_BY_ID.get(
        engine_id,
        _WEB_ENGINE_BY_ID[DEFAULT_WEB_ENGINE],
    )


def is_likely_web_question(text: str) -> bool:
    """Return whether ordinary text has a clear, language-level question form."""
    value = " ".join(text.split())
    return bool(value and (value.endswith("?") or _QUESTION_START_RE.match(value)))


def normalize_web_target(text: str) -> str | None:
    """Return an openable URI only when text unambiguously resembles one."""
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
