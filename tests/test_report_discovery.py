from pathlib import Path

import pandas as pd

from src.data_sources.moex_adapter import CompanySecurity
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


def test_discover_companies_adds_moex_candidates_without_overwriting_manual_fields(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "OLD",
            "secid": "OLD",
            "short_name": "Manual Old",
            "full_name": "",
            "inn": "123",
            "coverage_status": "smartlab_only",
            "status": "ok",
            "notes": "manual",
        }
    ]).to_csv(data / "companies_registry.csv", index=False)
    moex_rows = [
        CompanySecurity(secid="OLD", short_name="MOEX Old", sec_name="Old Issuer PJSC", board="TQBR", isin="RUOLD"),
        CompanySecurity(secid="NEW", short_name="New Co", sec_name="New Issuer PJSC", board="TQBR", isin="RUNEW"),
    ]

    summary = discover_companies(tmp_path, moex_securities=moex_rows)
    reg = pd.read_csv(data / "companies_registry.csv", dtype=str).fillna("")

    old = reg[reg["ticker"] == "OLD"].iloc[0]
    new = reg[reg["ticker"] == "NEW"].iloc[0]
    assert summary["new_companies_added"] == 1
    assert old["short_name"] == "Manual Old"
    assert old["inn"] == "123"
    assert old["is_traded"] == "true"
    assert old["moex_board"] == "TQBR"
    assert new["status"] == "needs_mapping"
    assert new["coverage_status"] == "candidate"
    assert new["has_smartlab_data"] == "false"
    assert new["notes"] == "created_from_moex_discovery"


def test_discover_companies_removes_stale_auto_moex_candidates(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "STALE",
            "secid": "STALE",
            "short_name": "Stale",
            "coverage_status": "candidate",
            "status": "needs_mapping",
            "notes": "created_from_moex_discovery",
        },
        {
            "ticker": "MANUAL",
            "secid": "MANUAL",
            "short_name": "Manual",
            "coverage_status": "candidate",
            "status": "needs_mapping",
            "notes": "manual_candidate",
        },
    ]).to_csv(data / "companies_registry.csv", index=False)

    discover_companies(tmp_path, moex_securities=[
        CompanySecurity(secid="NEW", short_name="New Co", sec_name="New Issuer PJSC", board="TQBR", isin="RUNEW"),
    ])
    reg = pd.read_csv(data / "companies_registry.csv", dtype=str).fillna("")

    assert set(reg["ticker"]) == {"MANUAL", "NEW"}


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


def test_fetch_reports_indexes_official_financial_reports_page(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "",
            "company_name": "TEST",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/investors/results/",
            "document_title": "TEST official financial reports page",
            "document_type": "financial_reports_page",
            "reporting_standard": "MIXED",
            "fiscal_year": "",
            "period": "",
            "period_type": "page",
            "priority": 6,
            "active": "true",
            "notes": "official issuer page",
        }
    ]).to_csv(data / "company_sources.csv", index=False)

    summary = fetch_reports(tmp_path, no_network=True)
    idx = pd.read_parquet(data / "report_index.parquet")

    assert summary["reports_found"] == 1
    assert idx.loc[0, "download_status"] == "metadata_only"
    assert idx.loc[0, "source_name"] == "financial_reports_page"
    assert idx.loc[0, "document_type"] == "financial_reports_page"
    assert idx.loc[0, "reporting_standard"] == "MIXED"
    assert idx.loc[0, "period_type"] == "page"


