"""Persistent browser profile identity."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    name: str
    session_name: str
    url: str
    bridge_handler: str
    bridge_script: str

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""
