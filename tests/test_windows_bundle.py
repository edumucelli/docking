"""Native runtime closure, wheel boundaries, and relocation resource lookup."""

import hashlib
import json
import os
import sys
import zipfile
from pathlib import PurePosixPath

import pytest

from docking.platform.backends.windows.startup import _prepare_bundle
from docking.platform.backends.windows.webview import loader_path
from tools.windows.bundle import (
    NativePackage,
    dependency_closure,
    extract_bundle,
    filesystem_path,
    install_wheel,
    read_packages,
    relative_package_file,
    runtime_file,
)


def test_native_dependency_closure_follows_runtime_edges_and_providers(tmp_path):
    database = tmp_path / "local"
    for name, dependencies, provides in (
        ("gtk", ("image>=2", "cairo"), ()),
        ("image-implementation", ("cairo",), ("image=3",)),
        ("cairo", ("gtk",), ()),
        ("unrelated-compiler", (), ()),
    ):
        directory = database / name
        directory.mkdir(parents=True)
        (directory / "desc").write_text(
            f"%NAME%\n{name}\n\n%VERSION%\n3\n\n"
            "%DEPENDS%\n"
            + "\n".join(dependencies)
            + "\n\n"
            + "%PROVIDES%\n"
            + "\n".join(provides)
            + "\n",
            encoding="utf-8",
        )
        (directory / "files").write_text(
            f"%FILES%\nucrt64/bin/{name}.dll\n", encoding="utf-8"
        )
    selected = dependency_closure(read_packages(database), ("gtk",))
    assert {package.name for package in selected} == {
        "gtk",
        "image-implementation",
        "cairo",
    }
    assert all(package.files for package in selected)


def test_missing_runtime_dependency_fails_instead_of_building_partial_bundle():
    package = NativePackage("gtk", "3", ("missing-runtime",), (), ())
    with pytest.raises(ValueError, match="missing-runtime"):
        dependency_closure((package,), ("gtk",))


@pytest.mark.parametrize(
    "path", ["/absolute", "ucrt64/../secret", "msys/bin/bash.exe", r"ucrt64\escape"]
)
def test_package_paths_cannot_escape_the_native_runtime(path):
    with pytest.raises(ValueError):
        relative_package_file(path, "ucrt64")


@pytest.mark.parametrize(
    "path",
    [
        "bin/python.exe",
        "bin/libgtk-3-0.dll",
        "lib/girepository-1.0/Gtk-3.0.typelib",
        "lib/python3.14/site-packages/gi/_gi.cp314-mingw_x86_64_ucrt.pyd",
        "lib/gstreamer-1.0/libgstwasapi2.dll",
        "share/licenses/python/LICENSE",
        "share/icons/Adwaita/index.theme",
        "etc/fonts/fonts.conf",
    ],
)
def test_runtime_policy_keeps_loadable_code_resources_and_licenses(path):
    assert runtime_file(PurePosixPath(path))


@pytest.mark.parametrize(
    "path",
    [
        "bin/gcc.exe",
        "bin/gtk3-demo.exe",
        "include/gtk-3.0/gtk/gtk.h",
        "lib/libgtk-3.dll.a",
        "lib/pkgconfig/gtk3.pc",
        "share/doc/gtk/manual.html",
        "lib/python3.14/test/test_os.py",
        "lib/python3.14/__pycache__/os.pyc",
        "lib/python3.14/site-packages/__editable__.docking.pth",
        "lib/python3.14/site-packages/docking.dist-info/direct_url.json",
    ],
)
def test_runtime_policy_excludes_development_files_and_installation_provenance(path):
    assert not runtime_file(PurePosixPath(path))


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.py", "C:/outside.py", r"..\outside.py", "docking.data/scripts/script"],
)
def test_wheel_is_validated_before_any_file_is_extracted(tmp_path, bad_path):
    wheel = tmp_path / "docking.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("docking-1.dist-info/METADATA", "Name: docking\nVersion: 1\n")
        archive.writestr("docking/first.py", "must not be extracted")
        archive.writestr(bad_path, "invalid")
    destination = tmp_path / "site"
    with pytest.raises(ValueError):
        install_wheel(wheel, destination)
    assert not destination.exists()


def test_installed_bundle_uses_its_own_resources_and_preserves_command_path(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    marker = tmp_path / "share/docking/runtime.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"version": "1"}), encoding="utf-8")
    monkeypatch.setenv("PATH", "external-provider-tools")
    monkeypatch.setenv("PYGI_DLL_PATH", "development-runtime")
    monkeypatch.setenv("GI_TYPELIB_PATH", "development-typelibs")
    _prepare_bundle(tmp_path)
    assert os.environ["PYGI_DLL_PATH"] == str(tmp_path / "bin")
    assert os.environ["GI_TYPELIB_PATH"] == str(tmp_path / "lib/girepository-1.0")
    assert os.environ["PATH"].endswith(os.pathsep + "external-provider-tools")


def test_source_runtime_keeps_explicit_developer_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGI_DLL_PATH", "development-runtime")
    _prepare_bundle(tmp_path)
    assert os.environ["PYGI_DLL_PATH"] == "development-runtime"


def test_bundled_webview_loader_and_explicit_override(tmp_path, monkeypatch):
    import docking.platform.backends.windows.webview as module

    monkeypatch.setattr(module.sys, "prefix", str(tmp_path))
    monkeypatch.delenv("DOCKING_WEBVIEW2_LOADER", raising=False)
    bundled = tmp_path / "share/docking/runtimes/webview2/WebView2Loader.dll"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    assert loader_path() == bundled
    monkeypatch.setenv("DOCKING_WEBVIEW2_LOADER", str(tmp_path / "explicit.dll"))
    assert loader_path() == tmp_path / "explicit.dll"


@pytest.mark.parametrize("mode", ["valid", "tampered", "extra-file", "escaping-file"])
def test_portable_artifact_extraction_checks_inventory_before_publishing(
    tmp_path, mode
):
    archive_path = tmp_path / "docking.zip"
    name = "../outside" if mode == "escaping-file" else "Docking.exe"
    inventory = {name: hashlib.sha256(b"original").hexdigest()}
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Docking/files.sha256.json", json.dumps(inventory))
        archive.writestr(
            "Docking/" + name, b"changed" if mode == "tampered" else b"original"
        )
        if mode == "extra-file":
            archive.writestr("Docking/unlisted.exe", b"extra")
    destination = tmp_path / "extracted"
    if mode == "valid":
        assert extract_bundle(archive_path, destination) == destination
        assert (destination / "Docking.exe").read_bytes() == b"original"
    else:
        with pytest.raises(ValueError):
            extract_bundle(archive_path, destination)
        assert not destination.exists()
        assert not (tmp_path / "outside").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows long-path APIs")
def test_portable_extraction_handles_long_nested_paths_without_system_policy(tmp_path):
    name = "runtime/lib/" + "long-directory/" * 15 + "owned.txt"
    archive_path = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Docking/files.sha256.json",
            json.dumps({name: hashlib.sha256(b"owned").hexdigest()}),
        )
        archive.writestr("Docking/" + name, b"owned")
    destination = tmp_path / "extracted"
    assert extract_bundle(archive_path, destination) == destination
    owned = filesystem_path(destination) / name
    assert len(str(owned)) > 260
    assert owned.read_bytes() == b"owned"
