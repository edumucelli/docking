"""Canonical application domain types.

Leaf module — imports nothing from ``docking.*``.  All types are frozen
dataclasses suitable for registry keys, cache entries, and immutable
published snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ApplicationOrigin(str, Enum):
    """How an ApplicationInfo record was discovered."""

    INSTALLED = "installed"
    HOST = "host"
    GENERATED = "generated"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class ApplicationAction:
    """One named action exposed by a desktop entry."""

    action_id: str
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationInfo:
    """Canonical installed (or runtime-only) application metadata.

    This single type replaces ``DesktopInfo``, ``DesktopAppListing``,
    ``ApplicationEntry``, ``ApplicationSnapshot`` and — via
    ``origin=RUNTIME`` — ``RuntimeAppIdentity``.
    """

    desktop_id: str
    name: str
    icon_name: str
    wm_class: str
    exec_line: str
    origin: ApplicationOrigin
    desktop_file: Path | None
    executable_path: Path | None
    aliases: tuple[str, ...] = ()
    generic_name: str = ""
    description: str = ""
    categories: str = ""
    keywords: tuple[str, ...] = ()
    actions: tuple[ApplicationAction, ...] = ()


class MatchMethod(str, Enum):
    """How a matcher resolved a window to an application."""

    LAUNCH_PROVENANCE = "launch-provenance"
    WINE_INSTANCE = "wine-instance"
    VISIBLE_ALIAS = "visible-alias"
    INSTANCE_HINT = "instance-hint"
    DESKTOP_ID = "desktop-id"
    WM_CLASS = "wm-class"
    RUNTIME_PATH_SPLIT = "runtime-path-split"


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """Why the matcher associated a window with a particular application."""

    method: MatchMethod
    raw_app_id: str
    instance_hint: str = ""
    pid: int | None = None
    executable_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ApplicationMatch:
    """A resolved application match with supporting evidence."""

    application: ApplicationInfo
    evidence: MatchEvidence

    @property
    def desktop_id(self) -> str:
        return self.application.desktop_id


__all__ = [
    "ApplicationAction",
    "ApplicationInfo",
    "ApplicationMatch",
    "ApplicationOrigin",
    "MatchEvidence",
    "MatchMethod",
]
