from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_csv, write_json
from src.normalization.tickers import canonical_ticker
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
    smartlab_cleaned_summary = load_smartlab_cleaned_summary(root)
    smartlab_fundamentals = build_site_fundamentals(load_parquet_if_exists(root / "data" / "processed" / "smartlab_fundamentals_cleaned.parquet"))
    now = utc_now_iso()

    rows = []
    for _, r in wide.sort_values(["ticker", "fiscal_year"]).iterrows():
        source_status = source_status_from_row(r)
        rows.append({
            "ticker": canonical_ticker(r.get("ticker"), root),
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
            "verification_status": verification_status_from_source(r.get("source"), source_status),
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
    quality_funnel = build_data_quality_funnel(root, facts, conflicts, official_summary, disclosure_summary)
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
            **smartlab_cleaned_summary,
            **quality_funnel,
            "data_quality_funnel": quality_funnel,
            "note": "Unified financials are an additive data layer. Current legacy dashboard remains backward compatible.",
        },
        "rows": rows,
        "official_facts": official_fact_rows(official_audit),
        "fundamentals": smartlab_fundamentals,
    }

    coverage = build_coverage(root, facts, now, source_counts, source_status_counts, conflicts, avg_quality, official_summary, disclosure_summary, quality_funnel)
    out_fin = root / "data" / "unified" / "site_financials.json"
    out_cov = root / "data" / "unified" / "site_coverage.json"
    write_json(out_fin, site_financials)
    write_json(out_cov, coverage)
    write_end_to_end_data_flow_reports(root, facts, disclosure_summary, quality_funnel, now)
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
    quality_funnel: dict | None = None,
) -> dict:
    official_summary = official_summary or {
        "official_ifrs_facts": 0,
        "missing_official_facts": 0,
        "role_counts": {},
        "source_status_counts": {},
        "year_status_counts": {},
    }
    disclosure_summary = disclosure_summary or default_disclosure_summary()
    quality_funnel = quality_funnel or build_data_quality_funnel(root, facts, conflicts, official_summary, disclosure_summary)
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
            **load_smartlab_cleaned_summary(root),
            **quality_funnel,
            "data_quality_funnel": quality_funnel,
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


def build_data_quality_funnel(
    root: Path,
    facts: pd.DataFrame,
    conflicts: int,
    official_summary: dict,
    disclosure_summary: dict,
) -> dict:
    smartlab_facts = count_processed_facts(
        [
            root / "data" / "processed" / "smartlab_fundamentals.parquet",
            root / "data" / "processed" / "smartlab_financial_facts.parquet",
        ],
        facts,
        "smartlab",
    )
    ifrs_processed = load_parquet_if_exists(root / "data" / "processed" / "ifrs_financial_facts.parquet")
    if ifrs_processed is None:
        ifrs_processed_facts = int(facts.get("best_source_name", pd.Series(dtype=str)).fillna("").astype(str).str.startswith("official_ifrs").sum()) if not facts.empty else 0
        extracted_reports = 0
    else:
        ifrs_processed_facts = int(len(ifrs_processed))
        source_names = ifrs_processed.get("source_name", pd.Series(dtype=str)).fillna("").astype(str)
        extracted = ifrs_processed[source_names != "official_ifrs_verified_seed"]
        report_ids = extracted.get("report_id", pd.Series(dtype=str)).dropna().astype(str)
        extracted_reports = int(report_ids[report_ids != ""].nunique())

    audit_summary = load_json_if_exists(root / "results" / "disclosure" / "report_index_audit_summary.json")
    audited_links_ok = int(audit_summary.get("ok", 0)) if audit_summary else 0
    downloaded_reports = count_downloaded_reports(root / "data" / "report_index.parquet")

    return {
        "smartlab_facts": int(smartlab_facts),
        "official_ifrs_processed_facts": int(ifrs_processed_facts),
        "verified_official_facts": int(official_summary["official_ifrs_facts"]),
        "official_report_links_found": int(disclosure_summary["reports_found_from_disclosure"] or 0),
        "audited_links_ok": int(audited_links_ok),
        "downloaded_audited_reports": int(downloaded_reports),
        "extracted_reports": int(extracted_reports),
        "conflicts": int(conflicts),
        "disclosure_errors": int(disclosure_summary["disclosure_errors_count"] or 0),
    }


