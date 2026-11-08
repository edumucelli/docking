"""Native-ID source lookup remains bounded and separate from executable aliases."""

import ctypes
from contextlib import nullcontext

import pytest

from docking.platform.applications.implementations.windows import (
    catalog_shortcuts as sources,
)


def test_sources_resolve_each_link_once_and_keep_distinct_exact_id_options(
    tmp_path, monkeypatch
):
    nested = tmp_path / "nested"
    nested.mkdir()
    paths = (
        tmp_path / "Same name.lnk",
        nested / "Same name.LNK",
        nested / "Unrelated.lnk",
    )
    for path in paths:
        path.write_bytes(b"native shortcut")
    (nested / "document.txt").write_text("ordinary document")
    monkeypatch.setattr(sources, "_program_directories", lambda: (tmp_path,))
    identities = {paths[0]: "Native.App", paths[1]: "Native.App", paths[2]: "Other.App"}
    calls = []

    def resolve(path):
        calls.append(path)
        return identities[path]

    monkeypatch.setattr(sources, "shortcut_application_id", resolve)
    resolved = sources.read_shortcut_sources(
        frozenset({"Native.App"}), cancelled=lambda: False
    )
    assert set(resolved) == {"Native.App"}
    assert {path for path, _identity in resolved["Native.App"]} == set(paths[:2])
    assert set(calls) == set(paths) and len(calls) == 3


def test_traversal_limit_and_cancel_bound_work_without_silently_publishing_prefix(
    tmp_path, monkeypatch
):
    for index in range(3):
        (tmp_path / f"shortcut{index}.lnk").write_bytes(b"link")
    monkeypatch.setattr(sources, "_MAX_ENTRIES", 2)
    with pytest.raises(OSError, match="traversal limit"):
        tuple(sources._links((tmp_path,), lambda: False))
    assert tuple(sources._links((tmp_path,), lambda: True)) == ()


@pytest.mark.parametrize("failure", [None, "missing", "denied"])
def test_known_folders_free_allocations_even_on_failure(tmp_path, monkeypatch, failure):
    allocated = [
        ctypes.create_unicode_buffer(str(tmp_path / name))
        for name in ("user", "common")
    ]
    calls, freed = [], []
    monkeypatch.setattr(sources, "apartment", lambda: nullcontext(object()))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: object(), raising=False)

    def bind(_library, name, *_signature):
        if name == "CoTaskMemFree":
            return lambda pointer: freed.append(pointer.value)
        assert name == "SHGetKnownFolderPath"

        def folder(_identifier, flags, token, output):
            assert flags == 0 and token is None
            index = len(calls)
            calls.append(index)
            output._obj.value = ctypes.addressof(allocated[index])
            if index == 1 and failure is not None:
                return -2147024894 if failure == "missing" else -2147024891
            return 0

        return folder

    monkeypatch.setattr(sources, "bind", bind)
    if failure == "denied":
        with pytest.raises(OSError):
            sources._program_directories()
    else:
        assert sources._program_directories() == (tmp_path / "user",) + (
            (tmp_path / "common",) if failure is None else ()
        )
    assert freed == [ctypes.addressof(value) for value in allocated]
