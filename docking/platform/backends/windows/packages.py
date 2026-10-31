"""Current-user package metadata used by application discovery workers."""

from __future__ import annotations

import ctypes
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

from docking.log import get_logger

from .ffi import bind

log = get_logger("windows.packages")
_U32 = ctypes.c_uint32
_HEAD = 0x10
_MORE_DATA = 122


class WindowsPackagePaths:
    """Resolve registered head packages, excluding resources and frameworks."""

    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._find = bind(
            kernel,
            "FindPackagesByPackageFamily",
            ctypes.c_int32,
            ctypes.c_wchar_p,
            _U32,
            ctypes.POINTER(_U32),
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(_U32),
            ctypes.c_wchar_p,
            ctypes.POINTER(_U32),
        )
        self._path = bind(
            kernel,
            "GetPackagePathByFullName",
            ctypes.c_int32,
            ctypes.c_wchar_p,
            ctypes.POINTER(_U32),
            ctypes.c_wchar_p,
        )

    def paths(self, family: str) -> tuple[Path, ...]:
        for _ in range(3):
            count, length = _U32(), _U32()
            status = self._find(
                family,
                _HEAD,
                ctypes.byref(count),
                None,
                ctypes.byref(length),
                None,
                None,
            )
            if status not in (0, _MORE_DATA):
                raise OSError(status, "Could not query registered package names")
            if not count.value:
                return ()
            names = (ctypes.c_wchar_p * count.value)()
            buffer = ctypes.create_unicode_buffer(length.value)
            status = self._find(
                family,
                _HEAD,
                ctypes.byref(count),
                names,
                ctypes.byref(length),
                buffer,
                None,
            )
            if status == _MORE_DATA:
                continue
            if status:
                raise OSError(status, "Could not read registered package names")
            return tuple(self._package_path(name) for name in names if name)
        raise OSError("Package registration kept changing during discovery")

    def _package_path(self, name: str) -> Path:
        for _ in range(3):
            length = _U32()
            status = self._path(name, ctypes.byref(length), None)
            if status not in (0, _MORE_DATA):
                raise OSError(status, "Could not query package path")
            buffer = ctypes.create_unicode_buffer(length.value)
            status = self._path(name, ctypes.byref(length), buffer)
            if status == _MORE_DATA:
                continue
            if status:
                raise OSError(status, "Could not read package path")
            if not buffer.value:
                raise OSError("Registered package has no installation path")
            return Path(buffer.value)
        raise OSError("Package path kept changing during discovery")


def hidden_manifest_entries(data: bytes) -> frozenset[str]:
    """Keep only explicit AppListEntry=none declarations, regardless of namespace."""
    root = ElementTree.fromstring(data)
    return frozenset(
        app.attrib["Id"]
        for app in root.findall("{*}Applications/{*}Application")
        if app.attrib.get("Id")
        and any(
            visual.attrib.get("AppListEntry") == "none"
            for visual in app.findall("{*}VisualElements")
        )
    )


def hidden_application_ids(application_ids: Iterable[str]) -> frozenset[str]:
    """Read explicit hidden entries off GTK; unknown metadata keeps Gio's decision."""
    requested = frozenset(application_ids)
    families = {app_id.split("!", 1)[0] for app_id in requested if "!" in app_id}
    if not families:
        return frozenset[str]()
    packages = WindowsPackagePaths()
    hidden: set[str] = set()
    for family in families:
        try:
            for path in packages.paths(family):
                # Limit the amount of manifest metadata retained and parsed.
                with (path / "AppxManifest.xml").open("rb") as stream:
                    data = stream.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise OSError("Package manifest exceeds metadata size limit")
                hidden.update(
                    f"{family}!{entry}" for entry in hidden_manifest_entries(data)
                )
        except (OSError, ElementTree.ParseError) as exc:
            log.debug("Could not read package visibility: %s", exc)
    return frozenset(hidden & requested)
