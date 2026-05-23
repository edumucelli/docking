# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""CoinGecko-backed state helpers for the Crypto applet."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from docking.applets.live_state import (
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
)
from docking.applets.tooltip import structured_tooltip
from docking.core.math import clamp_index
from docking.i18n import _
from docking.log import get_logger

log = get_logger(name="crypto.state")

REFRESH_INTERVAL_S = 10 * 60
STARTUP_FETCH_DELAY_S = 1
FETCH_TIMEOUT_S = 5
DEFAULT_VS_CURRENCY = "usd"
LOCAL_SAMPLE_MAX_PER_ASSET = 288
LOCAL_SAMPLE_RETENTION_HOURS = 24 * 31
LOCAL_SAMPLE_SOURCE = "coingecko-keyless"

_API_BASE = "https://api.coingecko.com/api/v3"


class AssetType(str, Enum):
    COIN = "coin"
    NFT = "nft"


class ChartInterval(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


CHART_INTERVAL_DAYS: dict[ChartInterval, int] = {
    ChartInterval.DAY: 1,
    ChartInterval.WEEK: 7,
    ChartInterval.MONTH: 30,
}


@dataclass(frozen=True, slots=True)
class CryptoAsset:
    """One selected CoinGecko asset."""

    asset_type: AssetType
    asset_id: str
    symbol: str
    name: str


@dataclass(frozen=True, slots=True)
class CryptoPoint:
    """One chartable price or floor-price point."""

    timestamp: str
    price: float


@dataclass(frozen=True, slots=True)
class CryptoSnapshot:
    """Current asset price plus chart points."""

    asset: CryptoAsset
    vs_currency: str
    price: float
    points: tuple[CryptoPoint, ...]
    fetched_at: dt.datetime
    change_pct_24h: float | None = None


@dataclass(frozen=True, slots=True)
class CryptoPrefs:
    """Persisted Crypto applet preferences."""

    assets: tuple[CryptoAsset, ...] = (
        CryptoAsset(AssetType.COIN, "bitcoin", "BTC", "Bitcoin"),
        CryptoAsset(AssetType.COIN, "ethereum", "ETH", "Ethereum"),
        CryptoAsset(AssetType.COIN, "solana", "SOL", "Solana"),
    )
    active_index: int = 0
    chart_interval: ChartInterval = ChartInterval.DAY
    vs_currency: str = DEFAULT_VS_CURRENCY
    samples: dict[str, tuple[CryptoPoint, ...]] = field(default_factory=dict)


def normalize_asset_type(value: object) -> AssetType:
    if isinstance(value, AssetType):
        return value
    try:
        return AssetType(str(value or AssetType.COIN.value).strip().lower())
    except ValueError:
        return AssetType.COIN


def normalize_asset_id(value: object, *, fallback: str = "bitcoin") -> str:
    asset_id = str(value or "").strip().lower()
    if asset_id:
        return asset_id
    return fallback


def normalize_symbol(value: object, *, fallback: str) -> str:
    symbol = str(value or "").strip().upper()
    return symbol or fallback.upper()


def normalize_name(value: object, *, fallback: str) -> str:
    name = str(value or "").strip()
    return name or fallback


def normalize_vs_currency(value: object) -> str:
    code = str(value or DEFAULT_VS_CURRENCY).strip().lower()
    if len(code) == 3 and code.isalpha():
        return code
    return DEFAULT_VS_CURRENCY


def normalize_chart_interval(value: object) -> ChartInterval:
    if isinstance(value, ChartInterval):
        return value
    try:
        return ChartInterval(str(value or ChartInterval.DAY.value).lower())
    except ValueError:
        return ChartInterval.DAY


def normalize_asset(
    *,
    asset_type: object,
    asset_id: object,
    symbol: object = "",
    name: object = "",
) -> CryptoAsset:
    current_type = normalize_asset_type(asset_type)
    current_id = normalize_asset_id(asset_id)
    fallback_symbol = (
        current_id[:4].upper() if current_type == AssetType.COIN else "NFT"
    )
    return CryptoAsset(
        asset_type=current_type,
        asset_id=current_id,
        symbol=normalize_symbol(symbol, fallback=fallback_symbol),
        name=normalize_name(name, fallback=current_id.replace("-", " ").title()),
    )


def asset_key(asset: CryptoAsset) -> str:
    return f"{asset.asset_type.value}:{asset.asset_id}"


def normalize_assets(assets: Sequence[CryptoAsset]) -> tuple[CryptoAsset, ...]:
    normalized: list[CryptoAsset] = []
    seen: set[str] = set()
    for asset in assets:
        current = normalize_asset(
            asset_type=asset.asset_type,
            asset_id=asset.asset_id,
            symbol=asset.symbol,
            name=asset.name,
        )
        key = asset_key(current)
        if key in seen:
            continue
        normalized.append(current)
        seen.add(key)
    return tuple(normalized) or CryptoPrefs().assets


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> CryptoPrefs:
    if not prefs:
        return CryptoPrefs()
    assets = _assets_from_pref_value(prefs.get("assets"))
    if not assets:
        assets = CryptoPrefs().assets
    try:
        active_index = int(prefs.get("active_index", 0))
    except (TypeError, ValueError):
        active_index = 0
    return CryptoPrefs(
        assets=assets,
        active_index=clamp_index(active_index, len(assets)),
        chart_interval=normalize_chart_interval(prefs.get("chart_interval")),
        vs_currency=normalize_vs_currency(prefs.get("vs_currency")),
        samples=(
            samples_from_pref_value(prefs.get("samples"))
            if prefs.get("sample_source") == LOCAL_SAMPLE_SOURCE
            else {}
        ),
    )


def prefs_payload(
    *,
    assets: Sequence[CryptoAsset],
    active_index: int,
    chart_interval: ChartInterval | str,
    vs_currency: str,
    samples: Mapping[str, Sequence[CryptoPoint]] | None = None,
) -> dict[str, object]:
    normalized_assets = normalize_assets(assets)
    return {
        "assets": [
            {
                "type": asset.asset_type.value,
                "id": asset.asset_id,
                "symbol": asset.symbol,
                "name": asset.name,
            }
            for asset in normalized_assets
        ],
        "active_index": clamp_index(active_index, len(normalized_assets)),
        "chart_interval": normalize_chart_interval(chart_interval).value,
        "vs_currency": normalize_vs_currency(vs_currency),
        "sample_source": LOCAL_SAMPLE_SOURCE,
        "samples": samples_payload(samples or {}),
    }


def _assets_from_pref_value(value: object) -> tuple[CryptoAsset, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    assets: list[CryptoAsset] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item_map = cast(Mapping[str, object], item)
        assets.append(
            normalize_asset(
                asset_type=item_map.get("type"),
                asset_id=item_map.get("id"),
                symbol=item_map.get("symbol"),
                name=item_map.get("name"),
            )
        )
    return normalize_assets(assets) if assets else ()


def samples_from_pref_value(value: object) -> dict[str, tuple[CryptoPoint, ...]]:
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[str, tuple[CryptoPoint, ...]] = {}
    for raw_key, raw_points in value.items():
        if not isinstance(raw_key, str):
            continue
        if not isinstance(raw_points, Sequence) or isinstance(raw_points, str):
            continue
        points: list[CryptoPoint] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, Mapping):
                continue
            point_map = cast(Mapping[str, object], raw_point)
            point = _point_from(
                timestamp=point_map.get("timestamp"),
                price=point_map.get("price"),
            )
            if point is not None:
                points.append(point)
        if points:
            parsed[raw_key] = tuple(points[-LOCAL_SAMPLE_MAX_PER_ASSET:])
    return parsed


def samples_payload(
    samples: Mapping[str, Sequence[CryptoPoint]],
) -> dict[str, list[dict[str, object]]]:
    payload: dict[str, list[dict[str, object]]] = {}
    for key, points in samples.items():
        if not points:
            continue
        payload[key] = [
            {"timestamp": point.timestamp, "price": point.price}
            for point in points[-LOCAL_SAMPLE_MAX_PER_ASSET:]
        ]
    return payload


def append_local_sample(
    *,
    samples: Mapping[str, Sequence[CryptoPoint]],
    asset: CryptoAsset,
    price: float,
    now: dt.datetime | None = None,
) -> dict[str, tuple[CryptoPoint, ...]]:
    if price <= 0:
        return {key: tuple(points) for key, points in samples.items()}
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    key = asset_key(asset)
    current = list(samples.get(key, ()))
    point = CryptoPoint(timestamp=timestamp.isoformat(), price=float(price))
    if current and current[-1].timestamp == point.timestamp:
        current[-1] = point
    else:
        current.append(point)
    updated = {sample_key: tuple(points) for sample_key, points in samples.items()}
    updated[key] = prune_local_samples(points=current, now=timestamp)
    return updated


def local_sample_points(
    *,
    samples: Mapping[str, Sequence[CryptoPoint]],
    asset: CryptoAsset,
    now: dt.datetime | None = None,
) -> tuple[CryptoPoint, ...]:
    return prune_local_samples(points=samples.get(asset_key(asset), ()), now=now)


def prune_local_samples(
    *,
    points: Sequence[CryptoPoint],
    now: dt.datetime | None = None,
) -> tuple[CryptoPoint, ...]:
    cutoff = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc
    ) - dt.timedelta(hours=LOCAL_SAMPLE_RETENTION_HOURS)
    retained = []
    for point in points:
        timestamp = _parse_timestamp(point.timestamp)
        if timestamp is not None and timestamp >= cutoff:
            retained.append(point)
    return tuple(retained[-LOCAL_SAMPLE_MAX_PER_ASSET:])


