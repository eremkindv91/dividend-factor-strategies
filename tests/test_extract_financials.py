from pathlib import Path

import pandas as pd

from src.pipeline.extract_financials import extract_financials
from src.pipeline.run_all import run_all


def _write_report_index(root: Path, facts_path: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "report_id": "report-test-2025",
            "ticker": "TEST",
            "inn": "123",
            "company_name": "TEST",
            "period": "2025",
            "period_type": "annual",
            "fiscal_year": 2025,
            "publication_date": "2026-03-01",
            "document_title": "TEST IFRS 2025 structured facts",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "file_path": str(facts_path.relative_to(root)),
            "source_url": "https://issuer.example/reports/test-2025-ifrs.csv",
            "source_name": "manual_report",
            "file_hash": "",
            "download_status": "metadata_only",
            "parse_status": "not_parsed",
            "extraction_status": "not_extracted",
            "quality_status": "not_checked",
            "url_status": "not_checked",
            "http_status": None,
            "content_type": "text/csv",
            "content_length": None,
            "final_url": "https://issuer.example/reports/test-2025-ifrs.csv",
            "url_checked_at": "",
            "created_at": "2026-06-27T00:00:00+00:00",
            "updated_at": "2026-06-27T00:00:00+00:00",
        }
    ]).to_parquet(data / "report_index.parquet", index=False)


def test_extract_financials_from_structured_csv_report(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2025.csv"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        {"line_item_raw": "Выручка", "value": 100.5, "currency": "RUB", "unit_multiplier": 1_000_000},
        {"line_item_raw": "Net profit", "value": 20.0, "currency": "RUB", "unit_multiplier": 1_000_000},
    ]).to_csv(facts_path, index=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 1
    assert summary["facts_from_ifrs"] == 2
    assert set(facts["line_item_std"]) == {"revenue", "net_income"}
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 100_500_000
    assert set(facts["source_type"]) == {"official_ifrs"}
    assert set(facts["source_name"]) == {"official_ifrs_structured_file"}


def test_run_all_uses_structured_ifrs_facts_when_confirmed_by_smartlab(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "TEST", "year": 2025, "sector": "IT", "revenue_mln": 100},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    facts_path = tmp_path / "reports" / "test_ifrs_2025.csv"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        {"line_item_raw": "Revenue", "value": 100.5, "currency": "RUB", "unit_multiplier": 1_000_000},
    ]).to_csv(facts_path, index=False)
    _write_report_index(tmp_path, facts_path)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)
    unified = pd.read_parquet(tmp_path / "data" / "unified" / "financial_facts_unified.parquet")

    assert summary["facts_from_ifrs"] == 1
    assert summary["reports_extracted"] == 1
    assert unified.loc[unified["line_item_std"] == "revenue", "best_source_name"].iloc[0] == "official_ifrs_structured_file"
    assert unified.loc[unified["line_item_std"] == "revenue", "selected_reason"].iloc[0] == "official_ifrs_confirmed_by_smartlab"
