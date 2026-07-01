from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_csv, write_json, write_parquet
from src.normalization.tickers import canonical_ticker
from src.paths import REPO_ROOT


PANEL_PRIMARY = Path("data/panels_final/panel_russia_final_smartlab.csv")
PANEL_FALLBACK = Path("data/panels_final/panel_russia_final.csv")
MAP_PATH = Path("src/config/smartlab_fundamentals_map.json")

CLEANED_COLUMNS = [
    "ticker",
    "year",
    "period",
    "standard",
    "field",
    "display_name_ru",
    "statement_group",
    "statement_type",
    "raw_field",
    "raw_value",
    "clean_value",
    "unit",
    "scale",
    "display_format",
    "chart_group",
    "sort_order",
    "source_name",
    "source_url",
    "source_status",
    "quality_status",
    "quality_reason",
    "needs_manual_review",
    "excluded_from_site",
    "exclude_from_score",
    "corrected_by",
    "correction_source",
    "audit_timestamp",
]

ISSUE_COLUMNS = [
    "ticker",
    "year",
    "field",
    "raw_value",
    "clean_value",
    "issue_type",
    "quality_status",
    "quality_reason",
    "suggested_action",
    "source_name",
    "source_url",
    "needs_manual_review",
    "excluded_from_site",
]

BACKFILL_COLUMNS = [
    "ticker",
    "canonical_ticker",
    "year",
    "period",
    "standard",
    "field",
    "value",
    "source_name",
    "source_url",
    "load_method",
    "raw_field_name",
    "normalized_field",
    "backfill_status",
    "backfill_reason",
    "loaded_at",
]

GAP_COLUMNS = [
    "ticker",
    "year",
    "field",
    "display_name_ru",
    "site_has_value",
    "raw_smartlab_has_value",
    "cleaned_layer_has_value",
    "can_be_calculated",
    "missing_reason",
    "next_action",
]

SAMPLE_COMPANIES = ["MTSS", "SBER", "LKOH", "PHOR", "GMKN", "CHMF", "MOEX", "NVTK", "ROSN", "ALRS"]
SAMPLE_COVERAGE_COLUMNS = [
    "ticker",
    "fundamentals_groups_count",
    "displayable_metrics_count",
    "missing_metrics_count",
    "calculated_metrics_count",
    "backfilled_metrics_count",
    "excluded_metrics_count",
    "review_metrics_count",
]

DERIVED_FIELDS = [
    {
        "field": "debt_to_assets",
        "display_name_ru": "Debt / Assets",
        "statement_group": "ratios",
        "statement_type": "ratios",
        "unit": "percent",
        "scale": 1,
        "display_format": "percent",
        "chart_group": "ratios",
        "sort_order": 55,
        "can_be_negative": False,
    },
    {
        "field": "shares_outstanding",
        "display_name_ru": "Акций в обращении",
        "statement_group": "valuation",
        "statement_type": "valuation",
        "unit": "shares",
        "scale": 1,
        "display_format": "shares",
        "chart_group": "valuation",
        "sort_order": 80,
        "can_be_negative": False,
    },
    {
        "field": "eps",
        "display_name_ru": "EPS",
        "statement_group": "per_share",
        "statement_type": "per_share",
        "unit": "rub_per_share",
        "scale": 1,
        "display_format": "rub",
        "chart_group": "per_share",
        "sort_order": 10,
        "can_be_negative": True,
    },
    {
        "field": "book_value_per_share",
        "display_name_ru": "Book value / share",
        "statement_group": "per_share",
        "statement_type": "per_share",
        "unit": "rub_per_share",
        "scale": 1,
        "display_format": "rub",
        "chart_group": "per_share",
        "sort_order": 20,
        "can_be_negative": True,
    },
    {
        "field": "revenue_per_share",
        "display_name_ru": "Выручка / акция",
        "statement_group": "per_share",
        "statement_type": "per_share",
        "unit": "rub_per_share",
        "scale": 1,
        "display_format": "rub",
        "chart_group": "per_share",
        "sort_order": 30,
        "can_be_negative": False,
    },
    {
        "field": "fcf_per_share",
        "display_name_ru": "FCF / акция",
        "statement_group": "per_share",
        "statement_type": "per_share",
        "unit": "rub_per_share",
        "scale": 1,
        "display_format": "rub",
        "chart_group": "per_share",
        "sort_order": 40,
        "can_be_negative": True,
    },
]


