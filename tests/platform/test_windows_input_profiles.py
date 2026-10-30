"""TSF profile identity, optional localized descriptions and native ownership."""

import ctypes
import sys
import threading
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows import input_profiles as mod


def profile():
    return mod.InputProfile(
        1,
        0x42A,
        "c2cb2cf0-af47-413e-9780-8bc3a3c16068",
        "5fb02ec5-0a77-4684-b4fa-def8a2195628",
        mod.KEYBOARD_CATEGORY,
        0,
        0,
        2,
        "Fixture input profile",
    )


def test_distinct_text_profiles_keep_identity_even_without_a_layout_handle():
    first = profile()
    second = replace(first, identifier="591ae943-56be-48f6-8966-06b43915cc5a")
    assert first.code != second.code and first.layout == second.layout == 0
    assert first.enabled
    assert not replace(first, flags=1).enabled
    assert replace(first, kind=2, layout=0xFFFFFFFFF0020409).code == "win32:f0020409"


def test_known_disabled_keyboard_stays_filtered_after_the_last_tip_is_disabled():
    keyboard = replace(profile(), kind=2, layout=0x042A042A, flags=0)
    unknown = 0x04090409
    catalog = mod.InputCatalog((), (keyboard,))
    assert catalog.synthetic_layouts((unknown, keyboard.layout)) == {keyboard.code}
    assert not mod.InputCatalog((), ()).synthetic_layouts((unknown, keyboard.layout))
    enabled = replace(keyboard, flags=2)
    assert not mod.InputCatalog((), (enabled,)).synthetic_layouts(
        (unknown, keyboard.layout)
    )


@pytest.mark.parametrize(
    "name", ["Vietnamese Telex", "Fixture \U0001f642", "Bad \ud800"]
)
def test_localized_description_uses_utf16_units_and_releases_the_bstr(
    monkeypatch, name
):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._profiles = ctypes.c_void_p(1)
    encoded = name.encode("utf-16-le", errors="surrogatepass")
    data = ctypes.create_string_buffer(encoded)
    api._string_length = Mock(return_value=len(encoded) // 2)
    api._free_string = Mock()

    def description(*arguments):
        arguments[-1]._obj.value = ctypes.addressof(data)
        return 0

    monkeypatch.setattr(mod, "method", lambda *_args: description)
    assert api._name(mod.NativeProfile()) == encoded.decode(
        "utf-16-le", errors="replace"
    )
    api._free_string.assert_called_once()


@pytest.mark.parametrize("native_enabled", [0, 1, -1])
def test_enabled_language_profile_query_uses_the_exact_tip_identity(
    monkeypatch, native_enabled
):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._profiles = ctypes.c_void_p(1)
    record = profile()

    def enabled(pointer, clsid, language, identifier, value):
        assert pointer is api._profiles and language == record.language
        assert bytes(clsid._obj) == bytes(mod.GUID.parse(record.clsid))
        assert bytes(identifier._obj) == bytes(mod.GUID.parse(record.identifier))
        value._obj.value = native_enabled
        return 0

    def method(pointer, index, result, *arguments):
        assert pointer is api._profiles and index == 18 and result is mod.HRESULT
        assert arguments == (
            ctypes.POINTER(mod.GUID),
            ctypes.c_uint16,
            ctypes.POINTER(mod.GUID),
            ctypes.POINTER(ctypes.c_int32),
        )
        return enabled

    monkeypatch.setattr(mod, "method", method)
    assert api.is_enabled(record) is bool(native_enabled)
    assert record.flags == 2


def test_enabled_language_profile_query_propagates_native_failure(monkeypatch):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._profiles = ctypes.c_void_p(1)

    def denied(*arguments):
        arguments[-1]._obj.value = 1
        return -2147024891

    monkeypatch.setattr(mod, "method", lambda *_args: denied)
    with pytest.raises(OSError):
        api.is_enabled(profile())


def test_keyboard_layouts_cannot_use_the_tip_enabled_query():
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    with pytest.raises(ValueError, match="require a text service"):
        api.is_enabled(replace(profile(), kind=2, layout=0x04090409))


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows TSF")
def test_native_profile_inventory_keeps_layouts_and_active_source_unchanged():
    from docking.applets.keyboardlayout.windows import WindowsKeyboardAPI
    from docking.platform.backends.windows.com import apartment

    errors = []

    def inspect():
        try:
            api = WindowsKeyboardAPI()
            before = api.layouts()
            with apartment(), ExitStack() as scope:
                service = mod.TextProfiles(scope)
                active = service.active()
                records = service.profiles()
                assert records and len({p.code for p in records}) == len(records)
                assert any(p.kind == 2 and p.enabled for p in records)
                assert service.active() == active
                assert api.layouts() == before
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=inspect, daemon=True)
    worker.start()
    worker.join(15)
    assert not worker.is_alive(), "Native profile enumeration did not finish"
    if errors:
        raise errors[0]


@pytest.mark.parametrize("handle", [0x04090409, 0xFFFFFFFFF0020409])
def test_loaded_layout_lookup_preserves_its_keyboard_identity(monkeypatch, handle):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._manager = ctypes.c_void_p(1)

    def lookup(_manager, kind, language, clsid, identifier, layout, output):
        assert kind == 2 and language == handle & 0xFFFF and layout == handle
        assert bytes(clsid._obj) == bytes(identifier._obj) == bytes(16)
        native = output._obj
        native.kind, native.language, native.layout, native.flags = (
            2,
            language,
            handle & 0xFFFFFFFF,
            2,
        )
        return 0

    monkeypatch.setattr(mod, "method", lambda *_args: lookup)
    profile = api.for_layout(handle)
    assert profile is not None and profile.kind == 2 and profile.enabled
    assert profile.code == f"win32:{handle & 0xFFFFFFFF:08x}"


@pytest.mark.parametrize("status", [-2147467259, -2147024809, 1])
def test_loaded_compatibility_layout_without_a_profile_is_absent(monkeypatch, status):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._manager = ctypes.c_void_p(1)
    monkeypatch.setattr(mod, "method", lambda *_args: lambda *_values: status)
    assert api.for_layout(0x42A042A) is None


@pytest.mark.parametrize("kind,layout", [(1, 0x04090409), (2, 0x040C040C), (0, 0)])
def test_loaded_layout_lookup_rejects_a_mismatched_com_response(
    monkeypatch, kind, layout
):
    api = mod.TextProfiles.__new__(mod.TextProfiles)
    api._manager = ctypes.c_void_p(1)

    def lookup(*arguments):
        arguments[-1]._obj.kind = kind
        arguments[-1]._obj.layout = layout
        return 0

    monkeypatch.setattr(mod, "method", lambda *_args: lookup)
    with pytest.raises(OSError, match="Invalid keyboard-profile metadata"):
        api.for_layout(0x04090409)
