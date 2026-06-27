from pathlib import Path

import pandas as pd

from src.pipeline.fetch_reports import fetch_reports


def test_invalid_report_url_goes_to_manual_review(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "source_type": "manual_report",
            "source_url": "javascript:alert(1)",
            "document_title": "bad url",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "active": "true",
        }
    ]).to_csv(data / "company_sources.csv", index=False)

    summary = fetch_reports(tmp_path, no_network=True)
    idx = pd.read_parquet(data / "report_index.parquet")
    errors = pd.read_csv(data / "manual_review" / "report_source_errors.csv")

    assert summary["reports_found"] == 0
    assert idx.empty
    assert errors.loc[0, "issue"] == "invalid_url_scheme"


def test_network_metadata_check_records_headers_without_download(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "source_type": "manual_report",
            "source_url": "https://issuer.example/reports/test-2025-ifrs.pdf",
            "document_title": "TEST IFRS 2025",
            "document_type": "IFRS annual",
            "reporting_standard": "IFRS",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "active": "true",
        }
    ]).to_csv(data / "company_sources.csv", index=False)

    def checker(url: str) -> dict:
        return {
            "url": url,
            "ok": True,
            "http_status": 200,
            "content_type": "application/pdf",
            "content_length": 12345,
            "final_url": url,
            "error": "",
        }

    summary = fetch_reports(tmp_path, no_network=False, metadata_checker=checker)
    idx = pd.read_parquet(data / "report_index.parquet")

    assert summary["reports_found"] == 1
    assert summary["reports_downloaded"] == 0
    assert idx.loc[0, "download_status"] == "metadata_checked"
    assert idx.loc[0, "url_status"] == "ok"
    assert idx.loc[0, "http_status"] == 200
    assert idx.loc[0, "content_type"] == "application/pdf"
    assert idx.loc[0, "content_length"] == 12345
