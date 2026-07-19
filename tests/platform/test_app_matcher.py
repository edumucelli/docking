"""Tests for the shared AppIdMatcher.

Covers Wine matching, candidate generation, missed-lookups, visible aliases,
instance-hint matching, and backend-integration scenarios that were
previously split across X11 and Wayland test files.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.app_matcher import (
    AppIdMatcher,
    _app_id_candidates,
    _class_group_candidates,
    _ensure_desktop_suffix,
    _normalize_alias,
    _wine_aliases_from_instance,
)
from docking.platform.launcher import GNOME_APP_PREFIX


def _launcher(*, resolve_by_wm_class=None, resolve=None):
    """Build a fake Launcher with the given resolve behaviours."""
    launcher = MagicMock()
    if resolve_by_wm_class is not None:
        launcher.resolve_by_wm_class.side_effect = (
            resolve_by_wm_class
            if callable(resolve_by_wm_class)
            else lambda _wm_class: resolve_by_wm_class
        )
    else:
        launcher.resolve_by_wm_class.return_value = None
    if resolve is not None:
        launcher.resolve.side_effect = (
            resolve if callable(resolve) else lambda _desktop_id, **_: resolve
        )
    else:
        launcher.resolve.return_value = None
    return launcher


@dataclass
class _FakeDesktopInfo:
    desktop_id: str
    name: str = "Test App"
    icon_name: str = "test-icon"
    wm_class: str = ""
    exec_line: str = ""


def _item(desktop_id: str, wm_class: str = "") -> SimpleNamespace:
    return SimpleNamespace(desktop_id=desktop_id, wm_class=wm_class)


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
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])

        # wm_class "Firefox" → normalized "firefox"
        assert matcher.match("Firefox") == "firefox.desktop"
        launcher.resolve.assert_not_called()

    def test_desktop_id_sans_suffix_hit(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("org.gnome.Nautilus.desktop", wm_class="")])

        # desktop_id sans suffix "org.gnome.Nautilus" → normalized
        assert matcher.match("org.gnome.Nautilus") == ("org.gnome.Nautilus.desktop")
        launcher.resolve.assert_not_called()

    def test_instance_hint_matches_visible_alias(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox-Bin")])

        # instance_hint "Firefox-Bin" → normalized "firefox-bin" should match
        assert (
            matcher.match("Unknown", instance_hint="Firefox-Bin") == "firefox.desktop"
        )

    def test_sync_visible_items_rebuilds_cache(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])
        assert matcher.match("Firefox") == "firefox.desktop"

        # Rebuild with different items
        matcher.sync_visible_items([_item("chrome.desktop", wm_class="Chrome")])
        assert matcher.match("Firefox") is None
        assert matcher.match("Chrome") == "chrome.desktop"


class TestWineMatching:
    def test_wine_class_group_with_exe_instance_matches_via_resolve_by_wm_class(
        self,
    ):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="wine-program.desktop")
                if wm_class in ("tool.exe", "tool")
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert (
            matcher.match("Wine", instance_hint="C:\\App\\Tool.exe")
            == "wine-program.desktop"
        )

    def test_wine_class_group_with_exe_instance_matches_via_visible_aliases(
        self,
    ):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items(
            [_item("wine-notepad.desktop", wm_class="notepad.exe")]
        )

        assert (
            matcher.match("Wine", instance_hint="C:\\Windows\\notepad.exe")
            == "wine-notepad.desktop"
        )
        launcher.resolve.assert_not_called()

    def test_wine_class_group_without_exe_instance_falls_through(self):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="wine.desktop")
                if wm_class == "wine"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("wine.desktop", wm_class="Wine")])

        # No instance_hint → no Wine special-casing → falls through to
        # visible aliases, where wm_class "Wine" → "wine" matches.
        assert matcher.match("Wine") == "wine.desktop"

    def test_non_wine_class_group_skips_wine_path(self):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="steam.desktop")
                if wm_class == "steam"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        # class_group is not "wine", so Wine path is skipped.
        # Falls through to candidates → resolve_by_wm_class.
        assert matcher.match("Steam", instance_hint="steam.exe") == "steam.desktop"

    def test_wine_class_group_with_non_exe_instance_falls_through(self):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="some-wine-app.desktop")
                if wm_class == "some-wine-app"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items(
            [_item("some-wine-app.desktop", wm_class="some-wine-app")]
        )

        # instance doesn't end with .exe → Wine path skipped → falls through
        assert (
            matcher.match("wine", instance_hint="some-wine-app")
            == "some-wine-app.desktop"
        )


class TestCandidateResolution:
    def test_space_to_hyphen_candidate_matches(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id == "mongodb-compass.desktop"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("MongoDB Compass") == "mongodb-compass.desktop"

    def test_gnome_prefix_candidate_matches(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id == "org.gnome.Terminal.desktop"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("Terminal") == "org.gnome.Terminal.desktop"

    def test_snap_container_app_id_matches(self):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="firefox.desktop")
                if wm_class == "firefox"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("firefox_firefox.desktop") == "firefox.desktop"

    def test_dot_suffix_candidate_matches(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id == "Nautilus.desktop"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("org.gnome.Nautilus") == "Nautilus.desktop"

    def test_raw_desktop_id_is_preferred_before_lowercase_variant(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id
                in {
                    "org.gnome.Nautilus.desktop",
                    "org.gnome.nautilus.desktop",
                }
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("org.gnome.Nautilus") == "org.gnome.Nautilus.desktop"

    def test_x11_order_prefers_lowercase_before_raw_class_group(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id in {"Terminal.desktop", "terminal.desktop"}
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("Terminal", prefer_raw_app_id=False) == "terminal.desktop"

    def test_x11_order_defers_wm_class_until_after_direct_candidates(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id == "org.gnome.Terminal.desktop"
                else None
            ),
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="terminal-wm-class.desktop")
                if wm_class == "terminal"
                else None
            ),
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert (
            matcher.match(
                "Terminal",
                prefer_raw_app_id=False,
                defer_wm_class_lookup=True,
            )
            == "org.gnome.Terminal.desktop"
        )

    def test_exe_candidate_does_not_resolve_generic_exe_desktop_first(self):
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id in {"exe.desktop", "notepad.desktop"}
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("notepad.exe") == "notepad.desktop"
        resolved_ids = [call.args[0] for call in launcher.resolve.call_args_list]
        assert "exe.desktop" not in resolved_ids

    def test_candidate_order_is_stable(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        # Run multiple times - order should be identical each time.
        first_run = matcher.match("MongoDB Compass")
        second_run = matcher.match("MongoDB Compass")
        assert first_run == second_run

    def test_all_candidates_exhausted_returns_none(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("CompletelyUnknownApp_xyz") is None


class TestMissedCandidates:
    def test_x11_style_missed_candidate_skips_second_gio_call(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher, cache_missed_desktop_ids=True)
        matcher.sync_visible_items([])

        # First call - launcher.resolve returns None, candidate is memoized
        matcher.match("NoSuchApp")
        first_call_count = launcher.resolve.call_count

        # Second call with same app_id - missed candidates are skipped
        matcher.match("NoSuchApp")
        # resolve is still called for the same number because the raw
        # candidate is tried each time (missed cache is per-desktop_id,
        # not per-app_id input). But each specific desktop_id is only
        # tried once per match() call and skipped on subsequent calls.
        # The key insight: "nosuchapp.desktop" is added to missed set
        # after the first match() call.  On the second match() call the
        # loop hits "nosuchapp.desktop" again and skips it.
        assert launcher.resolve.call_count == first_call_count

    def test_wayland_style_misses_are_retried(self):
        ready = False

        def resolve(desktop_id, **_):
            if ready and desktop_id == "FutureApp.desktop":
                return _FakeDesktopInfo(desktop_id=desktop_id)
            return None

        launcher = _launcher(resolve=resolve)
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("FutureApp") is None
        first_count = launcher.resolve.call_count

        ready = True
        assert matcher.match("FutureApp") == "FutureApp.desktop"
        assert launcher.resolve.call_count > first_count

    def test_successful_wm_class_match_uses_launcher_index(self):
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="demo.desktop")
                if wm_class == "demoapp"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("DemoApp") == "demo.desktop"


class TestEdgeCases:
    def test_empty_app_id_returns_none(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("") is None

    def test_instance_hint_none_is_harmless(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([_item("firefox.desktop", wm_class="Firefox")])

        # instance_hint=None should not affect matching
        assert matcher.match("Firefox", instance_hint=None) == "firefox.desktop"

    def test_whitespace_app_id_returns_none(self):
        launcher = _launcher()
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("   ") is None


class TestBackendIntegration:
    """Simulate how each backend calls AppIdMatcher."""

    def test_x11_fake_window_flow(self):
        """X11: class_group + class_instance passed to match()."""
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="winbox.desktop")
                if wm_class in ("tool.exe", "tool")
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert (
            matcher.match(
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
        launcher = _launcher(
            resolve=lambda desktop_id, **_: (
                _FakeDesktopInfo(desktop_id=desktop_id)
                if desktop_id == "notepad.desktop"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("notepad.exe") == "notepad.desktop"

    def test_wayland_wlr_flow(self):
        """wlr-foreign-toplevel: app_id from compositor."""
        launcher = _launcher(
            resolve_by_wm_class=lambda wm_class: (
                _FakeDesktopInfo(desktop_id="org.gnome.Nautilus.desktop")
                if wm_class.lower() == "nautilus"
                else None
            )
        )
        matcher = AppIdMatcher(launcher=launcher)
        matcher.sync_visible_items([])

        assert matcher.match("org.gnome.Nautilus") == "org.gnome.Nautilus.desktop"
