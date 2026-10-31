"""Real nonce-owned Win32 association queries cross the existing catalog worker.

No application is activated. A fresh helper imports Gio only after registration,
so prior tests cannot seed its process-wide Win32 association cache.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_HEARTBEAT_MS = 5


@contextmanager
def _new_registry_keys(registry, definitions):
    """Own only absent leaves and the missing empty ancestors they require."""
    for name in definitions:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, name):
                raise AssertionError("Nonce registry leaf already exists")
        except FileNotFoundError:
            pass
    created = []
    try:
        for name, values in definitions.items():
            parts = name.split("\\")
            for length in range(2, len(parts) + 1):
                parent = "\\".join(parts[:length])
                try:
                    with registry.OpenKey(registry.HKEY_CURRENT_USER, parent):
                        pass
                except FileNotFoundError:
                    with registry.CreateKeyEx(
                        registry.HKEY_CURRENT_USER, parent, 0, registry.KEY_SET_VALUE
                    ):
                        created.append(parent)
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER, name, 0, registry.KEY_SET_VALUE
            ) as key:
                for field, (kind, value) in values.items():
                    registry.SetValueEx(key, field, 0, kind, value)
        yield
    finally:
        for name in reversed(created):
            with registry.OpenKey(registry.HKEY_CURRENT_USER, name) as key:
                children, values, _modified = registry.QueryInfoKey(key)
            # Leaves contain only our declared values. A shared newly-created
            # ancestor is removed only while still empty; never erase other data.
            if name in definitions or children == values == 0:
                registry.DeleteKey(registry.HKEY_CURRENT_USER, name)


def _absent(registry, name):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, name):
            raise AssertionError("Nonce registry key was not removed")
    except FileNotFoundError:
        pass


def test_nonce_key_cleanup_preserves_existing_shared_parent_values():
    from tests.test_windows_application_associations import Registry

    registry = Registry()
    parent = r"Software\Classes\MIME"
    registry.keys[parent] = {"Existing": ("untouched", registry.REG_SZ)}
    before = {name: dict(values) for name, values in registry.keys.items()}
    leaf = parent + r"\Database\Content Type\application/x-owned-unit-nonce"
    primary = RuntimeError("Owned test body failed")
    with (
        pytest.raises(RuntimeError) as failed,
        _new_registry_keys(registry, {leaf: {"Extension": (1, ".owned-nonce")}}),
    ):
        raise primary
    assert failed.value is primary
    assert registry.keys == before and registry.opened == 0


def test_nonce_existing_leaf_rejected_before_any_mutation():
    from tests.test_windows_application_associations import Registry

    registry = Registry()
    leaf = r"Software\Classes\Existing.Nonce"
    registry.keys[leaf] = {"Other": ("retain", 1)}
    before = {name: dict(values) for name, values in registry.keys.items()}
    with (
        pytest.raises(AssertionError, match="already exists"),
        _new_registry_keys(registry, {leaf: {"": (1, "replace")}}),
    ):
        pytest.fail("Existing key cannot become an owned test fixture")
    assert registry.keys == before and registry.opened == 0


def test_heartbeat_allows_lower_priority_native_publication():
    from time import monotonic, sleep

    from tests.test_windows_catalog_worker import _Loop

    loop = _Loop()
    beats, published = [], []
    try:
        loop.timeout(_HEARTBEAT_MS, lambda: (beats.append(True), True)[1])
        # Begin with the default-priority heartbeat already due. Each native
        # wait dispatches one context source and pauses 2ms; its lower-priority
        # metadata idle must still run between real heartbeat callbacks.
        sleep(_HEARTBEAT_MS / 1000)
        loop.idle(lambda: (published.append(True), False)[1])
        deadline = monotonic() + 0.25
        while not published and monotonic() < deadline:
            loop.context.iteration(False)
            sleep(0.002)
        assert beats and published, "Heartbeat starved native metadata publication"
    finally:
        loop.close()


@contextmanager
def _registration(tmp_path, mode):
    import winreg

    from tools.windows.visual.applications_domain_smoke import (
        association_changed,
        association_registration,
    )

    token = uuid.uuid4().hex
    program = "Docking.Catalog.Native." + token
    extension = ".dockingcatalog" + token
    scheme = "docking-catalog-" + token
    mime = "application/x-docking-catalog-" + token
    executable = tmp_path / ("owned handler " + token + ".exe")
    shutil.copyfile(sys.executable, executable)
    assert executable.read_bytes()[:2] == b"MZ", "Native test requires a valid owned PE"
    base = "Software\\Classes\\"
    lifecycle = {}
    selected = scheme if mode == "uri-only" else extension
    mime_key = base + "MIME\\Database\\Content Type\\" + mime
    # Microsoft Default Programs: per-user RegisteredApplications/Capabilities.
    # MIME's Extension mapping is HKCR\MIME\Database\Content Type (merged HKCU).
    # https://learn.microsoft.com/en-us/windows/win32/shell/default-programs
    try:
        with association_registration(winreg, selected, program, executable, lifecycle):
            if mode == "uri-only":
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    base + program + "\\Capabilities\\FileAssociations",
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.DeleteValue(key, scheme)
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, base + scheme, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
                definitions = {
                    base + program + "\\Capabilities\\UrlAssociations": {
                        scheme: (winreg.REG_SZ, program)
                    },
                    base + scheme + "\\shell\\open\\command": {
                        "": (winreg.REG_SZ, f'"{executable}" "%1"')
                    },
                }
                content_type = "x-scheme-handler/" + scheme
            else:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, base + extension, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(key, "Content Type", 0, winreg.REG_SZ, mime)
                    if mode == "mime-recommendations":
                        winreg.DeleteValue(key, "")
                definitions = {mime_key: {"Extension": (winreg.REG_SZ, extension)}}
                if mode == "mime-recommendations":
                    definitions[base + extension + "\\OpenWithProgids"] = {
                        program: (winreg.REG_NONE, b"")
                    }
                content_type = mime
            with _new_registry_keys(winreg, definitions):
                association_changed()
                yield {
                    "mode": mode,
                    "content_type": content_type,
                    "extension": extension,
                    "program": program,
                    "scheme": scheme,
                    "executable": str(executable),
                    "expected_ids": [
                        "HKEY_CURRENT_USER\\Software\\Classes\\" + program,
                        "HKEY_CURRENT_USER\\Software\\Classes\\" + scheme,
                    ],
                }
    finally:
        association_changed()
    assert lifecycle["association_restored"]
    _absent(winreg, base + selected)
    _absent(winreg, base + program)
    for name in definitions:
        _absent(winreg, name)


def _worker_check(configuration, report):
    from threading import Event, current_thread, get_ident
    from time import monotonic, sleep

    from docking.launcher import prepare_display

    assert prepare_display()
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from docking.platform.applications import associations
    from docking.platform.applications.metadata import ApplicationMetadata
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.applications.types import ApplicationInfo
    from docking.platform.applications.windows import catalog
    from tests.test_windows_catalog_worker import _Loop, _Monitor, _Registrations

    ui_thread = get_ident()
    entered, release = Event(), Event()
    threads, native_handles, publications, beats, calls = {}, [], [], [], []
    loop = _Loop()
    registry = None

    def trace(kind, function):
        def call(*arguments):
            assert get_ident() != ui_thread, (
                "Native Gio association queried on main thread"
            )
            threads[get_ident()] = current_thread()
            calls.append((kind, str(arguments[0]), get_ident()))
            if not entered.is_set():
                entered.set()
                assert release.wait(10), "Owned native lookup gate was not released"
            result = function(*arguments)
            handles = (
                ()
                if kind == "mime"
                else (
                    tuple(result or ())
                    if kind in ("recommended", "all")
                    else (result,)
                    if result is not None
                    else ()
                )
            )
            for handle in handles:
                identifier = handle.get_id()
                assert identifier.casefold() in {
                    value.casefold() for value in configuration["expected_ids"]
                }, "Native query resolved another application"
                assert Path(handle.get_executable()).samefile(
                    configuration["executable"]
                )
                supported = tuple(handle.get_supported_types() or ())
                native_handles.append((handle, identifier, supported))
            return result

        return call

    def metadata(_ids, *, registrations, cancelled, exporter):
        assert get_ident() != ui_thread and not cancelled()
        threads[get_ident()] = current_thread()
        # This integration checks real association APIs, not whole-shell catalog
        # enumeration/export; those have separate native acceptance coverage.
        return ApplicationMetadata(applications=())

    def until(predicate):
        deadline = monotonic() + 30
        while not predicate():
            assert monotonic() < deadline, "Real native association did not publish"
            loop.context.iteration(False)
            sleep(0.002)

    def no_activation(*_arguments, **_keywords):
        raise AssertionError(
            "Association query tests must never activate an application"
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            Gio,
            "content_type_from_mime_type",
            trace("mime", Gio.content_type_from_mime_type),
        )
        patch.setattr(
            Gio.AppInfo,
            "get_default_for_type",
            trace("default", Gio.AppInfo.get_default_for_type),
        )
        patch.setattr(
            Gio.AppInfo,
            "get_recommended_for_type",
            trace("recommended", Gio.AppInfo.get_recommended_for_type),
        )
        patch.setattr(
            Gio.AppInfo, "get_all_for_type", trace("all", Gio.AppInfo.get_all_for_type)
        )
        patch.setattr(
            Gio.AppInfo,
            "get_default_for_uri_scheme",
            trace("uri", Gio.AppInfo.get_default_for_uri_scheme),
        )
        patch.setattr(Gio.AppInfo, "launch", no_activation)
        patch.setattr(Gio.AppInfo, "launch_uris", no_activation)
        patch.setattr(Gio.AppInfo, "launch_default_for_uri", no_activation)
        patch.setattr(catalog.catalog, "read_application_metadata", metadata)
        registry = ApplicationRegistry(
            catalog_backend=catalog.WindowsApplicationCatalogBackend(),
            application_source=lambda: (),
            executable_registration=_Registrations(),
            desktop_directories_source=lambda: (),
        )
        registry._app_monitor_factory = _Monitor
        registry._change_monitor_factory = lambda _changed: None
        registry._schedule_timeout, registry._cancel_timeout = loop.timeout, loop.remove
        registry._debounce_ms = 1
        assert registry._metadata is not None
        registry._metadata._schedule_idle = loop.idle
        registry._metadata._remove_source = loop.remove
        registry.subscribe(lambda: publications.append(get_ident()))
        content_type = configuration["content_type"]
        preferred = configuration["mode"] == "mime-recommendations"

        def query():
            return (
                registry.preferred_listing_for_content_types(
                    (content_type, configuration["extension"])
                )
                if preferred
                else registry.default_for_content_type(content_type)
            )

        try:
            assert query().status is associations.AssociationStatus.UNAVAILABLE
            loop.timeout(_HEARTBEAT_MS, lambda: (beats.append(get_ident()), True)[1])
            registry.start()
            assert query().status is associations.AssociationStatus.PENDING
            until(entered.is_set)
            until(lambda: len(beats) >= 4)
            assert not release.is_set() and set(beats) == {ui_thread}
            release.set()
            until(lambda: query().status is not associations.AssociationStatus.PENDING)

            def assert_retained(resolution):
                assert (
                    resolution.status is associations.AssociationStatus.READY
                    and resolution.listing is not None
                )
                listing = resolution.listing
                retained = (
                    registry._gio_handle_for(listing.desktop_id)
                    if isinstance(listing, ApplicationInfo)
                    else registry._gio_handle_for_unidentified(listing.listing_key)
                )
                assert any(
                    retained is handle for handle, _identifier, _types in native_handles
                ), "Main publication lost the native association handle"

            assert_retained(query())
            # The first accepted default may let the public preferred query
            # return while its additional recommendation facts are still queued.
            # Wait for those actual native results, rather than assuming a batch
            # has published merely because its first usable result is available.
            kinds = (
                ("default",)
                if configuration["mode"] == "uri-only"
                else ("default", "recommended", "all")
            )
            keys = tuple(
                associations.AssociationKey(kind, content_type) for kind in kinds
            )

            def accepted_reads():
                return dict(registry._metadata.association_reads(keys))

            until(
                lambda: all(
                    read.status is associations.AssociationStatus.READY
                    for read in accepted_reads().values()
                )
            )
            reads = accepted_reads()
            assert_retained(registry.default_for_content_type(content_type))
            assert_retained(
                registry.preferred_listing_for_content_types((content_type,))
            )
            assert publications and set(publications) == {ui_thread}
            assert calls and all(thread != ui_thread for _kind, _value, thread in calls)
            if preferred:
                # Win32 Gio can itself choose an OpenWith handler when there is
                # no explicit default. Exercise its real recommendation paths
                # without pretending this guarantees a missing default result.
                assert reads[
                    associations.AssociationKey("recommended", content_type)
                ].handles
                assert reads[associations.AssociationKey("all", content_type)].handles
            elif configuration["mode"] == "mime-default":
                assert ("mime", content_type, next(iter(threads))) in calls
                assert any(
                    kind == "default" and value == configuration["extension"]
                    for kind, value, _thread in calls
                )
            else:
                assert all(
                    not any(value.startswith(".") for value in types)
                    for _handle, _identifier, types in native_handles
                )
                assert any(kind == "uri" for kind, _value, _thread in calls)
            report.update(
                ok=True,
                heartbeat=len(beats),
                calls=calls,
                publications=publications,
                canonical_ids=sorted(
                    {identifier for _handle, identifier, _types in native_handles}
                ),
                retained_native_handle=True,
            )
        finally:
            release.set()
            registry.stop()
            for thread in threads.values():
                thread.join(5)
            report["worker_stopped"] = bool(threads) and all(
                not thread.is_alive() for thread in threads.values()
            )
            report["stopped_unavailable"] = (
                query().status is associations.AssociationStatus.UNAVAILABLE
            )
            loop.close()
    assert report["worker_stopped"] and report["stopped_unavailable"]


@pytest.mark.skipif(
    sys.platform != "win32", reason="Real Win32 Gio/HKCU association integration"
)
@pytest.mark.parametrize("mode", ["mime-default", "mime-recommendations", "uri-only"])
def test_native_association_queries_publish_without_blocking_main(tmp_path, mode):
    output = tmp_path / "result.json"
    with _registration(tmp_path, mode) as configuration:
        configuration.update(
            root=str(Path(__file__).resolve().parents[1]), output=str(output)
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--native-probe",
                json.dumps(configuration),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    result = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    assert completed.returncode == 0, (completed.stdout, result)
    assert result["ok"] and result["worker_stopped"] and result["stopped_unavailable"]
    assert result["retained_native_handle"] and result["heartbeat"] >= 4


if __name__ == "__main__":
    assert sys.platform == "win32" and sys.argv[1] == "--native-probe"
    configuration = json.loads(sys.argv[2])
    sys.path.insert(0, configuration["root"])
    os.environ.update(
        XDG_CONFIG_HOME=str(Path(configuration["output"]).parent / "config"),
        XDG_CACHE_HOME=str(Path(configuration["output"]).parent / "cache"),
    )
    result = {"ok": False, "mode": configuration["mode"]}
    try:
        _worker_check(configuration, result)
    except Exception as error:
        result["error"] = {"class": type(error).__name__, "message": str(error)}
        raise
    finally:
        Path(configuration["output"]).write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
