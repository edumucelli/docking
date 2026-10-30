"""Native input editor build paths, diagnostics and owned process lifecycle."""

import ctypes
import json
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_editor as mod


@pytest.fixture
def focus_desktop():
    editor = {"hwnd": 101, "pid": 202, "rect": [65, 50, 585, 350]}
    foreground = [999]
    position = [0, 0]
    pressed = Mock()

    def move(x, y):
        position[:] = x, y
        return 1

    def cursor(output):
        output._obj.x, output._obj.y = position
        return 1

    def click(x, y, *, before_press):
        move(x, y)
        before_press()
        pressed()
        foreground[0] = 101

    def wait(predicate, message):
        assert predicate(), message

    native = SimpleNamespace(
        windows=Mock(return_value=[editor]),
        window_at=Mock(return_value=101),
        click=Mock(side_effect=click),
        wait_for=wait,
        user=SimpleNamespace(
            GetForegroundWindow=lambda: foreground[0],
            GetCursorPos=Mock(side_effect=cursor),
            SetCursorPos=Mock(side_effect=move),
        ),
        _send=pressed,
    )

    def owner(hwnd, pid):
        pid._obj.value = 202 if hwnd == 101 else 909
        return 303 if hwnd == 101 else 910

    def cls(hwnd, text, length):
        text.value = "OwnedEditor" if hwnd == 101 else "Cover"
        return len(text.value)

    def rect(hwnd, output):
        bounds = editor["rect"] if hwnd == 101 else [0, 0, 1024, 768]
        output._obj.left, output._obj.top, output._obj.right, output._obj.bottom = (
            bounds
        )
        return 1

    native.user.WindowFromPoint = Mock(
        side_effect=lambda point: native.window_at(point.x, point.y)
    )
    native.user.GetAncestor = Mock(side_effect=lambda hwnd, _flag: hwnd)
    native.user.GetWindowThreadProcessId = Mock(side_effect=owner)
    native.user.GetClassNameW = Mock(side_effect=cls)
    native.user.GetWindowRect = Mock(side_effect=rect)
    return native, editor, foreground


def test_editor_focus_avoids_covering_popup_and_sends_one_verified_click(focus_desktop):
    native, editor, foreground = focus_desktop
    # The hosted Start popup covers x>=190; the editor's left client area is free.
    native.window_at.side_effect = lambda x, _y: 999 if x >= 190 else 101
    x, y = mod.focus_editor(native, editor)
    assert 65 < x < 190 and 50 < y < 350 and foreground[0] == 101
    assert native.click.call_count == 1 and native.click.call_args.args == (x, y)
    assert all(call.args == (202,) for call in native.windows.call_args_list)


def test_fully_covered_editor_never_clicks_an_unrelated_window(focus_desktop):
    native, editor, foreground = focus_desktop
    native.window_at.return_value = 999
    with pytest.raises(AssertionError, match="no exposed input point"):
        mod.focus_editor(native, editor)
    native.click.assert_not_called()
    assert foreground[0] == 999


@pytest.mark.parametrize("later", [[], [{"hwnd": 101, "pid": 303}]])
def test_editor_reuse_or_disappearance_before_click_is_fatal(focus_desktop, later):
    native, editor, _foreground = focus_desktop
    native.windows.side_effect = [[editor], later]
    with pytest.raises(AssertionError, match="identity changed"):
        mod.focus_editor(native, editor)
    native._send.assert_not_called()


def test_editor_covered_between_hit_tests_is_not_clicked(focus_desktop):
    native, editor, _foreground = focus_desktop
    native.window_at.side_effect = [101, 999]
    with pytest.raises(AssertionError, match="covered before click"):
        mod.focus_editor(native, editor)
    native._send.assert_not_called()


def test_editor_covered_during_pointer_settle_aborts_before_button_injection(
    focus_desktop, monkeypatch
):
    from tools.windows.visual import desktop_driver

    native, editor, _foreground = focus_desktop
    covered = [False]
    native.window_at.side_effect = lambda *_: 999 if covered[0] else 101
    monkeypatch.setattr(
        desktop_driver.time, "sleep", lambda _seconds: covered.__setitem__(0, True)
    )
    native.click = lambda *args, **kw: desktop_driver.WindowsDesktop.click(
        native, *args, **kw
    )
    with pytest.raises(AssertionError, match="covered before click"):
        mod.focus_editor(native, editor)
    native._send.assert_not_called()


def test_pointer_moving_during_settle_aborts_before_button_injection(
    focus_desktop, monkeypatch
):
    from tools.windows.visual import desktop_driver

    native, editor, _foreground = focus_desktop
    monkeypatch.setattr(
        desktop_driver.time, "sleep", lambda _seconds: native.user.SetCursorPos(10, 10)
    )
    native.click = lambda *args, **kw: desktop_driver.WindowsDesktop.click(
        native, *args, **kw
    )
    with pytest.raises(AssertionError, match="Pointer moved away"):
        mod.focus_editor(native, editor)
    native._send.assert_not_called()


def test_physical_editor_click_must_really_acquire_foreground(focus_desktop):
    native, editor, _foreground = focus_desktop
    native.click.side_effect = None
    with pytest.raises(AssertionError, match="did not receive foreground"):
        mod.focus_editor(native, editor)
    native.click.assert_called_once()


def test_focus_uses_current_editor_geometry(focus_desktop):
    native, editor, _foreground = focus_desktop
    native.windows.return_value = [dict(editor, rect=[-700, 100, -200, 500])]
    x, y = mod.focus_editor(native, editor)
    assert -700 < x < -200 and 100 < y < 500
    assert native.click.call_count == 1 and native.click.call_args.args == (x, y)


