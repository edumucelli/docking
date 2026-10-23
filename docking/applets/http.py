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

"""HTTP helpers for applets that use the Python standard library."""

from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "DockingApplet/1.0 (+https://github.com/edumucelli/docking)"


def is_valid_http_url(value: str) -> bool:
    """Require an HTTP(S) host and valid port without raw whitespace or controls."""
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        value.encode("utf-8")
        parsed = urlsplit(value)
        port = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and (port is None or 0 <= port <= 65535)
        )
    except ValueError:
        return False


def http_get_bytes(
    url: str,
    *,
    timeout: float = 8.0,
    user_agent: str = DEFAULT_USER_AGENT,
    max_bytes: int | None = None,
) -> bytes:
    """Fetch a URL and return the raw response body."""
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:
        if max_bytes is None:
            return cast(bytes, response.read())
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError(f"Response exceeds {max_bytes} bytes")
        return cast(bytes, payload)


def http_get_text(
    url: str,
    *,
    timeout: float = 8.0,
    user_agent: str = DEFAULT_USER_AGENT,
    encoding: str = "utf-8",
) -> str:
    """Fetch a URL and decode the response body as text."""
    return http_get_bytes(url, timeout=timeout, user_agent=user_agent).decode(encoding)


def http_get_json(
    url: str,
    *,
    timeout: float = 8.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Any:
    """Fetch a URL and decode the response body as JSON."""
    return json.loads(http_get_text(url, timeout=timeout, user_agent=user_agent))
