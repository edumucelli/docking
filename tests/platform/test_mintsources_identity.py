"""Software Sources identity regression from issue #364's October report."""

from types import SimpleNamespace

import pytest

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
)
from docking.platform.applications.types import MatchMethod
from docking.platform.backends.x11.impl.window_tracker import WindowMatcher
from tests.platform_factories import make_application_registry, make_process_identity


@pytest.mark.parametrize(
    "command,matched",
    [
        ("pkexec mintsources", True),
        ("/usr/bin/pkexec /usr/bin/mintsources", True),
        ("pkexec -- mintsources", True),
        ("mintsources", True),
        ("pkexec --user root mintsources", False),
        ("pkexec", False),
        ("pkexec --", False),
        ("pkexec other-tool", False),
        ('sh -c "pkexec mintsources"', False),
    ],
)
def test_software_sources_launcher(tmp_path, command, matched):
    apps = tmp_path / "applications"
    apps.mkdir()
    launcher = apps / "mintsources.desktop"
    launcher.write_text(
        "[Desktop Entry]\nType=Application\nName=Software Sources\n"
        f"Exec={command}\nIcon=mintsources\n"
    )
    proc = tmp_path / "proc" / "41"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(
        b"/usr/bin/python3\0/usr/lib/linuxmint/mintSources/mintSources.py\0"
    )
    registry = make_application_registry(
        application_source=lambda: (), desktop_directories_source=lambda: (apps,)
    )
    registry._catalog._desktop_app_info_for_id = lambda _id: None
    registry._catalog._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    processes = make_process_identity(
        LaunchProvenanceStore(),
        proc_root=proc.parent,
        executable_resolver=lambda _pid: None,
    )
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    window = SimpleNamespace(
        get_class_group_name=lambda: "MintSources.py",
        get_class_instance_name=lambda: "mintSources.py",
        get_pid=lambda: 41,
    )
    result = matcher.match_result(window)
    if matched:
        assert result.desktop_id == "mintsources.desktop"
        assert result.evidence.method is MatchMethod.SCRIPT_NAME
        assert result.application.exec_line == command
    else:
        assert result is None

    # A script name alone cannot identify a different window or choose between
    # multiple installed launchers sharing the same alias.
    window.get_class_group_name = lambda: "Other.py"
    assert matcher.match_result(window) is None
    window.get_class_group_name = lambda: "MintSources.py"
    (apps / "duplicate.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Duplicate\nExec=mintsources\n"
    )
    registry.refresh()
    assert matcher.match_result(window) is None
