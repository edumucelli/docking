"""Tests for main-thread application listing presentation helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from gi.repository import Gio

from docking.platform.applications.listing import (
    activate_listing,
    gicon_from_icon_name,
    listing_categories,
    listing_desktop_file_uri,
    listing_desktop_id,
    listing_gicon,
    listing_icon_name,
    listing_key,
    visible_listings,
)
from docking.platform.applications.registry import UnidentifiedApplicationListing
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


def _application(
    *,
    desktop_file: Path | None = None,
    declared_icon: str = "",
    has_gio_source: bool = False,
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id="org.example.Host.desktop",
        name="Host Tool",
        declared_icon=declared_icon,
        wm_class="org.example.Host",
        exec_line="host-tool %U",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.HOST,
        desktop_file=desktop_file,
        executable_path=None,
        aliases=("org.example.host",),
        visible=True,
        has_gio_source=has_gio_source,
        categories=("Development", "IDE"),
        categories_raw="Development;IDE;",
    )


def _unidentified(
    *, desktop_file: Path | None = None
) -> UnidentifiedApplicationListing:
    return UnidentifiedApplicationListing(
        listing_key="gio-idless:17",
        name="ID-less Tool",
        categories="Utility;",
        icon_name="applications-utilities",
        desktop_file=desktop_file,
        exec_line="idless-tool %U",
        description="Run the ID-less tool",
        generic_name="Utility Tool",
    )


def test_visible_listings_preserves_both_registry_snapshot_orders() -> None:
    application = _application()
    unidentified = _unidentified()
    registry = SimpleNamespace(
        snapshot=lambda: (application,),
        unidentified_snapshot=lambda: (unidentified,),
    )

    assert visible_listings(registry) == (application, unidentified)


def test_listing_facts_distinguish_canonical_and_idless_records() -> None:
    application = _application(declared_icon="host-tool")
    unidentified = _unidentified()

    assert application.name == "Host Tool"
    assert listing_categories(application) == "Development;IDE;"
    assert listing_icon_name(application) == "host-tool"
    assert listing_desktop_id(application) == "org.example.Host.desktop"
    assert listing_key(application) is None

    assert unidentified.name == "ID-less Tool"
    assert listing_categories(unidentified) == "Utility;"
    assert listing_icon_name(unidentified) == "applications-utilities"
    assert listing_desktop_id(unidentified) is None
    assert listing_key(unidentified) == "gio-idless:17"
    assert unidentified.exec_line == "idless-tool %U"
    assert unidentified.description == "Run the ID-less tool"
    assert unidentified.generic_name == "Utility Tool"
    assert application.exec_line == "host-tool %U"


def test_activate_listing_uses_exactly_one_launch_mechanism() -> None:
    launcher = MagicMock()
    launcher.launch.return_value = True
    launcher.launch_listing.return_value = True

    assert activate_listing(launcher, _application())
    launcher.launch.assert_called_once_with("org.example.Host.desktop")
    launcher.launch_listing.assert_not_called()

    launcher.reset_mock()

    assert activate_listing(launcher, _unidentified())
    launcher.launch.assert_not_called()
    launcher.launch_listing.assert_called_once_with("gio-idless:17")


def test_listing_gicon_prefers_private_registry_handles_for_both_forms() -> None:
    application = _application(declared_icon="fallback")
    unidentified = _unidentified()
    canonical_icon = Gio.ThemedIcon.new("canonical-icon")
    idless_icon = Gio.ThemedIcon.new("idless-icon")
    canonical_handle = MagicMock()
    canonical_handle.get_icon.return_value = canonical_icon
    idless_handle = MagicMock()
    idless_handle.get_icon.return_value = idless_icon
    registry = SimpleNamespace(
        _gio_handle_for=lambda desktop_id: (
            canonical_handle if desktop_id == "org.example.Host.desktop" else None
        ),
        _gio_handle_for_unidentified=lambda key: (
            idless_handle if key == "gio-idless:17" else None
        ),
    )

    assert listing_gicon(registry, application) is canonical_icon
    assert listing_gicon(registry, unidentified) is idless_icon


def test_listing_gicon_falls_back_to_themed_and_file_icons(tmp_path: Path) -> None:
    icon_file = tmp_path / "tool.svg"
    icon_file.write_text("<svg/>", encoding="utf-8")

    themed = gicon_from_icon_name("applications-utilities")
    absolute_file = gicon_from_icon_name(str(icon_file))
    uri_file = gicon_from_icon_name(icon_file.as_uri())

    assert isinstance(themed, Gio.ThemedIcon)
    assert themed.to_string() == "applications-utilities"
    assert isinstance(absolute_file, Gio.FileIcon)
    assert absolute_file.get_file().get_path() == str(icon_file)
    assert isinstance(uri_file, Gio.FileIcon)
    assert uri_file.get_file().get_uri() == icon_file.as_uri()
    assert gicon_from_icon_name("") is None


def test_desktop_file_uri_uses_exact_recorded_path_without_lookup(
    tmp_path: Path,
) -> None:
    recorded = tmp_path / "source" / ".." / "winner.desktop"
    application = _application(desktop_file=recorded)
    unidentified = _unidentified(desktop_file=recorded)

    assert listing_desktop_file_uri(application) == recorded.as_uri()
    assert listing_desktop_file_uri(unidentified) == recorded.as_uri()
    assert listing_desktop_file_uri(_application()) is None
