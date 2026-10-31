"""Resolve requested Windows associations on the existing metadata worker."""

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.platform.backends.windows.associations import directory_association

from ..associations import (
    AssociationKey,
    AssociationRead,
    AssociationReads,
    AssociationStatus,
)


def read_associations(
    keys: tuple[AssociationKey, ...], cancelled: Callable[[], bool]
) -> AssociationReads:
    results = []
    for key in keys:
        if cancelled():
            break
        try:
            result = _read(key)
        except (OSError, GLib.Error):
            result = AssociationRead(AssociationStatus.UNAVAILABLE)
        results.append((key, result))
    return tuple(results)


def _read(key: AssociationKey) -> AssociationRead:
    value = key.content_type.lower()
    if not value or "\0" in value:
        return AssociationRead(AssociationStatus.UNAVAILABLE)
    if value == "inode/directory":
        if key.lookup == "name":
            return AssociationRead(
                AssociationStatus.READY, name=directory_association(name=True)
            )
        if key.lookup == "default":
            identifier = directory_association(name=False)
            return AssociationRead(
                AssociationStatus.READY
                if identifier
                else AssociationStatus.UNAVAILABLE,
                application_id=identifier or "",
            )
        return AssociationRead(AssociationStatus.UNAVAILABLE)
    if value.startswith("x-scheme-handler/"):
        if key.lookup not in ("default", "name"):
            return AssociationRead(AssociationStatus.UNAVAILABLE)
        scheme = value.removeprefix("x-scheme-handler/")
        if (
            not scheme
            or not scheme[0].isalpha()
            or any(
                not (
                    character.isascii() and (character.isalnum() or character in "+-.")
                )
                for character in scheme
            )
        ):
            return AssociationRead(AssociationStatus.UNAVAILABLE)
        handle = Gio.AppInfo.get_default_for_uri_scheme(scheme)
    else:
        native_type = (
            value if value.startswith(".") else Gio.content_type_from_mime_type(value)
        )
        if not native_type or native_type == "*":
            return AssociationRead(AssociationStatus.UNAVAILABLE)
        if key.lookup == "recommended":
            return AssociationRead(
                AssociationStatus.READY,
                tuple(Gio.AppInfo.get_recommended_for_type(native_type) or ()),
            )
        if key.lookup == "all":
            return AssociationRead(
                AssociationStatus.READY,
                tuple(Gio.AppInfo.get_all_for_type(native_type) or ()),
            )
        handle = Gio.AppInfo.get_default_for_type(native_type, False)
    if key.lookup == "name":
        return AssociationRead(
            AssociationStatus.READY,
            name=handle.get_display_name() or None if handle is not None else None,
        )
    return AssociationRead(
        AssociationStatus.READY, (handle,) if handle is not None else ()
    )