def fetch_crypto_snapshot(
    *,
    asset: CryptoAsset,
    chart_interval: ChartInterval | str = ChartInterval.DAY,
    vs_currency: str = DEFAULT_VS_CURRENCY,
) -> CryptoSnapshot | None:
    if asset.asset_type == AssetType.NFT:
        return fetch_nft_snapshot(asset=asset, vs_currency=vs_currency)
    return fetch_coin_snapshot(
        asset=asset,
        chart_interval=chart_interval,
        vs_currency=vs_currency,
    )


def fetch_coin_snapshot(
    *,
    asset: CryptoAsset,
    chart_interval: ChartInterval | str = ChartInterval.DAY,
    vs_currency: str = DEFAULT_VS_CURRENCY,
) -> CryptoSnapshot | None:
    vs_currency = normalize_vs_currency(vs_currency)
    market = _fetch_json(
        "/coins/markets",
        {
            "vs_currency": vs_currency,
            "ids": asset.asset_id,
            "price_change_percentage": "24h",
        },
    )
    parsed = parse_coin_market_payload(data=market, fallback=asset)
    if parsed is None:
        return None
    market_row = cast(Mapping[str, object], cast(Sequence[object], market)[0])
    current_price = _float_or_none(market_row.get("current_price"))
    if current_price is None:
        return None
    points = fetch_coin_chart_points(
        asset_id=parsed.asset_id,
        chart_interval=chart_interval,
        vs_currency=vs_currency,
    )
    return CryptoSnapshot(
        asset=parsed,
        vs_currency=vs_currency,
        price=current_price,
        points=points,
        fetched_at=dt.datetime.now(dt.timezone.utc),
        change_pct_24h=_float_or_none(market_row.get("price_change_percentage_24h")),
    )


