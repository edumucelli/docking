"""Tests for lazy live currency-rate loading."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from docking.applets.unitconverter.state import Unit
from docking.search.providers.converter import ConverterSearchProvider
from docking.search.recognizers.conversion import parse_currency_conversion
from docking.search.services.currency_rates import (
    CurrencyRatesCatalog,
    CurrencyRatesState,
)


def test_currency_rates_load_lazily_and_convert() -> None:
    finished = threading.Event()
    catalog = CurrencyRatesCatalog(
        schedule_idle=lambda callback, *args: int(not callback(*args)),
    )
    catalog._loader = lambda: (
        Unit("Euro", "EUR", 1.0),
        Unit("US Dollar", "USD", 0.8),
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


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 1.5e308])
def test_currency_conversion_rejects_non_finite_values_and_results(
    value: float,
) -> None:
    catalog = CurrencyRatesCatalog(schedule_idle=lambda _callback, *_args: 1)
    catalog._units = (
        Unit("Euro", "EUR", 1.0),
        Unit("US Dollar", "USD", 0.8),
    )

    assert catalog.convert(value=value, source_code="EUR", target_code="USD") is None


def test_currency_rate_failure_can_retry() -> None:
    attempts = 0
    finished = threading.Event()

    def load():
        nonlocal attempts
        attempts += 1
        return (Unit("Euro", "EUR", 1.0),) if attempts == 2 else None

    catalog = CurrencyRatesCatalog(
        schedule_idle=lambda callback, *args: int(not callback(*args)),
    )
    catalog._loader = load
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
    )
    catalog._loader = load
    catalog._ttl_seconds = 60
    catalog._clock = lambda: now[0]
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


def test_failed_currency_query_remains_retryable_without_restarting(monkeypatch):
    thread = MagicMock()
    monkeypatch.setattr(
        "docking.search.services.currency_rates.threading.Thread", thread
    )
    catalog = CurrencyRatesCatalog(schedule_idle=lambda _callback, *_args: 1)
    catalog.ensure_loaded()
    catalog._finish_load(catalog._generation, None)
    provider = ConverterSearchProvider(
        copy_text=lambda _text: None, currency_rates=catalog
    )
    request = parse_currency_conversion("10 USD to EUR")
    assert request is not None

    for _ in range(3):
        result = provider._currency_result(request)
        assert result.title == "Currency rates unavailable"
        assert result.actions[0].label == "Retry"
    assert thread.call_count == 1


def test_retry_during_refresh_replaces_request_and_rejects_old_result(monkeypatch):
    thread = MagicMock()
    monkeypatch.setattr(
        "docking.search.services.currency_rates.threading.Thread", thread
    )
    catalog = CurrencyRatesCatalog(schedule_idle=lambda _callback, *_args: 1)
    original = (Unit("Euro", "EUR", 1.0), Unit("Dollar", "USD", 0.8))
    catalog.ensure_loaded()
    catalog._finish_load(catalog._generation, original)
    catalog._loaded_at = -catalog._ttl_seconds
    catalog.ensure_loaded()
    old_generation = catalog._generation

    catalog.retry()

    assert thread.call_count == 3
    assert catalog._generation > old_generation
    catalog._finish_load(old_generation, (Unit("Old", "USD", 0.1),))
    assert catalog.units == original
    updated = (Unit("Euro", "EUR", 1.0), Unit("Dollar", "USD", 0.9))
    catalog._finish_load(catalog._generation, updated)
    assert catalog.state is CurrencyRatesState.READY
    assert catalog.units == updated


def test_failed_background_refresh_keeps_rates_without_request_loop(monkeypatch):
    thread = MagicMock()
    monkeypatch.setattr(
        "docking.search.services.currency_rates.threading.Thread", thread
    )
    now = [0.0]
    catalog = CurrencyRatesCatalog(schedule_idle=lambda _callback, *_args: 1)
    catalog._clock = lambda: now[0]
    units = (Unit("Euro", "EUR", 1.0), Unit("Dollar", "USD", 0.8))
    catalog.ensure_loaded()
    catalog._finish_load(catalog._generation, units)
    now[0] = catalog._ttl_seconds + 1
    catalog.ensure_loaded()
    catalog._finish_load(catalog._generation, None)

    for _ in range(3):
        catalog.ensure_loaded()
    assert thread.call_count == 2
    assert catalog.state is CurrencyRatesState.READY
    assert catalog.convert(value=10, source_code="USD", target_code="EUR") == 8

    now[0] += 61
    catalog.ensure_loaded()
    assert thread.call_count == 3