@pytest.mark.parametrize("rows", [[], [{"hwnd": 101, "pid": 303}]])
def test_focus_rejects_editor_that_is_not_the_visible_owned_process(
    focus_desktop, rows
):
    native, editor, _foreground = focus_desktop
    native.windows.return_value = rows
    with pytest.raises(AssertionError, match="no longer visible"):
        mod.focus_editor(native, editor)
    native.window_at.assert_not_called()
    native.click.assert_not_called()


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


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("bits", [32, 64])
def test_editor_build_keeps_source_and_flags_and_records_explicit_architecture(
    tmp_path, monkeypatch, bits, platform
):
    monkeypatch.setattr(mod, "sys", SimpleNamespace(platform=platform))
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
        "creationflags": 0x08000000 if platform == "win32" else 0,
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

    def process_times(handle, created, exit, kernel, user):
        created._obj.low, created._obj.high = 123, 1
        return 1

    kernel = SimpleNamespace(
        GetProcessTimes=Mock(side_effect=process_times),
        GetProcessId=Mock(return_value=77),
        CreateEventW=Mock(return_value=900),
        SetEvent=Mock(return_value=1),
        CloseHandle=Mock(return_value=1),
    )
    process = Mock(pid=77, _handle=22)
    process.poll.return_value = None

    def finished(*, timeout):
        process.poll.return_value = 0
        return 0

    process.wait.side_effect = finished
    popen = Mock(return_value=process)
    executable = tmp_path / "editor-64/editor64.exe"
    executable.parent.mkdir()

    def compiled(directory, *, bits):
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664 if bits == 64 else 0x14C)
        executable.write_bytes(data)
        return executable

    build = Mock(return_value=executable, side_effect=compiled)
    monkeypatch.setattr(mod, "build_editor", build)
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mod.ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    machine = Mock(return_value=0x8664)
    editor = mod.OwnedInputEditor(
        native, tmp_path / "editor-64", tmp_path / "input.txt", bits=64, machine=machine
    )
    window = {"pid": 77, "hwnd": 701, "title": editor.title, "rect": [0, 0, 520, 300]}
    native.windows.return_value = [window]

    def window_pid(hwnd, pid):
        pid._obj.value = window["pid"]
        return 88

    native.user.GetWindowThreadProcessId = Mock(side_effect=window_pid)
    native.user.GetClassNameW = Mock(return_value=0)
    native.user.GetWindowRect = Mock(return_value=0)

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


@pytest.fixture
def exposure(focus_desktop, tmp_path, monkeypatch):
    import hashlib

    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows import windows as native_windows

    native, window, foreground = focus_desktop
    native.wait_for = Mock(side_effect=native.wait_for)
    window["rect"] = [156, 156, 676, 456]
    cover = {"hwnd": 999, "pid": 909, "rect": [31, 156, 1024, 675]}
    background = {"hwnd": 888, "pid": 808, "rect": [0, 0, 1024, 768]}
    windows = [cover, window, background]
    original = list(window["rect"])
    created, late_cover = [123], [False]
    owner = mod.OwnedInputEditor(
        native, tmp_path, tmp_path / "input.txt", bits=64, machine=lambda _: 0x8664
    )
    process = Mock(pid=202, _handle=22)
    process.poll.return_value = None
    owner.process, owner.window = process, window
    owner._created, owner._pe = 123, 0x8664
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, 0x8664)
    owner._executable = tmp_path / "editor.exe"
    owner._executable.write_bytes(data)
    owner._image_hash = hashlib.sha256(data).hexdigest()

    def times(handle, output, *rest):
        assert handle == 22
        output._obj.low = created[0]
        return 1

    owner._kernel = SimpleNamespace(
        GetProcessId=Mock(return_value=202), GetProcessTimes=Mock(side_effect=times)
    )
    native.windows.side_effect = lambda pid=None: [
        row for row in windows if pid is None or row["pid"] == pid
    ]
    native.workarea = Mock(return_value=[0, 0, 1024, 768])

    def at(x, y):
        if late_cover[0]:
            return 999
        return next(
            (
                row["hwnd"]
                for row in windows
                if row["rect"][0] <= x < row["rect"][2]
                and row["rect"][1] <= y < row["rect"][3]
            ),
            0,
        )

    def identity(hwnd, pid):
        pid._obj.value = next(row["pid"] for row in windows if row["hwnd"] == hwnd)
        return 303 if hwnd == 101 else 910

    def rect(hwnd, output):
        bounds = next(row["rect"] for row in windows if row["hwnd"] == hwnd)
        output._obj.left, output._obj.top, output._obj.right, output._obj.bottom = (
            bounds
        )
        return 1

    def position(hwnd, after, x, y, width, height, flags):
        assert hwnd == 101 and (after, width, height, flags) == (0, 0, 0, 0x215)
        w, h = (
            window["rect"][2] - window["rect"][0],
            window["rect"][3] - window["rect"][1],
        )
        window["rect"] = [x, y, x + w, y + h]
        return 1

    native.window_at.side_effect = at
    native.user.GetWindowThreadProcessId.side_effect = identity
    native.user.GetWindowRect.side_effect = rect
    native.user.GetWindow = Mock(
        side_effect=lambda hwnd, relation: (
            {999: 101, 101: 888, 888: 0}[hwnd] if relation == 2 else 0
        )
    )
    native.user.GetWindowLongW = Mock(return_value=0)
    native.user.SetWindowPos = Mock(side_effect=position)
    snapshot = SimpleNamespace(id=SimpleNamespace(value=101), pid=202)

    def activate(target, action):
        assert target is snapshot and action == "activate"
        foreground[0] = 101
        return ActionResult.OK

    api = SimpleNamespace(
        capture_snapshot=Mock(return_value=snapshot), perform=Mock(side_effect=activate)
    )
    constructor = Mock(return_value=api)
    monkeypatch.setattr(native_windows, "Win32WindowAPI", constructor)
    monkeypatch.setattr(ctypes, "set_last_error", lambda _: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "native"), raising=False
    )
    return SimpleNamespace(
        owner=owner,
        native=native,
        window=window,
        foreground=foreground,
        created=created,
        late_cover=late_cover,
        windows=windows,
        original=original,
        cover=cover,
        activation=api,
        activation_constructor=constructor,
        snapshot=snapshot,
    )


