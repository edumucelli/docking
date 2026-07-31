"""Search live windows through the backend-neutral window service.

Every request snapshots the windows currently exposed by the active backend.
Titles, desktop IDs, and application IDs participate in shared text matching;
the active state adds only a small bounded hint. Results carry preview metadata
without retaining backend window objects.

Window IDs are cached for the active result set and recovered only through a
provider-owned identity. Activation and close operations are delegated to the
window service, with close confirmation handled by the controller before the
provider is invoked.
"""

from __future__ import annotations

from docking.i18n import _
from docking.platform.backends.base import WindowId, WindowService
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import (
    action,
    action_parts,
    is_special_query,
    metadata,
    score_fields,
)
from docking.search.types import (
    SearchBatch,
    SearchIdentity,
    SearchPreview,
    SearchResult,
)


class WindowSearchProvider:
    """Produce live window matches and dispatch activate or close actions."""

    provider_id = "windows"

    def __init__(self, *, windows: WindowService) -> None:
        """Retain the active backend-neutral service used for window actions."""
        self._windows = windows
        self._window_ids: dict[str, WindowId] = {}

    def search(self, request: SearchRequest):
        """Snapshot and yield live windows matching the current query."""
        text = request.query.text.strip()
        self._window_ids = {}
        if request.query.is_empty or is_special_query(text):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return

        results: list[SearchResult] = []
        for window in self._windows.list_all_windows():
            request.raise_if_cancelled()
            score = score_fields(
                request,
                (window.title, window.desktop_id, window.app_id or ""),
                source_boost=5,
                state_boost=10 if window.active else 0,
            )
            if score is None:
                continue
            key = str(window.id)
            self._window_ids[key] = window.id
            result_actions = []
            if window.can_activate:
                result_actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=key,
                        action_id="activate",
                        label=_("Activate Window"),
                    )
                )
            if window.can_close:
                result_actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=key,
                        action_id="close",
                        label=_("Close Window"),
                        verb="close",
                    )
                )
            if not result_actions:
                continue
            results.append(
                SearchResult(
                    identity=SearchIdentity(self.provider_id, key),
                    title=window.title or _("Window"),
                    description=window.desktop_id,
                    score=score,
                    icon_name=self._windows.icon_name_for_desktop(window.desktop_id),
                    source=_("Windows"),
                    state=_("Active") if window.active else "",
                    actions=tuple(result_actions),
                    metadata=metadata(
                        desktop_id=window.desktop_id,
                        window_id=key,
                    ),
                    preview=SearchPreview(
                        title=window.title or _("Window"),
                        body=window.desktop_id,
                        kind="window" if window.can_preview else "text",
                        target=key if window.can_preview else "",
                    ),
                    canonical_key=f"window:{key}",
                )
            )
        yield SearchBatch.replace(self.provider_id, request.generation, results)

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        """Activate or close the cached window behind a validated action."""
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
        ):
            return False
        window_id = self._window_ids.get(parts[0])
        if window_id is None:
            return False
        if parts[1] == "activate":
            return self._windows.activate(window_id).succeeded
        if parts[1] == "close":
            return self._windows.close(window_id).succeeded
        return False


__all__ = ["WindowSearchProvider"]
