"""Portable hook transport and real cross-process usage-update ownership."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.applets.aiusage import hook_command, locking, store
from docking.applets.aiusage.backends import claude, codex
from docking.applets.aiusage.state import ModelUsage, state_from_prefs


def test_gui_python_uses_console_sibling_for_stdin_hooks(tmp_path, monkeypatch):
    gui = tmp_path / "pythonw.exe"
    console = tmp_path / "python.exe"
    gui.touch()
    console.touch()
    monkeypatch.setattr(hook_command, "sys", SimpleNamespace(executable=str(gui)))
    argv = hook_command.hook_argv("claude", "Stop")
    assert argv[0] == str(console)
    assert Path(argv[1]).name == "hook_runner.py"
    assert argv[2:] == ["claude", "Stop"]


def test_windows_claude_exec_hooks_preserve_other_hooks_and_are_idempotent(
    tmp_path, monkeypatch
):
    settings = tmp_path / "settings.json"
    other = {"type": "command", "command": "other-tool Stop"}
    old = {
        "type": "command",
        "command": "PYTHONPATH=old python -m docking.applets.aiusage.hook claude Stop",
    }
    relocated = {
        "type": "command",
        "command": "old-python.exe",
        "args": [r"C:\Old\docking\applets\aiusage\hook_runner.py", "claude", "Stop"],
    }
    settings.write_text(
        json.dumps(
            {
                "keep": "résumé",
                "hooks": {"Stop": [{"matcher": "*", "hooks": [other, old, relocated]}]},
            }
        ),
        encoding="utf-8",
    )
    argv = [
        r"C:\Program Files\Python\python.exe",
        r"C:\Projects\résumé [#%+]\hook_runner.py",
    ]
    monkeypatch.setattr(claude, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(claude, "_CLAUDE_SETTINGS", settings)
    monkeypatch.setattr(claude, "hook_argv", lambda *args: argv + list(args))
    claude.register_hooks()
    first = settings.read_bytes()
    claude.register_hooks()
    assert settings.read_bytes() == first
    result = json.loads(first)
    assert result["keep"] == "résumé"
    hooks = result["hooks"]
    assert hooks["Stop"][0]["hooks"] == [other]
    for event in ("Stop", "SessionStart"):
        owned = hooks[event][-1]["hooks"][0]
        assert owned["command"] == argv[0]
        assert owned["args"] == [*argv[1:], "claude", event]


def test_windows_codex_notify_argv_is_valid_toml_and_idempotent_with_bracket_paths(
    tmp_path, monkeypatch
):
    tomllib = pytest.importorskip("tomllib")
    config = tmp_path / "config.toml"
    config.write_text('model = "example"\n[profiles.work]\nkeep = true\n')
    argv = [
        r"C:\Program Files\Python\python.exe",
        r"C:\Projects\résumé [#%+]\hook_runner.py",
        "codex",
    ]
    monkeypatch.setattr(
        codex, "sys", SimpleNamespace(platform="win32", executable=argv[0])
    )
    monkeypatch.setattr(codex, "_CODEX_CONFIG", config)
    monkeypatch.setattr(codex, "hook_argv", lambda *_args: argv)
    codex.register_hook()
    first = config.read_bytes()
    codex.register_hook()
    assert config.read_bytes() == first
    parsed = tomllib.loads(first.decode("utf-8"))
    assert parsed["notify"] == argv
    assert parsed["profiles"]["work"]["keep"] is True


@pytest.mark.parametrize("wait_result", [0, 0x80])
def test_windows_mutex_closes_handle_and_releases_acquired_or_abandoned_owner(
    monkeypatch, tmp_path, wait_result
):
    kernel = MagicMock()
    kernel.CreateMutexW.return_value = 500
    kernel.WaitForSingleObject.return_value = wait_result
    monkeypatch.setattr(
        locking,
        "ctypes",
        SimpleNamespace(
            WinDLL=lambda *_a, **_kw: kernel,
            c_void_p=ctypes.c_void_p,
            get_last_error=lambda: 5,
            WinError=lambda code: OSError(code, "Native mutex failure"),
        ),
    )
    with (
        pytest.raises(RuntimeError, match="writer failed"),
        locking._windows_mutex(tmp_path / "dock.json"),
    ):
        raise RuntimeError("writer failed")
    kernel.ReleaseMutex.assert_called_once_with(500)
    kernel.CloseHandle.assert_called_once_with(500)
    name = kernel.CreateMutexW.call_args.args[2]
    assert name.startswith("Global\\Docking.AIUsage.")
    assert len(name) < 100


def test_failed_windows_mutex_wait_closes_handle_without_releasing_unowned_lock(
    monkeypatch, tmp_path
):
    kernel = MagicMock()
    kernel.CreateMutexW.return_value = 500
    kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
    monkeypatch.setattr(
        locking,
        "ctypes",
        SimpleNamespace(
            WinDLL=lambda *_a, **_kw: kernel,
            c_void_p=ctypes.c_void_p,
            get_last_error=lambda: 5,
            WinError=lambda code: OSError(code, "Native mutex failure"),
        ),
    )
    with pytest.raises(OSError), locking._windows_mutex(tmp_path / "dock.json"):
        pytest.fail("Writer entered after wait failure")
    kernel.ReleaseMutex.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(500)


def test_failed_lock_acquisition_still_closes_preferences_descriptor(
    tmp_path, monkeypatch
):
    path = tmp_path / "dock.json"
    monkeypatch.setattr(store, "config_path", lambda: path)
    descriptors = []

    @contextmanager
    def fail(fd, _path):
        descriptors.append(fd)
        raise OSError("lock failed")
        yield  # pragma: no cover

    monkeypatch.setattr(store, "exclusive_update", fail)
    with pytest.raises(OSError, match="lock failed"):
        store.replace_session(
            session_id="s", model_usage={"model": ModelUsage(input_tokens=1)}
        )
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_real_hook_process_runs_from_another_directory_with_utf8_stdin(tmp_path):
    config_home = tmp_path / "config with spaces"
    config = config_home / "docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {"preserve": "résumé # % +", "applet_prefs": {"clock": {"style": "analog"}}}
        ),
        encoding="utf-8",
    )
    transcript = tmp_path / "session résumé # % +.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.run(
        hook_command.hook_argv("claude", "Stop"),
        input=json.dumps(
            {"session_id": "native-hook", "transcript_path": str(transcript)}
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=tmp_path,
        env={**os.environ, "XDG_CONFIG_HOME": str(config_home)},
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(config.read_text(encoding="utf-8"))
    assert result["preserve"] == "résumé # % +"
    assert result["applet_prefs"]["clock"] == {"style": "analog"}
    day = state_from_prefs(prefs=result["applet_prefs"]["aiusage"]).days[0]
    assert day.sessions == 1
    assert dict(day.by_model)["claude-opus-4"].input_tokens == 10


def test_real_concurrent_hook_writers_preserve_every_session(tmp_path):
    config = tmp_path / "dock.json"
    config.write_text(json.dumps({"preserve": "résumé"}), encoding="utf-8")
    script = tmp_path / "writer.py"
    script.write_text("""import sys,time
