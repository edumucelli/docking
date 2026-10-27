"""Launch two native shortcut profiles of one executable and verify identity."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a native Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from docking.launcher import prepare_display

    prepare_display()
    from PIL import ImageGrab

    from docking.platform.applications.identity import (
        LaunchProvenanceStore,
        ProcessIdentityService,
    )
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.matcher import AppIdMatcher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.applications.types import (
        ApplicationInfo,
        ApplicationLocation,
        ApplicationOrigin,
        MatchMethod,
    )
    from tools.windows.visual.classic_fixture import ClassicApplications, write_shortcut
    from tools.windows.visual.desktop_driver import WindowsDesktop, read_shared_json

    @dataclass
    class FixtureLaunchers:
        records: tuple[ApplicationInfo, ...]

        def create(self, target: str | Path, *, startup_wm_class: str) -> str | None:
            raise AssertionError("This fixture only supplies existing launchers")

        def applications(self) -> tuple[ApplicationInfo, ...]:
            return self.records

        def directories(self) -> tuple[Path, ...]:
            return ()

    native = WindowsDesktop()
    assert not any(window["title"] == "Docking" for window in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    report = {"launches": []}
    with ClassicApplications(output) as fixture:
        executable = fixture.executables["Other"]
        applications = []
        for key in ("First", "Second"):
            phase = int(key == "Second")
            shortcut = fixture.root / f"private-{key}.lnk"
            working = (
                fixture.executables[key].parent / f"working directory {phase} résumé"
            )
            working.mkdir(exist_ok=True)
            write_shortcut(
                shortcut,
                executable=executable,
                argv=fixture.arguments(key, phase),
                cwd=working,
            )
            applications.append(
                ApplicationInfo(
                    desktop_id=f"Docking.Profile.{key}",
                    name=fixture.labels[key],
                    declared_icon="application-x-executable",
                    wm_class="sharedfixture",
                    exec_line="",
                    origin=ApplicationOrigin.GENERATED,
                    location=ApplicationLocation.HOST,
                    desktop_file=None,
                    executable_path=executable,
                    aliases=("sharedfixture",),
                    visible=True,
                    has_gio_source=False,
                    launch_target=str(shortcut),
                )
            )
        registry = ApplicationRegistry(
            application_source=lambda: (),
            executable_registration=FixtureLaunchers(tuple(applications)),
        )
        registry.refresh()
        store = LaunchProvenanceStore()
        service = ProcessIdentityService(store)
        matcher = AppIdMatcher(registry, service)
        launcher = ApplicationLauncher(registry, store)
        for key, application in zip(("First", "Second"), applications, strict=True):
            phase = int(key == "Second")
            assert launcher.launch(application.desktop_id)
            path = fixture.facts_path(key, phase)
            native.wait_for(path.is_file, "Shell launch did not report native facts")
            facts = read_shared_json(path)
            assert Path(facts["executable"]).samefile(executable)
            assert Path(facts["cwd"]).samefile(
                fixture.executables[key].parent / f"working directory {phase} résumé"
            )
            assert facts["argv"] == fixture.arguments(key, phase)[2:]
            windows = native.wait_for(
                lambda pid=facts["pid"]: native.windows(pid),
                "Owned native program did not map",
            )
            assert len(windows) == 1 and windows[0]["title"] == fixture.labels[key]
            x1, y1, _x2, _y2 = windows[0]["rect"]
            native.drag((x1 + 100, y1 + 15), (180 + phase * 550, 215))
            provenance = store.provenance_for_pid(facts["pid"])
            assert (
                provenance is not None
                and provenance.desktop_id == application.desktop_id
            )
            match = matcher.match_result("sharedfixture", process_id=facts["pid"])
            assert match is not None and match.desktop_id == application.desktop_id
            assert match.evidence.method is MatchMethod.LAUNCH_PROVENANCE
            report["launches"].append({"id": application.desktop_id, "facts": facts})
        assert (
            report["launches"][0]["facts"]["pid"]
            != report["launches"][1]["facts"]["pid"]
        )
        time.sleep(0.7)  # Let the native move/opening animations finish.
        ImageGrab.grab(all_screens=True).save(output / "two-shortcut-profiles.png")
        for record in report["launches"]:
            pid = record["facts"]["pid"]
            for window in native.windows(pid):
                assert window["title"] in fixture.labels.values()
                assert native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            deadline = time.monotonic() + 20
            while (
                store.provenance_for_pid(pid) is not None
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            assert store.provenance_for_pid(pid) is None
            assert not native.windows(pid)
        report["completed_processes_pruned"] = True
    assert native.workarea() == baseline and native.explorer() == explorer
    report.update(
        ok=True, same_executable=True, distinct_profiles=True, fixture_cleanup=True
    )
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Native shell identity, arguments, cwd, lifetime and cleanup passed")


if __name__ == "__main__":
    main()
