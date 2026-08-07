"""Search pinned non-application items from the live dock model.

Installed applications have their own richer provider, so this provider emits
only pinned applets, files, and folders. It reads the current visible model on
each request, uses shared text scoring, and marks the canonical target so a
file found elsewhere can be deduplicated.

Actions call existing model and target-service operations. Removing an item is
destructive enough to require controller confirmation, but the provider still
owns the final identity validation and mutation. Preview descriptors remain
cheap until the user opens the preview panel.
"""

from __future__ import annotations

from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND
from docking.i18n import _
from docking.platform.model import DockModel
from docking.platform.targets import TargetService
from docking.search.coordinator import SearchRequest
from docking.search.preview import preview_local_descriptor
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


class DockSearchProvider:
    """Produce pinned dock results and dispatch open or unpin actions."""

    provider_id = "dock"

    def __init__(self, *, model: DockModel, target_service: TargetService) -> None:
        """Retain the live model that owns pinned items and their mutations."""
        self._model = model
        self._target_service = target_service

    def search(self, request: SearchRequest):
        """Yield matching pinned applets, files, and folders from the model."""
        text = request.query.text.strip()
        if is_special_query(text):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return

        results: list[SearchResult] = []
        for item in self._model.visible_items():
            request.raise_if_cancelled()
            if item.kind == APP_KIND or not item.is_pinned:
                continue
            title = item.name or item.target
            if item.kind == APPLET_KIND:
                applet = self._model.get_applet(item.desktop_id)
                applet_name = getattr(applet, "name", "")
                if isinstance(applet_name, str) and applet_name.strip():
                    title = applet_name.strip()
            if request.query.is_empty:
                score = 255.0
            else:
                score = score_fields(
                    request,
                    (title, item.target, item.desktop_id),
                    source_boost=4,
                    state_boost=12,
                )
                if score is None:
                    continue

            if item.kind == APPLET_KIND:
                primary_label = _("Open")
            elif item.kind == FOLDER_KIND:
                primary_label = _("Open Folder")
            else:
                primary_label = _("Open File")
            result_actions = [
                action(
                    provider_id=self.provider_id,
                    entity_id=item.desktop_id,
                    action_id="open",
                    label=primary_label,
                ),
                action(
                    provider_id=self.provider_id,
                    entity_id=item.desktop_id,
                    action_id="remove",
                    label=_("Remove from Dock"),
                    verb="remove",
                ),
            ]
            results.append(
                SearchResult(
                    identity=SearchIdentity(self.provider_id, item.desktop_id),
                    title=title,
                    description=item.target if item.kind != APPLET_KIND else "",
                    score=score,
                    icon_name=item.icon_name or None,
                    source=_("Dock"),
                    state=_("Pinned"),
                    actions=tuple(result_actions),
                    metadata=metadata(
                        desktop_id=item.desktop_id,
                        target=item.target,
                        kind=item.kind,
                    ),
                    preview=(
                        preview_local_descriptor(target=item.target, title=title)
                        if item.kind in {FILE_KIND, FOLDER_KIND}
                        else SearchPreview(
                            title=title,
                            body=_("Dock applet"),
                            kind="applet",
                        )
                    ),
                    canonical_key=(
                        f"target:{item.target}"
                        if item.kind in {FILE_KIND, FOLDER_KIND}
                        else f"applet:{item.desktop_id}"
                    ),
                )
            )
        yield SearchBatch.replace(self.provider_id, request.generation, results)

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        """Dispatch a validated open or unpin operation."""
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
        ):
            return False
        desktop_id, action_id = parts
        item = self._model.find_by_desktop_id(desktop_id=desktop_id)
        if item is None:
            return False
        if action_id == "remove":
            self._model.unpin_item(desktop_id)
            return True
        if action_id != "open":
            return False
        if item.kind == APPLET_KIND:
            applet = self._model.get_applet(desktop_id)
            if applet is None:
                return False
            applet.on_clicked()
            return True
        if item.kind in {FILE_KIND, FOLDER_KIND}:
            return self._target_service.open_target(item.target)
        return False


__all__ = ["DockSearchProvider"]