def load_fundamental_map(root: Path = REPO_ROOT) -> list[dict]:
    path = root / MAP_PATH
    if not path.exists():
        path = REPO_ROOT / MAP_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def build_smartlab_fundamentals_cleaned(root: Path = REPO_ROOT, now: str | None = None) -> pd.DataFrame:
    cleaned, _backfilled, _gap, _gap_summary, _coverage = build_smartlab_fundamentals_layers(root, now)
    return cleaned


def build_smartlab_fundamentals_layers(
    root: Path = REPO_ROOT,
    now: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict, pd.DataFrame]:
    now = now or utc_now_iso()
    panel_rel = PANEL_PRIMARY if (root / PANEL_PRIMARY).exists() else PANEL_FALLBACK
    panel_path = root / panel_rel
    if not panel_path.exists():
        cleaned = pd.DataFrame(columns=CLEANED_COLUMNS)
        backfilled = pd.DataFrame(columns=BACKFILL_COLUMNS)
        gap = pd.DataFrame(columns=GAP_COLUMNS)
        gap_summary = smartlab_gap_summary(gap)
        coverage = pd.DataFrame(columns=SAMPLE_COVERAGE_COLUMNS)
        return cleaned, backfilled, gap, gap_summary, coverage
    panel = pd.read_csv(panel_path)
    if not {"ticker", "year"}.issubset(panel.columns):
        raise ValueError(f"{panel_rel}: required columns missing")
    mappings = load_fundamental_map(root)
    direct_rows: list[dict] = []
    backfill_rows: list[dict] = []
    for _, rec in panel.iterrows():
        ticker = canonical_ticker(rec.get("ticker"), root)
        year = _int_or_none(rec.get("year"))
        if not ticker or year is None:
            continue
        for meta in mappings:
            raw_field, raw = raw_value_for_meta(rec, meta)
            if raw_field is None or raw is None:
                continue
            source_status = "smartlab_fallback" if raw_field == primary_raw_field(meta) else "mapped_from_raw_existing"
            direct_rows.append(_clean_row(ticker, year, raw_field, raw, meta, now, source_status=source_status))
            if source_status == "mapped_from_raw_existing":
                backfill_rows.append(backfill_row(ticker, year, meta, raw, raw_field, "mapped_from_raw_existing", "raw alias found in SmartLab panel", now))

    direct = pd.DataFrame(direct_rows, columns=CLEANED_COLUMNS)
    if not direct.empty:
        direct = direct.sort_values(["ticker", "year", "statement_group", "sort_order", "field"]).reset_index(drop=True)
        direct = apply_time_series_reviews(direct)

    initial_gap = build_smartlab_gap_report(panel, direct, mappings)
    calculated_rows = calculate_missing_rows(panel, direct, mappings, now, backfill_rows, root)
    if calculated_rows:
        out = pd.concat([direct, pd.DataFrame(calculated_rows, columns=CLEANED_COLUMNS)], ignore_index=True)
    else:
        out = direct.copy()
    if out.empty:
        cleaned = pd.DataFrame(columns=CLEANED_COLUMNS)
    else:
        out = prefer_displayable_fundamental_rows(out)
        out = out.sort_values(["ticker", "year", "statement_group", "sort_order", "field"]).reset_index(drop=True)
        cleaned = apply_time_series_reviews(out)
    coverage = sample_company_card_coverage(cleaned, mappings)
    final_gap = build_smartlab_gap_report(panel, cleaned, mappings)
    gap_summary = smartlab_gap_summary(final_gap, initial_gap)
    backfilled = pd.DataFrame(backfill_rows, columns=BACKFILL_COLUMNS).drop_duplicates(
        ["ticker", "year", "normalized_field", "backfill_status", "raw_field_name"],
        keep="first",
    )
    return cleaned, backfilled, final_gap, gap_summary, coverage


