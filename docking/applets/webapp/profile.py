"""Pure website identity and origin policy shared by browser processes."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .state import NavigationTarget, navigation_target


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

    def allows_origin(self, uri: str) -> bool:
        return (
            navigation_target(uri, self.host) is NavigationTarget.INTERNAL
            and uri.split(":", 1)[0].casefold() == "https"
        )
