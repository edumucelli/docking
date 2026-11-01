"""Enumerate native application-folder identities in an owned worker apartment."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.applications.constants import FALLBACK_ICON
from docking.platform.applications.implementations.windows.catalog_shortcuts import (
    read_shortcut_sources,
)
from docking.platform.applications.metadata import ApplicationMetadata
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    object_result,
    query,
    release,
)
from docking.platform.native.windows.ffi import bind
from docking.platform.native.windows.packages import hidden_application_ids
from docking.platform.native.windows.properties import APP_ID, PropertyKey

log = get_logger("windows.catalog")

if TYPE_CHECKING:
    from docking.platform.applications.implementations.windows.export import (
        WindowsApplicationExport,
    )

_APPS_FOLDER = GUID.parse("1e87508d-89c2-42f0-8a7e-645a0f50ca58")
_SHELL_ITEM = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
_SHELL_ITEM2 = GUID.parse("7e9fb0d3-919f-4307-ab2e-9b1860310c93")
_ENUM_HANDLER = GUID.parse("94f60519-2850-4924-aa5a-d15e84868039")
_ENUM_ITEMS = GUID.parse("70629033-e363-4a28-a567-0db78006e6d7")
_LINK_TARGET = PropertyKey(GUID.parse("b9b4b3fc-2b51-4a42-b5d8-324146afcf25"), 2)


def _string(ole, pointer, index: int, *arguments) -> str:
    value = ctypes.c_void_p()
    kinds, values = arguments[::2], arguments[1::2]
    try:
        status = method(
            pointer, index, HRESULT, *kinds, ctypes.POINTER(ctypes.c_void_p)
        )(pointer, *values, ctypes.byref(value))
        if status < 0:
            raise OSError(
                status & 0xFFFFFFFF, "Could not read shell application metadata"
            )
        return ctypes.wstring_at(value) if value.value else ""
    finally:
        if value.value:
            bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)(value)


def _record(ole, stack: ExitStack, item) -> ApplicationInfo | None:
    attributes = ctypes.c_uint32()
    status = method(item, 6, HRESULT, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))(
        item, 0x80000, ctypes.byref(attributes)
    )  # SFGAO_HIDDEN
    if status < 0:
        raise OSError(
            status & 0xFFFFFFFF, "Could not read shell application attributes"
        )
    if attributes.value & 0x80000:
        return None
    properties = query(stack, item, _SHELL_ITEM2)
    app_id = _string(
        ole, properties, 17, ctypes.POINTER(PropertyKey), ctypes.byref(APP_ID)
    )
    name = _string(ole, item, 5, ctypes.c_uint32, 0)  # SIGDN_NORMALDISPLAY
    if not app_id or not name:
        return None
    try:
        target = _string(
            ole, properties, 17, ctypes.POINTER(PropertyKey), ctypes.byref(_LINK_TARGET)
        )
    except OSError:
        target = ""
    path = PureWindowsPath(target)
    executable = Path(target) if target and path.is_absolute() else None
    basename = path.stem.lower() if executable is not None else ""
    return ApplicationInfo(
        desktop_id=app_id,
        name=name,
        declared_icon=FALLBACK_ICON,
        wm_class=basename,
        exec_line="",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.HOST,
        desktop_file=None,
        executable_path=executable,
        aliases=tuple(dict.fromkeys(v for v in (app_id.lower(), basename) if v)),
        visible=True,
        has_gio_source=False,
        launch_target="shell:AppsFolder\\" + app_id,
    )


def read_shell_applications() -> tuple[ApplicationInfo, ...]:
    """Copy immutable facts; no COM pointers or Gio objects leave this call."""
    with apartment() as ole, ExitStack() as stack:
        folder = ctypes.c_void_p()
        get_folder = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHGetKnownFolderItem",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        try:
            status = get_folder(
                ctypes.byref(_APPS_FOLDER),
                0,
                None,
                ctypes.byref(_SHELL_ITEM),
                ctypes.byref(folder),
            )
            if status < 0 or not folder.value:
                raise OSError(
                    status & 0xFFFFFFFF, "Could not open the application folder"
                )
        finally:
            if folder.value:
                stack.callback(release, folder)
        iterator = object_result(
            stack,
            folder,
            3,
            ctypes.c_void_p,
            None,
            ctypes.POINTER(GUID),
            ctypes.byref(_ENUM_HANDLER),
            ctypes.POINTER(GUID),
            ctypes.byref(_ENUM_ITEMS),
        )
        rows: dict[str, ApplicationInfo] = {}
        for _ in range(10000):
            with ExitStack() as item_stack:
                item, count = ctypes.c_void_p(), ctypes.c_uint32()
                try:
                    status = method(
                        iterator,
                        3,
                        HRESULT,
                        ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(ctypes.c_uint32),
                    )(iterator, 1, ctypes.byref(item), ctypes.byref(count))
                    if status < 0:
                        raise OSError(
                            status & 0xFFFFFFFF, "Could not enumerate applications"
                        )
                finally:
                    if item.value:
                        item_stack.callback(release, item)
                if not count.value:
                    return tuple(rows.values())
                if not item.value:
                    raise OSError("Application folder returned a missing item")
                row = _record(ole, item_stack, item)
                if row is not None:
                    rows.setdefault(row.desktop_id, row)
        raise OSError("Application folder exceeds the enumeration limit")


def read_application_metadata(
    ids: tuple[str, ...],
    *,
    registrations: tuple[ApplicationInfo, ...] = (),
    cancelled: Callable[[], bool] = lambda: False,
    exporter: WindowsApplicationExport | None = None,
) -> ApplicationMetadata:
    if cancelled():
        return ApplicationMetadata()
    try:
        applications = read_shell_applications()
    except OSError as exc:
        log.debug("Could not read the native application catalog: %s", exc)
        applications = None
    if cancelled():
        return ApplicationMetadata()
    if applications is not None:
        combined = {app.desktop_id: app for app in applications}
        for app in registrations:
            previous = combined.get(app.desktop_id)
            combined[app.desktop_id] = (
                replace(
                    previous,
                    name=app.name,
                    executable_path=app.executable_path,
                    launch_target=app.launch_target,
                    working_directory=app.working_directory,
                )
                if previous is not None
                else app
            )
        applications = tuple(combined.values())
    all_ids = set(ids)
    if applications is not None:
        all_ids.update(app.desktop_id for app in applications)
    hidden = hidden_application_ids(all_ids)
    if cancelled():
        return ApplicationMetadata()
    if applications is not None:
        from docking.platform.applications.implementations.windows.export import (
            WindowsApplicationExport,
        )

        visible = tuple(app for app in applications if app.desktop_id not in hidden)
        identifiers = frozenset(
            app.desktop_id
            for app in visible
            if app.launch_target.startswith("shell:AppsFolder\\")
            and "!" not in app.desktop_id
        )
        try:
            sources = read_shortcut_sources(identifiers, cancelled=cancelled)
        except OSError as exc:
            log.debug("Could not read application shortcut sources: %s", exc)
            sources = {}
        if cancelled():
            return ApplicationMetadata()
        if exporter is None:
            exporter = WindowsApplicationExport()
        prepared = exporter.prepare_documents(visible, sources, cancelled=cancelled)
        if cancelled():
            return ApplicationMetadata()
        applications = exporter.prepare(prepared, cancelled=cancelled)
    return ApplicationMetadata(
        hidden_ids=hidden,
        applications=applications,
    )