def count_processed_facts(paths: list[Path], facts: pd.DataFrame, fallback_source_prefix: str) -> int:
    for path in paths:
        processed = load_parquet_if_exists(path)
        if processed is not None:
            return int(len(processed))
    if facts.empty:
        return 0
    source_names = facts.get("best_source_name", pd.Series(dtype=str)).fillna("").astype(str)
    return int(source_names.str.startswith(fallback_source_prefix).sum())


def count_downloaded_reports(report_index_path: Path) -> int:
    report_index = load_parquet_if_exists(report_index_path)
    if report_index is None or "download_status" not in report_index:
        return 0
    return int((report_index["download_status"].fillna("").astype(str) == "downloaded").sum())


NEWLY_PARSED_REPORT_COLUMNS = [
    "ticker",
    "year",
    "period",
    "field",
    "value",
    "source_url",
    "report_id",
    "extraction_method",
    "verification_status",
    "publish_status",
    "conflict_status",
    "selected_for_site",
    "reason",
]


def write_end_to_end_data_flow_reports(
    root: Path,
    facts: pd.DataFrame,
    disclosure_summary: dict,
    quality_funnel: dict,
    now: str | None = None,
) -> dict:
    now = now or utc_now_iso()
    ifrs = load_parquet_if_exists(root / "data" / "processed" / "ifrs_financial_facts.parquet")
    if ifrs is None:
        ifrs = pd.DataFrame()
    source_names = ifrs.get("source_name", pd.Series(dtype=str)).fillna("").astype(str) if not ifrs.empty else pd.Series(dtype=str)
    seed_mask = source_names == "official_ifrs_verified_seed"
    new_ifrs = ifrs[~seed_mask].copy() if not ifrs.empty else pd.DataFrame()
    seed_keys = set()
    if not ifrs.empty:
        seed_keys = {_fact_key(r) for _, r in ifrs[seed_mask].iterrows()}
    unified = build_unified_lookup(facts)
    rows = newly_parsed_fact_report_rows(new_ifrs, unified)
    published_new = sum(1 for r in rows if r["publish_status"] == "published")
    conflicts = sum(1 for r in rows if r["publish_status"] == "conflict_review")
    review = sum(1 for r in rows if r["publish_status"] in {"needs_review", "conflict_review"})
    matched_verified = 0
    for _, r in new_ifrs.iterrows():
        if _fact_key(r) in seed_keys:
            matched_verified += 1
    fact_sources = facts.get("best_source_name", pd.Series(dtype=str)).fillna("").astype(str) if not facts.empty else pd.Series(dtype=str)
    conflict_mask = facts.get("conflict_flag", pd.Series(False, index=facts.index)).fillna(False).astype(bool) if not facts.empty else pd.Series(dtype=bool)
    review_mask = facts.get("needs_manual_review", pd.Series(False, index=facts.index)).fillna(False).astype(bool) if not facts.empty else pd.Series(dtype=bool)
    publishable = ~(conflict_mask | review_mask) if not facts.empty else pd.Series(dtype=bool)
    summary = {
        "reports_found_from_disclosure": int(disclosure_summary.get("reports_found_from_disclosure") or 0),
        "reports_downloaded": int(quality_funnel.get("downloaded_audited_reports") or 0),
        "reports_extracted": int(quality_funnel.get("extracted_reports") or 0),
        "extracted_facts_total": int(len(ifrs)),
        "extracted_facts_new": int(len(new_ifrs)),
        "extracted_facts_matched_verified": int(matched_verified),
        "extracted_facts_conflicts": int(conflicts),
        "extracted_facts_needs_review": int(review),
        "published_facts_from_verified_ifrs": int(((fact_sources == "official_ifrs_verified_seed") & publishable).sum()) if not facts.empty else 0,
        "published_facts_from_newly_parsed_reports": int((fact_sources.str.startswith("official_ifrs") & (fact_sources != "official_ifrs_verified_seed") & publishable).sum()) if not facts.empty else 0,
        "published_facts_from_smartlab": int((fact_sources == "smartlab").sum()) if not facts.empty else 0,
        "published_facts_from_manual_overrides": int((fact_sources == "manual_override").sum()) if not facts.empty else 0,
        "facts_blocked_from_publish": int(sum(1 for r in rows if r["publish_status"] in {"conflict_review", "needs_review"})),
        "newly_parsed_blocked_reason": newly_parsed_blocked_reason(rows),
        "generated_at": now,
    }
    write_json(root / "data" / "quality" / "end_to_end_data_flow_summary.json", summary)
    write_csv(root / "data" / "quality" / "newly_parsed_facts_report.csv", rows, NEWLY_PARSED_REPORT_COLUMNS)
    return summary


