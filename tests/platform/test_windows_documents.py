"""Selected-file identity, literal Shell arguments and native COM ownership."""

import ctypes
from collections import Counter
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
from docking.platform.backends.windows import com, documents
from docking.platform.backends.windows.dataobject import file_drop_bytes
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


def test_native_application_identity_cannot_be_truncated_at_a_nul(monkeypatch):
    invoke = Mock()
    monkeypatch.setattr(documents, "_activate_packaged", invoke)
    assert not documents.launch_application_uris(
        "shell:AppsFolder\\Owned.Package!App\0Other", ["file:///C:/Owned/one.txt"]
    ).started
    invoke.assert_not_called()


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


@pytest.mark.parametrize(
    "failure", [None, "parse", "array", "null", "parse-cancel", "array-cancel"]
)
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
                if failure == "parse-cancel" and len(calls) == 2:
                    raise KeyboardInterrupt
                return -2147024891 if failure == "parse" and len(calls) == 2 else 0

            return parse
        assert name == "SHCreateShellItemArrayFromIDLists"

        def array(count, identifiers, output):
            assert count == 2 and tuple(identifiers) == (11, 12)
            output._obj.value = 0 if failure == "null" else 22
            if failure == "array-cancel":
                raise KeyboardInterrupt
            return -2147024891 if failure == "array" else 0

        return array

    monkeypatch.setattr(documents, "bind", bind)
    with ExitStack() as stack:
        if failure is not None:
            expected = KeyboardInterrupt if failure.endswith("-cancel") else OSError
            with pytest.raises(expected):
                documents._items(stack, object(), (r"C:\one.txt", r"C:\two.txt"))
        else:
            assert (
                documents._items(stack, object(), (r"C:\one.txt", r"C:\two.txt")).value
                == 22
            )
    assert freed == [12, 11]
    assert released == ([22] if failure in (None, "array", "array-cancel") else [])


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


