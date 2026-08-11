from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml_strategy.sector_features.mapping import load_sector_mapping, pack_for_security

from .fingerprints import aggregate_fingerprint, fingerprint
from .eligibility import evaluate_research_eligibility
from .freshness import age_days, asof_date, latest_timestamp, parse_timestamp, point_in_time_quality
from .schemas import RESEARCH_SCHEMA_VERSION
from .sector_context import build_sector_context, finite_number
from .validators import validate_research_bundle

INPUT_DATE_PATHS: dict[str, tuple[str, ...]] = {
    "data.json": ("meta.price_asof", "meta.обновлено"),
    "returns.json": ("meta.asof",),
    "marketsaw.json": ("data_last", "generated_at"),
    "marketsaw_imoex.json": ("data_last", "generated_at"),
    "market_history.json": ("data_asof", "generated_at"),
    "macro_cbr.json": ("key_rate.asof", "generated_at"),
    "futures_positions.json": ("meta.as_of", "meta.generated_at"),
    "market_positioning_commentary.json": ("meta.as_of", "meta.generated_at"),
    "quality.json": ("meta.as_of_date", "meta.calculated_at"),
    "site_financials.json": ("meta.generated_at",),
    "site_coverage.json": ("meta.generated_at",),
    "site_status.json": ("generated_at",),
    "news.json": ("generated_at", "date"),
    "cbr/valuation.json": ("meta.moex_asof", "meta.cbr_asof", "meta.generated_at"),
    "cbr/valuation_v2.json": ("meta.generated_at",),
    "cbr/credit_portfolio.json": ("meta.as_of", "meta.generated_at"),
    "cbr/data_quality.json": ("last_report_date", "generated_at"),
    "bonds/screener.json": ("meta.data_date", "meta.updated"),
    "bonds/chart_data.json": ("updated",),
    "ml_strategy/latest.json": ("data_as_of", "generated_at"),
    "ml_strategy/sector_features/latest_quality.json": ("generated_at",),
    "ml_strategy/sector_features/latest_registry.json": ("generated_at",),
}

COMPONENT_MAX_AGE_DAYS = {
    "market": 7,
    "fundamentals": 90,
    "sectors": 45,
    "stocks": 7,
    "ml": 14,
    "banks": 60,
    "bonds": 10,
    "news": 4,
}


@dataclass(frozen=True)
class LoadedInput:
    data: Any
    logical_path: str
    input_role: str
    selected_asof: str | None


class InputCatalog:
    def __init__(self, site_dir: Path, fallback_roots: list[Path] | None = None):
        self.roots = [(site_dir, "current")]
        self.roots.extend((path, f"fallback_{index + 1}") for index, path in enumerate(fallback_roots or []))
        self._cache: dict[str, LoadedInput | None] = {}

    def load(self, relative_path: str) -> LoadedInput | None:
        if relative_path in self._cache:
            return self._cache[relative_path]
        candidates: list[tuple[datetime | None, int, LoadedInput]] = []
        for priority, (root, role) in enumerate(self.roots):
            path = root / relative_path
            if not path.exists() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            stamp = latest_timestamp(payload, INPUT_DATE_PATHS.get(relative_path, ("generated_at",)))
            candidates.append(
                (
                    stamp,
                    -priority,
                    LoadedInput(
                        data=payload,
                        logical_path=f"site/{relative_path}",
                        input_role=role,
                        selected_asof=stamp.isoformat() if stamp else None,
                    ),
                )
            )
        if not candidates:
            self._cache[relative_path] = None
            return None
        candidates.sort(key=lambda row: (row[0] or datetime.min.replace(tzinfo=timezone.utc), row[1]), reverse=True)
        self._cache[relative_path] = candidates[0][2]
        return self._cache[relative_path]


def _data(loaded: LoadedInput | None, default: Any) -> Any:
    return loaded.data if loaded is not None else default


def _source_refs(*loaded: LoadedInput | None) -> list[dict]:
    refs = []
    seen: set[str] = set()
    for item in loaded:
        if item is None or item.logical_path in seen:
            continue
        seen.add(item.logical_path)
        refs.append(
            {
                "source_file": item.logical_path,
                "input_role": item.input_role,
                "selected_asof": asof_date(item.selected_asof),
            }
        )
    return refs


def _source_files(payload: dict) -> list[str]:
    return sorted(
        {
            str(row["source_file"])
            for row in payload.get("sources", [])
            if isinstance(row, dict) and row.get("source_file")
        }
    )


def _clean(value: Any, warnings: list[str], path: str = "$") -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item, warnings, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item, warnings, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        warnings.append(f"non_finite_replaced_with_null:{path}")
        return None
    return value


