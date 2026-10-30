"""Selected-file identity, literal Shell arguments and native COM ownership."""

import ctypes
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.applications import launcher as launcher_module
from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.launched_process import LaunchOutcome
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.windows.launch import WindowsApplicationLaunchBackend
from docking.platform.backends.windows import documents
from tests.platform.application_fakes import ApplicationRegistryStub, application


@pytest.fixture
def paths(monkeypatch):
    known = {
        "file:///C:/Documents/r%C3%A9sum%C3%A9.txt": Path("C:/Documents/résumé.txt"),
        "file://fileserver/team/second%20document.txt": Path(
            "//fileserver/team/second document.txt"
        ),
    }
    monkeypatch.setattr(documents, "local_path_from_uri_or_path", known.get)
    return tuple(known)


@pytest.mark.parametrize("msys_pathlib", [False, True])
def test_native_link_uses_exact_selected_identity_and_all_literal_paths(
    paths, monkeypatch, msys_pathlib
):
    if msys_pathlib:

        class ForwardWindowsPath(documents.PureWindowsPath):
            def __str__(self):
                return super().__str__().replace("\\", "/")

        monkeypatch.setattr(documents, "PureWindowsPath", ForwardWindowsPath)
    target = "C:/Pins/résumé & #100%.lnk"
    invoke = Mock(return_value=LaunchOutcome(True))
    monkeypatch.setattr(documents, "launch_shortcut", invoke)

    assert documents.launch_application_uris(target, paths) == LaunchOutcome(True)

    invoke.assert_called_once_with(
        target,
        arguments=(
            r"C:\Documents\résumé.txt",
            r"\\fileserver\team\second document.txt",
        ),
    )


def test_native_arguments_preserve_literal_names_and_protocol_urls(monkeypatch):
    native = (
        r"C:\Documents\résumé & #100% \literal 'quotes'.txt",
        r"\\fileserver\team\second document.txt",
    )
    protocol = "https://example.com/a%2Fb?path=C:/literal#fragment"
    monkeypatch.setattr(
        documents,
        "local_path_from_uri_or_path",
        lambda value: Path(value) if value in native else None,
    )
    invoke = Mock(return_value=LaunchOutcome(True))
    monkeypatch.setattr(documents, "launch_shortcut", invoke)
    target = r"C:\Pins\Selected.lnk"

    assert documents.launch_application_uris(target, (*native, protocol)).started

    invoke.assert_called_once_with(target, arguments=(*native, protocol))


@pytest.mark.parametrize(
    "uris",
    [
        (),
        ("",),
        ("file:relative.txt",),
        ("file:///C:/bad\0.txt",),
        ("javascript:alert(1)",),
        ("http://[broken",),
    ],
)
def test_invalid_selection_never_launches_or_partially_opens(uris, paths, monkeypatch):
    invoke = Mock()
    monkeypatch.setattr(documents, "launch_shortcut", invoke)

    assert documents.launch_application_uris(
        r"C:\Pins\Selected.lnk", uris
    ) == LaunchOutcome(False)
    invoke.assert_not_called()


def test_native_failure_is_final_and_cannot_launch_the_default_app(paths, monkeypatch):
    invoke = Mock(side_effect=OSError(5, "Denied"))
    monkeypatch.setattr(documents, "launch_shortcut", invoke)
    assert documents.launch_application_uris(
        r"C:\Pins\Selected.lnk", paths
    ) == LaunchOutcome(False)
    invoke.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_selected_windows_pin_overrides_gio_and_records_only_owned_provenance(
    monkeypatch, success
):
    from dataclasses import replace

    selected = replace(
        application("Native.App"),
        launch_target=r"C:\Pins\Selected.lnk",
        executable_path=Path("C:/Selected.exe"),
    )
    registry = ApplicationRegistryStub((selected,))
    handle = Mock()
    registry._gio_handle_for = Mock(return_value=handle)
    process = SimpleNamespace(pid=42, poll=lambda: None) if success else None
    invoke = Mock(return_value=LaunchOutcome(success, process))
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    monkeypatch.setattr(documents, "launch_application_uris", invoke)
    store = LaunchProvenanceStore()
    launcher = ApplicationLauncher(registry, store)
    uris = ["file:///C:/Documents/one.txt", "file:///C:/Documents/two.txt"]

    assert launcher.launch_app_uris(selected.desktop_id, uris) is success

    invoke.assert_called_once_with(selected.launch_target, uris)
    registry._gio_handle_for.assert_not_called()
    handle.launch_uris.assert_not_called()
    provenance = store.provenance_for_pid(42)
    assert (provenance.desktop_id if provenance is not None else None) == (
        selected.desktop_id if success else None
    )


def test_native_only_windows_pin_does_not_require_a_gio_handle(monkeypatch):
    from dataclasses import replace

    selected = replace(
        application("Native.App"),
        launch_target=r"C:\Pins\Selected.lnk",
        has_gio_source=False,
    )
    registry = ApplicationRegistryStub((selected,))
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    monkeypatch.setattr(
        documents, "launch_application_uris", lambda _target, _uris: LaunchOutcome(True)
    )
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore())
    assert launcher.launch_app_uris(selected.desktop_id, ["file:///C:/one.txt"])