def write_smartlab_fundamentals_cleaned(root: Path = REPO_ROOT) -> dict:
    cleaned, backfilled, gap, gap_summary, coverage = build_smartlab_fundamentals_layers(root)
    write_parquet(root / "data" / "processed" / "smartlab_fundamentals_cleaned.parquet", cleaned)
    write_parquet(root / "data" / "processed" / "smartlab_fundamentals_backfilled.parquet", backfilled)
    issues = fundamental_issues(cleaned)
    write_csv(root / "data" / "manual_review" / "fundamental_data_issues.csv", issues, ISSUE_COLUMNS)
    summary = smartlab_cleaned_summary(cleaned)
    summary.update(smartlab_backfill_summary(backfilled, gap_summary))
    write_csv(root / "data" / "quality" / "smartlab_fundamentals_gap_report.csv", gap.to_dict("records"), GAP_COLUMNS)
    write_json(root / "data" / "quality" / "smartlab_fundamentals_gap_summary.json", gap_summary)
    write_csv(root / "data" / "quality" / "sample_company_card_coverage.csv", coverage.to_dict("records"), SAMPLE_COVERAGE_COLUMNS)
    write_json(root / "data" / "quality" / "smartlab_cleaned_layer_summary.json", summary)
    return summary


def primary_raw_field(meta: dict) -> str | None:
    raw = meta.get("raw_field")
    return str(raw) if raw not in (None, "") else None


def raw_aliases(meta: dict) -> list[str]:
    aliases = [str(a) for a in meta.get("aliases", []) if str(a).strip()]
    primary = primary_raw_field(meta)
    if primary:
        aliases.insert(0, primary)
    seen = set()
    out = []
    for alias in aliases:
        if alias not in seen:
            out.append(alias)
            seen.add(alias)
    return out


def raw_value_for_meta(rec: pd.Series, meta: dict) -> tuple[str | None, float | None]:
    for alias in raw_aliases(meta):
        if alias not in rec.index:
            continue
        raw = _num_or_none(rec.get(alias))
        if raw is not None:
            return alias, raw
    return None, None


def clean_base_lookup(cleaned: pd.DataFrame) -> dict[tuple[str, int, str], float]:
    if cleaned.empty:
        return {}
    base = cleaned[
        (cleaned["quality_status"] == "clean")
        & ~cleaned["excluded_from_site"].fillna(False).astype(bool)
        & cleaned["clean_value"].notna()
    ]
    return {
        (str(r.ticker).upper(), int(r.year), str(r.field)): float(r.clean_value)
        for r in base.itertuples()
    }


def calculate_missing_rows(
    panel: pd.DataFrame,
    direct: pd.DataFrame,
    mappings: list[dict],
    now: str,
    backfill_rows: list[dict],
    root: Path,
) -> list[dict]:
    rows: list[dict] = []
    base = clean_base_lookup(direct)
    existing_site_keys = set(base)
    formula_metas = [m for m in mappings if str(m.get("calculation_formula") or "")]
    failed_denominators: set[tuple[str, int, str]] = set()
    panel_records = list(panel.iterrows())
    for _pass in range(max(len(formula_metas), 1)):
        progressed = False
        for _, rec in panel_records:
            ticker = canonical_ticker(rec.get("ticker"), root)
            year = _int_or_none(rec.get("year"))
            if not ticker or year is None:
                continue
            for meta in formula_metas:
                key = (ticker, year, str(meta["field"]))
                if key in existing_site_keys:
                    continue
                value, reason = calculate_formula_value(meta, ticker, year, base, rec)
                if value is None:
                    if reason == "unreliable_denominator" and key not in failed_denominators:
                        backfill_rows.append(backfill_row(ticker, year, meta, None, f"calculated:{meta['field']}", "failed", reason, now))
                        failed_denominators.add(key)
                    continue
                rows.append(_clean_row(ticker, year, f"calculated:{meta['field']}", value, meta, now, calculated=True))
                backfill_rows.append(backfill_row(ticker, year, meta, value, f"calculated:{meta['field']}", "calculated_from_base_facts", str(meta.get("calculation_formula") or ""), now))
                base[key] = value
                existing_site_keys.add(key)
                progressed = True
        if not progressed:
            break
    return rows


