"""Parity tests for explicit application projections."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.projections import (
    DesktopActionProjection,
    IconDescriptor,
    dock_metadata,
    new_window_action,
    quicklist_actions,
    search_actions,
    search_icon,
    search_metadata,
    visible_listing,
)
from docking.platform.applications.types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


def _application(**changes) -> ApplicationInfo:
    base = ApplicationInfo(
        desktop_id="org.example.App.desktop",
        name="Example App",
        declared_icon="org.example.App",
        wm_class="ExampleApp",
        exec_line="example-app %U",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path("/tmp/org.example.App.desktop"),
        executable_path=None,
        aliases=("exampleapp",),
        visible=True,
        has_gio_source=True,
    )
    return replace(base, **changes)


def test_dock_and_listing_match_distinct_legacy_file_views(tmp_path):
    path = tmp_path / "org.example.NoIcon.desktop"
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=No Icon\n"
        "Exec=no-icon\n"
        "Categories=Utility;\n",
        encoding="utf-8",
    )
    legacy_dock = desktop_entries.desktop_info_from_file(
        desktop_id=path.name,
        path=path,
    )
    legacy_listing = desktop_entries.desktop_listing_from_file(
        desktop_id=path.name,
        path=path,
    )
    assert legacy_dock is not None
    assert legacy_listing is not None
    canonical = _application(
        desktop_id=path.name,
        name=legacy_dock.name,
        declared_icon=legacy_listing.icon_name,
        wm_class=legacy_dock.wm_class,
        exec_line=legacy_dock.exec_line,
        desktop_file=path,
        categories=("Utility",),
        categories_raw=legacy_listing.categories,
    )

    assert dock_metadata(canonical) == legacy_dock
    listing = visible_listing(canonical)
    assert listing is not None
    assert (
        listing.desktop_id,
        listing.name,
        listing.categories,
        listing.icon_name,
    ) == legacy_listing[:4]
    assert listing.desktop_file == path


def test_dock_projection_repairs_generated_entry_icon_without_changing_search(
    tmp_path,
):
    executable = tmp_path / "tool"
    executable.write_bytes(b"\x7fELF")
    icon = tmp_path / "tool.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    canonical = _application(
        origin=ApplicationOrigin.GENERATED,
        declared_icon="",
        executable_path=executable.resolve(),
    )

    assert dock_metadata(canonical).icon_name == str(icon.resolve())
    assert search_icon(canonical) == IconDescriptor("none", "")


def test_search_projection_normalizes_canonical_registry_metadata(tmp_path):
    path = tmp_path / "org.example.Writer.desktop"
    canonical = _application(
        desktop_id=path.name,
        name="  Café   Writer ",
        declared_icon="/opt/example/writer.svg",
        desktop_file=path,
        description="  Write and   edit documents. ",
        categories=("Office", "Utility"),
        categories_raw=" Office ;Utility;office;;",
        keywords=("Write", "Documents"),
        actions=(
            ApplicationAction(
                action_id="shared",
                name="Shared from Gio",
                sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
                file_exec_line="writer --file-shared",
            ),
            ApplicationAction(
                action_id="gio-only",
                name="Gio Only",
                sources=frozenset({ActionSource.GIO}),
            ),
            ApplicationAction(
                action_id="file-only",
                name="File Only",
                sources=frozenset({ActionSource.DESKTOP_FILE}),
                file_exec_line="writer --file-only",
            ),
        ),
    )

    projected = search_metadata(canonical)

    assert projected.desktop_id == path.name
    assert projected.name == "Café Writer"
    assert projected.normalized_name == "café writer"
    assert projected.categories == ("Office", "Utility")
    assert projected.icon == IconDescriptor("file", "/opt/example/writer.svg")
    assert projected.description == "Write and edit documents."
    assert projected.keywords == ("Write", "Documents")
    assert [(action.action_id, action.name) for action in projected.actions] == [
        ("shared", "Shared from Gio"),
        ("gio-only", "Gio Only"),
        ("file-only", "File Only"),
    ]


@pytest.mark.parametrize(
    ("gio_description", "file_metadata", "canonical_description"),
    [
        (
            "Gio Description",
            "Comment=File Comment\nGenericName=File Generic\n",
            "Gio Description",
        ),
        (
            "",
            "Comment=File Comment\nGenericName=File Generic\n",
            "File Comment",
        ),
        ("", "GenericName=File Generic\n", "File Generic"),
        ("", "", ""),
    ],
)
def test_search_description_projection_uses_canonical_fallback_result(
    tmp_path,
    gio_description,
    file_metadata,
    canonical_description,
):
    path = tmp_path / "description.desktop"
    canonical = _application(
        desktop_id=path.name,
        name="Description",
        declared_icon="",
        desktop_file=path,
        description=canonical_description,
        generic_name="Gio Generic Must Not Be Search Description",
    )

    del gio_description, file_metadata
    assert search_metadata(canonical).description == canonical_description


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", IconDescriptor("none", "")),
        ("org.example.App", IconDescriptor("themed", "org.example.App")),
        ("/opt/app/icon.svg", IconDescriptor("file", "/opt/app/icon.svg")),
        (
            "file:///opt/app/icon.svg",
            IconDescriptor("file", "file:///opt/app/icon.svg"),
        ),
        (
            "resource://org/example/icon",
            IconDescriptor("serialized", "resource://org/example/icon"),
        ),
    ],
)
def test_search_icon_classification_preserves_declared_shape(value, expected):
    assert search_icon(_application(declared_icon=value)) == expected


def test_search_merges_actions_while_quicklists_remain_source_exclusive():
    actions = (
        ApplicationAction(
            action_id="shared",
            name="Shared from Gio",
            sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
            file_exec_line="app --shared",
        ),
        ApplicationAction(
            action_id="gio-only",
            name="Gio Only",
            sources=frozenset({ActionSource.GIO}),
        ),
        ApplicationAction(
            action_id="file-only",
            name="File Only",
            sources=frozenset({ActionSource.DESKTOP_FILE}),
            file_exec_line="app --file",
        ),
    )
    gio_application = _application(actions=actions, has_gio_source=True)
    file_application = replace(gio_application, has_gio_source=False)

    assert search_actions(gio_application) == (
        DesktopActionProjection("shared", "Shared from Gio"),
        DesktopActionProjection("gio-only", "Gio Only"),
        DesktopActionProjection("file-only", "File Only"),
    )
    assert quicklist_actions(gio_application) == (
        DesktopActionProjection("shared", "Shared from Gio"),
        DesktopActionProjection("gio-only", "Gio Only"),
    )
    assert quicklist_actions(file_application) == (
        DesktopActionProjection("shared", "Shared from Gio"),
        DesktopActionProjection("file-only", "File Only"),
    )
    assert new_window_action(file_application) is None

    new_window = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
        file_exec_line="app --new-window",
    )
    routed = _application(actions=(new_window,), has_gio_source=True)
    assert new_window_action(routed) is new_window


def test_quicklist_projection_preserves_gio_action_order():
    canonical = _application(
        actions=(
            ApplicationAction(
                action_id="new-window",
                name="New Window",
                sources=frozenset({ActionSource.GIO}),
            ),
            ApplicationAction(
                action_id="private",
                name="Private Window",
                sources=frozenset({ActionSource.GIO}),
            ),
        )
    )

    projected = quicklist_actions(canonical)

    assert [(action.action_id, action.name) for action in projected] == [
        ("new-window", "New Window"),
        ("private", "Private Window"),
    ]
