from pathlib import Path

import json

import pandas as pd

from src.pipeline.build_site_data import build_site_data


def test_build_site_data_exposes_reliability_counts(tmp_path: Path):
    unified_dir = tmp_path / "data" / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "revenue": 100.0,
            "quality_score": 90.0,
            "source": "official_ifrs_pdf_table",
            "conflict_flag": False,
        }
    ]).to_parquet(unified_dir / "company_financials_unified.parquet", index=False)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "official_ifrs_pdf_table",
            "best_quality_score": 90.0,
            "conflict_flag": False,
            "needs_manual_review": False,
            "is_reliable": True,
        },
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "net_income",
            "best_source_name": "smartlab",
            "best_quality_score": 70.0,
            "conflict_flag": True,
            "needs_manual_review": True,
            "is_reliable": False,
        },
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)
    data_dir = tmp_path / "data"
    pd.DataFrame([
        {"ticker": "TEST", "coverage_status": "partial"},
    ]).to_csv(data_dir / "companies_registry.csv", index=False)

    build_site_data(tmp_path)
    coverage = json.loads((unified_dir / "site_coverage.json").read_text(encoding="utf-8"))
    financials = json.loads((unified_dir / "site_financials.json").read_text(encoding="utf-8"))

    assert coverage["quality"]["reliable"] == 1
    assert coverage["quality"]["manual_review"] == 1
    assert financials["meta"]["reliable_facts"] == 1
    assert financials["meta"]["manual_review_facts"] == 1
