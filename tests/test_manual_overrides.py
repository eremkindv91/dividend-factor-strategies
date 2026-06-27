from pathlib import Path

import pandas as pd

from src.pipeline.unify_financial_data import unify_financial_data


def test_manual_override_file_wins_over_smartlab(tmp_path: Path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame([
        {
            "fact_id": "smartlab",
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_std": "revenue",
            "value_normalized": 100.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1_000_000,
            "source_name": "smartlab",
            "source_type": "aggregator",
            "source_url": "https://smart-lab.ru/q/TEST/f/y/",
            "source_priority": 7,
            "confidence_score": 0.7,
            "quality_score": 70,
            "extraction_method": "legacy_panel_migration",
        }
    ]).to_parquet(processed / "smartlab_fundamentals.parquet", index=False)

    overrides = tmp_path / "data" / "manual_overrides"
    overrides.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_std": "revenue",
            "value_normalized": 130.0,
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1,
            "source_url": "manual://TEST/2025/revenue",
            "reason": "checked against issuer report by analyst",
        }
    ]).to_csv(overrides / "financial_facts.csv", index=False)

    unify_financial_data(tmp_path)
    unified = pd.read_parquet(tmp_path / "data" / "unified" / "financial_facts_unified.parquet")

    assert unified.loc[0, "best_source_name"] == "manual_override"
    assert unified.loc[0, "manual_override_value"] == 130.0
    assert unified.loc[0, "best_value"] == 130.0
    assert unified.loc[0, "is_reliable"] == True
