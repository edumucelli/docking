"""Open selected files with an exact Windows application, never its default."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform.applications.backend import LaunchOutcome
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    query,
    release,
)
from docking.platform.native.windows.dataobject import cached_data_object
from docking.platform.native.windows.ffi import bind
from docking.platform.native.windows.launching import launch_shortcut
from docking.platform.native.windows.selection import shell_selection_formats

log = get_logger("windows.documents")
_APPS_FOLDER = "shell:AppsFolder\\"
_PROTOCOLS = frozenset({"http", "https", "mailto"})
_CONTRACT_NOT_REGISTERED = 0x80270254
_NO_INTERFACE = 0x80004002
_APP_ID = GUID.parse("36db0196-9665-46d1-9ba7-d3709eecf9ed")
_ENUM_HANDLERS = GUID.parse("973810ae-9599-4b88-9e4d-6ee98c9552da")


def _check(
    status: int, operation: str = "Selected application file activation"
) -> None:
    if status < 0:
        raise OSError(status & 0xFFFFFFFF, f"{operation} failed")


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
        try:
            status = parse(target, None, ctypes.byref(identifier), 0, None)
        finally:
            if identifier.value:
                stack.callback(free, identifier)
        _check(status, "SHParseDisplayName")
        if not identifier.value:
            raise OSError("Selected file has no Shell item")
        identifiers[index] = identifier
    items = ctypes.c_void_p()
    try:
        status = bind(
            shell,
            "SHCreateShellItemArrayFromIDLists",
            HRESULT,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        )(len(targets), identifiers, ctypes.byref(items))
    finally:
        if items.value:
            stack.callback(release, items)
    _check(status, "SHCreateShellItemArrayFromIDLists")
    if not items.value:
        raise OSError("Selected files have no Shell item array")
    return items


def _handler_app_id(stack: ExitStack, ole, handler) -> str:
    try:
        identity = query(stack, handler, _APP_ID)
    except OSError as exc:
        if exc.errno == _NO_INTERFACE:
            return ""
        raise
    if not identity.value:
        raise OSError("Missing association application identity")
    value = ctypes.c_void_p()
    try:
        status = method(identity, 4, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            identity, ctypes.byref(value)
        )
        # Shell handlers with a system-assigned identity can expose this
        # interface but fail GetAppID because no explicit AppID was assigned.
        if status & 0xFFFFFFFF == 0x80004005:  # E_FAIL
            return ""
        _check(status, "IObjectWithAppUserModelID.GetAppID")
        return ctypes.wstring_at(value) if status == 0 and value.value else ""
    finally:
        if value.value:
            bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)(value)


def _registered_handler(
    stack: ExitStack, ole, application_id: str, association: str, files: bool
) -> ctypes.c_void_p | None:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    iterator = ctypes.c_void_p()
    stack.callback(release, iterator)
    if files:
        status = bind(
            shell,
            "SHAssocEnumHandlers",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        )(association, 0, ctypes.byref(iterator))
    else:
        status = bind(
            shell,
            "SHAssocEnumHandlersForProtocolByApplication",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(association, ctypes.byref(_ENUM_HANDLERS), ctypes.byref(iterator))
    _check(status, "Association handler enumeration")
    if not iterator.value:
        raise OSError("Missing association handler enumeration")
    selected = None
    for _ in range(256):
        with ExitStack() as entry:
            handler, count = ctypes.c_void_p(), ctypes.c_uint32()
            entry.callback(release, handler)
            status = method(
                iterator,
                3,
                HRESULT,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_uint32),
            )(iterator, 1, ctypes.byref(handler), ctypes.byref(count))
            _check(status, "IEnumAssocHandlers.Next")
            if status == 1 and count.value == 0 and not handler.value:
                return selected
            if status != 0 or count.value != 1 or not handler.value:
                raise OSError("Invalid association handler result")
            if _handler_app_id(entry, ole, handler) != application_id:
                continue
            if selected is not None:
                raise OSError("Ambiguous selected application handler")
            selected = handler
            stack.enter_context(entry.pop_all())
    raise OSError("Association handler enumeration exceeds the limit")


def _invoke_registered(
    application_id: str, targets: tuple[tuple[str, bool], ...], files: bool
) -> LaunchOutcome:
    with apartment() as ole, ExitStack() as stack:
        associations = (
            tuple(
                sorted(
                    {
                        PureWindowsPath(target).suffix.lower()
                        for target, _ in targets
                        if len(PureWindowsPath(target).suffix) > 1
                    }
                )
            )
            if files
            else (urlsplit(targets[0][0]).scheme,)
        )
        data = None
        for association in associations:
            with ExitStack() as candidate:
                handler = _registered_handler(
                    candidate, ole, application_id, association, files
                )
                if handler is None:
                    continue
                if data is None:
                    formats = shell_selection_formats(
                        tuple(target for target, _file in targets), files=files
                    )
                    data = stack.enter_context(cached_data_object(formats))
                invoker = ctypes.c_void_p()
                candidate.callback(release, invoker)
                status = method(
                    handler,
                    9,
                    HRESULT,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                )(handler, data, ctypes.byref(invoker))
                _check(status, "IAssocHandler.CreateInvoker")
                if status != 0:
                    continue
                if not invoker.value:
                    raise OSError("Missing selected application invoker")
                supported = method(invoker, 3, HRESULT)(invoker)
                _check(supported, "IAssocHandlerInvoker.SupportsSelection")
                if supported != 0:
                    continue
                status = method(invoker, 4, HRESULT)(invoker)
                _check(status, "IAssocHandlerInvoker.Invoke")
                if status != 0:
                    log.debug(
                        "Selected handler did not start the selection: %#x", status
                    )
                return LaunchOutcome(status == 0)
        log.debug("No selected application handler supports the complete selection")
        return LaunchOutcome(False)


def _activate_packaged(
    application_id: str, targets: tuple[tuple[str, bool], ...]
) -> LaunchOutcome:
    files = all(is_file for _target, is_file in targets)
    if not files and (len(targets) != 1 or targets[0][1]):
        return LaunchOutcome(False)
    with apartment() as ole, ExitStack() as stack:
        items = _items(stack, ole, tuple(target for target, _is_file in targets))
        manager = ctypes.c_void_p()
        stack.callback(release, manager)
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
        _check(status, "CoCreateInstance(ApplicationActivationManager)")
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
        status = method(manager, 4 if files else 5, HRESULT, *kinds)(
            manager, *arguments
        )
        if status & 0xFFFFFFFF != _CONTRACT_NOT_REGISTERED:
            _check(status)
            # The activation manager returns a PID, not an owned process instance.
            # Reopening it would create false launch provenance after PID reuse.
            return LaunchOutcome(True)
    # Packaged desktop apps can expose a Shell association instead of the UWP
    # activation contract. Preserve the full selected identity on this route.
    return _invoke_registered(application_id, targets, files)


def launch_application_uris(target: str, uris: Sequence[str]) -> LaunchOutcome | None:
    """Return None only when the existing Gio path should handle this target."""
    if "\0" in target:
        return LaunchOutcome(False)
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
