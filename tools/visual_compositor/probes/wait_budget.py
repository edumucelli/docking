"""How long the compositor probes wait for a state change to converge.

The probes poll a compositor nested in a container, where a callback that
arrives immediately on a developer machine can take an order of magnitude
longer under load. The budget is generous and overridable per session, and
each failure reports how long it waited so the next tuning decision rests on
evidence rather than a guess. Raise LAB_WAIT_BUDGET_S on a slow runner
instead of editing the probes.
"""

from __future__ import annotations

import math
import os
import time

DEFAULT_WAIT_BUDGET_S = 30.0
WAIT_BUDGET_ENV = "LAB_WAIT_BUDGET_S"


def wait_budget_seconds() -> float:
    """Return the convergence budget in seconds."""
    raw = os.environ.get(WAIT_BUDGET_ENV)
    if raw is None:
        return DEFAULT_WAIT_BUDGET_S
    try:
        budget = float(raw)
    except ValueError:
        return DEFAULT_WAIT_BUDGET_S
    # A non-finite value would make a wait never expire, so fall back instead.
    if not math.isfinite(budget) or budget <= 0:
        return DEFAULT_WAIT_BUDGET_S
    return budget


def wait_deadline() -> float:
    """Return a monotonic deadline for one convergence wait."""
    return time.monotonic() + wait_budget_seconds()


def wait_has_timed_out(message: object) -> AssertionError:
    """Build the error raised when a wait exhausts its budget."""
    return AssertionError(f"{message} [waited {wait_budget_seconds():.0f}s]")
