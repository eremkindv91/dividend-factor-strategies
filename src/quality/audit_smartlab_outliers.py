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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = write_smartlab_outlier_audit(Path(args.repo_root))
    print(
        "[smartlab-outliers] "
        f"outliers={summary['total_outliers']} high={summary['high_severity']} "
        f"companies={len(summary['companies'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
