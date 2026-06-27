from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import backup_files, stable_id, utc_now_iso, write_csv, write_parquet
from src.paths import REPO_ROOT, ensure_dir

PANEL_PRIMARY = Path("data/panels_final/panel_russia_final_smartlab.csv")
PANEL_FALLBACK = Path("data/panels_final/panel_russia_final.csv")
SMARTLAB_BACKUP_FILES = [
    Path("results/smartlab/live_forecast.csv"),
    Path("results/smartlab/reconcile.csv"),
    PANEL_PRIMARY,
    PANEL_FALLBACK,
]

METRIC_MAP = {
    "revenue_mln": ("revenue", "income_statement", "currency", 1_000_000),
    "ebitda_mln": ("ebitda", "income_statement", "currency", 1_000_000),
    "net_profit_mln": ("net_income", "income_statement", "currency", 1_000_000),
    "equity_mln": ("total_equity", "balance_sheet", "currency", 1_000_000),
    "assets_mln": ("total_assets", "balance_sheet", "currency", 1_000_000),
    "net_debt_mln": ("net_debt", "balance_sheet", "currency", 1_000_000),
    "total_debt_mln": ("total_debt", "balance_sheet", "currency", 1_000_000),
    "cash_mln": ("cash_and_equivalents", "balance_sheet", "currency", 1_000_000),
    "CFO_": ("operating_cash_flow", "cash_flow", "currency", 1_000_000),
    "CAPEX_": ("capex", "cash_flow", "currency", 1_000_000),
    "FCF": ("free_cash_flow", "cash_flow", "currency", 1_000_000),
    "dps_rub": ("dividend_per_share", "dividends", "per_share", 1),
    "payout_ratio_pct": ("payout_ratio", "dividends", "percent", 1),
}

REGISTRY_FIELDS = [
    "ticker", "secid", "short_name", "full_name", "inn", "ogrn", "sector", "industry",
    "moex_board", "is_public", "is_traded", "has_smartlab_data", "has_ifrs", "has_ras",
    "disclosure_page_url", "e_disclosure_company_id", "issuer_website", "source_priority",
    "status", "coverage_status", "last_report_period", "last_successful_update", "notes",
]


