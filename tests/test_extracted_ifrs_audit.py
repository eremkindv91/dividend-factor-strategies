from pathlib import Path

import pandas as pd

from src.quality.audit_extracted_ifrs import build_extracted_ifrs_audit, write_extracted_ifrs_audit


def _ifrs_fact(ticker: str, metric: str, value: float, source_name: str, report_id: str = "report") -> dict:
    return {
        "fact_id": f"{source_name}-{ticker}-{metric}",
        "report_id": report_id,
        "ticker": ticker,
        "inn": "",
        "company_name": ticker,
        "fiscal_year": 2025,
        "period": "2025",
        "period_type": "annual",
        "reporting_standard": "IFRS",
        "statement_type": "income_statement",
        "line_item_raw": metric,
        "line_item_std": metric,
        "value_raw": value,
        "value_normalized": value,
        "currency": "RUB",
        "unit_type": "currency",
        "unit_multiplier": 1,
        "is_calculated": False,
        "formula": "",
        "source_page": "",
        "source_table_id": "statement",
        "source_url": f"https://issuer.example/{ticker}.xlsx",
        "source_name": source_name,
        "source_type": "official_ifrs",
        "source_priority": 2,
        "is_legacy_data": False,
        "extraction_method": "wide_xlsx",
        "confidence_score": 0.95,
        "quality_score": 92,
        "validation_status": "structured_extracted",
        "created_at": "2026-06-29T00:00:00+00:00",
    }


def _unified_fact(ticker: str, metric: str, official: float | None, smartlab: float | None) -> dict:
    return {
        "ticker": ticker,
        "company_name": ticker,
        "fiscal_year": 2025,
        "period": "2025",
        "line_item_std": metric,
        "currency": "RUB",
        "best_value": official if official is not None else smartlab,
        "best_source_name": "official_ifrs_xlsx" if official is not None else "smartlab",
        "best_source_type": "official_ifrs" if official is not None else "aggregator",
        "best_source_url": f"https://issuer.example/{ticker}.xlsx" if official is not None else "https://smart-lab.ru/",
        "best_quality_score": 92 if official is not None else 70,
        "smartlab_value": smartlab,
        "smartlab_source_url": "https://smart-lab.ru/",
        "official_ifrs_value": official,
        "official_ifrs_source_url": f"https://issuer.example/{ticker}.xlsx" if official is not None else None,
        "selected_reason": "official_ifrs_confirmed_by_smartlab",
        "conflict_flag": False,
        "conflict_type": None,
        "needs_manual_review": False,
        "is_reliable": True,
    }


def test_extracted_ifrs_audit_classifies_non_seed_facts_without_promoting_seed(tmp_path: Path):
    data_dir = tmp_path / "data"
    processed_dir = data_dir / "processed"
    unified_dir = data_dir / "unified"
    processed_dir.mkdir(parents=True)
    unified_dir.mkdir(parents=True)
    seed_rows = [
        _ifrs_fact("MATCH", "revenue", 100.0, "official_ifrs_verified_seed", "seed-match"),
        _ifrs_fact("CNFL", "revenue", 100.0, "official_ifrs_verified_seed", "seed-conflict"),
    ]
    pd.DataFrame(seed_rows).to_csv(data_dir / "official_ifrs_facts.csv", index=False)
    pd.DataFrame(seed_rows + [
        _ifrs_fact("MATCH", "revenue", 100.5, "official_ifrs_xlsx", "xlsx-match"),
        _ifrs_fact("CNFL", "revenue", 130.0, "official_ifrs_xlsx", "xlsx-conflict"),
        _ifrs_fact("NEW", "revenue", 101.0, "official_ifrs_xlsx", "xlsx-new"),
    ]).to_parquet(processed_dir / "ifrs_financial_facts.parquet", index=False)
    pd.DataFrame([
        _unified_fact("MATCH", "revenue", 100.5, 100.0),
        _unified_fact("CNFL", "revenue", 130.0, 100.0),
        _unified_fact("NEW", "revenue", 101.0, 100.0),
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)

    audit = build_extracted_ifrs_audit(tmp_path)
    statuses = dict(zip(audit["ticker"], audit["audit_status"]))

    assert list(audit["source_name"].unique()) == ["official_ifrs_xlsx"]
    assert statuses == {
        "MATCH": "matches_verified_seed",
        "CNFL": "conflict_vs_verified_seed",
        "NEW": "confirmed_by_smartlab",
    }
    assert audit.loc[audit["ticker"] == "CNFL", "needs_manual_review"].iloc[0] is True

    before_seed = (data_dir / "official_ifrs_facts.csv").read_text(encoding="utf-8")
    summary = write_extracted_ifrs_audit(tmp_path)
    after_seed = (data_dir / "official_ifrs_facts.csv").read_text(encoding="utf-8")

    assert before_seed == after_seed
    assert summary["extracted_ifrs_facts"] == 3
    assert summary["needs_manual_review"] == 1
    assert (data_dir / "manual_review" / "extracted_ifrs_audit.csv").exists()