def build_unified_lookup(facts: pd.DataFrame) -> dict:
    if facts.empty:
        return {}
    out = {}
    for _, r in facts.iterrows():
        out[_fact_key(r)] = r
    return out


def newly_parsed_fact_report_rows(new_ifrs: pd.DataFrame, unified: dict) -> list[dict]:
    rows: list[dict] = []
    if new_ifrs.empty:
        return rows
    sort_cols = [c for c in ["ticker", "fiscal_year", "period", "line_item_std"] if c in new_ifrs.columns]
    iterable = new_ifrs.sort_values(sort_cols).iterrows() if sort_cols else new_ifrs.iterrows()
    for _, r in iterable:
        match = unified.get(_fact_key(r))
        source = str(r.get("source_name") or "")
        selected = False
        conflict = False
        review = False
        conflict_type = ""
        reason = "not_selected"
        if match is not None:
            selected = str(match.get("best_source_name") or "") == source
            conflict = _bool(match.get("conflict_flag"))
            review = _bool(match.get("needs_manual_review"))
            conflict_type = str(match.get("conflict_type") or "")
            reason = str(match.get("selected_reason") or reason)
        verification = verification_status_for_source(source, conflict, review)
        if conflict:
            publish = "conflict_review"
            selected_for_site = False
        elif review:
            publish = "needs_review"
            selected_for_site = False
        elif selected:
            publish = "published"
            selected_for_site = True
        else:
            publish = "not_selected"
            selected_for_site = False
        rows.append({
            "ticker": canonical_ticker(r.get("ticker")),
            "year": _int_or_none(r.get("fiscal_year")),
            "period": str(r.get("period") or ""),
            "field": str(r.get("line_item_std") or ""),
            "value": _num(r.get("value_normalized")),
            "source_url": str(r.get("source_url") or ""),
            "report_id": str(r.get("report_id") or ""),
            "extraction_method": str(r.get("extraction_method") or ""),
            "verification_status": verification,
            "publish_status": publish,
            "conflict_status": conflict_type or ("needs_review" if review else ("none" if selected_for_site else "not_selected")),
            "selected_for_site": selected_for_site,
            "reason": reason,
        })
    return rows


def verification_status_for_source(source: str, conflict: bool = False, review: bool = False) -> str:
    if source == "official_ifrs_verified_seed":
        return "verified_seed"
    if source.startswith("official_ifrs"):
        return "parsed_official_unverified"
    if source == "manual_override":
        return "manual_override_verified"
    if source == "smartlab":
        return "smartlab_fallback"
    if conflict:
        return "conflict_review"
    if review:
        return "blocked"
    return "blocked"


def newly_parsed_blocked_reason(rows: list[dict]) -> str:
    if not rows:
        return ""
    if any(r["publish_status"] == "published" for r in rows):
        return ""
    statuses = {str(r.get("publish_status") or "") for r in rows}
    if statuses == {"not_selected"}:
        return "newly parsed facts matched existing higher-priority verified/SmartLab values and were kept as comparison candidates"
    if "conflict_review" in statuses:
        return "newly parsed facts include source conflicts and are held for review"
    if "needs_review" in statuses:
        return "newly parsed facts require manual review before publishing"
    return "newly parsed facts were not selected by source resolver"


