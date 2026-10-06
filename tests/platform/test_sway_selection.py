"""Native Sway selection must validate IPC and preserve generic fallback."""

from unittest.mock import MagicMock

import pytest

from docking.core.config import Config
from docking.platform.backends import selection
from docking.platform.backends.wayland import services, sway_ipc, sway_session
from tests.platform.application_fakes import identity_services


@pytest.mark.parametrize("reply", [OSError("stale socket"), {}, {"type": "root"}])
def test_native_sway_probe_rejects_stale_or_non_sway_socket(monkeypatch, reply):
    monkeypatch.setenv("SWAYSOCK", "/tmp/private-sway.sock")
    monkeypatch.setattr(services, "load_gtk_layer_shell", lambda: object())
    monkeypatch.setattr(services, "layer_shell_is_supported", lambda _shell: True)
    query = (
        MagicMock(side_effect=reply)
        if isinstance(reply, OSError)
        else MagicMock(return_value=reply)
    )
    monkeypatch.setattr(sway_ipc.SwayIpcClient, "query", query)
    constructor = MagicMock()
    monkeypatch.setattr(sway_session, "SwaySessionBackend", constructor)
    result = selection._create_sway_backend(
        config=Config(), model=MagicMock(), reason="native test", **identity_services()
    )
    query.assert_called_once_with(4)
    if reply == {"type": "root"}:
        assert result is constructor.return_value
    else:
        assert result is None
        constructor.assert_not_called()