def parse_coin_market_payload(
    *,
    data: object,
    fallback: CryptoAsset,
) -> CryptoAsset | None:
    if not isinstance(data, Sequence) or isinstance(data, str) or not data:
        return None
    row = data[0]
    if not isinstance(row, Mapping):
        return None
    row_map = cast(Mapping[str, object], row)
    if _float_or_none(row_map.get("current_price")) is None:
        return None
    return normalize_asset(
        asset_type=AssetType.COIN,
        asset_id=row_map.get("id") or fallback.asset_id,
        symbol=row_map.get("symbol") or fallback.symbol,
        name=row_map.get("name") or fallback.name,
    )


def fetch_coin_chart_points(
    *,
    asset_id: str,
    chart_interval: ChartInterval | str,
    vs_currency: str,
) -> tuple[CryptoPoint, ...]:
    interval = normalize_chart_interval(chart_interval)
    data = _fetch_json(
        f"/coins/{urllib.parse.quote(asset_id)}/market_chart",
        {
            "vs_currency": normalize_vs_currency(vs_currency),
            "days": CHART_INTERVAL_DAYS[interval],
        },
    )
    return parse_market_chart_payload(data=data)


def parse_market_chart_payload(*, data: object) -> tuple[CryptoPoint, ...]:
    if not isinstance(data, Mapping):
        return ()
    data_map = cast(Mapping[str, object], data)
    prices = data_map.get("prices")
    if not isinstance(prices, Sequence) or isinstance(prices, str):
        return ()
    points: list[CryptoPoint] = []
    for row in prices:
        if not isinstance(row, Sequence) or isinstance(row, str) or len(row) < 2:
            continue
        timestamp_ms = _float_or_none(row[0])
        price = _float_or_none(row[1])
        if timestamp_ms is None or price is None:
            continue
        if price <= 0:
            continue
        timestamp = dt.datetime.fromtimestamp(
            timestamp_ms / 1000.0,
            dt.timezone.utc,
        ).isoformat()
        points.append(CryptoPoint(timestamp=timestamp, price=price))
    return tuple(points)


