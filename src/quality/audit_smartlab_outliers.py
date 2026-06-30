from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_csv, write_json
from src.paths import REPO_ROOT


PANEL_PRIMARY = Path("data/panels_final/panel_russia_final_smartlab.csv")
PANEL_FALLBACK = Path("data/panels_final/panel_russia_final.csv")

OUTLIER_COLUMNS = [
    "ticker",
    "year",
    "sector",
    "field",
    "value",
    "previous_year",
    "previous_value",
    "ratio",
    "issue",
    "severity",
    "audit_status",
    "audit_reason",
    "source_file",
    "created_at",
]

TRIAGE_COLUMNS = OUTLIER_COLUMNS + [
    "triage_status",
    "priority",
    "suspect_year",
    "suspect_field",
    "suspect_value",
    "suggested_scale_divisor",
    "candidate_corrected_value",
    "candidate_ratio_after_correction",
    "suggested_action",
    "batch_key",
    "triage_reason",
]

BATCH_COLUMNS = [
    "batch_key",
    "ticker",
    "suspect_year",
    "triage_status",
    "priority",
    "rows",
    "fields",
    "suggested_scale_divisor",
    "candidate_corrected_values",
    "max_candidate_ratio_after_correction",
    "suggested_action",
    "triage_reason",
]

UNIT_MISMATCH_REVIEW_COLUMNS = [
    "batch_key",
    "ticker",
    "suspect_year",
    "review_status",
    "priority",
    "rows",
    "fields",
    "suggested_scale_divisor",
    "candidate_corrected_values",
    "max_candidate_ratio_after_correction",
    "do_not_auto_correct",
    "suggested_action",
    "audit_status",
    "audit_reason",
]

UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS = [
    "ticker",
    "fiscal_year",
    "period",
    "reporting_standard",
    "statement_type",
    "line_item_std",
    "value_normalized",
    "currency",
    "unit_type",
    "unit_multiplier",
    "source_url",
    "reason",
    "source_verification_status",
    "ready_for_manual_override",
    "batch_key",
    "field",
    "original_value_mln",
    "candidate_value_mln",
    "review_status",
    "suggested_action",
]

NONNEGATIVE_FIELDS = {
    "revenue_mln",
    "assets_mln",
    "total_debt_mln",
    "cash_mln",
    "market_cap_mln",
    "ev_mln",
}

YOY_FIELDS = [
    "revenue_mln",
    "ebitda_mln",
    "net_profit_mln",
    "equity_mln",
    "assets_mln",
    "net_debt_mln",
    "total_debt_mln",
    "cash_mln",
    "CFO_",
    "CAPEX_",
    "FCF",
    "market_cap_mln",
]

RATIO_FIELDS = [
    "roe_pct",
    "roa_pct",
    "net_margin_pct",
    "ebitda_margin_pct",
    "payout_ratio_pct",
]

FIELD_TO_MANUAL_FACT = {
    "revenue_mln": ("revenue", "income_statement"),
    "ebitda_mln": ("ebitda", "income_statement"),
    "net_profit_mln": ("net_income", "income_statement"),
    "equity_mln": ("total_equity", "balance_sheet"),
    "assets_mln": ("total_assets", "balance_sheet"),
    "net_debt_mln": ("net_debt", "balance_sheet"),
    "total_debt_mln": ("total_debt", "balance_sheet"),
    "cash_mln": ("cash_and_equivalents", "balance_sheet"),
    "CFO_": ("operating_cash_flow", "cash_flow"),
    "CAPEX_": ("capex", "cash_flow"),
    "FCF": ("free_cash_flow", "cash_flow"),
    "market_cap_mln": ("market_cap", "market_data"),
}


