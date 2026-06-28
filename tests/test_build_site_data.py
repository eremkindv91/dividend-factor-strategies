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
            "year_status": "newer_than_target",
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


def test_build_site_data_exposes_visible_source_statuses(tmp_path: Path):
    unified_dir = tmp_path / "data" / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "revenue": 100.0,
            "quality_score": 92.0,
            "source": "official_ifrs_xlsx",
            "conflict_flag": False,
            "needs_manual_review": False,
            "ocr_candidate": False,
        },
        {
            "ticker": "SMRT",
            "fiscal_year": 2025,
            "period": "2025",
            "revenue": 100.0,
            "quality_score": 70.0,
            "source": "smartlab",
            "conflict_flag": False,
            "needs_manual_review": False,
            "ocr_candidate": False,
        },
        {
            "ticker": "CNFL",
            "fiscal_year": 2025,
            "period": "2025",
            "revenue": 100.0,
            "quality_score": 70.0,
            "source": "smartlab,official_ifrs_pdf_text",
            "conflict_flag": True,
            "needs_manual_review": True,
            "ocr_candidate": False,
        },
        {
            "ticker": "OCR",
            "fiscal_year": 2025,
            "period": "2025",
            "revenue": 100.0,
            "quality_score": 65.0,
            "source": "official_ifrs_ocr_low_confidence",
            "conflict_flag": False,
            "needs_manual_review": True,
            "ocr_candidate": True,
        },
    ]).to_parquet(unified_dir / "company_financials_unified.parquet", index=False)
    pd.DataFrame([
        {
            "ticker": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "official_ifrs_xlsx",
            "best_quality_score": 92.0,
            "conflict_flag": False,
            "needs_manual_review": False,
            "is_reliable": True,
        },
        {
            "ticker": "SMRT",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "smartlab",
            "best_quality_score": 70.0,
            "conflict_flag": False,
            "needs_manual_review": False,
            "is_reliable": True,
        },
        {
            "ticker": "CNFL",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "smartlab",
            "best_quality_score": 70.0,
            "conflict_flag": True,
            "needs_manual_review": True,
            "is_reliable": False,
        },
        {
            "ticker": "OCR",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "official_ifrs_ocr_low_confidence",
            "best_quality_score": 65.0,
            "conflict_flag": False,
            "needs_manual_review": True,
            "is_reliable": False,
        },
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)

    build_site_data(tmp_path)
    coverage = json.loads((unified_dir / "site_coverage.json").read_text(encoding="utf-8"))
    financials = json.loads((unified_dir / "site_financials.json").read_text(encoding="utf-8"))

    statuses = {row["ticker"]: row["source_status"] for row in financials["rows"]}
    assert statuses == {
        "CNFL": "Conflict",
        "IFRS": "Official IFRS",
        "OCR": "OCR candidate",
        "SMRT": "SmartLab fallback",
    }
    assert coverage["source_status_counts"]["Official IFRS"] == 1
    assert coverage["source_status_counts"]["SmartLab fallback"] == 1
    assert coverage["source_status_counts"]["Conflict"] == 1
    assert coverage["source_status_counts"]["OCR candidate"] == 1


def test_build_site_data_publishes_fact_level_official_ifrs_provenance(tmp_path: Path):
    data_dir = tmp_path / "data"
    unified_dir = data_dir / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "fact_id": "seed-1",
            "report_id": "report-1",
            "ticker": "IFRS",
            "inn": "",
            "company_name": "IFRS Co",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_raw": "Revenue",
            "line_item_std": "revenue",
            "value_raw": 100,
            "value_normalized": 100.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1,
            "is_calculated": False,
            "formula": "",
            "source_page": "page 1",
            "source_table_id": "statement",
            "source_url": "https://example.com/ifrs.pdf",
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
    ]).to_csv(data_dir / "official_ifrs_facts.csv", index=False)
    pd.DataFrame([
        {
            "ticker": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "currency": "RUB",
            "revenue": 100.0,
            "quality_score": 96.0,
            "source": "official_ifrs_verified_seed",
            "conflict_flag": False,
            "needs_manual_review": False,
            "ocr_candidate": False,
        }
    ]).to_parquet(unified_dir / "company_financials_unified.parquet", index=False)
    pd.DataFrame([
        {
            "ticker": "IFRS",
            "company_name": "IFRS Co",
            "fiscal_year": 2025,
            "period": "2025",
            "line_item_std": "revenue",
            "currency": "RUB",
            "best_value": 100.0,
            "best_source_name": "official_ifrs_verified_seed",
            "best_source_type": "official_ifrs",
            "best_source_url": "https://example.com/ifrs.pdf",
            "best_quality_score": 96.0,
            "smartlab_value": None,
            "smartlab_source_url": None,
            "official_ifrs_value": 100.0,
            "official_ifrs_source_url": "https://example.com/ifrs.pdf",
            "selected_reason": "highest_priority_quality_source",
            "conflict_flag": False,
            "conflict_type": None,
            "needs_manual_review": False,
            "is_reliable": True,
        }
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)

    build_site_data(tmp_path)
    coverage = json.loads((unified_dir / "site_coverage.json").read_text(encoding="utf-8"))
    financials = json.loads((unified_dir / "site_financials.json").read_text(encoding="utf-8"))

    assert coverage["meta"]["official_ifrs_facts"] == 1
    assert coverage["official_ifrs_status_counts"] == {"Official IFRS": 1}
    assert financials["meta"]["official_ifrs_facts"] == 1
    assert financials["official_facts"] == [
        {
            "ticker": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "year_status": "newer_than_target",
            "metric": "revenue",
            "currency": "RUB",
            "official_ifrs_value": 100.0,
            "smartlab_value": None,
            "selected_value": 100.0,
            "source_status": "Official IFRS",
            "conflict_flag": False,
            "needs_manual_review": False,
            "source_url": "https://example.com/ifrs.pdf",
            "quality_score": 96.0,
            "selected_reason": "highest_priority_quality_source",
            "official_fact_role": "best_value",
        }
    ]
