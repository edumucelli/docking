"""Tests for the remaining shared application-derived values."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from docking.platform.applications.projections import (
    dock_icon_name,
    new_window_action,
    quicklist_actions,
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


def test_dock_icon_fallback_and_generated_sibling_icon(tmp_path):
    assert dock_icon_name(_application(declared_icon="")) == "application-x-executable"

    executable = tmp_path / "tool"
    executable.write_bytes(b"\x7fELF")
    icon = tmp_path / "tool.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    generated = _application(
        origin=ApplicationOrigin.GENERATED,
        declared_icon="",
        executable_path=executable.resolve(),
    )

    assert dock_icon_name(generated) == str(icon.resolve())


def test_quicklists_filter_by_launch_source_and_preserve_canonical_actions():
    shared = ApplicationAction(
        action_id="shared",
        name="Shared",
        sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
        file_exec_line="app --shared",
    )
    gio_only = ApplicationAction(
        action_id="gio-only",
        name="Gio Only",
        sources=frozenset({ActionSource.GIO}),
    )
    file_only = ApplicationAction(
        action_id="file-only",
        name="File Only",
        sources=frozenset({ActionSource.DESKTOP_FILE}),
        file_exec_line="app --file",
    )
    gio_application = _application(actions=(shared, gio_only, file_only))
    file_application = replace(gio_application, has_gio_source=False)

    assert quicklist_actions(gio_application) == (shared, gio_only)
    assert quicklist_actions(file_application) == (shared, file_only)


def test_new_window_requires_a_gio_action():
    new_window = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
        file_exec_line="app --new-window",
    )
    application = _application(actions=(new_window,))

    assert new_window_action(application) is new_window
    assert new_window_action(replace(application, has_gio_source=False)) is None
