"""Native input editor build paths, diagnostics and owned process lifecycle."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_editor as mod


@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("D:/owned build", "D:\\owned build"),
        ("//owned-fixture/share/build", r"\\owned-fixture\share\build"),
        ("//?/UNC/owned-fixture/share/build", r"\\owned-fixture\share\build"),
        ("//?/D:/owned build", "D:\\owned build"),
    ],
)
@pytest.mark.parametrize("bits", [32, 64])
def test_editor_compiler_receives_native_paths_including_forward_unc(
    root, expected, bits
):
    command = mod._editor_command(
        Path("C:/Windows/Microsoft.NET/Framework/v4.0.30319/csc.exe"),
        Path(root) / "editor.cs",
        Path(root) / f"editor{bits}.exe",
        bits=bits,
    )
    assert command == [
        r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
        "/nologo",
        "/target:winexe",
        "/platform:" + ("x86" if bits == 32 else "x64"),
        "/reference:System.Windows.Forms.dll",
        "/out:" + expected + f"\\editor{bits}.exe",
        expected + r"\editor.cs",
    ]


@pytest.mark.parametrize("bits", [32, 64])
def test_editor_build_keeps_source_and_flags_and_records_explicit_architecture(
    tmp_path, monkeypatch, bits
):
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    run = Mock(
        return_value=subprocess.CompletedProcess([], 0, b"compiler warning", b"")
    )
    monkeypatch.setattr(mod.subprocess, "run", run)
    executable = mod.build_editor(tmp_path, bits=bits)
    assert executable == tmp_path / f"editor{bits}.exe"
    assert (tmp_path / "editor.cs").read_text() == mod._EDITOR_SOURCE
    assert run.call_args.kwargs == {
        "check": True,
        "capture_output": True,
        "timeout": 30,
    }
    assert run.call_args.args[0][-2:] == [
        "/out:" + mod.command_path(executable),
        mod.command_path(tmp_path / "editor.cs"),
    ]
    metadata = json.loads((tmp_path / "editor-build.json").read_text())
    assert metadata["status"] == "success" and metadata["returncode"] == 0
    assert metadata["platform"] == ("x86" if bits == 32 else "x64")
    assert not metadata["output_truncated"]
    assert "compiler warning" in (tmp_path / "editor-build.log").read_text()


def test_compiler_failure_retains_stdout_stderr_and_redacts_command_paths(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYSTEMROOT", "//owned-fixture/share/Windows")
    monkeypatch.setenv("USERPROFILE", "C:/Users/fixture-home")
    monkeypatch.setenv("TEMP", "C:/Users/fixture-home/Temp")
    stdout = str(tmp_path / "editor.cs") + ": error CS2007: invalid compiler option"
    stderr = (
        mod.command_path(tmp_path / "editor32.exe")
        + " C:\\Users\\fixture-home\\Temp\\compiler.tmp"
        + " \\\\owned-fixture\\share\\Windows\\Microsoft.NET\\Framework\\v4.0.30319\\csc.exe"
    )

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(
            1, command, stdout.encode(), stderr.encode()
        )

    monkeypatch.setattr(mod.subprocess, "run", fail)
    with pytest.raises(
        RuntimeError, match=r"compilation failed.*editor-build\.log"
    ) as error:
        mod.build_editor(tmp_path)
    log = (tmp_path / "editor-build.log").read_text()
    assert "error CS2007" in log and "<owned-build>" in log and "<framework>" in log
    assert "<temp>" in log
    for private in (str(tmp_path), "owned-fixture", "fixture-home"):
        assert private not in log
        assert private not in str(error.value)
        assert private not in (tmp_path / "editor-build.json").read_text()
    metadata = json.loads((tmp_path / "editor-build.json").read_text())
    assert metadata["status"] == "failed" and metadata["returncode"] == 1


def test_editor_compile_timeout_retains_partial_output_and_stays_fatal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command, 30, output=b"partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(mod.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        mod.build_editor(tmp_path)
    assert error.value.timeout == 30 and error.value.cmd == "<framework-csc>"
    log = (tmp_path / "editor-build.log").read_text()
    assert "partial stdout" in log and "partial stderr" in log
    metadata = json.loads((tmp_path / "editor-build.json").read_text())
    assert metadata["status"] == "timeout" and metadata["returncode"] is None


def test_editor_compiler_launch_error_is_retained_without_private_filename(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        Mock(side_effect=FileNotFoundError(2, "missing", "private-path")),
    )
    with pytest.raises(OSError, match="compiler did not start") as error:
        mod.build_editor(tmp_path)
    assert "private-path" not in str(error.value)
    metadata = json.loads((tmp_path / "editor-build.json").read_text())
    assert metadata["status"] == "launch-error" and metadata["errno"] == 2
    assert (tmp_path / "editor-build.log").is_file()


def test_editor_build_diagnostics_are_bounded_utf8_and_redacted_before_truncation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    output = (str(tmp_path) + " 日本語" * 10_000).encode()
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, output, b"")),
    )
    mod.build_editor(tmp_path)
    log = (tmp_path / "editor-build.log").read_text(encoding="utf-8")
    assert len(log.encode()) <= mod._EDITOR_LOG_LIMIT
    assert str(tmp_path) not in log and "<owned-build>" in log
    metadata = json.loads((tmp_path / "editor-build.json").read_text())
    assert metadata["status"] == "success" and metadata["output_truncated"]


@pytest.fixture
def owned(tmp_path, monkeypatch):
    native = Mock(spec=mod.WindowsDesktop)
    native.user = SimpleNamespace(PostMessageW=Mock(return_value=True))
    kernel = SimpleNamespace(
        CreateEventW=Mock(return_value=900),
        SetEvent=Mock(return_value=1),
        CloseHandle=Mock(return_value=1),
    )
    process = Mock(pid=77)
    process.poll.return_value = None

    def finished(*, timeout):
        process.poll.return_value = 0
        return 0

    process.wait.side_effect = finished
    popen = Mock(return_value=process)
    build = Mock(return_value=tmp_path / "editor-64/editor64.exe")
    monkeypatch.setattr(mod, "build_editor", build)
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mod.ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    machine = Mock(return_value=0x8664)
    editor = mod.OwnedInputEditor(
        native, tmp_path / "editor-64", tmp_path / "input.txt", bits=64, machine=machine
    )
    window = {"pid": 77, "hwnd": 701, "title": editor.title, "rect": [0, 0, 520, 300]}
    native.windows.return_value = [window]

    def wait_for(predicate, message, _seconds=15):
        value = predicate()
        if value is None:
            raise AssertionError(message)
        return value

    native.wait_for.side_effect = wait_for
    return SimpleNamespace(
        editor=editor,
        native=native,
        kernel=kernel,
        process=process,
        machine=machine,
        popen=popen,
        build=build,
        window=window,
    )


def test_owned_amd64_editor_normalizes_launch_and_owns_pause_event_until_normal_close(
    owned,
):
    editor = owned.editor
    assert editor.start() == owned.window
    owned.build.assert_called_once_with(editor.directory, bits=64)
    owned.machine.assert_called_once_with(77)
    arguments = owned.popen.call_args.args[0]
    assert arguments[0] == mod.command_path(owned.build.return_value)
    assert arguments[2] == mod.command_path(editor.document)
    assert arguments[3].startswith("Local\\DockingInputFixture.")
    assert arguments[4] == mod.command_path(editor.marker)
    assert editor.gate == 900 and editor.journal["machine"] == "0x8664"
    owned.kernel.CloseHandle.assert_not_called()
    editor.close()
    owned.kernel.SetEvent.assert_called_once_with(900)
    owned.native.user.PostMessageW.assert_called_once_with(701, 0x10, 0, 0)
    owned.process.wait.assert_called_once_with(timeout=10)
    owned.process.terminate.assert_not_called()
    owned.kernel.CloseHandle.assert_called_once_with(900)
    assert (
        editor.gate is None
        and editor.journal["closed"]
        and editor.journal["event_closed"]
    )
    editor.close()
    assert owned.kernel.CloseHandle.call_count == 1
    with pytest.raises(RuntimeError, match="started twice"):
        editor.start()


@pytest.mark.parametrize("actual", [0xAA64, 0xA64E, 0x14C])
def test_owned_amd64_editor_rejects_native_arm_or_x86_and_rolls_back(owned, actual):
    owned.machine.return_value = actual
    with pytest.raises(RuntimeError, match="wrong process architecture"):
        owned.editor.start()
    owned.native.wait_for.assert_not_called()
    owned.native.user.PostMessageW.assert_called_once_with(701, 0x10, 0, 0)
    owned.kernel.CloseHandle.assert_called_once_with(900)
    assert owned.editor.journal["closed"] and not owned.editor.journal["started"]


def test_owned_x86_editor_keeps_same_pause_protocol_and_requires_actual_x86(owned):
    owned.machine.return_value = 0x14C
    editor = mod.OwnedInputEditor(
        owned.native,
        owned.editor.directory,
        owned.editor.document,
        bits=32,
        machine=owned.machine,
    )
    owned.window["title"] = editor.title
    editor.start()
    owned.build.assert_called_once_with(editor.directory, bits=32)
    assert editor.gate == 900 and editor.journal["machine"] == "0x014c"
    editor.close()
    assert editor.journal["closed"] and editor.journal["event_closed"]


def test_failed_event_creation_never_closes_invalid_handle_or_launches_process(owned):
    owned.kernel.CreateEventW.return_value = 0
    with pytest.raises(OSError, match="pause event did not open"):
        owned.editor.start()
    owned.popen.assert_not_called()
    owned.kernel.CloseHandle.assert_not_called()


def test_startup_cannot_adopt_foreign_pid_window_and_terminates_only_owned_process(
    owned,
):
    owned.window["pid"] = 99
    with pytest.raises(AssertionError, match="did not map"):
        owned.editor.start()
    owned.process.terminate.assert_called_once_with()
    owned.native.user.PostMessageW.assert_not_called()
    owned.kernel.CloseHandle.assert_called_once_with(900)


def test_close_timeout_is_fatal_and_cleans_process_and_event(owned):
    owned.editor.start()
    owned.process.wait.side_effect = [subprocess.TimeoutExpired("owned-editor", 10), 0]
    with pytest.raises(subprocess.TimeoutExpired):
        owned.editor.close()
    owned.process.terminate.assert_called_once_with()
    owned.kernel.CloseHandle.assert_called_once_with(900)
    assert owned.editor.journal["forced_termination"]
    assert not owned.editor.journal["closed"] and owned.editor.journal["event_closed"]


def test_close_cancellation_propagates_after_releasing_owned_process_and_event(owned):
    owned.editor.start()
    owned.process.wait.side_effect = [KeyboardInterrupt(), 0]
    with pytest.raises(KeyboardInterrupt):
        owned.editor.close()
    owned.process.terminate.assert_called_once_with()
    owned.kernel.CloseHandle.assert_called_once_with(900)
    assert owned.editor.journal["forced_termination"]
    assert not owned.editor.journal["closed"] and owned.editor.journal["event_closed"]


def test_close_never_posts_to_reused_hwnd_in_a_different_process(owned):
    owned.editor.start()
    owned.window["pid"] = 99
    owned.process.wait.side_effect = [subprocess.TimeoutExpired("owned-editor", 10), 0]
    with pytest.raises(subprocess.TimeoutExpired):
        owned.editor.close()
    owned.native.user.PostMessageW.assert_not_called()
    owned.process.terminate.assert_called_once_with()
    owned.kernel.CloseHandle.assert_called_once_with(900)


def test_failed_handle_release_stays_owned_for_retry(owned):
    owned.editor.start()
    owned.kernel.CloseHandle.side_effect = [0, 1]
    with pytest.raises(OSError, match="pause event did not close"):
        owned.editor.close()
    assert owned.editor.gate == 900 and not owned.editor.journal["event_closed"]
    owned.editor.close()
    assert owned.editor.gate is None and owned.editor.journal["event_closed"]


@pytest.mark.parametrize("bits", [0, 16, 128])
def test_unsupported_editor_architecture_never_creates_resources(tmp_path, bits):
    with pytest.raises(ValueError, match="architecture"):
        mod.build_editor(tmp_path / "not-created", bits=bits)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("module", ["keyboardlayout_smoke", "input_profile_smoke"])
def test_both_input_tools_expose_explicit_optional_amd64_editor_mode(module):
    result = subprocess.run(
        [sys.executable, "-m", "tools.windows.visual." + module, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--owned-amd64-editor" in result.stdout
    assert "stock Notepad" in " ".join(result.stdout.split())