class _Handlers:
    """Model native reference/allocation ownership, including failed out-params."""

    def __init__(
        self, monkeypatch, identities=("Other.Package!App", "Owned.Package!App")
    ):
        self.identities = identities
        self.by_query = {}
        self.current_identities = identities
        self.current_query = None
        self.position = 0
        self.next_shape = None
        self.failure = None
        self.cancel = None
        self.null_stage = None
        self.supported = 0
        self.supported_by_query = {}
        self.create_status = 0
        self.create_status_by_query = {}
        self.invoke_status = 0
        self.appid_status = 0
        self.appid_status_by_index = {}
        self.appid_null_indices = set()
        self.enum_calls, self.formats, self.events = [], [], []
        self.selections = []
        self.create_requests, self.support_requests, self.invocations = [], [], []
        self.created, self.released, self.allocated, self.freed = (
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
        self.buffers = []
        monkeypatch.setattr(
            ctypes, "WinDLL", lambda *_a, **_kw: object(), raising=False
        )
        monkeypatch.setattr(documents, "apartment", lambda: self.apartment())
        monkeypatch.setattr(documents, "cached_data_object", self.data)
        monkeypatch.setattr(documents, "shell_selection_formats", self.selection)
        monkeypatch.setattr(documents, "bind", self.bind)
        monkeypatch.setattr(documents, "method", self.method)
        monkeypatch.setattr(com, "method", self.method)

    @contextmanager
    def apartment(self):
        yield object()

    @contextmanager
    def data(self, formats):
        self.formats.append(formats)
        self.events.append("data-open")
        try:
            yield ctypes.c_void_p(4000)
        finally:
            self.events.append("data-close")

    def selection(self, targets, *, files):
        self.selections.append((targets, files))
        if self.status("selection"):
            raise OSError(5, "Owned selection preparation failed")
        formats = (("Shell IDList Array", b"owned-shell-selection"),)
        return (
            (*formats, ("CF_HDROP", file_drop_bytes(targets)))
            if files
            else (
                *formats,
                ("UniformResourceLocatorW", (targets[0] + "\0").encode("utf-16-le")),
            )
        )

    def output(self, pointer, address, stage):
        if self.null_stage == stage:
            return
        pointer._obj.value = address
        self.created[address] += 1

    def status(self, stage):
        if self.cancel == stage:
            raise KeyboardInterrupt
        return -2147024891 if self.failure == stage else 0

    def bind(self, _library, name, *_signature):
        if name == "CoTaskMemFree":
            return lambda value: self.freed.update((value.value,))
        assert name in (
            "SHAssocEnumHandlers",
            "SHAssocEnumHandlersForProtocolByApplication",
        ), "No executable, display-name or default-application lookup is allowed"

        def enumerate_handlers(key, option, output):
            if name == "SHAssocEnumHandlers":
                assert option == 0
            else:
                assert bytes(option._obj) == bytes(documents._ENUM_HANDLERS)
            self.enum_calls.append((name, key))
            self.current_query = key
            self.current_identities = self.by_query.get(key, self.identities)
            self.position = 0
            self.output(output, 100, "enum")
            return self.status("enum")

        return enumerate_handlers

    def method(self, pointer, slot, _result, *_signature):
        address = pointer.value

        def invoke(_this, *arguments):
            if slot == 2:
                self.released[address] += 1
                self.events.append(("release", address))
                return 0
            if address == 100:
                assert slot == 3 and arguments[0] == 1
                if self.next_shape is not None:
                    status, count, has_pointer = self.next_shape
                    if has_pointer:
                        self.output(arguments[1], 1000, "next")
                    arguments[2]._obj.value = count
                    self.position += 1
                    return status
                if self.position >= len(self.current_identities):
                    arguments[2]._obj.value = 0
                    return 1
                self.output(arguments[1], 1000 + self.position, "next")
                arguments[2]._obj.value = 1
                self.position += 1
                return self.status("next")
            if 1000 <= address < 2000:
                if slot == 0:
                    assert bytes(arguments[0]._obj) == bytes(documents._APP_ID)
                    self.output(arguments[1], address + 1000, "identity")
                    if self.current_identities[address - 1000] is None:
                        return -2147467262  # E_NOINTERFACE, unrelated handler.
                    return self.status("identity")
                assert slot == 9 and arguments[0].value == 4000
                self.create_requests.append((self.current_query, arguments[0].value))
                self.output(arguments[1], 3000, "create")
                return self.status("create") or self.create_status_by_query.get(
                    self.current_query, self.create_status
                )
            if 2000 <= address < 3000:
                assert slot == 4
                index = address - 2000
                if index not in self.appid_null_indices:
                    buffer = ctypes.create_unicode_buffer(
                        self.current_identities[index]
                    )
                    self.buffers.append(buffer)
                    allocated = ctypes.addressof(buffer)
                    arguments[0]._obj.value = allocated
                    self.allocated[allocated] += 1
                return self.status("appid") or self.appid_status_by_index.get(
                    index, self.appid_status
                )
            assert address == 3000
            if slot == 3:
                self.events.append("supports")
                self.support_requests.append(self.current_query)
                return self.status("supports") or self.supported_by_query.get(
                    self.current_query, self.supported
                )
            assert slot == 4
            self.events.append("invoke")
            self.invocations.append(self.current_query)
            return self.status("invoke") or self.invoke_status

        return invoke

    def balanced(self):
        assert self.created == self.released
        assert self.allocated == self.freed
        if self.formats:
            assert self.events[-1] != "data-open"
            assert self.events.count("data-open") == self.events.count("data-close")


@pytest.mark.parametrize(
    "selection,expected_query",
    [
        ((r"C:\Owned\résumé_日本語.txt",), ".txt"),
        ((r"C:\Owned\one.txt", r"C:\Owned\two.txt"), ".txt"),
        ((r"C:\First\one.txt", r"D:\Other folder\two & #100%.md"), ".md"),
        ((r"C:\First\README", r"D:\Other folder\two.txt"), ".txt"),
    ],
)
def test_registered_selection_uses_exact_id_and_one_complete_unicode_data_object(
    monkeypatch, selection, expected_query
):
    native = _Handlers(monkeypatch)
    outcome = documents._invoke_registered(
        "Owned.Package!App", tuple((path, True) for path in selection), True
    )
    assert outcome == LaunchOutcome(True)
    assert native.enum_calls == [("SHAssocEnumHandlers", expected_query)]
    assert native.selections == [(selection, True)]
    assert native.formats == [
        (
            ("Shell IDList Array", b"owned-shell-selection"),
            ("CF_HDROP", file_drop_bytes(selection)),
        )
    ]
    assert native.events.count("invoke") == 1
    assert native.events.index("supports") < native.events.index("invoke")
    assert native.events.index(("release", 3000)) < native.events.index("data-close")
    native.balanced()


def test_registered_protocol_uses_separate_enum_and_exact_unmodified_uri(monkeypatch):
    native = _Handlers(monkeypatch)
    uri = "https://example.com/r%C3%A9sum%C3%A9?q=100%25#literal"
    assert documents._invoke_registered(
        "Owned.Package!App", ((uri, False),), False
    ).started
    assert native.enum_calls == [
        ("SHAssocEnumHandlersForProtocolByApplication", "https")
    ]
    assert native.formats == [
        (
            ("Shell IDList Array", b"owned-shell-selection"),
            ("UniformResourceLocatorW", (uri + "\0").encode("utf-16-le")),
        )
    ]
    assert native.selections == [((uri,), False)]
    native.balanced()


@pytest.mark.parametrize("name", ("README", ".profile", "trailing."))
def test_extensionless_selection_never_queries_an_invalid_association(
    monkeypatch, name
):
    native = _Handlers(monkeypatch)
    assert not documents._invoke_registered(
        "Owned.Package!App", (("C:\\Owned\\" + name, True),), True
    ).started
    assert not native.enum_calls and not native.formats and not native.invocations
    native.balanced()


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("first", ("missing", "unsupported", "create-refused"))
def test_mixed_selection_order_cannot_hide_a_supported_exact_application_handler(
    monkeypatch, reverse, first
):
    native = _Handlers(monkeypatch, ("Owned.Package!App",))
    selection = (r"C:\First\unknown.bin", r"D:\Other\résumé_日本語.txt")
    if reverse:
        selection = tuple(reversed(selection))
    if first == "missing":
        native.by_query[".bin"] = ()
    elif first == "unsupported":
        native.supported_by_query[".bin"] = 1
    else:
        native.create_status_by_query[".bin"] = 1
    assert documents._invoke_registered(
        "Owned.Package!App", tuple((path, True) for path in selection), True
    ).started
    assert native.enum_calls == [
        ("SHAssocEnumHandlers", ".bin"),
        ("SHAssocEnumHandlers", ".txt"),
    ]
    assert native.selections == [(selection, True)]
    assert native.formats == [
        (
            ("Shell IDList Array", b"owned-shell-selection"),
            ("CF_HDROP", file_drop_bytes(selection)),
        )
    ]
    assert native.invocations == [".txt"]
    assert native.support_requests == (
        [".bin", ".txt"] if first == "unsupported" else [".txt"]
    )
    assert all(data == 4000 for _query, data in native.create_requests)
    native.balanced()


@pytest.mark.parametrize("status", (1, 2))
def test_positive_createinvoker_status_never_reaches_support_or_invoke(
    monkeypatch, status
):
    native = _Handlers(monkeypatch)
    native.create_status = status
    assert not documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert not native.support_requests and not native.invocations
    assert native.created[3000] == native.released[3000] == 1
    native.balanced()


def test_repeated_case_variant_extensions_share_one_query_without_changing_paths(
    monkeypatch,
):
    native = _Handlers(monkeypatch)
    selection = (r"C:\Owned\first.TXT", r"D:\Other\second.txt")
    assert documents._invoke_registered(
        "Owned.Package!App", tuple((path, True) for path in selection), True
    ).started
    assert native.enum_calls == [("SHAssocEnumHandlers", ".txt")]
    assert native.selections == [(selection, True)]
    assert native.formats == [
        (
            ("Shell IDList Array", b"owned-shell-selection"),
            ("CF_HDROP", file_drop_bytes(selection)),
        )
    ]
    native.balanced()


@pytest.mark.parametrize("status", (1, -2147024891))
def test_an_attempted_invoke_never_retries_another_extension(monkeypatch, status):
    native = _Handlers(monkeypatch, ("Owned.Package!App",))
    native.invoke_status = status
    selected = ((r"C:\Owned\one.bin", True), (r"D:\Other\two.txt", True))
    if status < 0:
        with pytest.raises(OSError):
            documents._invoke_registered("Owned.Package!App", selected, True)
    else:
        assert not documents._invoke_registered(
            "Owned.Package!App", selected, True
        ).started
    assert native.enum_calls == [("SHAssocEnumHandlers", ".bin")]
    assert native.invocations == [".bin"]
    native.balanced()


@pytest.mark.parametrize(
    "shape",
    [
        (0, 0, False),
        (0, 0, True),
        (1, 1, True),
        (1, 1, False),
        (1, 0, True),
        (2, 0, False),
        (0, 2, True),
        (0, 1, False),
    ],
)
def test_incoherent_next_status_count_or_pointer_is_rejected_and_released(
    monkeypatch, shape
):
    native = _Handlers(monkeypatch)
    native.next_shape = shape
    with pytest.raises(OSError, match="Invalid association handler result"):
        documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        )
    assert not native.formats and not native.invocations
    native.balanced()


