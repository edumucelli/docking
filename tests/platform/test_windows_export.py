"""Native launcher export identity, cache generations and exact binary copies."""

from dataclasses import replace
from pathlib import Path

import pytest

from docking.platform.applications.implementations.windows import export as export
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


def test_catalog_document_snapshot_tracks_content_and_outlives_source_changes(tmp_path):
    original = replace(
        application("Native.App"), launch_target=r"shell:AppsFolder\Native.App"
    )
    source = tmp_path / "Installed.lnk"
    source.write_bytes(b"original binary including arguments/cwd/options")
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    sources = {original.desktop_id: ((source, source.stat()),)}
    first = exporter.prepare_documents((original,), sources, cancelled=lambda: False)[0]
    snapshot = Path(first.document_target)
    assert snapshot.read_bytes() == source.read_bytes()
    assert first.launch_target == original.launch_target
    assert exporter.prepare((first,))[0].export_file == snapshot
    source.write_bytes(b"replacement native options")
    second = exporter.prepare_documents(
        (original,),
        {original.desktop_id: ((source, source.stat()),)},
        cancelled=lambda: False,
    )[0]
    assert second.document_target != first.document_target
    source.unlink()
    assert snapshot.read_bytes() == b"original binary including arguments/cwd/options"
    assert Path(second.document_target).read_bytes() == b"replacement native options"
    assert not tuple((tmp_path / "cache").glob(".snapshot-*"))


@pytest.mark.parametrize("same", [False, True])
def test_exact_id_with_differing_shortcut_options_is_ambiguous(tmp_path, same):
    original = application("Native.App")
    one, two = tmp_path / "One.lnk", tmp_path / "Two.lnk"
    one.write_bytes(b"same native options")
    two.write_bytes(one.read_bytes() if same else b"different profile arguments")
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    sources = {original.desktop_id: ((one, one.stat()), (two, two.stat()))}
    prepared = exporter.prepare_documents(
        (original,), sources, cancelled=lambda: False
    )[0]
    assert bool(prepared.document_target) is same
    assert not tuple((tmp_path / "cache").glob(".snapshot-*"))


def test_replaced_source_or_cancel_cannot_publish_a_document_target(tmp_path):
    original = application("Native.App")
    source = tmp_path / "Installed.lnk"
    source.write_bytes(b"first")
    sources = {original.desktop_id: ((source, source.stat()),)}
    source.write_bytes(b"replacement file with a different identity/options")
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    assert exporter.prepare_documents(
        (original,), sources, cancelled=lambda: False
    ) == (original,)
    assert exporter.prepare_documents((original,), sources, cancelled=lambda: True) == (
        original,
    )
    assert not tuple((tmp_path / "cache").glob(".snapshot-*"))


def test_cancel_during_copy_closes_and_removes_the_partial_snapshot(tmp_path):
    original = application("Native.App")
    source = tmp_path / "Installed.lnk"
    source.write_bytes(b"native options")
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    calls = iter((False, True))
    result = exporter.prepare_documents(
        (original,),
        {original.desktop_id: ((source, source.stat()),)},
        cancelled=lambda: next(calls),
    )
    assert result == (original,)
    assert tuple((tmp_path / "cache").iterdir()) == ()


@pytest.mark.parametrize("stage", ["before", "file", "formats"])
def test_export_stop_prevents_any_further_file_or_native_format_work(
    tmp_path, monkeypatch, stage
):
    from threading import Event

    closed = Event()
    calls = []
    applications = (application("One.App"), application("Two.App"))
    exporter = export.WindowsApplicationExport(tmp_path / "cache")

    def file(app):
        calls.append(("file", app.desktop_id))
        if stage == "file":
            closed.set()
        return tmp_path / "owned.lnk"

    def formats(_path):
        calls.append(("formats", "One.App"))
        if stage == "formats":
            closed.set()
        return (("CF_HDROP", b"owned file"),)

    monkeypatch.setattr(exporter, "_file", file)
    monkeypatch.setattr(export, "file_drag_formats", formats)
    if stage == "before":
        closed.set()
    assert exporter.prepare(applications, cancelled=closed.is_set) == applications
    expected = [] if stage == "before" else [("file", "One.App")]
    if stage == "formats":
        expected.append(("formats", "One.App"))
    assert calls == expected


def test_unchanged_export_reuses_owned_format_bytes_and_refreshes_changed_file(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.lnk"
    source.write_bytes(b"first native options")
    app = replace(application("Native.App"), document_target=str(source))
    calls = []

    def formats(path):
        calls.append(path)
        return (("Native file data", path.read_bytes()),)

    monkeypatch.setattr(export, "file_drag_formats", formats)
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    first = exporter.prepare((app,))
    assert exporter.prepare((app,)) == first and len(calls) == 1
    source.write_bytes(b"replacement native binary and options")
    second = exporter.prepare((app,))
    assert len(calls) == 2 and second[0].export_data != first[0].export_data
    source.unlink()
    assert exporter.prepare((app,))[0].export_file is None


def test_prepared_cache_is_bounded_and_metadata_changes_cannot_reuse_another_entry(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.lnk"
    source.write_bytes(b"native options")
    app = replace(application("Native.App"), document_target=str(source))
    renamed = replace(app, name="Renamed")
    monkeypatch.setattr(export, "_PREPARED_LIMIT", 1)
    calls = []
    monkeypatch.setattr(
        export,
        "file_drag_formats",
        lambda path: calls.append(path) or (("Native file data", b"pidl"),),
    )
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    exporter.prepare((app,))
    exporter.prepare((renamed,))
    exporter.prepare((app,))
    assert len(calls) == 3 and len(exporter._prepared) == 1


def test_transient_native_export_failure_is_not_cached(tmp_path, monkeypatch):
    source = tmp_path / "source.lnk"
    source.write_bytes(b"native options")
    app = replace(application("Native.App"), document_target=str(source))
    calls = []

    def formats(_path):
        calls.append(True)
        if len(calls) == 1:
            raise OSError(5, "Unavailable Shell")
        return (("Native file data", b"pidl"),)

    monkeypatch.setattr(export, "file_drag_formats", formats)
    exporter = export.WindowsApplicationExport(tmp_path / "cache")
    assert exporter.prepare((app,))[0].export_file is None
    assert exporter.prepare((app,))[0].export_data
    assert len(calls) == 2
