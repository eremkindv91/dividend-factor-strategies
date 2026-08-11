from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Callable


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def percentile_rank(value: Any, peer_values: list[Any]) -> float | None:
    target = finite_number(value)
    peers = [number for item in peer_values if (number := finite_number(item)) is not None]
    if target is None or not peers:
        return None
    lower = sum(number < target for number in peers)
    equal = sum(number == target for number in peers)
    average_rank = lower + (equal + 1) / 2
    return round(100 * average_rank / len(peers), 2)


def _nested(row: dict, *path: str) -> Any:
    current: Any = row
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


FACTOR_SPECS: dict[str, dict[str, Any]] = {
    "momentum": {
        "source_field": "mom_score",
        "extract": lambda row, quality: row.get("mom_score"),
        "direction": "higher_is_stronger",
        "desirability": "higher",
    },
    "quality": {
        "source_field": "quality_score_sector",
        "extract": lambda row, quality: quality.get("quality_score_sector", row.get("quality_score")),
        "direction": "higher_is_stronger",
        "desirability": "higher",
    },
    "valuation": {
        "source_field": "valuation.upside_pct",
        "extract": lambda row, quality: _nested(row, "valuation", "upside_pct"),
        "direction": "higher_is_more_upside",
        "desirability": "higher",
    },
    "dividend": {
        "source_field": "dividend_yield_expected",
        "extract": lambda row, quality: row.get("dividend_yield_expected"),
        "direction": "higher_is_higher_yield_not_necessarily_better",
        "desirability": None,
    },
    "volatility": {
        "source_field": "vol_ann",
        "extract": lambda row, quality: row.get("vol_ann"),
        "direction": "higher_is_more_volatile",
        "desirability": None,
    },
    "liquidity": {
        "source_field": "adv",
        "extract": lambda row, quality: row.get("adv"),
        "direction": "higher_is_more_liquid",
        "desirability": "higher",
    },
}


def _latest_return(returns_payload: dict, ticker: str) -> tuple[str | None, float | None]:
    meta = returns_payload.get("meta", {}) if isinstance(returns_payload, dict) else {}
    months = meta.get("months") or []
    rows = returns_payload.get("data", {}) if isinstance(returns_payload, dict) else {}
    values = rows.get(ticker) if isinstance(rows, dict) else None
    if not months or not isinstance(values, list) or len(values) < len(months):
        return None, None
    return str(months[-1]), finite_number(values[len(months) - 1])


