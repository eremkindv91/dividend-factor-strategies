from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


GRAPH_VERSION = "graph_v1"
MODEL_CONFIG_VERSION = "gemini_free_v2_single_model"


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


class AIConfig(BaseModel):
    execution_mode: Literal["mock", "real"] = "mock"
    real_execution_authorized: bool = False
    billing_allowed: bool = False
    free_tier_verified: bool = False
    analyst_model: str = "gemini-3.1-flash-lite"
    verifier_model: str = "gemini-3.1-flash-lite"
    synthesizer_model: str = "gemini-3.1-flash-lite"
    stock_universe_mode: Literal["priority", "changed", "explicit", "all"] = "priority"
    explicit_tickers: list[str] = Field(default_factory=list)
    allow_all_universe: bool = False
    max_ai_requests_per_run: int = Field(default=18, ge=1, le=100)
    max_stock_memos_per_run: int = Field(default=3, ge=0, le=20)
    max_parallel_requests: int = Field(default=3, ge=1, le=6)
    max_retries: int = Field(default=2, ge=0, le=4)
    max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    request_timeout_seconds: int = Field(default=60, ge=5, le=180)
    temperature: float = Field(default=0.1, ge=0, le=0.5)
    graph_version: str = GRAPH_VERSION
    model_config_version: str = MODEL_CONFIG_VERSION
    output_dir: Path = Path("site/data/research/ai")

    @model_validator(mode="after")
    def enforce_free_only(self) -> "AIConfig":
        if self.billing_allowed:
            raise ValueError("AI_BILLING_ALLOWED=true is forbidden in Gemini free-only V1")
        if self.execution_mode == "real" and not self.real_execution_authorized:
            raise ValueError(
                "real Gemini execution requires AI_REAL_GEMINI_SMOKE_AUTHORIZED=true; "
                "this does not assert that the project billing tier is free"
            )
        if self.stock_universe_mode == "all" and not self.allow_all_universe:
            raise ValueError("all-universe mode requires AI_ALLOW_ALL_UNIVERSE=true")
        return self

    @classmethod
    def from_env(cls) -> "AIConfig":
        explicit = [
            item.strip().upper()
            for item in os.getenv("AI_EXPLICIT_TICKERS", "").split(",")
            if item.strip()
        ]
        return cls(
            execution_mode=os.getenv("AI_EXECUTION_MODE", "mock").strip().lower(),
            real_execution_authorized=_bool_env("AI_REAL_GEMINI_SMOKE_AUTHORIZED"),
            billing_allowed=_bool_env("AI_BILLING_ALLOWED"),
            free_tier_verified=_bool_env("GEMINI_FREE_TIER_VERIFIED"),
            analyst_model=os.getenv("GEMINI_ANALYST_MODEL", "gemini-3.1-flash-lite"),
            verifier_model=os.getenv("GEMINI_VERIFIER_MODEL", "gemini-3.1-flash-lite"),
            synthesizer_model=os.getenv("GEMINI_SYNTHESIZER_MODEL", "gemini-3.1-flash-lite"),
            stock_universe_mode=os.getenv("AI_STOCK_UNIVERSE_MODE", "priority").strip().lower(),
            explicit_tickers=explicit,
            allow_all_universe=_bool_env("AI_ALLOW_ALL_UNIVERSE"),
            max_ai_requests_per_run=_int_env("MAX_AI_REQUESTS_PER_RUN", 18),
            max_stock_memos_per_run=_int_env("MAX_STOCK_MEMOS_PER_RUN", 3),
            max_parallel_requests=_int_env("MAX_PARALLEL_REQUESTS", 3),
            max_retries=_int_env("MAX_RETRIES", 2),
            max_output_tokens=_int_env("MAX_OUTPUT_TOKENS", 4096),
            request_timeout_seconds=_int_env("AI_REQUEST_TIMEOUT_SECONDS", 60),
            output_dir=Path(os.getenv("AI_RESEARCH_OUTPUT_DIR", "site/data/research/ai")),
        )

    def public_model_config(self) -> dict:
        return {
            "provider": "gemini",
            "execution_mode": self.execution_mode,
            "analyst_model": self.analyst_model,
            "verifier_model": self.verifier_model,
            "synthesizer_model": self.synthesizer_model,
            "model_config_version": self.model_config_version,
            "billing_allowed": False,
            "free_tier_verified": self.free_tier_verified,
        }
