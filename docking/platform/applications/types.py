"""Binding-free canonical values for installed and matched applications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ApplicationOrigin(Enum):
    """How an application identity entered Docking's model."""

    INSTALLED = "installed"
    GENERATED = "generated"
    RUNTIME = "runtime"


class ApplicationLocation(Enum):
    """Where the application must execute."""

    SANDBOX = "sandbox"
    HOST = "host"


class ActionSource(Enum):
    """Metadata source that declared a desktop action."""

    GIO = "gio"
    DESKTOP_FILE = "desktop-file"


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationAction:
    """One source-aware action declared for an application."""

    action_id: str
    name: str
    sources: frozenset[ActionSource]
    file_exec_line: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationInfo:
    """Source-faithful metadata for one resolvable application."""

    desktop_id: str
    name: str
    declared_icon: str
    wm_class: str
    exec_line: str
    origin: ApplicationOrigin
    location: ApplicationLocation
    desktop_file: Path | None
    executable_path: Path | None
    aliases: tuple[str, ...]
    visible: bool
    has_gio_source: bool
    generic_name: str = ""
    description: str = ""
    categories: tuple[str, ...] = ()
    categories_raw: str = ""
    keywords: tuple[str, ...] = ()
    actions: tuple[ApplicationAction, ...] = ()

    @property
    def icon_name(self) -> str:
        """Compatibility spelling for the canonical declared icon."""
        return self.declared_icon


class MatchMethod(Enum):
    """Evidence route that selected an application identity."""

    LAUNCH_PROVENANCE = "launch-provenance"
    WINE_INSTANCE = "wine-instance"
    VISIBLE_ALIAS = "visible-alias"
    INSTANCE_HINT = "instance-hint"
    DESKTOP_ID = "desktop-id"
    WM_CLASS = "wm-class"
    RUNTIME_PATH_SPLIT = "runtime-path-split"


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchEvidence:
    """Raw runtime evidence retained with a structured match."""

    method: MatchMethod
    raw_app_id: str
    instance_hint: str = ""
    pid: int | None = None
    executable_path: Path | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationMatch:
    """A desktop identity selected from runtime evidence."""

    desktop_id: str
    application: ApplicationInfo | None
    evidence: MatchEvidence

    @property
    def runtime_app(self) -> ApplicationInfo | None:
        """Expose runtime-only metadata under the migration-era name."""
        if (
            self.application is not None
            and self.application.origin is ApplicationOrigin.RUNTIME
        ):
            return self.application
        return None


__all__ = [
    "ActionSource",
    "ApplicationAction",
    "ApplicationInfo",
    "ApplicationLocation",
    "ApplicationMatch",
    "ApplicationOrigin",
    "MatchEvidence",
    "MatchMethod",
]
