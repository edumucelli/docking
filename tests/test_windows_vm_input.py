"""Hardware focus handshakes cannot replay an old request or acknowledge failure."""

import json
from types import SimpleNamespace

import pytest

from tools.windows.visual import vm_input


def _request(stage, *, origin="start", delay=0, token="current"):
    return {"stage": stage, "origin": origin, "delay": delay, "token": token}


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("desktop", [("esc", 0.3), ("meta_l-d", 0.4)]),
        ("start", [("esc", 0.3), ("meta_l", 0.4)]),
        ("taskbar", [("esc", 0.3), ("meta_l", 0.4), ("esc", 0.3)]),
    ],
)
def test_hardware_origin_sequence_reaches_the_requested_shell_state(origin, expected):
    assert vm_input.focus_commands(_request("origin", origin=origin)) == expected


def test_start_escape_is_released_before_the_shortcut_and_delay():
    assert vm_input.focus_commands(_request("activate", delay=0.5)) == [
        ("esc", 0.54),
        ("ctrl-alt-spc", 0.25),
    ]


@pytest.mark.parametrize(
    "hardware_request",
    [
        _request("unknown"),
        _request("origin", origin="unknown"),
        _request("origin", delay=-1),
        _request("activate", delay=11),
        _request("activate", delay=float("nan")),
        _request("activate", delay=float("inf")),
    ],
)
def test_invalid_hardware_requests_are_rejected(hardware_request):
    with pytest.raises(ValueError):
        vm_input.focus_commands(hardware_request)


def _write_request(output, stage, **kwargs):
    (output / f"keyboard-{stage}.request.json").write_text(
        json.dumps(_request(stage, **kwargs)), encoding="utf-8"
    )


def test_fresh_requests_replace_stale_acknowledgements_only_after_input(
    tmp_path, monkeypatch
):
    _write_request(tmp_path, "origin", token="new-origin")
    _write_request(tmp_path, "activate", token="new-activation")
    response = tmp_path / "keyboard-origin.response.json"
    response.write_text(json.dumps({"token": "previous"}), encoding="utf-8")
    sent = []

    def send(commands, *, container):
        assert container == "owned-lab"
        if not sent:
            assert json.loads(response.read_text())["token"] == "previous"
        sent.append(commands)

    monkeypatch.setattr(vm_input, "send_keys", send)
    vm_input.drive_focus_keyboard(tmp_path, container="owned-lab")
    assert len(sent) == 2
    assert json.loads(response.read_text())["token"] == "new-origin"
    activation = json.loads((tmp_path / "keyboard-activate.response.json").read_text())
    assert activation["token"] == "new-activation"
    assert activation["commands"] == [["esc", 0.04], ["ctrl-alt-spc", 0.25]]


def test_an_acknowledged_request_is_never_replayed(tmp_path, monkeypatch):
    _write_request(tmp_path, "origin")
    (tmp_path / "keyboard-origin.response.json").write_text(
        json.dumps({"token": "current"}), encoding="utf-8"
    )
    clock = iter((0, 100))
    monkeypatch.setattr(
        vm_input, "time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    sent = []
    monkeypatch.setattr(vm_input, "send_keys", lambda *a, **kw: sent.append(a))
    with pytest.raises(TimeoutError, match="No fresh keyboard request"):
        vm_input.drive_focus_keyboard(tmp_path, container="owned-lab")
    assert not sent


def test_failed_input_is_not_acknowledged(tmp_path, monkeypatch):
    _write_request(tmp_path, "origin")

    def fail(*_args, **_kwargs):
        raise RuntimeError("Monitor unavailable")

    monkeypatch.setattr(vm_input, "send_keys", fail)
    with pytest.raises(RuntimeError, match="Monitor unavailable"):
        vm_input.drive_focus_keyboard(tmp_path, container="owned-lab")
    assert not (tmp_path / "keyboard-origin.response.json").exists()
