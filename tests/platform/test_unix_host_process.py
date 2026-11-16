"""Unix diagnostics must leave WebKit's garbage-collection signal available."""

import os
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from docking.applets.aiusage.implementations.unix.hooks import UnixUsageHooks
from docking.platform.host.implementations.unix.process import hook_command
from docking.platform.host.selection import host_services


@pytest.mark.parametrize("directory", ["My Projects", "developer's tools"])
def test_hook_command_quotes_project_and_interpreter_paths(directory):
    root = Path("/tmp") / directory / "docking"
    executable = str(root / ".venv/bin/python")

    assert shlex.split(hook_command(root, executable)) == [
        f"PYTHONPATH={root}",
        executable,
        "-m",
        "docking.applets.aiusage.hook",
    ]


@pytest.mark.parametrize("directory", ["source", "My Projects", "developer's tools"])
@pytest.mark.parametrize("current_present", [False, True])
def test_unix_hook_registration_migrates_legacy_commands_without_losing_other_hooks(
    tmp_path, monkeypatch, directory, current_present
):
    root = tmp_path / directory
    executable = str(root / ".venv/bin/python")
    host = host_services()
    monkeypatch.setattr(host, "hook_command", hook_command)
    policy = UnixUsageHooks(host, root=root, executable=executable)
    legacy = f"PYTHONPATH={root} {executable} -m docking.applets.aiusage.hook"
    current = hook_command(root, executable)
    hooks = {}
    for event in ("Stop", "SessionStart"):
        other = {"type": "command", "command": f"other-tool {event}"}
        old = {"type": "command", "command": f"{legacy} claude {event}"}
        hooks[event] = [
            {"matcher": "keep", "hooks": [other, old]},
            {"hooks": [old]},
        ]
        if current_present:
            hooks[event].append(
                {"hooks": [{"type": "command", "command": f"{current} claude {event}"}]}
            )

    changed = policy.update_claude(hooks)

    assert changed is (legacy != current)
    for event, entries in hooks.items():
        if legacy == current:
            assert entries[0]["hooks"][1]["command"] == f"{current} claude {event}"
            continue
        assert len(entries) == 2
        assert entries[0] == {
            "matcher": "keep",
            "hooks": [{"type": "command", "command": f"other-tool {event}"}],
        }
        assert entries[1]["hooks"] == [
            {"type": "command", "command": f"{current} claude {event}"}
        ]
    assert policy.update_claude(hooks) is False


@pytest.mark.skipif(os.name == "nt", reason="Unix diagnostic signals")
def test_stack_dump_preserves_existing_sigusr1_handler():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            dedent("""
                import faulthandler
                import os
                import signal

                from docking.platform.host.implementations.unix.process import prepare_process

                signal.signal(signal.SIGUSR1, lambda *_: print("SIGUSR1 preserved"))
                prepare_process()
                assert faulthandler.is_enabled()
                os.kill(os.getpid(), signal.SIGUSR1)

                def dump_stacks():
                    os.kill(os.getpid(), signal.SIGUSR2)

                dump_stacks()
            """),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "SIGUSR1 preserved" in result.stdout
    assert "in dump_stacks" in result.stderr
