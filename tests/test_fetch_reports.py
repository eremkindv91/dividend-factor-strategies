from pathlib import Path

import pandas as pd

from src.pipeline.fetch_reports import fetch_reports


def _write_company_sources(root: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "1234567890",
            "company_name": "Test Issuer",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/reports/",
            "document_title": "Test official reports page",
            "document_type": "financial_reports_page",
            "reporting_standard": "MIXED",
            "fiscal_year": "",
            "period": "",
            "period_type": "page",
            "priority": 6,
            "active": "true",
            "notes": "unit test",
        }
    ]).to_csv(data / "company_sources.csv", index=False)


def test_fetch_reports_discovers_ifrs_links_and_updates_report_index(tmp_path: Path):
    _write_company_sources(tmp_path)
    html = """
    <html><body>
      <a href="/files/test-ifrs-2025.pdf">Annual IFRS financial statements 2025</a>
      <a href="/files/test-msfo-2024.xlsx">МСФО консолидированная финансовая отчетность 2024</a>
    </body></html>
    """

    summary = fetch_reports(
        tmp_path,
        from_year=2024,
        to_year=2026,
        no_network=False,
        metadata_checker=lambda url: {
            "ok": True,
            "http_status": 200,
            "content_type": "text/html" if url.endswith("/reports/") else "application/pdf",
            "content_length": 123,
            "final_url": url,
            "error": "",
        },
        page_fetcher=lambda _url: html,
    )
    idx = pd.read_parquet(tmp_path / "data" / "report_index.parquet")

    assert summary["reports_found_from_disclosure"] == 3
    assert summary["reports_downloaded"] == 0
    assert summary["disclosure_errors_count"] == 0
    assert summary["last_disclosure_check"]
    assert summary["report_index_updated_at"]
    assert len(idx) == 3
    assert (idx["file_path"].fillna("") == "").all()
    assert set(idx[idx["source_name"] == "official_page_link"]["reporting_standard"]) == {"IFRS"}
    assert not (tmp_path / "data" / "raw_reports").exists()


def test_fetch_reports_writes_disclosure_errors_without_failing_pipeline(tmp_path: Path):
    _write_company_sources(tmp_path)

    summary = fetch_reports(
        tmp_path,
        no_network=False,
        metadata_checker=lambda url: {
            "ok": True,
            "http_status": 200,
            "content_type": "text/html",
            "content_length": 123,
            "final_url": url,
            "error": "",
        },
        page_fetcher=lambda _url: "<html><body>Access denied by WAF</body></html>",
    )
    errors = pd.read_csv(tmp_path / "data" / "manual_review" / "disclosure_errors.csv")

    assert summary["reports_found_from_disclosure"] == 1
    assert summary["disclosure_errors_count"] == 1
    assert errors.loc[0, "issue"] == "disclosure_error_page"


def test_fetch_reports_downloads_raw_files_only_with_explicit_download_flag(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "1234567890",
            "company_name": "Test Issuer",
            "source_type": "manual_report",
            "source_url": "https://issuer.example/files/test-ifrs-2025.pdf",
            "document_title": "Annual IFRS financial statements 2025",
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

    fetch_reports(
        tmp_path,
        no_network=False,
        metadata_checker=lambda url: {"ok": True, "http_status": 200, "content_type": "application/pdf", "content_length": 10, "final_url": url, "error": ""},
        downloader=lambda _url: {"ok": True, "content": b"PDF bytes", "content_type": "application/pdf", "http_status": 200, "final_url": _url},
    )
    assert not (tmp_path / "data" / "raw_reports").exists()

    summary = fetch_reports(
        tmp_path,
        no_network=False,
        download=True,
        metadata_checker=lambda url: {"ok": True, "http_status": 200, "content_type": "application/pdf", "content_length": 10, "final_url": url, "error": ""},
        downloader=lambda _url: {"ok": True, "content": b"PDF bytes", "content_type": "application/pdf", "http_status": 200, "final_url": _url},
    )

    assert summary["reports_downloaded"] == 1
    assert list((tmp_path / "data" / "raw_reports").rglob("*.pdf"))
