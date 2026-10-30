"""Native executable registration survives catalog refresh and process restart."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from docking.platform.applications import registry as registry_module
from docking.platform.applications import windows_registration as registration
from docking.platform.applications.identity import ProcessIdentity
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import ApplicationOrigin
from docking.platform.backends.windows.shortcuts import ShellLink


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(64)
    header[:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    path.write_bytes(header + b"PE\0\0")
    return path


@pytest.fixture
def links(monkeypatch):
    def write(path, *, executable, name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([str(executable), str(executable.parent), ""]), encoding="utf-8"
        )

    def read(path):
        target, cwd, arguments = json.loads(path.read_text(encoding="utf-8"))
        return ShellLink(Path(target) if target else None, cwd, arguments)

    monkeypatch.setattr(registration, "write_shortcut", write)
    monkeypatch.setattr(registration, "read_shortcut", read)


def _shortcut(path, executable, *, arguments='--profile "résumé 日本語" ""', cwd=None):
    path.write_text(
        json.dumps(
            [str(executable) if executable else "", str(cwd or path.parent), arguments]
        ),
        encoding="utf-8",
    )
    return path


def test_import_preserves_native_identity_and_selected_binary_after_source_removal(
    tmp_path, monkeypatch, links
):
    executable = _executable(tmp_path / "program" / "Shared.exe")
    cwd = tmp_path / "different working directory"
    source = _shortcut(tmp_path / "résumé 日本語.lnk", executable, cwd=cwd)
    original = source.read_bytes()
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    monkeypatch.setattr(registry_module, "is_windows", lambda: True)
    registry = ApplicationRegistry(
        application_source=lambda: (), executable_registration=backend
    )
    imported = registry.register_executable(source.as_uri())
    assert imported.desktop_id == "Native.App"
    assert imported.name == source.stem
    assert imported.working_directory == str(cwd)
    copy = Path(imported.launch_target)
    assert copy.read_bytes() == original
    assert copy != source
    source.unlink()
    restarted = ApplicationRegistry(
        application_source=lambda: (), executable_registration=backend
    )
    restarted.refresh()
    assert restarted.get("Native.App") == imported
    assert registration.read_shortcut(copy).arguments == '--profile "résumé 日本語" ""'


def test_imported_shortcut_overrides_launch_options_without_losing_catalog_metadata(
    tmp_path, monkeypatch, links
):
    from tests.platform.application_fakes import GioApplicationFake

    executable = _executable(tmp_path / "Shared.exe")
    source = _shortcut(tmp_path / "Selected.lnk", executable, cwd=tmp_path / "profile")
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    monkeypatch.setattr(registry_module, "is_windows", lambda: True)
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    handle = GioApplicationFake(
        "Native.App", name="Catalog", commandline=str(executable)
    )
    registry = ApplicationRegistry(
        application_source=lambda: (handle,), executable_registration=backend
    )
    registry.refresh()
    before = registry.get("Native.App")
    imported = registry.register_executable(source)
    assert len(registry.snapshot()) == 1
    assert imported.name == "Selected"
    assert imported.origin is before.origin is ApplicationOrigin.INSTALLED
    assert imported.has_gio_source and registry._gio_handle_for("Native.App") is handle
    assert imported.categories == before.categories
    assert imported.working_directory == str(tmp_path / "profile")
    assert Path(imported.launch_target).read_bytes() == source.read_bytes()
    diagnostic = registry.diagnostic_snapshot().applications[0]
    assert diagnostic.name == imported.name and diagnostic.aliases == imported.aliases


@pytest.mark.parametrize("failure", ["copy", "publish"])
def test_failed_shortcut_update_preserves_previous_registration(
    tmp_path, monkeypatch, links, failure
):
    executable = _executable(tmp_path / "Shared.exe")
    source = _shortcut(tmp_path / "Selected.lnk", executable)
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    backend.create(source, startup_wm_class="")
    before = backend.applications()
    files = {p.resolve(): p.read_bytes() for p in backend.directory.iterdir()}
    _shortcut(source, executable, arguments="--changed")

    def denied(*_a, **_kw):
        raise PermissionError("Denied")

    if failure == "copy":
        monkeypatch.setattr(registration.shutil, "copyfile", denied)
    else:
        monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError):
        backend.create(source, startup_wm_class="")
    assert backend.applications() == before
    assert {p.resolve(): p.read_bytes() for p in backend.directory.iterdir()} == files


@pytest.mark.parametrize("stage", ["metadata", "identity", "copy"])
def test_replaced_source_cannot_mix_validation_identity_and_persisted_binary(
    tmp_path, monkeypatch, links, stage
):
    executable = _executable(tmp_path / "Shared.exe")
    source = _shortcut(tmp_path / "Selected.lnk", executable)
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    backend.create(source, startup_wm_class="")
    previous = backend.applications()
    retained = {p.resolve(): p.read_bytes() for p in backend.directory.iterdir()}
    replacement = _shortcut(tmp_path / "replacement.lnk", tmp_path / "document.txt")
    read, copy = registration.read_shortcut, registration.shutil.copyfile

    def read_replaced(path):
        link = read(path)
        replacement.replace(source)
        return link

    def identity_replaced(_path):
        replacement.replace(source)
        return "Other.App"

    def copy_replaced(origin, destination):
        result = copy(origin, destination)
        replacement.replace(source)
        return result

    if stage == "metadata":
        monkeypatch.setattr(registration, "read_shortcut", read_replaced)
    elif stage == "identity":
        monkeypatch.setattr(registration, "shortcut_application_id", identity_replaced)
    else:
        monkeypatch.setattr(registration.shutil, "copyfile", copy_replaced)

    with pytest.raises(OSError, match="Shortcut changed during import"):
        backend.create(source, startup_wm_class="")
    monkeypatch.setattr(registration, "read_shortcut", read)
    assert backend.applications() == previous
    assert {
        p.resolve(): p.read_bytes() for p in backend.directory.iterdir()
    } == retained
    assert registration.read_shortcut(source).executable == tmp_path / "document.txt"


def test_updating_shortcut_changes_target_but_retains_previous_generation(
    tmp_path, monkeypatch, links
):
    executable = _executable(tmp_path / "Shared.exe")
    source = tmp_path / "Selected.lnk"
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    previous = None
    for index in range(4):
        _shortcut(source, executable, arguments=f"--profile {index}")
        backend.create(source, startup_wm_class="")
        current = backend.applications()[0]
        assert current.desktop_id == "Native.App"
        assert (
            registration.read_shortcut(Path(current.launch_target)).arguments
            == f"--profile {index}"
        )
        if previous is not None:
            assert Path(previous.launch_target).is_file()
            assert previous.launch_target != current.launch_target
        previous = current
    assert len(tuple(backend.directory.glob("*.lnk"))) == 2
    assert len(tuple(backend.directory.glob("*.json"))) == 1


def test_shortcut_fallback_id_is_stable_and_does_not_merge_same_executable_profiles(
    tmp_path, monkeypatch, links
):
    executable = _executable(tmp_path / "Shared.exe")
    monkeypatch.setattr(registration, "shortcut_application_id", lambda _p: None)
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    first = _shortcut(tmp_path / "one.lnk", executable)
    second = _shortcut(tmp_path / "two.lnk", executable)
    identifier = backend.create(first, startup_wm_class="")
    assert identifier == backend.create(first, startup_wm_class="")
    assert identifier != backend.create(second, startup_wm_class="")
    assert len(backend.applications()) == 2


@pytest.mark.parametrize("kind", ["folder", "document", "virtual"])
def test_shortcut_validation_distinguishes_application_and_file_links(
    tmp_path, monkeypatch, links, kind
):
    target = tmp_path / kind
    if kind == "folder":
        target.mkdir()
    elif kind == "document":
        target.write_text("Document", encoding="utf-8")
    else:
        target = None
    source = _shortcut(tmp_path / "Selected.lnk", target)
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    identifier = backend.create(source, startup_wm_class="")
    if kind == "virtual":
        assert identifier == "Native.App"
        assert backend.applications()[0].executable_path is None
    else:
        assert identifier is None
        assert not backend.directory.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("file", "../outside.lnk"),
        ("file", "..\\outside.lnk"),
        ("id", 7),
        ("name", None),
    ],
)
def test_invalid_import_manifest_cannot_replace_a_valid_record(
    tmp_path, monkeypatch, links, field, value
):
    source = _shortcut(tmp_path / "Selected.lnk", _executable(tmp_path / "Shared.exe"))
    monkeypatch.setattr(
        registration, "shortcut_application_id", lambda _p: "Native.App"
    )
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    backend.create(source, startup_wm_class="")
    manifest = next(backend.directory.glob("*.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data[field] = value
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert backend.applications() == ()


def test_persistent_registration_is_idempotent_and_matches_the_actual_executable(
    tmp_path, monkeypatch, links
):
    executable = _executable(tmp_path / "résumé 日本語" / "Shared.exe")
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    monkeypatch.setattr(registry_module, "is_windows", lambda: True)

    def fresh_registry():
        return ApplicationRegistry(
            application_source=lambda: (), executable_registration=backend
        )

    first = fresh_registry()
    application = first.register_executable(executable.as_uri())
    assert application is not None
    assert application.origin is ApplicationOrigin.GENERATED
    assert application.executable_path == executable
    assert application.desktop_file is None
    assert first.register_executable(executable) == application
    assert len(tuple(backend.directory.glob("*.lnk"))) == 1
    assert tuple(backend.directory.glob("*.desktop")) == ()

    restarted = fresh_registry()
    restarted.refresh()
    assert restarted.get(application.desktop_id) == application
    assert restarted.snapshot() == (application,)

    from types import SimpleNamespace

    matcher = AppIdMatcher(
        restarted,
        SimpleNamespace(
            identity_for_pid=lambda _pid: ProcessIdentity(
                pid=7, executable_path=executable
            )
        ),
    )
    matched = matcher.match_result("Shared", process_id=7)
    assert matched is not None and matched.desktop_id == application.desktop_id
    assert matched.application.origin is ApplicationOrigin.GENERATED


@pytest.mark.parametrize(
    "content,suffix",
    [(b"ordinary document", ".exe"), (b"\x7fELF", ".exe"), (b"MZ", ".txt")],
)
def test_documents_and_non_windows_images_never_create_launchers(
    tmp_path, links, content, suffix
):
    target = tmp_path / ("file" + suffix)
    target.write_bytes(content)
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    assert backend.create(target, startup_wm_class="") is None
    assert not backend.directory.exists()


def test_failed_write_does_not_register_or_create_an_application(tmp_path, monkeypatch):
    target = _executable(tmp_path / "app.exe")
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")

    def denied(*_args, **_kwargs):
        raise PermissionError("Denied")

    monkeypatch.setattr(registration, "write_shortcut", denied)
    with pytest.raises(PermissionError):
        backend.create(target, startup_wm_class="")
    assert backend.applications() == ()


def test_saved_launcher_requires_a_resolvable_registry_record(
    tmp_path, monkeypatch, links
):
    target = _executable(tmp_path / "app.exe")
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    registry = ApplicationRegistry(
        application_source=lambda: (),
        desktop_directories_source=lambda: (),
        executable_registration=backend,
    )
    monkeypatch.setattr(backend, "applications", lambda: ())

    with pytest.raises(OSError, match="could not be resolved"):
        registry.register_executable(target)

    assert tuple(backend.directory.glob("*.lnk"))
    assert registry.snapshot() == ()


def test_changed_shortcut_cannot_retarget_an_existing_application_id(
    tmp_path, monkeypatch, links
):
    target = _executable(tmp_path / "one" / "Shared.exe")
    other = _executable(tmp_path / "two" / "Shared.exe")
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    identifier = backend.create(target, startup_wm_class="")
    shortcut = backend.directory / f"{identifier}.lnk"
    registration.write_shortcut(shortcut, executable=other, name="other")
    assert backend.applications() == ()


def test_bad_shortcut_does_not_hide_other_persistent_launchers(
    tmp_path, monkeypatch, links
):
    backend = registration.WindowsExecutableRegistration(tmp_path / "launchers")
    first = backend.create(_executable(tmp_path / "one.exe"), startup_wm_class="")
    second = backend.create(_executable(tmp_path / "two.exe"), startup_wm_class="")
    original_read = registration.read_shortcut

    def read(path):
        if path.stem == first:
            raise OSError("Invalid link")
        return original_read(path)

    monkeypatch.setattr(registration, "read_shortcut", read)
    assert [app.desktop_id for app in backend.applications()] == [second]
    monkeypatch.setattr(
        registration,
        "read_shortcut",
        lambda p: replace(original_read(p), arguments="--changed"),
    )
    assert backend.applications() == ()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows path semantics")
def test_native_identity_is_stable_from_python_and_an_active_msys2_shell(tmp_path):
    target = _executable(tmp_path / "résumé 日本語" / "App.exe")
    script = (
        "import sys; from pathlib import Path; "
        "from docking.platform.applications.windows_registration import executable_id; "
        "print(executable_id(Path(sys.argv[1])))"
    )
    identifiers = []
    for active in (False, True):
        env = {**os.environ}
        env.pop("MSYSTEM", None)
        if active:
            env["MSYSTEM"] = "UCRT64"
        for path in (
            str(target),
            str(target).swapcase(),
            str(target).replace("\\", "/"),
        ):
            identifiers.append(
                subprocess.check_output(
                    [sys.executable, "-c", script, path],
                    env=env,
                    text=True,
                    encoding="utf-8",
                ).strip()
            )
    assert len(set(identifiers)) == 1
