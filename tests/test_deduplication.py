import pandas as pd

from src.unification.deduplication import deduplicate_facts


def test_deduplicate_keeps_best_quality_for_same_contract():
    df = pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_std": "revenue",
            "currency": "RUB",
            "value_normalized": 100,
            "quality_score": 70,
            "confidence_score": 0.7,
            "source_priority": 7,
        },
        {
            "ticker": "TEST",
            "fiscal_year": 2025,
            "period": "2025",
            "reporting_standard": "IFRS",
            "statement_type": "income_statement",
            "line_item_std": "revenue",
            "currency": "RUB",
            "value_normalized": 101,
            "quality_score": 90,
            "confidence_score": 0.9,
            "source_priority": 3,
        },
    ])

    out, removed = deduplicate_facts(df)

    assert removed == 1
    assert len(out) == 1
    assert out.iloc[0]["value_normalized"] == 101
