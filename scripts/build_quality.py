#!/usr/bin/env python3
"""Build the RU Quality live screener artifact for the static site.

The current production panel has no reliable publication dates or split-adjusted
share history. The builder therefore exposes the available accounting ROE fallback,
never manufactures EPS/filing dates, and keeps low-confidence rows out of the
default portfolio.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.strategies.quality import (  # noqa: E402
    METHODOLOGY_VERSION,
    PortfolioConfig,
    build_quality_portfolio,
    compute_quality_scores,
    earnings_variability,
    infer_issuer_id,
    infer_security_type,
)

DEFAULT_PANEL = REPO / "data/panels_final/panel_russia_final.csv"
DEFAULT_REGISTRY = REPO / "data/companies_registry.csv"
DEFAULT_MOMENTUM = REPO / "model_output/momentum.json"
DEFAULT_RETURNS = REPO / "model_output/returns.json"
DEFAULT_REPORT_INDEX = REPO / "data/report_index.parquet"
DEFAULT_OFFICIAL_FACTS = REPO / "data/official_ifrs_facts.csv"
DEFAULT_SITE_DATA = REPO / "site/data.json"
DEFAULT_CONFIG = REPO / "src/config/quality_ru.json"
DEFAULT_MODEL_OUT = REPO / "model_output/quality_rf.json"
DEFAULT_SITE_OUT = REPO / "site/quality.json"


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    clean = str(value).strip()
    return clean or None


def _valid_date(value: Any) -> str | None:
    clean = _text(value)
    if not clean:
        return None
    try:
        return date.fromisoformat(clean[:10]).isoformat()
    except ValueError:
        return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _registry_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    return {str(row["ticker"]): row.to_dict() for _, row in frame.iterrows()}


def _site_map(path: Path) -> dict[str, dict[str, Any]]:
    data = _read_json(path, {})
    return {
        str(row.get("ticker")): row
        for row in (data.get("tickers") or [])
        if isinstance(row, dict) and row.get("ticker")
    }


def _momentum_map(path: Path) -> dict[str, dict[str, Any]]:
    data = _read_json(path, {})
    return data.get("data") or {}


def _returns_history(path: Path) -> dict[str, int]:
    data = _read_json(path, {})
    return {
        str(ticker): sum(isinstance(value, (int, float)) for value in values) * 21
        for ticker, values in (data.get("data") or {}).items()
        if isinstance(values, list)
    }


def _publication_map(path: Path, as_of: date) -> dict[tuple[str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for _, row in frame.iterrows():
        ticker = _text(row.get("ticker"))
        year = _num(row.get("fiscal_year"))
        publication = _valid_date(row.get("publication_date"))
        if not ticker or year is None or not publication:
            continue
        if date.fromisoformat(publication) > as_of:
            continue
        key = (ticker, int(year))
        current = out.get(key)
        if current is None or publication > current["publication_date"]:
            out[key] = {
                "publication_date": publication,
                "report_standard": _text(row.get("reporting_standard")),
                "source_url": _text(row.get("source_url")),
                "report_id": _text(row.get("report_id")),
                "source_name": _text(row.get("source_name")),
            }
    return out


def _official_map(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for (ticker, year), group in frame.groupby(["ticker", "fiscal_year"]):
        first = group.iloc[0]
        out[(str(ticker), int(year))] = {
            "report_standard": _text(first.get("reporting_standard")),
            "source_url": _text(first.get("source_url")),
            "report_id": _text(first.get("report_id")),
            "source_name": _text(first.get("source_name")),
        }
    return out


def _latest_value(group: pd.DataFrame, column: str) -> float | None:
    if column not in group:
        return None
    series = pd.to_numeric(group[column], errors="coerce").dropna()
    return float(series.iloc[-1]) if len(series) else None


def _eps_history(group: pd.DataFrame) -> tuple[list[float] | None, str | None]:
    eps_column = next((name for name in ("eps", "eps_ttm") if name in group.columns), None)
    if eps_column:
        values = pd.to_numeric(group[eps_column], errors="coerce").dropna().tail(5)
        return (values.astype(float).tolist(), "reported_eps") if len(values) == 5 else (None, None)
    shares_column = next(
        (name for name in ("weighted_average_diluted_shares", "shares_outstanding", "number_of_shares") if name in group.columns),
        None,
    )
    if not shares_column or "net_profit_mln" not in group:
        return None, None
    subset = group[["year", "net_profit_mln", shares_column]].copy()
    subset["net_profit_mln"] = pd.to_numeric(subset["net_profit_mln"], errors="coerce")
    subset[shares_column] = pd.to_numeric(subset[shares_column], errors="coerce")
    subset = subset.dropna().loc[lambda frame: frame[shares_column] > 0].tail(5)
    if len(subset) != 5:
        return None, None
    eps = subset["net_profit_mln"] * 1_000_000 / subset[shares_column]
    return eps.astype(float).tolist(), "net_income_over_reported_shares"


def _confidence(
    *, publication_date: str | None, report_standard: str, roe_method: str | None,
    descriptor_count: int, evar_available: bool, filing_age_days: int | None,
    max_filing_age_days: int,
) -> str:
    standard = report_standard.upper()
    stale = filing_age_days is None or filing_age_days > max_filing_age_days
    if not publication_date or stale or standard in {"UNKNOWN", "RAS", "RAP"}:
        return "low"
    if standard == "IFRS" and roe_method == "eps_over_bvps" and descriptor_count == 3 and evar_available:
        return "high"
    return "medium"


def _row_explanation(row: dict[str, Any]) -> dict[str, Any]:
    z = row.get("z") or {}
    strengths = []
    weaknesses = []
    if _num(z.get("roe")) is not None:
        (strengths if z["roe"] >= 0.5 else weaknesses if z["roe"] <= -0.5 else []).append(
            "высокая рентабельность" if z["roe"] >= 0.5 else "рентабельность ниже рынка"
        )
    if _num(z.get("debt_to_equity")) is not None:
        (strengths if z["debt_to_equity"] >= 0.5 else weaknesses if z["debt_to_equity"] <= -0.5 else []).append(
            "контролируемая долговая нагрузка" if z["debt_to_equity"] >= 0.5 else "долговая нагрузка выше рынка"
        )
    if _num(z.get("earnings_variability")) is not None:
        (strengths if z["earnings_variability"] >= 0.5 else weaknesses if z["earnings_variability"] <= -0.5 else []).append(
            "стабильная прибыль" if z["earnings_variability"] >= 0.5 else "повышенная изменчивость прибыли"
        )
    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "summary": (
            ("Сильные стороны: " + ", ".join(strengths) + ".") if strengths else "Явного преимущества по доступным факторам нет."
        ),
        "confidence_note": (
            "Достоверность низкая: нет подтверждённой даты публикации или полной истории EPS."
            if row.get("confidence") == "low"
            else "Достоверность средняя: используется допустимый fallback или доступны два фактора."
            if row.get("confidence") == "medium"
            else "Достоверность высокая: три фактора и дата официальной отчётности подтверждены."
        ),
    }


def validate_quality_artifact(payload: dict[str, Any]) -> None:
    meta, rows = payload.get("meta"), payload.get("rows")
    if not isinstance(meta, dict) or meta.get("methodology_version") != METHODOLOGY_VERSION:
        raise ValueError("quality artifact has invalid methodology metadata")
    if not isinstance(rows, list) or not rows:
        raise ValueError("quality artifact rows are empty")
    required = {
        "ticker", "sector", "raw", "winsorized", "z", "coverage_ratio",
        "confidence", "eligible", "exclusion_reasons", "warnings", "provenance",
    }
    for row in rows:
        missing = required - set(row)
        if missing:
            raise ValueError(f"quality row {row.get('ticker')} misses {sorted(missing)}")
        if row.get("eligible") and row.get("confidence") == "low":
            raise ValueError(f"low-confidence row {row.get('ticker')} is default eligible")
        if row.get("quality_score_sector") is not None and row.get("quality_z_absolute") is None:
            raise ValueError(f"sector score without absolute composite: {row.get('ticker')}")
    if meta.get("n_universe") != len(rows):
        raise ValueError("quality meta n_universe does not match rows")


def build_quality_artifact(
    *, panel_path: Path = DEFAULT_PANEL, registry_path: Path = DEFAULT_REGISTRY,
    momentum_path: Path = DEFAULT_MOMENTUM, returns_path: Path = DEFAULT_RETURNS,
    report_index_path: Path = DEFAULT_REPORT_INDEX, official_facts_path: Path = DEFAULT_OFFICIAL_FACTS,
    site_data_path: Path = DEFAULT_SITE_DATA, config_path: Path = DEFAULT_CONFIG,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc).date()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    panel = pd.read_csv(panel_path)
    panel["ticker"] = panel["ticker"].astype(str)
    panel["year"] = pd.to_numeric(panel["year"], errors="coerce")
    panel = panel.loc[panel["year"].notna() & (panel["year"] <= as_of.year)].copy()
    if panel.empty:
        raise ValueError("quality source panel is empty")
    panel["year"] = panel["year"].astype(int)

    registry = _registry_map(registry_path)
    site = _site_map(site_data_path)
    momentum = _momentum_map(momentum_path)
    history_days = _returns_history(returns_path)
    publications = _publication_map(report_index_path, as_of)
    official = _official_map(official_facts_path)
    excluded_sectors = set(config.get("excluded_sectors") or [])
    excluded_tickers = set(config.get("excluded_tickers") or [])
    super_sectors = config.get("super_sectors") or {}
    raw_rows = []

    for ticker, group in panel.sort_values("year").groupby("ticker", sort=True):
        group = group.sort_values("year")
        comparable = group.copy()
        comparable["_net_income"] = pd.to_numeric(
            comparable["net_profit_mln"] if "net_profit_mln" in comparable else pd.Series(index=comparable.index, dtype=float),
            errors="coerce",
        )
        comparable["_equity"] = pd.to_numeric(
            comparable["equity_mln"] if "equity_mln" in comparable else pd.Series(index=comparable.index, dtype=float),
            errors="coerce",
        )
        comparable = comparable.loc[comparable["_net_income"].notna() & comparable["_equity"].notna()]
        current = comparable.iloc[-1] if len(comparable) else group.iloc[-1]
        fiscal_year = int(current["year"])
        registry_row = registry.get(ticker, {})
        site_row = site.get(ticker, {})
        momentum_row = momentum.get(ticker, {})
        sector = _text(current.get("sector")) or _text(registry_row.get("sector")) or "Unknown"
        equity = _num(current.get("equity_mln"))
        net_income = _num(current.get("net_profit_mln"))
        total_debt = _num(current.get("total_debt_mln"))
        eps_values, eps_method = _eps_history(group)
        evar = earnings_variability(eps_values) if eps_values else None
        shares_column = next(
            (name for name in ("weighted_average_diluted_shares", "shares_outstanding", "number_of_shares") if name in group),
            None,
        )
        roe = None
        roe_method = None
        if eps_values and shares_column and equity and equity > 0:
            shares = _latest_value(group, shares_column)
            bvps = equity * 1_000_000 / shares if shares and shares > 0 else None
            roe = eps_values[-1] / bvps if bvps and bvps > 0 else None
            roe_method = "eps_over_bvps" if roe is not None else None
        if roe is None and net_income is not None and equity and equity > 0:
            roe = net_income / equity
            roe_method = "accounting_fallback"
        debt_to_equity = total_debt / equity if total_debt is not None and equity and equity > 0 else None

        report_meta = dict(official.get((ticker, fiscal_year), {}))
        report_meta.update(publications.get((ticker, fiscal_year), {}))
        publication_date = report_meta.get("publication_date")
        report_standard = str(report_meta.get("report_standard") or "UNKNOWN").upper()
        filing_age = (
            (as_of - date.fromisoformat(publication_date)).days if publication_date else None
        )
        descriptor_count = sum(value is not None for value in (roe, debt_to_equity, evar))
        confidence = _confidence(
            publication_date=publication_date,
            report_standard=report_standard,
            roe_method=roe_method,
            descriptor_count=descriptor_count,
            evar_available=evar is not None,
            filing_age_days=filing_age,
            max_filing_age_days=int(config["max_filing_age_days"]),
        )

        market_cap_mln = _num(site_row.get("mcap")) or _latest_value(group, "market_cap_mln")
        market_cap_rub = market_cap_mln * 1_000_000 if market_cap_mln is not None else None
        free_float_pct = _latest_value(group, "free_float_pct")
        free_float_mcap = (
            market_cap_rub * free_float_pct / 100
            if market_cap_rub is not None and free_float_pct is not None and 0 < free_float_pct <= 100
            else None
        )
        adv = _num(site_row.get("adv")) or _num(momentum_row.get("adv"))
        trading_days = history_days.get(ticker)
        board = _text(registry_row.get("moex_board"))
        security_type = infer_security_type(ticker)
        price = _num(site_row.get("price")) or _latest_value(group, "price_end")
        lot_size = int(_num(site_row.get("lot_size")) or 1)
        investability_reasons = []
        if board != config["board"]:
            investability_reasons.append("not_tqbr")
        if market_cap_rub is None or market_cap_rub < config["min_market_cap_rub"]:
            investability_reasons.append("market_cap_below_threshold_or_missing")
        if adv is None or adv < config["min_adtv_rub"]:
            investability_reasons.append("adv_below_threshold_or_missing")
        if trading_days is None or trading_days < config["min_trading_history_days"]:
            investability_reasons.append("trading_history_too_short_or_missing")
        if security_type not in config["allowed_security_types"]:
            investability_reasons.append("security_type_not_allowed")
        if equity is None or equity <= 0:
            investability_reasons.append("non_positive_common_equity")
        if ticker in excluded_tickers:
            investability_reasons.append("ticker_excluded_by_config")

        warnings = []
        if roe_method == "accounting_fallback":
            warnings.append("roe_accounting_fallback")
        if evar is None:
            warnings.append("missing_five_year_comparable_eps")
        if publication_date is None:
            warnings.extend(["publication_date_unknown", "point_in_time_backtest_ineligible"])
        if report_standard == "UNKNOWN":
            warnings.append("report_standard_unknown")
        if trading_days is not None:
            warnings.append("trading_history_days_estimated_from_monthly_returns")
        financial_required = sector in excluded_sectors
        if financial_required:
            debt_to_equity = None
            warnings.append("industrial_debt_to_equity_not_applied_to_financials")

        issuer_id = infer_issuer_id(ticker, _text(registry_row.get("inn")))
        raw_rows.append(
            {
                "ticker": ticker,
                "issuer_id": issuer_id,
                "name": _text(site_row.get("name")) or _text(registry_row.get("short_name")) or ticker,
                "sector": sector,
                "super_sector": super_sectors.get(sector, "Market"),
                "security_type": security_type,
                "report_standard": report_standard,
                "report_period_end": f"{fiscal_year:04d}-12-31",
                "publication_date": publication_date,
                "ingestion_date": _valid_date(current.get("ingestion_date")) if "ingestion_date" in current else None,
                "data_as_of": as_of.isoformat(),
                "consolidated": None,
                "restatement_flag": None,
                "source_document_id": report_meta.get("report_id"),
                "raw": {
                    "roe": round(roe, 8) if roe is not None else None,
                    "debt_to_equity": round(debt_to_equity, 8) if debt_to_equity is not None else None,
                    "earnings_variability": round(evar, 8) if evar is not None else None,
                },
                "diagnostics": {
                    "roe_method": roe_method,
                    "roe_accounting": round(net_income / equity, 8) if net_income is not None and equity and equity > 0 else None,
                    "eps_method": eps_method,
                    "eps_history": eps_values,
                    "profit_variability_proxy": _latest_value(group, "vol3y_net_profit"),
                },
                "market_cap_rub": round(market_cap_rub, 2) if market_cap_rub is not None else None,
                "free_float_market_cap_rub": round(free_float_mcap, 2) if free_float_mcap is not None else None,
                "adv_rub": round(adv, 2) if adv is not None else None,
                "trading_history_days": trading_days,
                "volatility": _num(momentum_row.get("vol_ann")),
                "price": price,
                "lot_size": lot_size,
                "confidence": confidence,
                "financial_model_required": financial_required,
                "investability": {
                    "eligible": not investability_reasons and not financial_required,
                    "board": board,
                    "reasons": investability_reasons,
                },
                "exclusion_reasons": list(investability_reasons),
                "warnings": warnings,
                "provenance": {
                    "source": str(panel_path.relative_to(REPO)) if panel_path.is_relative_to(REPO) else str(panel_path),
                    "source_type": "SmartLab_reconciled_panel",
                    "source_url": report_meta.get("source_url"),
                    "source_name": report_meta.get("source_name"),
                    "fiscal_year": fiscal_year,
                    "roe_fields": ["net_profit_mln", "equity_mln"] if roe_method == "accounting_fallback" else ["eps", "shares_outstanding", "equity_mln"],
                    "debt_to_equity_fields": ["total_debt_mln", "equity_mln"],
                    "earnings_variability_fields": ["eps", "shares_outstanding"],
                },
            }
        )

    scored_rows, stats = compute_quality_scores(raw_rows, config)
    for row in scored_rows:
        row["explanation"] = _row_explanation(row)
        row["exclusion_reasons"] = list(dict.fromkeys(
            row.get("exclusion_reasons", []) + (row.get("investability") or {}).get("reasons", [])
        ))

    missing_metrics = Counter()
    for row in scored_rows:
        for key, value in (row.get("raw") or {}).items():
            if value is None:
                missing_metrics[key] += 1
    oldest_period = min((row["report_period_end"] for row in scored_rows), default=None)
    warnings = list(dict.fromkeys(
        stats.get("warnings", [])
        + ["publication_date coverage is incomplete; strict point-in-time backtest is unavailable"]
        + (["no high/medium confidence companies pass the default portfolio gate"] if stats["n_eligible"] == 0 else [])
    ))
    calculated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "meta": {
            "schema_version": config["schema_version"],
            "methodology_version": METHODOLOGY_VERSION,
            "calculated_at": calculated_at,
            "as_of_date": as_of.isoformat(),
            "universe_name": config["universe_name"],
            "normalization": "sector_relative",
            "winsorization": config["winsorization"],
            "n_universe": stats["n_universe"],
            "n_scored": stats["n_scored"],
            "n_investable_scored": stats["n_investable_scored"],
            "n_eligible": stats["n_eligible"],
            "data_coverage": stats["data_coverage"],
            "confidence": stats["confidence"],
            "normalization_scopes": stats["normalization_scopes"],
            "descriptor_stats": stats["descriptors"],
            "sources": [
                str(panel_path.relative_to(REPO)) if panel_path.is_relative_to(REPO) else str(panel_path),
                str(momentum_path.relative_to(REPO)) if momentum_path.exists() and momentum_path.is_relative_to(REPO) else str(momentum_path),
                str(official_facts_path.relative_to(REPO)) if official_facts_path.exists() and official_facts_path.is_relative_to(REPO) else str(official_facts_path),
            ],
            "warnings": warnings,
            "stale": False,
            "extended_status": "unavailable_insufficient_verified_descriptor_coverage",
            "backtest_status": "unavailable_no_publication_date_history",
        },
        "config": config,
        "data_quality": {
            "confidence": stats["confidence"],
            "missing_metrics": dict(missing_metrics),
            "oldest_report_period_end": oldest_period,
            "weak_coverage_sectors": sorted({
                row["sector"] for row in scored_rows if float(row.get("coverage_ratio") or 0) < 0.67
            }),
        },
        "backtest": {
            "status": "unavailable",
            "point_in_time": False,
            "reason": "Нет исторических publication_date и split-adjusted EPS/share history; числовые backtest-метрики не публикуются.",
        },
        "rows": scored_rows,
    }
    default_cfg = config["default"]
    payload["default_portfolio"] = build_quality_portfolio(
        scored_rows,
        PortfolioConfig(
            top_n=int(default_cfg["top_n"]),
            sector_neutral=bool(default_cfg["sector_neutral"]),
            weighting=str(default_cfg["weighting"]),
            min_coverage=float(config["min_quality_coverage"]),
            min_adv_rub=float(config["min_adtv_rub"]),
            min_market_cap_rub=float(config["min_market_cap_rub"]),
            max_security_weight=float(default_cfg["max_security_weight"]),
            max_issuer_weight=float(default_cfg["max_issuer_weight"]),
            max_sector_weight=float(default_cfg["max_sector_weight"]),
            capital=float(default_cfg["capital"]),
            allow_low_confidence=bool(default_cfg["allow_low_confidence"]),
            one_security_per_issuer=bool(config["one_security_per_issuer"]),
        ),
    )
    validate_quality_artifact(payload)
    return payload


def _stale_fallback(path: Path, reason: str) -> dict[str, Any] | None:
    previous = _read_json(path, None)
    if not isinstance(previous, dict):
        return None
    try:
        validate_quality_artifact(previous)
    except ValueError:
        return None
    fallback = json.loads(json.dumps(previous))
    fallback["meta"]["stale"] = True
    fallback["meta"].setdefault("warnings", []).append(f"stale_fallback:{reason[:160]}")
    fallback["meta"]["fallback_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--momentum", type=Path, default=DEFAULT_MOMENTUM)
    parser.add_argument("--returns", type=Path, default=DEFAULT_RETURNS)
    parser.add_argument("--report-index", type=Path, default=DEFAULT_REPORT_INDEX)
    parser.add_argument("--official-facts", type=Path, default=DEFAULT_OFFICIAL_FACTS)
    parser.add_argument("--site-data", type=Path, default=DEFAULT_SITE_DATA)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT)
    parser.add_argument("--site-out", type=Path, default=DEFAULT_SITE_OUT)
    parser.add_argument("--as-of", type=date.fromisoformat)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = build_quality_artifact(
            panel_path=args.panel,
            registry_path=args.registry,
            momentum_path=args.momentum,
            returns_path=args.returns,
            report_index_path=args.report_index,
            official_facts_path=args.official_facts,
            site_data_path=args.site_data,
            config_path=args.config,
            as_of=args.as_of,
        )
    except Exception as exc:  # noqa: BLE001
        fallback = _stale_fallback(args.model_out, str(exc))
        if fallback is None:
            print(f"[quality] build failed and no valid fallback exists: {exc}", file=sys.stderr)
            return 1
        payload = fallback
        print(f"[quality] source build failed; publishing stale validated artifact: {exc}", file=sys.stderr)
    _atomic_json(args.model_out, payload)
    _atomic_json(args.site_out, payload)
    meta = payload["meta"]
    print(
        "[quality] "
        f"universe={meta['n_universe']} scored={meta['n_scored']} "
        f"investable_scored={meta.get('n_investable_scored')} eligible={meta['n_eligible']} "
        f"coverage={meta['data_coverage']:.1%} stale={meta.get('stale')}"
    )
    print(f"[quality] confidence={meta.get('confidence')} scopes={meta.get('normalization_scopes')}")
    print(f"[quality] warnings={len(meta.get('warnings') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
