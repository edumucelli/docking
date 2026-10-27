"""Shell-link ABI, atomic replacement and read-only metadata lookup."""

import ctypes
import sys
from contextlib import contextmanager

import pytest

from docking.platform.backends.windows import shortcuts


@pytest.fixture
def link(monkeypatch):
    events = []

    @contextmanager
    def context():
        events.append("open")
        try:
            yield ctypes.c_void_p(11), ctypes.c_void_p(22)
        finally:
            events.append("release")

    monkeypatch.setattr(shortcuts, "_link", context)
    return events


@pytest.mark.parametrize("failure", [7, 9, 17, 20, "save"])
def test_failed_save_preserves_the_previous_launcher_and_removes_temporary_files(
    tmp_path, monkeypatch, link, failure
):
    path = tmp_path / "app.lnk"
    path.write_bytes(b"original")

    def method(pointer, index, *_signature):
        def invoke(*_arguments):
            link.append(index)
            return (
                -1
                if index == failure or (pointer.value == 22 and failure == "save")
                else 0
            )

        return invoke

    monkeypatch.setattr(shortcuts, "method", method)
    with pytest.raises(OSError):
        shortcuts.write_shortcut(path, executable=tmp_path / "app.exe", name="app")
    assert path.read_bytes() == b"original"
    assert {entry.resolve() for entry in tmp_path.iterdir()} == {path.resolve()}
    assert link[-1] == "release"


def test_reads_stored_unicode_target_without_resolving_or_running_it(
    tmp_path, monkeypatch, link
):
    target = tmp_path / "résumé 日本語.exe"
    calls = []

    def method(pointer, index, *_signature):
        def invoke(_this, *args):
            calls.append((pointer.value, index))
            if pointer.value == 11:
                args[0].value = {3: str(target), 8: str(tmp_path), 10: ""}[index]
                if index == 3:
                    assert args[2] is None and args[3] == 4
            return 0

        return invoke

    monkeypatch.setattr(shortcuts, "method", method)
    result = shortcuts.read_shortcut(tmp_path / "app.lnk")
    assert result == shortcuts.ShellLink(target, str(tmp_path), "")
    assert calls == [(22, 5), (11, 3), (11, 8), (11, 10)]
    assert link[-1] == "release"


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Shell-link ABI")
def test_native_shortcut_round_trip_preserves_unicode_and_directory(tmp_path):
    executable = tmp_path / "résumé 日本語.exe"
    executable.write_bytes(b"fixture")
    path = tmp_path / "launcher 日本語.lnk"
    for _ in range(3):
        shortcuts.write_shortcut(path, executable=executable, name="résumé 日本語")
        result = shortcuts.read_shortcut(path)
        assert result.executable == executable
        assert result.working_directory.replace("/", "\\") == str(tmp_path).replace(
            "/", "\\"
        )
        assert result.arguments == ""
    assert {entry.resolve() for entry in tmp_path.iterdir()} == {
        path.resolve(),
        executable.resolve(),
    }