def test_fetch_reports_discovers_report_links_from_official_page(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "",
            "company_name": "TEST",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/investors/results/",
            "document_title": "TEST official financial reports page",
            "document_type": "financial_reports_page",
            "reporting_standard": "MIXED",
            "fiscal_year": "",
            "period": "",
            "period_type": "page",
            "priority": 6,
            "active": "true",
            "notes": "official issuer page",
        }
    ]).to_csv(data / "company_sources.csv", index=False)

    html = """
    <html><body>
      <a href="/files/test-ifrs-2025.pdf">IFRS annual report 2025</a>
      <a href="presentation-2025.pdf">Investor presentation 2025</a>
      <a href="/files/test-rsbu-2025.pdf">РСБУ отчетность 2025</a>
    </body></html>
    """

    summary = fetch_reports(
        tmp_path,
        no_network=False,
        metadata_checker=lambda _url: {
            "ok": True,
            "http_status": 200,
            "content_type": "text/html",
            "content_length": len(html),
            "final_url": "https://issuer.example/investors/results/",
            "error": "",
        },
        page_fetcher=lambda _url: html,
    )
    idx = pd.read_parquet(data / "report_index.parquet")
    reports = idx[idx["source_name"] == "official_page_link"]

    assert summary["reports_found"] == 4
    assert set(reports["source_url"]) == {
        "https://issuer.example/files/test-ifrs-2025.pdf",
        "https://issuer.example/investors/results/presentation-2025.pdf",
        "https://issuer.example/files/test-rsbu-2025.pdf",
    }
    assert set(reports["download_status"]) == {"link_discovered"}
    assert reports.loc[reports["source_url"].str.contains("ifrs"), "reporting_standard"].iloc[0] == "IFRS"
    assert reports.loc[reports["source_url"].str.contains("rsbu"), "reporting_standard"].iloc[0] == "RAS"
    assert set(reports["fiscal_year"]) == {2025}


def test_fetch_reports_applies_year_window_to_discovered_report_links(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/reports/",
            "document_title": "TEST reports",
            "document_type": "financial_reports_page",
            "reporting_standard": "MIXED",
            "period_type": "page",
            "active": "true",
        }
    ]).to_csv(data / "company_sources.csv", index=False)
    html = """
    <a href="/reports/test-ifrs-2024.pdf">IFRS annual report 2024</a>
    <a href="/reports/test-ifrs-2025.pdf">IFRS annual report 2025</a>
    """

    fetch_reports(
        tmp_path,
        from_year=2025,
        to_year=2025,
        no_network=False,
        metadata_checker=lambda _url: {
            "ok": True,
            "http_status": 200,
            "content_type": "text/html",
            "content_length": len(html),
            "final_url": "https://issuer.example/reports/",
            "error": "",
        },
        page_fetcher=lambda _url: html,
    )
    idx = pd.read_parquet(data / "report_index.parquet")
    reports = idx[idx["source_name"] == "official_page_link"]

    assert set(reports["period"]) == {"2025"}


def test_fetch_reports_can_download_discovered_report_files(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/reports/",
            "document_title": "TEST reports",
            "document_type": "financial_reports_page",
            "reporting_standard": "MIXED",
            "period_type": "page",
            "active": "true",
        }
    ]).to_csv(data / "company_sources.csv", index=False)
    html = '<a href="/reports/test-ifrs-2025.xlsx">IFRS annual report 2025</a>'

    summary = fetch_reports(
        tmp_path,
        no_network=False,
        download=True,
        metadata_checker=lambda _url: {
            "ok": True,
            "http_status": 200,
            "content_type": "text/html",
            "content_length": len(html),
            "final_url": "https://issuer.example/reports/",
            "error": "",
        },
        page_fetcher=lambda _url: html,
        downloader=lambda _url: {
            "ok": True,
            "content": b"fake-xlsx-bytes",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "final_url": "https://issuer.example/reports/test-ifrs-2025.xlsx",
            "http_status": 200,
            "error": "",
        },
    )
    idx = pd.read_parquet(data / "report_index.parquet")
    report = idx[idx["source_name"] == "official_page_link"].iloc[0]

    assert summary["reports_downloaded"] == 1
    assert report["download_status"] == "downloaded"
    assert report["file_hash"]
    assert report["file_path"] == "data/raw_reports/TEST/2025/test-ifrs-2025.xlsx"
    assert (tmp_path / report["file_path"]).read_bytes() == b"fake-xlsx-bytes"


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
