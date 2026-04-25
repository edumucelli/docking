"""Pure upload and state helpers for the Drag Share applet."""

from __future__ import annotations

import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from docking.log import get_logger

UPLOAD_ENDPOINT = "https://0x0.st"
USER_AGENT = "Docking/1.0 (Linux; Drag Share Applet)"
UPLOAD_TIMEOUT_S = 60

log = get_logger("dragshare.state")


class UrlOpen(Protocol):
    def __call__(self, request: urllib.request.Request, *, timeout: float) -> Any: ...


class DragshareStatus(str, Enum):
    IDLE = "idle"
    UPLOADING = "uploading"
    DONE = "done"
    ERROR = "error"


class UploadError(RuntimeError):
    """Raised when 0x0.st upload cannot produce a usable share URL."""


@dataclass(frozen=True, slots=True)
class UploadResult:
    url: str
    file_name: str


def file_path_from_uri(uri: str) -> Path | None:
    """Return a local file path for file:// URI or plain path input."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == "file":
        return Path(urllib.parse.unquote(parsed.path))
    if parsed.scheme:
        return None
    return Path(urllib.parse.unquote(uri)).expanduser()


def first_uploadable_file(uris: list[str]) -> Path | None:
    """Return the first existing regular local file from a URI drop payload."""
    for uri in uris:
        path = file_path_from_uri(uri)
        if path is not None and path.is_file():
            return path
    return None


def upload_file(
    path: Path,
    *,
    endpoint: str = UPLOAD_ENDPOINT,
    timeout: float = UPLOAD_TIMEOUT_S,
    opener: UrlOpen | None = None,
) -> UploadResult:
    """Upload one file to 0x0.st and return the resulting public URL."""
    path = path.expanduser()
    if not path.is_file():
        raise UploadError("Only regular files can be shared")

    boundary = f"----DockingDragShare{uuid.uuid4().hex}"
    body = _multipart_body(path=path, boundary=boundary)
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    open_url = opener if opener is not None else urllib.request.urlopen

    try:
        with open_url(request, timeout=timeout) as response:
            status = response.getcode()
            payload = response.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        log.debug("0x0.st HTTP error for %s: %s %s", path, exc.code, body_text)
        detail = body_text if body_text else f"HTTP {exc.code}"
        raise UploadError(detail) from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        log.debug("0x0.st upload failed for %s: %s", path, exc)
        raise UploadError("Upload failed") from exc

    if status < 200 or status >= 300:
        raise UploadError(f"HTTP {status}")
    if not payload.startswith(("http://", "https://")):
        log.debug("Unexpected 0x0.st response for %s: %r", path, payload)
        raise UploadError("Upload service returned an invalid URL")

    return UploadResult(url=payload, file_name=path.name)


def _multipart_body(*, path: Path, boundary: str) -> bytes:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = _quote_header_value(path.name)
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        "\r\n"
    ).encode()
    footer = f"\r\n--{boundary}--\r\n".encode()
    return header + path.read_bytes() + footer


def _quote_header_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
