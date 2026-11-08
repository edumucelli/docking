"""Windows association normalization and bounded native directory lookup."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.applications.associations import AssociationKey, AssociationStatus
from docking.platform.applications.implementations.windows import associations as mod
from docking.platform.native.windows import associations as native


@pytest.fixture
def gio(monkeypatch):
    methods = SimpleNamespace(
        get_default_for_type=Mock(return_value=None),
        get_default_for_uri_scheme=Mock(return_value=None),
        get_recommended_for_type=Mock(return_value=[]),
        get_all_for_type=Mock(return_value=[]),
    )
    fake = SimpleNamespace(
        AppInfo=methods, content_type_from_mime_type=Mock(return_value=".owned")
    )
    monkeypatch.setattr(mod, "Gio", fake)
    return fake


@pytest.mark.parametrize("kind", ["default", "recommended", "all"])
def test_worker_normalizes_mime_to_native_extension(gio, kind):
    result = mod.read_associations(
        (AssociationKey(kind, "Audio/X-Owned"),), lambda: False
    )[0][1]
    assert result.status is AssociationStatus.READY
    gio.content_type_from_mime_type.assert_called_once_with("audio/x-owned")
    method = {
        "default": gio.AppInfo.get_default_for_type,
        "recommended": gio.AppInfo.get_recommended_for_type,
        "all": gio.AppInfo.get_all_for_type,
    }[kind]
    method.assert_called_once_with(
        ".owned", False
    ) if kind == "default" else method.assert_called_once_with(".owned")


def test_extension_and_protocol_have_separate_correct_native_semantics(gio):
    handler = object()
    gio.AppInfo.get_default_for_uri_scheme.return_value = handler
    rows = mod.read_associations(
        (
            AssociationKey("default", ".OWNED"),
            AssociationKey("default", "x-scheme-handler/Own+ed"),
        ),
        lambda: False,
    )
    assert rows[1][1].handles == (handler,)
    gio.content_type_from_mime_type.assert_not_called()
    gio.AppInfo.get_default_for_type.assert_called_once_with(".owned", False)
    gio.AppInfo.get_default_for_uri_scheme.assert_called_once_with("own+ed")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "bad\0type",
        "x-scheme-handler/",
        "x-scheme-handler/3bad",
        "x-scheme-handler/bad scheme",
        "x-scheme-handler/é",
    ],
)
def test_invalid_query_is_unavailable_without_guessing_any_handler(gio, value):
    assert (
        mod.read_associations((AssociationKey("default", value),), lambda: False)[0][
            1
        ].status
        is AssociationStatus.UNAVAILABLE
    )
    gio.AppInfo.get_default_for_type.assert_not_called()
    gio.AppInfo.get_default_for_uri_scheme.assert_not_called()


@pytest.mark.parametrize("native_type", [None, "*"])
def test_unknown_mime_never_falls_back_to_unknown_application(gio, native_type):
    gio.content_type_from_mime_type.return_value = native_type
    result = mod.read_associations(
        (AssociationKey("default", "unknown/owned"),), lambda: False
    )[0][1]
    assert result.status is AssociationStatus.UNAVAILABLE
    gio.AppInfo.get_default_for_type.assert_not_called()


def test_one_failed_read_preserves_other_cells_and_empty_is_ready(gio):
    gio.AppInfo.get_default_for_type.side_effect = OSError(5, "Owned failure")
    rows = mod.read_associations(
        (
            AssociationKey("default", "audio/owned"),
            AssociationKey("recommended", "audio/owned"),
        ),
        lambda: False,
    )
    assert [row.status for _key, row in rows] == [
        AssociationStatus.UNAVAILABLE,
        AssociationStatus.READY,
    ]
    assert rows[1][1].handles == ()


def test_cancelled_batch_does_not_call_additional_native_getters(gio):
    cancelled = [False]

    def first(*_args):
        cancelled[0] = True

    gio.AppInfo.get_default_for_type.side_effect = first
    rows = mod.read_associations(
        (AssociationKey("default", ".one"), AssociationKey("all", ".two")),
        lambda: cancelled[0],
    )
    assert len(rows) == 1
    gio.AppInfo.get_all_for_type.assert_not_called()


@pytest.mark.parametrize("kind", ["recommended", "all"])
def test_gio_has_no_protocol_or_directory_list_api_so_unsupported_is_explicit(
    gio, kind
):
    rows = mod.read_associations(
        (
            AssociationKey(kind, "x-scheme-handler/owned"),
            AssociationKey(kind, "inode/directory"),
        ),
        lambda: False,
    )
    assert all(read.status is AssociationStatus.UNAVAILABLE for _key, read in rows)
    gio.AppInfo.get_all_for_type.assert_not_called()
    gio.AppInfo.get_recommended_for_type.assert_not_called()


def test_directory_uses_user_association_name_and_exact_appid_without_mime(
    gio, monkeypatch
):
    getter = Mock(side_effect=lambda *, name: "Owned Files" if name else "Exact.App.Id")
    monkeypatch.setattr(mod, "directory_association", getter)
    rows = mod.read_associations(
        (
            AssociationKey("name", "inode/directory"),
            AssociationKey("default", "inode/directory"),
        ),
        lambda: False,
    )
    assert rows[0][1].name == "Owned Files"
    assert rows[1][1].application_id == "Exact.App.Id" and rows[1][1].handles == ()
    gio.content_type_from_mime_type.assert_not_called()


def binding(monkeypatch, query):
    library = object()
    monkeypatch.setattr(
        native.ctypes, "WinDLL", Mock(return_value=library), raising=False
    )
    bind = Mock(return_value=query)
    monkeypatch.setattr(native, "bind", bind)
    return library, bind


@pytest.mark.parametrize("name, field", [(True, 4), (False, 21)])
def test_directory_native_abi_flags_and_two_pass_buffer(monkeypatch, name, field):
    calls = []

    def query(flags, selected, association, verb, buffer, size):
        calls.append((flags, selected, association, verb, buffer))
        if buffer is None:
            size._obj.value = 6
            return 1
        assert size._obj.value == 6
        buffer.value = "Owned"
        return 0

    library, bind = binding(monkeypatch, query)
    assert native.directory_association(name=name) == "Owned"
    assert [(a, b, c, d) for a, b, c, d, _buffer in calls] == [
        (0x508, field, "Directory", "open")
    ] * 2
    arguments = bind.call_args.args
    assert arguments[:2] == (library, "AssocQueryStringW")
    assert arguments[3:5] == (ctypes.c_uint32, ctypes.c_int32)
    assert arguments[-1] == ctypes.POINTER(ctypes.c_uint32)


@pytest.mark.parametrize("status", [0x80070002, 0x80070003, 0x80070483])
def test_directory_missing_association_is_none_not_other_application(
    monkeypatch, status
):
    query = Mock(return_value=status)
    binding(monkeypatch, query)
    assert native.directory_association(name=True) is None
    assert query.call_count == 1


@pytest.mark.parametrize("status, size", [(0, 10), (1, 0), (1, 32769), (0x80070005, 2)])
def test_directory_size_query_errors_fail_without_allocating(monkeypatch, status, size):
    def query(*arguments):
        arguments[-1]._obj.value = size
        return status

    binding(monkeypatch, query)
    with pytest.raises(OSError):
        native.directory_association(name=True)


@pytest.mark.parametrize("status, changed_size", [(0x80004003, 8), (0, 9), (0, 0)])
def test_directory_buffer_changes_are_unavailable_not_truncated_success(
    monkeypatch, status, changed_size
):
    def query(*arguments):
        if arguments[-2] is None:
            arguments[-1]._obj.value = 6
            return 1
        arguments[-1]._obj.value = changed_size
        return status

    binding(monkeypatch, query)
    with pytest.raises(OSError):
        native.directory_association(name=False)
