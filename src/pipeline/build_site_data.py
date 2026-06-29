from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_json
from src.paths import REPO_ROOT
from src.quality.audit_official_ifrs import build_official_ifrs_audit, official_ifrs_audit_summary


def build_site_data(root: Path = REPO_ROOT, copy_to_site: bool = True) -> dict:
    wide_path = root / "data" / "unified" / "company_financials_unified.parquet"
    facts_path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if not wide_path.exists() or not facts_path.exists():
        raise FileNotFoundError("Unified parquet files are missing. Run unify_financial_data first.")
    wide = pd.read_parquet(wide_path)
    facts = pd.read_parquet(facts_path)
    official_audit = build_official_ifrs_audit(root)
    official_summary = official_ifrs_audit_summary(official_audit)
    disclosure_summary = load_disclosure_summary(root)
    now = utc_now_iso()

    rows = []
    for _, r in wide.sort_values(["ticker", "fiscal_year"]).iterrows():
        source_status = source_status_from_row(r)
        rows.append({
            "ticker": r.get("ticker"),
            "period": str(r.get("period")),
            "fiscal_year": int(r.get("fiscal_year")) if pd.notna(r.get("fiscal_year")) else None,
            "currency": r.get("currency") or "RUB",
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
            "source_status": source_status,
            "needs_manual_review": _bool(r.get("needs_manual_review")),
            "ocr_candidate": _bool(r.get("ocr_candidate")),
            "conflict_flag": bool(r.get("conflict_flag")) if pd.notna(r.get("conflict_flag")) else False,
        })

    source_counts = facts["best_source_name"].fillna("unknown").value_counts().to_dict() if not facts.empty else {}
    conflicts = int(facts["conflict_flag"].sum()) if "conflict_flag" in facts else 0
    source_status_counts = pd.Series([row["source_status"] for row in rows], dtype=str).value_counts().to_dict()
    avg_quality = float(facts["best_quality_score"].dropna().mean()) if "best_quality_score" in facts and not facts.empty else None
    reliable = int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0
    manual_review = int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0
    site_financials = {
        "meta": {
            "generated_at": now,
            "source": "data/unified/company_financials_unified.parquet",
            "rows": len(rows),
            "source_counts": source_counts,
            "source_status_counts": source_status_counts,
            "conflicts_count": conflicts,
            "reliable_facts": reliable,
            "manual_review_facts": manual_review,
            "average_quality_score": round(avg_quality, 2) if avg_quality is not None else None,
            "official_ifrs_facts": official_summary["official_ifrs_facts"],
            "official_ifrs_missing_facts": official_summary["missing_official_facts"],
            "official_ifrs_role_counts": official_summary["role_counts"],
            "official_ifrs_status_counts": official_summary["source_status_counts"],
            "official_ifrs_year_status_counts": official_summary["year_status_counts"],
            "reports_found_from_disclosure": disclosure_summary["reports_found_from_disclosure"],
            "last_disclosure_check": disclosure_summary["last_disclosure_check"],
            "companies_with_official_report_links": disclosure_summary["companies_with_official_report_links"],
            "disclosure_errors_count": disclosure_summary["disclosure_errors_count"],
            "note": "Unified financials are an additive data layer. Current legacy dashboard remains backward compatible.",
        },
        "rows": rows,
        "official_facts": official_fact_rows(official_audit),
    }

    coverage = build_coverage(root, facts, now, source_counts, source_status_counts, conflicts, avg_quality, official_summary, disclosure_summary)
    out_fin = root / "data" / "unified" / "site_financials.json"
    out_cov = root / "data" / "unified" / "site_coverage.json"
    write_json(out_fin, site_financials)
    write_json(out_cov, coverage)
    if copy_to_site:
        write_json(root / "site" / "site_financials.json", site_financials)
        write_json(root / "site" / "site_coverage.json", coverage)
    return {"site_financials": str(out_fin.relative_to(root)), "site_coverage": str(out_cov.relative_to(root)), "rows": len(rows)}