def test_covered_owned_editor_moves_once_after_normal_activation_without_changing_z_order(
    exposure,
):
    native, journal = exposure.native, {}
    point = mod.focus_editor(
        native, exposure.window, owned=exposure.owner, journal=journal
    )
    native.user.SetWindowPos.assert_called_once_with(101, 0, 0, 0, 0, 0, 0x215)
    native.click.assert_called_once_with(
        *point, before_press=native.click.call_args.kwargs["before_press"]
    )
    attempt = journal["attempts"][0]
    assert (
        attempt["placement"]["foreground_before"]
        == attempt["placement"]["foreground_after"]
        == 101
    )
    assert attempt["activated"] and exposure.foreground[0] == 101
    assert attempt["identity_before_placement"]["created"] == 123
    assert attempt["identity_before_placement"]["pe_machine"] == "0x8664"
    assert len(attempt["identity_before_placement"]["image_sha256"]) == 64
    initial = [p for p in attempt["points"] if p["phase"] == "initial"]
    assert len(initial) == 16 and all(p["hit"]["pid"] == 909 for p in initial)
    assert attempt["placement"]["after"] == [0, 0, 520, 300]
    assert attempt["before_press"]["root"]["hwnd"] == 101
    assert "geometry_restored" not in attempt["placement"]
    assert exposure.cover["rect"] == [31, 156, 1024, 675]
    assert [row["hwnd"] for row in exposure.windows] == [999, 101, 888]


def test_placement_refuses_full_workarea_occlusion_without_moving_or_clicking(exposure):
    exposure.cover["rect"] = [0, 0, 1024, 768]
    with pytest.raises(AssertionError, match="No observed exposed placement"):
        mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    exposure.native.click.assert_not_called()


def test_placement_does_not_assume_enumwindows_order(exposure):
    exposure.native.windows.side_effect = lambda pid=None: [
        row for row in reversed(exposure.windows) if pid is None or row["pid"] == pid
    ]
    mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_called_once_with(101, 0, 0, 0, 0, 0, 0x215)


def test_placement_uses_negative_workarea_coordinates_without_resize(exposure):
    exposure.native.workarea.return_value = [-1024, 0, 0, 768]
    for row in exposure.windows:
        row["rect"] = [
            row["rect"][0] - 1024,
            row["rect"][1],
            row["rect"][2] - 1024,
            row["rect"][3],
        ]
    mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_called_once_with(
        101, 0, -1024, 0, 0, 0, 0x215
    )
    assert exposure.window["rect"] == [-1024, 0, -504, 300]


def test_already_exposed_owned_editor_never_changes_geometry(exposure):
    exposure.cover["rect"] = [900, 156, 1024, 675]
    mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    assert exposure.window["rect"] == exposure.original


def test_cyclic_z_order_cannot_authorize_unknown_placement(exposure):
    exposure.native.user.GetWindow.side_effect = lambda hwnd, _: hwnd
    with pytest.raises(AssertionError, match="No observed exposed placement"):
        mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    assert exposure.native.user.GetWindow.call_count == 1


def test_unknown_background_identity_is_not_treated_as_free_placement(exposure):
    exposure.native.window_at.side_effect = lambda x, y: 999 if y >= 156 else 777
    exposure.native.user.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: (
        setattr(
            pid._obj, "value", 707 if hwnd == 777 else (202 if hwnd == 101 else 909)
        )
        or 303
    )
    old_rect = exposure.native.user.GetWindowRect.side_effect
    exposure.native.user.GetWindowRect.side_effect = lambda hwnd, out: old_rect(
        888 if hwnd == 777 else hwnd, out
    )
    with pytest.raises(AssertionError, match="No observed exposed placement"):
        mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    exposure.native.click.assert_not_called()


