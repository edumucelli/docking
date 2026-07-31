"""Present recognized dates, current times, and time-zone conversions.

Temporal parsing is isolated in a pure recognizer that returns already
formatted, immutable values. The provider converts exactly one recognized
value into a high-confidence result and stores its copy text behind the
canonical temporal key. It does not perform broad text matching and therefore
runs only when intent routing has confidently selected it.
"""

from __future__ import annotations

from collections.abc import Callable

from docking.i18n import _
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import action, action_parts, metadata
from docking.search.recognizers.temporal import (
    TemporalKind,
    TemporalValue,
    parse_temporal_query,
)
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class TemporalSearchProvider:
    """Produce one temporal utility result and own its copy action."""

    provider_id = "datetime"

    def __init__(self, *, copy_text: Callable[[str], None]) -> None:
        """Store the clipboard callback and initialize the active value cache."""
        self._copy_text = copy_text
        self._values: dict[str, str] = {}

    def search(self, request: SearchRequest):
        """Yield one recognized and fully evaluated temporal result."""
        value = (
            request.recognized
            if isinstance(request.recognized, TemporalValue)
            else parse_temporal_query(request.query.text)
        )
        self._values = {}
        if value is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        key = value.canonical_key
        self._values[key] = value.copy_text
        result = SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=value.title,
            description=value.description,
            score=1_000,
            icon_name=(
                "x-office-calendar"
                if value.kind is TemporalKind.DATE
                else "preferences-system-time"
            ),
            source=_("Date & Time"),
            state=value.state,
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy"),
                ),
            ),
            metadata=metadata(value=value.copy_text),
            canonical_key=value.canonical_key,
        )
        yield SearchBatch.replace(
            self.provider_id,
            request.generation,
            (result,),
        )

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        """Copy the cached temporal value for a validated action."""
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
            or parts[1] != "copy"
        ):
            return False
        value = self._values.get(parts[0])
        if value is None:
            return False
        self._copy_text(value)
        return True


__all__ = ["TemporalSearchProvider"]
