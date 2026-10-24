"""Generated desktop launchers must execute the exact selected file."""

import time

import pytest
from gi.repository import Gio

from docking.platform.applications import entries
from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.projections import dock_icon_name
from docking.platform.applications.registry import ApplicationRegistry


@pytest.fixture(autouse=True)
def isolated_launchers(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(entries, "_refresh_desktop_database", lambda _directory: None)


@pytest.mark.parametrize("launch_method", ["gio", "dock"])
@pytest.mark.parametrize(
    "filename",
    [
        "plain",
        "a" * 255,
        "space tool",
        "percent%f",
        'quote"tool',
        r"back\tool",
        "dollar$tool",
        "tick`tool",
        "line\ntool",
        "tab\ttool",
        "equal=tool",
        r"back\$tool",
    ],
)
def test_generated_launcher_executes_selected_file(
    tmp_path, monkeypatch, filename, launch_method
):
    executable = tmp_path / filename
    executable.write_text('#!/bin/sh\nprintf launched > "$DOCKING_AUDIT_MARKER"\n')
    executable.chmod(0o700)
    marker = tmp_path / "launched"
    generated = entries.create_desktop_entry_for_executable(executable)
    assert generated is not None
    app_info = Gio.DesktopAppInfo.new_from_filename(str(generated.path))
    assert app_info is not None, generated.path.read_text()
    context = Gio.AppLaunchContext()
    context.setenv("DOCKING_AUDIT_MARKER", str(marker))

    registry = ApplicationRegistry(
        application_source=lambda: (),
        desktop_directories_source=lambda: (generated.path.parent,),
    )
    registry.refresh()
    if launch_method == "gio":
        assert app_info.launch([], context)
    else:
        monkeypatch.setenv("DOCKING_AUDIT_MARKER", str(marker))
        launcher = ApplicationLauncher(registry, LaunchProvenanceStore())
        assert launcher.launch(generated.desktop_id)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if marker.exists() and marker.read_text() == "launched":
            break
        time.sleep(0.01)

    assert marker.read_text() == "launched"
    application = registry.get(generated.desktop_id)
    assert application is not None
    assert application.executable_path == executable.resolve()
    assert dock_icon_name(application) == generated.icon_name
    assert (
        entries.normalized_exec_basename(application.exec_line)
        == executable.name.lower()
    )


@pytest.mark.parametrize(
    "operation",
    [
        entries.appimage_path_needing_executable_permission,
        entries.create_desktop_entry_for_executable,
    ],
)
def test_symlink_loop_drop_is_rejected_without_raising(tmp_path, operation):
    path = tmp_path / "loop.AppImage"
    path.symlink_to(path.name)

    assert operation(path) is None


def test_stale_exec_home_cannot_abort_the_application_catalog(tmp_path):
    directory = tmp_path / "applications"
    directory.mkdir()
    (directory / "stale.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Stale\nExec=~_docking_audit_absent_user/tool\n"
    )
    (directory / "valid.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Valid\nExec=/bin/true\n"
    )
    registry = ApplicationRegistry(
        application_source=lambda: (), desktop_directories_source=lambda: (directory,)
    )

    registry.refresh()

    assert registry.get("valid.desktop") is not None