def build_coverage(
    root: Path,
    facts: pd.DataFrame,
    now: str,
    source_counts: dict,
    source_status_counts: dict,
    conflicts: int,
    avg_quality: float | None,
    official_summary: dict | None = None,
    disclosure_summary: dict | None = None,
) -> dict:
    official_summary = official_summary or {
        "official_ifrs_facts": 0,
        "missing_official_facts": 0,
        "role_counts": {},
        "source_status_counts": {},
        "year_status_counts": {},
    }
    disclosure_summary = disclosure_summary or default_disclosure_summary()
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
            "source_status_counts": source_status_counts,
            "conflicts_count": conflicts,
            "reliable_facts": int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "manual_review_facts": int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "average_quality_score": round(avg_quality, 2) if avg_quality is not None else None,
            "official_ifrs_facts": official_summary["official_ifrs_facts"],
            "official_ifrs_missing_facts": official_summary["missing_official_facts"],
            "official_ifrs_role_counts": official_summary["role_counts"],
            "official_ifrs_status_counts": official_summary["source_status_counts"],
            "official_ifrs_year_status_counts": official_summary["year_status_counts"],
            "reports_found_from_disclosure": disclosure_summary["reports_found_from_disclosure"],
            "last_disclosure_check": disclosure_summary["last_disclosure_check"],
            "companies_with_official_report_links": disclosure_summary["companies_with_official_report_links"],
            "disclosure_errors_count": disclosure_summary["disclosure_errors_count"],
        },
        "coverage_status_counts": counts,
        "source_status_counts": source_status_counts,
        "official_ifrs_status_counts": official_summary["source_status_counts"],
        "official_ifrs_role_counts": official_summary["role_counts"],
        "official_ifrs_year_status_counts": official_summary["year_status_counts"],
        "quality": {
            "reliable": int(facts.get("is_reliable", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
            "good": int((facts.get("best_quality_score", pd.Series(dtype=float)) >= 90).sum()) if not facts.empty else 0,
            "acceptable": int(((facts.get("best_quality_score", pd.Series(dtype=float)) >= 70) & (facts.get("best_quality_score", pd.Series(dtype=float)) < 90)).sum()) if not facts.empty else 0,
            "manual_review": int(facts.get("needs_manual_review", pd.Series(dtype=bool)).sum()) if not facts.empty else 0,
        },
    }


def load_disclosure_summary(root: Path) -> dict:
    summary = default_disclosure_summary()
    summary_path = root / "data" / "disclosure_summary.json"
    if summary_path.exists():
        try:
            raw = json.loads(summary_path.read_text(encoding="utf-8"))
            summary.update({k: raw.get(k, summary[k]) for k in summary if k in raw})
        except (OSError, json.JSONDecodeError):
            pass
    report_index = root / "data" / "report_index.parquet"
    if report_index.exists():
        try:
            idx = pd.read_parquet(report_index)
            if not idx.empty:
                summary["reports_found_from_disclosure"] = int(summary.get("reports_found_from_disclosure") or len(idx))
                link_mask = idx.get("source_name", pd.Series(dtype=str)).astype(str).isin([
                    "official_page_link", "manual_report", "report_pdf", "report_xlsx", "ifrs_report",
                ])
                urls = idx.get("source_url", pd.Series(dtype=str)).fillna("").astype(str).str.strip() != ""
                summary["companies_with_official_report_links"] = int(idx[link_mask & urls]["ticker"].dropna().astype(str).str.upper().nunique())
                updated = idx.get("updated_at", pd.Series(dtype=str)).dropna().astype(str)
                if summary.get("report_index_updated_at") in (None, "") and not updated.empty:
                    summary["report_index_updated_at"] = updated.max()
        except Exception:  # noqa: BLE001
            pass
    errors_path = root / "data" / "manual_review" / "disclosure_errors.csv"
    if errors_path.exists():
        try:
            errors = pd.read_csv(errors_path)
            summary["disclosure_errors_count"] = int(len(errors))
        except Exception:  # noqa: BLE001
            pass
    return summary


def default_disclosure_summary() -> dict:
    return {
        "reports_found_from_disclosure": 0,
        "last_disclosure_check": None,
        "report_index_updated_at": None,
        "companies_with_official_report_links": 0,
        "disclosure_errors_count": 0,
    }


def _num(v):
    return None if pd.isna(v) else float(v)


def _bool(v) -> bool:
    return False if pd.isna(v) else bool(v)


def source_status_from_row(row: pd.Series) -> str:
    source = str(row.get("source") or "").lower()
    if _bool(row.get("conflict_flag")):
        return "Conflict"
    if _bool(row.get("ocr_candidate")) or "ocr" in source:
        return "OCR candidate"
    if _bool(row.get("needs_manual_review")):
        return "Needs review"
    if "official_ifrs" in source:
        return "Official IFRS"
    if "smartlab" in source:
        return "SmartLab fallback"
    return "Needs review"


def official_fact_rows(audit: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    if audit.empty:
        return rows
    for _, r in audit.sort_values(["ticker", "fiscal_year", "period", "metric"]).iterrows():
        rows.append({
            "ticker": r.get("ticker"),
            "fiscal_year": int(r.get("fiscal_year")) if pd.notna(r.get("fiscal_year")) else None,
            "period": str(r.get("period")),
            "year_status": r.get("year_status"),
            "metric": r.get("metric"),
            "currency": r.get("currency"),
            "official_ifrs_value": _num(r.get("official_ifrs_value")),
            "smartlab_value": _num(r.get("smartlab_value")),
            "selected_value": _num(r.get("selected_value")),
            "source_status": r.get("source_status"),
            "conflict_flag": _bool(r.get("conflict_flag")),
            "needs_manual_review": _bool(r.get("needs_manual_review")),
            "source_url": r.get("source_url"),
            "quality_score": _num(r.get("quality_score")),
            "selected_reason": r.get("selected_reason"),
            "official_fact_role": r.get("official_fact_role"),
        })
    return rows


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
