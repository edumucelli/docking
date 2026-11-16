"""A blank capture, undelivered click or unrelated process must fail minimum coverage."""

import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.visual.shared.linux.probes import launcher_probe as launcher


def test_launch_record_is_invisible_until_the_complete_nonce_is_written(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_LAUNCH_NONCE", "owned-fixture")
    monkeypatch.setattr(launcher.sys, "argv", ["launcher_probe.py", "--launched"])
    launched = tmp_path / "launched.json"

    def interrupted_write(path, text):
        with path.open("w") as stream:
            stream.write(text[:1])
            stream.flush()
            assert not launched.exists()
            stream.write(text[1:])

    observed = []
    monkeypatch.setattr(Path, "write_text", interrupted_write)
    monkeypatch.setattr(
        launcher.native,
        "fixture",
        lambda: observed.append(json.loads(launched.read_text())),
    )

    runpy.run_path(launcher.__file__, run_name="__main__")

    assert observed == [{"pid": os.getpid(), "nonce": "owned-fixture"}]


@pytest.mark.parametrize(
    "failure", [None, "blank", "no-input", "no-launch", "wrong-nonce"]
)
def test_launcher_requires_pixels_input_and_its_own_launched_fixture(
    monkeypatch, tmp_path, failure
):
    monkeypatch.setenv("LAB_EXPECTED_BACKEND", "sway")
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setenv(
        "LAB_INPUT_SUPPORTED", "false" if failure == "no-input" else "true"
    )
    monkeypatch.setenv("LAB_INPUT_SOCKET", "owned-pointer")
    monkeypatch.setenv("LAB_LAUNCH_NONCE", "owned-fixture")
    image = SimpleNamespace(
        get_width=lambda: 96,
        get_height=lambda: 96,
        get_pixels=lambda: (
            (b"\x00\x00\x00" if failure == "blank" else b"\x00\x00\xff") * 96 * 96
        ),
        get_rowstride=lambda: 96 * 3,
        get_n_channels=lambda: 3,
    )
    probe = launcher.LauncherProbe(None, SimpleNamespace(name="sway"), "bottom")

    async def capture(_phase):
        return image

    async def wait(predicate, message):
        assert predicate(), message

    def click(command, **_kwargs):
        if command[-1] == "272" and failure != "no-launch":
            (tmp_path / "launched.json").write_text(
                json.dumps(
                    {
                        "pid": 1234,
                        "nonce": "unrelated"
                        if failure == "wrong-nonce"
                        else "owned-fixture",
                    }
                )
            )

    probe.capture, probe.wait = capture, wait
    monkeypatch.setattr(launcher.subprocess, "run", click)
    killed = []
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: killed.append(pid))
    task = probe.run()

    def finish():
        try:
            while True:
                task.send(None)
        except StopIteration:
            pass

    if failure:
        with pytest.raises(AssertionError):
            finish()
        assert not (tmp_path / "launcher-result.json").exists()
        assert not killed
    else:
        finish()
        assert killed == [1234]
        assert (
            json.loads((tmp_path / "launcher-result.json").read_text())["launched"]
            is True
        )
