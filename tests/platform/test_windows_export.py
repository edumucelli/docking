"""Native launcher export identity, cache generations and exact binary copies."""

from dataclasses import replace
from pathlib import Path

import pytest

from docking.platform.applications import windows_export as export
from tests.platform.application_fakes import application


@pytest.fixture(autouse=True)
def native_file_formats(monkeypatch):
    monkeypatch.setattr(
        export, "file_drag_formats", lambda _path: (("Native file data", b"file-pidl"),)
    )


def test_shell_export_keeps_identity_and_uses_a_friendly_unicode_filename(
    tmp_path, monkeypatch
):
    calls = []

    def write(path, **kwargs):
        calls.append(kwargs)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"native namespace shortcut")

    monkeypatch.setattr(export, "write_application_shortcut", write)
    original = replace(
        application("Example.Package!App"),
        name="résumé 日本語",
        launch_target=r"shell:AppsFolder\Example.Package!App",
    )
    exporter = export.WindowsApplicationExport(tmp_path)
    prepared = exporter.prepare((original,))[0]
    assert prepared.desktop_id == original.desktop_id
    assert prepared.export_file.name == "résumé 日本語.lnk"
    assert prepared.export_file.read_bytes() == b"native namespace shortcut"
    assert calls == [
        {
            "target": original.launch_target,
            "application_id": original.desktop_id,
            "name": original.name,
        }
    ]
    assert exporter.prepare((original,))[0] == prepared
    assert len(calls) == 1
    assert original.export_file is None


def test_export_preserves_selected_binary_and_refreshes_changed_generations(
    tmp_path, monkeypatch
):
    copy = export.shutil.copyfile
    data = tmp_path / "original.bin"

    def copy_native(source, destination):
        assert source == Path(selected.launch_target)
        return copy(data, destination)

    monkeypatch.setattr(export.shutil, "copyfile", copy_native)
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    previous = None
    for version in range(2):
        data.write_bytes(bytes(range(256)) + str(version).encode())
        selected = replace(
            application("Native.App"),
            name="Chosen profile",
            launch_target=rf"C:\config\profile-{version}.lnk",
        )
        prepared = exporter.prepare((selected,))[0]
        assert prepared.export_file.read_bytes() == data.read_bytes()
        assert prepared.export_file.name == "Chosen profile.lnk"
        if previous is not None:
            assert previous.export_file != prepared.export_file
            assert previous.export_file.is_file()
        previous = prepared


@pytest.mark.parametrize(
    "name", ["../folder\\name:*?", "CON", "nul.txt", "LPT1", "COM¹", " . ", "😀" * 100]
)
def test_export_filename_remains_a_single_valid_windows_component(name):
    filename = export._filename(name)
    assert filename.endswith(".lnk")
    assert not any(c in filename for c in '<>:"/\\|?*')
    assert len(filename.encode("utf-16-le")) <= 170
    assert filename.upper().split(".")[0] not in {"CON", "NUL", "LPT1", "COM¹"}


def test_failed_export_keeps_the_application_launchable_without_a_drag_file(
    tmp_path, monkeypatch
):
    def denied(*_a, **_kw):
        raise PermissionError("Read-only cache")

    monkeypatch.setattr(export, "write_application_shortcut", denied)
    original = replace(
        application("Native.App"), launch_target=r"shell:AppsFolder\Native.App"
    )
    assert export.WindowsApplicationExport(tmp_path).prepare((original,)) == (original,)
    assert tuple(tmp_path.iterdir()) == ()
