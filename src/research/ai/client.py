from __future__ import annotations

import asyncio
import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .config import AIConfig
from .schemas import RequestUsage
from .wire import (
    MARKET_SECTIONS,
    STOCK_SECTIONS,
    WireAnalystOutput,
    WireFinding,
    WireMarketMemo,
    WireMarketSection,
    WireStockMemo,
    WireStockSection,
    WireVerificationDecision,
    WireVerifierOutput,
)


T = TypeVar("T", bound=BaseModel)


class AIClientError(RuntimeError):
    pass


class CapacityError(AIClientError):
    pass


class BudgetExceeded(AIClientError):
    pass


class TechnicalAIError(AIClientError):
    pass


@dataclass(frozen=True)
class Generated(Generic[T]):
    value: T
    usage: RequestUsage


class RequestBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.requests = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> int:
        async with self._lock:
            if self.requests >= self.limit:
                raise BudgetExceeded(f"AI request budget exhausted ({self.limit})")
            self.requests += 1
            return self.requests


class AIClient(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        node: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[T],
        model: str,
    ) -> Generated[T]:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return []


def _retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("429", "resource_exhausted", "503", "unavailable", "timeout", "deadline", "internal", "500")
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError, ValidationError, json.JSONDecodeError)) or any(
        marker in text for marker in markers
    )


def _capacity(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "resource_exhausted" in text or "rate_limit" in text


_GEMINI_KEY_PATTERN = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")


def _safe_error_detail(exc: Exception) -> str:
    detail = re.sub(r"\s+", " ", str(exc)).strip()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        detail = detail.replace(key, "<redacted>")
    return _GEMINI_KEY_PATTERN.sub("<redacted>", detail)[:400]


def _parse_response(response: Any, response_model: type[T]) -> T:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, response_model):
        return parsed
    if isinstance(parsed, dict):
        return response_model.model_validate(parsed)
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise TechnicalAIError("Gemini returned an empty response")
    return response_model.model_validate_json(text)


def _usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return None


_UNSUPPORTED_GEMINI_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "default", "minLength", "maxLength", "title"}
)


