"""Privacy-preserving search result and action relevance learning."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from docking.core.paths import ensure_parent_dir
from docking.log import get_logger
from docking.platform.environment import docking_state_dir
from docking.search.matcher import normalize_search_text
from docking.search.types import SearchAction, SearchIdentity, SearchQuery, SearchResult

DEFAULT_USAGE_FILE = docking_state_dir() / "search-usage.json"
MAX_USAGE_RECORDS = 1_000
MAX_RANK_BOOST = 18.0
log = get_logger("search.usage")


@dataclass(frozen=True, slots=True)
class UsageRecord:
    count: int
    last_used: float


class SearchUsageStore:
    """Learn selections without persisting raw queries, paths, or titles."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or DEFAULT_USAGE_FILE
        self._lock = threading.RLock()
        self._results: dict[str, UsageRecord] = {}
        self._actions: dict[str, UsageRecord] = {}
        self._query_results: dict[str, UsageRecord] = {}
        self._load()

    def record(
        self,
        *,
        query: str,
        result: SearchResult,
        action: SearchAction,
        now: float | None = None,
    ) -> None:
        used_at = float(now if now is not None else time.time())
        if not math.isfinite(used_at) or used_at <= 0:
            used_at = time.time()
        result_key = _identity_hash(result.identity)
        action_key = _identity_hash(action.identity)
        query_key = _query_result_hash(query=query, identity=result.identity)
        with self._lock:
            self._results[result_key] = _increment(
                self._results.get(result_key),
                now=used_at,
            )
            self._actions[action_key] = _increment(
                self._actions.get(action_key),
                now=used_at,
            )
            if query_key is not None:
                self._query_results[query_key] = _increment(
                    self._query_results.get(query_key),
                    now=used_at,
                )
            self._trim()
            try:
                self._save()
            except OSError as exc:
                log.warning("Failed to save search usage state: %s", exc)

    def boost(
        self,
        result: SearchResult,
        query: SearchQuery,
        *,
        now: float | None = None,
    ) -> float:
        reference = float(now if now is not None else time.time())
        result_record = self._results.get(_identity_hash(result.identity))
        query_key = _query_result_hash(query=query.text, identity=result.identity)
        query_record = self._query_results.get(query_key) if query_key else None
        boost = _frequency_boost(result_record, maximum=7.0)
        boost += _frequency_boost(query_record, maximum=7.0)
        newest = max(
            (
                record.last_used
                for record in (result_record, query_record)
                if record is not None
            ),
            default=0.0,
        )
        age = max(0.0, reference - newest)
        if newest and age <= 86400:
            boost += 4.0
        elif newest and age <= 7 * 86400:
            boost += 2.0
        elif newest and age <= 30 * 86400:
            boost += 1.0
        return min(MAX_RANK_BOOST, boost)

    def rank_actions(
        self,
        actions: tuple[SearchAction, ...],
    ) -> tuple[SearchAction, ...]:
        """Preserve the primary action and learn ordering of secondary actions."""
        if len(actions) < 3:
            return actions
        primary, *secondary = actions
        indexed = list(enumerate(secondary))
        indexed.sort(
            key=lambda pair: (
                -self._action_count(pair[1]),
                pair[0],
            )
        )
        return (primary, *(action for _index, action in indexed))

    def clear(self) -> None:
        with self._lock:
            self._results.clear()
            self._actions.clear()
            self._query_results.clear()
            try:
                self._path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Failed to clear search usage state: %s", exc)

    def _action_count(self, action: SearchAction) -> int:
        record = self._actions.get(_identity_hash(action.identity))
        return record.count if record is not None else 0

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        self._results = _records_from(raw.get("results"))
        self._actions = _records_from(raw.get("actions"))
        self._query_results = _records_from(raw.get("query_results"))
        self._trim()

    def _save(self) -> None:
        ensure_parent_dir(self._path)
        payload = {
            "version": 1,
            "results": _records_payload(self._results),
            "actions": _records_payload(self._actions),
            "query_results": _records_payload(self._query_results),
        }
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.replace(self._path)

    def _trim(self) -> None:
        self._results = _trimmed(self._results)
        self._actions = _trimmed(self._actions)
        self._query_results = _trimmed(self._query_results)


def _identity_hash(identity: SearchIdentity) -> str:
    value = f"{identity.provider_id}\0{identity.key}".encode()
    return hashlib.sha256(value).hexdigest()


def _query_result_hash(
    *,
    query: str,
    identity: SearchIdentity,
) -> str | None:
    normalized = normalize_search_text(query)
    if not normalized:
        return None
    value = f"{normalized}\0{_identity_hash(identity)}".encode()
    return hashlib.sha256(value).hexdigest()


def _increment(record: UsageRecord | None, *, now: float) -> UsageRecord:
    return UsageRecord(
        count=(record.count if record is not None else 0) + 1,
        last_used=now,
    )


def _frequency_boost(record: UsageRecord | None, *, maximum: float) -> float:
    if record is None:
        return 0.0
    return min(maximum, math.log2(record.count + 1) * 1.5)


def _trimmed(records: dict[str, UsageRecord]) -> dict[str, UsageRecord]:
    if len(records) <= MAX_USAGE_RECORDS:
        return records
    newest = sorted(
        records.items(),
        key=lambda item: item[1].last_used,
        reverse=True,
    )[:MAX_USAGE_RECORDS]
    return dict(newest)


def _records_from(value: object) -> dict[str, UsageRecord]:
    if not isinstance(value, dict):
        return {}
    records: dict[str, UsageRecord] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            continue
        raw_count = raw.get("count", 0)
        raw_last_used = raw.get("last_used", 0)
        if not isinstance(raw_count, str | int | float) or not isinstance(
            raw_last_used,
            str | int | float,
        ):
            continue
        try:
            count = int(raw_count)
            last_used = float(raw_last_used)
        except (OverflowError, TypeError, ValueError):
            continue
        if 0 < count <= 1_000_000_000 and last_used > 0 and math.isfinite(last_used):
            records[key] = UsageRecord(count=count, last_used=last_used)
    return records


def _records_payload(
    records: dict[str, UsageRecord],
) -> dict[str, dict[str, float | int]]:
    return {
        key: {
            "count": record.count,
            "last_used": record.last_used,
        }
        for key, record in records.items()
    }


__all__ = [
    "DEFAULT_USAGE_FILE",
    "MAX_RANK_BOOST",
    "SearchUsageStore",
    "UsageRecord",
]
