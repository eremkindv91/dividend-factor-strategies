from pathlib import Path

import pandas as pd

from src.pipeline.unify_financial_data import unify_financial_data


def _fact(source_name: str, source_type: str, value: float, quality: int, priority: int) -> dict:
    return {
        "fact_id": f"{source_name}-{value}",
        "ticker": "TEST",
        "inn": None,
        "company_name": None,
        "fiscal_year": 2025,
        "period": "2025",
        "reporting_standard": "IFRS",
        "statement_type": "income_statement",
        "line_item_std": "revenue",
        "value_normalized": value,
        "currency": "RUB",
        "source_name": source_name,
        "source_type": source_type,
        "source_url": f"https://example.com/{source_name}",
        "source_priority": priority,
        "confidence_score": quality / 100,
        "quality_score": quality,
        "extraction_method": "pdf_table",
    }


def test_unified_layer_prefers_confirmed_official_source(tmp_path: Path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame([
        _fact("smartlab", "aggregator", 100.0, 70, 7),
        _fact("official_ifrs_pdf_table", "official_ifrs", 100.5, 90, 4),
    ]).to_parquet(processed / "smartlab_fundamentals.parquet", index=False)

    summary = unify_financial_data(tmp_path)
    unified = pd.read_parquet(tmp_path / "data" / "unified" / "financial_facts_unified.parquet")

    assert summary["facts_unified"] == 1
    assert unified.loc[0, "best_source_name"] == "official_ifrs_pdf_table"
    assert unified.loc[0, "selected_reason"] == "official_ifrs_confirmed_by_smartlab"


def test_unified_layer_routes_large_conflict_to_manual_review(tmp_path: Path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame([
        _fact("smartlab", "aggregator", 100.0, 70, 7),
        _fact("official_ifrs_pdf_table", "official_ifrs", 120.0, 90, 4),
    ]).to_parquet(processed / "smartlab_fundamentals.parquet", index=False)

    summary = unify_financial_data(tmp_path)
    conflicts = pd.read_csv(tmp_path / "data" / "manual_review" / "source_conflicts.csv")

    assert summary["conflicts_count"] == 1
    assert conflicts.loc[0, "conflict_type"] == "value_conflict_gt_5pct"
    assert conflicts.loc[0, "difference_abs"] == 20.0
    assert conflicts.loc[0, "difference_pct"] == 20.0
