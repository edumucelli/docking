"""Search open window titles through the backend-neutral WindowService."""

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
    provider_id = "windows"

    def __init__(self, *, windows: WindowService) -> None:
        self._windows = windows
        self._window_ids: dict[str, WindowId] = {}

    def search(self, request: SearchRequest):
        text = request.query.text.strip()
        self._window_ids = {}
        explicit_scope = request.query.context_value("scope") == "win"
        if (request.query.is_empty and not explicit_scope) or is_special_query(text):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return

        results: list[SearchResult] = []
        for window in self._windows.list_all_windows():
            request.raise_if_cancelled()
            if request.query.is_empty:
                score = 250 + (10 if window.active else 0)
            else:
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
