"""Windows application metadata supplied by Gio's native Win32 AppInfo backend.

Keep native launch handles in the registry, as on Unix. Windows command lines
must be launched through those handles rather than parsed as desktop Exec lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from ..constants import FALLBACK_ICON
from ..discovery import DiscoveryResult, safe_call
from ..types import (
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


def merge_shell_catalog(
    result: DiscoveryResult, applications: tuple[ApplicationInfo, ...]
) -> DiscoveryResult:
    """Use shell menu identities while retaining all Gio association handles."""
    native_ids = {app.desktop_id for app in applications}
    gio = {app.desktop_id: app for app in result.applications}
    records = [
        replace(app, visible=False)
        for app in result.applications
        if app.desktop_id not in native_ids
    ]
    for native in applications:
        previous = gio.get(native.desktop_id)
        if previous is not None:
            native = replace(
                previous,
                name=native.name,
                executable_path=native.executable_path,
                wm_class=native.wm_class,
                aliases=tuple(dict.fromkeys((*native.aliases, *previous.aliases))),
                visible=native.visible,
                launch_target=native.launch_target,
                document_target=native.document_target,
                export_file=native.export_file,
                export_data=native.export_data,
            )
        records.append(native)
    return replace(
        result,
        applications=tuple(records),
        presentation_order=tuple(app.desktop_id for app in records),
        diagnostics=tuple(
            ApplicationDiagnostic(
                desktop_id=app.desktop_id,
                name=app.name,
                desktop_file=app.desktop_file,
                visible=app.visible,
                has_gio_source=app.has_gio_source,
                wm_class=app.wm_class,
                launcher_basename=app.wm_class,
                aliases=app.aliases,
            )
            for app in records
        ),
    )