def fetch_nft_snapshot(
    *,
    asset: CryptoAsset,
    vs_currency: str = DEFAULT_VS_CURRENCY,
) -> CryptoSnapshot | None:
    data = _fetch_json(f"/nfts/{urllib.parse.quote(asset.asset_id)}", {})
    parsed = parse_nft_payload(data=data, fallback=asset)
    if parsed is None:
        return None
    return CryptoSnapshot(
        asset=parsed[0],
        vs_currency=normalize_vs_currency(vs_currency),
        price=parsed[1],
        points=(),
        fetched_at=dt.datetime.now(dt.timezone.utc),
    )


def parse_nft_payload(
    *,
    data: object,
    fallback: CryptoAsset,
) -> tuple[CryptoAsset, float] | None:
    if not isinstance(data, Mapping):
        return None
    data_map = cast(Mapping[str, object], data)
    floor_price = data_map.get("floor_price")
    price = None
    if isinstance(floor_price, Mapping):
        floor_map = cast(Mapping[str, object], floor_price)
        price = _float_or_none(floor_map.get("usd")) or _float_or_none(
            floor_map.get("native_currency")
        )
    price = price or _float_or_none(data_map.get("floor_price_in_usd"))
    if price is None or price <= 0:
        return None
    asset = normalize_asset(
        asset_type=AssetType.NFT,
        asset_id=data_map.get("id") or fallback.asset_id,
        symbol=data_map.get("symbol") or fallback.symbol,
        name=data_map.get("name") or fallback.name,
    )
    return asset, price


def percent_change(points: Sequence[CryptoPoint]) -> float | None:
    usable = [point.price for point in points if point.price > 0]
    if len(usable) < 2:
        return None
    first = usable[0]
    last = usable[-1]
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def format_price(price: float | None, *, vs_currency: str = DEFAULT_VS_CURRENCY) -> str:
    if price is None:
        return "-"
    prefix = "$" if normalize_vs_currency(vs_currency) == "usd" else ""
    if abs(price) >= 1000:
        body = f"{price:,.0f}"
    elif abs(price) >= 100:
        body = f"{price:,.2f}"
    elif abs(price) >= 1:
        body = f"{price:.3f}".rstrip("0").rstrip(".")
    else:
        body = f"{price:.6f}".rstrip("0").rstrip(".") or "0"
    suffix = "" if prefix else f" {normalize_vs_currency(vs_currency).upper()}"
    return f"{prefix}{body}{suffix}"


def format_change(points: Sequence[CryptoPoint]) -> str:
    change = percent_change(points)
    if change is None:
        return "n/a"
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.2f}%"


def build_tooltip(
    *,
    asset: CryptoAsset,
    snapshot: CryptoSnapshot | None,
    loading: bool,
    fetch_failed: bool,
    error: str | None = None,
    chart_interval: ChartInterval | str = ChartInterval.DAY,
    cadence_seconds: int | None = None,
) -> str:
    state_error = error or (_("Unavailable") if fetch_failed else None)
    status = resolve_live_status(
        has_data=snapshot is not None,
        loading=loading,
        error=state_error,
        updated_at=snapshot.fetched_at if snapshot else None,
    )
    title = asset_label(asset)
    if snapshot is None:
        return structured_tooltip(
            title=title,
            primary=live_state_label(status),
            freshness=live_freshness_lines(
                status=status,
                cadence_seconds=cadence_seconds,
            ),
            error=live_state_error(status=status, error=state_error),
            recovery=refresh_recovery_label(status),
        )
    details = [
        _("Interval: {interval}").format(
            interval=normalize_chart_interval(chart_interval).value.title()
        ),
        _("Change: {change}").format(change=format_change(snapshot.points)),
    ]
    if snapshot.change_pct_24h is not None:
        details.append(
            _("24h: {change}").format(change=f"{snapshot.change_pct_24h:+.2f}%")
        )
    return structured_tooltip(
        title=asset_label(snapshot.asset),
        primary=_("{price}").format(
            price=format_price(snapshot.price, vs_currency=snapshot.vs_currency)
        ),
        details=details,
        freshness=live_freshness_lines(
            status=status,
            updated_at=snapshot.fetched_at,
            cadence_seconds=cadence_seconds,
        ),
        error=live_state_error(status=status, error=state_error),
        recovery=refresh_recovery_label(status),
    )


def asset_label(asset: CryptoAsset) -> str:
    if asset.asset_type == AssetType.NFT:
        return _("{name} NFT").format(name=asset.name)
    return f"{asset.name} ({asset.symbol})"


def _fetch_json(path: str, params: Mapping[str, object]) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{_API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Docking/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def _point_from(*, timestamp: object, price: object) -> CryptoPoint | None:
    value = _float_or_none(price)
    if value is None or value <= 0:
        return None
    ts = str(timestamp or "").strip()
    if not ts:
        return None
    return CryptoPoint(timestamp=ts, price=value)


def _parse_timestamp(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result
