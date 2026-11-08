"""Resolve requested Windows associations on the existing metadata worker."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.platform.applications import handle_adapters as handles
from docking.platform.applications.backend import ApplicationAssociationBackend
from docking.platform.applications.metadata import ApplicationMetadataLookup
from docking.platform.applications.types import (
    ApplicationInfo,
    TransientApplicationInfo,
)

if TYPE_CHECKING:
    from docking.platform.applications.registry import ApplicationRegistry

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.platform.applications.associations import (
    AssociationKey,
    AssociationRead,
    AssociationReads,
    AssociationStatus,
)
from docking.platform.native.windows.associations import directory_association


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


class WindowsApplicationAssociationBackend(ApplicationAssociationBackend):
    """Consume the catalog owner's accepted facts; never read native APIs in GTK."""

    def __init__(self, lookup: ApplicationMetadataLookup) -> None:
        self._lookup = lookup

    @property
    def revision(self) -> int:
        return self._lookup.association_revision

    def reads(self, keys: tuple[AssociationKey, ...]) -> AssociationReads:
        return self._lookup.association_reads(keys)

    def read(self, key: AssociationKey) -> AssociationRead:
        return self.reads((key,))[0][1]

    def visible(self, handle: object) -> bool:
        return (
            handles.desktop_id(handle) not in self._lookup.hidden_ids
            and handles.safe_call(handle, "should_show") is not False
            and not handles.safe_call(handle, "get_is_hidden")
            and not handles.safe_call(handle, "get_nodisplay")
        )

    def association_only(self, application: ApplicationInfo) -> bool:
        return (
            self._lookup.applications is not None
            and application.desktop_id not in self._lookup.hidden_ids
        )

    def registered(
        self, registry: ApplicationRegistry, handle: object
    ) -> ApplicationInfo | None:
        identifier = handles.desktop_id(handle)
        return (
            registry.get(identifier)
            if identifier not in self._lookup.hidden_ids
            else None
        )

    def listing(
        self, registry: ApplicationRegistry, handle: object | None
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        if handle is None or not self.visible(handle):
            return None
        registered = self.registered(registry, handle)
        if registered is not None:
            if registered.visible:
                return registered
            if not self.association_only(registered):
                return None
        return registry._retain_content_listing(handle)

    def transient(self, app_info: object, listing_key: str) -> TransientApplicationInfo:
        listing = handles.transient_from_gio(
            app_info=app_info, listing_key=listing_key, require_visible=False
        )
        assert listing is not None
        return listing
