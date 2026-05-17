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

"""Release update checks and persistent update notification state.

User intent lives in ``Config``: whether update checks are enabled and how often
they should run. Runtime bookkeeping lives here, under XDG state storage:
last check time, ignored versions, and reminder suppression.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from docking.log import get_logger

logger = get_logger("updates")

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/edumucelli/docking/releases/latest"
)
PROJECT_RELEASES_URL = "https://github.com/edumucelli/docking/releases"
DEFAULT_STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "docking"
)
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "updates.json"
DEFAULT_UPDATE_TIMEOUT_S = 6
UPDATE_USER_AGENT = "Docking update checker"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """GitHub release metadata needed by the update UI."""

    version: str
    url: str
    name: str
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateState:
    """Persistent runtime state for update checks and popup responses."""

    last_checked_at: str = ""
    last_seen_version: str = ""
    ignored_version: str = ""
    remind_after: str = ""
    last_result: str = ""
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    """Result of deciding whether to show the update popup."""

    should_show: bool
    reason: str
    release: ReleaseInfo | None = None


def load_state(path: Path | str | None = None) -> UpdateState:
    """Load update state, falling back to defaults on missing/corrupt data."""
    state_path = Path(path) if path is not None else DEFAULT_STATE_FILE
    if not state_path.exists():
        return UpdateState()
    try:
        with state_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        logger.warning("Failed to load update state %s: %s", state_path, exc)
        return UpdateState()
    if not isinstance(raw, dict):
        logger.warning("Invalid update state payload in %s; using defaults", state_path)
        return UpdateState()
    return UpdateState(
        last_checked_at=_coerce_str(raw.get("last_checked_at")),
        last_seen_version=_coerce_str(raw.get("last_seen_version")),
        ignored_version=_coerce_str(raw.get("ignored_version")),
        remind_after=_coerce_str(raw.get("remind_after")),
        last_result=_coerce_str(raw.get("last_result")),
        last_error=_coerce_str(raw.get("last_error")),
    )


def save_state(state: UpdateState, *, path: Path | str | None = None) -> None:
    """Persist update state atomically."""
    state_path = Path(path) if path is not None else DEFAULT_STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        json.dump(
            {
                "ignored_version": state.ignored_version,
                "last_checked_at": state.last_checked_at,
                "last_error": state.last_error,
                "last_result": state.last_result,
                "last_seen_version": state.last_seen_version,
                "remind_after": state.remind_after,
            },
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    tmp_path.replace(state_path)


def should_check_for_updates(
    *,
    enabled: bool,
    interval_hours: int,
    state: UpdateState,
    now: datetime | None = None,
) -> bool:
    """Return true when an automatic update check is due."""
    if not enabled:
        return False
    checked_at = parse_timestamp(state.last_checked_at)
    if checked_at is None:
        return True
    reference = _aware_utc(now)
    return reference - checked_at >= timedelta(hours=max(1, interval_hours))


def decide_update_popup(
    *,
    current_version: str,
    release: ReleaseInfo | None,
    state: UpdateState,
    now: datetime | None = None,
) -> UpdateDecision:
    """Decide whether a fetched release should be shown to the user."""
    if release is None:
        return UpdateDecision(False, "no-release")
    if not is_newer_version(release.version, current_version):
        return UpdateDecision(False, "not-newer", release)
    if normalize_version(release.version) == normalize_version(state.ignored_version):
        return UpdateDecision(False, "ignored", release)
    remind_after = parse_timestamp(state.remind_after)
    if remind_after is not None and _aware_utc(now) < remind_after:
        return UpdateDecision(False, "remind-later", release)
    return UpdateDecision(True, "newer", release)


def fetch_latest_release(
    *,
    url: str = GITHUB_LATEST_RELEASE_URL,
    timeout: int = DEFAULT_UPDATE_TIMEOUT_S,
) -> ReleaseInfo:
    """Fetch latest GitHub release metadata."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": UPDATE_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.URLError:
        raise
    data = json.loads(payload.decode("utf-8"))
    return parse_github_release(data)


def parse_github_release(data: object) -> ReleaseInfo:
    """Parse a GitHub release payload into ``ReleaseInfo``."""
    if not isinstance(data, dict):
        raise ValueError("GitHub release payload is not an object")
    payload = cast("dict[str, object]", data)
    tag = _coerce_str(payload.get("tag_name"))
    if not tag:
        raise ValueError("GitHub release payload is missing tag_name")
    html_url = _coerce_str(payload.get("html_url"))
    if not html_url:
        html_url = PROJECT_RELEASES_URL
    name = _coerce_str(payload.get("name"))
    if not name:
        name = tag
    published_at = _coerce_str(payload.get("published_at"))
    return ReleaseInfo(
        version=normalize_version(tag),
        url=html_url,
        name=name,
        published_at=published_at if published_at else None,
    )


def is_newer_version(candidate: str, current: str) -> bool:
    """Return true when candidate is newer than current."""
    return compare_versions(candidate, current) > 0


def compare_versions(left: str, right: str) -> int:
    """Compare two simple release versions.

    Supports normal Docking tags such as ``1.16.0`` and ``v1.16.0``. Suffixes
    are treated as prerelease/build details and ignored for ordering.
    """
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts), 3)
    left_parts = left_parts + (0,) * (width - len(left_parts))
    right_parts = right_parts + (0,) * (width - len(right_parts))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def normalize_version(version: str) -> str:
    """Normalize release tags for storage and comparison."""
    value = str(version).strip()
    if value.startswith(("v", "V")):
        value = value[1:]
    match = re.match(r"^([0-9]+(?:\.[0-9]+)*)", value)
    return match.group(1) if match else value


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_utc(parsed)


def utc_now_iso(now: datetime | None = None) -> str:
    """Return a UTC ISO timestamp suitable for update state."""
    return _aware_utc(now).isoformat()


def _version_parts(version: str) -> tuple[int, ...]:
    normalized = normalize_version(version)
    if not normalized:
        return ()
    parts: list[int] = []
    for piece in normalized.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts)


def _aware_utc(value: datetime | None) -> datetime:
    timestamp = datetime.now(timezone.utc) if value is None else value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _coerce_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
