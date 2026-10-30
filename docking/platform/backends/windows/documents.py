"""Open selected files with an exact Windows application, never its default."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform.applications.launched_process import LaunchOutcome

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind
from .launching import launch_shortcut

log = get_logger("windows.documents")
_APPS_FOLDER = "shell:AppsFolder\\"
_PROTOCOLS = frozenset({"http", "https", "mailto"})


def _check(status: int) -> None:
    if status < 0:
        raise OSError(
            status & 0xFFFFFFFF, "Selected application file activation failed"
        )


def _targets(uris: Sequence[str]) -> tuple[tuple[str, bool], ...] | None:
    targets = []
    for uri in uris:
        if not uri or "\0" in uri:
            return None
        path = local_path_from_uri_or_path(uri)
        if path is not None and PureWindowsPath(str(path)).is_absolute():
            # MSYS2 pathlib can retain forward separators even for Windows paths.
            targets.append((str(path).replace("/", "\\"), True))
            continue
        try:
            parsed = urlsplit(uri)
        except ValueError:
            return None
        if parsed.scheme not in _PROTOCOLS:
            return None
        targets.append((uri, False))
    return tuple(targets) if targets else None


def _items(stack: ExitStack, ole, targets: tuple[str, ...]) -> ctypes.c_void_p:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    parse = bind(
        shell,
        "SHParseDisplayName",
        HRESULT,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    free = bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)
    identifiers = (ctypes.c_void_p * len(targets))()
    for index, target in enumerate(targets):
        identifier = ctypes.c_void_p()
        status = parse(target, None, ctypes.byref(identifier), 0, None)
        if identifier.value:
            stack.callback(free, identifier)
        _check(status)
        if not identifier.value:
            raise OSError("Selected file has no Shell item")
        identifiers[index] = identifier
    items = ctypes.c_void_p()
    status = bind(
        shell,
        "SHCreateShellItemArrayFromIDLists",
        HRESULT,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )(len(targets), identifiers, ctypes.byref(items))
    if items.value:
        stack.callback(release, items)
    _check(status)
    if not items.value:
        raise OSError("Selected files have no Shell item array")
    return items


def _activate_packaged(
    application_id: str, targets: tuple[tuple[str, bool], ...]
) -> LaunchOutcome:
    files = all(is_file for _target, is_file in targets)
    if not files and (len(targets) != 1 or targets[0][1]):
        return LaunchOutcome(False)
    with apartment() as ole, ExitStack() as stack:
        items = _items(stack, ole, tuple(target for target, _is_file in targets))
        manager = ctypes.c_void_p()
        status = bind(
            ole,
            "CoCreateInstance",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(
            ctypes.byref(GUID.parse("45ba127d-10a8-46ea-8ab7-56ea9078943c")),
            None,
            4,
            ctypes.byref(GUID.parse("2e941141-7f97-4756-ba1d-9decde894a3d")),
            ctypes.byref(manager),
        )
        if manager.value:
            stack.callback(release, manager)
        _check(status)
        if not manager.value:
            raise OSError("Missing application activation manager")
        bind(
            ole, "CoAllowSetForegroundWindow", HRESULT, ctypes.c_void_p, ctypes.c_void_p
        )(manager, None)
        pid = ctypes.c_uint32()
        kinds = (
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            *((ctypes.c_wchar_p,) if files else ()),
            ctypes.POINTER(ctypes.c_uint32),
        )
        arguments = (
            application_id,
            items,
            *(("open",) if files else ()),
            ctypes.byref(pid),
        )
        _check(method(manager, 4 if files else 5, HRESULT, *kinds)(manager, *arguments))
        # The activation manager returns a PID, not an owned process instance.
        # Reopening it would create false launch provenance after PID reuse.
        return LaunchOutcome(True)


def launch_application_uris(target: str, uris: Sequence[str]) -> LaunchOutcome | None:
    """Return None only when the existing Gio path should handle this target."""
    link = PureWindowsPath(target)
    packaged = target.startswith(_APPS_FOLDER) and "!" in target[len(_APPS_FOLDER) :]
    if not packaged and not (link.is_absolute() and link.suffix.lower() == ".lnk"):
        return None
    targets = _targets(uris)
    if targets is None:
        return LaunchOutcome(False)
    try:
        if packaged:
            return _activate_packaged(target[len(_APPS_FOLDER) :], targets)
        return launch_shortcut(
            target, arguments=tuple(value for value, _file in targets)
        )
    except OSError as exc:
        log.warning(
            "Could not open files with the selected Windows application: %s", exc
        )
        return LaunchOutcome(False)
