"""Pure FX pair state, formatting, cache, and fetch helpers.

This module is intentionally free of GTK.  The applet layer owns menus,
timers, and drawing invalidation; this layer owns the data model and the
rules that make Currency FX behave consistently across startup, refresh,
scroll, and preference writes.

Currency FX has three rate/data sources:

* Currency ids come from Unit Converter's currency loader.  That keeps the pair
  picker aligned with the converter.
* Current rates come from a timestamped pair endpoint.  The day chart depends
  on this because daily reference rates do not move intraday.
* Week and month charts come from the same provider's daily-history endpoint.
  Those intervals are calendar-day sparklines and do not try to represent
  intraday movement.

The day chart is deliberately different.  Public daily-history APIs collapse
the current day into one or two points, which renders as a straight line for
many pairs.  For day, the applet stores the rates it has observed locally and
uses that per-pair cache as the intraday sparkline.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from docking.applets.unitconverter.state import Unit, fetch_currency_rates
from docking.i18n import _
from docking.log import get_logger

log = get_logger(name="currencyfx.state")

DEFAULT_BASE = "EUR"
DEFAULT_QUOTE = "USD"
DEFAULT_CHART_INTERVAL = "week"
REFRESH_INTERVAL_S = 15 * 60
STARTUP_FETCH_DELAY_S = 1
FETCH_TIMEOUT_S = 5

DEFAULT_CURRENCY_CODES: tuple[str, ...] = (
    "EUR",
    "USD",
    "GBP",
    "JPY",
    "CHF",
    "AUD",
    "CAD",
    "NZD",
    "CNY",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
)

_LIVE_PAIR_URL = "https://fxapi.app/api/{base}/{quote}.json"
_LIVE_HISTORY_URL = "https://fxapi.app/api/history/{base}/{quote}.json"


class ChartInterval(str, Enum):
    """User-visible chart interval.

    ``DAY`` is local-sample based.  ``WEEK`` and ``MONTH`` are remote daily
    history windows.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


# Number of calendar days requested for intervals backed by remote history.
# DAY is kept here so callers can use one normalization path even though the
# applet does not fetch remote day history.
CHART_INTERVAL_DAYS: dict[ChartInterval, int] = {
    ChartInterval.DAY: 1,
    ChartInterval.WEEK: 7,
    ChartInterval.MONTH: 30,
}

# Local samples are collected on the applet refresh cadence.  The cap prevents
# unbounded preference growth if the refresh interval changes or the applet is
# left running for a long time.  With the current 15 minute refresh, 192 points
# covers two days, while retention keeps the visible day window to 24 hours.
LOCAL_SAMPLE_RETENTION_HOURS = 24
LOCAL_SAMPLE_MAX_PER_PAIR = 192
LOCAL_SAMPLE_SOURCE = "fxapi.app"


@dataclass(frozen=True, slots=True)
class FxPair:
    """One tracked FX pair.

    ``base`` is the currency being converted from; ``quote`` is the target
    currency shown at the bottom of the icon.
    """

    base: str
    quote: str


@dataclass(frozen=True, slots=True)
class FxPoint:
    """One chartable FX rate point.

    ``date`` is an ISO date for daily history points and an ISO datetime
    for local day-cache samples.  Rendering only needs order and rate value, so
    both shapes share this compact object.
    """

    date: str
    rate: float


@dataclass(frozen=True, slots=True)
class FxSnapshot:
    """Current rate plus the points used by the current chart.

    ``rate`` is always the current Unit Converter derived rate when available.
    ``points`` depends on the selected interval: local samples for day, daily
    history for week/month.
    """

    base: str
    quote: str
    rate: float
    points: tuple[FxPoint, ...]
    fetched_at: dt.datetime


@dataclass(frozen=True, slots=True)
class CurrencyFxPrefs:
    """Persisted Currency FX preferences.

    ``pairs`` are the user-added pairs, not a fixed common-pair list.
    ``active_index`` selects the pair shown on the icon and used by scroll.
    ``chart_interval`` is shared by all pairs.
    ``samples`` is the per-pair local day cache stored as ``EUR/BRL`` keys.
    Sample data is source-scoped so stale points from older providers are not
    mixed into live-rate day charts.
    """

    pairs: tuple[FxPair, ...] = (FxPair(DEFAULT_BASE, DEFAULT_QUOTE),)
    active_index: int = 0
    chart_interval: ChartInterval = ChartInterval.WEEK
    samples: dict[str, tuple[FxPoint, ...]] = field(default_factory=dict)


