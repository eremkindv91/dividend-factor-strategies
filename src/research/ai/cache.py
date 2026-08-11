from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..fingerprints import fingerprint
from .config import AIConfig
from .prompts import PROMPT_VERSIONS


def ai_run_fingerprint(manifest: dict, config: AIConfig) -> str:
    return fingerprint(
        {
            "research_input_hash": manifest.get("research_input_hash"),
            "graph_version": config.graph_version,
            "prompt_versions": PROMPT_VERSIONS,
            "research_schema_version": manifest.get("schema_version"),
            "model_config_version": config.model_config_version,
            "models": config.public_model_config(),
        }
    )


def stock_ai_fingerprint(
    *,
    stock_fingerprint: str,
    manifest: dict,
    config: AIConfig,
) -> str:
    components = manifest.get("components") or {}
    return fingerprint(
        {
            "ticker_state_hash": stock_fingerprint,
            "market_hash": (components.get("market") or {}).get("fingerprint"),
            "sector_hash": (components.get("sectors") or {}).get("fingerprint"),
            "news_hash": (components.get("news") or {}).get("fingerprint"),
            "prompt_version": PROMPT_VERSIONS["stock"],
            "verifier_prompt_version": PROMPT_VERSIONS["verifier"],
            "synthesizer_prompt_version": PROMPT_VERSIONS["synthesizer"],
            "model_config_version": config.model_config_version,
            "models": config.public_model_config(),
        }
    )


class ValidatedCache:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def market(self, expected_fingerprint: str) -> dict[str, Any] | None:
        payload = self._read(self.output_dir / "market_memo.json")
        if not payload or payload.get("publishable") is not True:
            return None
        return payload if payload.get("ai_run_fingerprint") == expected_fingerprint else None

    def stock(self, ticker: str, expected_fingerprint: str) -> dict[str, Any] | None:
        payload = self._read(self.output_dir / "stocks" / f"{ticker}.json")
        if not payload or payload.get("publishable") is not True:
            return None
        return payload if payload.get("stock_ai_fingerprint") == expected_fingerprint else None

    def previous_stock_fingerprints(self) -> dict[str, str]:
        status = self._read(self.output_dir / "status.json") or {}
        values = status.get("stock_state_fingerprints") or {}
        return {str(key): str(value) for key, value in values.items()}
