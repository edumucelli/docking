"""Update Manager's renamed Python process and desktop variants (issue #369)."""

from types import SimpleNamespace

import pytest

from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.types import MatchMethod
from docking.platform.backends.x11.impl.window_tracker import WindowMatcher
from tests.platform_factories import make_application_registry, make_process_identity


@pytest.fixture
def mintupdate_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    apps = tmp_path / "applications"
    apps.mkdir()
    for desktop_id, visibility in (
        ("mintupdate.desktop", "NotShowIn=KDE;"),
        ("mintupdate-kde.desktop", "OnlyShowIn=KDE;"),
    ):
        (apps / desktop_id).write_text(
            "[Desktop Entry]\nType=Application\nName=Update Manager\n"
            f"Exec=mintupdate\n{visibility}\n"
        )
    process = tmp_path / "proc" / "42"
    process.mkdir(parents=True)
    executable = tmp_path / "python3.13"
    executable.touch()
    (process / "exe").symlink_to(executable)
    (process / "cmdline").write_bytes(b"mintUpdate\0" + b"\0" * 100)
    registry = make_application_registry(
        application_source=lambda: (), desktop_directories_source=lambda: (apps,)
    )
    registry._catalog._desktop_app_info_for_id = lambda _id: None
    registry._catalog._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    processes = make_process_identity(LaunchProvenanceStore(), proc_root=process.parent)
    return apps, process, registry, processes


@pytest.mark.parametrize("frontend", ["x11", "wayland"])
@pytest.mark.parametrize("desktop", ["X-Cinnamon", "KDE"])
@pytest.mark.parametrize("renamed", [False, True])
def test_update_manager_matches_its_current_desktop_variant(
    mintupdate_identity, monkeypatch, frontend, desktop, renamed
):
    _apps, process, registry, processes = mintupdate_identity
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    registry.refresh()
    if not renamed:
        (process / "cmdline").write_bytes(
            b"/usr/bin/python3.13\0/usr/lib/linuxmint/mintUpdate/mintUpdate.py\0"
        )
    if frontend == "x11":
        matcher = WindowMatcher(
            application_registry=registry, process_identity_service=processes
        )
        match = matcher.match_result(
            SimpleNamespace(
                get_class_group_name=lambda: "MintUpdate.py",
                get_class_instance_name=lambda: "mintUpdate.py",
                get_pid=lambda: 42,
            )
        )
    else:
        match = AppIdMatcher(registry, processes).match_result(
            "MintUpdate.py", instance_hint="mintUpdate.py", process_id=42
        )
    expected = "mintupdate-kde.desktop" if desktop == "KDE" else "mintupdate.desktop"
    assert match is not None
    assert match.desktop_id == expected
    assert match.application.exec_line == "mintupdate"
    assert match.evidence.method is (
        MatchMethod.PROCESS_TITLE if renamed else MatchMethod.SCRIPT_NAME
    )
    identity = processes.identity_for_pid(42)
    assert identity.script_basename == (None if renamed else "mintUpdate.py")


@pytest.mark.parametrize(
    "command,extra",
    [
        ("mintupdate", ""),
        ("mintupdate", "OnlyShowIn=X-Cinnamon;"),
        ("mintupdate --other", "OnlyShowIn=KDE;"),
        ("mintupdate", "OnlyShowIn=KDE;\nTerminal=true"),
        ("mintupdate", "OnlyShowIn=KDE;\nPath=/other"),
    ],
)
def test_script_matching_keeps_genuine_launcher_ambiguity(
    mintupdate_identity, command, extra
):
    apps, _process, registry, processes = mintupdate_identity
    # Replace the KDE alternative with an ambiguous or different invocation.
    (apps / "mintupdate-kde.desktop").write_text(
        f"[Desktop Entry]\nType=Application\nName=Other\nExec={command}\n{extra}\n"
    )
    registry.refresh()
    assert (
        AppIdMatcher(registry, processes).match_result("MintUpdate.py", process_id=42)
        is None
    )


@pytest.mark.parametrize(
    "title,window_class,executable",
    [
        (b"other\0", "MintUpdate.py", "python3.13"),
        (b"mintUpdate\0", "Other.py", "python3.13"),
        (b"mintUpdate\0", "MintUpdate.py", "other"),
        (b"mintUpdate\0", "MintUpdate.py", None),
        (b"mintUpdate --private\0", "MintUpdate.py", "python3.13"),
        (b"mintUpdate\0--private\0", "MintUpdate.py", "python3.13"),
        (b"/somewhere/mintUpdate\0", "MintUpdate.py", "python3.13"),
        (b"\xff\0", "MintUpdate.py", "python3.13"),
        (b"x" * 4097, "MintUpdate.py", "python3.13"),
    ],
)
def test_process_title_requires_corroborating_identity(
    mintupdate_identity, title, window_class, executable
):
    _apps, process, registry, processes = mintupdate_identity
    (process / "cmdline").write_bytes(title)
    (process / "exe").unlink()
    if executable is not None:
        target = process.parent.parent / executable
        target.touch()
        (process / "exe").symlink_to(target)
    assert (
        AppIdMatcher(registry, processes).match_result(window_class, process_id=42)
        is None
    )
    assert "private" not in repr(processes.identity_for_pid(42))