def test_cover_appearing_after_placement_restores_geometry_without_click(exposure):
    native, journal = exposure.native, {}
    position = native.user.SetWindowPos.side_effect

    def covered(*args):
        result = position(*args)
        exposure.late_cover[0] = True
        return result

    native.user.SetWindowPos.side_effect = covered
    with pytest.raises(AssertionError, match="no exposed input point"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    assert [call.args[2:4] for call in native.user.SetWindowPos.call_args_list] == [
        (0, 0),
        (156, 156),
    ]
    assert journal["attempts"][0]["placement"]["geometry_restored"]
    native.click.assert_not_called()
    assert (
        exposure.foreground[0] == 101 and exposure.window["rect"] == exposure.original
    )


@pytest.mark.parametrize("change", ["process", "creation", "hwnd", "pe", "image"])
def test_owned_placement_refuses_identity_change_before_positioning(exposure, change):
    owner = exposure.owner
    if change == "process":
        owner._kernel.GetProcessId.return_value = 999
    elif change == "creation":
        exposure.created[0] = 124
    elif change == "hwnd":
        owner.window = dict(exposure.window, hwnd=777)
    else:
        data = bytearray(owner._executable.read_bytes())
        if change == "pe":
            struct.pack_into("<H", data, 68, 0xAA64)
        else:
            data[-1] = 1
        owner._executable.write_bytes(data)
    with pytest.raises(AssertionError, match="changed"):
        mod.focus_editor(exposure.native, exposure.window, owned=owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    exposure.native.click.assert_not_called()


def test_owned_placement_settle_failure_restores_geometry(exposure, monkeypatch):
    from tools.windows.visual import desktop_driver

    native, journal = exposure.native, {}
    monkeypatch.setattr(
        desktop_driver.time, "sleep", lambda _: exposure.late_cover.__setitem__(0, True)
    )
    native.click = lambda *args, **kw: desktop_driver.WindowsDesktop.click(
        native, *args, **kw
    )
    with pytest.raises(AssertionError, match="covered before click"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    native._send.assert_not_called()
    assert journal["attempts"][0]["placement"]["geometry_restored"]
    assert exposure.window["rect"] == exposure.original


def test_failed_geometry_restoration_retains_original_focus_failure(exposure):
    native, journal = exposure.native, {}
    position = native.user.SetWindowPos.side_effect

    def covered(*args):
        if args[2:4] == (156, 156):
            return 0
        result = position(*args)
        exposure.late_cover[0] = True
        return result

    native.user.SetWindowPos.side_effect = covered
    with pytest.raises(AssertionError, match="no exposed input point"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    assert journal["attempts"][0]["placement"]["restore_error"] == "AssertionError"
    assert "geometry_restored" not in journal["attempts"][0]["placement"]


def test_creation_time_uses_fixed_width_filetime_and_keeps_all_64_bits(owned):
    assert ctypes.sizeof(mod._FileTime) == 8
    assert mod._creation(owned.kernel, 22) == (1 << 32) | 123
    assert owned.kernel.GetProcessTimes.argtypes == (
        ctypes.c_void_p,
        *([ctypes.POINTER(mod._FileTime)] * 4),
    )


@pytest.mark.parametrize("topmost", [False, True])
def test_owned_placement_preserves_topmost_band(exposure, topmost):
    native, journal = exposure.native, {}
    native.user.GetWindowLongW.return_value = 8 if topmost else 0
    mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    assert journal["attempts"][0]["placement"]["topmost"] is topmost
    assert all(
        call.args[-1] == 0x215 for call in native.user.SetWindowPos.call_args_list
    )


def test_owned_placement_does_not_continue_if_foreground_changes(exposure):
    native, journal = exposure.native, {}
    position = native.user.SetWindowPos.side_effect

    def changed(*args):
        result = position(*args)
        exposure.foreground[0] = 555
        return result

    native.user.SetWindowPos.side_effect = changed
    with pytest.raises(AssertionError, match="changed foreground"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    native.click.assert_not_called()
    assert exposure.foreground[0] == 555
    assert journal["attempts"][0]["placement"]["geometry_restored"]


def test_owned_identity_change_during_placement_prevents_click_and_restore(exposure):
    native, journal = exposure.native, {}
    position = native.user.SetWindowPos.side_effect

    def changed(*args):
        result = position(*args)
        exposure.created[0] = 124
        return result

    native.user.SetWindowPos.side_effect = changed
    with pytest.raises(AssertionError, match="creation time changed"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    native.click.assert_not_called()
    assert native.user.SetWindowPos.call_count == 1
    assert journal["attempts"][0]["placement"]["restore_error"] == "AssertionError"


def test_owned_placement_native_rejection_never_clicks(exposure):
    native, journal = exposure.native, {}
    position = native.user.SetWindowPos.side_effect
    native.user.SetWindowPos.side_effect = lambda *args: (
        0 if args[2:4] == (0, 0) else position(*args)
    )
    with pytest.raises(AssertionError, match="placement failed"):
        mod.focus_editor(native, exposure.window, owned=exposure.owner, journal=journal)
    native.click.assert_not_called()
    assert journal["attempts"][0]["placement"]["geometry_restored"]


def test_placement_rejects_editor_larger_than_workarea(exposure):
    exposure.native.workarea.return_value = [0, 0, 500, 200]
    with pytest.raises(AssertionError, match="does not fit"):
        mod.focus_editor(exposure.native, exposure.window, owned=exposure.owner)
    exposure.native.user.SetWindowPos.assert_not_called()
    exposure.native.click.assert_not_called()


def test_creation_query_failure_rolls_back_owned_process_and_event(owned, monkeypatch):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "query failed"), raising=False
    )
    owned.kernel.GetProcessTimes.return_value = 0
    owned.kernel.GetProcessTimes.side_effect = None
    with pytest.raises(OSError, match="query failed"):
        owned.editor.start()
    owned.kernel.CloseHandle.assert_called_once_with(900)
    assert owned.editor.journal["closed"] and owned.editor.journal["event_closed"]


def test_point_probe_preserves_desktop_shared_dword_pointer_contract(focus_desktop):
    from ctypes import wintypes

    native, _editor, _foreground = focus_desktop
    facts = mod._point_facts(native, 100, 200)
    assert facts["root"]["pid"] == 202
    assert native.user.GetWindowThreadProcessId.argtypes == (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    )


@pytest.fixture
def gtk_target(focus_desktop):
    from docking.platform.backends.windows.input_context import InputContext

    native, mapped, foreground = focus_desktop
    state = SimpleNamespace(
        origin=[65, 50],
        offset=[14, 12],
        size=[492, 36],
        scale=1,
        visible=True,
        mapped=True,
        realized=True,
        focused=True,
    )
    window = SimpleNamespace(
        get_visible=lambda: state.visible,
        get_mapped=lambda: state.mapped,
        get_scale_factor=lambda: state.scale,
        get_window=lambda: SimpleNamespace(get_origin=lambda: (True, *state.origin)),
    )
    entry = SimpleNamespace(
        get_toplevel=lambda: window,
        get_visible=lambda: state.visible,
        get_mapped=lambda: state.mapped,
        get_realized=lambda: state.realized,
        translate_coordinates=lambda toplevel, x, y: tuple(state.offset),
        get_allocation=lambda: SimpleNamespace(
            width=state.size[0], height=state.size[1]
        ),
        has_focus=lambda: state.focused,
    )
    current = [window, entry]
    context = InputContext(101, 101, 303, 202)
    api = SimpleNamespace(
        valid=Mock(return_value=True),
        input_desktop_active=Mock(return_value=True),
        foreground=Mock(side_effect=lambda: context if foreground[0] == 101 else None),
    )
    dispatch = Mock(side_effect=lambda operation: operation())
    kwargs = {
        "entry": entry,
        "window": window,
        "current": lambda: tuple(current),
        "dispatch": dispatch,
        "context": context,
        "api": api,
        "journal": {},
    }
    return SimpleNamespace(
        native=native,
        mapped=mapped,
        state=state,
        current=current,
        kwargs=kwargs,
        entry=entry,
        window=window,
    )


def test_gtk_entry_click_targets_header_instead_of_editor_body(gtk_target, monkeypatch):
    f = gtk_target
    body_grid = Mock(
        side_effect=AssertionError("GTK entries must never use the body grid")
    )
    monkeypatch.setattr(mod, "_editor_points", body_grid)
    mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    assert f.native.click.call_args.args == (325, 80)
    assert 50 < f.native.click.call_args.args[1] < 110
    assert f.kwargs["journal"]["geometry"]["rect"] == [79, 62, 571, 98]
    assert f.kwargs["journal"]["focused"]
    body_grid.assert_not_called()
    f.kwargs["api"].valid.assert_called_with(f.kwargs["context"])


@pytest.mark.parametrize("scale", (1, 2, 3))
def test_gtk_entry_geometry_uses_logical_origin_offset_and_dpi(gtk_target, scale):
    f = gtk_target
    f.state.origin, f.state.offset, f.state.size, f.state.scale = (
        [-200, 100],
        [12, 20],
        [600, 40],
        scale,
    )
    f.mapped["rect"] = [-200 * scale, 100 * scale, 500 * scale, 500 * scale]
    mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    assert f.native.click.call_args.args == (112 * scale, 140 * scale)
    assert f.kwargs["journal"]["geometry"]["scale"] == scale


@pytest.mark.parametrize(
    "change",
    ("entry", "window", "hidden", "unmapped", "unrealized", "reparented", "allocation"),
)
def test_replaced_or_unavailable_gtk_target_never_receives_a_click(gtk_target, change):
    f = gtk_target
    if change == "entry":
        f.current[1] = object()
    elif change == "window":
        f.current[0] = object()
    elif change == "reparented":
        f.entry.get_toplevel = lambda: object()
    elif change == "allocation":
        f.state.size = [0, 36]
    else:
        setattr(
            f.state,
            {"hidden": "visible", "unmapped": "mapped", "unrealized": "realized"}[
                change
            ],
            False,
        )
    with pytest.raises(AssertionError):
        mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    f.native.click.assert_not_called()
    f.native._send.assert_not_called()


@pytest.mark.parametrize(
    "change",
    (
        "entry",
        "window",
        "origin",
        "offset",
        "scale",
        "hidden",
        "hit",
        "cursor",
        "context",
    ),
)
def test_gtk_target_is_revalidated_after_pointer_settle_before_button_input(
    gtk_target, monkeypatch, change
):
    from tools.windows.visual import desktop_driver

    f = gtk_target

    def mutate(_seconds):
        if change == "entry":
            f.current[1] = object()
        elif change == "window":
            f.current[0] = object()
        elif change == "origin":
            f.state.origin[0] += 10
        elif change == "offset":
            f.state.offset[1] += 10
        elif change == "scale":
            f.state.scale = 2
        elif change == "hidden":
            f.state.visible = False
        elif change == "hit":
            f.native.window_at.return_value = 999
        elif change == "cursor":
            f.native.user.SetCursorPos(1, 1)
        else:
            f.kwargs["api"].valid.return_value = False

    monkeypatch.setattr(desktop_driver.time, "sleep", mutate)
    f.native.click = lambda *a, **kw: desktop_driver.WindowsDesktop.click(
        f.native, *a, **kw
    )
    with pytest.raises(AssertionError):
        mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    f.native._send.assert_not_called()
    assert "focused" not in f.kwargs["journal"]


@pytest.mark.parametrize(
    "change", ("foreground", "gtk-focus", "replacement", "geometry")
)
def test_physical_entry_click_must_prove_same_native_and_gtk_focus(gtk_target, change):
    f = gtk_target
    click = f.native.click.side_effect

    def changed(x, y, *, before_press):
        click(x, y, before_press=before_press)
        if change == "foreground":
            f.kwargs["api"].foreground.side_effect = None
            f.kwargs["api"].foreground.return_value = None
        elif change == "gtk-focus":
            f.state.focused = False
        elif change == "replacement":
            f.current[1] = object()
        else:
            f.state.offset[0] += 2

    f.native.click.side_effect = changed
    with pytest.raises(AssertionError):
        mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    assert "focused" not in f.kwargs["journal"]


@pytest.mark.parametrize("change", ("pid", "thread", "outside", "input-desktop"))
def test_entry_focus_rejects_wrong_native_identity_or_coordinates(gtk_target, change):
    f = gtk_target
    if change == "pid":
        f.kwargs["context"] = f.kwargs["context"].__class__(101, 101, 303, 909)
    elif change == "thread":
        f.kwargs["context"] = f.kwargs["context"].__class__(101, 101, 404, 202)
    elif change == "outside":
        f.state.origin[0] = 1000
    else:
        f.kwargs["api"].input_desktop_active.return_value = False
    with pytest.raises(AssertionError):
        mod.focus_gtk_entry(f.native, f.mapped, **f.kwargs)
    f.native.click.assert_not_called()


@pytest.fixture
def entry_trace():
    from tools.windows.visual.keyboardlayout_smoke import _EntryInputTrace

    signals, disconnected = {}, []

    def connect(signal, callback):
        subscription = len(signals) + 1
        signals[subscription] = signal, callback
        return subscription

    entry = SimpleNamespace(
        connect=Mock(side_effect=connect),
        disconnect=Mock(side_effect=disconnected.append),
    )
    journal = {}
    readback = Mock(return_value={"text": "", "layout": 0xF0020409, "has_focus": True})
    trace = _EntryInputTrace(entry, journal, readback)
    return SimpleNamespace(
        entry=entry,
        journal=journal,
        trace=trace,
        signals=signals,
        disconnected=disconnected,
        readback=readback,
    )


def test_owned_entry_trace_observes_only_its_normal_signal_callbacks(entry_trace):
    f = entry_trace
    event = SimpleNamespace(
        type=8,
        keyval=39,
        hardware_keycode=81,
        group=0,
        state=0,
        is_modifier=False,
        time=123,
    )
    assert f.signals[1][1](f.entry, event) is False
    f.trace.observe("after-scan-0")
    assert f.signals[2][1](f.entry, event) is False
    f.signals[3][1](f.entry, "'")
    assert f.journal["events"][0] == {
        "phase": "before-input",
        "monotonic_ns": f.journal["events"][0]["monotonic_ns"],
        "signal": "key",
        "type": 8,
        "keyval": 39,
        "hardware_keycode": 81,
        "group": 0,
        "state": 0,
        "is_modifier": False,
        "time": 123,
    }
    assert f.journal["events"][1]["phase"] == "after-scan-0"
    assert f.journal["events"][2]["text"] == "'"
    f.trace.close()
    assert f.disconnected == [3, 2, 1] and f.journal["disconnected"]
    assert f.journal["observations"][-1]["layout"] == 0xF0020409
    assert f.journal["observations"][-1]["phase"] == "after-input"


def test_owned_entry_trace_bounds_key_preedit_and_readback_data(entry_trace):
    f = entry_trace
    for _ in range(100):
        f.signals[3][1](f.entry, "a" * 100)
        f.trace.observe("bounded")
    assert len(f.journal["events"]) == 64 and f.journal["events_dropped"] == 36
    assert len(f.journal["events"][0]["text"]) == 32
    assert len(f.journal["observations"]) == 16
    f.trace.close()
    assert f.journal["disconnected"]


def test_owned_entry_trace_disconnects_after_text_assertion_without_replacing_it(
    entry_trace,
):
    from tools.windows.visual.input_requests import fixture_cleanup

    f = entry_trace
    f.readback.side_effect = RuntimeError("private diagnostic failure")
    error = AssertionError("Search physical input mismatch: dvorak")
    with (
        pytest.raises(AssertionError) as failed,
        fixture_cleanup(f.trace.close, f.journal, "observer"),
    ):
        raise error
    assert failed.value is error
    assert f.disconnected == [3, 2, 1]
    assert f.journal["observations"][-1]["diagnostic_error"] == "RuntimeError"
    assert "private" not in str(f.journal)


def test_owned_entry_trace_releases_prior_subscriptions_when_later_connect_fails():
    from tools.windows.visual.keyboardlayout_smoke import _EntryInputTrace

    entry = SimpleNamespace(
        connect=Mock(side_effect=[11, RuntimeError("connect failed")]),
        disconnect=Mock(),
    )
    with pytest.raises(RuntimeError, match="connect failed"):
        _EntryInputTrace(entry, {}, dict)
    entry.disconnect.assert_called_once_with(11)


def test_owned_entry_trace_attempts_every_disconnect_even_when_one_fails(entry_trace):
    f = entry_trace
    f.entry.disconnect.side_effect = [RuntimeError("disconnect failed"), None, None]
    with pytest.raises(RuntimeError, match="disconnect failed"):
        f.trace.close()
    assert [call.args[0] for call in f.entry.disconnect.call_args_list] == [3, 2, 1]
    assert not f.journal.get("disconnected")


def test_owned_entry_trace_preserves_connect_failure_when_rollback_also_fails():
    from tools.windows.visual.keyboardlayout_smoke import _EntryInputTrace

    error = RuntimeError("connect failed")
    entry = SimpleNamespace(
        connect=Mock(side_effect=[11, error]),
        disconnect=Mock(side_effect=ValueError("private rollback detail")),
    )
    journal = {}
    with pytest.raises(RuntimeError) as failed:
        _EntryInputTrace(entry, journal, dict)
    assert failed.value is error and journal["disconnect_error"] == "ValueError"
    assert "private" not in str(journal)


def test_pre_key_entry_readback_does_not_touch_lazy_gdk_keymap(monkeypatch):
    from docking.platform.backends.windows.input_context import InputContext
    from tools.windows.visual.keyboardlayout_smoke import (
        _entry_input_readback,
        _EntryInputTrace,
    )

    lazy_keymap = SimpleNamespace(
        get_for_display=Mock(side_effect=AssertionError("GTK keymap refreshed"))
    )
    monkeypatch.setitem(
        sys.modules, "gi.repository.Gdk", SimpleNamespace(Keymap=lazy_keymap)
    )
    entry = SimpleNamespace(
        get_text=lambda: "",
        has_focus=lambda: True,
        get_visible=lambda: True,
        get_mapped=lambda: True,
        get_display=Mock(side_effect=AssertionError("GDK display queried")),
        get_property=Mock(side_effect=AssertionError("GTK IM module queried")),
        get_window=Mock(side_effect=AssertionError("GTK IM client window queried")),
        connect=Mock(side_effect=[1, 2, 3]),
        disconnect=Mock(),
    )
    context = InputContext(101, 101, 303, 202)
    api = SimpleNamespace(foreground=lambda: context, active=lambda _thread: 0xF0020409)
    native = SimpleNamespace(
        user=SimpleNamespace(MapVirtualKeyExW=Mock(return_value=81))
    )
    trace = _EntryInputTrace(
        entry,
        {},
        lambda: _entry_input_readback(
            entry,
            current=lambda: entry,
            api=api,
            native=native,
            scans=[0x10],
        ),
    )
    trace.observe("before-scan-0")
    before, scan = trace.journal["observations"]
    assert before["text"] == "" and scan["layout"] == 0xF0020409
    assert scan["context"]["thread"] == 303 and scan["has_focus"]
    assert scan["scan_mapping"] == [{"scan": 0x10, "virtual": 81}]
    assert "diagnostic_error" not in scan and "keymap" not in scan and "gtk" not in scan
    lazy_keymap.get_for_display.assert_not_called()
    entry.get_display.assert_not_called()
    entry.get_property.assert_not_called()
    entry.get_window.assert_not_called()
    trace.close()
    assert trace.journal["disconnected"]


def test_full_workarea_start_overlay_yields_to_only_owned_normal_activation(exposure):
    f = exposure
    f.cover["rect"] = [0, 0, 1024, 768]
    activate = f.activation.perform.side_effect

    def foreground(target, action):
        result = activate(target, action)
        # Model ordinary activation putting the owned window above the panel.
        f.windows[:] = [f.window, f.cover, f.windows[-1]]
        return result

    f.activation.perform.side_effect = foreground
    journal = {}
    point = mod.focus_editor(f.native, f.window, owned=f.owner, journal=journal)
    f.activation.capture_snapshot.assert_called_once_with(101, expected_pid=202)
    f.activation.perform.assert_called_once_with(f.snapshot, "activate")
    f.native.user.SetWindowPos.assert_not_called()
    f.native.click.assert_called_once_with(
        *point, before_press=f.native.click.call_args.kwargs["before_press"]
    )
    row = journal["attempts"][0]
    assert row["owned_activation"]["foreground_before"] == 999
    assert row["owned_activation"]["foreground_after"] == 101
    assert (
        row["owned_activation"]["identity_before"]
        == row["owned_activation"]["identity_after"]
    )
    assert (
        row["owned_activation"]["bounds_before"]
        == row["owned_activation"]["bounds_after"]
        == f.original
    )
    assert (
        row["owned_activation"]["topmost_before"]
        is row["owned_activation"]["topmost_after"]
        is False
    )
    assert all(
        p["root"]["hwnd"] == 999 for p in row["points"] if p["phase"] == "initial"
    )
    assert (
        row["points"][-1]["phase"] == "after_activation"
        and row["points"][-1]["root"]["hwnd"] == 101
    )
    assert row["activated"] and "placement" not in row


def test_placement_after_activation_uses_fresh_cover_bounds_not_initial_full_panel(
    exposure,
):
    f = exposure
    f.cover["rect"] = [0, 0, 1024, 768]
    activate = f.activation.perform.side_effect

    def foreground(target, action):
        result = activate(target, action)
        f.cover["rect"] = [31, 156, 1024, 675]
        return result

    f.activation.perform.side_effect = foreground
    journal = {}
    mod.focus_editor(f.native, f.window, owned=f.owner, journal=journal)
    row = journal["attempts"][0]
    assert len([p for p in row["points"] if p["phase"] == "after_activation"]) == 16
    assert all(
        p["root"]["bounds"] == [0, 0, 1024, 768]
        for p in row["points"]
        if p["phase"] == "initial"
    )
    assert any(c["exposed_points"] for c in row["placement_candidates"])
    assert row["placement"]["after"] == [0, 0, 520, 300]
    f.activation.perform.assert_called_once()
    assert f.native.click.call_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        "denied",
        "false-success",
        "changed-identity",
        "geometry",
        "topmost",
        "native-error",
    ],
)
def test_owned_normal_activation_failure_never_falls_back_or_clicks(exposure, failure):
    from docking.platform.backends.base import ActionResult

    f = exposure
    activate = f.activation.perform.side_effect

    def perform(target, action):
        if failure == "denied":
            return ActionResult.FAILED
        if failure == "false-success":
            return ActionResult.OK
        if failure == "native-error":
            raise OSError(5, "Owned normal activation failed")
        result = activate(target, action)
        if failure == "changed-identity":
            f.created[0] = 124
        elif failure == "geometry":
            f.window["rect"] = [157, 156, 677, 456]
        else:
            f.native.user.GetWindowLongW.return_value = 8
        return result

    f.activation.perform.side_effect = perform
    journal = {}
    with pytest.raises((AssertionError, OSError)):
        mod.focus_editor(f.native, f.window, owned=f.owner, journal=journal)
    f.activation.perform.assert_called_once_with(f.snapshot, "activate")
    f.native.user.SetWindowPos.assert_not_called()
    f.native.click.assert_not_called()
    assert "placement_candidates" not in journal["attempts"][0]
    if failure in ("denied", "native-error"):
        f.native.wait_for.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "window", "pid", "creation"])