def calculate_formula_value(
    meta: dict,
    ticker: str,
    year: int,
    base: dict[tuple[str, int, str], float],
    rec: pd.Series,
) -> tuple[float | None, str]:
    field = str(meta["field"])

    def val(name: str) -> float | None:
        if name == "year_end_price":
            raw = _num_or_none(rec.get("price_end"))
            return raw if raw is not None and raw > 0 else None
        return base.get((ticker, year, name))

    def denom(name: str) -> float | None:
        raw = val(name)
        if raw is None or abs(raw) < 1e-12 or raw <= 0:
            return None
        return raw

    if field == "free_cash_flow":
        ocf, capex = val("operating_cash_flow"), val("capex")
        return (ocf + capex, "") if ocf is not None and capex is not None else (None, "missing_base_facts")
    if field == "net_debt":
        debt, cash = val("total_debt"), val("cash_and_equivalents")
        return (debt - cash, "") if debt is not None and cash is not None else (None, "missing_base_facts")
    if field == "liabilities":
        assets, equity = val("total_assets"), val("total_equity")
        return (assets - equity, "") if assets is not None and equity is not None else (None, "missing_base_facts")
    if field == "shares_outstanding":
        market_cap, price = val("market_cap"), val("year_end_price")
        if market_cap is None or price is None:
            return None, "missing_base_facts"
        return market_cap * 1_000_000 / price, ""
    if field == "dividends":
        dps, shares = val("dividends_per_share"), val("shares_outstanding")
        return (dps * shares / 1_000_000, "") if dps is not None and shares is not None else (None, "missing_base_facts")
    if field == "dividend_yield":
        dps, price = val("dividends_per_share"), val("year_end_price")
        if dps is None or price is None:
            return None, "missing_base_facts"
        return dps / price * 100, ""

    ratio_formulas = {
        "net_margin": ("net_income", "revenue", 100),
        "ebitda_margin": ("ebitda", "revenue", 100),
        "operating_margin": ("operating_profit", "revenue", 100),
        "roe": ("net_income", "total_equity", 100),
        "roa": ("net_income", "total_assets", 100),
        "debt_to_assets": ("total_debt", "total_assets", 100),
        "debt_to_equity": ("total_debt", "total_equity", 1),
        "net_debt_to_ebitda": ("net_debt", "ebitda", 1),
        "payout_ratio": ("dividends", "net_income", 100),
    }
    if field in ratio_formulas:
        numerator_field, denominator_field, multiplier = ratio_formulas[field]
        numerator = val(numerator_field)
        denominator = denom(denominator_field)
        if numerator is None:
            return None, "missing_base_facts"
        if denominator is None:
            return None, "unreliable_denominator"
        return numerator / denominator * multiplier, ""

    per_share_formulas = {
        "eps": "net_income",
        "book_value_per_share": "total_equity",
        "revenue_per_share": "revenue",
        "fcf_per_share": "free_cash_flow",
    }
    if field in per_share_formulas:
        numerator = val(per_share_formulas[field])
        shares = denom("shares_outstanding")
        if numerator is None:
            return None, "missing_base_facts"
        if shares is None:
            return None, "unreliable_denominator"
        return numerator * 1_000_000 / shares, ""

    return None, "unsupported_formula"


def prefer_displayable_fundamental_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()

    def rank(row: pd.Series) -> tuple[int, int]:
        clean = row.get("quality_status") == "clean" and not bool(row.get("excluded_from_site"))
        source_status = str(row.get("source_status") or "")
        if clean and source_status == "smartlab_fallback":
            return (0, 0)
        if clean and source_status == "mapped_from_raw_existing":
            return (1, 0)
        if clean and source_status == "calculated_from_clean_base_facts":
            return (2, 0)
        if clean:
            return (3, 0)
        return (4, 0)

    out["_rank"] = out.apply(rank, axis=1)
    out = (
        out.sort_values(["ticker", "year", "field", "_rank"])
        .drop_duplicates(["ticker", "year", "field"], keep="first")
        .drop(columns=["_rank"])
    )
    return out.reset_index(drop=True)


def backfill_row(
    ticker: str,
    year: int,
    meta: dict,
    value: float | None,
    raw_field_name: str,
    status: str,
    reason: str,
    now: str,
) -> dict:
    return {
        "ticker": ticker,
        "canonical_ticker": ticker,
        "year": year,
        "period": str(year),
        "standard": "IFRS",
        "field": meta["field"],
        "value": value,
        "source_name": "SmartLab",
        "source_url": f"https://smart-lab.ru/q/{ticker}/f/y/",
        "load_method": "existing_smartlab_panel",
        "raw_field_name": raw_field_name,
        "normalized_field": meta["field"],
        "backfill_status": status,
        "backfill_reason": reason,
        "loaded_at": now,
    }


