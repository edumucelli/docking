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
        return ShellLink(Path(target), cwd, arguments)

    monkeypatch.setattr(registration, "write_shortcut", write)
    monkeypatch.setattr(registration, "read_shortcut", read)


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