def _gemini_json_schema(response_model: type[BaseModel]) -> dict[str, Any]:
    """Return the documented Gemini JSON Schema subset for a Pydantic model.

    Gemini rejects unsupported Pydantic string constraints with a generic
    INVALID_ARGUMENT response. The full model still validates every response.
    """

    schema = response_model.model_json_schema()
    definitions = schema.get("$defs", {})

    def clean(value: Any, resolving: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in resolving or name not in definitions:
                    raise ValueError(f"unsupported recursive or missing schema reference: {ref}")
                return clean(definitions[name], resolving | {name})
            variants = value.get("anyOf")
            if isinstance(variants, list) and variants and all(
                isinstance(item, dict) and set(item) == {"type"} and isinstance(item["type"], str)
                for item in variants
            ):
                types = list(dict.fromkeys(item["type"] for item in variants))
                return {"type": types}
            return {
                key: clean(item, resolving)
                for key, item in value.items()
                if key not in _UNSUPPORTED_GEMINI_SCHEMA_KEYS and key != "$defs"
            }
        if isinstance(value, list):
            return [clean(item, resolving) for item in value]
        return value

    return clean(schema)


class GeminiClient(AIClient):
    def __init__(self, config: AIConfig, budget: RequestBudget | None = None):
        if config.billing_allowed:
            raise ValueError("paid Gemini execution is disabled")
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise RuntimeError("google-genai is not installed") from exc
        http_options = types.HttpOptions(
            timeout=config.request_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        self._types = types
        self._client = genai.Client(api_key=key, http_options=http_options)
        self.config = config
        self.budget = budget or RequestBudget(config.max_ai_requests_per_run)
        self._semaphore = asyncio.Semaphore(config.max_parallel_requests)
        self.rate_limit_errors_total = 0
        self.retry_count_total = 0

    async def generate(
        self,
        *,
        node: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[T],
        model: str,
    ) -> Generated[T]:
        started = time.perf_counter()
        rate_limits = 0
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(self.config.max_retries + 1):
            attempts = attempt + 1
            await self.budget.reserve()
            try:
                async with self._semaphore:
                    response = await self._client.aio.models.generate_content(
                        model=model,
                        contents=json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                        config=self._types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_json_schema=_gemini_json_schema(response_model),
                            temperature=self.config.temperature,
                            max_output_tokens=self.config.max_output_tokens,
                        ),
                    )
                value = _parse_response(response, response_model)
                usage = getattr(response, "usage_metadata", None)
                return Generated(
                    value=value,
                    usage=RequestUsage(
                        node=node,
                        model=model,
                        attempts=attempts,
                        input_tokens=_usage_value(usage, "prompt_token_count", "prompt_tokens"),
                        output_tokens=_usage_value(usage, "candidates_token_count", "output_tokens"),
                        total_tokens=_usage_value(usage, "total_token_count", "total_tokens"),
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        rate_limit_errors=rate_limits,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - provider exceptions vary by SDK version
                last_error = exc
                if _capacity(exc):
                    rate_limits += 1
                    self.rate_limit_errors_total += 1
                if attempt >= self.config.max_retries or not _retryable(exc):
                    break
                self.retry_count_total += 1
                await asyncio.sleep(min(2**attempt, 4))
        if last_error is not None and _capacity(last_error):
            raise CapacityError(f"Gemini free-tier capacity unavailable after {attempts} attempts") from last_error
        detail = _safe_error_detail(last_error) if last_error is not None else "unknown error"
        raise TechnicalAIError(
            f"Gemini request failed after {attempts} attempts: {type(last_error).__name__}: {detail}"
        ) from last_error

    async def list_models(self) -> list[str]:
        models = await asyncio.to_thread(lambda: list(self._client.models.list()))
        return sorted(
            str(getattr(model, "name", "")).removeprefix("models/")
            for model in models
            if getattr(model, "name", None)
        )


class MockAIClient(AIClient):
    """Scripted client used to prove graph behavior without network or quota."""

    def __init__(
        self,
        responses: dict[str, list[Any]],
        *,
        budget: RequestBudget,
        delay_seconds: float = 0,
        model_names: list[str] | None = None,
    ):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.budget = budget
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []
        self.model_names = model_names or ["gemini-3.1-flash-lite", "gemini-3.5-flash"]

    async def generate(
        self,
        *,
        node: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[T],
        model: str,
    ) -> Generated[T]:
        await self.budget.reserve()
        self.calls.append(node)
        started = time.perf_counter()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        scripted = self.responses.get(node, [])
        if not scripted:
            raise TechnicalAIError(f"no mock response configured for {node}")
        item = scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            value = response_model.model_validate_json(item)
        elif isinstance(item, response_model):
            value = item
        else:
            value = response_model.model_validate(item)
        return Generated(
            value=value,
            usage=RequestUsage(
                node=node,
                model=model,
                attempts=1,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                duration_ms=round((time.perf_counter() - started) * 1000),
            ),
        )

    async def list_models(self) -> list[str]:
        return sorted(self.model_names)


class DeterministicMockAIClient(AIClient):
    """Network-free valid responses for end-to-end graph and CLI smoke tests."""

    def __init__(self, budget: RequestBudget, *, delay_seconds: float = 0):
        self.budget = budget
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []

    @staticmethod
    def _finding(node: str, payload: dict[str, Any]) -> WireAnalystOutput:
        catalog = payload.get("evidence_catalog") or []
        artifact_names = sorted({str(row.get("source_ref") or "").split("#", 1)[0] for row in catalog})
        agent = "stock" if node.startswith("stock:") else node
        entity_type = "stock" if agent == "stock" else ("market" if agent == "market" else agent.rstrip("s"))
        if entity_type not in {"market", "macro", "sector", "stock", "bank", "bond", "news"}:
            entity_type = "market"
        entity_id = node.split(":", 1)[1] if ":" in node else agent.upper()
        if agent == "stock":
            preferred = [
                "market_snapshot.json",
                "sector_snapshot.json",
                f"stocks/{entity_id}.json",
            ]
            preferred.extend(name for name in artifact_names if name.startswith("stock_context/"))
            artifact_names = preferred
        else:
            artifact_names = artifact_names[:1]
        findings: list[WireFinding] = []
        for index, artifact in enumerate(artifact_names):
            source_ref = f"{artifact}#asof"
            entry = next((row for row in catalog if row.get("source_ref") == source_ref), None)
            if entry is None:
                continue
            source = entry.get("value")
            findings.append(
                WireFinding(
                    id=f"{node.replace(':', '_')}_asof_{index}",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    claim=f"Доступен проверяемый срез данных {artifact} на дату {source}.",
                    claim_type="data_availability",
                    kind="fact",
                    evidence_refs=[entry["id"]],
                    counter_evidence_refs=[],
                    materiality="medium",
                    confidence=0.8,
                    causal=False,
                    warnings=[],
                    invalidation=["Появление более свежего валидного среза."],
                )
            )
        return WireAnalystOutput(findings=findings, warnings=[])

    @staticmethod
    def _verifier(payload: dict[str, Any]) -> WireVerifierOutput:
        return WireVerifierOutput(
            results=[
                WireVerificationDecision(
                    finding_id=row["id"],
                    verdict="PASS",
                    confidence=min(float(row.get("confidence", 0)), 0.8),
                    reason="mock_evidence_contract_passed",
                    warnings=[],
                )
                for row in payload.get("findings", [])
            ],
            warnings=[],
        )

    @staticmethod
    def _market_memo(payload: dict[str, Any]) -> WireMarketMemo:
        findings = payload.get("findings", [])
        ids = [row["id"] for row in findings]
        return WireMarketMemo(
            regime_label="недостаточно данных для изменения режима",
            regime_confidence="low",
            summary="Mock-проверка graph contract; не является реальным Gemini research.",
            key_finding_ids=ids,
            sections=[
                WireMarketSection(
                    key=key,
                    summary=(
                        "Сохраняются ограничения качества данных."
                        if key == "risks"
                        else "Выводы ограничены доступными проверенными findings."
                    ),
                    finding_ids=ids,
                )
                for key in sorted(MARKET_SECTIONS)
            ],
        )

    @staticmethod
    def _stock_memo(payload: dict[str, Any]) -> WireStockMemo:
        findings = payload.get("findings", [])
        ids = [row["id"] for row in findings]
        return WireStockMemo(
            confidence="low",
            sections=[
                WireStockSection(
                    key=key,
                    summary=(
                        "Сохраняются ограничения качества данных."
                        if key == "risks"
                        else "Доступен только проверенный source-state context."
                    ),
                    finding_ids=ids,
                )
                for key in sorted(STOCK_SECTIONS)
            ],
            invalidation=["Более свежий валидный research state."],
        )

    async def generate(
        self,
        *,
        node: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[T],
        model: str,
    ) -> Generated[T]:
        await self.budget.reserve()
        self.calls.append(node)
        started = time.perf_counter()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if response_model is WireAnalystOutput:
            value = self._finding(node, payload)
        elif response_model is WireVerifierOutput:
            value = self._verifier(payload)
        elif response_model is WireMarketMemo:
            value = self._market_memo(payload)
        elif response_model is WireStockMemo:
            value = self._stock_memo(payload)
        else:  # pragma: no cover - new schema requires explicit mock support
            raise TechnicalAIError(f"unsupported deterministic mock schema: {response_model.__name__}")
        return Generated(
            value=response_model.model_validate(value),
            usage=RequestUsage(
                node=node,
                model=model,
                attempts=1,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                duration_ms=round((time.perf_counter() - started) * 1000),
            ),
        )