def test_owned_activation_revalidates_snapshot_and_popen_before_request(
    exposure, failure
):
    f = exposure
    if failure == "missing":
        f.activation.capture_snapshot.return_value = None
    elif failure == "window":
        f.activation.capture_snapshot.return_value = SimpleNamespace(
            id=SimpleNamespace(value=777), pid=202
        )
    elif failure == "pid":
        f.activation.capture_snapshot.return_value = SimpleNamespace(
            id=SimpleNamespace(value=101), pid=909
        )
    else:

        def snapshot(*_args, **_kwargs):
            f.created[0] = 124
            return f.snapshot

        f.activation.capture_snapshot.side_effect = snapshot
    with pytest.raises(AssertionError):
        mod.focus_editor(f.native, f.window, owned=f.owner)
    f.activation.perform.assert_not_called()
    f.native.user.SetWindowPos.assert_not_called()
    f.native.click.assert_not_called()


@pytest.mark.parametrize("owned", [False, True])
def test_normal_activation_permission_is_never_used_for_stock_or_exposed_editor(
    exposure, owned
):
    f = exposure
    if owned:
        f.cover["rect"] = [900, 156, 1024, 675]
        mod.focus_editor(f.native, f.window, owned=f.owner)
        assert f.native.click.call_count == 1
    else:
        with pytest.raises(AssertionError, match="no exposed input point"):
            mod.focus_editor(f.native, f.window)
        f.native.click.assert_not_called()
    f.activation_constructor.assert_not_called()
    f.activation.perform.assert_not_called()


