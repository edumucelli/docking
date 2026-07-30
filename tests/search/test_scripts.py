"""Tests for explicit user-owned script commands."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.search.providers.scripts import _parse_script_arguments
from docking.search.services.script_commands import (
    ScriptCommandCatalog,
    execute_script,
)


def _script(tmp_path, name="deploy"):
    path = tmp_path / name
    path.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                "# @docking.name Deploy Project",
                "# @docking.description Deploy the current project",
                "# @docking.keyword deploy",
                "# @docking.icon system-run",
                "exit 0",
            )
        )
    )
    path.chmod(0o700)
    return path


def test_script_metadata_and_catalog_discovery(tmp_path) -> None:
    path = _script(tmp_path)
    ignored = tmp_path / "not-executable"
    ignored.write_text("#!/bin/sh")
    catalog = ScriptCommandCatalog(directories=(tmp_path,))
    commands = catalog.snapshot()

    assert len(commands) == 1
    command = commands[0]
    assert command.path == path
    assert command.name == "Deploy Project"
    assert command.keyword == "deploy"


def test_script_arguments_use_shell_quoting_without_shell_execution(
    tmp_path,
    monkeypatch,
) -> None:
    _script(tmp_path)
    command = ScriptCommandCatalog(directories=(tmp_path,)).snapshot()[0]
    popen = MagicMock()
    monkeypatch.setattr(
        "docking.search.services.script_commands.subprocess.Popen",
        popen,
    )

    assert _parse_script_arguments('--env "staging west"') == (
        "--env",
        "staging west",
    )
    assert execute_script(
        command=command,
        arguments=("--env", "staging west"),
        run_in_terminal=False,
    )

    argv = popen.call_args.args[0]
    assert argv == [str(command.path), "--env", "staging west"]
    assert popen.call_args.kwargs["shell"] is False


def test_malformed_script_arguments_are_rejected() -> None:
    assert _parse_script_arguments('"unterminated') is None


def test_script_is_revalidated_immediately_before_execution(
    tmp_path,
    monkeypatch,
) -> None:
    path = _script(tmp_path)
    command = ScriptCommandCatalog(directories=(tmp_path,)).snapshot()[0]
    replacement = tmp_path / "replacement"
    replacement.write_text("#!/bin/sh\n")
    replacement.chmod(0o700)
    path.unlink()
    path.symlink_to(replacement)
    popen = MagicMock()
    monkeypatch.setattr(
        "docking.search.services.script_commands.subprocess.Popen",
        popen,
    )

    assert not execute_script(
        command=command,
        arguments=(),
        run_in_terminal=False,
    )
    popen.assert_not_called()