@pytest.mark.parametrize(
    "identities",
    [
        (),
        ("Owned.Package!DifferentApp",),
        ("Different.Package!App",),
        ("Owned.Package!App", "Owned.Package!App"),
    ],
)
def test_missing_other_or_ambiguous_full_identity_cannot_create_an_invoker(
    monkeypatch, identities
):
    native = _Handlers(monkeypatch, identities)
    if len(identities) == 2:
        with pytest.raises(OSError, match="Ambiguous"):
            documents._invoke_registered(
                "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
            )
    else:
        assert not documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        ).started
    assert not native.formats and "invoke" not in native.events
    native.balanced()


@pytest.mark.parametrize("supported", (1, 2))
def test_unsupported_complete_selection_cannot_partially_invoke(monkeypatch, supported):
    native = _Handlers(monkeypatch)
    native.supported = supported  # Only S_OK proves support.
    assert not documents._invoke_registered(
        "Owned.Package!App",
        ((r"C:\Owned\one.txt", True), (r"D:\Other\two.bin", True)),
        True,
    ).started
    assert "invoke" not in native.events
    native.balanced()


@pytest.mark.parametrize("status", (1, 2))
def test_nonnegative_invoke_refusal_is_not_reported_as_started(monkeypatch, status):
    native = _Handlers(monkeypatch)
    native.invoke_status = status
    assert not documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert native.events.count("invoke") == 1
    native.balanced()


