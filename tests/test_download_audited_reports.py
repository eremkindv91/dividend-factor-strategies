from pathlib import Path

import pandas as pd

from src.pipeline.download_audited_reports import download_audited_reports


REPORT_COLUMNS = [
    "report_id", "ticker", "inn", "company_name", "period", "period_type", "fiscal_year",
    "publication_date", "document_title", "document_type", "reporting_standard", "file_path",
    "source_url", "source_name", "file_hash", "download_status", "parse_status",
    "extraction_status", "quality_status", "url_status", "http_status", "content_type",
    "content_length", "final_url", "url_checked_at", "created_at", "updated_at",
]


def _report(report_id: str, ticker: str, url: str, title: str, content_type: str = "") -> dict:
    return {
        "report_id": report_id,
        "ticker": ticker,
        "inn": "",
        "company_name": f"{ticker} Issuer",
        "period": "2025",
        "period_type": "annual",
        "fiscal_year": 2025,
        "publication_date": "",
        "document_title": title,
        "document_type": "IFRS annual",
        "reporting_standard": "IFRS",
        "file_path": "",
        "source_url": url,
        "source_name": "manual_report",
        "file_hash": "",
        "download_status": "metadata_checked",
        "parse_status": "not_parsed",
        "extraction_status": "not_extracted",
        "quality_status": "source_url_ok",
        "url_status": "ok",
        "http_status": 200,
        "content_type": content_type,
        "content_length": None,
        "final_url": url,
        "url_checked_at": "2026-06-29T00:00:00+00:00",
        "created_at": "2026-06-29T00:00:00+00:00",
        "updated_at": "2026-06-29T00:00:00+00:00",
    }


def test_download_audited_reports_downloads_only_ok_file_links(tmp_path: Path):
    data = tmp_path / "data"
    audit_dir = tmp_path / "results" / "disclosure"
    data.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    reports = pd.DataFrame([
        _report("ok-pdf", "OKPDF", "https://issuer.example/ok.pdf", "Official IFRS PDF", "application/pdf"),
        _report("ok-html", "OKHTML", "https://issuer.example/reports", "Official IFRS page", "text/html"),
        _report("review-xlsx", "REVIEW", "https://issuer.example/review.xlsx", "Needs review XLSX", "application/vnd.ms-excel"),
        _report("failed-pdf", "FAIL", "https://issuer.example/fail.pdf", "Failed PDF", "application/pdf"),
    ], columns=REPORT_COLUMNS)
    reports.to_parquet(data / "report_index.parquet", index=False)
    pd.DataFrame([
        {"ticker": "OKPDF", "source_url": "https://issuer.example/ok.pdf", "audit_status": "ok"},
        {"ticker": "OKHTML", "source_url": "https://issuer.example/reports", "audit_status": "ok"},
        {"ticker": "REVIEW", "source_url": "https://issuer.example/review.xlsx", "audit_status": "needs_review"},
        {"ticker": "FAIL", "source_url": "https://issuer.example/fail.pdf", "audit_status": "failed"},
    ]).to_csv(audit_dir / "report_index_audit.csv", index=False)

    calls = []

    def downloader(url: str) -> dict:
        calls.append(url)
        return {
            "ok": True,
            "content": b"fake pdf bytes",
            "content_type": "application/pdf",
            "http_status": 200,
            "final_url": url,
        }

    summary = download_audited_reports(tmp_path, downloader=downloader, now="2026-06-29T00:00:00+00:00")
    updated = pd.read_parquet(data / "report_index.parquet")

    assert calls == ["https://issuer.example/ok.pdf"]
    assert summary["eligible_reports"] == 1
    assert summary["reports_downloaded"] == 1
    assert summary["skipped_non_file_ok"] == 1
    assert summary["skipped_not_ok"] == 2
    ok_row = updated.loc[updated["report_id"] == "ok-pdf"].iloc[0]
    assert ok_row["download_status"] == "downloaded"
    assert ok_row["file_path"].startswith("data/raw_reports/OKPDF/2025/")
    assert (tmp_path / ok_row["file_path"]).exists()
    assert updated.loc[updated["report_id"] == "review-xlsx", "file_path"].iloc[0] == ""