from pathlib import Path
from docking.applets.aiusage import store
from docking.applets.aiusage.state import ModelUsage
path, barrier, index = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
store.config_path=lambda:path
original=store.aiusage_state.set_session
def slow(**kwargs):
    time.sleep(.02)
    return original(**kwargs)
store.aiusage_state.set_session=slow
barrier.with_name(barrier.name+str(index)+'.ready').touch()
deadline=time.monotonic()+20
while not barrier.exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
for count in (1,2,3):
    store.replace_session(session_id='session-'+str(index),model_usage={'gpt-5.4':ModelUsage(input_tokens=(index+1)*count)})
assert 'gi.repository.Gtk' not in sys.modules
""")
    barrier = tmp_path / "go"
    root = str(Path(__file__).resolve().parents[2])
    environment = {
        **os.environ,
        "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    processes = [
        subprocess.Popen(
            [
                hook_command.hook_argv()[0],
                str(script),
                str(config),
                str(barrier),
                str(index),
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(4)
    ]
    try:
        import time

        deadline = time.monotonic() + 25
        while len(list(tmp_path.glob("go*.ready"))) != 4:
            assert time.monotonic() < deadline, "Hook writers did not become ready"
            time.sleep(0.01)
        barrier.touch()
        for process in processes:
            _out, error = process.communicate(timeout=30)
            assert process.returncode == 0, error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    result = json.loads(config.read_text(encoding="utf-8"))
    assert result["preserve"] == "résumé"
    day = state_from_prefs(prefs=result["applet_prefs"]["aiusage"]).days[0]
    assert day.sessions == 4
    assert dict(day.by_model)["gpt-5.4"].input_tokens == 30
