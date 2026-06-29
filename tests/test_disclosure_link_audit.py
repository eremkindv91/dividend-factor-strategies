from pathlib import Path

import pandas as pd

from src.quality.audit_disclosure_links import audit_report_index


def _row(ticker: str, title: str, url: str, doc_type: str = "IFRS annual", standard: str = "IFRS") -> dict:
    return {
        "report_id": f"report-{ticker}-{len(url)}",
        "ticker": ticker,
        "inn": "",
        "company_name": f"{ticker} Issuer",
        "period": "2025",
        "period_type": "annual",
        "fiscal_year": 2025,
        "publication_date": "",
        "document_title": title,
        "document_type": doc_type,
        "reporting_standard": standard,
        "file_path": "",
        "source_url": url,
        "source_name": "official_page_link",
        "file_hash": "",
        "download_status": "metadata_only",
        "parse_status": "not_parsed",
        "extraction_status": "not_extracted",
        "quality_status": "not_checked",
        "url_status": "not_checked",
        "http_status": None,
        "content_type": "",
        "content_length": None,
        "final_url": url,
        "url_checked_at": "",
        "created_at": "2026-06-29T00:00:00+00:00",
        "updated_at": "2026-06-29T00:00:00+00:00",
    }


def test_audit_report_index_classifies_links_and_writes_outputs(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        _row("OK", "Official IFRS financial statements 2025", "https://fs.moex.com/f/report.pdf"),
        _row("BLOCK", "Official IFRS financial statements 2025", "https://issuer.example/blocked.pdf"),
        _row("FAIL", "Official IFRS financial statements 2025", "https://issuer.example/missing.pdf"),
        _row("REJECT", "Investor presentation 2025", "https://issuer.example/presentation.pdf", "presentation", "MIXED"),
        _row("REVIEW", "Official financial reports page", "https://www.lukoil.com/reports", "financial_reports_page", "MIXED"),
    ]).to_parquet(data / "report_index.parquet", index=False)

    def checker(url: str) -> dict:
        if "fs.moex.com" in url:
            return {"http_status": 200, "content_type": "application/pdf", "final_url": url, "sample": "", "error": ""}
        if "blocked" in url:
            return {"http_status": 403, "content_type": "text/html", "final_url": url, "sample": "Access denied", "error": ""}
        if "missing" in url:
            return {"http_status": 404, "content_type": "text/html", "final_url": url, "sample": "Not found", "error": ""}
        if "presentation" in url:
            return {"http_status": 200, "content_type": "application/pdf", "final_url": url, "sample": "", "error": ""}
        return {"http_status": 200, "content_type": "text/html", "final_url": url, "sample": "Financial reports", "error": ""}

    summary = audit_report_index(tmp_path, checker=checker, generated_at="2026-06-29T00:00:00+00:00")
    audit = pd.read_csv(tmp_path / "results" / "disclosure" / "report_index_audit.csv")

    assert summary == {
        "total_links": 5,
        "ok": 1,
        "blocked": 1,
        "failed": 1,
        "rejected": 1,
        "needs_review": 1,
        "unique_companies": 5,
        "ifrs_candidates": 3,
        "annual_ifrs_candidates": 3,
        "interim_ifrs_candidates": 0,
        "generated_at": "2026-06-29T00:00:00+00:00",
    }
    assert audit["audit_status"].value_counts().to_dict() == {
        "ok": 1,
        "blocked": 1,
        "failed": 1,
        "rejected": 1,
        "needs_review": 1,
    }
    assert not (tmp_path / "data" / "raw_reports").exists()
