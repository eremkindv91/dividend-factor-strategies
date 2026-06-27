from src.quality.financial_validators import safe_ratio, validate_non_negative
from src.quality.quality_score import quality_bucket
from src.pipeline.validate_financials import validate_financials

import pandas as pd


def test_safe_ratio_rejects_bad_denominator():
    assert safe_ratio(1, 0) == (None, "non_positive_denominator")
    assert safe_ratio(2, 4) == (0.5, None)


def test_non_negative_validator_for_core_metrics():
    assert validate_non_negative("revenue", -1) == "negative_value_not_allowed"
    assert validate_non_negative("net_debt", -1) is None


def test_quality_bucket():
    assert quality_bucket(95) == "good"
    assert quality_bucket(70) == "acceptable"
    assert quality_bucket(None) == "manual_review"


def test_reliable_unified_fact_requires_provenance(tmp_path):
    unified_dir = tmp_path / "data" / "unified"
    unified_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "period": "2025",
            "line_item_std": "revenue",
            "best_source_name": "official_ifrs_pdf_table",
            "best_source_url": "",
            "currency": "RUB",
            "unit_type": "currency",
            "unit_multiplier": 1,
            "is_reliable": True,
            "created_at": "2026-06-27T00:00:00+00:00",
        }
    ]).to_parquet(unified_dir / "financial_facts_unified.parquet", index=False)

    res = validate_financials(tmp_path)
    errors = pd.read_csv(tmp_path / "data" / "manual_review" / "financial_validation_errors.csv")

    assert res["errors"] == 1
    assert errors.loc[0, "issue"] == "missing_reliable_field:best_source_url"