def _fact_key(row: pd.Series) -> tuple:
    return (
        canonical_ticker(row.get("ticker")),
        _int_or_none(row.get("fiscal_year")),
        str(row.get("period") or ""),
        str(row.get("line_item_std") or ""),
    )


def load_parquet_if_exists(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None


def load_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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


def default_smartlab_cleaned_summary() -> dict:
    return {
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


def load_smartlab_cleaned_summary(root: Path) -> dict:
    summary = default_smartlab_cleaned_summary()
    raw = load_json_if_exists(root / "data" / "quality" / "smartlab_cleaned_layer_summary.json")
    summary.update({k: raw.get(k, summary[k]) for k in summary if k in raw})
    return summary


def build_site_fundamentals(cleaned: pd.DataFrame | None) -> dict:
    if cleaned is None or cleaned.empty:
        return {}
    required = {"ticker", "year", "field", "statement_group", "clean_value", "raw_value"}
    if not required.issubset(cleaned.columns):
        return {}
    groups_order = ["income", "balance", "cashflow", "dividends", "ratios", "valuation", "per_share"]
    out: dict[str, dict[str, list[dict]]] = {}
    safe = cleaned.copy()
    safe["ticker"] = safe["ticker"].fillna("").astype(str).str.upper()
    safe = safe[safe["ticker"] != ""]
    for ticker, ticker_df in safe.sort_values(["ticker", "statement_group", "sort_order", "field", "year"]).groupby("ticker", sort=True):
        company: dict[str, list[dict]] = {group: [] for group in groups_order}
        for (group, field), metric_df in ticker_df.groupby(["statement_group", "field"], sort=False):
            values = []
            for _, r in metric_df.sort_values("year").iterrows():
                excluded = _bool(r.get("excluded_from_site"))
                values.append({
                    "year": int(r.get("year")) if pd.notna(r.get("year")) else None,
                    "period": str(r.get("period")) if pd.notna(r.get("period")) else "",
                    "value": None if excluded else _num(r.get("clean_value")),
                    "raw_value": _num(r.get("raw_value")),
                    "quality_status": r.get("quality_status") or "unknown",
                    "quality_reason": r.get("quality_reason") or "",
                    "source_name": r.get("source_name") or "SmartLab",
                    "source_status": r.get("source_status") or "smartlab_fallback",
                    "source_url": r.get("source_url") or "",
                    "needs_manual_review": _bool(r.get("needs_manual_review")),
                    "excluded_from_site": excluded,
                })
            first = metric_df.iloc[0]
            company.setdefault(str(group), []).append({
                "field": str(field),
                "name_ru": first.get("display_name_ru") or str(field),
                "statement_group": str(group),
                "statement_type": first.get("statement_type") or "",
                "display_format": first.get("display_format") or "number",
                "unit": first.get("unit") or "",
                "scale": int(first.get("scale")) if pd.notna(first.get("scale")) else 1,
                "sort_order": int(first.get("sort_order")) if pd.notna(first.get("sort_order")) else 999,
                "chart_type": "bar",
                "values": values,
            })
        out[canonical_ticker(ticker)] = {k: v for k, v in company.items() if v}
    return out


def _num(v):
    return None if pd.isna(v) else float(v)


def _bool(v) -> bool:
    return False if pd.isna(v) else bool(v)


def _int_or_none(v) -> int | None:
    if pd.isna(v):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


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


def verification_status_from_source(source_value, source_status: str | None = None) -> str:
    source = str(source_value or "").lower()
    if source_status == "Conflict":
        return "conflict_review"
    if source_status in {"Needs review", "OCR candidate"}:
        return "blocked"
    if "official_ifrs_verified_seed" in source:
        return "verified_seed"
    if "manual_override" in source:
        return "manual_override_verified"
    if "official_ifrs" in source:
        return "parsed_official_unverified"
    if "smartlab" in source:
        return "smartlab_fallback"
    return "blocked"


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