def normalize_code(value: object, *, fallback: str) -> str:
    """Normalize a persisted or UI-provided currency code.

    The UI only offers known 3-letter codes, but preferences are user-editable.
    Invalid values fall back instead of leaking malformed ids into URLs, keys,
    or labels.
    """
    code = str(value or "").strip().upper()
    if len(code) == 3 and code.isalpha():
        return code
    return fallback


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> CurrencyFxPrefs:
    """Build preferences from persisted values.

    Only the current preference schema is accepted.  During early iteration we
    intentionally avoid carrying compatibility branches for abandoned keys, so
    startup behavior stays easy to reason about.
    """
    if not prefs:
        return CurrencyFxPrefs()

    pairs = _pairs_from_pref_value(prefs.get("pairs"))
    if not pairs:
        pairs = CurrencyFxPrefs().pairs

    try:
        active_index = int(prefs.get("active_index", 0))
    except (TypeError, ValueError):
        active_index = 0
    active_index = max(0, min(active_index, len(pairs) - 1))

    return CurrencyFxPrefs(
        pairs=pairs,
        active_index=active_index,
        chart_interval=normalize_chart_interval(prefs.get("chart_interval")),
        samples=(
            samples_from_pref_value(prefs.get("samples"))
            if prefs.get("sample_source") == LOCAL_SAMPLE_SOURCE
            else {}
        ),
    )


def prefs_payload(
    *,
    pairs: Sequence[FxPair],
    active_index: int,
    chart_interval: ChartInterval | str,
    samples: Mapping[str, Sequence[FxPoint]] | None = None,
) -> dict[str, object]:
    """Build payload used by save_prefs().

    The returned structure is JSON-safe and mirrors the applet features:
    added pairs, active pair index, selected chart interval, and local day
    samples.  Pair and interval normalization happen before write so the config
    file stays canonical after any menu or dialog action.
    """
    normalized_pairs = normalize_pairs(pairs)
    active_index = max(0, min(active_index, len(normalized_pairs) - 1))
    interval = normalize_chart_interval(chart_interval)
    return {
        "pairs": [
            {"base": pair.base, "quote": pair.quote} for pair in normalized_pairs
        ],
        "active_index": active_index,
        "chart_interval": interval.value,
        "sample_source": LOCAL_SAMPLE_SOURCE,
        "samples": samples_payload(samples or {}),
    }


def normalize_pair(*, base: object, quote: object) -> FxPair:
    """Normalize a pair and avoid same-code pairs.

    Same-currency pairs are not useful for the applet.  If a persisted or
    dialog-provided pair resolves to the same code on both sides, the quote is
    moved to the default counterpart.
    """
    base_code = normalize_code(base, fallback=DEFAULT_BASE)
    quote_code = normalize_code(quote, fallback=DEFAULT_QUOTE)
    if quote_code == base_code:
        quote_code = DEFAULT_QUOTE if base_code != DEFAULT_QUOTE else DEFAULT_BASE
    return FxPair(base=base_code, quote=quote_code)


def normalize_chart_interval(value: object) -> ChartInterval:
    """Normalize chart interval preference."""
    if isinstance(value, ChartInterval):
        return value
    try:
        return ChartInterval(str(value or DEFAULT_CHART_INTERVAL).lower())
    except ValueError:
        return ChartInterval.WEEK


def chart_interval_days(interval: ChartInterval | str) -> int:
    """Return how many calendar days belong to an interval."""
    return CHART_INTERVAL_DAYS[normalize_chart_interval(interval)]


def pair_key(*, base: str, quote: str) -> str:
    """Stable key for pair-specific local sample storage."""
    pair = normalize_pair(base=base, quote=quote)
    return f"{pair.base}/{pair.quote}"


def normalize_pairs(pairs: Sequence[FxPair]) -> tuple[FxPair, ...]:
    """Dedupe normalized pairs preserving order.

    Scroll order is the same as add order, so dedupe cannot sort or otherwise
    reshuffle the list.
    """
    normalized: list[FxPair] = []
    seen: set[FxPair] = set()
    for pair in pairs:
        current = normalize_pair(base=pair.base, quote=pair.quote)
        if current not in seen:
            normalized.append(current)
            seen.add(current)
    if not normalized:
        normalized.append(FxPair(DEFAULT_BASE, DEFAULT_QUOTE))
    return tuple(normalized)


def currency_codes_from_units(units: Sequence[Unit] | None) -> tuple[str, ...]:
    """Return sorted 3-letter currency codes from Unit Converter units."""
    codes = {
        unit.symbol.upper()
        for unit in units or ()
        if len(unit.symbol) == 3 and unit.symbol.isalpha()
    }
    return tuple(sorted(codes))


