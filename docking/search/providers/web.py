"""Detected URL and configurable web-search results."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from docking.i18n import _
from docking.platform.launcher import open_target
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import action, action_parts, metadata
from docking.search.types import SearchBatch, SearchIdentity, SearchResult
from docking.search.web import get_web_engine, normalize_web_target


class WebSearchProvider:
    provider_id = "web"

    def __init__(self, *, copy_text: Callable[[str], None]) -> None:
        self._copy_text = copy_text
        self._targets: dict[str, str] = {}

    def search(self, request: SearchRequest):
        text = request.query.text.strip()
        self._targets = {}
        if not text:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return

        direct_target = normalize_web_target(text)
        if direct_target is not None:
            result = self._url_result(direct_target)
        else:
            engine = get_web_engine(request.query.context_value("web_engine"))
            result = self._search_result(
                query=text,
                engine_id=engine.id,
                engine_name=engine.name,
                target=engine.url_for(text),
                explicit=request.query.context_value("explicit") == "true",
            )
        self._targets[result.identity.key] = dict(result.metadata)["target"]
        yield SearchBatch.replace(
            self.provider_id,
            request.generation,
            (result,),
        )

    def _url_result(self, target: str) -> SearchResult:
        key = hashlib.sha256(target.encode()).hexdigest()
        is_email = target.startswith("mailto:")
        return SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=_("Compose email") if is_email else _("Open URL"),
            description=target.removeprefix("mailto:"),
            score=1_050,
            icon_name="internet-web-browser",
            source=_("Web"),
            state=_("Email") if is_email else _("URL"),
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="open",
                    label=_("Compose") if is_email else _("Open"),
                ),
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Address"),
                ),
            ),
            metadata=metadata(target=target),
            canonical_key=f"url:{target}",
        )

    def _search_result(
        self,
        *,
        query: str,
        engine_id: str,
        engine_name: str,
        target: str,
        explicit: bool,
    ) -> SearchResult:
        key = hashlib.sha256(target.encode()).hexdigest()
        return SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=_("Search {engine} for “{query}”").format(
                engine=engine_name,
                query=query,
            ),
            description=target,
            score=950 if explicit else 75,
            icon_name="edit-find-symbolic",
            source=_("Web"),
            state=engine_name,
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="open",
                    label=_("Search the Web"),
                ),
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Address"),
                ),
            ),
            metadata=metadata(
                target=target,
                engine_id=engine_id,
                query=query,
            ),
            canonical_key=f"url:{target}",
        )

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
        target = self._targets.get(parts[0])
        if target is None:
            return False
        if parts[1] == "open":
            return open_target(target)
        if parts[1] == "copy":
            self._copy_text(target.removeprefix("mailto:"))
            return True
        return False


__all__ = ["WebSearchProvider"]
