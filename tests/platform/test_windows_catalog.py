"""Native application-folder records and ownership of shell allocations."""

import ctypes
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from docking.platform.backends.windows import catalog
from tests.platform.application_fakes import application


def _shell(monkeypatch, *, fail_next=False, fail_string=False):
    rows = (
        ("Example.Classic", "Same name", r"C:\Program Files\Example\one.exe", False),
        ("Example.Hidden", "Hidden", "", True),
        ("Example.Other", "Same name", "::{shell-target}", False),
    )
    released, freed, allocations = [], [], []
    sequence = iter(range(len(rows)))
    monkeypatch.setattr(catalog, "apartment", lambda: nullcontext(object()))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: object(), raising=False)
    monkeypatch.setattr(catalog, "release", lambda p: released.append(p.value))

    def bind(_dll, name, *_types):
        if name == "SHGetKnownFolderItem":

            def folder(_id, _flags, _token, _iid, output):
                output._obj.value = 100
                return 0

            return folder
        assert name == "CoTaskMemFree"
        return lambda p: freed.append(p.value)

    def objects(stack, pointer, index, *_args):
        assert pointer.value == 100 and index == 3
        result = ctypes.c_void_p(200)
        stack.callback(catalog.release, result)
        return result

    def query(stack, pointer, _interface):
        result = ctypes.c_void_p(pointer.value + 1000)
        stack.callback(catalog.release, result)
        return result

    def method(pointer, index, *_types):
        def invoke(_self, *args):
            if pointer.value == 200:
                try:
                    row = next(sequence)
                except StopIteration:
                    return 1
                args[1]._obj.value = 300 + row
                args[2]._obj.value = 1
                return -1 if fail_next and row == 1 else 0
            row = rows[(pointer.value % 1000) - 300]
            if index == 6:
                args[1]._obj.value = 0x80000 if row[3] else 0
                return 0
            if index == 5:
                text = row[1]
            else:
                assert index == 17
                text = row[0] if args[0]._obj.id == 5 else row[2]
            buffer = ctypes.create_unicode_buffer(text)
            allocations.append(buffer)
            args[-1]._obj.value = ctypes.addressof(buffer)
            return -1 if fail_string else 0

        return invoke

    monkeypatch.setattr(catalog, "bind", bind)
    monkeypatch.setattr(catalog, "object_result", objects)
    monkeypatch.setattr(catalog, "query", query)
    monkeypatch.setattr(catalog, "method", method)
    return released, freed, allocations


def test_catalog_preserves_ids_and_distinct_same_name_items_and_frees_resources(
    monkeypatch,
):
    released, freed, allocations = _shell(monkeypatch)
    rows = catalog.read_shell_applications()
    assert [r.desktop_id for r in rows] == ["Example.Classic", "Example.Other"]
    assert rows[0].name == rows[1].name == "Same name"
    assert rows[0].executable_path == Path(r"C:\Program Files\Example\one.exe")
    assert rows[0].launch_target == r"shell:AppsFolder\Example.Classic"
    assert rows[1].executable_path is None
    assert not rows[0].has_gio_source
    assert len(released) == len(set(released)) == 7
    assert released[-2:] == [200, 100]
    assert set(freed) == {ctypes.addressof(b) for b in allocations}


def test_failed_enumeration_discards_partial_catalog_and_releases_returned_item(
    monkeypatch,
):
    released, freed, allocations = _shell(monkeypatch, fail_next=True)
    with pytest.raises(OSError, match="enumerate applications"):
        catalog.read_shell_applications()
    assert set(released) == {100, 200, 300, 301, 1300}
    assert set(freed) == {ctypes.addressof(b) for b in allocations}


def test_failed_property_read_still_frees_the_returned_string_and_interfaces(
    monkeypatch,
):
    released, freed, allocations = _shell(monkeypatch, fail_string=True)
    with pytest.raises(OSError, match="metadata"):
        catalog.read_shell_applications()
    assert set(released) == {100, 200, 300, 1300}
    assert set(freed) == {ctypes.addressof(b) for b in allocations}


