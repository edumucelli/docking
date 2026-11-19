"""Tests for startup tips state and rotation."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

import docking.core.tips as tips_mod
from docking.core.tips import (
    FIRST_TIP_ID,
    STARTUP_TIPS,
    StartupTipsState,
    load_state,
    save_state,
    select_startup_tip,
)
from docking.platform.host.base import HostFamily


class TestStartupTipsState:
    def test_missing_file_returns_defaults(self, tmp_path):
        assert load_state(path=tmp_path / "tips.json") == StartupTipsState()

    def test_invalid_payload_returns_defaults(self, tmp_path, caplog):
        path = tmp_path / "tips.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="docking.tips"):
            state = load_state(path=path)

        assert state == StartupTipsState()
        assert "Invalid startup tips state payload" in caplog.text

    def test_round_trip(self, tmp_path):
        path = tmp_path / "tips.json"
        state = StartupTipsState(shown_tip_ids=(FIRST_TIP_ID, "recent-files"))

        save_state(state, path=path)

        assert load_state(path=path) == state

    def test_string_values_are_normalized_and_deduped(self, tmp_path):
        path = tmp_path / "tips.json"
        path.write_text(
            json.dumps(
                {
                    "shown_tip_ids": [
                        FIRST_TIP_ID,
                        " recent-files ",
                        FIRST_TIP_ID,
                        "",
                    ]
                }
            ),
            encoding="utf-8",
        )

        assert load_state(path=path).shown_tip_ids == (
            FIRST_TIP_ID,
            "recent-files",
        )


class TestSelectStartupTip:
    def test_disabled_returns_none_without_creating_state(self, tmp_path):
        path = tmp_path / "tips.json"

        tip = select_startup_tip(enabled=False, path=path)

        assert tip is None
        assert not path.exists()

    def test_first_tip_is_fixed(self, tmp_path):
        path = tmp_path / "tips.json"

        tip = select_startup_tip(
            enabled=True,
            path=path,
            chooser=lambda tips: tips[-1],
        )

        assert tip is not None
        assert tip.id == FIRST_TIP_ID
        assert load_state(path=path).shown_tip_ids == (FIRST_TIP_ID,)

    def test_later_tip_uses_unshown_candidates(self, tmp_path):
        path = tmp_path / "tips.json"
        save_state(StartupTipsState(shown_tip_ids=(FIRST_TIP_ID,)), path=path)
        chosen_candidates = []

        def choose(candidates):
            chosen_candidates.extend(tip.id for tip in candidates)
            return candidates[0]

        tip = select_startup_tip(enabled=True, path=path, chooser=choose)

        assert tip is not None
        assert tip.id != FIRST_TIP_ID
        assert FIRST_TIP_ID not in chosen_candidates
        assert tip.id in load_state(path=path).shown_tip_ids

    def test_cycle_resets_after_all_tips_were_shown(self, tmp_path):
        path = tmp_path / "tips.json"
        save_state(
            StartupTipsState(shown_tip_ids=tuple(tip.id for tip in STARTUP_TIPS)),
            path=path,
        )

        tip = select_startup_tip(
            enabled=True,
            path=path,
            chooser=lambda candidates: candidates[-1],
        )

        assert tip is not None
        assert tip.id != FIRST_TIP_ID
        assert load_state(path=path).shown_tip_ids == (FIRST_TIP_ID, tip.id)

    @pytest.mark.parametrize(
        ("host_family", "offered"),
        [(HostFamily.LINUX, True), (HostFamily.BSD, True), (HostFamily.WINDOWS, False)],
    )
    def test_import_tip_is_offered_where_import_is_available(
        self, tmp_path, monkeypatch, host_family, offered
    ):
        monkeypatch.setattr(
            tips_mod, "host_services", lambda: SimpleNamespace(family=host_family)
        )
        path = tmp_path / "tips.json"
        save_state(StartupTipsState(shown_tip_ids=(FIRST_TIP_ID,)), path=path)

        def choose(candidates):
            matching = [tip for tip in candidates if tip.id == "import-dock-layout"]
            assert bool(matching) is offered
            return matching[0] if matching else candidates[0]

        tip = select_startup_tip(enabled=True, path=path, chooser=choose)

        assert tip is not None
        assert (tip.id == "import-dock-layout") is offered

    def test_catalog_has_unique_tips(self):
        ids = [tip.id for tip in STARTUP_TIPS]

        assert len(STARTUP_TIPS) == 26
        assert len(ids) == len(set(ids))
        assert ids[0] == tips_mod.FIRST_TIP_ID
        assert {
            "applications-drag-to-dock",
            "clock-calendar",
            "devices-stack",
            "whatsapp-applet",
        } <= set(ids)
