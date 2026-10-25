"""Native default-handler dispatch uses a literal shell target, never a command."""

from unittest.mock import MagicMock

import pytest

from docking.platform.backends.windows import shell


@pytest.mark.parametrize(
    "target",
    [
        r"C:\My Files\résumé & 100%.txt",
        r"\\server\share\file #1.txt",
        "https://example.com/docs?a=1&b=2",
    ],
)
def test_default_handler_uses_the_native_shell_association(target, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(shell.os, "startfile", launch, raising=False)
    assert shell.open_default_target(target)
    launch.assert_called_once_with(target, "open")


@pytest.mark.parametrize(
    "error", [OSError("No handler"), NotImplementedError("No shell")]
)
def test_default_handler_reports_native_open_failure(error, monkeypatch):
    launch = MagicMock(side_effect=error)
    monkeypatch.setattr(shell.os, "startfile", launch, raising=False)
    assert not shell.open_default_target("missing.txt")