def build_smartlab_gap_report(panel: pd.DataFrame, cleaned: pd.DataFrame, mappings: list[dict]) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame(columns=GAP_COLUMNS)
    base = clean_base_lookup(cleaned)
    loaded_rows = {
        (str(r.ticker).upper(), int(r.year), str(r.field)): r
        for r in cleaned.itertuples()
    } if not cleaned.empty else {}
    rows: list[dict] = []
    for _, rec in panel.iterrows():
        ticker = canonical_ticker(rec.get("ticker"))
        year = _int_or_none(rec.get("year"))
        if not ticker or year is None:
            continue
        for meta in mappings:
            field = str(meta["field"])
            key = (ticker, year, field)
            loaded = loaded_rows.get(key)
            site_has_value = key in base
            raw_field, raw_value = raw_value_for_meta(rec, meta)
            raw_has = raw_value is not None
            alias_columns_present = any(alias in rec.index for alias in raw_aliases(meta))
            can_calc = False
            if meta.get("calculation_formula"):
                calc, reason = calculate_formula_value(meta, ticker, year, base, rec)
                can_calc = calc is not None
            missing_reason = ""
            if not site_has_value:
                missing_reason = missing_reason_for_gap(meta, loaded, raw_has, raw_field, can_calc, alias_columns_present)
            rows.append({
                "ticker": ticker,
                "year": year,
                "field": field,
                "display_name_ru": meta.get("site_display_name_ru", field),
                "site_has_value": bool(site_has_value),
                "raw_smartlab_has_value": bool(raw_has),
                "cleaned_layer_has_value": bool(loaded is not None and pd.notna(getattr(loaded, "clean_value", None))),
                "can_be_calculated": bool(can_calc and not site_has_value),
                "missing_reason": missing_reason,
                "next_action": next_action_for_gap(missing_reason),
            })
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


def missing_reason_for_gap(
    meta: dict,
    loaded,
    raw_has: bool,
    raw_field: str | None,
    can_calc: bool,
    alias_columns_present: bool,
) -> str:
    if loaded is not None and (
        bool(getattr(loaded, "excluded_from_site", False))
        or bool(getattr(loaded, "needs_manual_review", False))
        or str(getattr(loaded, "quality_status", "")) != "clean"
    ):
        return "blocked_by_quality_rule"
    if can_calc:
        return "can_calculate_from_base_facts"
    aliases = raw_aliases(meta)
    if raw_has and raw_field not in aliases:
        return "raw_field_name_not_mapped"
    if raw_has:
        return "raw_field_name_not_mapped"
    if alias_columns_present:
        return "mapped_but_null"
    if meta.get("calculation_formula"):
        return "missing_base_facts"
    return "not_loaded_from_smartlab"


def next_action_for_gap(reason: str) -> str:
    return {
        "not_loaded_from_smartlab": "check SmartLab page/backfill source field",
        "raw_field_name_not_mapped": "add alias to smartlab_fundamentals_map.json",
        "mapped_but_null": "leave missing until SmartLab publishes value",
        "blocked_by_quality_rule": "manual source check before publishing",
        "can_calculate_from_base_facts": "calculate from clean base facts",
        "missing_base_facts": "load required base facts first",
        "not_available_on_smartlab": "leave unavailable",
        "ticker_mapping_issue": "check canonical ticker aliases",
        "period_standard_mismatch": "normalize period and standard",
    }.get(reason, "")


def smartlab_gap_summary(gap: pd.DataFrame, initial_gap: pd.DataFrame | None = None) -> dict:
    if gap.empty:
        return {
            "generated_at": utc_now_iso(),
            "total_expected_values": 0,
            "present_in_site": 0,
            "missing_in_site": 0,
            "missing_but_raw_available": 0,
            "missing_due_to_mapping": 0,
            "missing_can_be_calculated": 0,
            "missing_needs_smartlab_backfill": 0,
            "missing_not_available": 0,
            "top_missing_fields": {},
            "top_missing_companies": {},
        }
    missing = ~gap["site_has_value"].fillna(False).astype(bool)
    reason = gap["missing_reason"].fillna("").astype(str)
    initial = initial_gap if initial_gap is not None and not initial_gap.empty else gap
    initial_missing = ~initial["site_has_value"].fillna(False).astype(bool)
    return {
        "generated_at": utc_now_iso(),
        "total_expected_values": int(len(gap)),
        "present_in_site": int((~missing).sum()),
        "missing_in_site": int(missing.sum()),
        "missing_but_raw_available": int((missing & gap["raw_smartlab_has_value"].fillna(False).astype(bool)).sum()),
        "missing_due_to_mapping": int((missing & (reason == "raw_field_name_not_mapped")).sum()),
        "missing_can_be_calculated": int((initial_missing & initial["can_be_calculated"].fillna(False).astype(bool)).sum()),
        "missing_needs_smartlab_backfill": int((missing & reason.isin(["not_loaded_from_smartlab", "missing_base_facts"])).sum()),
        "missing_not_available": int((missing & reason.isin(["not_available_on_smartlab", "mapped_but_null"])).sum()),
        "top_missing_fields": {str(k): int(v) for k, v in gap[missing]["field"].value_counts().head(20).to_dict().items()},
        "top_missing_companies": {str(k): int(v) for k, v in gap[missing]["ticker"].value_counts().head(20).to_dict().items()},
    }