def migrate_smartlab(root: Path = REPO_ROOT, make_backup: bool = True) -> dict:
    panel_rel = PANEL_PRIMARY if (root / PANEL_PRIMARY).exists() else PANEL_FALLBACK
    panel_path = root / panel_rel
    if not panel_path.exists():
        raise FileNotFoundError(f"SmartLab/panel source not found: {PANEL_PRIMARY} or {PANEL_FALLBACK}")

    backup_path = None
    if make_backup:
        backup_path = backup_files([root / p for p in SMARTLAB_BACKUP_FILES], root / "data" / "backups" / "smartlab")

    df = pd.read_csv(panel_path)
    required = {"ticker", "year"}
    if not required.issubset(df.columns):
        raise ValueError(f"{panel_rel}: required columns missing: {sorted(required - set(df.columns))}")

    now = utc_now_iso()
    rows: list[dict] = []
    issues: list[dict] = []
    for _, rec in df.iterrows():
        ticker = str(rec.get("ticker") or "").strip().upper()
        year = rec.get("year")
        if not ticker or pd.isna(year):
            issues.append({"file_path": str(panel_rel), "ticker": ticker, "year": year, "field": "", "issue": "missing_ticker_or_year", "created_at": now})
            continue
        fiscal_year = int(year)
        for col, (metric, statement, unit, multiplier) in METRIC_MAP.items():
            if col not in df.columns:
                continue
            value = rec.get(col)
            if pd.isna(value):
                continue
            try:
                raw_value = float(value)
            except (TypeError, ValueError):
                issues.append({"file_path": str(panel_rel), "ticker": ticker, "year": fiscal_year, "field": col, "issue": f"non_numeric_value={value}", "created_at": now})
                continue
            value_normalized = raw_value * multiplier if unit == "currency" else raw_value
            period = str(fiscal_year)
            source_url = f"https://smart-lab.ru/q/{ticker}/f/y/"
            fact_id = stable_id("smartlab", ticker, fiscal_year, period, metric, col)
            rows.append({
                "fact_id": fact_id,
                "report_id": None,
                "ticker": ticker,
                "inn": None,
                "company_name": None,
                "fiscal_year": fiscal_year,
                "period": period,
                "period_type": "annual",
                "reporting_standard": "UNKNOWN",
                "statement_type": statement,
                "line_item_raw": col,
                "line_item_std": metric,
                "value_raw": raw_value,
                "value_normalized": value_normalized,
                "currency": "RUB" if unit == "currency" or metric == "dividend_per_share" else None,
                "unit_type": unit,
                "unit_multiplier": multiplier,
                "is_calculated": metric in {"net_debt", "free_cash_flow"},
                "formula": None,
                "source_page": None,
                "source_table_id": None,
                "source_url": source_url,
                "source_name": "smartlab",
                "source_type": "aggregator",
                "source_priority": 7,
                "is_legacy_data": True,
                "extraction_method": "legacy_panel_migration",
                "confidence_score": 0.70,
                "quality_score": 70,
                "validation_status": "legacy_acceptable",
                "created_at": now,
            })

    out_df = pd.DataFrame(rows)
    if not out_df.empty:
        out_df = out_df.drop_duplicates(
            ["ticker", "fiscal_year", "period", "reporting_standard", "statement_type", "line_item_std", "currency"],
            keep="first",
        ).reset_index(drop=True)
    write_parquet(root / "data" / "processed" / "smartlab_fundamentals.parquet", out_df)
    write_csv(
        root / "data" / "manual_review" / "smartlab_migration_issues.csv",
        issues,
        ["file_path", "ticker", "year", "field", "issue", "created_at"],
    )
    registry_summary = update_registry_from_panel(root, df, now)
    return {
        "source_panel": str(panel_rel),
        "backup_path": str(backup_path) if backup_path else None,
        "facts_written": int(len(out_df)),
        "issues": len(issues),
        "registry": registry_summary,
    }


def update_registry_from_panel(root: Path, df: pd.DataFrame, now: str) -> dict:
    reg_path = root / "data" / "companies_registry.csv"
    existing: dict[str, dict] = {}
    if reg_path.exists():
        old = pd.read_csv(reg_path, dtype=str).fillna("")
        for _, row in old.iterrows():
            existing[str(row.get("ticker", "")).upper()] = {k: row.get(k, "") for k in REGISTRY_FIELDS}

    newest = df.sort_values("year").groupby("ticker", as_index=False).tail(1)
    for _, row in newest.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        current = existing.get(ticker, {k: "" for k in REGISTRY_FIELDS})
        defaults = {
            "ticker": ticker,
            "secid": ticker,
            "short_name": ticker,
            "sector": row.get("sector", ""),
            "is_public": "true",
            "is_traded": "",
            "has_smartlab_data": "true",
            "has_ifrs": "",
            "has_ras": "",
            "source_priority": "7",
            "status": "ok",
            "coverage_status": "smartlab_only",
            "last_report_period": str(int(row.get("year"))) if not pd.isna(row.get("year")) else "",
            "last_successful_update": now,
            "notes": "created_from_smartlab_panel",
        }
        for k, v in defaults.items():
            if not str(current.get(k, "")).strip():
                current[k] = v
        existing[ticker] = current

    rows = [existing[k] for k in sorted(existing)]
    write_csv(reg_path, rows, REGISTRY_FIELDS)
    return {"companies": len(rows), "path": str(reg_path.relative_to(root))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    res = migrate_smartlab(Path(args.repo_root), make_backup=not args.no_backup)
    print(f"[smartlab-migrate] source={res['source_panel']}")
    print(f"[smartlab-migrate] facts={res['facts_written']} issues={res['issues']}")
    if res["backup_path"]:
        print(f"[smartlab-migrate] backup={res['backup_path']}")
    print(f"[smartlab-migrate] registry={res['registry']['companies']} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