def merge_currency_codes(codes: Sequence[str]) -> tuple[str, ...]:
    """Merge live codes with startup defaults while keeping defaults first.

    The startup defaults make the Add Pair dialog usable before the first
    network refresh.  Live codes are appended after refresh so newly supported
    currencies become selectable without dropping familiar defaults.
    """
    normalized = {
        normalize_code(code, fallback="")
        for code in codes
        if normalize_code(code, fallback="")
    }
    ordered: list[str] = list(DEFAULT_CURRENCY_CODES)
    for code in sorted(normalized):
        if code not in ordered:
            ordered.append(code)
    return tuple(ordered)


def _pairs_from_pref_value(value: object) -> tuple[FxPair, ...]:
    """Parse the persisted added-pairs list."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    pairs: list[FxPair] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item_map = cast(Mapping[str, object], item)
        pairs.append(
            normalize_pair(base=item_map.get("base"), quote=item_map.get("quote"))
        )
    return normalize_pairs(pairs) if pairs else ()


def samples_from_pref_value(value: object) -> dict[str, tuple[FxPoint, ...]]:
    """Parse persisted local samples.

    Samples are best-effort cache data.  Invalid keys, malformed rows, and
    non-positive rates are ignored so a bad cache entry never blocks applet
    startup.
    """
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[str, tuple[FxPoint, ...]] = {}
    for raw_key, raw_points in value.items():
        if not isinstance(raw_key, str):
            continue
        if not isinstance(raw_points, Sequence) or isinstance(raw_points, str):
            continue
        parts = raw_key.split("/", 1)
        if len(parts) != 2:
            continue
        key = pair_key(base=parts[0], quote=parts[1])
        points: list[FxPoint] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, Mapping):
                continue
            point_map = cast(Mapping[str, object], raw_point)
            point = _point_from(
                date=point_map.get("timestamp") or point_map.get("date"),
                rate=point_map.get("rate"),
            )
            if point is not None:
                points.append(point)
        if points:
            parsed[key] = tuple(points[-LOCAL_SAMPLE_MAX_PER_PAIR:])
    return parsed


def samples_payload(
    samples: Mapping[str, Sequence[FxPoint]],
) -> dict[str, list[dict[str, object]]]:
    """Build JSON-safe local sample cache payload.

    The serialized field name is ``timestamp`` even though ``FxPoint`` uses
    ``date``.  That makes the preference file self-documenting for day-cache
    entries while keeping the render-facing object shared with history points.
    """
    payload: dict[str, list[dict[str, object]]] = {}
    for key, points in samples.items():
        if not points:
            continue
        payload[key] = [
            {"timestamp": point.date, "rate": point.rate}
            for point in points[-LOCAL_SAMPLE_MAX_PER_PAIR:]
        ]
    return payload


def append_local_sample(
    *,
    samples: Mapping[str, Sequence[FxPoint]],
    base: str,
    quote: str,
    rate: float,
    now: dt.datetime | None = None,
) -> dict[str, tuple[FxPoint, ...]]:
    """Append one current-rate sample and prune stale points.

    This is called after every successful current-rate fetch, regardless of
    selected interval.  That way the day chart has data immediately when the
    user switches to it after running on week or month for a while.
    """
    if rate <= 0:
        return {key: tuple(points) for key, points in samples.items()}
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    key = pair_key(base=base, quote=quote)
    current = list(samples.get(key, ()))
    point = FxPoint(date=timestamp.isoformat(), rate=float(rate))
    if current and current[-1].date == point.date:
        current[-1] = point
    else:
        current.append(point)

    updated = {sample_key: tuple(points) for sample_key, points in samples.items()}
    updated[key] = prune_local_samples(points=current, now=timestamp)
    return updated


def local_sample_points(
    *,
    samples: Mapping[str, Sequence[FxPoint]],
    base: str,
    quote: str,
    now: dt.datetime | None = None,
) -> tuple[FxPoint, ...]:
    """Return retained local samples for one pair."""
    key = pair_key(base=base, quote=quote)
    current = samples.get(key, ())
    return prune_local_samples(points=current, now=now)


def prune_local_samples(
    *,
    points: Sequence[FxPoint],
    now: dt.datetime | None = None,
) -> tuple[FxPoint, ...]:
    """Keep only recent local samples for day charts.

    Unknown timestamp shapes are dropped.  The cache is optional display data,
    so strict pruning is safer than preserving values that cannot be ordered
    reliably.
    """
    cutoff = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc,
    ) - dt.timedelta(hours=LOCAL_SAMPLE_RETENTION_HOURS)
    retained = []
    for point in points:
        timestamp = _parse_sample_timestamp(point.date)
        if timestamp is not None and timestamp >= cutoff:
            retained.append(point)
    return tuple(retained[-LOCAL_SAMPLE_MAX_PER_PAIR:])


def pair_rate_from_units(
    *,
    units: Sequence[Unit],
    base: str,
    quote: str,
) -> float | None:
    """Return quote units per one base unit from Unit Converter factors.

    Unit Converter stores rates as factors relative to its internal currency
    base.  Dividing base factor by quote factor gives the direct pair rate
    shown as ``1 BASE = rate QUOTE``.
    """
    base = normalize_code(base, fallback=DEFAULT_BASE)
    quote = normalize_code(quote, fallback=DEFAULT_QUOTE)
    if base == quote:
        return 1.0
    factors = {
        unit.symbol.upper(): float(unit.factor)
        for unit in units
        if unit.factor > 0 and len(unit.symbol) == 3
    }
    base_factor = factors.get(base)
    quote_factor = factors.get(quote)
    if base_factor is None or quote_factor is None:
        return None
    return base_factor / quote_factor


def fetch_live_rate(
    *,
    base: str,
    quote: str,
) -> FxPoint | None:
    """Fetch the current timestamped pair rate.

    Unit Converter rates are reference/daily rates, which are good for static
    conversion but poor for a local day chart.  This endpoint supplies the
    current pair rate and provider timestamp used as the local sample time.
    """
    base = normalize_code(base, fallback=DEFAULT_BASE)
    quote = normalize_code(quote, fallback=DEFAULT_QUOTE)
    if base == quote:
        return FxPoint(
            date=dt.datetime.now(dt.timezone.utc).isoformat(),
            rate=1.0,
        )

    url = _LIVE_PAIR_URL.format(
        base=urllib.parse.quote(base),
        quote=urllib.parse.quote(quote),
    )
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Docking/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        log.warning("Failed to fetch live FX rate: %s", exc)
        return None

    return parse_live_rate_payload(data=data, base=base, quote=quote)


def parse_live_rate_payload(
    *,
    data: object,
    base: str,
    quote: str,
) -> FxPoint | None:
    """Parse a timestamped live pair response."""
    base = normalize_code(base, fallback=DEFAULT_BASE)
    quote = normalize_code(quote, fallback=DEFAULT_QUOTE)
    if not isinstance(data, Mapping):
        return None
    data_map = cast(Mapping[str, object], data)
    payload_base = normalize_code(data_map.get("base"), fallback=base)
    payload_quote = normalize_code(data_map.get("target"), fallback=quote)
    if payload_base != base or payload_quote != quote:
        return None
    return _point_from(
        date=data_map.get("timestamp") or dt.datetime.now(dt.timezone.utc).isoformat(),
        rate=data_map.get("rate"),
    )


def fetch_history(
    *,
    base: str,
    quote: str,
    chart_interval: ChartInterval | str = ChartInterval.WEEK,
    today: dt.date | None = None,
) -> tuple[FxPoint, ...]:
    """Fetch recent daily rates for the pair.

    Remote history is only used for week and month.  Day returns no points by
    design because the visible day chart is assembled from local samples.
    """
    base = normalize_code(base, fallback=DEFAULT_BASE)
    quote = normalize_code(quote, fallback=DEFAULT_QUOTE)
    interval = normalize_chart_interval(chart_interval)
    if interval == ChartInterval.DAY:
        return ()
    if base == quote:
        current = today or dt.date.today()
        return (FxPoint(date=current.isoformat(), rate=1.0),)

    end = today or dt.date.today()
    history_days = chart_interval_days(interval)
    start = end - dt.timedelta(days=max(1, history_days - 1))
    url = _LIVE_HISTORY_URL.format(
        base=urllib.parse.quote(base),
        quote=urllib.parse.quote(quote),
    )
    query = urllib.parse.urlencode(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
    )
    url = f"{url}?{query}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Docking/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        log.warning("Failed to fetch FX history: %s", exc)
        return ()

    return parse_history_payload(data=data)


def parse_history_payload(*, data: object) -> tuple[FxPoint, ...]:
    """Parse daily-history responses."""
    points: list[FxPoint] = []
    if isinstance(data, Mapping):
        data_map = cast(Mapping[str, object], data)
        rates = data_map.get("rates")
        if isinstance(rates, Sequence) and not isinstance(rates, str):
            for row in rates:
                if not isinstance(row, Mapping):
                    continue
                row_map = cast(Mapping[str, object], row)
                point = _point_from(date=row_map.get("date"), rate=row_map.get("rate"))
                if point is not None:
                    points.append(point)
    return tuple(sorted(points, key=lambda point: point.date))


def fetch_fx_snapshot(
    *,
    base: str,
    quote: str,
    chart_interval: ChartInterval | str = ChartInterval.WEEK,
) -> tuple[FxSnapshot | None, tuple[str, ...]]:
    """Fetch current FX rate and interval-specific chart points.

    Currency ids intentionally use Unit Converter's currency fetch path so both
    applets expose the same codes.  Current pair rates prefer the timestamped
    live endpoint; Unit Converter factors remain a fallback if the live request
    fails.

    The returned codes feed the Add Pair combo boxes.  The snapshot may be
    ``None`` if the current pair cannot be computed from the available unit
    factors.
    """
    base = normalize_code(base, fallback=DEFAULT_BASE)
    quote = normalize_code(quote, fallback=DEFAULT_QUOTE)
    units = fetch_currency_rates()
    codes = currency_codes_from_units(units)
    live_point = fetch_live_rate(base=base, quote=quote)
    current_rate = (
        live_point.rate
        if live_point is not None
        else pair_rate_from_units(units=units or (), base=base, quote=quote)
    )
    interval = normalize_chart_interval(chart_interval)
    points = (
        ()
        if interval == ChartInterval.DAY
        else fetch_history(
            base=base,
            quote=quote,
            chart_interval=interval,
        )
    )

    if current_rate is None and points:
        current_rate = points[-1].rate
    if current_rate is None:
        return None, codes

    today = dt.date.today().isoformat()
    if interval != ChartInterval.DAY:
        current_point = FxPoint(date=today, rate=current_rate)
        if points and points[-1].date == today:
            points = (*points[:-1], current_point)
        else:
            points = (*points, current_point)

    snapshot = FxSnapshot(
        base=base,
        quote=quote,
        rate=current_rate,
        points=points,
        fetched_at=(
            _parse_sample_timestamp(live_point.date) if live_point is not None else None
        )
        or dt.datetime.now(dt.timezone.utc),
    )
    return snapshot, codes


def percent_change(points: Sequence[FxPoint]) -> float | None:
    """Return percentage change from first to last point."""
    usable = [point.rate for point in points if point.rate > 0]
    if len(usable) < 2:
        return None
    first = usable[0]
    last = usable[-1]
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def format_rate(rate: float | None) -> str:
    """Format an FX rate compactly."""
    if rate is None:
        return "-"
    if abs(rate) >= 100:
        return f"{rate:,.2f}"
    if abs(rate) >= 10:
        return f"{rate:.3f}".rstrip("0").rstrip(".")
    if abs(rate) >= 1:
        return f"{rate:.4f}".rstrip("0").rstrip(".")
    return f"{rate:.6f}".rstrip("0").rstrip(".") or "0"


def format_change(points: Sequence[FxPoint]) -> str:
    """Format recent percentage change."""
    change = percent_change(points)
    if change is None:
        return "n/a"
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.2f}%"


def build_tooltip(
    *,
    base: str,
    quote: str,
    snapshot: FxSnapshot | None,
    fetch_failed: bool,
) -> str:
    """Build tooltip text."""
    pair = f"{base}/{quote}"
    if snapshot is None:
        if fetch_failed:
            return _("{pair}: unavailable").format(pair=pair)
        return _("{pair}: loading...").format(pair=pair)
    return "\n".join(
        (
            pair,
            _("1 {base} = {rate} {quote}").format(
                base=snapshot.base,
                rate=format_rate(snapshot.rate),
                quote=snapshot.quote,
            ),
            _("Change: {change}").format(change=format_change(snapshot.points)),
        )
    )


def _point_from(*, date: object, rate: object) -> FxPoint | None:
    """Coerce one untrusted history or cache row into an ``FxPoint``."""
    if not isinstance(rate, str | int | float):
        return None
    try:
        parsed_rate = float(rate)
    except (TypeError, ValueError):
        return None
    if parsed_rate <= 0:
        return None
    date_text = str(date or "")
    if not date_text:
        return None
    return FxPoint(date=date_text, rate=parsed_rate)


def _parse_sample_timestamp(value: str) -> dt.datetime | None:
    """Parse local-cache timestamps as timezone-aware UTC datetimes."""
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)
