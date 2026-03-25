"""Pure URL-shortening request logic for the URL Shortener applet.

This module isolates the remote-service boundary used by the shortener applet.
The GTK layer should not need to know how query parameters are built, which
HTTP errors can occur, or how those failures are translated into user-facing
messages.

The logic here is intentionally small and explicit:

- normalize the submitted URL,
- call the is.gd simple API,
- translate transport/HTTP failures into ``Error: ...`` strings,
- provide the tiny persisted preference payload used by the dialog.

That contract keeps the UI code straightforward: it asks for a shortened URL and
renders whatever string comes back.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_API_URL = "https://is.gd/create.php"
_USER_AGENT = "Docking/1.0 (Linux; URL Shortener Applet)"
_TIMEOUT_S = 8


def shorten_url(url: str) -> str:
    """Shorten a URL via is.gd.

    Returns the short URL on success or an error message prefixed with
    ``Error:`` on failure.
    """
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    params = urllib.parse.urlencode({"format": "simple", "url": url})
    try:
        req = urllib.request.Request(
            f"{_API_URL}?{params}",
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.read().decode().strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace").strip()
        return f"Error: {body}" if body else f"Error: HTTP {exc.code}"
    except urllib.error.URLError:
        return "Error: network unavailable"
    except TimeoutError:
        return "Error: request timed out"


def prefs_payload(*, last_url: str) -> dict[str, Any]:
    return {"last_url": last_url}