def test_metadata_filters_explicit_hidden_entries_in_both_sources(monkeypatch):
    visible, hidden = application("Main"), application("Widget")
    monkeypatch.setattr(catalog, "read_shell_applications", lambda: (visible, hidden))
    requested = []
    monkeypatch.setattr(
        catalog,
        "hidden_application_ids",
        lambda ids: requested.append(set(ids)) or frozenset(("Widget",)),
    )
    result = catalog.read_application_metadata(("GioOnly",))
    assert requested == [{"Main", "Widget", "GioOnly"}]
    assert result.applications == (visible,)
    assert result.hidden_ids == {"Widget"}


def test_unavailable_catalog_is_distinct_from_a_successful_empty_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "hidden_application_ids", lambda _ids: frozenset())

    def failed():
        raise OSError("Shell restarting")

    monkeypatch.setattr(catalog, "read_shell_applications", failed)
    assert catalog.read_application_metadata(()).applications is None
    monkeypatch.setattr(catalog, "read_shell_applications", lambda: ())
    assert catalog.read_application_metadata(()).applications == ()


def test_exports_use_selected_registration_and_never_prepare_hidden_components(
    monkeypatch,
):
    from docking.platform.applications.windows_export import WindowsApplicationExport

    visible, hidden = application("Main"), application("Widget")
    selected = replace(
        visible,
        name="Selected profile",
        launch_target=r"C:\private\selected.lnk",
        working_directory=r"C:\profile",
    )
    prepared = []
    monkeypatch.setattr(catalog, "read_shell_applications", lambda: (visible, hidden))
    monkeypatch.setattr(
        catalog, "hidden_application_ids", lambda _ids: frozenset(("Widget",))
    )

    def prepare(_self, apps, *, cancelled):
        assert not cancelled()
        prepared.extend(apps)
        return tuple(replace(app, export_file=Path("prepared.lnk")) for app in apps)

    monkeypatch.setattr(WindowsApplicationExport, "prepare", prepare)
    result = catalog.read_application_metadata((), registrations=(selected,))
    assert len(prepared) == 1
    assert prepared[0].desktop_id == "Main"
    assert prepared[0].launch_target == selected.launch_target
    assert prepared[0].working_directory == selected.working_directory
    assert result.applications[0].export_file == Path("prepared.lnk")


@pytest.mark.parametrize("stage", ["catalog", "hidden", "sources", "documents"])
def test_stop_between_native_catalog_phases_prevents_subsequent_export_work(
    monkeypatch, stage
):
    from threading import Event
    from unittest.mock import Mock

    from docking.platform.applications.windows_export import WindowsApplicationExport

    closed = Event()
    visible = replace(
        application("Native.App"), launch_target=r"shell:AppsFolder\Native.App"
    )

    def finish(phase, value):
        if phase == stage:
            closed.set()
        return value

    monkeypatch.setattr(
        catalog, "read_shell_applications", lambda: finish("catalog", (visible,))
    )
    monkeypatch.setattr(
        catalog, "hidden_application_ids", lambda _ids: finish("hidden", frozenset())
    )
    monkeypatch.setattr(
        catalog,
        "read_shortcut_sources",
        lambda _ids, *, cancelled: finish("sources", {}),
    )
    monkeypatch.setattr(
        WindowsApplicationExport,
        "prepare_documents",
        lambda _self, apps, _sources, *, cancelled: finish("documents", apps),
    )
    export = Mock(side_effect=AssertionError("No native export after stop"))
    monkeypatch.setattr(WindowsApplicationExport, "prepare", export)
    result = catalog.read_application_metadata((), cancelled=closed.is_set)
    assert closed.is_set() and result.applications is None
    export.assert_not_called()
