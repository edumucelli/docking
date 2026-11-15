"""Unix diagnostics must leave WebKit's garbage-collection signal available."""

import os
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from docking.platform.host.implementations.unix.process import hook_command


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
