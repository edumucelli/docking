"""Lazy background cache for live currency conversion factors."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum

from docking.applets.unitconverter.state import (
    Unit,
    fetch_currency_rates,
    set_currency_units,
)
from docking.log import get_logger

CurrencyLoader = Callable[[], tuple[Unit, ...] | None]
IdleScheduler = Callable[..., int]
Listener = Callable[[], None]
log = get_logger("search.currency")
DEFAULT_CURRENCY_TTL_SECONDS = 60 * 60


class CurrencyRatesState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class CurrencyRatesCatalog:
    """Fetch rates only after a currency query is entered."""

    def __init__(
        self,
        *,
        schedule_idle: IdleScheduler,
        loader: CurrencyLoader = fetch_currency_rates,
        ttl_seconds: float = DEFAULT_CURRENCY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._schedule_idle = schedule_idle
        self._loader = loader
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._clock = clock
        self._state = CurrencyRatesState.IDLE
        self._units: tuple[Unit, ...] = ()
        self._loaded_at = 0.0
        self._refreshing = False
        self._listeners: list[Listener] = []
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CurrencyRatesState:
        return self._state

    @property
    def units(self) -> tuple[Unit, ...]:
        return self._units

    def add_listener(self, listener: Listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._state is CurrencyRatesState.LOADING or self._refreshing:
                return
            if (
                self._state is CurrencyRatesState.READY
                and self._clock() - self._loaded_at < self._ttl_seconds
            ):
                return
            if self._state is CurrencyRatesState.READY:
                self._refreshing = True
            else:
                self._state = CurrencyRatesState.LOADING
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._load,
            args=(generation,),
            name="docking-currency-rates",
            daemon=True,
        ).start()

    def retry(self) -> None:
        with self._lock:
            self._state = CurrencyRatesState.IDLE
        self.ensure_loaded()
        self._notify()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            if self._state is CurrencyRatesState.LOADING:
                self._state = CurrencyRatesState.IDLE
            self._refreshing = False

    def convert(
        self,
        *,
        value: float,
        source_code: str,
        target_code: str,
    ) -> float | None:
        factors = {
            unit.symbol.upper(): unit.factor for unit in self._units if unit.factor > 0
        }
        source = factors.get(source_code.upper())
        target = factors.get(target_code.upper())
        if source is None or target is None:
            return None
        return value * source / target

    def _load(self, generation: int) -> None:
        try:
            units = self._loader()
        except Exception as exc:
            log.warning("Failed to load currency rates: %s", exc)
            units = None
        self._schedule_idle(self._finish_load, generation, units)

    def _finish_load(
        self,
        generation: int,
        units: tuple[Unit, ...] | None,
    ) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            if units:
                self._units = tuple(units)
                self._state = CurrencyRatesState.READY
                self._loaded_at = self._clock()
                self._refreshing = False
                set_currency_units(self._units)
            elif self._units:
                self._state = CurrencyRatesState.READY
                self._refreshing = False
            else:
                self._state = CurrencyRatesState.ERROR
                self._refreshing = False
        self._notify()
        return False

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


__all__ = [
    "DEFAULT_CURRENCY_TTL_SECONDS",
    "CurrencyRatesCatalog",
    "CurrencyRatesState",
]
