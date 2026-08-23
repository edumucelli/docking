"""Tests for generic shell command and terminal launching."""

from __future__ import annotations

import logging
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform import commands as commands_mod
from docking.platform.commands import (
    DESKTOP_EXEC_FIELD_CODES_RE,
    TERMINAL_CANDIDATES,
    TERMINAL_LOOKUP_TIMEOUT_SECONDS,
    ResolvedTerminal,
    TerminalMode,
    _flatpak_host_terminal_name,
    append_file_argument,
    build_shell_argv,
    build_terminal_argv,
    clean_desktop_exec,
    find_terminal,
    launch_command,
    shell_path,
)


@pytest.fixture(autouse=True)
def _clear_host_terminal_cache():
    _flatpak_host_terminal_name.cache_clear()
    yield
    _flatpak_host_terminal_name.cache_clear()


def test_shell_path_uses_environment_and_falls_back_to_posix_sh(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert shell_path() == "/bin/zsh"

    monkeypatch.setenv("SHELL", "")
    assert shell_path() == "/bin/sh"


def test_build_shell_argv_strips_command_and_supports_shell_injection():
    assert build_shell_argv(command="  echo ok  ", shell="/bin/zsh") == [
        "/bin/zsh",
        "-lc",
        "echo ok",
    ]


def test_terminal_candidates_cover_common_desktops_and_wayland():
    names = {candidate.executable for candidate in TERMINAL_CANDIDATES}

    assert {
        "x-terminal-emulator",
        "gnome-terminal",
        "kgx",
        "ptyxis",
        "konsole",
        "qterminal",
        "foot",
        "kitty",
        "alacritty",
        "wezterm",
        "xterm",
    }.issubset(names)


def test_find_terminal_uses_first_available_resolver_result():
    checked: list[str] = []

    def resolver(name: str) -> str | None:
        checked.append(name)
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    assert find_terminal(resolver=resolver) == ResolvedTerminal(
        executable="/usr/bin/gnome-terminal",
        exec_prefix=("--",),
        mode=TerminalMode.ARGV,
    )
    assert checked == ["x-terminal-emulator", "sensible-terminal", "gnome-terminal"]


def test_find_terminal_prefers_discovered_flatpak_host_terminal(monkeypatch):
    local_resolver = MagicMock(side_effect=AssertionError("local lookup is unexpected"))
    monkeypatch.setattr(
        commands_mod,
        "_flatpak_host_terminal_name",
        lambda: "konsole",
    )
    monkeypatch.setattr(
        commands_mod,
        "resolve_terminal_executable",
        local_resolver,
    )

    assert find_terminal() == ResolvedTerminal(
        executable="konsole",
        exec_prefix=("-e",),
        mode=TerminalMode.ARGV,
    )
    local_resolver.assert_not_called()


def test_build_terminal_argv_uses_argv_terminal():
    def resolver(name: str) -> str | None:
        return name if name == "x-terminal-emulator" else None

    assert build_terminal_argv(
        command="echo ok",
        shell="/bin/sh",
        resolver=resolver,
    ) == [
        "x-terminal-emulator",
        "-e",
        "/bin/sh",
        "-lc",
        "echo ok",
    ]


def test_build_terminal_argv_supports_multi_argument_prefix():
    def resolver(name: str) -> str | None:
        return name if name == "wezterm" else None

    assert build_terminal_argv(
        command="echo ok",
        shell="/bin/sh",
        resolver=resolver,
    ) == [
        "wezterm",
        "start",
        "--",
        "/bin/sh",
        "-lc",
        "echo ok",
    ]


def test_build_terminal_argv_supports_command_string_terminal():
    def resolver(name: str) -> str | None:
        return name if name == "xfce4-terminal" else None

    assert build_terminal_argv(
        command="echo ok",
        shell="/bin/sh",
        resolver=resolver,
    ) == [
        "xfce4-terminal",
        "-e",
        "/bin/sh -lc 'echo ok'",
    ]


def test_build_terminal_argv_returns_none_without_terminal():
    assert (
        build_terminal_argv(
            command="echo ok",
            resolver=lambda _name: None,
        )
        is None
    )


def test_flatpak_host_terminal_lookup_skips_scan_without_spawn(monkeypatch):
    spawn_path = MagicMock(return_value=None)
    host_command = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(commands_mod.flatpak, "spawn_path", spawn_path)
    monkeypatch.setattr(commands_mod.flatpak, "host_command", host_command)
    monkeypatch.setattr(commands_mod.subprocess, "run", run)

    assert _flatpak_host_terminal_name() is None
    assert _flatpak_host_terminal_name() is None

    spawn_path.assert_called_once_with()
    host_command.assert_not_called()
    run.assert_not_called()


def test_flatpak_host_terminal_lookup_is_one_cached_host_scan(monkeypatch):
    host_command = MagicMock(
        return_value=["flatpak-spawn", "--host", "sh", "-lc", "scan"],
    )
    run = MagicMock(return_value=SimpleNamespace(stdout="konsole\n"))
    monkeypatch.setattr(
        commands_mod.flatpak,
        "spawn_path",
        lambda: "/usr/bin/flatpak-spawn",
    )
    monkeypatch.setattr(commands_mod.flatpak, "host_command", host_command)
    monkeypatch.setattr(commands_mod.subprocess, "run", run)

    assert _flatpak_host_terminal_name() == "konsole"
    assert _flatpak_host_terminal_name() == "konsole"

    host_command.assert_called_once()
    host_argv = host_command.call_args.args[0]
    assert host_argv[:2] == ["sh", "-lc"]
    script = host_argv[2]
    assert 'command -v "$cmd"' in script
    assert script.index("x-terminal-emulator") < script.index("gnome-terminal")
    assert host_command.call_args.kwargs == {"sanitize_env": False}
    run.assert_called_once_with(
        ["flatpak-spawn", "--host", "sh", "-lc", "scan"],
        check=False,
        capture_output=True,
        text=True,
        timeout=TERMINAL_LOOKUP_TIMEOUT_SECONDS,
    )


def test_flatpak_host_terminal_lookup_caches_timeout_failure(monkeypatch):
    run = MagicMock(
        side_effect=subprocess.TimeoutExpired(
            cmd=["flatpak-spawn", "--host"],
            timeout=TERMINAL_LOOKUP_TIMEOUT_SECONDS,
        )
    )
    monkeypatch.setattr(
        commands_mod.flatpak,
        "spawn_path",
        lambda: "/usr/bin/flatpak-spawn",
    )
    monkeypatch.setattr(
        commands_mod.flatpak,
        "host_command",
        lambda _argv, *, sanitize_env: ["flatpak-spawn", "--host"],
    )
    monkeypatch.setattr(commands_mod.subprocess, "run", run)

    assert _flatpak_host_terminal_name() is None
    assert _flatpak_host_terminal_name() is None
    run.assert_called_once()


def test_launch_command_rejects_empty_command():
    popen = MagicMock()

    assert launch_command(command=" ", run_in_terminal=False, popen=popen) is False
    popen.assert_not_called()


def test_launch_command_starts_detached_shell_process(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(commands_mod.flatpak, "host_command", lambda _argv: None)
    popen = MagicMock()

    assert launch_command(command=" echo ok ", run_in_terminal=False, popen=popen)

    popen.assert_called_once_with(
        ["/bin/zsh", "-lc", "echo ok"],
        shell=False,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_launch_command_spawns_on_flatpak_host(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    host_command = MagicMock(
        return_value=[
            "/usr/bin/flatpak-spawn",
            "--host",
            "/bin/sh",
            "-lc",
            "echo ok",
        ]
    )
    monkeypatch.setattr(commands_mod.flatpak, "host_command", host_command)
    popen = MagicMock()

    assert launch_command(command="echo ok", run_in_terminal=False, popen=popen)

    host_command.assert_called_once_with(["/bin/sh", "-lc", "echo ok"])
    assert popen.call_args.args[0] == [
        "/usr/bin/flatpak-spawn",
        "--host",
        "/bin/sh",
        "-lc",
        "echo ok",
    ]


def test_launch_command_preserves_terminal_resolver_and_popen_injections(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(commands_mod.flatpak, "host_command", lambda _argv: None)
    popen = MagicMock()

    assert launch_command(
        command="echo ok",
        run_in_terminal=True,
        resolver=lambda name: "/terminal" if name == "x-terminal-emulator" else None,
        popen=popen,
    )

    assert popen.call_args.args[0] == [
        "/terminal",
        "-e",
        "/bin/sh",
        "-lc",
        "echo ok",
    ]


def test_launch_command_logs_when_no_terminal_is_available(caplog):
    popen = MagicMock()

    with caplog.at_level(logging.WARNING, logger="docking.commands"):
        launched = launch_command(
            command="echo ok",
            run_in_terminal=True,
            resolver=lambda _name: None,
            popen=popen,
        )

    assert launched is False
    popen.assert_not_called()
    assert "No terminal emulator found" in caplog.text
    assert caplog.records[-1].action == "launch_command"


def test_launch_command_logs_spawn_failure(monkeypatch, caplog):
    monkeypatch.setattr(commands_mod.flatpak, "host_command", lambda _argv: None)
    popen = MagicMock(side_effect=OSError("boom"))

    with caplog.at_level(logging.WARNING, logger="docking.commands"):
        launched = launch_command(
            command="echo ok",
            run_in_terminal=False,
            popen=popen,
        )

    assert launched is False
    assert "Failed to launch command echo ok: boom" in caplog.text
    assert caplog.records[-1].action == "launch_command"


def test_append_file_argument_quotes_path_and_handles_empty_command():
    assert append_file_argument(command="atril", path="/tmp/report one.pdf") == (
        "atril '/tmp/report one.pdf'"
    )
    assert append_file_argument(command=" ", path="/tmp/report one.pdf") == (
        "'/tmp/report one.pdf'"
    )


def test_desktop_exec_field_code_regex_and_percent_semantics_are_preserved():
    assert DESKTOP_EXEC_FIELD_CODES_RE.pattern == r"%[uUfFdDnNickvm]"
    for code in "uUfFdDnNickvm":
        assert clean_desktop_exec(f"example %{code}") == "example"

    assert clean_desktop_exec("example %Z") == "example %Z"
    assert clean_desktop_exec("example %%") == "example %%"
    assert clean_desktop_exec("example %%U") == "example %"
