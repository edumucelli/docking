"""Minimal pure-Python LibreSpeed client.

Ported from the Go reference implementation:
  https://github.com/librespeed/speedtest-cli
  LibreSpeed          Copyright (C) 2016-2020 Federico Dossena
  librespeed-cli      Copyright (C) 2020 Maddie Zhan
  Original license: GNU Lesser General Public License v3.0

This Python re-implementation is GPL-3.0-or-later, consistent with the
rest of Docking. It reproduces only the wire protocol (server list
fetching, HTTP ping, concurrent download and upload) -- not the CLI,
telemetry, CSV/JSON output, share-URL posting, or ICMP features.

Design notes
------------
- Uses only the Python standard library: urllib, http.client, ssl,
  threading, json. Zero new dependencies for Docking.
- Concurrent HTTP requests saturate bandwidth; stop signal via
  ``threading.Event`` so the wall-clock duration is honored.
- HTTP-only ping; no ICMP (avoids needing root).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from docking.log import get_logger

log = get_logger("speedtest.librespeed")

USER_AGENT = "docking-speedtest/1.0"
SERVER_LIST_URL = "https://librespeed.org/backend-servers/servers.php"
SERVER_LIST_FALLBACK_SUFFIX = "/.well-known/librespeed"

# Bytes per Mbps (decimal, 10^6 / 8).
_MBPS_DIVISOR = 125_000.0

# Chunk parameters for download test. ``ckSize`` is what the backend expects
# on its ``garbage.php`` endpoint; it caps each response at that many ~1MB
# chunks. We open parallel requests within a time budget to saturate the link.
_DOWNLOAD_CKSIZE = 100

# Size of each upload request in bytes. Matches librespeed-cli's default.
_UPLOAD_REQUEST_BYTES = 1024 * 1024  # 1 MiB per POST

# Read/write chunk size on the socket.
_IO_CHUNK = 64 * 1024

DEFAULT_PING_COUNT = 6
DEFAULT_DURATION_S = 10.0
DEFAULT_CONCURRENCY = 3
DEFAULT_SERVER_PICK_POOL = 5  # How many servers to ping when selecting fastest.
DEFAULT_TIMEOUT_S = 15.0


@dataclass
class Server:
    """One LibreSpeed backend server."""

    id: int
    name: str
    server: str  # Base URL or //host
    dl_url: str
    ul_url: str
    ping_url: str
    sponsor_name: str = ""

    @property
    def base_url(self) -> str:
        """Canonical base URL with scheme."""
        raw = self.server
        if raw.startswith("//"):
            return "https:" + raw
        if "://" not in raw:
            return "https://" + raw
        return raw

    def endpoint(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        clean = path.lstrip("/")
        return f"{base}/{clean}"


class SpeedtestResult(NamedTuple):
    download_mbps: float
    upload_mbps: float
    ping_ms: float
    jitter_ms: float
    server_name: str
    server_id: int


class LibrespeedError(Exception):
    """Any failure during a LibreSpeed run."""


@dataclass
class _Counter:
    """Thread-safe byte counter."""

    total: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, n: int) -> None:
        with self._lock:
            self.total += n


# -- Server list -------------------------------------------------------------


def fetch_server_list(
    *, url: str = SERVER_LIST_URL, timeout: float = DEFAULT_TIMEOUT_S
) -> list[Server]:
    """Fetch the JSON server list from librespeed.org (or a mirror)."""
    try:
        raw = _http_get_text(url=url, timeout=timeout)
    except Exception as exc:
        log.debug("Primary server list fetch failed, trying well-known: %s", exc)
        raw = _http_get_text(url=url + SERVER_LIST_FALLBACK_SUFFIX, timeout=timeout)
    return parse_server_list(text=raw)


def parse_server_list(*, text: str) -> list[Server]:
    """Parse the JSON shape produced by librespeed.org/backend-servers/servers.php."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise LibrespeedError("server list: unexpected JSON shape")
    servers: list[Server] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            servers.append(
                Server(
                    id=int(entry.get("id", 0)),
                    name=str(entry.get("name", "")),
                    server=str(entry["server"]),
                    dl_url=str(entry.get("dlURL", "")),
                    ul_url=str(entry.get("ulURL", "")),
                    ping_url=str(entry.get("pingURL", "")),
                    sponsor_name=str(entry.get("sponsorName", "") or ""),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("Skipping malformed server entry: %s", exc)
            continue
    return servers


# -- Ping --------------------------------------------------------------------


def ping_jitter(
    *,
    server: Server,
    count: int = DEFAULT_PING_COUNT,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[float, float]:
    """Return (avg_ping_ms, jitter_ms) via repeated HTTP GET of the ping URL.

    The first sample is discarded to absorb TLS/connect overhead.
    """
    url = server.endpoint(server.ping_url)
    samples: list[float] = []
    for _ in range(count):
        start = time.monotonic()
        try:
            _http_get_drain(url=url, timeout=timeout)
        except Exception as exc:
            log.debug("Ping sample failed: %s", exc)
            continue
        samples.append((time.monotonic() - start) * 1000.0)

    if len(samples) > 1:
        samples = samples[1:]
    if not samples:
        raise LibrespeedError("ping: no successful samples")

    avg = sum(samples) / len(samples)
    jitter = 0.0
    last = samples[0]
    for idx, value in enumerate(samples[1:], start=1):
        instant = abs(last - value)
        # Weighted smoothing used by librespeed-cli.
        if idx > 1:
            jitter = (
                jitter * 0.7 + instant * 0.3
                if jitter > instant
                else instant * 0.2 + jitter * 0.8
            )
        last = value
    return avg, jitter


# -- Fastest server selection ------------------------------------------------


def select_fastest(
    servers: list[Server],
    *,
    pool: int = DEFAULT_SERVER_PICK_POOL,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[Server, float, float]:
    """Pick the lowest-ping server from the first ``pool`` servers in the list."""
    if not servers:
        raise LibrespeedError("no servers available")
    candidates = servers[:pool]
    best: tuple[Server, float, float] | None = None
    for srv in candidates:
        try:
            avg, jit = ping_jitter(server=srv, count=3, timeout=timeout)
        except Exception as exc:
            log.debug("Skipping %s (%s): %s", srv.name, srv.base_url, exc)
            continue
        if best is None or avg < best[1]:
            best = (srv, avg, jit)
    if best is None:
        raise LibrespeedError("no server responded to ping")
    return best


# -- Download test -----------------------------------------------------------


def _download_worker(
    *,
    url: str,
    counter: _Counter,
    stop: threading.Event,
    timeout: float,
) -> None:
    parsed = urllib.parse.urlsplit(url)
    while not stop.is_set():
        conn = _open_connection(parsed=parsed, timeout=timeout)
        if conn is None:
            return
        try:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                },
            )
            resp = conn.getresponse()
            while not stop.is_set():
                chunk = resp.read(_IO_CHUNK)
                if not chunk:
                    break
                counter.add(len(chunk))
            resp.close()
        except Exception as exc:
            log.debug("Download worker error: %s", exc)
            return
        finally:
            with contextlib.suppress(Exception):
                conn.close()


def run_download(
    *,
    server: Server,
    duration: float = DEFAULT_DURATION_S,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> float:
    """Saturate download for ``duration`` seconds; return average Mbps."""
    query = urllib.parse.urlencode({"ckSize": _DOWNLOAD_CKSIZE})
    url = (
        server.endpoint(server.dl_url) + ("&" if "?" in server.dl_url else "?") + query
    )
    return _run_test(
        worker=lambda c, s: _download_worker(
            url=url, counter=c, stop=s, timeout=timeout
        ),
        duration=duration,
        concurrency=concurrency,
    )


# -- Upload test -------------------------------------------------------------


def _upload_worker(
    *,
    url: str,
    counter: _Counter,
    stop: threading.Event,
    timeout: float,
    payload: bytes,
) -> None:
    parsed = urllib.parse.urlsplit(url)
    while not stop.is_set():
        conn = _open_connection(parsed=parsed, timeout=timeout)
        if conn is None:
            return
        try:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            conn.putrequest("POST", path, skip_accept_encoding=True)
            conn.putheader("User-Agent", USER_AGENT)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(len(payload)))
            conn.putheader("Accept-Encoding", "identity")
            conn.endheaders()
            sent = 0
            while sent < len(payload) and not stop.is_set():
                end = min(sent + _IO_CHUNK, len(payload))
                conn.send(payload[sent:end])
                counter.add(end - sent)
                sent = end
            resp = conn.getresponse()
            resp.read()
            resp.close()
        except Exception as exc:
            log.debug("Upload worker error: %s", exc)
            return
        finally:
            with contextlib.suppress(Exception):
                conn.close()


def run_upload(
    *,
    server: Server,
    duration: float = DEFAULT_DURATION_S,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> float:
    """Saturate upload for ``duration`` seconds; return average Mbps."""
    url = server.endpoint(server.ul_url)
    payload = os.urandom(_UPLOAD_REQUEST_BYTES)
    return _run_test(
        worker=lambda c, s: _upload_worker(
            url=url, counter=c, stop=s, timeout=timeout, payload=payload
        ),
        duration=duration,
        concurrency=concurrency,
    )


# -- Orchestrator ------------------------------------------------------------


def run_speedtest(
    *,
    duration: float = DEFAULT_DURATION_S,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> SpeedtestResult:
    """End-to-end run: fetch servers, pick fastest, ping, download, upload."""
    servers = fetch_server_list(timeout=timeout)
    if not servers:
        raise LibrespeedError("server list is empty")
    server, ping_ms, jitter_ms = select_fastest(servers, timeout=timeout)
    log.debug("Selected %s (%s), ping %.1fms", server.name, server.base_url, ping_ms)
    download_mbps = run_download(
        server=server, duration=duration, concurrency=concurrency, timeout=timeout
    )
    upload_mbps = run_upload(
        server=server, duration=duration, concurrency=concurrency, timeout=timeout
    )
    return SpeedtestResult(
        download_mbps=download_mbps,
        upload_mbps=upload_mbps,
        ping_ms=ping_ms,
        jitter_ms=jitter_ms,
        server_name=server.name,
        server_id=server.id,
    )


# -- Internals ---------------------------------------------------------------


def _run_test(
    *,
    worker: Callable[[_Counter, threading.Event], None],
    duration: float,
    concurrency: int,
) -> float:
    counter = _Counter()
    stop = threading.Event()
    threads = [
        threading.Thread(target=worker, args=(counter, stop), daemon=True)
        for _ in range(max(1, concurrency))
    ]
    start = time.monotonic()
    for t in threads:
        t.start()
        # Stagger starts slightly to avoid a thundering herd on the server.
        time.sleep(0.05)
    stop.wait(timeout=duration)
    stop.set()
    elapsed = max(time.monotonic() - start, 0.001)
    for t in threads:
        t.join(timeout=2.0)
    return counter.total / elapsed / _MBPS_DIVISOR


def _open_connection(
    *,
    parsed: urllib.parse.SplitResult,
    timeout: float,
) -> http.client.HTTPConnection | None:
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        return http.client.HTTPSConnection(
            host, port=port, timeout=timeout, context=context
        )
    return http.client.HTTPConnection(host, port=port, timeout=timeout)


def _http_get_text(*, url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_get_drain(*, url: str, timeout: float) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Drain a tiny bit so the round-trip time includes first-byte return.
        resp.read(1)