def test_installed_classic_files_use_prepared_source_without_changing_normal_activation(
    monkeypatch,
):
    from dataclasses import replace

    selected = replace(
        application("Native.App"),
        launch_target=r"shell:AppsFolder\Native.App",
        document_target=r"C:\cache\source-options.lnk",
        has_gio_source=False,
    )
    registry = ApplicationRegistryStub((selected,))
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    invoke = Mock(return_value=LaunchOutcome(True))
    monkeypatch.setattr(documents, "launch_application_uris", invoke)
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore())
    assert launcher.launch_app_uris(selected.desktop_id, ["file:///C:/one.txt"])
    invoke.assert_called_once_with(selected.document_target, ["file:///C:/one.txt"])
    assert (
        registry.get(selected.desktop_id).launch_target
        == r"shell:AppsFolder\Native.App"
    )


@pytest.mark.parametrize("failure", [None, "parse", "array", "null"])
def test_shell_item_array_releases_partial_pidls_and_interfaces(monkeypatch, failure):
    released, freed, calls = [], [], []
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )
    monkeypatch.setattr(
        documents, "release", lambda pointer: released.append(pointer.value)
    )

    def bind(_library, name, *_signature):
        if name == "CoTaskMemFree":
            return lambda pointer: freed.append(pointer.value)
        if name == "SHParseDisplayName":

            def parse(target, _context, output, _attributes, _requested):
                calls.append(target)
                output._obj.value = len(calls) + 10
                return -2147024891 if failure == "parse" and len(calls) == 2 else 0

            return parse
        assert name == "SHCreateShellItemArrayFromIDLists"

        def array(count, identifiers, output):
            assert count == 2 and tuple(identifiers) == (11, 12)
            output._obj.value = 0 if failure == "null" else 22
            return -2147024891 if failure == "array" else 0

        return array

    monkeypatch.setattr(documents, "bind", bind)
    with ExitStack() as stack:
        if failure is not None:
            with pytest.raises(OSError):
                documents._items(stack, object(), (r"C:\one.txt", r"C:\two.txt"))
        else:
            assert (
                documents._items(stack, object(), (r"C:\one.txt", r"C:\two.txt")).value
                == 22
            )
    assert freed == [12, 11]
    assert released == ([22] if failure in (None, "array") else [])


@pytest.mark.parametrize("failure", [None, "create", "activate"])
@pytest.mark.parametrize("files", [True, False])
def test_packaged_activation_uses_selected_contract_and_releases_objects(
    monkeypatch, failure, files
):
    released, calls = [], []

    @contextmanager
    def apartment():
        yield object()

    def items(stack, _ole, targets):
        assert targets == (
            (r"C:\one.txt", r"C:\two.txt") if files else ("https://example.com/one",)
        )
        pointer = ctypes.c_void_p(22)
        stack.callback(lambda: released.append(pointer.value))
        return pointer

    def bind(_library, name, *_signature):
        if name == "CoAllowSetForegroundWindow":
            return lambda manager, _reserved: calls.append((name, manager.value)) or 0
        assert name == "CoCreateInstance"

        def create(_class_id, _outer, context, _interface_id, output):
            assert context == 4
            output._obj.value = 33
            return -2147024891 if failure == "create" else 0

        return create

    def method(pointer, index, _result, *signature):
        assert pointer.value == 33 and index == (4 if files else 5)
        assert signature[-1] == ctypes.POINTER(ctypes.c_uint32)

        def invoke(_this, application_id, selected_items, *arguments):
            assert application_id == "Native.Package!App" and selected_items.value == 22
            assert arguments[:-1] == (("open",) if files else ())
            calls.append(("activate", index))
            arguments[-1]._obj.value = 42
            return -2147024891 if failure == "activate" else 0

        return invoke

    monkeypatch.setattr(documents, "apartment", apartment)
    monkeypatch.setattr(documents, "_items", items)
    monkeypatch.setattr(documents, "bind", bind)
    monkeypatch.setattr(documents, "method", method)
    monkeypatch.setattr(
        documents, "release", lambda pointer: released.append(pointer.value)
    )
    selected = (
        ((r"C:\one.txt", True), (r"C:\two.txt", True))
        if files
        else (("https://example.com/one", False),)
    )
    if failure is not None:
        with pytest.raises(OSError):
            documents._activate_packaged("Native.Package!App", selected)
    else:
        assert documents._activate_packaged(
            "Native.Package!App", selected
        ) == LaunchOutcome(True)
    assert released == [33, 22]
    assert (
        ("activate", 4 if files else 5) in calls if failure != "create" else not calls
    )


@pytest.mark.parametrize(
    "selection",
    [
        ((r"C:\one.txt", True), ("https://example.com/one", False)),
        (("https://example.com/one", False), ("https://example.com/two", False)),
    ],
)
def test_packaged_mixed_or_multiple_protocol_selection_cannot_partially_activate(
    selection, monkeypatch
):
    apartment = Mock(side_effect=AssertionError("No native partial invocation"))
    monkeypatch.setattr(documents, "apartment", apartment)
    assert documents._activate_packaged(
        "Native.Package!App", selection
    ) == LaunchOutcome(False)
    apartment.assert_not_called()
