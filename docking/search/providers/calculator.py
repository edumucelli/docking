"""Turn recognized arithmetic into copyable calculator results.

Evaluation is delegated to the safe AST recognizer. This provider never calls
Python ``eval`` and never executes names outside the recognizer's fixed
operation table. Both valid values and structured errors become results, so an
explicit but incomplete expression receives useful feedback instead of
silently falling back to unrelated fuzzy matches.

Answers are cached by a hash-based result identity for the active result set.
Invocation can therefore copy the exact displayed answer without placing raw
expressions inside action payloads.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from docking.i18n import _
from docking.search.coordinator import SearchRequest
from docking.search.providers.base import action, action_parts, metadata
from docking.search.recognizers.calculation import (
    CalculationError,
    CalculationValue,
    recognize_calculation,
)
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class CalculatorSearchProvider:
    """Present recognized calculations and own their copy action."""

    provider_id = "calculator"

    def __init__(self, *, copy_text: Callable[[str], None]) -> None:
        """Store the clipboard callback used by emitted copy actions."""
        self._copy_text = copy_text
        self._answers: dict[str, str] = {}

    def search(self, request: SearchRequest):
        """Yield a calculation answer or a structured explicit-input error."""
        text = request.query.text.strip()
        self._answers = {}
        value = (
            request.recognized
            if isinstance(request.recognized, CalculationValue)
            else recognize_calculation(text)
        )
        if value is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        if value.error is not None:
            descriptions = {
                CalculationError.DIVISION_BY_ZERO: _("Division by zero"),
                CalculationError.DOMAIN: _("Invalid value or mathematical domain"),
                CalculationError.OVERFLOW: _("Result is too large"),
                CalculationError.TOO_COMPLEX: _("Expression is too complex"),
                CalculationError.INVALID: _("Unsupported or incomplete expression"),
            }
            error = SearchResult(
                identity=SearchIdentity(self.provider_id, "error"),
                title=_("Invalid expression"),
                description=descriptions[value.error],
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
        expression = value.expression
        answer = value.answer
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
        """Copy the cached answer for a validated result and action identity."""
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
