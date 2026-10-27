"""Registered package paths and explicit manifest visibility declarations."""

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.platform.backends.windows import packages


def _manifest(
    *,
    hidden="Widget",
    namespace="http://schemas.microsoft.com/appx/manifest/foundation/windows10",
):
    return f'''<Package xmlns="{namespace}" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
      <Applications>
        <Application Id="Main"><uap:VisualElements DisplayName="Same name"/></Application>
        <Application Id="{hidden}"><uap:VisualElements DisplayName="Same name" AppListEntry="none"/></Application>
        <Application Id="Other"><uap:VisualElements AppListEntry="default"/></Application>
      </Applications>
    </Package>'''.encode()


@pytest.mark.parametrize(
    "namespace",
    [
        "",
        "urn:example:legacy",
        "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    ],
)
def test_only_explicit_hidden_visual_entries_are_selected(namespace):
    assert packages.hidden_manifest_entries(_manifest(namespace=namespace)) == {
        "Widget"
    }


def test_registered_paths_use_current_user_head_packages_and_native_u32_lengths():
    api = packages.WindowsPackagePaths.__new__(packages.WindowsPackagePaths)
    path = r"C:\Program Files\WindowsApps\Example_1.0_x64__publisher"
    calls = []

    def find(family, flags, count, names, length, buffer, properties):
        assert family == "Example_publisher"
        assert flags == 0x10
        assert ctypes.sizeof(count._obj) == ctypes.sizeof(length._obj) == 4
        assert properties is None
        calls.append(names is None)
        count._obj.value = 1
        length._obj.value = 32
        if names is None:
            return 122
        names[0] = "Example_1.0_x64__publisher"
        return 0

    def lookup(name, length, buffer):
        assert name == "Example_1.0_x64__publisher"
        length._obj.value = len(path) + 1
        if buffer is None:
            return 122
        buffer.value = path
        return 0

    api._find, api._path = find, lookup
    assert api.paths("Example_publisher") == (Path(path),)
    assert calls == [True, False]


def test_package_registration_changes_retry_without_using_partial_names():
    api = packages.WindowsPackagePaths.__new__(packages.WindowsPackagePaths)
    reads = []

    def find(_family, _flags, count, names, length, _buffer, _properties):
        count._obj.value = 1
        length._obj.value = 32
        if names is None:
            return 122
        reads.append(True)
        if len(reads) == 1:
            return 122
        names[0] = "Current"
        return 0

    api._find = find
    api._package_path = lambda name: Path(name)
    assert api.paths("Example_publisher") == (Path("Current"),)
    assert len(reads) == 2


def test_native_query_errors_are_reported():
    api = packages.WindowsPackagePaths.__new__(packages.WindowsPackagePaths)
    api._find = lambda *_args: 5
    with pytest.raises(OSError, match="registered package names"):
        api.paths("Example_publisher")


def test_visibility_keeps_native_ids_and_does_not_merge_equal_names(
    tmp_path, monkeypatch
):
    (tmp_path / "AppxManifest.xml").write_bytes(_manifest())
    monkeypatch.setattr(
        packages,
        "WindowsPackagePaths",
        lambda: SimpleNamespace(paths=lambda _family: (tmp_path,)),
    )
    ids = (
        "Example_publisher!Main",
        "Example_publisher!Widget",
        "Example_publisher!Other",
    )
    assert packages.hidden_application_ids(ids) == {"Example_publisher!Widget"}
    assert not packages.hidden_application_ids(("Example_publisher!Main",))


@pytest.mark.parametrize("failure", ["missing", "malformed", "oversized"])
def test_unknown_manifest_metadata_does_not_hide_apps(tmp_path, monkeypatch, failure):
    if failure == "malformed":
        (tmp_path / "AppxManifest.xml").write_text("<Package>")
    elif failure == "oversized":
        (tmp_path / "AppxManifest.xml").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    monkeypatch.setattr(
        packages,
        "WindowsPackagePaths",
        lambda: SimpleNamespace(paths=lambda _family: (tmp_path,)),
    )
    assert not packages.hidden_application_ids(("Example_publisher!Main",))


def test_ordinary_win32_ids_need_no_package_api(monkeypatch):
    def unexpected():
        raise AssertionError("No packaged IDs were requested")

    monkeypatch.setattr(packages, "WindowsPackagePaths", unexpected)
    assert not packages.hidden_application_ids(("Example.App", "notepad.exe"))