def sample_company_card_coverage(cleaned: pd.DataFrame, mappings: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    expected = len({str(m["field"]) for m in mappings})
    for ticker in SAMPLE_COMPANIES:
        company = cleaned[cleaned["ticker"].astype(str).str.upper() == ticker] if not cleaned.empty else pd.DataFrame()
        displayable = company[
            company["clean_value"].notna()
            & ~company["excluded_from_site"].fillna(False).astype(bool)
        ] if not company.empty else pd.DataFrame()
        rows.append({
            "ticker": ticker,
            "fundamentals_groups_count": int(displayable["statement_group"].nunique()) if not displayable.empty else 0,
            "displayable_metrics_count": int(displayable["field"].nunique()) if not displayable.empty else 0,
            "missing_metrics_count": int(max(expected - (displayable["field"].nunique() if not displayable.empty else 0), 0)),
            "calculated_metrics_count": int(displayable[displayable["source_status"] == "calculated_from_clean_base_facts"]["field"].nunique()) if not displayable.empty else 0,
            "backfilled_metrics_count": int(displayable[displayable["source_status"] == "mapped_from_raw_existing"]["field"].nunique()) if not displayable.empty else 0,
            "excluded_metrics_count": int(company[company["excluded_from_site"].fillna(False).astype(bool)]["field"].nunique()) if not company.empty else 0,
            "review_metrics_count": int(company[company["needs_manual_review"].fillna(False).astype(bool)]["field"].nunique()) if not company.empty else 0,
        })
    return pd.DataFrame(rows, columns=SAMPLE_COVERAGE_COLUMNS)


def smartlab_backfill_summary(backfilled: pd.DataFrame, gap_summary: dict) -> dict:
    statuses = backfilled["backfill_status"].fillna("").astype(str).value_counts().to_dict() if not backfilled.empty else {}
    return {
        "smartlab_backfilled_values": int(statuses.get("mapped_from_raw_existing", 0)),
        "smartlab_calculated_values": int(statuses.get("calculated_from_base_facts", 0)),
        "smartlab_backfill_failed": int(statuses.get("failed", 0)),
        "smartlab_gap_total_expected_values": int(gap_summary.get("total_expected_values", 0)),
        "smartlab_gap_present_in_site": int(gap_summary.get("present_in_site", 0)),
        "smartlab_gap_missing_in_site": int(gap_summary.get("missing_in_site", 0)),
        "smartlab_gap_missing_can_be_calculated": int(gap_summary.get("missing_can_be_calculated", 0)),
        "smartlab_gap_missing_needs_backfill": int(gap_summary.get("missing_needs_smartlab_backfill", 0)),
    }


def derived_values(rec: pd.Series) -> list[tuple[dict, float]]:
    out: list[tuple[dict, float]] = []
    market_cap = _num_or_none(rec.get("market_cap_mln"))
    price = _num_or_none(rec.get("price_end"))
    shares = None
    if market_cap is not None and market_cap > 0 and price is not None and price > 0:
        shares = market_cap * 1_000_000 / price
        out.append((DERIVED_FIELDS[1], shares))
    debt = _num_or_none(rec.get("total_debt_mln"))
    assets = _num_or_none(rec.get("assets_mln"))
    if debt is not None and assets is not None and assets != 0:
        out.append((DERIVED_FIELDS[0], debt / assets * 100))
    if shares is not None and shares > 0:
        for src, meta in [
            ("net_profit_mln", DERIVED_FIELDS[2]),
            ("equity_mln", DERIVED_FIELDS[3]),
            ("revenue_mln", DERIVED_FIELDS[4]),
            ("FCF", DERIVED_FIELDS[5]),
        ]:
            value = _num_or_none(rec.get(src))
            if value is not None and (meta.get("can_be_negative", False) or value >= 0):
                out.append((meta, value * 1_000_000 / shares))
    return out


def _clean_row(
    ticker: str,
    year: int,
    raw_field: str,
    raw: float,
    meta: dict,
    now: str,
    calculated: bool = False,
    source_status: str | None = None,
) -> dict:
    status, reason, review, excluded, exclude_score = quality_decision(meta, raw)
    clean = None if excluded else raw
    source_status = source_status or ("calculated_from_clean_base_facts" if calculated else "smartlab_fallback")
    return {
        "ticker": ticker,
        "year": year,
        "period": str(year),
        "standard": "IFRS",
        "field": meta["field"],
        "display_name_ru": meta.get("site_display_name_ru", meta.get("display_name_ru", meta["field"])),
        "statement_group": meta["statement_group"],
        "statement_type": meta["statement_type"],
        "raw_field": raw_field,
        "raw_value": raw,
        "clean_value": clean,
        "unit": meta["unit"],
        "scale": int(meta["scale"]),
        "display_format": meta["display_format"],
        "chart_group": meta["chart_group"],
        "sort_order": int(meta["sort_order"]),
        "source_name": "SmartLab",
        "source_url": f"https://smart-lab.ru/q/{ticker}/f/y/",
        "source_status": source_status,
        "quality_status": status,
        "quality_reason": reason,
        "needs_manual_review": review,
        "excluded_from_site": excluded,
        "exclude_from_score": exclude_score,
        "corrected_by": "",
        "correction_source": "",
        "audit_timestamp": now,
    }


def quality_decision(meta: dict, raw: float) -> tuple[str, str, bool, bool, bool]:
    field = meta["field"]
    if not meta.get("can_be_negative", False) and raw < 0:
        return "negative_value_blocked", f"{field} cannot be negative", True, True, True
    if field == "shares_outstanding" and raw <= 0:
        return "impossible_value_blocked", "shares outstanding must be positive", True, True, True
    if field == "dividend_yield":
        if raw > 100:
            return "ratio_extreme_blocked", "dividend yield above 100%", True, True, True
        if raw > 30:
            return "ratio_high_review", "dividend yield above 30%", True, False, True
    if field == "payout_ratio" and raw > 200:
        return "ratio_high_review", "payout ratio above 200%", True, False, True
    if field == "roe":
        if abs(raw) > 1000:
            return "ratio_extreme_blocked", "ROE excluded: extreme value or unreliable denominator", True, True, True
        if abs(raw) > 300:
            return "ratio_high_review", "ROE above 300% requires review", True, False, True
    if field == "roa" and raw > 100:
        return "ratio_high_review", "ROA above 100% requires review", True, False, True
    if field in {"net_margin", "ebitda_margin"}:
        if abs(raw) > 1000:
            return "ratio_extreme_blocked", f"{field} above 1000%", True, True, True
        if abs(raw) > 100:
            return "ratio_high_review", f"{field} above 100%", True, False, True
    if field == "debt_to_assets" and raw > 100:
        return "ratio_high_review", "debt/assets above 100%", True, False, True
    if field == "net_debt_to_ebitda" and raw > 20:
        return "ratio_high_review", "net debt/EBITDA above 20x", True, False, True
    if field == "pe" and raw > 100:
        return "ratio_high_review", "P/E above 100x", True, False, True
    if field == "pb" and raw < 0:
        return "negative_equity_warning", "P/B below zero can indicate negative equity", True, False, True
    return "clean", "", False, False, False


def apply_time_series_reviews(cleaned: pd.DataFrame) -> pd.DataFrame:
    if cleaned.empty:
        return cleaned
    out = cleaned.copy()
    reviewable = (
        (out["quality_status"] == "clean")
        & out["display_format"].isin(["money_mln", "rub", "shares"])
        & ~out["field"].isin({"market_cap", "enterprise_value", "shares_outstanding"})
    )
    for (_ticker, field), idx in out[reviewable].groupby(["ticker", "field"]).groups.items():
        series = out.loc[list(idx)].sort_values("year")
        values = series["clean_value"].astype(float)
        years = series["year"].astype(int)
        if len(series) < 2:
            continue
        prev_values = values.shift(1)
        next_values = values.shift(-1)
        bad_idx = set()
        for pos, row_idx in enumerate(series.index):
            value = values.iloc[pos]
            if value == 0:
                continue
            prev = prev_values.iloc[pos]
            nxt = next_values.iloc[pos]
            prev_bad = pd.notna(prev) and abs(prev) > 0 and (abs(value) / abs(prev) > 10 or abs(value) / abs(prev) < 0.1)
            next_bad = pd.notna(nxt) and abs(nxt) > 0 and (abs(value) / abs(nxt) > 10 or abs(value) / abs(nxt) < 0.1)
            isolated_edge = (pos == 0 and next_bad) or (pos == len(series) - 1 and prev_bad)
            isolated_middle = prev_bad and next_bad
            if isolated_edge or isolated_middle:
                bad_idx.add(row_idx)
        for row_idx in bad_idx:
            year = int(out.at[row_idx, "year"])
            out.at[row_idx, "quality_status"] = "yoy_scale_review"
            out.at[row_idx, "quality_reason"] = f"{field} has >10x YoY scale jump/drop around {year}; source check required"
            out.at[row_idx, "needs_manual_review"] = True
            out.at[row_idx, "exclude_from_score"] = True
    return out


def fundamental_issues(cleaned: pd.DataFrame) -> list[dict]:
    if cleaned.empty:
        return []
    bad = cleaned[cleaned["quality_status"] != "clean"]
    rows = []
    for _, r in bad.iterrows():
        rows.append({
            "ticker": r.get("ticker"),
            "year": _int_or_none(r.get("year")),
            "field": r.get("field"),
            "raw_value": r.get("raw_value"),
            "clean_value": r.get("clean_value"),
            "issue_type": r.get("quality_status"),
            "quality_status": r.get("quality_status"),
            "quality_reason": r.get("quality_reason"),
            "suggested_action": "source_check_or_leave_hidden" if r.get("excluded_from_site") else "review_before_scoring",
            "source_name": r.get("source_name"),
            "source_url": r.get("source_url"),
            "needs_manual_review": bool(r.get("needs_manual_review")),
            "excluded_from_site": bool(r.get("excluded_from_site")),
        })
    return rows


def smartlab_cleaned_summary(cleaned: pd.DataFrame) -> dict:
    if cleaned.empty:
        return {
            "generated_at": utc_now_iso(),
            "smartlab_fields_loaded": 0,
            "smartlab_companies_loaded": 0,
            "smartlab_company_year_rows": 0,
            "smartlab_fundamental_values_total": 0,
            "smartlab_fundamental_values_clean": 0,
            "smartlab_fundamental_values_excluded": 0,
            "smartlab_ratio_values_blocked": 0,
            "smartlab_values_needs_review": 0,
            "smartlab_missing_values": 0,
            "smartlab_corrected_confirmed": 0,
        }
    excluded = cleaned["excluded_from_site"].fillna(False).astype(bool)
    review = cleaned["needs_manual_review"].fillna(False).astype(bool)
    ratio_blocked = cleaned["quality_status"].fillna("").astype(str).str.contains("ratio_") & excluded
    return {
        "generated_at": utc_now_iso(),
        "smartlab_fields_loaded": int(cleaned["field"].nunique()),
        "smartlab_companies_loaded": int(cleaned["ticker"].nunique()),
        "smartlab_company_year_rows": int(cleaned[["ticker", "year"]].drop_duplicates().shape[0]),
        "smartlab_fundamental_values_total": int(len(cleaned)),
        "smartlab_fundamental_values_clean": int((cleaned["quality_status"] == "clean").sum()),
        "smartlab_fundamental_values_excluded": int(excluded.sum()),
        "smartlab_ratio_values_blocked": int(ratio_blocked.sum()),
        "smartlab_values_needs_review": int(review.sum()),
        "smartlab_missing_values": 0,
        "smartlab_corrected_confirmed": int((cleaned["quality_status"] == "corrected_confirmed").sum()),
    }


def _num_or_none(value) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    if pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = write_smartlab_fundamentals_cleaned(Path(args.repo_root))
    print(
        "[fundamentals-cleaned] "
        f"values={summary['smartlab_fundamental_values_total']} "
        f"clean={summary['smartlab_fundamental_values_clean']} "
        f"excluded={summary['smartlab_fundamental_values_excluded']} "
        f"review={summary['smartlab_values_needs_review']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
