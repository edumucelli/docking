"""Journal and restore temporary built-in text-service profiles in an owned lab."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager

from docking.platform.backends.windows.com import GUID, HRESULT, apartment, method
from docking.platform.backends.windows.input_profiles import InputProfile, TextProfiles
from docking.platform.backends.windows.winrt import check

SERVICE_CLSID = "c2cb2cf0-af47-413e-9780-8bc3a3c16068"
LANGUAGE_ID = 0x042A
TELEX_IDENTIFIER = "5fb02ec5-0a77-4684-b4fa-def8a2195628"
VNI_IDENTIFIER = "591ae943-56be-48f6-8966-06b43915cc5a"
IDENTIFIERS = frozenset((TELEX_IDENTIFIER, VNI_IDENTIFIER))


def is_fixture_profile(profile: InputProfile) -> bool:
    return (
        profile.kind == 1
        and profile.language == LANGUAGE_ID
        and profile.clsid == SERVICE_CLSID
        and profile.identifier in IDENTIFIERS
    )


def profile_enabled(service: TextProfiles, profile: InputProfile) -> bool:
    value = ctypes.c_int32()
    check(
        method(
            service._profiles,
            18,
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_uint16,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_int32),
        )(
            service._profiles,
            ctypes.byref(GUID.parse(profile.clsid)),
            profile.language,
            ctypes.byref(GUID.parse(profile.identifier)),
            ctypes.byref(value),
        )
    )
    return bool(value.value)


def set_profile_enabled(
    service: TextProfiles, profile: InputProfile, value: bool
) -> None:
    check(
        method(
            service._profiles,
            17,
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_uint16,
            ctypes.POINTER(GUID),
            ctypes.c_int32,
        )(
            service._profiles,
            ctypes.byref(GUID.parse(profile.clsid)),
            profile.language,
            ctypes.byref(GUID.parse(profile.identifier)),
            value,
        )
    )


@contextmanager
def text_profile_fixtures(
    journal: dict, publish: Callable[[], None]
) -> Iterator[tuple[InputProfile, ...]]:
    with apartment(), ExitStack() as scope:
        service = TextProfiles(scope)
        profiles = tuple(p for p in service.profiles() if is_fixture_profile(p))
        journal["available"] = len(profiles) == 2
        journal["original_enabled"] = {
            p.code: profile_enabled(service, p) for p in profiles
        }
        journal["owned"] = []
        publish()
        try:
            with ExitStack() as restore:
                for profile in profiles:
                    if not journal["original_enabled"][profile.code]:
                        journal["owned"].append(profile.code)
                        publish()
                        restore.callback(set_profile_enabled, service, profile, False)
                        set_profile_enabled(service, profile, True)
                yield profiles
        finally:
            journal["enabled_after"] = {
                p.code: profile_enabled(service, p) for p in profiles
            }
            journal["restored"] = (
                journal["enabled_after"] == journal["original_enabled"]
            )
            publish()
