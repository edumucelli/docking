"""Synchronous Unix association reads and Gio handle conversion."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

from docking.log import get_logger, with_context
from docking.platform.applications.backend import ApplicationAssociationBackend

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio

from ... import handle_adapters as handles
from ...associations import AssociationKey, AssociationRead, AssociationStatus

if TYPE_CHECKING:
    from ...registry import ApplicationRegistry
from ...types import ApplicationInfo, TransientApplicationInfo
from . import discovery

log = with_context(get_logger("application_registry"))


def _content_handler_is_visible(app_info: object) -> bool:
    return (
        handles.safe_call(app_info, "should_show") is not False
        and not bool(handles.safe_call(app_info, "get_is_hidden"))
        and not bool(handles.safe_call(app_info, "get_nodisplay"))
    )


class UnixApplicationAssociationBackend(ApplicationAssociationBackend):
    @property
    def revision(self) -> int:
        return 0

    def reads(
        self, keys: tuple[AssociationKey, ...]
    ) -> Iterable[tuple[AssociationKey, AssociationRead]]:
        return ((key, self._read(key)) for key in keys)

    def association_only(self, application: ApplicationInfo) -> bool:
        return False

    visible = staticmethod(_content_handler_is_visible)

    def _read(self, key: AssociationKey) -> AssociationRead:
        try:
            if key.lookup in {"default", "name"}:
                handle = Gio.AppInfo.get_default_for_type(key.content_type, False)
                if key.lookup == "name":
                    return AssociationRead(
                        AssociationStatus.READY,
                        name=handle.get_display_name() or None
                        if handle is not None
                        else None,
                    )
                handles = (handle,) if handle is not None else ()
            elif key.lookup == "recommended":
                handles = tuple(
                    Gio.AppInfo.get_recommended_for_type(key.content_type) or ()
                )
            else:
                handles = tuple(Gio.AppInfo.get_all_for_type(key.content_type) or ())
            return AssociationRead(AssociationStatus.READY, handles=handles)
        except Exception as exc:
            message = log.bind(action="read_association", content_type=key.content_type)
            if key.lookup == "name":
                message.warning("Failed to resolve default directory app: %s", exc)
            else:
                message.debug("Failed to read application association: %s", exc)
            return AssociationRead(AssociationStatus.UNAVAILABLE)

    def registered(
        self,
        registry: ApplicationRegistry,
        app_info: object,
    ) -> ApplicationInfo | None:
        desktop_id = handles.desktop_id(app_info)
        if desktop_id:
            application = registry.get(desktop_id)
            if application is not None:
                return application

        filename = handles.source_text(handles.safe_call(app_info, "get_filename"))
        if not filename:
            return None
        path = Path(filename).expanduser()
        return registry.resolve_by_desktop_file(path)

    def listing(
        self,
        registry: ApplicationRegistry,
        app_info: object | None,
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        if app_info is None or not _content_handler_is_visible(app_info):
            return None
        registered = self.registered(registry, app_info)
        if registered is not None:
            return registered if registered.visible else None
        return registry._retain_content_listing(app_info)

    def transient(self, app_info: object, listing_key: str) -> TransientApplicationInfo:
        listing = discovery.transient_from_gio(
            app_info=app_info, listing_key=listing_key, require_visible=False
        )
        if listing is None:
            raise AssertionError("unfiltered Gio listing unexpectedly missing")
        return listing