def build_smartlab_outlier_audit(root: Path = REPO_ROOT, now: str | None = None) -> pd.DataFrame:
    now = now or utc_now_iso()
    panel_rel = PANEL_PRIMARY if (root / PANEL_PRIMARY).exists() else PANEL_FALLBACK
    panel_path = root / panel_rel
    if not panel_path.exists():
        return pd.DataFrame(columns=OUTLIER_COLUMNS)

    panel = pd.read_csv(panel_path)
    required = {"ticker", "year"}
    if not required.issubset(panel.columns):
        missing = ", ".join(sorted(required - set(panel.columns)))
        raise ValueError(f"{panel_rel}: required columns missing: {missing}")

    df = panel.copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    rows: list[dict] = []

    for _, rec in df.iterrows():
        ticker = rec.get("ticker")
        year = _int_or_none(rec.get("year"))
        if not ticker or year is None:
            continue
        sector = rec.get("sector") if "sector" in df.columns else ""
        for field in NONNEGATIVE_FIELDS:
            if field not in df.columns:
                continue
            value = _num_or_none(rec.get(field))
            if value is not None and value < 0:
                rows.append(outlier_row(ticker, year, sector, field, value, None, None, None, "negative_value", "high", f"{field} is negative", panel_rel, now))

        assets = _num_or_none(rec.get("assets_mln")) if "assets_mln" in df.columns else None
        cash = _num_or_none(rec.get("cash_mln")) if "cash_mln" in df.columns else None
        debt = _num_or_none(rec.get("total_debt_mln")) if "total_debt_mln" in df.columns else None
        if assets is not None and assets > 0 and cash is not None and cash > assets * 1.05:
            ratio = cash / assets
            rows.append(outlier_row(ticker, year, sector, "cash_mln", cash, None, assets, ratio, "cash_exceeds_assets", "high", "cash is more than 105% of total assets", panel_rel, now))
        if assets is not None and assets > 0 and debt is not None and debt > assets * 1.5:
            ratio = debt / assets
            rows.append(outlier_row(ticker, year, sector, "total_debt_mln", debt, None, assets, ratio, "debt_exceeds_assets", "medium", "total debt is more than 150% of total assets", panel_rel, now))

        for field in RATIO_FIELDS:
            if field not in df.columns:
                continue
            value = _num_or_none(rec.get(field))
            if value is not None and abs(value) > 500:
                rows.append(outlier_row(ticker, year, sector, field, value, None, None, None, "extreme_ratio", "medium", f"{field} absolute value is above 500%", panel_rel, now))

    for field in YOY_FIELDS:
        if field not in df.columns:
            continue
        for _ticker, group in df[["ticker", "year", field]].dropna(subset=["year"]).sort_values(["ticker", "year"]).groupby("ticker"):
            prev_year = None
            prev_value = None
            for _, rec in group.iterrows():
                year = _int_or_none(rec.get("year"))
                value = _num_or_none(rec.get(field))
                if year is None or value is None:
                    continue
                if prev_year is not None and prev_value is not None:
                    ratio = yoy_ratio(value, prev_value)
                    if ratio is not None and ratio >= 10 and abs(value - prev_value) >= 1_000:
                        sector = _sector_for(df, _ticker, year)
                        severity = "high" if ratio >= 50 else "medium"
                        rows.append(outlier_row(_ticker, year, sector, field, value, prev_year, prev_value, ratio, "yoy_jump_gt_10x", severity, f"{field} changed by more than 10x year over year", panel_rel, now))
                prev_year = year
                prev_value = value

    out = pd.DataFrame(rows, columns=OUTLIER_COLUMNS)
    if out.empty:
        return out
    out = out.drop_duplicates(["ticker", "year", "field", "issue"], keep="first")
    return out.sort_values(["severity", "ticker", "year", "field", "issue"], ascending=[True, True, True, True, True]).reset_index(drop=True)


def write_smartlab_outlier_audit(root: Path = REPO_ROOT) -> dict:
    audit = build_smartlab_outlier_audit(root)
    rows = audit.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "smartlab_panel_outliers.csv", rows, OUTLIER_COLUMNS)
    summary = smartlab_outlier_summary(audit)
    write_json(root / "data" / "manual_review" / "smartlab_panel_outliers_summary.json", {"meta": summary, "rows": rows})
    return summary


def build_smartlab_outlier_triage(root: Path = REPO_ROOT, now: str | None = None) -> pd.DataFrame:
    audit = build_smartlab_outlier_audit(root, now=now)
    if audit.empty:
        return pd.DataFrame(columns=TRIAGE_COLUMNS)

    rows = []
    for _, row in audit.iterrows():
        rows.append(triage_outlier(row))
    out = pd.DataFrame(rows, columns=TRIAGE_COLUMNS)
    return out.sort_values(["priority", "batch_key", "field"], ascending=[True, True, True]).reset_index(drop=True)


