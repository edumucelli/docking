"""Immutable, binding-free evidence from the last window tracking scan."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from docking.platform.applications.types import (
        ApplicationDiscoveryDiagnostic,
        ApplicationMatch,
    )


class IdentityHintStatus(Enum):
    """Result of reading an additional, diagnostic-only window identity."""

    PRESENT = "present"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    READ_ERROR = "read-error"


@dataclass(frozen=True)
class WindowIdentityHint:
    property_name: str
    status: IdentityHintStatus
    value: str = ""


class WindowReason(Enum):
    """Why a tracking scan included, omitted, or failed to read a window."""

    INCLUDED = "included"
    NO_MATCH = "no-match"
    NO_IDENTITY = "no-identity"
    EMPTY_CLASS = "empty-class"
    CLASS_READ_FAILED = "class-read-failed"
    WORKSPACE = "workspace"
    XID_READ_FAILED = "xid-read-failed"
    WINDOW_TYPE_READ_FAILED = "window-type-read-failed"
    DESKTOP_OR_DOCK = "desktop-or-dock"
    SKIP_TASKLIST = "skip-tasklist"
    SKIP_TASKLIST_READ_FAILED = "skip-tasklist-read-failed"
    SKIP_TASKBAR = "skip-taskbar"
    OWN_PROCESS = "own-process"
    INVALID_WINDOW_ID = "invalid-window-id"


@dataclass(frozen=True)
class WindowDiagnostic:
    """Shareable identity evidence and the actual scan decision for one window."""

    window_id: str | None = None
    identities: tuple[tuple[str, str], ...] = ()
    pid: int | None = None
    executable_path: str | None = None
    workspace: str | None = None
    outcome: Literal["matched", "unmatched", "excluded", "error"] = "unmatched"
    reason: WindowReason = WindowReason.NO_MATCH
    desktop_id: str | None = None
    match_method: str | None = None
    matched_identity: str | None = None
    desktop_file: str | None = None
    launcher_basename: str | None = None
    aliases: tuple[str, ...] = ()
    read_errors: tuple[str, ...] = ()
    identity_hints: tuple[WindowIdentityHint, ...] = ()
    sandbox_app_id: str | None = None
    script_basename: str | None = None


@dataclass(frozen=True)
class WindowTrackingDiagnostic:
    """Read-only snapshot; retrieving it never starts a tracking scan."""

    status: Literal[
        "unavailable", "unsupported", "pending", "stopped", "failed", "available"
    ] = "unavailable"
    scanned_at: datetime | None = None
    registry_generation: int | None = None
    windows: tuple[WindowDiagnostic, ...] = ()
    detail: str = "Detailed window capture is unavailable for this backend."
    application_discovery: ApplicationDiscoveryDiagnostic | None = None


def with_match(
    record: WindowDiagnostic, match: ApplicationMatch | None
) -> WindowDiagnostic:
    """Copy only shareable launcher identity fields from a successful match."""
    if match is None:
        return record
    app = match.application
    basename = None
    if app is not None:
        try:
            argv = shlex.split(app.exec_line)
        except ValueError:
            argv = []
        basename = Path(argv[0]).name if argv else None
    return replace(
        record,
        outcome="matched",
        reason=WindowReason.INCLUDED,
        desktop_id=match.desktop_id,
        match_method=match.evidence.method.value,
        matched_identity=match.evidence.raw_app_id,
        desktop_file=str(app.desktop_file) if app and app.desktop_file else None,
        launcher_basename=basename,
        aliases=app.aliases if app else (),
    )
