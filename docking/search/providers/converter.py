"""Implicit static-unit conversion results."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from docking.i18n import _
from docking.search.conversion import (
    CurrencyConversionRequest,
    UnitConversion,
    parse_currency_conversion,
    parse_unit_conversion,
)
from docking.search.coordinator import SearchRequest
from docking.search.currency import CurrencyRatesCatalog, CurrencyRatesState
from docking.search.providers.base import action, action_parts, metadata
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class ConverterSearchProvider:
    provider_id = "converter"

    def __init__(
        self,
        *,
        copy_text: Callable[[str], None],
        currency_rates: CurrencyRatesCatalog | None = None,
    ) -> None:
        self._copy_text = copy_text
        self._currency_rates = currency_rates
        self._answers: dict[str, str] = {}

    def search(self, request: SearchRequest):
        conversion = parse_unit_conversion(request.query.text)
        self._answers = {}
        if conversion is not None:
            result = self._static_result(conversion)
        else:
            currency = parse_currency_conversion(request.query.text)
            result = self._currency_result(currency) if currency is not None else None
        if result is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        yield SearchBatch.replace(
            self.provider_id,
            request.generation,
            (result,),
        )

    def _static_result(self, conversion: UnitConversion) -> SearchResult:
        answer = f"{conversion.formatted_result} {conversion.target.symbol}"
        key = hashlib.sha256(conversion.expression.encode()).hexdigest()
        self._answers[key] = answer
        return SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=answer,
            description=_("{value:g} {source} to {target}").format(
                value=conversion.value,
                source=conversion.source.name,
                target=conversion.target.name,
            ),
            score=1_000,
            icon_name="accessories-calculator",
            source=_("Converter"),
            state=conversion.category.value,
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Result"),
                ),
            ),
            metadata=metadata(
                expression=conversion.expression,
                answer=answer,
                category=conversion.category.value,
            ),
            canonical_key=f"conversion:{conversion.expression.casefold()}",
        )

    def _currency_result(
        self,
        request: CurrencyConversionRequest,
    ) -> SearchResult:
        key = hashlib.sha256(request.expression.encode()).hexdigest()
        catalog = self._currency_rates
        if catalog is None:
            return self._currency_status_result(
                key=key,
                request=request,
                title=_("Currency rates unavailable"),
                retry=False,
            )
        catalog.ensure_loaded()
        if catalog.state is CurrencyRatesState.LOADING:
            return self._currency_status_result(
                key=key,
                request=request,
                title=_("Loading currency rates..."),
                retry=False,
            )
        if catalog.state is CurrencyRatesState.ERROR:
            return self._currency_status_result(
                key=key,
                request=request,
                title=_("Currency rates unavailable"),
                retry=True,
            )
        converted = catalog.convert(
            value=request.value,
            source_code=request.source_code,
            target_code=request.target_code,
        )
        if converted is None:
            return self._currency_status_result(
                key=key,
                request=request,
                title=_("Currency pair unavailable"),
                retry=True,
            )
        answer = f"{converted:,.4f}".rstrip("0").rstrip(".")
        answer = f"{answer} {request.target_code}"
        self._answers[key] = answer
        return SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=answer,
            description=_("{value:g} {source} to {target}").format(
                value=request.value,
                source=request.source_code,
                target=request.target_code,
            ),
            score=1_000,
            icon_name="accessories-calculator",
            source=_("Converter"),
            state=_("Currency"),
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="copy",
                    label=_("Copy Result"),
                ),
            ),
            metadata=metadata(
                expression=request.expression,
                answer=answer,
                category="Currency",
            ),
            canonical_key=f"conversion:{request.expression.casefold()}",
        )

    def _currency_status_result(
        self,
        *,
        key: str,
        request: CurrencyConversionRequest,
        title: str,
        retry: bool,
    ) -> SearchResult:
        actions = (
            (
                action(
                    provider_id=self.provider_id,
                    entity_id=key,
                    action_id="retry",
                    label=_("Retry"),
                ),
            )
            if retry
            else ()
        )
        return SearchResult(
            identity=SearchIdentity(self.provider_id, key),
            title=title,
            description=request.expression,
            score=1_000,
            icon_name="view-refresh-symbolic" if retry else "content-loading-symbolic",
            source=_("Converter"),
            state=_("Currency"),
            actions=actions,
            metadata=metadata(expression=request.expression),
            canonical_key=f"conversion:{request.expression.casefold()}",
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
        if parts[1] == "retry" and self._currency_rates is not None:
            self._currency_rates.retry()
            return False
        if parts[1] != "copy":
            return False
        answer = self._answers.get(parts[0])
        if answer is None:
            return False
        self._copy_text(answer)
        return True


__all__ = ["ConverterSearchProvider"]
