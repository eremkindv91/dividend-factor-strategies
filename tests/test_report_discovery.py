from pathlib import Path

import pandas as pd

from src.pipeline.discover_companies import discover_companies
from src.pipeline.fetch_reports import fetch_reports
from src.pipeline.run_all import run_all


def test_discover_companies_writes_company_sources_from_registry(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "secid": "TEST",
            "short_name": "TEST",
            "disclosure_page_url": "https://example.com/disclosure/test",
            "issuer_website": "https://issuer.example/test",
            "coverage_status": "smartlab_only",
        }
    ]).to_csv(data / "companies_registry.csv", index=False)

    summary = discover_companies(tmp_path)
    sources = pd.read_csv(data / "company_sources.csv")

    assert summary["source_rows"] == 2
    assert set(sources["source_type"]) == {"disclosure_page", "issuer_website"}
    assert set(sources["ticker"]) == {"TEST"}


def test_fetch_reports_builds_report_index_from_manual_sources(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "",
            "company_name": "TEST",
            "source_type": "manual_report",
            "source_url": "https://issuer.example/reports/test-2025-ifrs.pdf",
            "document_title": "TEST IFRS 2025",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "priority": 1,
            "active": "true",
            "notes": "unit test",
        }
    ]).to_csv(data / "company_sources.csv", index=False)

    summary = fetch_reports(tmp_path, from_year=2025, to_year=2025, ticker="TEST", no_network=True)
    idx = pd.read_parquet(data / "report_index.parquet")

    assert summary["reports_found"] == 1
    assert summary["reports_downloaded"] == 0
    assert idx.loc[0, "download_status"] == "metadata_only"
    assert idx.loc[0, "source_url"] == "https://issuer.example/reports/test-2025-ifrs.pdf"
    assert idx.loc[0, "reporting_standard"] == "IFRS"


def test_fetch_reports_limit_companies_filters_current_run_only(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "AAA",
            "source_type": "manual_report",
            "source_url": "https://issuer.example/reports/aaa-2025-ifrs.pdf",
            "document_title": "AAA IFRS 2025",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "active": "true",
        },
        {
            "ticker": "BBB",
            "source_type": "manual_report",
            "source_url": "https://issuer.example/reports/bbb-2025-ifrs.pdf",
            "document_title": "BBB IFRS 2025",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "active": "true",
        },
    ]).to_csv(data / "company_sources.csv", index=False)

    summary = fetch_reports(tmp_path, limit_companies=1, no_network=True)
    idx = pd.read_parquet(data / "report_index.parquet")
    sources = pd.read_csv(data / "company_sources.csv")

    assert summary["reports_found"] == 1
    assert set(idx["ticker"]) == {"AAA"}
    assert set(sources["ticker"]) == {"AAA", "BBB"}


def test_run_all_default_stage2_keeps_smartlab_baseline_when_no_reports(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "TEST", "year": 2025, "sector": "IT", "revenue_mln": 10},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)
    idx = pd.read_parquet(tmp_path / "data" / "report_index.parquet")

    assert summary["mode"] == "default"
    assert summary["facts_from_smartlab"] == 1
    assert summary["reports_found"] == 0
    assert idx.empty
