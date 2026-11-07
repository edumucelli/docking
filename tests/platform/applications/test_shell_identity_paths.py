"""Shell-provided paths distinguish classic applications sharing an exe name."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.core.items import DockItem
from docking.platform.applications.identity import ProcessIdentity
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.types import ApplicationLocation, ApplicationOrigin
from tests.platform.application_fakes import ApplicationRegistryStub, application


@pytest.mark.parametrize("visible", [False, True])
def test_canonical_path_distinguishes_shell_records_without_exec_strings(visible):
    applications = tuple(
        replace(
            application(f"Example.{label}", wm_class="shared"),
            executable_path=Path(f"C:/Applications/{label}/Shared.exe"),
            exec_line="",
            location=ApplicationLocation.HOST,
        )
        for label in ("First", "Second")
    )
    # Both existing tie-breaks must yield to the actual process path: visible
    # aliases prefer the last pin; the registry prefers the first candidate.
    expected = applications[0 if visible else 1]
    identity = ProcessIdentity(pid=42, executable_path=expected.executable_path)
    matcher = AppIdMatcher(
        ApplicationRegistryStub(applications),
        SimpleNamespace(identity_for_pid=lambda _pid: identity),
    )
    if visible:
        matcher.sync_visible_items(
            [
                DockItem(kind="app", desktop_id=app.desktop_id, application_info=app)
                for app in applications
            ]
        )

    matched = matcher.match_result("shared", process_id=42)

    assert matched is not None
    assert matched.desktop_id == expected.desktop_id
    assert matched.application is expected
    assert matched.evidence.executable_path == expected.executable_path


def _pe_image():
    header = bytearray(128)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (128).to_bytes(4, "little")
    return bytes(header) + b"PE\0\0"


@pytest.mark.parametrize("visible", [False, True])
def test_unlisted_pe_program_does_not_borrow_another_executables_pin(tmp_path, visible):
    paths = [tmp_path / folder / "Shared.exe" for folder in ("installed", "other")]
    for path in paths:
        path.parent.mkdir()
        path.write_bytes(_pe_image())
    installed = replace(
        application("Example.Installed", wm_class="shared"),
        executable_path=paths[0],
        exec_line="",
        location=ApplicationLocation.HOST,
    )
    process = ProcessIdentity(pid=42, executable_path=paths[1])
    from docking.platform.applications.implementations.windows.registration import (
        WindowsExecutableRegistration,
    )

    registry = ApplicationRegistryStub((installed,))
    registry.registrations = WindowsExecutableRegistration()
    matcher = AppIdMatcher(
        registry,
        SimpleNamespace(identity_for_pid=lambda _pid: process),
    )
    if visible:
        matcher.sync_visible_items(
            [
                DockItem(
                    kind="app",
                    desktop_id=installed.desktop_id,
                    application_info=installed,
                )
            ]
        )

    matched = matcher.match_result("shared", process_id=42)

    assert matched is not None
    assert matched.desktop_id != installed.desktop_id
    assert matched.application.origin is ApplicationOrigin.RUNTIME
    assert matched.application.executable_path == paths[1]
