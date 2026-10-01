"""Behave environment setup for deterministic dock scenarios."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.bdd_support.harness import DockHarness


def before_scenario(context, scenario) -> None:
    if "atspi_session" in scenario.effective_tags:
        context.accessibility_sessions = []
        return
    if "gtk_preview" in scenario.effective_tags:
        from tests.ui.preview_support import PreviewHarness

        context.preview = PreviewHarness()
        return
    if "gtk_stack" in scenario.effective_tags:
        from tests.ui.stack_support import StackHarness

        context.stack = StackHarness()
        return
    context.harness = DockHarness()
    context.harness.start()


def after_scenario(context, _scenario) -> None:
    if hasattr(context, "accessibility_sessions"):
        for session in reversed(context.accessibility_sessions):
            session.close()
    if hasattr(context, "preview"):
        context.preview.close()
    if hasattr(context, "harness"):
        context.harness.stop()
    if hasattr(context, "stack"):
        context.stack.close()
