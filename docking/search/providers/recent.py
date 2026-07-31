"""Recent-file search results backed by the shared catalog."""

from __future__ import annotations

from docking.i18n import _
from docking.search.coordinator import SearchRequest
from docking.search.preview import preview_local_descriptor
from docking.search.providers.base import (
    action,
    action_parts,
    is_special_query,
    metadata,
    score_fields,
)
from docking.search.services.recent_files import RecentFilesCatalog
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class RecentFilesSearchProvider:
    provider_id = "recent-files"

    def __init__(self, *, catalog: RecentFilesCatalog) -> None:
        self._catalog = catalog

    def search(self, request: SearchRequest):
        text = request.query.text.strip()
        if request.query.is_empty or is_special_query(text):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        results: list[SearchResult] = []
        for index, entry in enumerate(self._catalog.snapshot()):
            request.raise_if_cancelled()
            score = score_fields(
                request,
                (entry.name, entry.uri, entry.mime_type),
                source_boost=4,
                state_boost=max(0, 8 - index),
            )
            if score is None:
                continue
            results.append(
                SearchResult(
                    identity=SearchIdentity(self.provider_id, entry.uri),
                    title=entry.name,
                    description=entry.uri,
                    score=score,
                    icon_name="document-open-recent",
                    source=_("Recent Files"),
                    state=_("Recent"),
                    actions=(
                        action(
                            provider_id=self.provider_id,
                            entity_id=entry.uri,
                            action_id="open",
                            label=_("Open"),
                        ),
                    ),
                    metadata=metadata(uri=entry.uri, mime_type=entry.mime_type),
                    preview=preview_local_descriptor(
                        target=entry.uri,
                        title=entry.name,
                    ),
                    canonical_key=f"target:{entry.uri}",
                )
            )
        yield SearchBatch.replace(self.provider_id, request.generation, results)

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
            or parts[1] != "open"
        ):
            return False
        return self._catalog.open_uri(parts[0])


__all__ = ["RecentFilesSearchProvider"]
