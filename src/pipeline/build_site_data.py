from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_json
from src.paths import REPO_ROOT


def build_site_data(root: Path = REPO_ROOT, copy_to_site: bool = True) -> dict:
    wide_path = root / "data" / "unified" / "company_financials_unified.parquet"
    facts_path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if not wide_path.exists() or not facts_path.exists():
        raise FileNotFoundError("Unified parquet files are missing. Run unify_financial_data first.")
    wide = pd.read_parquet(wide_path)
    facts = pd.read_parquet(facts_path)
    now = utc_now_iso()

    rows = []
    for _, r in wide.sort_values(["ticker", "fiscal_year"]).iterrows():
        rows.append({
            "ticker": r.get("ticker"),
            "period": str(r.get("period")),
            "fiscal_year": int(r.get("fiscal_year")) if pd.notna(r.get("fiscal_year")) else None,
            "revenue": _num(r.get("revenue")),
            "operating_profit": _num(r.get("operating_profit")),
            "net_income": _num(r.get("net_income")),
            "total_assets": _num(r.get("total_assets")),
            "total_equity": _num(r.get("total_equity")),
            "total_debt": _num(r.get("total_debt")),
            "cash_and_equivalents": _num(r.get("cash_and_equivalents")),
            "net_debt": _num(r.get("net_debt")),
            "operating_cash_flow": _num(r.get("operating_cash_flow")),
            "capex": _num(r.get("capex")),
            "free_cash_flow": _num(r.get("free_cash_flow")),
            "dividends_paid": _num(r.get("dividends_paid")),
            "dividend_per_share": _num(r.get("dividend_per_share")),
            "payout_ratio": _num(r.get("payout_ratio")),
            "quality_score": _num(r.get("quality_score")),
            "source": r.get("source"),
            "conflict_flag": bool(r.get("conflict_flag")) if pd.notna(r.get("conflict_flag")) else False,
        })

    source_counts = facts["best_source_name"].fillna("unknown").value_counts().to_dict() if not facts.empty else {}
    conflicts = int(facts["conflict_flag"].sum()) if "conflict_flag" in facts else 0
    avg_quality = float(facts["best_quality_score"].dropna().mean()) if "best_quality_score" in facts and not facts.empty else None
    reliable = int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0
    manual_review = int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0
    site_financials = {
        "meta": {
            "generated_at": now,
            "source": "data/unified/company_financials_unified.parquet",
            "rows": len(rows),
            "source_counts": source_counts,
            "conflicts_count": conflicts,
            "reliable_facts": reliable,
            "manual_review_facts": manual_review,
            "average_quality_score": round(avg_quality, 2) if avg_quality is not None else None,
            "note": "Unified financials are an additive data layer. Current legacy dashboard remains backward compatible.",
        },
        "rows": rows,
    }

    coverage = build_coverage(root, facts, now, source_counts, conflicts, avg_quality)
    out_fin = root / "data" / "unified" / "site_financials.json"
    out_cov = root / "data" / "unified" / "site_coverage.json"
    write_json(out_fin, site_financials)
    write_json(out_cov, coverage)
    if copy_to_site:
        write_json(root / "site" / "site_financials.json", site_financials)
        write_json(root / "site" / "site_coverage.json", coverage)
    return {"site_financials": str(out_fin.relative_to(root)), "site_coverage": str(out_cov.relative_to(root)), "rows": len(rows)}


def build_coverage(root: Path, facts: pd.DataFrame, now: str, source_counts: dict, conflicts: int, avg_quality: float | None) -> dict:
    reg_path = root / "data" / "companies_registry.csv"
    if reg_path.exists():
        reg = pd.read_csv(reg_path, dtype=str).fillna("")
    else:
        reg = pd.DataFrame()
    counts = {}
    if not reg.empty and "coverage_status" in reg:
        counts = reg["coverage_status"].value_counts().to_dict()
    return {
        "meta": {
            "generated_at": now,
            "companies_total": int(len(reg)),
            "companies_active": int((reg.get("coverage_status", pd.Series(dtype=str)) == "active").sum()) if not reg.empty else 0,
            "source_counts": source_counts,
            "conflicts_count": conflicts,
            "reliable_facts": int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "manual_review_facts": int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "average_quality_score": round(avg_quality, 2) if avg_quality is not None else None,
        },
        "coverage_status_counts": counts,
        "quality": {
            "reliable": int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "good": int((facts.get("best_quality_score", pd.Series(dtype=float)) >= 90).sum()) if not facts.empty else 0,
            "acceptable": int(((facts.get("best_quality_score", pd.Series(dtype=float)) >= 70) & (facts.get("best_quality_score", pd.Series(dtype=float)) < 90)).sum()) if not facts.empty else 0,
            "manual_review": int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
        },
    }


def _num(v):
    return None if pd.isna(v) else float(v)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--no-copy-to-site", action="store_true")
    args = parser.parse_args()
    res = build_site_data(Path(args.repo_root), copy_to_site=not args.no_copy_to_site)
    print(f"[build-site-data] rows={res['rows']} wrote {res['site_financials']} and {res['site_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
