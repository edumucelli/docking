"""Behave environment setup for deterministic dock scenarios."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.bdd_support.harness import DockHarness


def before_scenario(context, _scenario) -> None:
    context.harness = DockHarness()
    context.harness.start()


def after_scenario(context, _scenario) -> None:
    if hasattr(context, "harness"):
        context.harness.stop()
