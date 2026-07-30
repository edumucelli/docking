"""Explicit ``=`` calculator results using Docking's safe AST evaluator."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from docking.applets.calculator.state import evaluate
from docking.i18n import _
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import action, action_parts, metadata
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class CalculatorSearchProvider:
    provider_id = "calculator"

    def __init__(self, *, copy_text: Callable[[str], None]) -> None:
        self._copy_text = copy_text
        self._answers: dict[str, str] = {}

    def search(self, request: SearchRequest):
        text = request.query.text.strip()
        self._answers = {}
        if not text.startswith("="):
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        expression = text[1:].strip()
        answer = evaluate(expression)
        if not expression or not answer:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        if answer.startswith("Error"):
            error = SearchResult(
                identity=SearchIdentity(self.provider_id, "error"),
                title=_("Invalid expression"),
                description=answer.removeprefix("Error:").strip(),
                score=1_000,
                icon_name="dialog-error",
                source=_("Calculator"),
                state=_("Error"),
            )
            yield SearchBatch.replace(
                self.provider_id,
                request.generation,
                (error,),
            )
            return
        key = hashlib.sha256(expression.encode()).hexdigest()
        self._answers[key] = answer
        result = SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=answer,
            description=expression,
            score=1_000,
            icon_name="accessories-calculator",
            source=_("Calculator"),
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Result"),
                ),
            ),
            metadata=metadata(expression=expression, answer=answer),
            canonical_key=f"calculator:{expression}",
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
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
            or parts[1] != "copy"
        ):
            return False
        answer = self._answers.get(parts[0])
        if answer is None:
            return False
        self._copy_text(answer)
        return True


__all__ = ["CalculatorSearchProvider"]