def test_explicit_identity_requires_a_successful_getappid_result(monkeypatch):
    native = _Handlers(monkeypatch)
    native.appid_status = 1
    assert not documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert not native.formats
    native.balanced()


@pytest.mark.parametrize("stage", ("enum", "next", "identity", "create"))
def test_successful_status_with_missing_interface_cannot_invoke(monkeypatch, stage):
    native = _Handlers(monkeypatch)
    native.null_stage = stage
    with pytest.raises(OSError):
        documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        )
    assert "invoke" not in native.events
    native.balanced()


def test_unterminated_handler_enumeration_is_bounded_before_any_invoke(monkeypatch):
    native = _Handlers(monkeypatch, (None,) * 256)
    with pytest.raises(OSError, match="exceeds the limit"):
        documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        )
    assert native.position == 256
    assert not native.formats
    native.balanced()


def test_handler_without_explicit_identity_cannot_be_selected_by_other_metadata(
    monkeypatch,
):
    native = _Handlers(monkeypatch, (None, "Owned.Package!App"))
    assert documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert native.events.count("invoke") == 1
    native.balanced()


@pytest.mark.parametrize("missing_first", (False, True))
@pytest.mark.parametrize("nonnull_output", (False, True))
def test_getappid_unassigned_identity_does_not_hide_the_exact_handler(
    monkeypatch, missing_first, nonnull_output
):
    native = _Handlers(monkeypatch, ("Owned.Package!App", "Owned.Package!App"))
    missing = 0 if missing_first else 1
    native.appid_status_by_index[missing] = ctypes.c_int32(0x80004005).value
    if not nonnull_output:
        native.appid_null_indices.add(missing)
    assert documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert native.invocations == [".txt"]
    assert sum(native.allocated.values()) == (2 if nonnull_output else 1)
    native.balanced()


