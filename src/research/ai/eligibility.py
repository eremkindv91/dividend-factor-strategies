from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AIConfig
from .schemas import UniverseSelection


def _load_stock(research_dir: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    relative = str(row.get("path") or "").removeprefix("data/research/")
    if not relative.startswith("stocks/"):
        return None
    try:
        payload = json.loads((research_dir / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _exclusion_reasons(stock: dict[str, Any] | None) -> list[str]:
    if stock is None:
        return ["snapshot_unavailable"]
    reasons = []
    if (stock.get("price") or {}).get("value") is None:
        reasons.append("price_missing")
    if not ((stock.get("liquidity") or {}).get("adv_rub") or 0) > 0:
        reasons.append("positive_adv_missing")
    if not (stock.get("sector_context") or {}).get("sector"):
        reasons.append("sector_missing")
    if (stock.get("data_quality") or {}).get("fresh") is not True:
        reasons.append("stock_state_not_currently_usable")
    return reasons


def _diversify_by_sector(
    ranked: list[str],
    eligible: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> list[str]:
    first_by_sector: list[str] = []
    remaining: list[str] = []
    seen: set[str] = set()
    for ticker in ranked:
        sector = str((eligible[ticker][1].get("sector_context") or {}).get("sector") or "unknown")
        if sector not in seen:
            seen.add(sector)
            first_by_sector.append(ticker)
        else:
            remaining.append(ticker)
    return first_by_sector + remaining


def select_stock_universe(
    research_dir: Path,
    stock_index: dict[str, Any],
    config: AIConfig,
    *,
    previous_fingerprints: dict[str, str] | None = None,
) -> tuple[UniverseSelection, dict[str, dict[str, Any]]]:
    previous = previous_fingerprints or {}
    excluded: dict[str, list[str]] = {}
    eligible: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for row in stock_index.get("stocks", []):
        ticker = str(row.get("ticker") or "").upper()
        stock = _load_stock(research_dir, row)
        reasons = _exclusion_reasons(stock)
        if reasons:
            excluded[ticker or "UNKNOWN"] = reasons
        elif stock is not None:
            eligible[ticker] = (row, stock)

    ranked = sorted(
        eligible,
        key=lambda ticker: (
            -float((eligible[ticker][1].get("quality") or {}).get("coverage_ratio") or 0),
            -float((eligible[ticker][1].get("liquidity") or {}).get("adv_rub") or 0),
            ticker,
        ),
    )
    mode = config.stock_universe_mode
    if mode == "explicit":
        candidates = [ticker for ticker in config.explicit_tickers if ticker in eligible]
        for ticker in config.explicit_tickers:
            if ticker not in eligible:
                excluded.setdefault(ticker, []).append("explicit_ticker_not_eligible")
    elif mode == "changed":
        candidates = [
            ticker
            for ticker in ranked
            if previous.get(ticker) != str(eligible[ticker][0].get("fingerprint"))
        ]
    else:
        candidates = ranked

    if mode in {"priority", "changed"}:
        candidates = _diversify_by_sector(candidates, eligible)
    selected = candidates if mode == "all" else candidates[: config.max_stock_memos_per_run]
    selection = UniverseSelection(
        mode=mode,
        eligible=sorted(eligible),
        selected=selected,
        excluded=excluded,
        ranking_method=(
            "core eligibility: price + positive ADV + sector + current stock state; "
            "priority order: existing coverage ratio, ADV, ticker with first-pass sector diversification; "
            "no ML candidate portfolio"
        ),
    )
    return selection, {ticker: eligible[ticker][1] for ticker in selected}
