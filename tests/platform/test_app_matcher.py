"""Tests for the shared AppIdMatcher.

Covers Wine matching, candidate generation, missed-lookups, visible aliases,
instance-hint matching, and backend-integration scenarios that were
previously split across X11 and Wayland test files.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.platform.applications.matcher as app_matcher_mod
from docking.platform.applications.constants import GNOME_APP_PREFIX
from docking.platform.applications.identity import LaunchProvenance, ProcessIdentity
from docking.platform.applications.matcher import (
    AppIdMatcher,
    _app_id_candidates,
    _class_group_candidates,
    _ensure_desktop_suffix,
    _normalize_alias,
    _wine_aliases_from_instance,
)
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationMatch,
    ApplicationOrigin,
    MatchMethod,
)


def _registry(*, resolve_by_wm_class=None, resolve=None):
    """Build a registry fake with configurable canonical resolution."""
    registry = MagicMock()
    registry.generation = 0
    if resolve_by_wm_class is not None:

        def resolve_all(alias):
            resolved = (
                resolve_by_wm_class(alias)
                if callable(resolve_by_wm_class)
                else resolve_by_wm_class
            )
            if resolved is None:
                return ()
            return resolved if isinstance(resolved, tuple) else (resolved,)

        registry.resolve_all_by_wm_class.side_effect = resolve_all
    else:
        registry.resolve_all_by_wm_class.return_value = ()
    if resolve is not None:
        registry.get.side_effect = (
            resolve if callable(resolve) else lambda _desktop_id: resolve
        )
    else:
        registry.get.return_value = None
    return registry


def _matcher(registry=None, *, cache_missed_desktop_ids: bool = False):
    process_identity_service = MagicMock()
    process_identity_service.identity_for_pid.return_value = None
    return AppIdMatcher(
        registry=registry or _registry(),
        process_identity_service=process_identity_service,
        cache_missed_desktop_ids=cache_missed_desktop_ids,
    )


def _match_id(matcher, app_id, **kwargs):
    result = matcher.match_result(app_id, **kwargs)
    return result.desktop_id if result is not None else None


def _application(
    desktop_id: str,
    name: str = "Test App",
    icon_name: str = "test-icon",
    wm_class: str = "",
    exec_line: str = "",
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        declared_icon=icon_name,
        wm_class=wm_class,
        exec_line=exec_line,
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=None,
        aliases=tuple(filter(None, (desktop_id.removesuffix(".desktop"), wm_class))),
        visible=True,
        has_gio_source=False,
    )


def _item(
    desktop_id: str,
    wm_class: str = "",
    *,
    exec_line: str = "",
    name: str = "Test App",
    icon_name: str = "test-icon",
) -> SimpleNamespace:
    application = _application(
        desktop_id,
        name=name,
        icon_name=icon_name,
        wm_class=wm_class,
        exec_line=exec_line,
    )
    return SimpleNamespace(
        kind="app",
        desktop_id=desktop_id,
        wm_class=wm_class,
        exec_line=exec_line,
        name=name,
        icon_name=icon_name,
        application_info=application,
    )


class TestNormalizeAlias:
    def test_strips_desktop_suffix_and_lowercases(self):
        assert _normalize_alias("Firefox.desktop") == "firefox"

    def test_strips_mixed_case_desktop_suffix(self):
        assert _normalize_alias("Firefox.Desktop") == "firefox"

    def test_handles_already_clean_input(self):
        assert _normalize_alias("Firefox") == "firefox"

    def test_strips_whitespace(self):
        assert _normalize_alias("  firefox.desktop  ") == "firefox"


class TestEnsureDesktopSuffix:
    def test_adds_suffix_when_missing(self):
        assert _ensure_desktop_suffix("firefox") == "firefox.desktop"

    def test_keeps_existing_suffix(self):
        assert _ensure_desktop_suffix("firefox.desktop") == "firefox.desktop"

    def test_keeps_mixed_case_existing_suffix(self):
        assert _ensure_desktop_suffix("Firefox.Desktop") == "Firefox.Desktop"

    def test_strips_whitespace(self):
        assert _ensure_desktop_suffix("  firefox  ") == "firefox.desktop"


class TestWineAliasesFromInstance:
    def test_extracts_basename_from_windows_path(self):
        aliases = _wine_aliases_from_instance("C:\\Program Files\\App\\Tool.exe")
        assert aliases == ["tool.exe", "tool", "c:\\program files\\app\\tool.exe"]

    def test_extracts_basename_from_unix_wine_path(self):
        aliases = _wine_aliases_from_instance("/home/user/Games/App/game.exe")
        assert aliases == ["game.exe", "game", "/home/user/games/app/game.exe"]

    def test_handles_simple_exe_name(self):
        aliases = _wine_aliases_from_instance("notepad.exe")
        assert aliases == ["notepad.exe", "notepad"]

    def test_non_exe_is_kept_as_is(self):
        aliases = _wine_aliases_from_instance("someapp")
        assert aliases == ["someapp"]

    def test_deduplicates(self):
        aliases = _wine_aliases_from_instance("tool.exe")  # basename == raw
        # "tool.exe" (basename), "tool" (without .exe), "tool.exe" (raw == basename)
        # After dedup: ["tool.exe", "tool"]
        assert aliases == ["tool.exe", "tool"]


class TestAppIdCandidates:
    def test_raw_and_lower_and_desktop_stripped(self):
        candidates = _app_id_candidates("Firefox")
        assert "Firefox" in candidates
        assert "firefox" in candidates

    def test_dot_suffix_extracts_last_segment(self):
        candidates = _app_id_candidates("org.gnome.Nautilus")
        assert "Nautilus" in candidates

    def test_snap_underscore_expands_prefixes(self):
        candidates = _app_id_candidates("firefox_firefox")
        assert "firefox" in candidates
        assert "firefox.desktop" in candidates
        assert "firefox_firefox" in candidates

    def test_snap_underscore_with_desktop_suffix(self):
        candidates = _app_id_candidates("firefox_firefox.desktop")
        assert "firefox" in candidates
        assert "firefox.desktop" in candidates

    def test_empty_input_returns_empty(self):
        assert _app_id_candidates("") == []

    def test_deduplicates(self):
        candidates = _app_id_candidates("app.app")
        assert len(candidates) == len(set(candidates))

    def test_multi_segment_snap(self):
        candidates = _app_id_candidates("app_plugin_feature")
        assert "app" in candidates
        assert "app_plugin" in candidates
        assert "app_plugin_feature" in candidates

    def test_exe_suffix_stripped(self):
        candidates = _app_id_candidates("notepad.exe")
        assert "notepad" in candidates  # stripped .exe
        assert "notepad.exe" in candidates  # original

    def test_exe_in_non_wine_context(self):
        """For a Wayland compositor reporting class='someapp.exe' directly."""
        candidates = _app_id_candidates("SomeApp.exe")
        assert "SomeApp" in candidates  # original case, .exe stripped
        assert "someapp" in candidates  # lower, .exe stripped
        assert "SomeApp.exe" in candidates  # original
        assert "someapp.exe" in candidates  # lower
        assert "exe" not in candidates  # avoid matching a generic exe.desktop


class TestClassGroupCandidates:
    def test_no_spaces(self):
        assert _class_group_candidates(
            class_lower="firefox",
            class_group="Firefox",
        ) == ["firefox", f"{GNOME_APP_PREFIX}Firefox"]

    def test_spaces_to_hyphens_and_joined(self):
        result = _class_group_candidates(
            class_lower="mongodb compass",
            class_group="MongoDB Compass",
        )
        assert "mongodb compass" in result
        assert "mongodb-compass" in result
        assert "mongodbcompass" in result
        assert f"{GNOME_APP_PREFIX}MongoDB Compass" in result

    def test_no_duplicates(self):
        result = _class_group_candidates(
            class_lower="simple",
            class_group="simple",
        )
        assert len(result) == len(set(result))


class TestVisibleAliases:
    def test_direct_wm_class_hit(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])

        # wm_class "Firefox" → normalized "firefox"
        assert _match_id(matcher, "Firefox") == "firefox.desktop"
        launcher.get.assert_not_called()

    def test_desktop_id_sans_suffix_hit(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("org.gnome.Nautilus.desktop", wm_class="")])

        # desktop_id sans suffix "org.gnome.Nautilus" → normalized
        assert _match_id(matcher, "org.gnome.Nautilus") == (
            "org.gnome.Nautilus.desktop"
        )
        launcher.get.assert_not_called()

    def test_instance_hint_matches_visible_alias(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox-Bin")])

        # instance_hint "Firefox-Bin" → normalized "firefox-bin" should match
        assert (
            _match_id(matcher, "Unknown", instance_hint="Firefox-Bin")
            == "firefox.desktop"
        )

    def test_sync_visible_items_rebuilds_cache(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])
        assert _match_id(matcher, "Firefox") == "firefox.desktop"

        # Rebuild with different items
        matcher.sync_visible_items([_item("chrome.desktop", wm_class="Chrome")])
        assert _match_id(matcher, "Firefox") is None
        assert _match_id(matcher, "Chrome") == "chrome.desktop"


class TestExecutableIdentity:
    @staticmethod
    def _binary(tmp_path, version: str):
        path = tmp_path / version / "bin" / "tool"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x7fELF")
        return path.resolve()

    @staticmethod
    def _script(tmp_path, version: str):
        path = tmp_path / version / "bin" / "tool.sh"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        return path.resolve()

    def test_exact_process_path_selects_matching_visible_registry(
        self, tmp_path, monkeypatch
    ):
        first = self._binary(tmp_path, "tool-v1")
        second = self._binary(tmp_path, "tool-v2")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(first),
                ),
                _item(
                    "tool-v2.desktop",
                    "SharedTool",
                    exec_line=str(second),
                ),
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=42, executable_path=first),
        )

        result = matcher.match_result("SharedTool", process_id=42)

        assert result is not None
        assert result.desktop_id == "tool-v1.desktop"
        assert result.application is not None

    def test_shared_wm_class_duplicate_paths_keep_first_indexed_registry(
        self, tmp_path, monkeypatch
    ):
        executable = self._binary(tmp_path, "shared")
        first = _application(
            desktop_id="first.desktop",
            wm_class="SharedTool",
            exec_line=str(executable),
        )
        second = _application(
            desktop_id="second.desktop",
            wm_class="SharedTool",
            exec_line=str(executable),
        )
        launcher = _registry()
        launcher.resolve_all_by_wm_class.side_effect = lambda alias: (
            (first, second) if alias.casefold() == "sharedtool" else ()
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=48, executable_path=executable),
        )

        result = matcher.match_result("SharedTool", process_id=48)

        assert result is not None
        assert result.desktop_id == "first.desktop"
        assert result.application is not None

    def test_alias_candidate_order_precedes_later_shared_class_path_match(
        self, tmp_path, monkeypatch
    ):
        executable = self._binary(tmp_path, "exact")
        earlier_alias = _application(
            desktop_id="vendor.SharedTool.desktop",
            wm_class="SharedTool",
        )
        later_exact = _application(
            desktop_id="shared-tool-exact.desktop",
            wm_class="SharedTool",
            exec_line=str(executable),
        )
        launcher = _registry()

        def resolve_all(alias):
            normalized = alias.casefold()
            if normalized == "vendor.sharedtool":
                return (earlier_alias,)
            if normalized == "sharedtool":
                return (earlier_alias, later_exact)
            return ()

        launcher.resolve_all_by_wm_class.side_effect = resolve_all
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=49, executable_path=executable),
        )

        result = matcher.match_result("vendor.SharedTool", process_id=49)

        assert result is not None
        assert result.desktop_id == "vendor.SharedTool.desktop"
        assert result.application is not None

    def test_exact_path_outside_alias_candidates_does_not_override_match(
        self, tmp_path, monkeypatch
    ):
        executable = self._binary(tmp_path, "unrelated")
        alias_candidate = _application(
            desktop_id="shared-tool.desktop",
            wm_class="SharedTool",
        )
        launcher = _registry()
        launcher.resolve_all_by_wm_class.side_effect = lambda alias: (
            (alias_candidate,) if alias.casefold() == "sharedtool" else ()
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=50, executable_path=executable),
        )

        result = matcher.match_result("SharedTool", process_id=50)

        assert result is not None
        assert result.desktop_id == "shared-tool.desktop"
        assert result.application is not None

    def test_conflicting_direct_process_path_creates_runtime_identity(
        self, tmp_path, monkeypatch
    ):
        pinned = self._binary(tmp_path, "tool-v1")
        running = self._binary(tmp_path, "tool-v2")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(pinned),
                    name="Shared Tool",
                )
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=43, executable_path=running),
        )

        result = matcher.match_result("SharedTool", process_id=43)

        assert result is not None
        expected_id = app_matcher_mod.desktop_entries.generated_desktop_id_for_path(
            running
        )
        assert result.desktop_id == expected_id
        assert isinstance(result, ApplicationMatch)
        assert isinstance(result.application, ApplicationInfo)
        assert result.application.desktop_id == expected_id
        assert result.application.executable_path == running
        assert result.application.name == "Shared Tool"
        assert result.application.declared_icon == "test-icon"
        assert result.application.wm_class == "SharedTool"
        assert result.application.origin is ApplicationOrigin.RUNTIME
        assert result.evidence.method is MatchMethod.RUNTIME_PATH_SPLIT

    def test_missing_process_path_preserves_visible_alias_fallback(
        self, tmp_path, monkeypatch
    ):
        pinned = self._binary(tmp_path, "tool-v1")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(pinned),
                )
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=44),
        )

        assert _match_id(matcher, "SharedTool", process_id=44) == "tool-v1.desktop"

    def test_sibling_bundle_wrapper_and_native_are_distinct(
        self, tmp_path, monkeypatch
    ):
        pinned = self._script(tmp_path, "tool-v1")
        running = self._binary(tmp_path, "tool-v2")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(pinned),
                )
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(pid=45, executable_path=running),
        )

        result = matcher.match_result("SharedTool", process_id=45)

        assert result is not None
        assert result.desktop_id != "tool-v1.desktop"
        assert result.application is not None
        assert result.application.origin is ApplicationOrigin.RUNTIME

    def test_wrapper_and_native_in_same_bundle_keep_family_fallback(
        self, tmp_path, monkeypatch
    ):
        pinned = self._script(tmp_path, "tool-v1")
        running = pinned.with_name("tool")
        running.write_bytes(b"\x7fELF")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(pinned),
                )
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(
                pid=46,
                executable_path=running.resolve(),
            ),
        )

        assert _match_id(matcher, "SharedTool", process_id=46) == "tool-v1.desktop"

    def test_wrapper_candidate_is_not_shadowed_by_native_runtime_item(
        self, tmp_path, monkeypatch
    ):
        wrapper = self._script(tmp_path, "tool-v1")
        native = self._binary(tmp_path, "tool-v2")
        interpreter = tmp_path / "tool-v1" / "runtime" / "bin" / "interpreter"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"\x7fELF")
        matcher = _matcher(_registry())
        matcher.sync_visible_items(
            [
                _item(
                    "tool-v1.desktop",
                    "SharedTool",
                    exec_line=str(wrapper),
                ),
                _item(
                    "tool-v2.desktop",
                    "SharedTool",
                    exec_line=str(native),
                ),
            ]
        )
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(
                pid=47,
                executable_path=interpreter.resolve(),
            ),
        )

        assert _match_id(matcher, "SharedTool", process_id=47) == "tool-v1.desktop"

    def test_system_bin_is_not_treated_as_specific_bundle_root(self):
        assert app_matcher_mod._specific_bundle_root(Path("/usr/bin/tool")) is None

    def test_launch_provenance_survives_wrapper_exec(self, tmp_path, monkeypatch):
        java = self._binary(tmp_path, "runtime")
        matcher = _matcher(_registry())
        matcher.sync_visible_items([])
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(
                pid=45,
                executable_path=java,
                launch=LaunchProvenance(
                    desktop_id="tool-wrapper.desktop",
                ),
            ),
        )

        assert _match_id(matcher, "SharedTool", process_id=45) == "tool-wrapper.desktop"

    def test_unresolved_launch_provenance_still_yields_desktop_id(
        self, tmp_path, monkeypatch
    ):
        runtime = self._binary(tmp_path, "removed-launcher")
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])
        monkeypatch.setattr(
            matcher._process_identity_service,
            "identity_for_pid",
            lambda _pid: ProcessIdentity(
                pid=51,
                executable_path=runtime,
                launch=LaunchProvenance(
                    desktop_id="removed-launcher.desktop",
                ),
            ),
        )

        result = matcher.match_result("SharedTool", process_id=51)

        assert result is not None
        assert result.desktop_id == "removed-launcher.desktop"
        assert result.application is None


class TestWineMatching:
    def test_wine_class_group_with_exe_instance_matches_via_resolve_by_wm_class(
        self,
    ):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="wine-program.desktop")
                if wm_class in ("tool.exe", "tool")
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert (
            _match_id(matcher, "Wine", instance_hint="C:\\App\\Tool.exe")
            == "wine-program.desktop"
        )

    def test_wine_class_group_with_exe_instance_matches_via_visible_aliases(
        self,
    ):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items(
            [_item("wine-notepad.desktop", wm_class="notepad.exe")]
        )

        assert (
            _match_id(matcher, "Wine", instance_hint="C:\\Windows\\notepad.exe")
            == "wine-notepad.desktop"
        )
        launcher.get.assert_not_called()

    def test_wine_class_group_without_exe_instance_falls_through(self):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="wine.desktop") if wm_class == "wine" else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("wine.desktop", wm_class="Wine")])

        # No instance_hint → no Wine special-casing → falls through to
        # visible aliases, where wm_class "Wine" → "wine" matches.
        assert _match_id(matcher, "Wine") == "wine.desktop"

    def test_non_wine_class_group_skips_wine_path(self):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="steam.desktop")
                if wm_class == "steam"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        # class_group is not "wine", so Wine path is skipped.
        # Falls through to candidates → resolve_by_wm_class.
        assert _match_id(matcher, "Steam", instance_hint="steam.exe") == "steam.desktop"

    def test_wine_class_group_with_non_exe_instance_falls_through(self):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="some-wine-app.desktop")
                if wm_class == "some-wine-app"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items(
            [_item("some-wine-app.desktop", wm_class="some-wine-app")]
        )

        # instance doesn't end with .exe → Wine path skipped → falls through
        assert (
            _match_id(matcher, "wine", instance_hint="some-wine-app")
            == "some-wine-app.desktop"
        )


class TestCandidateResolution:
    def test_space_to_hyphen_candidate_matches(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id == "mongodb-compass.desktop"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "MongoDB Compass") == "mongodb-compass.desktop"

    def test_gnome_prefix_candidate_matches(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id == "org.gnome.Terminal.desktop"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "Terminal") == "org.gnome.Terminal.desktop"

    def test_snap_container_app_id_matches(self):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="firefox.desktop")
                if wm_class == "firefox"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "firefox_firefox.desktop") == "firefox.desktop"

    def test_dot_suffix_candidate_matches(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id == "Nautilus.desktop"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "org.gnome.Nautilus") == "Nautilus.desktop"

    def test_raw_desktop_id_is_preferred_before_lowercase_variant(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id
                in {
                    "org.gnome.Nautilus.desktop",
                    "org.gnome.nautilus.desktop",
                }
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "org.gnome.Nautilus") == "org.gnome.Nautilus.desktop"

    def test_x11_order_prefers_lowercase_before_raw_class_group(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id in {"Terminal.desktop", "terminal.desktop"}
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert (
            _match_id(matcher, "Terminal", prefer_raw_app_id=False)
            == "terminal.desktop"
        )

    def test_x11_order_defers_wm_class_until_after_direct_candidates(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id == "org.gnome.Terminal.desktop"
                else None
            ),
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="terminal-wm-class.desktop")
                if wm_class == "terminal"
                else None
            ),
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert (
            _match_id(
                matcher,
                "Terminal",
                prefer_raw_app_id=False,
                defer_wm_class_lookup=True,
            )
            == "org.gnome.Terminal.desktop"
        )

    def test_exe_candidate_does_not_resolve_generic_exe_desktop_first(self):
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id in {"exe.desktop", "notepad.desktop"}
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "notepad.exe") == "notepad.desktop"
        resolved_ids = [call.args[0] for call in launcher.resolve.call_args_list]
        assert "exe.desktop" not in resolved_ids

    def test_candidate_order_is_stable(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        # Run multiple times - order should be identical each time.
        first_run = _match_id(matcher, "MongoDB Compass")
        second_run = _match_id(matcher, "MongoDB Compass")
        assert first_run == second_run

    def test_all_candidates_exhausted_returns_none(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "CompletelyUnknownApp_xyz") is None


class TestMissedCandidates:
    def test_x11_style_missed_candidate_skips_second_gio_call(self):
        launcher = _registry()
        matcher = _matcher(launcher, cache_missed_desktop_ids=True)
        matcher.sync_visible_items([])

        # First call - launcher.resolve returns None, candidate is memoized
        _match_id(matcher, "NoSuchApp")
        first_call_count = launcher.get.call_count

        # Second call with same app_id - missed candidates are skipped
        _match_id(matcher, "NoSuchApp")
        # resolve is still called for the same number because the raw
        # candidate is tried each time (missed cache is per-desktop_id,
        # not per-app_id input). But each specific desktop_id is only
        # tried once per match() call and skipped on subsequent calls.
        # The key insight: "nosuchapp.desktop" is added to missed set
        # after the first match() call.  On the second match() call the
        # loop hits "nosuchapp.desktop" again and skips it.
        assert launcher.get.call_count == first_call_count

    def test_wayland_style_misses_are_retried(self):
        ready = False

        def resolve(desktop_id):
            if ready and desktop_id == "FutureApp.desktop":
                return _application(desktop_id=desktop_id)
            return None

        launcher = _registry(resolve=resolve)
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "FutureApp") is None
        first_count = launcher.get.call_count

        ready = True
        assert _match_id(matcher, "FutureApp") == "FutureApp.desktop"
        assert launcher.get.call_count > first_count

    def test_x11_cached_miss_survives_visible_resync_before_registry_migration(self):
        ready = False

        def resolve(desktop_id):
            if ready and desktop_id == "FutureApp.desktop":
                return _application(desktop_id=desktop_id)
            return None

        launcher = _registry(resolve=resolve)
        matcher = _matcher(launcher, cache_missed_desktop_ids=True)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "FutureApp") is None
        first_count = launcher.get.call_count

        ready = True
        matcher.sync_visible_items([])
        assert _match_id(matcher, "FutureApp") is None
        assert launcher.get.call_count == first_count

    def test_successful_wm_class_match_uses_launcher_index(self):
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="demo.desktop")
                if wm_class == "demoapp"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "DemoApp") == "demo.desktop"


class TestEdgeCases:
    def test_empty_app_id_returns_none(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "") is None

    def test_instance_hint_none_is_harmless(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])

        # instance_hint=None should not affect matching
        assert _match_id(matcher, "Firefox", instance_hint=None) == "firefox.desktop"

    def test_whitespace_app_id_returns_none(self):
        launcher = _registry()
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "   ") is None


class TestBackendIntegration:
    """Simulate how each backend calls AppIdMatcher."""

    def test_x11_fake_window_flow(self):
        """X11: class_group + class_instance passed to match()."""
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="winbox.desktop")
                if wm_class in ("tool.exe", "tool")
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert (
            _match_id(
                matcher,
                "Wine",
                instance_hint="C:\\Games\\WinBox\\tool.exe",
            )
            == "winbox.desktop"
        )

    def test_wayland_hyprland_flow(self):
        """Hyprland: class field from JSON passed as app_id.

        On Hyprland, ``class`` is typically the WM_CLASS instance name,
        e.g. ``"notepad.exe"`` for a Wine app.  The matcher strips the
        ``.exe`` suffix as a candidate so the launcher can resolve a
        desktop entry whose ``StartupWMClass`` or alias is ``notepad``.
        """
        launcher = _registry(
            resolve=lambda desktop_id, **_: (
                _application(desktop_id=desktop_id)
                if desktop_id == "notepad.desktop"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "notepad.exe") == "notepad.desktop"

    def test_wayland_wlr_flow(self):
        """wlr-foreign-toplevel: app_id from compositor."""
        launcher = _registry(
            resolve_by_wm_class=lambda wm_class: (
                _application(desktop_id="org.gnome.Nautilus.desktop")
                if wm_class.lower() == "nautilus"
                else None
            )
        )
        matcher = _matcher(launcher)
        matcher.sync_visible_items([])

        assert _match_id(matcher, "org.gnome.Nautilus") == "org.gnome.Nautilus.desktop"
