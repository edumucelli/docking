"""Tests for lazy live currency-rate loading."""

from __future__ import annotations

import threading

import pytest

from docking.applets.unitconverter.state import Unit
from docking.search.currency import CurrencyRatesCatalog, CurrencyRatesState


def test_currency_rates_load_lazily_and_convert() -> None:
    finished = threading.Event()
    catalog = CurrencyRatesCatalog(
        schedule_idle=lambda callback, *args: int(not callback(*args)),
        loader=lambda: (
            Unit("Euro", "EUR", 1.0),
            Unit("US Dollar", "USD", 0.8),
        ),
    )
    catalog.add_listener(
        lambda: finished.set() if catalog.state is CurrencyRatesState.READY else None
    )

    assert catalog.state is CurrencyRatesState.IDLE
    catalog.ensure_loaded()
    assert finished.wait(timeout=1)
    assert catalog.state is CurrencyRatesState.READY
    assert catalog.convert(
        value=10,
        source_code="USD",
        target_code="EUR",
    ) == pytest.approx(8)


def test_currency_rate_failure_can_retry() -> None:
    attempts = 0
    finished = threading.Event()

    def load():
        nonlocal attempts
        attempts += 1
        return (Unit("Euro", "EUR", 1.0),) if attempts == 2 else None

    catalog = CurrencyRatesCatalog(
        schedule_idle=lambda callback, *args: int(not callback(*args)),
        loader=load,
    )
    catalog.add_listener(
        lambda: (
            finished.set()
            if catalog.state in {CurrencyRatesState.ERROR, CurrencyRatesState.READY}
            else None
        )
    )

    catalog.ensure_loaded()
    assert finished.wait(timeout=1)
    assert catalog.state is CurrencyRatesState.ERROR
    finished.clear()
    catalog.retry()
    assert finished.wait(timeout=1)
    assert catalog.state is CurrencyRatesState.READY


def test_ready_rates_refresh_after_ttl_while_remaining_available() -> None:
    now = [0.0]
    attempts = 0
    refreshed = threading.Event()

    def load():
        nonlocal attempts
        attempts += 1
        return (
            Unit("Euro", "EUR", 1.0),
            Unit("US Dollar", "USD", 0.8 + attempts / 100),
        )

    catalog = CurrencyRatesCatalog(
        schedule_idle=lambda callback, *args: int(not callback(*args)),
        loader=load,
        ttl_seconds=60,
        clock=lambda: now[0],
    )
    catalog.add_listener(
        lambda: refreshed.set() if catalog.state is CurrencyRatesState.READY else None
    )
    catalog.ensure_loaded()
    assert refreshed.wait(timeout=1)
    assert attempts == 1
    refreshed.clear()

    now[0] = 61
    catalog.ensure_loaded()

    assert catalog.state is CurrencyRatesState.READY
    assert refreshed.wait(timeout=1)
    assert attempts == 2
