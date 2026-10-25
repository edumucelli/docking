"""Windows application metadata supplied by Gio's native Win32 AppInfo backend.

Keep native launch handles in the registry, as on Unix. Windows command lines
must be launched through those handles rather than parsed as desktop Exec lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath

from .constants import FALLBACK_ICON
from .discovery import DiscoveryResult, safe_call
from .types import (
    ApplicationDiagnostic,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


def discover_windows_applications(source: Iterable[object]) -> DiscoveryResult:
    """Convert Win32 AppInfo records without requiring Unix DesktopAppInfo."""
    applications: list[ApplicationInfo] = []
    handles: dict[str, object] = {}
    diagnostics: list[ApplicationDiagnostic] = []
    for handle in source:
        name = str(
            safe_call(handle, "get_display_name") or safe_call(handle, "get_name") or ""
        ).strip()
        executable = str(safe_call(handle, "get_executable") or "")
        command = str(safe_call(handle, "get_commandline") or executable)
        if not name:
            continue
        application_id = str(safe_call(handle, "get_id") or "").strip()
        if not application_id:
            digest = hashlib.sha256((name + "\0" + command).encode()).hexdigest()[:24]
            application_id = f"windows:{digest}"
        if application_id in handles:
            continue
        basename = PureWindowsPath(executable).stem.lower()
        aliases = tuple(
            dict.fromkeys(
                value for value in (application_id.lower(), basename) if value
            )
        )
        icon = safe_call(handle, "get_icon")
        icon_name = str(safe_call(icon, "to_string") or FALLBACK_ICON)
        app = ApplicationInfo(
            desktop_id=application_id,
            name=name,
            declared_icon=icon_name,
            wm_class=basename,
            exec_line=command,
            origin=ApplicationOrigin.INSTALLED,
            location=ApplicationLocation.HOST,
            desktop_file=None,
            executable_path=Path(executable) if executable else None,
            aliases=aliases,
            visible=safe_call(handle, "should_show") is not False,
            has_gio_source=True,
            description=str(safe_call(handle, "get_description") or ""),
        )
        applications.append(app)
        handles[application_id] = handle
        diagnostics.append(
            ApplicationDiagnostic(
                desktop_id=application_id,
                name=name,
                desktop_file=None,
                visible=app.visible,
                has_gio_source=True,
                wm_class=basename,
                launcher_basename=basename,
                aliases=aliases,
            )
        )
    return DiscoveryResult(
        applications=tuple(applications),
        handles=handles,
        transient=(),
        transient_handles={},
        presentation_order=tuple(handles),
        diagnostics=tuple(diagnostics),
    )
