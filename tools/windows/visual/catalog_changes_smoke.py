"""Observe prompt real application inventory changes without forcing a refresh."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def _error(error):
    facts = {"class": type(error).__name__}
    if isinstance(error, OSError):
        # WinError is an external boundary; portable OSError has no winerror.
        try:
            code = error.winerror
        except AttributeError:
            code = None
        code = error.errno if code is None else code
        facts["code"] = code
        if isinstance(code, int) and (code < 0 or code & 0x80000000):
            facts["hresult"] = f"0x{code & 0xFFFFFFFF:08x}"
    return facts


def _fixture_rows(applications, labels):
    if applications is None:
        return None
    return [
        {"id": app.desktop_id, "name": app.name}
        for app in applications
        if any(app.name.startswith(label) for label in labels)
    ]


class _CatalogTrace:
    """Bounded, passive worker/publication facts for owned fixture labels only."""

    def __init__(self, state, labels):
        self.state, self.labels = state, tuple(labels)
        self.lock = threading.RLock()
        self.reads, self.published = [], []
        self.publications = []
        self.operations, self.milestones = [], []
        self.operations_dropped = self.milestones_dropped = 0
        self.publications_dropped = 0
        self.reads_dropped = self.published_dropped = 0
        self._published_state = None
        self.observer = {"calls": 0, "first_ns": None, "last_ns": None, "max_gap_ms": 0}

    def mark(self, kind):
        row = {"kind": kind, "monotonic_ns": time.monotonic_ns(), **self.state()}
        with self.lock:
            if len(self.milestones) >= 64:
                del self.milestones[6]
                self.milestones_dropped += 1
            self.milestones.append(row)

    def operation(self, kind, action):
        row = {
            "kind": kind,
            "started_ns": time.monotonic_ns(),
            "thread": threading.get_ident(),
            "before": self.state(),
            "finished": False,
        }
        with self.lock:
            if len(self.operations) >= 128:
                del self.operations[6]
                self.operations_dropped += 1
            self.operations.append(row)
        try:
            return action()
        except BaseException as error:
            with self.lock:
                row["error"] = _error(error)
            raise
        finally:
            after, finished = self.state(), time.monotonic_ns()
            with self.lock:
                row.update(
                    after=after,
                    finished=True,
                    finished_ns=finished,
                    elapsed_ms=round((finished - row["started_ns"]) / 1_000_000),
                )

    def read(self, kind, action):
        started = time.monotonic()
        row = {
            "kind": kind,
            "before": self.state(),
            "finished": False,
            "started_ns": time.monotonic_ns(),
            "thread": threading.get_ident(),
        }
        with self.lock:
            if len(self.reads) >= 64:
                del self.reads[6]
                self.reads_dropped += 1
            self.reads.append(row)
        try:
            result = action()
        except BaseException as error:
            with self.lock:
                row["error"] = _error(error)
            raise
        else:
            applications = result.applications if kind == "metadata" else result
            with self.lock:
                row.update(
                    available=applications is not None,
                    fixture=_fixture_rows(applications, self.labels),
                )
            return result
        finally:
            after = self.state()
            with self.lock:
                row.update(
                    finished=True,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    after=after,
                    finished_ns=time.monotonic_ns(),
                )

    def publish(self, applications):
        observed = time.monotonic_ns()
        row = {**self.state(), "fixture": _fixture_rows(applications, self.labels)}
        with self.lock:
            previous = self.observer["last_ns"]
            self.observer["calls"] += 1
            if previous is None:
                self.observer["first_ns"] = observed
            else:
                self.observer["max_gap_ms"] = max(
                    self.observer["max_gap_ms"], (observed - previous) / 1_000_000
                )
            self.observer["last_ns"] = observed
            if row == self._published_state:
                return
            self._published_state = row
            if len(self.published) >= 64:
                del self.published[6]
                self.published_dropped += 1
            self.published.append({"monotonic_ns": observed, **row})

    def publication(self, epoch, generation, metadata):
        row = {
            "monotonic_ns": time.monotonic_ns(),
            "requested_epoch": epoch,
            "requested_generation": generation,
            "current": self.state(),
            "available": metadata.applications is not None,
            "fixture": _fixture_rows(metadata.applications, self.labels),
        }
        with self.lock:
            if len(self.publications) >= 64:
                del self.publications[6]
                self.publications_dropped += 1
            self.publications.append(row)

    def snapshot(self):
        with self.lock:
            # Callers serialize this detached copy after registry shutdown.
            return json.loads(
                json.dumps(
                    {
                        "reads": self.reads,
                        "published": self.published,
                        "publications": self.publications,
                        "operations": self.operations,
                        "milestones": self.milestones,
                        "observer": self.observer,
                        "operations_dropped": self.operations_dropped,
                        "milestones_dropped": self.milestones_dropped,
                        "publications_dropped": self.publications_dropped,
                        "reads_dropped": self.reads_dropped,
                        "published_dropped": self.published_dropped,
                    }
                )
            )


class _RegistryTimeline:
    """Time only calls the existing registry would make; restore every wrapper."""

    def __init__(self, registry, gio_catalog, trace):
        self.registry, self.gio_catalog = registry, gio_catalog
        self.source = registry._catalog.discover
        self.worker_source = gio_catalog._read_gio_discovery
        self.refresh = registry.refresh
        self.invalidate = registry._catalog.lookup.invalidate
        registry._catalog.discover = lambda **kwargs: trace.operation(
            "registry-source", lambda: self.source(**kwargs)
        )
        registry.refresh = lambda: trace.operation("gtk-refresh", self.refresh)
        registry._catalog.lookup.invalidate = lambda: trace.operation(
            "metadata-invalidate", self.invalidate
        )
        gio_catalog._read_gio_discovery = lambda: trace.operation(
            "worker-gio-discovery", self.worker_source
        )

    def close(self):
        self.registry._catalog.discover = self.source
        self.registry.refresh = self.refresh
        self.registry._catalog.lookup.invalidate = self.invalidate
        self.gio_catalog._read_gio_discovery = self.worker_source


def _wait_recovery(
    wait, predicate, message, *, oracle, observe, observed, labels, trace
):
    trace.mark("recovery-wait-start")
    try:
        wait(predicate, message)
    except TimeoutError:
        # The original outcome has already failed. A fresh Shell read here
        # cannot prime its cache and turn the operation into a pass.
        trace.mark("recovery-wait-failed")
        observed["failed_wait"] = message
        try:
            observed["published_at_failure"] = observe()
        except BaseException as error:
            observed["publication_observation_error"] = _error(error)
        try:
            observed["independent_shell_after_failure"] = _fixture_rows(
                trace.operation("failed-outcome-oracle", oracle), labels
            )
        except BaseException as error:
            observed["independent_shell_error"] = _error(error)
        raise
    else:
        trace.mark("recovery-wait-passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--restart-explorer",
        action="store_true",
        help="Restart only the exact shell on this explicitly owned test desktop",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned Windows desktop")
    from tools.windows.visual.tray_acceptance import runtime_origins, select_runtime

    runtime = select_runtime(args.installed_root, args.wheel)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    from docking.platform.applications import registry as registry_module
    from docking.platform.applications.implementations.windows import (
        catalog as gio_catalog,
    )
    from docking.platform.applications.implementations.windows import (
        native_catalog as native_catalog,
    )
    from docking.platform.applications.implementations.windows.documents import (
        launch_application_uris,
    )
    from docking.platform.applications.implementations.windows.native_catalog import (
        read_shell_applications,
    )
    from tools.windows.visual.classic_fixture import ClassicApplications
    from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json

    Gtk.init([])
    native = WindowsDesktop()
    before, explorer = native.workarea(), native.explorer()
    report = {
        "schema": 1,
        "ok": False,
        "forced_refresh": False,
        "phases": [],
        "native_notifications": 0,
        "options": {"restart_explorer": args.restart_explorer},
        "runtime": {
            "implementation": "installed-wheel" if runtime.installed else "source",
            "wheel_sha256": runtime.wheel_sha256,
            "origins": runtime_origins(
                runtime.root, {"registry": registry_module.__file__}
            ),
        },
    }
    gate_requested, entered, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    original_read = native_catalog.read_shell_applications
    original_metadata = native_catalog.read_application_metadata
    fixture = None
    trace = None
    registry_timeline = None
    metadata_lookup, original_publish = None, None

    def gated_read():
        def read():
            if gate_requested.is_set():
                gate_requested.clear()
                trace.operation("gate-enter", entered.set)
                assert trace.operation("held-read-wait", lambda: release.wait(90)), (
                    "Owned pending metadata read was not released"
                )
            return original_read()

        return trace.read("shell", read)

    try:
        with ClassicApplications(output) as fixture:
            from tools.windows.visual.application_graph import (
                create_application_registry,
            )

            registry = create_application_registry()

            def state():
                metadata = registry._catalog.lookup
                assert metadata is not None, (
                    "Windows catalog metadata lookup is unavailable"
                )
                with metadata._lock:
                    return {
                        "registry_generation": registry.generation,
                        "metadata_generation": metadata._generation,
                        "metadata_epoch": metadata._epoch,
                        "metadata_started": metadata._started,
                        "pending_generation": metadata._pending[0]
                        if metadata._pending is not None
                        else None,
                        "catalog_available": metadata.applications is not None,
                        "gate_entered": entered.is_set(),
                        "gate_released": release.is_set(),
                    }

            trace = _CatalogTrace(state, fixture.labels.values())
            registry_timeline = _RegistryTimeline(registry, gio_catalog, trace)
            metadata_lookup = registry._catalog.lookup
            original_publish = metadata_lookup._publish

            def publish(epoch, generation, metadata):
                trace.publication(epoch, generation, metadata)
                return original_publish(epoch, generation, metadata)

            metadata_lookup._publish = publish
            native_catalog.read_shell_applications = gated_read

            def metadata_read(*arguments, **keywords):
                return trace.read(
                    "metadata", lambda: original_metadata(*arguments, **keywords)
                )

            native_catalog.read_application_metadata = metadata_read
            factory = registry._change_monitor_factory

            def watch(changed):
                def notify():
                    report["native_notifications"] += 1
                    return trace.operation("native-change", changed)

                return factory(notify)

            registry._change_monitor_factory = watch
            registry.start()
            shared, failures = {}, []
            loop = GLib.MainLoop()

            def observe():
                shared["applications"] = {
                    app.name: app for app in registry.snapshot() if app.visible
                }
                shared["names"] = set(shared["applications"])
                trace.publish(tuple(shared["applications"].values()))
                return True

            timer = GLib.timeout_add(50, observe)

            def wait(predicate, message, seconds=20):
                def run():
                    end = time.monotonic() + seconds
                    while not predicate():
                        if time.monotonic() >= end:
                            raise TimeoutError(message)
                        time.sleep(0.05)

                return trace.operation("wait:" + message, run)

            def drive():
                try:
                    wait(
                        lambda: fixture.labels["First"] in shared.get("names", set()),
                        "Initial inventory did not publish",
                        60,
                    )
                    source = fixture.links / (fixture.labels["First"] + ".lnk")
                    renamed = fixture.links / (fixture.labels["First"] + " renamed.lnk")
                    for action in ("rename", "delete", "recreate"):
                        phase = {"action": action, "passed": False}
                        report["phases"].append(phase)
                        started = time.monotonic()
                        notifications = report["native_notifications"]
                        if action == "rename":
                            source.rename(renamed)
                        elif action == "delete":
                            renamed.unlink()
                        else:
                            fixture.update("First")
                        expected = renamed.stem if action == "rename" else source.stem

                        def matches(names, action=action, expected=expected):
                            if action == "delete":
                                return (
                                    source.stem not in names
                                    and renamed.stem not in names
                                )
                            return expected in names and (
                                action != "rename" or source.stem not in names
                            )

                        wait(
                            lambda: matches(
                                {app.name for app in read_shell_applications()}
                            ),
                            "Independent Shell inventory did not change",
                        )
                        phase["oracle_ms"] = round((time.monotonic() - started) * 1000)
                        wait(
                            lambda: matches(shared.get("names", set())),
                            "Native notification did not publish prompt inventory",
                        )
                        phase["published_ms"] = round(
                            (time.monotonic() - started) * 1000
                        )
                        phase["native_notifications"] = (
                            report["native_notifications"] - notifications
                        )
                        assert (
                            phase["published_ms"] < 20000
                            and phase["native_notifications"] > 0
                        )
                        phase["passed"] = True
                    if args.restart_explorer:
                        from tools.windows.visual.applications_domain_smoke import (
                            finish_program,
                        )

                        cached = shared["applications"][fixture.labels["First"]]
                        assert cached.document_target
                        facts_path = output / "facts-First-restart.json"
                        (
                            fixture.executables["First"].parent
                            / "selected-documents.json"
                        ).write_text(
                            json.dumps(
                                {
                                    "facts": str(facts_path),
                                    "title": fixture.labels["First"],
                                }
                            ),
                            encoding="utf-8",
                        )
                        document = fixture.root / "Pending restart résumé.txt"
                        document.write_text(
                            "Owned catalog restart document", encoding="utf-8"
                        )
                        trace.operation("gate-request", gate_requested.set)
                        source.rename(renamed)
                        wait(
                            entered.is_set,
                            "Native change did not begin the held real metadata read",
                        )
                        restart = trace.operation(
                            "owned-explorer-restart", native.restart_explorer
                        )
                        import psutil

                        replacement = psutil.Process(restart["after"]["pid"])
                        assert Path(replacement.exe()).samefile(
                            Path(os.environ["WINDIR"]) / "explorer.exe"
                        )
                        assert (
                            replacement.is_running()
                            and native.explorer() == restart["after"]
                        )
                        report["explorer_recovery"] = {
                            "passed": False,
                            "held_actual_read": True,
                            "cached_source_activation": False,
                            "after_restart_change": False,
                            "replacement_owned": True,
                            "shell_recovered": True,
                            **restart,
                        }
                        outcome = launch_application_uris(
                            cached.document_target, [document.as_uri()]
                        )
                        assert outcome is not None and outcome.started
                        facts = finish_program(
                            native,
                            facts_path,
                            fixture.executables["First"],
                            [
                                *fixture.arguments("First"),
                                str(document).replace("/", "\\"),
                            ],
                        )
                        assert Path(facts["cwd"]).samefile(
                            fixture.executables["First"].parent
                            / "working directory 0 résumé"
                        )
                        report["explorer_recovery"].update(
                            cached_source_activation=True, facts=facts
                        )
                        if outcome.process is not None:
                            assert outcome.process.poll() == 0
                        trace.operation("gate-release", release.set)
                        _wait_recovery(
                            wait,
                            lambda: (
                                renamed.stem in shared.get("names", set())
                                and source.stem not in shared.get("names", set())
                            ),
                            "Pending catalog did not recover after shell replacement",
                            oracle=original_read,
                            observe=trace.snapshot,
                            observed=report["explorer_recovery"],
                            labels=fixture.labels.values(),
                            trace=trace,
                        )
                        notifications = report["native_notifications"]
                        renamed.rename(source)
                        _wait_recovery(
                            wait,
                            lambda: (
                                source.stem in shared.get("names", set())
                                and renamed.stem not in shared.get("names", set())
                            ),
                            "Catalog subscription did not reconnect after replacement",
                            oracle=original_read,
                            observe=trace.snapshot,
                            observed=report["explorer_recovery"],
                            labels=fixture.labels.values(),
                            trace=trace,
                        )
                        assert report["native_notifications"] > notifications
                        report["explorer_recovery"] = {
                            "passed": True,
                            "held_actual_read": True,
                            "cached_source_activation": True,
                            "after_restart_change": True,
                            "replacement_owned": True,
                            "shell_recovered": replacement.is_running()
                            and native.explorer() == restart["after"],
                            "facts": facts,
                            **restart,
                        }
                except Exception as error:
                    failures.append(error)
                    report["error"] = _error(error)
                finally:
                    release.set()
                    GLib.idle_add(lambda: (loop.quit(), False)[1])

            worker = threading.Thread(target=drive, name="owned-catalog-changes")
            worker.start()
            try:
                loop.run()
            finally:
                registry.stop()
                GLib.source_remove(timer)
                worker.join(5)
                report["observer_stopped"] = not worker.is_alive()
                assert report["observer_stopped"]
            if failures:
                raise failures[0]
        report["owned_shortcuts_removed"] = not fixture.links.exists()
        report["workarea_restored"] = native.workarea() == before
        current = native.explorer()
        report["explorer_preserved"] = current == explorer
        report["explorer_recovered"] = bool(
            args.restart_explorer
            and report.get("explorer_recovery", {}).get("passed")
            and current == report["explorer_recovery"]["after"]
            and current["style"] == explorer["style"]
        )
        assert (
            report["owned_shortcuts_removed"]
            and report["workarea_restored"]
            and (report["explorer_preserved"] or report["explorer_recovered"])
        )
        report["ok"] = True
    finally:
        release.set()
        native_catalog.read_shell_applications = original_read
        native_catalog.read_application_metadata = original_metadata
        if metadata_lookup is not None and original_publish is not None:
            metadata_lookup._publish = original_publish
        if registry_timeline is not None:
            registry_timeline.close()
        inflight_failure = sys.exc_info()[0] is not None
        try:
            if args.restart_explorer and native.explorer() is None:
                native.ensure_explorer()
                report["cleanup_shell_recovered"] = True
        except (OSError, AssertionError) as error:
            report["shell_cleanup_error"] = {
                "class": type(error).__name__,
                "errno": error.errno if isinstance(error, OSError) else None,
            }
            if not inflight_failure:
                raise
        finally:
            if fixture is not None:
                report["owned_shortcuts_removed"] = not fixture.links.exists()
            report["workarea_restored"] = native.workarea() == before
            current = native.explorer()
            report["explorer_after"] = current
            report["explorer_preserved"] = current == explorer
            recovery = report.get("explorer_recovery")
            if recovery is not None:
                report["explorer_recovered"] = bool(
                    recovery.get("replacement_owned") and current == recovery["after"]
                )
            if trace is not None:
                report["catalog_trace"] = trace.snapshot()
            write_shared_json(output / "result.json", report)


if __name__ == "__main__":
    main()