@pytest.mark.parametrize("nonnull_output", (False, True))
def test_only_unassigned_getappid_handler_cannot_invoke(monkeypatch, nonnull_output):
    native = _Handlers(monkeypatch, ("Owned.Package!App",))
    native.appid_status_by_index[0] = ctypes.c_int32(0x80004005).value
    if not nonnull_output:
        native.appid_null_indices.add(0)
    assert not documents._invoke_registered(
        "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
    ).started
    assert not native.formats and not native.invocations
    native.balanced()


@pytest.mark.parametrize("status", (0x80070005, 0x80010108, 0x8000FFFF))
@pytest.mark.parametrize("nonnull_output", (False, True))
def test_other_getappid_failures_abort_and_free_their_outputs(
    monkeypatch, status, nonnull_output
):
    native = _Handlers(monkeypatch)
    native.appid_status_by_index[0] = ctypes.c_int32(status).value
    if not nonnull_output:
        native.appid_null_indices.add(0)
    with pytest.raises(OSError) as raised:
        documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        )
    assert raised.value.errno == status
    assert not native.formats and not native.invocations
    native.balanced()


@pytest.mark.parametrize(
    "stage",
    ("enum", "next", "identity", "appid", "selection", "create", "supports", "invoke"),
)
@pytest.mark.parametrize("cancelled", (False, True))
def test_handler_failure_or_interruption_releases_even_nonnull_failed_outputs(
    monkeypatch, stage, cancelled
):
    native = _Handlers(monkeypatch)
    if cancelled:
        native.cancel = stage
    else:
        native.failure = stage
    with pytest.raises(KeyboardInterrupt if cancelled else OSError):
        documents._invoke_registered(
            "Owned.Package!App", ((r"C:\Owned\one.txt", True),), True
        )
    if stage != "invoke":
        assert "invoke" not in native.events
    native.balanced()


@pytest.mark.parametrize("files", (True, False))
@pytest.mark.parametrize(
    "stage,status,fallback,started",
    [
        ("activate", 0, False, True),
        ("activate", 0x80270254, True, True),
        ("activate", 0x80070005, False, False),
        ("activate", 0x80270252, False, False),
        ("activate", 0x8027025A, False, False),
        ("create", 0x80270254, False, False),
        ("items", 0x80270254, False, False),
    ],
)
def test_only_missing_activation_contract_uses_the_same_identity_registered_route(
    monkeypatch, files, stage, status, fallback, started
):
    from contextlib import nullcontext

    released = []
    selected = (
        ((r"C:\Owned\one.txt", True),) if files else (("https://example.com", False),)
    )
    native_status = ctypes.c_int32(status).value

    def items(stack, _ole, _targets):
        stack.callback(lambda: released.append(22))
        if stage == "items":
            raise OSError(status, "Owned Shell item failure")
        return ctypes.c_void_p(22)

    def bind(_library, name, *_signature):
        if name == "CoAllowSetForegroundWindow":
            return lambda *_args: 0
        assert name == "CoCreateInstance"

        def create(_class, _outer, _context, _interface, output):
            output._obj.value = 33
            return native_status if stage == "create" else 0

        return create

    def method(pointer, index, *_signature):
        assert pointer.value == 33 and index == (4 if files else 5)
        return lambda *_args: native_status

    def invoke(application_id, targets, is_file):
        assert released == [33, 22]
        assert application_id == "Owned.Package!App"
        assert targets == selected and is_file is files
        return LaunchOutcome(True)

    dispatch = Mock(side_effect=invoke)
    monkeypatch.setattr(documents, "apartment", lambda: nullcontext(object()))
    monkeypatch.setattr(documents, "_items", items)
    monkeypatch.setattr(documents, "_targets", lambda _uris: selected)
    monkeypatch.setattr(documents, "bind", bind)
    monkeypatch.setattr(documents, "method", method)
    monkeypatch.setattr(
        documents, "release", lambda pointer: released.append(pointer.value)
    )
    monkeypatch.setattr(documents, "_invoke_registered", dispatch)
    outcome = documents.launch_application_uris(
        "shell:AppsFolder\\Owned.Package!App", ("owned selection",)
    )
    assert outcome == LaunchOutcome(started)
    assert dispatch.call_count == int(fallback)
    assert released == ([22] if stage == "items" else [33, 22])
