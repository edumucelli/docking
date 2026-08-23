# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Compatibility adapters for legacy application-listing consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from docking.platform import launcher as launcher_facade
from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.listing import (
    ApplicationListing,
    gicon_from_icon_name,
    listing_categories,
    listing_desktop_file_uri,
    listing_desktop_id,
    listing_gicon,
    listing_icon_name,
    listing_key,
    visible_listings,
)
from docking.platform.applications.registry import (
    ApplicationRegistry,
    UnidentifiedApplicationListing,
)

launch_desktop_id = launcher_facade.launch


@dataclass(frozen=True, slots=True)
class ApplicationEntry:
    """Legacy ``Gio.AppInfo``-shaped view over one registry listing."""

    desktop_id: str
    name: str
    categories: str
    icon_name: str
    app_info: object | None = None
    desktop_file: Path | None = None
    listing_key: str | None = None
    _listing: ApplicationListing | None = None
    _registry: ApplicationRegistry | None = None
    _launcher: ApplicationLauncher | None = None

    def get_id(self) -> str:
        return self.desktop_id

    def get_display_name(self) -> str:
        return self.name

    def get_categories(self) -> str:
        return self.categories

    def get_icon(self) -> object | None:
        if self._listing is not None:
            return listing_gicon(self._registry, self._listing)
        getter = getattr(self.app_info, "get_icon", None)
        if callable(getter):
            icon = getter()
            if icon is not None:
                return icon
        return gicon_from_icon_name(self.icon_name)

    def desktop_file_uri(self) -> str | None:
        """Return the ``.desktop`` file URI used for drag-to-pin operations."""
        if self._listing is not None:
            return listing_desktop_file_uri(self._listing)
        path = self.desktop_file
        filename: object | None = None
        if self.app_info is not None:
            try:
                getter = getattr(self.app_info, "get_filename", None)
                if callable(getter):
                    filename = getter()
            except (AttributeError, TypeError):
                filename = None
        if filename:
            path = Path(str(filename)).expanduser()
        elif path is None and self.desktop_id:
            path = desktop_entries.find_desktop_file(self.desktop_id)
        if path is None:
            return None
        try:
            return path.expanduser().resolve().as_uri()
        except (OSError, RuntimeError, ValueError):
            return None

    def launch(
        self,
        files: list[object] | None,
        context: object | None,
    ) -> None:
        launcher = None
        if self._listing is not None or not self.desktop_id:
            launcher = (
                self._launcher or launcher_facade.get_configured_application_launcher()
            )
        if launcher is not None:
            if self.desktop_id:
                launcher.launch(self.desktop_id)
                return
            if self.listing_key is not None:
                launcher.launch_listing(self.listing_key)
                return

        if self.desktop_id:
            launch_desktop_id(desktop_id=self.desktop_id)
            return

        gio_launch = getattr(self.app_info, "launch", None)
        if callable(gio_launch):
            gio_launch(files, context)


def all_desktop_app_infos(
    registry: ApplicationRegistry | None = None,
) -> list[ApplicationEntry]:
    """Adapt canonical listings, discovering once for standalone legacy callers."""
    launcher = launcher_facade.get_configured_application_launcher()
    if registry is None and launcher is not None:
        registry = launcher.registry
    if registry is None:
        registry = ApplicationRegistry()
        registry.refresh()
    return [
        _application_entry(
            listing=listing,
            registry=registry,
            launcher=launcher,
        )
        for listing in visible_listings(registry)
    ]


def _application_entry(
    *,
    listing: ApplicationListing,
    registry: ApplicationRegistry,
    launcher: ApplicationLauncher | None,
) -> ApplicationEntry:
    if isinstance(listing, UnidentifiedApplicationListing):
        app_info = registry._gio_handle_for_unidentified(listing.listing_key)
    else:
        app_info = registry._gio_handle_for(listing.desktop_id)
    return ApplicationEntry(
        desktop_id=listing_desktop_id(listing) or "",
        name=listing.name,
        categories=listing_categories(listing),
        icon_name=listing_icon_name(listing),
        app_info=app_info,
        desktop_file=listing.desktop_file,
        listing_key=listing_key(listing),
        _listing=listing,
        _registry=registry,
        _launcher=launcher,
    )


__all__ = ["ApplicationEntry", "all_desktop_app_infos"]
