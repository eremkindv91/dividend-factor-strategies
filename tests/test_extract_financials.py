from pathlib import Path

import pandas as pd

from src.normalization.ifrs_mapper import load_ifrs_mapping
from src.pipeline.extract_financials import extract_financials, extract_text_facts
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


def test_extract_financials_marks_report_index_extracted(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2025.csv"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        {"line_item_raw": "Revenue", "value": 100.5, "currency": "RUB", "unit_multiplier": 1_000_000},
    ]).to_csv(facts_path, index=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    report_index = pd.read_parquet(tmp_path / "data" / "report_index.parquet")

    assert summary["reports_extracted"] == 1
    assert report_index.loc[0, "extraction_status"] == "extracted"
    assert report_index.loc[0, "parse_status"] == "parsed"
    assert report_index.loc[0, "quality_status"] == "structured_extracted"


def test_extract_financials_from_wide_xlsx_report(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2025.xlsx"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        ["RUB million", None, None],
        ["Revenue", 100.5, 90.0],
        ["Net income", 20.0, 18.0],
        ["Total assets", 500.0, 450.0],
        ["Total equity", 200.0, 180.0],
        ["Cash and cash equivalents", 50.0, 40.0],
    ]).to_excel(facts_path, index=False, header=["line", "2025", "2024"])
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 1
    assert set(facts["line_item_std"]) == {"revenue", "net_income", "total_assets", "total_equity", "cash_and_equivalents"}
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 100_500_000
    assert set(facts["source_name"]) == {"official_ifrs_xlsx"}
    assert set(facts["extraction_method"]) == {"wide_xlsx"}


def test_extract_financials_prefers_full_year_column_over_quarter_in_wide_xlsx(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2024_full_year.xlsx"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        [None, None, "2024", None, None, None, None],
        ["RUB million", "line", "Q1", "Q2", "Q3", "Q4", "Full year"],
        [None, "Total revenue", 10.0, 20.0, 30.0, 40.0, 100.0],
        [None, "Net profit", 1.0, 2.0, 3.0, 4.0, 10.0],
    ]).to_excel(facts_path, index=False, header=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 1
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 100_000_000
    assert facts.loc[facts["line_item_std"] == "net_income", "value_normalized"].iloc[0] == 10_000_000


def test_extract_financials_prefers_12m_column_over_quarter_in_wide_xlsx(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2021_12m.xlsx"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        ["USD million", None, None, None, None],
        ["line", "Q1 2021", "Q2 2021", "Q4 2021", "12M 2021"],
        ["Revenue", 25.0, 30.0, 40.0, 150.0],
        ["Total assets", 300.0, 310.0, 320.0, 400.0],
    ]).to_excel(facts_path, index=False, header=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 1
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 150_000_000
    assert facts.loc[facts["line_item_std"] == "total_assets", "value_normalized"].iloc[0] == 400_000_000


def test_extract_financials_prefers_total_revenue_over_component_revenue(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2024_revenue_components.xlsx"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        [None, None, "2024", None],
        ["RUB million", "line", "Q4", "Full year"],
        [None, "Service revenue", 40.0, 80.0],
        [None, "Sales of goods", 10.0, 20.0],
        [None, "Total revenue", 50.0, 100.0],
    ]).to_excel(facts_path, index=False, header=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 1
    assert facts.loc[facts["line_item_std"] == "revenue", "line_item_raw"].iloc[0] == "Total revenue"
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 100_000_000


def test_extract_text_facts_uses_current_period_value_after_note_number():
    mapping = load_ifrs_mapping(Path("data/mapping/ifrs_line_items.yaml"))
    report = {"report_id": "pdf-report", "ticker": "PDF", "fiscal_year": 2025, "period": "2025", "source_url": "https://issuer.example/report.pdf"}
    text = "\n".join([
        "RUB million",
        "Revenues 5 573,628 507,689",
        "Profit for the year 114,243 84,469",
        "Total assets 13 027 819,1 12 092 628,5",
    ])

    facts = extract_text_facts(report, text, mapping, "2026-06-29T00:00:00+00:00")
    values = {row["line_item_std"]: row["value_normalized"] for row in facts}

    assert values["revenue"] == 573_628_000_000
    assert values["net_income"] == 114_243_000_000
    assert values["total_assets"] == 13_027_819_100_000


def test_extract_financials_keeps_committed_official_seed_without_raw_downloads(tmp_path: Path):
    seed = tmp_path / "data" / "official_ifrs_facts.csv"
    seed.parent.mkdir(parents=True)
    pd.DataFrame([
        {
            "fact_id": "seed-SBER-2020-revenue",
            "report_id": "official-seed-SBER-2020",
            "ticker": "SBER",
            "inn": "7707083893",
            "company_name": "Sberbank",
            "fiscal_year": 2020,
            "period": "2020",
            "period_type": "annual",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_raw": "Net interest income",
            "line_item_std": "revenue",
            "value_raw": "100",
            "value_normalized": 100_000_000.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1_000_000,
            "is_calculated": False,
            "formula": "",
            "source_page": "",
            "source_table_id": "",
            "source_url": "https://www.sberbank.com/common/img/uploaded/files/info/en/sberbank_ifrs_12m2020_eng.pdf",
            "source_name": "official_ifrs_pdf_text",
            "source_type": "official_ifrs",
            "source_priority": 3,
            "is_legacy_data": False,
            "extraction_method": "verified_seed",
            "confidence_score": 0.9,
            "quality_score": 88,
            "validation_status": "verified_seed",
            "created_at": "2026-06-28T00:00:00+00:00",
        }
    ]).to_csv(seed, index=False)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["facts_from_ifrs"] == 1
    assert summary["seed_facts_loaded"] == 1
    assert facts.loc[0, "ticker"] == "SBER"
    assert facts.loc[0, "source_type"] == "official_ifrs"


def test_extract_financials_writes_mixed_raw_value_types_to_parquet(tmp_path: Path):
    seed = tmp_path / "data" / "official_ifrs_facts.csv"
    seed.parent.mkdir(parents=True)
    pd.DataFrame([
        {
            "fact_id": "seed-1",
            "report_id": "seed-report",
            "ticker": "SEED",
            "fiscal_year": 2024,
            "period": "2024",
            "period_type": "annual",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_raw": "Revenue",
            "line_item_std": "revenue",
            "value_raw": "1 000",
            "value_normalized": 1000.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1,
            "source_url": "https://issuer.example/seed.pdf",
            "source_name": "official_ifrs_pdf_text",
            "source_type": "official_ifrs",
            "source_priority": 3,
            "confidence_score": 0.9,
            "quality_score": 88,
            "validation_status": "verified_seed",
        }
    ]).to_csv(seed, index=False)

    facts_path = tmp_path / "reports" / "test_ifrs_2025.csv"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        {"line_item_raw": "Net profit", "value": 20.0, "currency": "RUB", "unit_multiplier": 1_000_000},
    ]).to_csv(facts_path, index=False)
    _write_report_index(tmp_path, facts_path)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["facts_from_ifrs"] == 2
    assert facts["value_raw"].map(type).eq(str).all()


