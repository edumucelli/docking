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
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "DockingApplet/1.0 (+https://github.com/edumucelli/docking)"


def http_get_bytes(
    url: str,
    *,
    timeout: float = 8.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> bytes:
    """Fetch a URL and return the raw response body."""
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


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