def write_smartlab_outlier_triage(root: Path = REPO_ROOT) -> dict:
    triage = build_smartlab_outlier_triage(root)
    rows = triage.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "smartlab_panel_outlier_triage.csv", rows, TRIAGE_COLUMNS)
    batches = build_smartlab_outlier_batches(triage)
    batch_rows = batches.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "smartlab_panel_outlier_batches.csv", batch_rows, BATCH_COLUMNS)
    write_json(root / "data" / "manual_review" / "smartlab_panel_outlier_batches_summary.json", {"meta": smartlab_batch_summary(batches), "rows": batch_rows})
    review = build_smartlab_unit_mismatch_review(batches)
    review_rows = review.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "smartlab_unit_mismatch_review.csv", review_rows, UNIT_MISMATCH_REVIEW_COLUMNS)
    review_summary = smartlab_unit_mismatch_review_summary(review)
    write_json(root / "data" / "manual_review" / "smartlab_unit_mismatch_review_summary.json", {"meta": review_summary, "rows": review_rows})
    candidates = build_smartlab_unit_mismatch_override_candidates(triage, review)
    candidate_rows = candidates.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "smartlab_unit_mismatch_override_candidates.csv", candidate_rows, UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS)
    candidate_summary = smartlab_unit_mismatch_override_candidate_summary(candidates)
    write_json(root / "data" / "manual_review" / "smartlab_unit_mismatch_override_candidates_summary.json", {"meta": candidate_summary, "rows": candidate_rows})
    summary = smartlab_triage_summary(triage)
    summary.update({
        "review_batches": review_summary["review_batches"],
        "review_probable_full_company_unit_mismatch": review_summary["probable_full_company_unit_mismatch"],
        "review_single_field_needs_source_check": review_summary["single_field_needs_source_check"],
        "review_candidate_rows": review_summary["candidate_rows"],
        "review_do_not_auto_correct": review_summary["do_not_auto_correct"],
        "override_candidate_rows": candidate_summary["candidate_rows"],
        "override_candidate_ready": candidate_summary["ready_for_manual_override"],
        "override_candidate_companies": candidate_summary["companies"],
    })
    write_json(root / "data" / "manual_review" / "smartlab_panel_outlier_triage_summary.json", {"meta": summary, "rows": rows})
    return summary


def build_smartlab_outlier_batches(triage: pd.DataFrame) -> pd.DataFrame:
    if triage.empty:
        return pd.DataFrame(columns=BATCH_COLUMNS)
    unit = triage[triage["triage_status"] == "suspected_unit_mismatch"].copy()
    if unit.empty:
        return pd.DataFrame(columns=BATCH_COLUMNS)
    rows = []
    for batch_key, group in unit.groupby("batch_key", sort=True):
        first = group.iloc[0]
        corrected_values = []
        for _, row in group.sort_values("field").iterrows():
            corrected_values.append(f"{row.get('field')}={_format_num(row.get('candidate_corrected_value'))}")
        rows.append({
            "batch_key": batch_key,
            "ticker": first.get("ticker"),
            "suspect_year": _int_or_none(first.get("suspect_year")),
            "triage_status": first.get("triage_status"),
            "priority": _int_or_none(first.get("priority")),
            "rows": int(len(group)),
            "fields": ",".join(sorted(group["field"].dropna().astype(str).unique().tolist())),
            "suggested_scale_divisor": _int_or_none(first.get("suggested_scale_divisor")),
            "candidate_corrected_values": ";".join(corrected_values),
            "max_candidate_ratio_after_correction": float(group["candidate_ratio_after_correction"].max()),
            "suggested_action": first.get("suggested_action"),
            "triage_reason": first.get("triage_reason"),
        })
    return pd.DataFrame(rows, columns=BATCH_COLUMNS)


