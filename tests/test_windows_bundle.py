"""Native runtime closure, wheel boundaries, and relocation resource lookup."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from docking.platform.backends.windows.startup import _prepare_bundle
from docking.platform.backends.windows.webview import loader_path
from tools.windows import bundle as bundle_tool
from tools.windows.bundle import (
    NativePackage,
    dependency_closure,
    extract_bundle,
    filesystem_path,
    install_wheel,
    native_path,
    read_packages,
    relative_package_file,
    runtime_file,
    staging_directory,
    verify_archive,
    write_pixbuf_cache,
)
from tools.windows.visual import bundle_smoke


@pytest.fixture
def deployed_input_helper(tmp_path, monkeypatch):
    import tempfile

    root = tmp_path / "standalone/runtime"
    source = (
        root / "lib/site-packages/docking/platform/backends/windows/input-thread-64.dll"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"bundled helper fixture")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    temporary = tmp_path / "msys64/tmp"
    deployed = temporary / "Docking/input-runtime" / checksum / source.name
    deployed.parent.mkdir(parents=True)
    deployed.write_bytes(source.read_bytes())
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temporary))
    return root, source, deployed, checksum


def test_bundle_accepts_only_its_exact_authenticated_input_cache(deployed_input_helper):
    root, source, deployed, checksum = deployed_input_helper
    foreign, actual = bundle_smoke._foreign_msys_modules(
        [str(deployed), str(root / "bin/libgtk-3-0.dll")],
        root=root,
        bundled_helper=source,
        deployed_helper=deployed,
    )
    assert foreign == [] and actual == checksum


def test_bundle_rejects_tampered_cached_input_helper(deployed_input_helper):
    root, source, deployed, _checksum = deployed_input_helper
    deployed.write_bytes(b"changed cached DLL")
    with pytest.raises(AssertionError, match="differs"):
        bundle_smoke._foreign_msys_modules(
            [str(deployed)], root=root, bundled_helper=source, deployed_helper=deployed
        )


def test_bundle_still_rejects_foreign_msys_and_other_temp_mappings(
    deployed_input_helper,
):
    root, source, deployed, _checksum = deployed_input_helper
    other = deployed.parents[3] / "other/input-thread-64.dll"
    other.parent.mkdir()
    other.write_bytes(source.read_bytes())
    msys = deployed.parents[4] / "ucrt64/bin/libgtk-3-0.dll"
    foreign, _checksum = bundle_smoke._foreign_msys_modules(
        [str(deployed), str(other), str(msys)],
        root=root,
        bundled_helper=source,
        deployed_helper=deployed,
    )
    assert foreign == [str(other), str(msys)]
    with pytest.raises(AssertionError, match="outside"):
        bundle_smoke._foreign_msys_modules(
            [str(other)], root=root, bundled_helper=source, deployed_helper=other
        )


def test_isolated_bundle_probe_embeds_the_same_provenance_validator():
    import ast
    import inspect

    source = inspect.getsource(bundle_smoke._foreign_msys_modules)
    assert bundle_smoke.PROBE.count(source) == 1
    assert "__DLL_PROVENANCE__" not in bundle_smoke.PROBE
    compile(bundle_smoke.PROBE, "<bundle-probe>", "exec")
    tree = ast.parse(bundle_smoke.PROBE)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_foreign_msys_modules"
    )
    assert ast.dump(function) == ast.dump(ast.parse(source).body[0])


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


@pytest.mark.parametrize(
    "mode", ["duplicate-member", "duplicate-json", "alias", "hash"]
)
def test_release_and_extraction_share_strict_archive_validation(tmp_path, mode):
    path = tmp_path / "portable.zip"
    checksum = hashlib.sha256(b"payload").hexdigest()
    inventory = json.dumps({"Docking.exe": checksum})
    if mode == "duplicate-json":
        inventory = (
            '{"Docking.exe":"' + checksum + '","Docking.exe":"' + checksum + '"}'
        )
    elif mode == "alias":
        inventory = json.dumps({"Docking.exe": checksum, "DOCKING.EXE": checksum})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Docking/files.sha256.json", inventory)
        archive.writestr(
            "Docking/Docking.exe", b"changed" if mode == "hash" else b"payload"
        )
        if mode == "duplicate-member":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("Docking/Docking.exe", b"payload")
        elif mode == "alias":
            archive.writestr("Docking/DOCKING.EXE", b"payload")
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError):
        verify_archive(archive)
    with pytest.raises(ValueError):
        extract_bundle(path, tmp_path / "extracted")
    assert not (tmp_path / "extracted").exists()


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
    assert filesystem_path(owned).read_bytes() == b"owned"


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows long-path APIs")
def test_failed_stage_cleans_long_paths_in_an_active_msys_shell(tmp_path):
    name = "runtime/lib/" + "long-directory/" * 15 + "owned.txt"
    with (
        pytest.raises(RuntimeError, match="interrupted"),
        staging_directory(tmp_path, "interrupted-") as stage,
    ):
        owned = stage / name
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned")
        assert owned.relative_to(stage).as_posix() == name
        raise RuntimeError("interrupted")
    assert not stage.exists()


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "not-windows",
        "sharing",
        "writable",
        "link",
        "reparse",
        "hardlink",
        "unsupported",
        "retry-denied",
    ],
)
def test_stage_only_recovers_single_owned_readonly_windows_files(monkeypatch, defect):
    original = PermissionError("owned file denied")
    original.winerror = 32 if defect == "sharing" else 5
    second = PermissionError("still denied after clearing read-only")
    calls = []

    class File:
        def chmod(self, mode, *, follow_symlinks):
            chmod(self, mode, follow_symlinks=follow_symlinks)

        def unlink(self):
            calls.append("unlink")
            if calls.count("unlink") == 1:
                raise original
            if defect == "retry-denied":
                raise second

        def lstat(self):
            return SimpleNamespace(
                st_mode=stat.S_IFLNK
                if defect == "link"
                else stat.S_IFREG | stat.S_IREAD,
                st_nlink=2 if defect == "hardlink" else 1,
                st_file_attributes=(
                    0 if defect == "writable" else stat.FILE_ATTRIBUTE_READONLY
                )
                | (stat.FILE_ATTRIBUTE_REPARSE_POINT if defect == "reparse" else 0),
            )

    def chmod(path, mode, *, follow_symlinks):
        assert path is owned and mode & stat.S_IWRITE
        assert follow_symlinks is False
        calls.append("chmod")

    owned = File()
    monkeypatch.setattr(
        bundle_tool,
        "sys",
        SimpleNamespace(platform="linux" if defect == "not-windows" else "win32"),
    )
    monkeypatch.setattr(bundle_tool.os, "chmod", chmod)
    monkeypatch.setattr(
        bundle_tool.os,
        "supports_follow_symlinks",
        set() if defect == "unsupported" else {chmod},
    )
    if defect is None:
        bundle_tool._unlink_stage_file(owned)
        assert calls == ["unlink", "chmod", "unlink"]
    else:
        with pytest.raises(PermissionError) as raised:
            bundle_tool._unlink_stage_file(owned)
        assert raised.value is (second if defect == "retry-denied" else original)
        assert calls == (
            ["unlink", "chmod", "unlink"] if defect == "retry-denied" else ["unlink"]
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows file attributes")
def test_stage_removes_readonly_long_path_files_in_an_active_msys_shell(tmp_path):
    if os.chmod not in os.supports_follow_symlinks:
        pytest.skip("Requires Windows no-follow chmod support")
    with staging_directory(tmp_path, "readonly-") as stage:
        path = stage / ("nested-directory/" * 18) / "Buenos_Aires"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"owned runtime timezone data")
        path.chmod(stat.S_IREAD, follow_symlinks=False)
        assert path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert len(str(path)) > 260
    assert not stage.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows directory junction")
def test_stage_cleanup_never_traverses_a_directory_junction(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"keep outside content and attributes")
    original = sentinel.stat()
    with staging_directory(tmp_path, "junction-") as stage:
        subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                native_path(stage / "linked"),
                native_path(outside),
            ],
            check=True,
            capture_output=True,
        )
    assert not stage.exists()
    assert sentinel.read_bytes() == b"keep outside content and attributes"
    assert sentinel.stat().st_file_attributes == original.st_file_attributes


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows long-path APIs")
def test_wheel_extraction_retains_extended_paths_in_an_active_msys_shell(tmp_path):
    name = "docking/" + "long-directory/" * 15 + "owned.py"
    wheel = tmp_path / "docking.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "docking-1.dist-info/METADATA",
            "Name: docking\nVersion: 1\nRequires-Dist: requests>=2\n",
        )
        archive.writestr(name, b"owned wheel contents")
    with staging_directory(tmp_path, "wheel-") as stage:
        site = stage / "site"
        assert install_wheel(wheel, site) == ("1", ("requests>=2",))
        assert (site / name).read_bytes() == b"owned wheel contents"


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows GdkPixbuf tools")
def test_pixbuf_cache_keeps_relative_modules_after_moving_extended_path_stage(
    tmp_path, monkeypatch
):
    prefix = Path(sys.base_prefix)
    if not (prefix / "bin/gdk-pixbuf-query-loaders.exe").is_file():
        pytest.skip("Requires the UCRT64 GTK runtime used by Windows CI")
    monkeypatch.setenv(
        "PATH", native_path(prefix / "bin") + os.pathsep + os.environ.get("PATH", "")
    )
    with staging_directory(tmp_path, "pixbuf-") as stage:
        runtime = stage / "runtime"
        binary = runtime / "bin"
        binary.mkdir(parents=True)
        for name in ("gdk-pixbuf-query-loaders.exe", "libgdk_pixbuf-2.0-0.dll"):
            shutil.copy2(prefix / "bin" / name, binary / name)
        loaders = runtime / "lib/gdk-pixbuf-2.0/2.10.0/loaders"
        loaders.mkdir(parents=True)
        for path in (prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders").glob("*.dll"):
            shutil.copy2(path, loaders / path.name)
        # Other DLL dependencies remain available from the installed runtime;
        # this test isolates the copied GdkPixbuf DLL's relocation behavior.
        write_pixbuf_cache(runtime)
        relocated = stage / "relocated"
        runtime.rename(relocated)
        cache = relocated / "lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
        modules = [
            PureWindowsPath(json.loads(line))
            for line in cache.read_text(encoding="utf-8").splitlines()
            if line.startswith('"') and line.endswith('.dll"')
        ]
        assert modules and all(not path.is_absolute() for path in modules)
        assert all((relocated / path).is_file() for path in modules)