def build_sector_context(
    tickers: list[dict],
    returns_payload: dict,
    quality_rows: list[dict],
    pack_for_ticker: Callable[[str, str], str | None],
    pack_models: dict[str, dict],
) -> tuple[list[dict], dict[str, dict]]:
    quality_by_ticker = {str(row.get("ticker")): row for row in quality_rows if row.get("ticker")}
    sectors: dict[str, list[dict]] = defaultdict(list)
    for row in tickers:
        ticker = str(row.get("ticker") or "").strip()
        sector = str(row.get("sector") or "").strip()
        if ticker and sector:
            sectors[sector].append(row)

    factor_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sector, rows in sectors.items():
        for factor, spec in FACTOR_SPECS.items():
            for row in rows:
                value = finite_number(spec["extract"](row, quality_by_ticker.get(str(row.get("ticker")), {})))
                if value is not None:
                    factor_values[sector][factor].append(value)

    positions: dict[str, dict] = {}
    sector_rows: list[dict] = []
    benchmark_period, market_return = _latest_return(returns_payload, "MCFTR")
    if market_return is None:
        benchmark_period, market_return = _latest_return(returns_payload, "IMOEX")

    for sector in sorted(sectors):
        rows = sectors[sector]
        monthly: list[float] = []
        periods: set[str] = set()
        packs: set[str] = set()
        for row in rows:
            ticker = str(row["ticker"])
            period, stock_return = _latest_return(returns_payload, ticker)
            if period:
                periods.add(period)
            if stock_return is not None:
                monthly.append(stock_return)
            pack = pack_for_ticker(ticker, sector)
            if pack:
                packs.add(pack)

        period = benchmark_period if benchmark_period in periods else (max(periods) if periods else None)
        sector_return = round(statistics.fmean(monthly), 8) if monthly else None
        breadth = round(sum(value > 0 for value in monthly) / len(monthly), 8) if monthly else None
        dispersion = round(statistics.pstdev(monthly), 8) if len(monthly) > 1 else None
        factor_medians = {
            factor: round(statistics.median(values), 8) if values else None
            for factor, values in factor_values[sector].items()
        }
        pack_id = next(iter(packs)) if len(packs) == 1 else None
        model = dict(pack_models.get(pack_id, {})) if pack_id else {
            "pack_id": None,
            "qc_passed": False,
            "promotion_status": "NOT_APPLICABLE",
            "approved_feature_columns": [],
            "tradable_signal": False,
        }
        if model.get("promotion_status") != "APPROVED":
            model["tradable_signal"] = False
        sector_rows.append(
            {
                "sector": sector,
                "constituents": sorted(str(row["ticker"]) for row in rows),
                "constituent_count": len(rows),
                "descriptive": {
                    "return_period": period,
                    "latest_monthly_return": sector_return,
                    "market_latest_monthly_return": market_return,
                    "relative_strength_vs_market": (
                        round(sector_return - market_return, 8)
                        if sector_return is not None and market_return is not None
                        else None
                    ),
                    "breadth_positive_return": breadth,
                    "dispersion": dispersion,
                    "observations": len(monthly),
                    "factor_medians": factor_medians,
                },
                "model": model,
                "data_quality": {
                    "missing_fields": [
                        key
                        for key, value in {
                            "latest_monthly_return": sector_return,
                            "market_latest_monthly_return": market_return,
                            "relative_strength_vs_market": (
                                sector_return - market_return
                                if sector_return is not None and market_return is not None
                                else None
                            ),
                            "breadth_positive_return": breadth,
                            "dispersion": dispersion,
                        }.items()
                        if value is None
                    ],
                    "warnings": (
                        ([] if monthly else ["no_common_latest_monthly_returns"])
                        + (["market_benchmark_monthly_return_unavailable"] if market_return is None else [])
                    ),
                },
            }
        )

    for metric in ("latest_monthly_return", "breadth_positive_return", "dispersion"):
        peer_values = [row["descriptive"].get(metric) for row in sector_rows]
        for row in sector_rows:
            row["descriptive"].setdefault("cross_sector_percentiles", {})[metric] = percentile_rank(
                row["descriptive"].get(metric), peer_values
            )

    sector_by_name = {row["sector"]: row for row in sector_rows}
    for sector, rows in sectors.items():
        summary = sector_by_name[sector]["descriptive"]
        for row in rows:
            ticker = str(row["ticker"])
            quality = quality_by_ticker.get(ticker, {})
            factor_position: dict[str, dict] = {}
            for factor, spec in FACTOR_SPECS.items():
                raw = finite_number(spec["extract"](row, quality))
                raw_pct = percentile_rank(raw, factor_values[sector][factor])
                desirability = raw_pct if spec["desirability"] == "higher" else None
                factor_position[f"{factor}_percentile"] = {
                    "raw_value": raw,
                    "raw_percentile": raw_pct,
                    "desirability_percentile": desirability,
                    "n_peers": len(factor_values[sector][factor]),
                    "direction": spec["direction"],
                    "source_field": spec["source_field"],
                }
            period, stock_return = _latest_return(returns_payload, ticker)
            sector_return = summary.get("latest_monthly_return")
            positions[ticker] = {
                "ticker": ticker,
                "sector": sector,
                "sector_position": factor_position,
                "relative_performance": {
                    "period": period,
                    "frequency": "monthly",
                    "stock_return": stock_return,
                    "sector_return": sector_return,
                    "market_return": market_return,
                    "stock_minus_sector": (
                        round(stock_return - sector_return, 8)
                        if stock_return is not None and sector_return is not None
                        else None
                    ),
                    "sector_minus_market": (
                        round(sector_return - market_return, 8)
                        if sector_return is not None and market_return is not None
                        else None
                    ),
                    "twenty_day_return_available": False,
                },
            }
    return sector_rows, positions