def build_smartlab_unit_mismatch_review(triage_or_batches: pd.DataFrame) -> pd.DataFrame:
    if triage_or_batches.empty:
        return pd.DataFrame(columns=UNIT_MISMATCH_REVIEW_COLUMNS)
    if set(BATCH_COLUMNS).issubset(triage_or_batches.columns):
        batches = triage_or_batches
    else:
        batches = build_smartlab_outlier_batches(triage_or_batches)
    if batches.empty:
        return pd.DataFrame(columns=UNIT_MISMATCH_REVIEW_COLUMNS)

    rows = []
    for _, batch in batches.iterrows():
        field_names = _field_set(batch.get("fields"))
        row_count = _int_or_none(batch.get("rows")) or 0
        if row_count >= 8 and len(field_names) >= 8:
            review_status = "probable_full_company_unit_mismatch"
            priority = 1
            suggested_action = "verify_source_then_create_manual_override"
            reason = "many fields in one ticker-year look scaled by x1000; verify source before any override"
        else:
            review_status = "single_field_needs_source_check"
            priority = 2
            suggested_action = "manual_source_check_only"
            reason = "isolated field mismatch; do not infer a company-year scale correction"
        rows.append({
            "batch_key": batch.get("batch_key"),
            "ticker": batch.get("ticker"),
            "suspect_year": _int_or_none(batch.get("suspect_year")),
            "review_status": review_status,
            "priority": priority,
            "rows": row_count,
            "fields": batch.get("fields"),
            "suggested_scale_divisor": _int_or_none(batch.get("suggested_scale_divisor")),
            "candidate_corrected_values": batch.get("candidate_corrected_values"),
            "max_candidate_ratio_after_correction": _num_or_none(batch.get("max_candidate_ratio_after_correction")),
            "do_not_auto_correct": True,
            "suggested_action": suggested_action,
            "audit_status": "needs_source_verification",
            "audit_reason": reason,
        })
    return pd.DataFrame(rows, columns=UNIT_MISMATCH_REVIEW_COLUMNS).sort_values(
        ["priority", "ticker", "suspect_year"],
        ascending=[True, True, True],
    ).reset_index(drop=True)