def test_normal_activation_waits_for_cross_queue_foreground_without_repeating_request(
    exposure,
):
    from docking.platform.backends.base import ActionResult

    f = exposure
    f.activation.perform.side_effect = None
    f.activation.perform.return_value = ActionResult.OK
    waiting = f.native.wait_for.side_effect
    polls = []

    def wait(predicate, message):
        if message == "Owned editor normal activation did not receive foreground":
            polls.append(predicate())
            assert polls == [False]
            f.foreground[0] = 101
            f.windows[:] = [f.window, f.cover, f.windows[-1]]
            polls.append(predicate())
            assert polls == [False, True]
            return True
        return waiting(predicate, message)

    f.native.wait_for.side_effect = wait
    journal = {}
    mod.focus_editor(f.native, f.window, owned=f.owner, journal=journal)
    f.activation.perform.assert_called_once_with(f.snapshot, "activate")
    row = journal["attempts"][0]["owned_activation"]
    assert row["foreground_immediate"] == 999 and row["foreground_after"] == 101
    assert row["identity_before"] == row["identity_after"]
    f.native.user.SetWindowPos.assert_not_called()
    assert f.native.click.call_count == 1


def test_normal_activation_false_success_expires_existing_wait_budget_without_fallback(
    exposure, monkeypatch
):
    from docking.platform.backends.base import ActionResult
    from tools.windows.visual import desktop_driver

    f = exposure
    now = [0.0]
    monkeypatch.setattr(desktop_driver.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        desktop_driver.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    f.activation.perform.side_effect = None
    f.activation.perform.return_value = ActionResult.OK
    f.native.wait_for.side_effect = desktop_driver.WindowsDesktop.wait_for
    journal = {}
    with pytest.raises(
        AssertionError, match="normal activation did not receive foreground"
    ):
        mod.focus_editor(f.native, f.window, owned=f.owner, journal=journal)
    assert 15 <= now[0] < 15.2
    f.activation.perform.assert_called_once_with(f.snapshot, "activate")
    assert journal["attempts"][0]["owned_activation"]["foreground_after"] == 999
    f.native.user.SetWindowPos.assert_not_called()
    f.native.click.assert_not_called()


def test_normal_activation_wait_stops_if_owned_identity_changes(exposure):
    from docking.platform.backends.base import ActionResult

    f = exposure
    f.activation.perform.side_effect = None
    f.activation.perform.return_value = ActionResult.OK

    def wait(predicate, _message):
        assert predicate() is False
        f.created[0] = 124
        predicate()
        pytest.fail("Changed target must stop the accepted activation wait")

    f.native.wait_for.side_effect = wait
    with pytest.raises(AssertionError, match="creation time changed"):
        mod.focus_editor(f.native, f.window, owned=f.owner)
    f.activation.perform.assert_called_once()
    f.native.user.SetWindowPos.assert_not_called()
    f.native.click.assert_not_called()
