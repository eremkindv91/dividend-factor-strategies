from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_csv, write_json, write_parquet
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
    now = now or utc_now_iso()
    panel_rel = PANEL_PRIMARY if (root / PANEL_PRIMARY).exists() else PANEL_FALLBACK
    panel_path = root / panel_rel
    if not panel_path.exists():
        return pd.DataFrame(columns=CLEANED_COLUMNS)
    panel = pd.read_csv(panel_path)
    if not {"ticker", "year"}.issubset(panel.columns):
        raise ValueError(f"{panel_rel}: required columns missing")
    mappings = load_fundamental_map(root)
    rows: list[dict] = []
    for _, rec in panel.iterrows():
        ticker = str(rec.get("ticker") or "").strip().upper()
        year = _int_or_none(rec.get("year"))
        if not ticker or year is None:
            continue
        for meta in mappings:
            raw_field = meta["raw_field"]
            if raw_field not in panel.columns:
                continue
            raw = _num_or_none(rec.get(raw_field))
            if raw is None:
                continue
            rows.append(_clean_row(ticker, year, raw_field, raw, meta, now))
        for meta, raw in derived_values(rec):
            rows.append(_clean_row(ticker, year, f"calculated:{meta['field']}", raw, meta, now, calculated=True))
    out = pd.DataFrame(rows, columns=CLEANED_COLUMNS)
    if out.empty:
        return out
    out = out.sort_values(["ticker", "year", "statement_group", "sort_order", "field"]).reset_index(drop=True)
    return apply_time_series_reviews(out)


def write_smartlab_fundamentals_cleaned(root: Path = REPO_ROOT) -> dict:
    cleaned = build_smartlab_fundamentals_cleaned(root)
    write_parquet(root / "data" / "processed" / "smartlab_fundamentals_cleaned.parquet", cleaned)
    issues = fundamental_issues(cleaned)
    write_csv(root / "data" / "manual_review" / "fundamental_data_issues.csv", issues, ISSUE_COLUMNS)
    summary = smartlab_cleaned_summary(cleaned)
    write_json(root / "data" / "quality" / "smartlab_cleaned_layer_summary.json", summary)
    return summary


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


def _clean_row(ticker: str, year: int, raw_field: str, raw: float, meta: dict, now: str, calculated: bool = False) -> dict:
    status, reason, review, excluded, exclude_score = quality_decision(meta, raw)
    clean = None if excluded else raw
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
        "source_status": "calculated_from_smartlab_base_facts" if calculated else "smartlab_fallback",
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
