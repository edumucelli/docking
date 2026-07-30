"""Explicit ``cmd`` provider for user-owned script commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from docking.i18n import _
from docking.platform.launcher import open_target
from docking.search.coordinator import SearchRequest
from docking.search.preview import preview_local_descriptor
from docking.search.providers.base import action, action_parts, metadata, score_fields
from docking.search.scripts import (
    ScriptCommand,
    ScriptCommandCatalog,
    execute_script,
    parse_script_arguments,
)
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


@dataclass(frozen=True, slots=True)
class _Invocation:
    command: ScriptCommand
    arguments: tuple[str, ...]


class ScriptCommandSearchProvider:
    provider_id = "scripts"

    def __init__(
        self,
        *,
        catalog: ScriptCommandCatalog,
        copy_text: Callable[[str], None],
    ) -> None:
        self._catalog = catalog
        self._copy_text = copy_text
        self._invocations: dict[str, _Invocation] = {}

    def search(self, request: SearchRequest):
        query = request.query.text.strip()
        tokens = parse_script_arguments(query)
        if tokens is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        self._invocations = {}
        results: list[SearchResult] = []
        for command in self._catalog.snapshot():
            request.raise_if_cancelled()
            exact_keyword = bool(tokens) and tokens[0].casefold() == command.keyword
            arguments = tokens[1:] if exact_keyword else ()
            if not query:
                score = 250
            elif exact_keyword:
                score = 1_000
            else:
                score = score_fields(
                    request,
                    (command.name, command.keyword, command.description),
                    source_boost=8,
                )
                if score is None:
                    continue
            key = str(command.path)
            self._invocations[key] = _Invocation(command, arguments)
            safe_actions = (
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="open",
                    label=_("Open Script"),
                ),
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Path"),
                ),
            )
            result_actions = (
                (
                    action(
                        provider_id=self.provider_id,
                        entity_id=key,
                        action_id="run",
                        label=_("Run"),
                    ),
                    action(
                        provider_id=self.provider_id,
                        entity_id=key,
                        action_id="terminal",
                        label=_("Run in Terminal"),
                    ),
                    *safe_actions,
                )
                if exact_keyword
                else safe_actions
            )
            results.append(
                SearchResult(
                    identity=SearchIdentity(self.provider_id, key),
                    title=command.name,
                    description=command.description or str(command.path),
                    score=score,
                    icon_name=command.icon_name,
                    source=_("Script Commands"),
                    state=command.keyword,
                    keywords=(command.keyword,),
                    actions=result_actions,
                    metadata=metadata(
                        path=str(command.path),
                        keyword=command.keyword,
                        arguments=" ".join(arguments),
                        mode=command.mode,
                    ),
                    preview=preview_local_descriptor(
                        target=str(command.path),
                        title=command.name,
                    ),
                    canonical_key=f"script:{command.path}",
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
        invocation = self._invocations.get(parts[0])
        if invocation is None:
            return False
        if parts[1] == "run":
            return execute_script(
                command=invocation.command,
                arguments=invocation.arguments,
                run_in_terminal=invocation.command.mode == "terminal",
            )
        if parts[1] == "terminal":
            return execute_script(
                command=invocation.command,
                arguments=invocation.arguments,
                run_in_terminal=True,
            )
        if parts[1] == "open":
            return open_target(str(invocation.command.path))
        if parts[1] == "copy":
            self._copy_text(str(invocation.command.path))
            return True
        return False


__all__ = ["ScriptCommandSearchProvider"]