def smartlab_unit_mismatch_review_summary(review: pd.DataFrame) -> dict:
    if review.empty:
        return {
            "generated_at": utc_now_iso(),
            "review_batches": 0,
            "probable_full_company_unit_mismatch": 0,
            "single_field_needs_source_check": 0,
            "candidate_rows": 0,
            "do_not_auto_correct": True,
            "companies": [],
        }
    return {
        "generated_at": utc_now_iso(),
        "review_batches": int(len(review)),
        "probable_full_company_unit_mismatch": int((review["review_status"] == "probable_full_company_unit_mismatch").sum()),
        "single_field_needs_source_check": int((review["review_status"] == "single_field_needs_source_check").sum()),
        "candidate_rows": int(review["rows"].sum()),
        "do_not_auto_correct": bool(review["do_not_auto_correct"].all()),
        "companies": sorted(review["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def build_smartlab_unit_mismatch_override_candidates(triage: pd.DataFrame, review: pd.DataFrame) -> pd.DataFrame:
    if triage.empty or review.empty:
        return pd.DataFrame(columns=UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS)
    approved_batches = set(
        review.loc[
            review["review_status"] == "probable_full_company_unit_mismatch",
            "batch_key",
        ].dropna().astype(str)
    )
    if not approved_batches:
        return pd.DataFrame(columns=UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS)
    unit = triage[
        triage["triage_status"].eq("suspected_unit_mismatch")
        & triage["batch_key"].astype(str).isin(approved_batches)
    ].copy()
    if unit.empty:
        return pd.DataFrame(columns=UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS)

    review_by_batch = review.set_index("batch_key").to_dict(orient="index")
    rows = []
    for _, row in unit.sort_values(["ticker", "suspect_year", "field"]).iterrows():
        field = str(row.get("field") or "")
        fact = FIELD_TO_MANUAL_FACT.get(field)
        if not fact:
            continue
        line_item, statement_type = fact
        fiscal_year = _int_or_none(row.get("suspect_year"))
        candidate_value_mln = _num_or_none(row.get("candidate_corrected_value"))
        if fiscal_year is None or candidate_value_mln is None:
            continue
        batch_key = str(row.get("batch_key") or "")
        review_row = review_by_batch.get(batch_key, {})
        rows.append({
            "ticker": row.get("ticker"),
            "fiscal_year": fiscal_year,
            "period": str(fiscal_year),
            "reporting_standard": "IFRS",
            "statement_type": statement_type,
            "line_item_std": line_item,
            "value_normalized": candidate_value_mln * 1_000_000,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1,
            "source_url": "",
            "reason": "candidate x1000 SmartLab unit correction; requires official source verification before copying to data/manual_overrides/financial_facts.csv",
            "source_verification_status": "needs_source_verification",
            "ready_for_manual_override": False,
            "batch_key": batch_key,
            "field": field,
            "original_value_mln": _num_or_none(row.get("suspect_value")),
            "candidate_value_mln": candidate_value_mln,
            "review_status": review_row.get("review_status", ""),
            "suggested_action": review_row.get("suggested_action", ""),
        })
    return pd.DataFrame(rows, columns=UNIT_MISMATCH_OVERRIDE_CANDIDATE_COLUMNS).reset_index(drop=True)


def smartlab_unit_mismatch_override_candidate_summary(candidates: pd.DataFrame) -> dict:
    if candidates.empty:
        return {
            "generated_at": utc_now_iso(),
            "candidate_rows": 0,
            "ready_for_manual_override": 0,
            "companies": [],
        }
    return {
        "generated_at": utc_now_iso(),
        "candidate_rows": int(len(candidates)),
        "ready_for_manual_override": int(candidates["ready_for_manual_override"].sum()),
        "companies": sorted(candidates["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def smartlab_batch_summary(batches: pd.DataFrame) -> dict:
    if batches.empty:
        return {
            "generated_at": utc_now_iso(),
            "batch_count": 0,
            "rows": 0,
            "companies": [],
        }
    return {
        "generated_at": utc_now_iso(),
        "batch_count": int(len(batches)),
        "rows": int(batches["rows"].sum()),
        "companies": sorted(batches["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def smartlab_outlier_summary(audit: pd.DataFrame) -> dict:
    if audit.empty:
        return {
            "generated_at": utc_now_iso(),
            "total_outliers": 0,
            "high_severity": 0,
            "medium_severity": 0,
            "by_issue": {},
            "by_field": {},
            "companies": [],
        }
    return {
        "generated_at": utc_now_iso(),
        "total_outliers": int(len(audit)),
        "high_severity": int((audit["severity"] == "high").sum()),
        "medium_severity": int((audit["severity"] == "medium").sum()),
        "by_issue": _counts(audit["issue"]),
        "by_field": _counts(audit["field"]),
        "companies": sorted(audit["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def smartlab_triage_summary(triage: pd.DataFrame) -> dict:
    if triage.empty:
        return {
            "generated_at": utc_now_iso(),
            "total_triage_rows": 0,
            "suspected_unit_mismatch": 0,
            "high_priority": 0,
            "batch_count": 0,
            "by_status": {},
            "by_issue": {},
            "companies": [],
        }
    unit = triage[triage["triage_status"] == "suspected_unit_mismatch"]
    return {
        "generated_at": utc_now_iso(),
        "total_triage_rows": int(len(triage)),
        "suspected_unit_mismatch": int(len(unit)),
        "high_priority": int((triage["priority"] == 1).sum()),
        "batch_count": int(unit["batch_key"].nunique()),
        "by_status": _counts(triage["triage_status"]),
        "by_issue": _counts(triage["issue"]),
        "companies": sorted(triage["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def triage_outlier(row: pd.Series) -> dict:
    base = {col: row.get(col) for col in OUTLIER_COLUMNS}
    unit_candidate = unit_mismatch_candidate(row)
    if unit_candidate:
        suspect_year, suspect_value, divisor, corrected_value, corrected_ratio = unit_candidate
        triage_status = "suspected_unit_mismatch"
        priority = 1
        batch_key = f"unit_mismatch:{row.get('ticker')}:{suspect_year}"
        reason = "scaled value divided by 1000 is consistent with adjacent year"
    else:
        suspect_year = _int_or_none(row.get("year"))
        suspect_value = _num_or_none(row.get("value"))
        divisor = None
        corrected_value = None
        corrected_ratio = None
        triage_status, priority, reason = non_unit_triage(row)
        batch_key = f"{triage_status}:{row.get('ticker')}:{suspect_year}"

    base.update({
        "triage_status": triage_status,
        "priority": priority,
        "suspect_year": suspect_year,
        "suspect_field": row.get("field"),
        "suspect_value": suspect_value,
        "suggested_scale_divisor": divisor,
        "candidate_corrected_value": corrected_value,
        "candidate_ratio_after_correction": corrected_ratio,
        "suggested_action": "manual_source_check_before_panel_edit",
        "batch_key": batch_key,
        "triage_reason": reason,
    })
    return base


def unit_mismatch_candidate(row: pd.Series) -> tuple[int, float, int, float, float] | None:
    if row.get("issue") != "yoy_jump_gt_10x":
        return None
    year = _int_or_none(row.get("year"))
    previous_year = _int_or_none(row.get("previous_year"))
    value = _num_or_none(row.get("value"))
    previous_value = _num_or_none(row.get("previous_value"))
    if year is None or previous_year is None or value is None or previous_value is None:
        return None

    candidates: list[tuple[float, int, float, int, float]] = []
    previous_scaled = previous_value / 1000
    previous_ratio = yoy_ratio(value, previous_scaled)
    if previous_ratio is not None and previous_ratio <= 5 and abs(previous_value) >= 100_000:
        candidates.append((previous_ratio, previous_year, previous_value, 1000, previous_scaled))

    value_scaled = value / 1000
    value_ratio = yoy_ratio(value_scaled, previous_value)
    if value_ratio is not None and value_ratio <= 5 and abs(value) >= 100_000:
        candidates.append((value_ratio, year, value, 1000, value_scaled))

    if not candidates:
        return None
    ratio, suspect_year, suspect_value, divisor, corrected_value = sorted(candidates, key=lambda item: item[0])[0]
    return suspect_year, suspect_value, divisor, corrected_value, ratio


def non_unit_triage(row: pd.Series) -> tuple[str, int, str]:
    issue = str(row.get("issue") or "")
    severity = str(row.get("severity") or "")
    if issue == "negative_value":
        return "invalid_negative_review", 1, "nonnegative field is negative"
    if issue in {"cash_exceeds_assets", "debt_exceeds_assets"}:
        return "balance_sheet_consistency_review", 2, "balance sheet relation failed"
    if issue == "extreme_ratio":
        return "ratio_formula_review", 3, "ratio value is outside expected range"
    if issue == "yoy_jump_gt_10x":
        return "large_yoy_move_review", 2 if severity == "high" else 3, "large year-over-year move requires source check"
    return "needs_review", 3, "outlier requires manual review"


def outlier_row(
    ticker: str,
    year: int,
    sector,
    field: str,
    value: float,
    previous_year: int | None,
    previous_value: float | None,
    ratio: float | None,
    issue: str,
    severity: str,
    reason: str,
    source_file: Path,
    now: str,
) -> dict:
    return {
        "ticker": ticker,
        "year": year,
        "sector": "" if pd.isna(sector) else sector,
        "field": field,
        "value": value,
        "previous_year": previous_year,
        "previous_value": previous_value,
        "ratio": ratio,
        "issue": issue,
        "severity": severity,
        "audit_status": "needs_review",
        "audit_reason": reason,
        "source_file": str(source_file),
        "created_at": now,
    }


def yoy_ratio(value: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return max(abs(value / previous), abs(previous / value)) if value != 0 else None


def _sector_for(df: pd.DataFrame, ticker: str, year: int):
    if "sector" not in df.columns:
        return ""
    rows = df[(df["ticker"] == ticker) & (df["year"] == year)]
    if rows.empty:
        return ""
    return rows.iloc[0].get("sector", "")


def _counts(series: pd.Series) -> dict:
    return {str(k): int(v) for k, v in series.fillna("Missing").value_counts().to_dict().items()}


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


def _format_num(value) -> str:
    num = _num_or_none(value)
    if num is None:
        return ""
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.6g}"


def _field_set(value) -> set[str]:
    if pd.isna(value):
        return set()
    return {part.strip() for part in str(value).split(",") if part.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    root = Path(args.repo_root)
    summary = write_smartlab_outlier_audit(root)
    triage = write_smartlab_outlier_triage(root)
    print(
        "[smartlab-outliers] "
        f"outliers={summary['total_outliers']} high={summary['high_severity']} "
        f"companies={len(summary['companies'])} "
        f"unit_mismatch={triage['suspected_unit_mismatch']} batches={triage['batch_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
