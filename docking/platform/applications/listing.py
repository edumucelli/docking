"""Main-thread presentation helpers for visible application listings."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from .registry import ApplicationRegistry, UnidentifiedApplicationListing
from .types import ApplicationInfo

ApplicationListing = ApplicationInfo | UnidentifiedApplicationListing


def visible_listings(
    registry: ApplicationRegistry | None,
) -> tuple[ApplicationListing, ...]:
    """Return the registry's visible and ID-less presentation snapshots."""
    if registry is None:
        return ()
    return (*registry.snapshot(), *registry.unidentified_snapshot())


def listing_name(listing: ApplicationListing) -> str:
    """Return the recorded display name."""
    return listing.name


def listing_categories(listing: ApplicationListing) -> str:
    """Return the source-faithful freedesktop category field."""
    if isinstance(listing, ApplicationInfo):
        return listing.categories_raw
    return listing.categories


def listing_icon_name(listing: ApplicationListing) -> str:
    """Return the recorded icon fact without adding a generic fallback."""
    if isinstance(listing, ApplicationInfo):
        return listing.declared_icon
    return listing.icon_name


def listing_desktop_id(listing: ApplicationListing) -> str | None:
    """Return a canonical desktop ID, or ``None`` for an ID-less listing."""
    if isinstance(listing, ApplicationInfo):
        return listing.desktop_id
    return None


def listing_key(listing: ApplicationListing) -> str | None:
    """Return the opaque key used for a Gio-backed transient listing."""
    if isinstance(listing, UnidentifiedApplicationListing):
        return listing.listing_key
    return None


def listing_exec_line(listing: ApplicationListing) -> str:
    """Return the source-faithful desktop command line."""
    return listing.exec_line


def listing_description(listing: ApplicationListing) -> str:
    """Return the source-faithful application description."""
    return listing.description


def listing_generic_name(listing: ApplicationListing) -> str:
    """Return the source-faithful generic application name."""
    return listing.generic_name


def listing_gicon(
    registry: ApplicationRegistry | None,
    listing: ApplicationListing,
) -> Gio.Icon | None:
    """Return the retained Gio icon, with file/themed fact fallbacks."""
    handle = _gio_handle(registry=registry, listing=listing)
    if handle is not None:
        getter = getattr(handle, "get_icon", None)
        if callable(getter):
            try:
                icon = getter()
            except Exception:
                icon = None
            if icon is not None:
                return icon
    return gicon_from_icon_name(listing_icon_name(listing))


def gicon_from_icon_name(icon_name: str) -> Gio.Icon | None:
    """Construct a file or themed GIcon from a recorded icon fact."""
    value = icon_name.strip()
    if not value:
        return None
    if value.startswith("file:"):
        return Gio.FileIcon.new(Gio.File.new_for_uri(value))
    if Path(value).is_absolute():
        return Gio.FileIcon.new(Gio.File.new_for_path(value))
    return Gio.ThemedIcon.new(value)


def listing_desktop_file_uri(listing: ApplicationListing) -> str | None:
    """Return the exact recorded desktop-file path as a file URI."""
    desktop_file = listing.desktop_file
    if desktop_file is None:
        return None
    try:
        return desktop_file.as_uri()
    except ValueError:
        return None


def _gio_handle(
    *,
    registry: ApplicationRegistry | None,
    listing: ApplicationListing,
) -> object | None:
    if registry is None:
        return None
    if isinstance(listing, ApplicationInfo):
        getter = getattr(registry, "_gio_handle_for", None)
        return getter(listing.desktop_id) if callable(getter) else None
    getter = getattr(registry, "_gio_handle_for_unidentified", None)
    return getter(listing.listing_key) if callable(getter) else None


__all__ = [
    "ApplicationListing",
    "gicon_from_icon_name",
    "listing_categories",
    "listing_description",
    "listing_desktop_file_uri",
    "listing_desktop_id",
    "listing_exec_line",
    "listing_generic_name",
    "listing_gicon",
    "listing_icon_name",
    "listing_key",
    "listing_name",
    "visible_listings",
]
