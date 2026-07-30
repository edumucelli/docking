"""Tests for explicit user-owned script commands."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.search.scripts import (
    ScriptCommandCatalog,
    execute_script,
    parse_script_arguments,
    script_command_from_path,
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
    command = script_command_from_path(path)
    catalog = ScriptCommandCatalog(directories=(tmp_path,))

    assert command is not None
    assert command.name == "Deploy Project"
    assert command.keyword == "deploy"
    assert catalog.snapshot() == (command,)


def test_script_arguments_use_shell_quoting_without_shell_execution(
    tmp_path,
    monkeypatch,
) -> None:
    command = script_command_from_path(_script(tmp_path))
    assert command is not None
    popen = MagicMock()
    monkeypatch.setattr("docking.search.scripts.subprocess.Popen", popen)

    assert parse_script_arguments('--env "staging west"') == (
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
    assert parse_script_arguments('"unterminated') is None


def test_script_is_revalidated_immediately_before_execution(
    tmp_path,
    monkeypatch,
) -> None:
    path = _script(tmp_path)
    command = script_command_from_path(path)
    assert command is not None
    replacement = tmp_path / "replacement"
    replacement.write_text("#!/bin/sh\n")
    replacement.chmod(0o700)
    path.unlink()
    path.symlink_to(replacement)
    popen = MagicMock()
    monkeypatch.setattr("docking.search.scripts.subprocess.Popen", popen)

    assert not execute_script(
        command=command,
        arguments=(),
        run_in_terminal=False,
    )
    popen.assert_not_called()