def test_extract_financials_does_not_reparse_reports_covered_by_verified_seed(tmp_path: Path):
    facts_path = tmp_path / "reports" / "test_ifrs_2025.csv"
    facts_path.parent.mkdir(parents=True)
    pd.DataFrame([
        {"line_item_raw": "Net profit", "value": 20.0, "currency": "RUB", "unit_multiplier": 1_000_000},
    ]).to_csv(facts_path, index=False)
    _write_report_index(tmp_path, facts_path)

    seed = tmp_path / "data" / "official_ifrs_facts.csv"
    pd.DataFrame([
        {
            "fact_id": "seed-report-test-2025-revenue",
            "report_id": "report-test-2025",
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "period_type": "annual",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_raw": "Revenue",
            "line_item_std": "revenue",
            "value_raw": "100",
            "value_normalized": 100_000_000.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1_000_000,
            "source_url": "https://issuer.example/reports/test-2025-ifrs.csv",
            "source_name": "official_ifrs_verified_seed",
            "source_type": "official_ifrs",
            "source_priority": 2,
            "confidence_score": 0.98,
            "quality_score": 96,
            "validation_status": "verified_seed",
        }
    ]).to_csv(seed, index=False)

    summary = extract_financials(tmp_path, skip_ocr=True)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "ifrs_financial_facts.parquet")

    assert summary["reports_extracted"] == 0
    assert summary["reports_skipped_by_seed"] == 1
    assert set(facts["line_item_std"]) == {"revenue"}
    report_index = pd.read_parquet(tmp_path / "data" / "report_index.parquet")
    assert report_index.loc[0, "extraction_status"] == "skipped_verified_seed"
    assert report_index.loc[0, "parse_status"] == "skipped"
    assert report_index.loc[0, "quality_status"] == "verified_seed_present"


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
