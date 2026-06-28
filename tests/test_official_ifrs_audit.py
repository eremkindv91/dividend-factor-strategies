from pathlib import Path

import pandas as pd

from src.quality.audit_official_ifrs import build_official_ifrs_audit, write_official_ifrs_audit


def _seed_fact(ticker: str, metric: str, value: float) -> dict:
    return {
        "fact_id": f"seed-{ticker}-{metric}",
        "report_id": f"report-{ticker}",
        "ticker": ticker,
        "inn": "",
        "company_name": ticker,
        "fiscal_year": 2025,
        "period": "2025",
        "year_status": "newer_than_target",
        "period_type": "annual",
        "reporting_standard": "IFRS",
        "statement_type": "income_statement",
        "line_item_raw": metric,
        "line_item_std": metric,
        "value_raw": value,
        "value_normalized": value,
        "currency": "RUB",
        "unit_type": "currency",
        "unit_multiplier": 1,
        "is_calculated": False,
        "formula": "",
        "source_page": "page 1",
        "source_table_id": "statement",
        "source_url": f"https://example.com/{ticker}.pdf",
        "source_name": "official_ifrs_verified_seed",
        "source_type": "official_ifrs",
        "source_priority": 2,
        "is_legacy_data": False,
        "extraction_method": "verified_seed",
        "confidence_score": 0.98,
        "quality_score": 96,
        "validation_status": "verified_seed",
        "created_at": "2026-06-28T00:00:00+00:00",
    }


def _unified_fact(ticker: str, metric: str, official: float, best: float, source: str, conflict: bool = False) -> dict:
    return {
        "ticker": ticker,
        "company_name": ticker,
        "fiscal_year": 2025,
        "period": "2025",
        "line_item_std": metric,
        "currency": "RUB",
        "best_value": best,
        "best_source_name": source,
        "best_source_type": "official_ifrs" if source.startswith("official_ifrs") else "aggregator",
        "best_source_url": f"https://example.com/{ticker}.pdf" if source.startswith("official_ifrs") else "https://smart-lab.ru/",
        "best_quality_score": 96 if source.startswith("official_ifrs") else 70,
        "smartlab_value": None if source.startswith("official_ifrs") else best,
        "smartlab_source_url": "https://smart-lab.ru/",
        "official_ifrs_value": official,
        "official_ifrs_source_url": f"https://example.com/{ticker}.pdf",
        "selected_reason": "official_ifrs_confirmed_by_smartlab" if not conflict else "official_ifrs_conflict_gt_5pct_needs_review",
        "conflict_flag": conflict,
        "conflict_type": "value_conflict_gt_5pct" if conflict else None,
        "needs_manual_review": conflict,
        "is_reliable": not conflict,
    }


def test_official_ifrs_audit_classifies_best_comparison_conflict_and_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    unified_dir = data_dir / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([
        _seed_fact("BEST", "revenue", 100.0),
        _seed_fact("COMP", "revenue", 200.0),
        _seed_fact("CNFL", "revenue", 300.0),
        _seed_fact("MISS", "revenue", 400.0),
    ]).to_csv(data_dir / "official_ifrs_facts.csv", index=False)
    pd.DataFrame([
        _unified_fact("BEST", "revenue", 100.0, 100.0, "official_ifrs_verified_seed"),
        _unified_fact("COMP", "revenue", 200.0, 199.0, "smartlab"),
        _unified_fact("CNFL", "revenue", 300.0, 300.0, "official_ifrs_verified_seed", conflict=True),
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)

    audit = build_official_ifrs_audit(tmp_path)

    roles = dict(zip(audit["ticker"], audit["official_fact_role"]))
    statuses = dict(zip(audit["ticker"], audit["source_status"]))
    assert roles == {
        "BEST": "best_value",
        "COMP": "comparison_source",
        "CNFL": "best_value",
        "MISS": "missing",
    }
    assert statuses["CNFL"] == "Conflict"
    assert statuses["MISS"] == "Missing"


def test_official_ifrs_audit_writes_summary_and_csv(tmp_path: Path):
    data_dir = tmp_path / "data"
    unified_dir = data_dir / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([_seed_fact("BEST", "revenue", 100.0)]).to_csv(data_dir / "official_ifrs_facts.csv", index=False)
    pd.DataFrame([_unified_fact("BEST", "revenue", 100.0, 100.0, "official_ifrs_verified_seed")]).to_parquet(
        unified_dir / "financial_facts_unified.parquet",
        index=False,
    )

    summary = write_official_ifrs_audit(tmp_path)

    assert summary["official_ifrs_facts"] == 1
    assert summary["missing_official_facts"] == 0
    assert summary["role_counts"] == {"best_value": 1}
    audit_csv = data_dir / "quality" / "official_ifrs_audit.csv"
    assert audit_csv.exists()
    audit = pd.read_csv(audit_csv)
    assert audit.loc[0, "year_status"] == "newer_than_target"
