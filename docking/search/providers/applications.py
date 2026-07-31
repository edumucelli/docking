"""Search installed applications and expose their contextual launcher actions.

The provider consumes immutable application catalog snapshots, but delegates
launching, pinning, window activation, and recent-document opening to existing
platform and model services. Base results stay cheap. Expensive preview data
and contextual actions are built only when the user asks to refine or preview
a selected application.

Application desktop IDs are used as stable identities. Canonical keys allow an
application result to deduplicate equivalent entities from another provider,
while action identities remain in this provider's namespace. Small caches map
those transport-neutral identities back to window IDs and preview values for
the current result set.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import unquote, urlparse

from docking.core.items import APP_KIND
from docking.i18n import _
from docking.platform import launcher as launcher_actions
from docking.platform.backends.base import WindowId, WindowService
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.recent_docs import recent_docs_for_app
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import (
    action,
    action_parts,
    is_special_query,
    metadata,
    score_fields,
)
from docking.search.services.application_catalog import ApplicationCatalog
from docking.search.types import (
    SearchBatch,
    SearchIdentity,
    SearchPreview,
    SearchResult,
)


class ApplicationSearchProvider:
    """Produce installed application results and dispatch application actions."""

    provider_id = "applications"

    def __init__(
        self,
        *,
        catalog: ApplicationCatalog,
        launcher: Launcher,
        model: DockModel,
        windows: WindowService,
        recent_docs_limit: int,
    ) -> None:
        """Bind the immutable catalog to existing launcher and model services."""
        self._catalog = catalog
        self._launcher = launcher
        self._model = model
        self._windows = windows
        self._recent_docs_limit = recent_docs_limit
        self._refined_window_ids: dict[tuple[str, str], WindowId] = {}
        self._preview_cache: dict[str, SearchPreview] = {}

    def search(self, request: SearchRequest):
        """Yield ranked installed applications for one current request."""
        request.raise_if_cancelled()
        self._preview_cache.clear()
        text = request.query.text.strip()
        if is_special_query(text):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return

        visible_apps = {
            item.desktop_id: item
            for item in self._model.visible_items()
            if item.kind == APP_KIND
        }
        results: list[SearchResult] = []
        for application in self._catalog.snapshot():
            request.raise_if_cancelled()
            item = visible_apps.get(application.desktop_id)
            pinned = bool(item and item.is_pinned)
            running = bool(item and item.is_running)
            recent = bool(item and item.is_recent)
            if request.query.is_empty:
                if not (pinned or running or recent):
                    continue
                score = 240.0 + (30 if pinned else 0) + (20 if running else 0)
                if recent:
                    score += 10
            else:
                score = score_fields(
                    request,
                    (
                        application.name,
                        application.description,
                        *application.keywords,
                        *application.categories,
                    ),
                    source_boost=6,
                    state_boost=(12 if pinned else 0)
                    + (10 if running else 0)
                    + (5 if recent else 0),
                )
                if score is None:
                    continue

            windows = self._windows.list_windows(application.desktop_id)
            can_focus = any(window.can_activate for window in windows)
            can_close = any(window.can_close for window in windows)
            actions = []
            if can_focus:
                actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=application.desktop_id,
                        action_id="focus",
                        label=_("Focus"),
                    )
                )
            else:
                actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=application.desktop_id,
                        action_id="open",
                        label=_("Open"),
                    )
                )
            actions.append(
                action(
                    provider_id=self.provider_id,
                    entity_id=application.desktop_id,
                    action_id="new-window",
                    label=_("Open New Window"),
                )
            )
            for desktop_action in application.actions:
                actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=application.desktop_id,
                        action_id=f"desktop:{desktop_action.action_id}",
                        label=desktop_action.name,
                    )
                )
            actions.append(
                action(
                    provider_id=self.provider_id,
                    entity_id=application.desktop_id,
                    action_id="unpin" if pinned else "pin",
                    label=_("Remove from Dock") if pinned else _("Keep in Dock"),
                )
            )
            if can_close:
                actions.append(
                    action(
                        provider_id=self.provider_id,
                        entity_id=application.desktop_id,
                        action_id="close-all",
                        label=_("Close All") if len(windows) > 1 else _("Close"),
                        verb="close",
                    )
                )

            states = [
                label
                for enabled, label in (
                    (running, _("Running")),
                    (pinned, _("Pinned")),
                    (recent, _("Recent")),
                )
                if enabled
            ]
            results.append(
                SearchResult(
                    identity=SearchIdentity(
                        self.provider_id,
                        application.desktop_id,
                    ),
                    title=application.name,
                    description=application.description,
                    score=score,
                    icon_name=_icon_name(application.icon.kind, application.icon.value),
                    source=_("Applications"),
                    state=" · ".join(states),
                    keywords=application.keywords,
                    actions=tuple(actions),
                    metadata=metadata(desktop_id=application.desktop_id),
                    canonical_key=f"app:{application.desktop_id}",
                )
            )

        yield SearchBatch.replace(self.provider_id, request.generation, results)

    def build_preview(self, result: SearchResult) -> SearchPreview:
        """Build and cache a provider-neutral preview for one application."""
        desktop_id = result.identity.key
        cached = self._preview_cache.get(desktop_id)
        if cached is not None:
            return cached
        application = self._catalog.get(desktop_id)
        windows = self._windows.list_windows(desktop_id)
        documents = recent_docs_for_app(
            desktop_id,
            launcher=self._launcher,
            limit=min(5, self._recent_docs_limit),
        )
        lines = []
        description = (
            application.description if application is not None else result.description
        )
        if description:
            lines.extend((description, ""))
        lines.append(_("Desktop ID: {desktop_id}").format(desktop_id=desktop_id))
        if result.state:
            lines.append(_("State: {state}").format(state=result.state))
        if application is not None and application.categories:
            lines.append(
                _("Categories: {categories}").format(
                    categories=", ".join(application.categories)
                )
            )
        if windows:
            lines.extend(("", _("Windows")))
            lines.extend(f"• {window.title or _('Window')}" for window in windows[:5])
        if result.actions:
            lines.extend(("", _("Actions")))
            lines.extend(f"• {item.label}" for item in result.actions[:6])
        if documents:
            lines.extend(("", _("Recent Documents")))
            lines.extend(f"• {document.name}" for document in documents)
        preview = SearchPreview(
            title=result.title,
            body="\n".join(lines),
            kind="application",
        )
        self._preview_cache[desktop_id] = preview
        return preview

    def refine(self, result: SearchResult) -> SearchResult:
        """Expand a selected application with live windows and recent files."""
        """Add per-window and recent-document actions for Tab refinement."""
        if result.identity.provider_id != self.provider_id:
            return result
        desktop_id = result.identity.key
        window_actions = []
        for window in self._windows.list_windows(desktop_id):
            if not window.can_activate:
                continue
            window_key = str(window.id)
            self._refined_window_ids[(desktop_id, window_key)] = window.id
            window_actions.append(
                action(
                    provider_id=self.provider_id,
                    entity_id=desktop_id,
                    action_id=f"window:{window_key}",
                    label=_("Activate “{title}”").format(
                        title=window.title or _("Window")
                    ),
                )
            )
        recent_actions = [
            action(
                provider_id=self.provider_id,
                entity_id=desktop_id,
                action_id=f"recent:{document.uri}",
                label=_("Open Recent: {name}").format(name=document.name),
            )
            for document in recent_docs_for_app(
                desktop_id,
                launcher=self._launcher,
                limit=self._recent_docs_limit,
            )
        ]
        existing = list(result.actions)
        refined_actions = (
            [existing[0], *window_actions, *existing[1:], *recent_actions]
            if existing
            else [*window_actions, *recent_actions]
        )
        return replace(result, actions=tuple(refined_actions))

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        """Validate an application action identity and dispatch its side effect."""
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
        ):
            return False
        desktop_id, action_id = parts
        if action_id == "open":
            launcher_actions.launch(desktop_id=desktop_id)
            return True
        if action_id == "focus":
            return self._windows.activate_most_recent(desktop_id).succeeded
        if action_id.startswith("window:"):
            window_id = self._refined_window_ids.get(
                (desktop_id, action_id.removeprefix("window:"))
            )
            return window_id is not None and self._windows.activate(window_id).succeeded
        if action_id.startswith("recent:"):
            return launcher_actions.open_target(action_id.removeprefix("recent:"))
        if action_id == "new-window":
            launcher_actions.launch_new_window(desktop_id=desktop_id)
            return True
        if action_id.startswith("desktop:"):
            launcher_actions.launch_action(
                desktop_id=desktop_id,
                action_id=action_id.removeprefix("desktop:"),
            )
            return True
        if action_id == "pin":
            return self._model.pin_application(desktop_id)
        if action_id == "unpin":
            self._model.unpin_item(desktop_id)
            return True
        if action_id == "close-all":
            return self._windows.close_all(desktop_id).succeeded
        return False


def _icon_name(kind: str, value: str) -> str | None:
    if not value:
        return None
    if kind == "file" and value.startswith("file://"):
        return unquote(urlparse(value).path)
    return value


__all__ = ["ApplicationSearchProvider"]
