from pathlib import Path

import pandas as pd

from src.data_sources.disclosure_adapter import (
    DisclosureClient,
    discover_disclosure_reports,
    discover_report_links,
    is_disclosure_error_page,
    is_ifrs_report_link,
    normalize_issuer_name,
)


def test_normalize_issuer_name_matches_common_legal_forms():
    assert normalize_issuer_name("ПАО «ГМК Норильский никель»") == "норильский никель"
    assert normalize_issuer_name("PJSC LUKOIL") == "lukoil"


def test_discover_report_links_keeps_ifrs_and_consolidated_reports_only():
    html = """
    <html><body>
      <a href="/files/issuer-ifrs-2025.pdf">Annual IFRS financial statements 2025</a>
      <a href="/files/issuer-msfo-2024.pdf">Консолидированная финансовая отчетность по МСФО 2024</a>
      <a href="/files/issuer-presentation-2025.pdf">Investor presentation 2025</a>
      <a href="/files/issuer-rsbu-2025.pdf">РСБУ отчетность 2025</a>
    </body></html>
    """

    rows = discover_report_links(
        html=html,
        base_url="https://issuer.example/reports/",
        source_row={"ticker": "TEST", "inn": "123", "company_name": "Test Issuer"},
        checked_at="2026-06-29T00:00:00+00:00",
        from_year=2024,
        to_year=2026,
    )

    assert [row["source_url"] for row in rows] == [
        "https://issuer.example/files/issuer-ifrs-2025.pdf",
        "https://issuer.example/files/issuer-msfo-2024.pdf",
    ]
    assert set(row["reporting_standard"] for row in rows) == {"IFRS"}
    assert set(row["source_name"] for row in rows) == {"official_page_link"}


def test_ifrs_report_link_accepts_required_keywords():
    assert is_ifrs_report_link(
        "https://issuer.example/reports/annual-2025.pdf",
        "Consolidated financial statements IFRS 2025",
    )
    assert is_ifrs_report_link(
        "https://issuer.example/reports/msfo-2025.xlsx",
        "Годовая консолидированная финансовая отчетность по МСФО 2025",
    )
    assert not is_ifrs_report_link(
        "https://issuer.example/reports/presentation-2025.pdf",
        "Investor presentation 2025",
    )


def test_disclosure_client_finds_issuer_card_by_ticker_inn_or_name():
    html = """
    <html><body>
      <a href="/portal/company.aspx?id=42">ПАО Тестовый эмитент</a>
      <span>ИНН 1234567890</span>
      <span>TEST</span>
    </body></html>
    """
    client = DisclosureClient(
        base_url="https://e-disclosure.example",
        page_fetcher=lambda _url: html,
        sleep_seconds=0,
    )

    card = client.find_issuer_card(ticker="TEST", inn="1234567890", issuer_name="Тестовый эмитент")

    assert card is not None
    assert card["source_url"] == "https://e-disclosure.example/portal/company.aspx?id=42"
    assert card["match_method"] in {"inn", "ticker", "issuer_name"}


def test_error_pages_are_detected_without_exception():
    assert is_disclosure_error_page("<html><title>Access denied</title><body>WAF forbidden</body></html>")
    assert is_disclosure_error_page("<html><body>Ошибка 404. Страница не найдена</body></html>")
    assert not is_disclosure_error_page("<html><body><a href='report.pdf'>IFRS 2025</a></body></html>")


def test_discover_disclosure_reports_records_timeout_and_keeps_rows(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "secid": "TEST",
            "short_name": "Test",
            "full_name": "Test Issuer",
            "inn": "1234567890",
            "disclosure_page_url": "https://issuer.example/reports/",
            "issuer_website": "",
            "coverage_status": "smartlab_only",
        }
    ]).to_csv(data / "companies_registry.csv", index=False)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "inn": "1234567890",
            "company_name": "Test Issuer",
            "source_type": "financial_reports_page",
            "source_url": "https://issuer.example/reports/",
            "document_title": "Test reports",
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

    def fetcher(url: str) -> str:
        if "issuer.example" in url:
            raise TimeoutError("timeout")
        return ""

    rows, errors = discover_disclosure_reports(
        tmp_path,
        from_year=2024,
        to_year=2026,
        page_fetcher=fetcher,
        metadata_checker=lambda _url: {"ok": True, "http_status": 200, "content_type": "text/html", "content_length": 1, "final_url": _url, "error": ""},
        no_network=False,
    )

    assert rows[0]["source_name"] == "financial_reports_page"
    assert errors[0]["issue"] == "disclosure_page_fetch_error"
    assert "timeout" in errors[0]["detail"]


def test_registry_e_disclosure_company_id_becomes_disclosure_card(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "secid": "TEST",
            "short_name": "Test",
            "full_name": "Test Issuer",
            "inn": "1234567890",
            "disclosure_page_url": "",
            "e_disclosure_company_id": "42",
            "issuer_website": "",
            "coverage_status": "smartlab_only",
        }
    ]).to_csv(data / "companies_registry.csv", index=False)

    rows, errors = discover_disclosure_reports(tmp_path, no_network=True)

    assert errors == []
    assert rows[0]["source_name"] == "disclosure_page"
    assert rows[0]["source_url"] == "https://www.e-disclosure.ru/portal/company.aspx?id=42"
