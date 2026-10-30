"""Observe prompt real application inventory changes without forcing a refresh."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


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
    from docking.platform.backends.windows import catalog as native_catalog
    from docking.platform.backends.windows.catalog import read_shell_applications
    from docking.platform.backends.windows.documents import launch_application_uris
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
    fixture = None

    def gated_read():
        if gate_requested.is_set():
            gate_requested.clear()
            entered.set()
            assert release.wait(90), "Owned pending metadata read was not released"
        return original_read()

    if args.restart_explorer:
        native_catalog.read_shell_applications = gated_read
    try:
        with ClassicApplications(output) as fixture:
            registry = registry_module.ApplicationRegistry()
            factory = registry._change_monitor_factory

            def watch(changed):
                def notify():
                    report["native_notifications"] += 1
                    changed()

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
                return True

            timer = GLib.timeout_add(50, observe)

            def wait(predicate, message, seconds=20):
                end = time.monotonic() + seconds
                while not predicate():
                    if time.monotonic() >= end:
                        raise TimeoutError(message)
                    time.sleep(0.05)

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
                        gate_requested.set()
                        source.rename(renamed)
                        wait(
                            entered.is_set,
                            "Native change did not begin the held real metadata read",
                        )
                        restart = native.restart_explorer()
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
                        release.set()
                        wait(
                            lambda: (
                                renamed.stem in shared.get("names", set())
                                and source.stem not in shared.get("names", set())
                            ),
                            "Pending catalog did not recover after shell replacement",
                        )
                        notifications = report["native_notifications"]
                        renamed.rename(source)
                        wait(
                            lambda: (
                                source.stem in shared.get("names", set())
                                and renamed.stem not in shared.get("names", set())
                            ),
                            "Catalog subscription did not reconnect after replacement",
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
                    report["error"] = {
                        "class": type(error).__name__,
                        "errno": error.errno if isinstance(error, OSError) else None,
                    }
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
            write_shared_json(output / "result.json", report)


if __name__ == "__main__":
    main()