def _cbr_public_refs(value: Any) -> Any:
    """Turn CBR-relative download paths into public URLs before publication."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "source_file" and isinstance(item, str) and item.startswith("/"):
                normalized[key] = f"https://cbr.ru{item}"
            else:
                normalized[key] = _cbr_public_refs(item)
        return normalized
    if isinstance(value, list):
        return [_cbr_public_refs(item) for item in value]
    return value


def _compact_inflation(inflation: dict) -> dict:
    """Keep current CBR inflation state without copying historical series."""
    expectations = inflation.get("expectations") or {}
    monthly = inflation.get("mom") or {}
    compact = {
        key: inflation.get(key)
        for key in (
            "target",
            "latest_month",
            "latest_yoy",
            "above_target",
            "expectations_error",
            "mom_error",
        )
        if key in inflation
    }
    if expectations:
        compact["expectations"] = {
            key: expectations.get(key)
            for key in ("period", "latest_expected", "latest_perceived", "note", "source_file")
            if key in expectations
        }
    if monthly:
        compact["mom"] = {
            key: monthly.get(key)
            for key in ("latest", "note", "source_file")
            if key in monthly
        }
    return _cbr_public_refs(compact)


def _quality(
    *,
    asof: str | None,
    now: datetime,
    max_age_days: int,
    missing_fields: list[str] | None = None,
    warnings: list[str] | None = None,
    source_quality: str = "mixed_existing_sources",
    pit_quality: str = "unknown",
) -> dict:
    age = age_days(asof, now)
    return {
        "fresh": bool(age is not None and -0.01 <= age <= max_age_days),
        "age_days": age,
        "missing_fields": sorted(set(missing_fields or [])),
        "warnings": sorted(set(warnings or [])),
        "source_quality": source_quality,
        "point_in_time_quality": pit_quality,
    }


def _history_summaries(history: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in history.get("instruments", []) if isinstance(history, dict) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        summary = row.get("summary") or {}
        out[str(row["id"])] = {
            "name": row.get("name"),
            "asof": row.get("data_last"),
            "last": finite_number(summary.get("last")),
            "change_pct": finite_number(summary.get("change_pct")),
            "sma20": finite_number(summary.get("sma20")),
            "sma50": finite_number(summary.get("sma50")),
            "sma200": finite_number(summary.get("sma200")),
            "rsi14": finite_number(summary.get("rsi14")),
            "volatility20_annualized_pct": finite_number(summary.get("volatility20_annualized_pct")),
            "low20": finite_number(summary.get("low20")),
            "high20": finite_number(summary.get("high20")),
            "trend": summary.get("trend"),
        }
    return out


def _latest_cross_section(returns_payload: dict, tickers: list[dict]) -> tuple[str | None, list[float]]:
    months = (returns_payload.get("meta") or {}).get("months") or []
    data = returns_payload.get("data") or {}
    if not months or not isinstance(data, dict):
        return None, []
    index = len(months) - 1
    values: list[float] = []
    for row in tickers:
        series = data.get(row.get("ticker"))
        if isinstance(series, list) and len(series) > index:
            value = finite_number(series[index])
            if value is not None:
                values.append(value)
    return str(months[index]), values


def _market_snapshot(catalog: InputCatalog, tickers: list[dict], now: datetime) -> dict:
    data_in = catalog.load("data.json")
    returns_in = catalog.load("returns.json")
    history_in = catalog.load("market_history.json")
    saw_in = catalog.load("marketsaw.json")
    saw_imoex_in = catalog.load("marketsaw_imoex.json")
    macro_in = catalog.load("macro_cbr.json")
    futures_in = catalog.load("futures_positions.json")
    positioning_in = catalog.load("market_positioning_commentary.json")
    data = _data(data_in, {})
    returns = _data(returns_in, {})
    history = _history_summaries(_data(history_in, {}))
    saw = _data(saw_in, {})
    saw_imoex = _data(saw_imoex_in, {})
    macro = _data(macro_in, {})
    futures = _data(futures_in, {})
    positioning = _data(positioning_in, {})
    asof = asof_date((data.get("meta") or {}).get("price_asof"))
    period, cross_section = _latest_cross_section(returns, tickers)
    missing: list[str] = []
    warnings: list[str] = []
    if not history:
        missing.append("market_history")
    if not saw.get("current_phase"):
        missing.append("market_phase_mcftr")
    if not macro:
        missing.append("rates")
    if not futures:
        missing.append("positioning")
    source_dates = {
        "prices": asof,
        "returns": asof_date((returns.get("meta") or {}).get("asof")),
        "market_history": max((row.get("asof") for row in history.values() if row.get("asof")), default=None),
        "market_phase_mcftr": asof_date(saw.get("data_last")),
        "market_phase_imoex": asof_date(saw_imoex.get("data_last")),
        "rates": asof_date((macro.get("key_rate") or {}).get("asof")),
        "positioning": asof_date((futures.get("meta") or {}).get("as_of")),
    }
    dated = sorted(set(value for value in source_dates.values() if value))
    if len(dated) > 1:
        warnings.append("market_inputs_have_different_asof_dates")
    cross_mean = sum(cross_section) / len(cross_section) if cross_section else None
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "market",
        "asof": asof,
        "generated_at": now.isoformat(),
        "source_dates": source_dates,
        "sources": _source_refs(
            data_in, returns_in, history_in, saw_in, saw_imoex_in, macro_in, futures_in, positioning_in
        ),
        "returns": {
            key: {"asof": row.get("asof"), "last": row.get("last"), "change_pct": row.get("change_pct")}
            for key, row in history.items()
            if key in {"MCFTR", "IMOEX", "RTSI"}
        },
        "trend": {
            key: {
                "trend": row.get("trend"),
                "sma20": row.get("sma20"),
                "sma50": row.get("sma50"),
                "sma200": row.get("sma200"),
                "rsi14": row.get("rsi14"),
                "low20": row.get("low20"),
                "high20": row.get("high20"),
            }
            for key, row in history.items()
            if key in {"MCFTR", "IMOEX", "RTSI"}
        },
        "breadth": {
            "period": period,
            "frequency": "monthly",
            "positive_share": round(sum(value > 0 for value in cross_section) / len(cross_section), 8)
            if cross_section
            else None,
            "mean_return": round(cross_mean, 8) if cross_mean is not None else None,
            "observations": len(cross_section),
        },
        "volatility": {
            key: row.get("volatility20_annualized_pct")
            for key, row in history.items()
            if key in {"MCFTR", "IMOEX", "RTSI"}
        },
        "market_phase": {
            "mcftr": saw.get("current_phase"),
            "imoex": saw_imoex.get("current_state"),
        },
        "positioning": {
            "asof": (futures.get("meta") or {}).get("as_of"),
            "indices": {
                key: {
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "summary": row.get("summary"),
                }
                for key, row in (futures.get("indices") or {}).items()
                if isinstance(row, dict)
            },
            "existing_interpretation": {
                "asof": (positioning.get("meta") or {}).get("as_of"),
                "llm_used": (positioning.get("meta") or {}).get("llm_used"),
                "fallback_used": (positioning.get("meta") or {}).get("fallback_used"),
            }
            if positioning
            else None,
        },
        "rates": {
            "key_rate": {
                key: value for key, value in (macro.get("key_rate") or {}).items() if key != "series"
            },
            "inflation": _compact_inflation(macro.get("inflation") or {}),
            "real_key_rate": macro.get("real_key_rate"),
        }
        if macro
        else {"available": False},
        "fx": {
            key: history[key]
            for key in ("USD000UTSTOM", "CNYRUB_TOM")
            if key in history
        },
    }
    payload["data_quality"] = _quality(
        asof=asof,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["market"],
        missing_fields=missing,
        warnings=warnings,
        source_quality="official_and_existing_derived",
        pit_quality="verified" if asof else "unknown",
    )
    return payload


def _compact_fundamentals(financials: dict, quality_rows: list[dict], now: datetime, sources: list[dict]) -> dict:
    quality_by_ticker = {str(row.get("ticker")): row for row in quality_rows if row.get("ticker")}
    companies: dict[str, dict] = {}
    for ticker, groups in sorted((financials.get("fundamentals") or {}).items()):
        latest: dict[str, dict] = {}
        source_statuses: set[str] = set()
        warnings: list[str] = []
        if not isinstance(groups, dict):
            continue
        for fields in groups.values():
            if not isinstance(fields, list):
                continue
            for field in fields:
                values = field.get("values") or []
                valid = [
                    row
                    for row in values
                    if isinstance(row, dict) and finite_number(row.get("value")) is not None
                ]
                if not valid:
                    continue
                row = max(valid, key=lambda item: int(item.get("year") or 0))
                latest[str(field.get("field"))] = {
                    "year": row.get("year"),
                    "value": finite_number(row.get("value")),
                    "unit": field.get("unit"),
                    "source_name": field.get("source_name"),
                    "source_status": field.get("source_status"),
                    "source_url": field.get("source_url"),
                    "quality_status": row.get("quality_status"),
                    "needs_manual_review": bool(row.get("needs_manual_review")),
                }
                if field.get("source_status"):
                    source_statuses.add(str(field["source_status"]))
                if row.get("needs_manual_review"):
                    warnings.append(f"manual_review:{field.get('field')}")
        quality = quality_by_ticker.get(str(ticker), {})
        publication = quality.get("publication_date")
        pit = point_in_time_quality(publication, bool(latest))
        if pit == "partial":
            warnings.append("publication_timestamp_unknown")
        companies[str(ticker)] = {
            "ticker": str(ticker),
            "latest": latest,
            "report_period_end": quality.get("report_period_end"),
            "publication_date": publication,
            "publication_timestamp_available": publication is not None,
            "point_in_time_quality": pit,
            "source_statuses": sorted(source_statuses),
            "warnings": sorted(set(warnings)),
        }
    generated = (financials.get("meta") or {}).get("generated_at")
    partial = sum(row["point_in_time_quality"] == "partial" for row in companies.values())
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "fundamentals",
        "asof": None,
        "generated_at": now.isoformat(),
        "snapshot_generated_at": generated,
        "sources": sources,
        "companies": companies,
        "summary": {
            "companies": len(companies),
            "verified_point_in_time": sum(row["point_in_time_quality"] == "verified" for row in companies.values()),
            "partial_point_in_time": partial,
            "unknown_point_in_time": sum(row["point_in_time_quality"] == "unknown" for row in companies.values()),
        },
    }
    payload["data_quality"] = _quality(
        asof=None,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["fundamentals"],
        missing_fields=["component_source_asof"] if companies else ["companies"],
        warnings=["publication_timestamps_partial"] if partial else [],
        source_quality="mixed_verified_and_fallback",
        pit_quality="partial" if partial else ("verified" if companies else "unknown"),
    )
    return payload


def _pack_models(quality: dict) -> dict[str, dict]:
    approved = set(quality.get("approved_feature_columns") or [])
    out: dict[str, dict] = {}
    for row in quality.get("packs", []) if isinstance(quality, dict) else []:
        if not isinstance(row, dict) or not row.get("pack_id"):
            continue
        status = str(row.get("status") or "RESEARCH_ONLY")
        used = bool(row.get("used_in_production")) and status == "APPROVED"
        out[str(row["pack_id"])] = {
            "pack_id": row.get("pack_id"),
            "feature_role": row.get("feature_role"),
            "model_prediction": None,
            "qc_passed": used,
            "promotion_status": status,
            "approved_feature_columns": [name for name in row.get("features", []) if name in approved],
            "tradable_signal": used,
            "used_in_production": used,
            "latest_available_at": row.get("latest_available_at"),
            "reason": row.get("reason"),
            "ablation_reason": row.get("ablation_reason"),
        }
    return out


def _ml_snapshot(catalog: InputCatalog, now: datetime) -> tuple[dict, dict[str, dict]]:
    latest_in = catalog.load("ml_strategy/latest.json")
    quality_in = catalog.load("ml_strategy/sector_features/latest_quality.json")
    registry_in = catalog.load("ml_strategy/sector_features/latest_registry.json")
    latest = _data(latest_in, {})
    sector_quality = _data(quality_in, {})
    pack_models = _pack_models(sector_quality)
    asof = asof_date(latest.get("data_as_of"))
    limitations = [str(item) for item in latest.get("limitations", [])]
    survivorship = "partial" if any("survivorship" in item.lower() for item in limitations) else "unknown"
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "ml",
        "asof": asof,
        "generated_at": now.isoformat(),
        "sources": _source_refs(latest_in, quality_in, registry_in),
        "model": latest.get("model") or {},
        "model_status": latest.get("model_status"),
        "data_status": latest.get("data_status"),
        "signal_status": latest.get("signal_status"),
        "action_status": latest.get("action_status"),
        "validation": latest.get("diagnostics") or {},
        "sector_features": {
            "status": sector_quality.get("status"),
            "point_in_time_policy": sector_quality.get("point_in_time_policy"),
            "approved_feature_columns": sector_quality.get("approved_feature_columns") or [],
            "packs": list(pack_models.values()),
        },
        "survivorship_status": survivorship,
        "limitations": limitations,
        "portfolio_payload_excluded": True,
    }
    warnings = []
    if latest.get("model_status") != "production":
        warnings.append("ml_not_in_production")
    if any(row.get("promotion_status") == "RESEARCH_ONLY" for row in pack_models.values()):
        warnings.append("sector_packs_research_only")
    payload["data_quality"] = _quality(
        asof=asof,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["ml"],
        missing_fields=[] if latest else ["ml_snapshot"],
        warnings=warnings,
        source_quality="validated_existing_ml_artifact" if latest else "unavailable",
        pit_quality="verified" if (sector_quality.get("point_in_time_policy") == "available_at <= prediction_timestamp") else "unknown",
    )
    return payload, pack_models


def _bank_snapshot(catalog: InputCatalog, now: datetime) -> dict:
    valuation_in = catalog.load("cbr/valuation.json")
    residual_in = catalog.load("cbr/valuation_v2.json")
    credit_in = catalog.load("cbr/credit_portfolio.json")
    quality_in = catalog.load("cbr/data_quality.json")
    valuation = _data(valuation_in, {})
    residual = _data(residual_in, {})
    credit = _data(credit_in, {})
    quality = _data(quality_in, {})
    residual_by = {str(row.get("ticker")): row for row in residual.get("banks", []) if row.get("ticker")}
    credit_by = {str(row.get("ticker")): row for row in credit.get("banks", []) if row.get("ticker")}
    banks = []
    for row in valuation.get("banks", []) if isinstance(valuation, dict) else []:
        ticker = str(row.get("ticker") or "")
        ri = residual_by.get(ticker, {})
        loans = credit_by.get(ticker, {})
        banks.append(
            {
                "ticker": ticker,
                "bank": row.get("name"),
                "asof": row.get("vintages") or {},
                "roe_pct": finite_number(row.get("roe")),
                "cost_of_equity": finite_number((ri.get("cost_of_equity") or {}).get("cost_of_equity")),
                "p_bv_regulatory": finite_number(row.get("p_bv")),
                "residual_income": {
                    "status": ri.get("status"),
                    "fair_pbv": finite_number((ri.get("valuation") or {}).get("fair_pbv")),
                    "fair_price_per_share": (ri.get("valuation") or {}).get("fair_price_per_share"),
                    "equity_source": ri.get("equity_source"),
                },
                "capital": {
                    "capital_rub": finite_number(row.get("capital_rub")),
                    "n1_0_pct": finite_number(row.get("n10")),
                    "n1_0_headroom_pp": finite_number(row.get("n10_headroom")),
                },
                "profit_ttm_rub": finite_number(row.get("profit_ttm_rub")),
                "loan_portfolio": loans.get("latest"),
                "dividend_capacity": {
                    "score": finite_number(row.get("dividend_capacity_score")),
                    "dividend_yield_pct": finite_number(row.get("div_yield")),
                },
                "warnings": sorted(set((row.get("warnings") or []) + (ri.get("warnings") or []))),
                "data_quality": {
                    "score": finite_number(row.get("data_quality_score")),
                    "residual_income_quality": ri.get("quality"),
                },
            }
        )
    meta = valuation.get("meta") or {}
    asof = asof_date(meta.get("oldest_vintage") or meta.get("cbr_asof"))
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "banks",
        "asof": asof,
        "generated_at": now.isoformat(),
        "sources": _source_refs(valuation_in, residual_in, credit_in, quality_in),
        "banks": banks,
        "source_status": meta.get("sources") or {},
    }
    payload["data_quality"] = _quality(
        asof=asof,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["banks"],
        missing_fields=[] if banks else ["banks"],
        warnings=(quality.get("validation_issues") or []) + (["cbr_pipeline_errors"] if quality.get("errors") else []),
        source_quality="cbr_official_with_existing_model_outputs" if banks else "unavailable",
        pit_quality="partial" if banks else "unknown",
    )
    return payload


def _bond_snapshot(catalog: InputCatalog, now: datetime) -> dict:
    screener_in = catalog.load("bonds/screener.json")
    chart_in = catalog.load("bonds/chart_data.json")
    screener = _data(screener_in, {})
    chart = _data(chart_in, {})
    bonds = []
    fields = (
        "secid", "isin", "name", "rating", "rating_group", "rating_source", "rating_agency",
        "rating_date", "rating_source_url", "currency", "price_market", "ytm_market", "ytm_net",
        "price_fair", "ytm_fair", "deviation", "duration_years", "coupon_pct", "freq", "maturity",
        "valtoday", "lot_value", "max_rub",
    )
    required = ("secid", "price_market", "ytm_market", "duration_years", "maturity")
    for row in screener.get("bonds", []) if isinstance(screener, dict) else []:
        item = {field: row.get(field) for field in fields}
        item["data_quality"] = {
            "missing_fields": [field for field in required if row.get(field) in (None, "")],
            "warnings": ["rating_missing"] if not row.get("rating") else [],
        }
        bonds.append(item)
    meta = screener.get("meta") or {}
    asof = asof_date(meta.get("data_date") or meta.get("updated"))
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "bonds",
        "asof": asof,
        "generated_at": now.isoformat(),
        "sources": _source_refs(screener_in, chart_in),
        "curve_context": {
            "ofz_curve": chart.get("ofz_curve") or [],
            "spread": chart.get("spread"),
        },
        "bonds": bonds,
    }
    payload["data_quality"] = _quality(
        asof=asof,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["bonds"],
        missing_fields=[] if bonds else ["bonds"],
        warnings=[],
        source_quality="moex_and_official_ratings" if bonds else "unavailable",
        pit_quality="verified" if asof else "unknown",
    )
    return payload


def _news_snapshot(catalog: InputCatalog, now: datetime) -> dict:
    news_in = catalog.load("news.json")
    news = _data(news_in, {})
    items = []
    for bucket in ("overnight", "yesterday"):
        for row in news.get(bucket, []) if isinstance(news, dict) else []:
            if not isinstance(row, dict):
                continue
            item = {
                "id": row.get("id"),
                "timestamp": row.get("published_at"),
                "title": row.get("headline"),
                "context": row.get("context"),
                "category": row.get("category"),
                "materiality": row.get("importance"),
                "investment_relevant": row.get("investment_relevant"),
                "sources": row.get("sources") or [],
                "bucket": bucket,
            }
            if row.get("ticker"):
                item["tickers"] = [str(row["ticker"])]
            elif isinstance(row.get("tickers"), list):
                item["tickers"] = [str(value) for value in row["tickers"]]
            if isinstance(row.get("sectors"), list):
                item["sectors"] = [str(value) for value in row["sectors"]]
            items.append(item)
    agenda = [
        {
            key: row.get(key)
            for key in ("time", "event", "ticker", "type", "importance")
        }
        for row in news.get("today_agenda", [])
        if isinstance(row, dict)
    ] if isinstance(news, dict) else []
    asof = asof_date(news.get("generated_at") or news.get("date"))
    payload = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "news",
        "asof": asof,
        "generated_at": now.isoformat(),
        "sources": _source_refs(news_in),
        "items": items,
        "agenda": agenda,
        "existing_external_backdrop": news.get("external_backdrop"),
        "classification_policy": "existing_fields_only_no_phase2_llm_classification",
    }
    payload["data_quality"] = _quality(
        asof=asof,
        now=now,
        max_age_days=COMPONENT_MAX_AGE_DAYS["news"],
        missing_fields=[] if items or agenda else ["news_items"],
        warnings=[] if news else ["news_snapshot_unavailable"],
        source_quality="existing_news_pipeline" if news else "unavailable",
        pit_quality="verified" if asof else "unknown",
    )
    return payload


def _compact_quality(row: dict) -> dict:
    provenance = row.get("provenance") or {}
    return {
        "model": row.get("quality_model"),
        "score_absolute": finite_number(row.get("quality_score_absolute")),
        "score_sector": finite_number(row.get("quality_score_sector")),
        "rank_pct": finite_number(row.get("quality_rank_pct")),
        "sector_rank_pct": finite_number(row.get("sector_rank_pct")),
        "coverage_ratio": finite_number(row.get("coverage_ratio")),
        "confidence": row.get("confidence"),
        "status": row.get("status"),
        "eligible": row.get("eligible"),
        "warnings": row.get("warnings") or [],
        "methodology_version": row.get("methodology_version"),
        "publication_date": row.get("publication_date"),
        "report_period_end": row.get("report_period_end"),
        "provenance": {
            "source_type": provenance.get("source_type"),
            "source_url": provenance.get("source_url"),
            "report_date": provenance.get("report_date"),
        },
    }


def _stock_snapshots(
    *,
    data: dict,
    returns: dict,
    quality: dict,
    fundamentals: dict,
    sector_positions: dict[str, dict],
    sector_rows: list[dict],
    pack_for_ticker_fn,
    pack_models: dict[str, dict],
    market: dict,
    ml: dict,
    futures: dict,
    news: dict,
    now: datetime,
    sources: list[dict],
) -> dict:
    quality_by = {str(row.get("ticker")): row for row in quality.get("rows", []) if row.get("ticker")}
    fundamental_by = fundamentals.get("companies") or {}
    sector_by = {row["sector"]: row for row in sector_rows}
    news_by: dict[str, list[dict]] = {}
    for item in news.get("items", []):
        for ticker in item.get("tickers", []) or []:
            news_by.setdefault(str(ticker), []).append(
                {key: item.get(key) for key in ("id", "timestamp", "title", "category", "materiality", "sources")}
            )
    futures_by = (futures.get("tickers") or {}) if isinstance(futures, dict) else {}
    phase = (market.get("market_phase") or {}).get("mcftr") or {}
    market_context = {
        "asof": market.get("asof"),
        "mcftr": (market.get("returns") or {}).get("MCFTR"),
        "imoex": (market.get("returns") or {}).get("IMOEX"),
        "market_phase": {
            key: phase.get(key)
            for key in ("direction", "label", "risk_level", "move_pct", "current_date")
        },
        "key_rate": ((market.get("rates") or {}).get("key_rate") or {}).get("current"),
    }
    stocks: dict[str, dict] = {}
    price_asof = (data.get("meta") or {}).get("price_asof")
    for row in data.get("tickers", []) if isinstance(data, dict) else []:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        sector = str(row.get("sector") or "")
        quality_row = quality_by.get(ticker, {})
        fundamental = fundamental_by.get(ticker, {})
        position = sector_positions.get(ticker, {})
        sector_summary = sector_by.get(sector, {})
        pack_id = pack_for_ticker_fn(ticker, sector)
        sector_model = dict(pack_models.get(pack_id, {})) if pack_id else {
            "pack_id": None,
            "qc_passed": False,
            "promotion_status": "NOT_APPLICABLE",
            "approved_feature_columns": [],
            "tradable_signal": False,
        }
        warnings = list(row.get("flags") or [])
        warnings.extend(fundamental.get("warnings") or [])
        if (position.get("relative_performance") or {}).get("market_return") is None:
            warnings.append("market_benchmark_monthly_return_unavailable")
        if row.get("price_fresh") is False:
            warnings.append("price_not_fresh")
        missing = [
            key
            for key, value in {
                "price": row.get("price"),
                "sector": sector or None,
                "momentum": row.get("mom_score"),
                "volatility": row.get("vol_ann"),
                "liquidity": row.get("adv"),
                "fundamentals": fundamental.get("latest"),
                "market_return": (position.get("relative_performance") or {}).get("market_return"),
            }.items()
            if value in (None, "", {})
        ]
        stocks[ticker] = {
            "ticker": ticker,
            "name": row.get("name"),
            "asof": asof_date(price_asof),
            "market_context": market_context,
            "sector_context": {
                "sector": sector,
                "descriptive": {
                    key: (sector_summary.get("descriptive") or {}).get(key)
                    for key in (
                        "return_period",
                        "latest_monthly_return",
                        "market_latest_monthly_return",
                        "relative_strength_vs_market",
                        "breadth_positive_return",
                        "dispersion",
                    )
                },
                "model": sector_model,
            },
            "sector_position": position.get("sector_position") or {},
            "price": {
                "value": finite_number(row.get("price")),
                "asof": asof_date(price_asof),
                "field": row.get("price_field"),
                "fresh": row.get("price_fresh"),
            },
            "returns": position.get("relative_performance") or {},
            "momentum": {"score": finite_number(row.get("mom_score")), "source_field": "mom_score"},
            "volatility": {"annualized": finite_number(row.get("vol_ann")), "source_field": "vol_ann"},
            "liquidity": {
                "adv_rub": finite_number(row.get("adv")),
                "market_cap_mln_rub": finite_number(row.get("mcap")),
                "lot_size": finite_number(row.get("lot_size")),
            },
            "fundamentals": fundamental.get("latest") or {},
            "valuation": {
                key: (row.get("valuation") or {}).get(key)
                for key in ("method", "vclass", "fair_price", "upside_pct", "note", "alert", "assumptions")
            },
            "quality": _compact_quality(quality_row),
            "dividends": {
                "cut_risk": finite_number(row.get("cut_risk")),
                "stability_score": finite_number(row.get("stability_score")),
                "forecast": finite_number(row.get("dividend_forecast")),
                "forecast_low": finite_number(row.get("dividend_forecast_lo")),
                "forecast_high": finite_number(row.get("dividend_forecast_hi")),
                "expected_yield_pct": finite_number(row.get("dividend_yield_expected")),
                "yield_if_paid_pct": finite_number(row.get("dividend_yield_if_paid")),
                "payout_pct": finite_number(row.get("payout")),
                "streak_years": finite_number(row.get("div_streak")),
            },
            "factor_scores": {
                "sector_percentiles_existing": row.get("sector_percentiles"),
                "quality_rank_pct": finite_number(row.get("quality_rank_pct")),
                "verdict": row.get("verdict"),
                "dividend_model_top_features": row.get("shap_top5") or [],
            },
            "ml": {
                "dividend_model": {
                    "forecast_asof": (data.get("meta") or {}).get("forecast_asof"),
                    "status": row.get("status"),
                    "ranking_status": row.get("ranking_status"),
                    "ranking_eligible": row.get("ranking_eligible"),
                    "review_reasons": row.get("ranking_review_reasons") or [],
                },
                "stock_model": {
                    "model_status": ml.get("model_status"),
                    "signal_status": ml.get("signal_status"),
                    "per_stock_prediction_available": False,
                    "tradable_signal": False,
                },
                "sector_model": sector_model,
                "validation": {
                    key: (ml.get("validation") or {}).get(key, {}).get("status")
                    for key in ("predictive_gate", "portfolio_gate")
                    if isinstance((ml.get("validation") or {}).get(key), dict)
                },
            },
            "positioning": {
                "futures": (futures_by.get(ticker) or {}).get("summary")
                if isinstance(futures_by.get(ticker), dict)
                else None
            },
            "news_refs": news_by.get(ticker, []),
            "data_quality": {
                **_quality(
                    asof=price_asof,
                    now=now,
                    max_age_days=COMPONENT_MAX_AGE_DAYS["stocks"],
                    missing_fields=missing,
                    warnings=warnings,
                    source_quality=str(row.get("status") or "mixed_existing_sources"),
                    pit_quality=fundamental.get("point_in_time_quality", "unknown"),
                ),
                "publication_timestamp_available": fundamental.get("publication_timestamp_available", False),
            },
        }
    return {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "stocks",
        "asof": asof_date(price_asof),
        "generated_at": now.isoformat(),
        "sources": sources,
        "survivorship_status": ml.get("survivorship_status", "unknown"),
        "methodology": {
            "factor_values": "existing fields only",
            "sector_percentile": "average rank within same sector; finite observations only",
            "returns": "latest common monthly return from returns.json; not labelled as 20d",
        },
        "stocks": stocks,
        "data_quality": _quality(
            asof=price_asof,
            now=now,
            max_age_days=COMPONENT_MAX_AGE_DAYS["stocks"],
            missing_fields=[] if stocks else ["stocks"],
            warnings=[],
            source_quality="existing_stock_and_fundamental_artifacts",
            pit_quality="partial" if stocks else "unknown",
        ),
    }


def _public_fundamentals_index(fundamentals: dict) -> dict:
    companies = {}
    for ticker, row in (fundamentals.get("companies") or {}).items():
        latest = row.get("latest") or {}
        years = [item.get("year") for item in latest.values() if isinstance(item, dict) and item.get("year")]
        companies[ticker] = {
            "ticker": ticker,
            "latest_year": max(years) if years else None,
            "latest_values_hash": fingerprint(latest),
            "report_period_end": row.get("report_period_end"),
            "publication_date": row.get("publication_date"),
            "publication_timestamp_available": row.get("publication_timestamp_available"),
            "point_in_time_quality": row.get("point_in_time_quality"),
            "source_statuses": row.get("source_statuses") or [],
            "warnings": row.get("warnings") or [],
        }
    return {
        **{key: value for key, value in fundamentals.items() if key != "companies"},
        "companies": companies,
    }


def _split_stock_artifacts(stocks: dict) -> tuple[dict, dict[str, dict]]:
    snapshots = stocks.get("stocks") or {}
    files: dict[str, dict] = {}
    index_rows = []
    for ticker, snapshot in sorted(snapshots.items()):
        payload = {
            "schema_version": RESEARCH_SCHEMA_VERSION,
            "component": "stock",
            "generated_at": stocks.get("generated_at"),
            "survivorship_status": stocks.get("survivorship_status"),
            "methodology": stocks.get("methodology"),
            **snapshot,
        }
        payload["fingerprint"] = fingerprint(payload)
        relative = f"stocks/{ticker}.json"
        files[relative] = payload
        index_rows.append(
            {
                "ticker": ticker,
                "name": snapshot.get("name"),
                "sector": (snapshot.get("sector_context") or {}).get("sector"),
                "asof": snapshot.get("asof"),
                "path": f"data/research/{relative}",
                "fingerprint": payload["fingerprint"],
                "point_in_time_quality": (snapshot.get("data_quality") or {}).get("point_in_time_quality"),
            }
        )
    index = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "stocks",
        "asof": stocks.get("asof"),
        "generated_at": stocks.get("generated_at"),
        "sources": stocks.get("sources") or [],
        "survivorship_status": stocks.get("survivorship_status"),
        "methodology": stocks.get("methodology"),
        "stock_count": len(index_rows),
        "stocks": index_rows,
        "data_quality": stocks.get("data_quality") or {},
    }
    index["stock_payload_hash"] = fingerprint(
        {row["ticker"]: row["fingerprint"] for row in index_rows}
    )
    return index, files


def _component_manifest(name: str, payload: dict, now: datetime) -> dict:
    quality = payload.get("data_quality") or {}
    warnings = list(quality.get("warnings") or [])
    missing = quality.get("missing_fields") or []
    if not _source_files(payload):
        status = "unavailable"
    elif missing or warnings or not quality.get("fresh"):
        status = "degraded"
    else:
        status = "available"
    return {
        "asof": payload.get("asof"),
        "fresh": bool(quality.get("fresh")),
        "status": status,
        "source_files": _source_files(payload),
        "fingerprint": payload["fingerprint"],
        "warnings": sorted(set(warnings + (["component_not_fresh"] if not quality.get("fresh") else []))),
    }


def _date_span(components: dict[str, dict]) -> dict:
    dates = [parse_timestamp(row.get("asof")) for row in components.values()]
    dates = [value for value in dates if value is not None]
    if not dates:
        return {"oldest": None, "newest": None, "spread_days": None}
    oldest, newest = min(dates), max(dates)
    return {
        "oldest": oldest.date().isoformat(),
        "newest": newest.date().isoformat(),
        "spread_days": (newest.date() - oldest.date()).days,
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def build_research_state(
    repo_root: Path,
    *,
    site_dir: Path | None = None,
    output_dir: Path | None = None,
    fallback_roots: list[Path] | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc).replace(microsecond=0)
    site = site_dir or repo_root / "site"
    output = output_dir or site / "data" / "research"
    catalog = InputCatalog(site, fallback_roots)

    data_in = catalog.load("data.json")
    returns_in = catalog.load("returns.json")
    quality_in = catalog.load("quality.json")
    financials_in = catalog.load("site_financials.json")
    futures_in = catalog.load("futures_positions.json")
    data = _data(data_in, {})
    returns = _data(returns_in, {})
    quality = _data(quality_in, {})
    financials_raw = _data(financials_in, {})

    market = _market_snapshot(catalog, data.get("tickers", []), current)
    ml, pack_models = _ml_snapshot(catalog, current)
    mapping = load_sector_mapping(repo_root / "config" / "ml_strategy" / "sector_mapping.yml")
    pack_fn = lambda ticker, sector: pack_for_security(ticker, sector, mapping)
    sector_rows, sector_positions = build_sector_context(
        data.get("tickers", []), returns, quality.get("rows", []), pack_fn, pack_models
    )
    sector_asof_candidates = [market.get("source_dates", {}).get("returns")]
    sector_asof_candidates.extend(
        row.get("model", {}).get("latest_available_at") for row in sector_rows if row.get("model")
    )
    parsed_sector_dates = [parse_timestamp(value) for value in sector_asof_candidates]
    parsed_sector_dates = [value for value in parsed_sector_dates if value is not None]
    sector_asof = min(parsed_sector_dates).date().isoformat() if parsed_sector_dates else market.get("asof")
    sector_snapshot = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "component": "sectors",
        "asof": sector_asof,
        "generated_at": current.isoformat(),
        "sources": _source_refs(data_in, returns_in, quality_in, catalog.load("ml_strategy/sector_features/latest_quality.json")),
        "methodology": {
            "descriptive_returns": "equal-weight latest common monthly returns from returns.json",
            "breadth": "share of finite constituent monthly returns above zero",
            "dispersion": "population standard deviation of finite constituent monthly returns",
            "peer_percentiles": "average rank within exact sector; non-finite values excluded",
            "model_invariant": "promotion_status other than APPROVED implies tradable_signal=false",
        },
        "sectors": sector_rows,
    }
    sector_snapshot["data_quality"] = _quality(
        asof=sector_asof,
        now=current,
        max_age_days=COMPONENT_MAX_AGE_DAYS["sectors"],
        missing_fields=[] if sector_rows else ["sectors"],
        warnings=["all_sector_packs_research_only"]
        if pack_models and all(row.get("promotion_status") != "APPROVED" for row in pack_models.values())
        else [],
        source_quality="existing_cross_section_and_sector_ablation",
        pit_quality="verified"
        if (ml.get("sector_features") or {}).get("point_in_time_policy") == "available_at <= prediction_timestamp"
        else "unknown",
    )

    fundamentals = _compact_fundamentals(
        financials_raw,
        quality.get("rows", []),
        current,
        _source_refs(financials_in, quality_in),
    )
    news = _news_snapshot(catalog, current)
    futures = _data(futures_in, {})
    stocks = _stock_snapshots(
        data=data,
        returns=returns,
        quality=quality,
        fundamentals=fundamentals,
        sector_positions=sector_positions,
        sector_rows=sector_rows,
        pack_for_ticker_fn=pack_fn,
        pack_models=pack_models,
        market=market,
        ml=ml,
        futures=futures,
        news=news,
        now=current,
        sources=_source_refs(data_in, returns_in, quality_in, financials_in, futures_in),
    )
    banks = _bank_snapshot(catalog, current)
    bonds = _bond_snapshot(catalog, current)

    cleaning_warnings: list[str] = []
    fundamentals_public = _public_fundamentals_index(fundamentals)
    stocks_clean = _clean(stocks, cleaning_warnings, "$.stocks")
    stock_index, stock_files = _split_stock_artifacts(stocks_clean)
    artifact_payloads = {
        "market_snapshot.json": market,
        "fundamentals_snapshot.json": fundamentals_public,
        "sector_snapshot.json": sector_snapshot,
        "stock_index.json": stock_index,
        "ml_snapshot.json": ml,
        "bank_snapshot.json": banks,
        "bond_snapshot.json": bonds,
        "news_snapshot.json": news,
    }
    artifact_payloads = {
        name: _clean(payload, cleaning_warnings, f"$.{name}") for name, payload in artifact_payloads.items()
    }
    for payload in artifact_payloads.values():
        payload["fingerprint"] = fingerprint(payload)

    component_files = {
        "market": "market_snapshot.json",
        "fundamentals": "fundamentals_snapshot.json",
        "sectors": "sector_snapshot.json",
        "stocks": "stock_index.json",
        "ml": "ml_snapshot.json",
        "banks": "bank_snapshot.json",
        "bonds": "bond_snapshot.json",
        "news": "news_snapshot.json",
    }
    components = {
        component: _component_manifest(component, artifact_payloads[filename], current)
        for component, filename in component_files.items()
    }
    research_hash = aggregate_fingerprint(
        {component: row["fingerprint"] for component, row in components.items()}
    )
    manifest_warnings: list[str] = list(cleaning_warnings)
    span = _date_span(components)
    if span.get("spread_days") not in (None, 0):
        manifest_warnings.append(f"component_asof_spread_days:{span['spread_days']}")
    for component, row in components.items():
        manifest_warnings.extend(f"{component}:{warning}" for warning in row.get("warnings", []))
    manifest = {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "research_asof": market.get("asof"),
        "research_asof_basis": "market.price_asof; component dates remain explicit",
        "generated_at": current.isoformat(),
        "research_input_hash": research_hash,
        "components": components,
        "component_date_span": span,
        "survivorship_status": ml.get("survivorship_status", "unknown"),
        "warnings": sorted(set(manifest_warnings)),
        "validation_errors": [],
        "ready_for_ai": False,
    }
    artifacts = {**artifact_payloads, **stock_files, "research_manifest.json": manifest}
    validation = validate_research_bundle(artifacts, now=current)
    required_market = bool(market.get("asof") and (market.get("returns") or market.get("market_phase")))
    manifest["validation_errors"] = sorted(set(validation.errors))
    manifest["warnings"] = sorted(set(manifest["warnings"] + validation.warnings))
    legacy_ready = bool(validation.ok and required_market and research_hash.startswith("sha256:"))
    eligibility = evaluate_research_eligibility(
        components,
        schema_ready=validation.ok,
        research_hash=research_hash,
        now=current,
    )
    manifest.update(eligibility)
    manifest["ready_for_ai"] = legacy_ready
    if not required_market:
        manifest["validation_errors"].append("required_market_context_missing")
        manifest["validation_errors"] = sorted(set(manifest["validation_errors"]))

    final_validation = validate_research_bundle(artifacts, now=current)
    if final_validation.errors != validation.errors:
        manifest["validation_errors"] = sorted(set(manifest["validation_errors"] + final_validation.errors))
        manifest["ready_for_ai"] = False

    obsolete = output / "stock_snapshots.json"
    if obsolete.exists():
        obsolete.unlink()
    stock_dir = output / "stocks"
    expected_stock_names = {Path(name).name for name in stock_files}
    if stock_dir.exists():
        for path in stock_dir.glob("*.json"):
            if path.name not in expected_stock_names:
                path.unlink()
    for name, payload in artifacts.items():
        _write_json_atomic(output / name, payload)
    return artifacts
