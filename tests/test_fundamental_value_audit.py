from pathlib import Path
import json

import pandas as pd

from src.quality.audit_fundamental_values import (
    build_smartlab_fundamentals_cleaned,
    write_smartlab_fundamentals_cleaned,
)


def test_cleaned_smartlab_fundamentals_preserve_raw_and_block_dangerous_values(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "BAD",
            "year": 2024,
            "sector": "Tech",
            "revenue_mln": -10.0,
            "net_profit_mln": -5.0,
            "assets_mln": 100.0,
            "roe_pct": 189000.0,
            "div_yield_pct": 120.0,
            "market_cap_mln": 1000.0,
            "price_end": 10.0,
            "FCF": 50.0,
        },
        {
            "ticker": "GOOD",
            "year": 2024,
            "sector": "Tech",
            "revenue_mln": 100.0,
            "net_profit_mln": -7.0,
            "assets_mln": 250.0,
            "equity_mln": 120.0,
            "roe_pct": -6.0,
            "div_yield_pct": 8.0,
            "market_cap_mln": 1500.0,
            "price_end": 30.0,
            "FCF": -20.0,
        },
    ])
    original.to_csv(panel_path, index=False)

    cleaned = build_smartlab_fundamentals_cleaned(tmp_path, now="2026-06-30T00:00:00+00:00")
    rows = {(r.ticker, r.field): r for r in cleaned.itertuples()}

    assert rows[("BAD", "revenue")].raw_value == -10.0
    assert pd.isna(rows[("BAD", "revenue")].clean_value)
    assert rows[("BAD", "revenue")].quality_status == "negative_value_blocked"
    assert rows[("BAD", "revenue")].excluded_from_site is True
    assert rows[("BAD", "roe")].quality_status == "ratio_extreme_blocked"
    assert pd.isna(rows[("BAD", "roe")].clean_value)
    assert rows[("BAD", "dividend_yield")].quality_status == "ratio_extreme_blocked"
    assert rows[("GOOD", "net_income")].clean_value == -7.0
    assert rows[("GOOD", "net_income")].quality_status == "clean"
    assert rows[("GOOD", "shares_outstanding")].clean_value == 50_000_000.0
    assert rows[("GOOD", "fcf_per_share")].clean_value == -0.4

    summary = write_smartlab_fundamentals_cleaned(tmp_path)
    after = pd.read_csv(panel_path)
    saved = pd.read_parquet(tmp_path / "data" / "processed" / "smartlab_fundamentals_cleaned.parquet")
    issues = pd.read_csv(tmp_path / "data" / "manual_review" / "fundamental_data_issues.csv")
    saved_summary = json.loads((tmp_path / "data" / "quality" / "smartlab_cleaned_layer_summary.json").read_text())

    pd.testing.assert_frame_equal(after, original)
    assert summary["smartlab_fundamental_values_total"] == len(saved)
    assert summary["smartlab_fundamental_values_excluded"] == 3
    assert summary["smartlab_values_needs_review"] == 3
    assert saved_summary["smartlab_fundamental_values_total"] == len(saved)
    assert set(issues["field"]) >= {"revenue", "roe", "dividend_yield"}


def test_cleaned_smartlab_fundamentals_marks_isolated_yoy_scale_jumps_for_review(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "JUMP", "year": 2022, "revenue_mln": 100.0, "net_profit_mln": 10.0},
        {"ticker": "JUMP", "year": 2023, "revenue_mln": 100000.0, "net_profit_mln": 11.0},
        {"ticker": "JUMP", "year": 2024, "revenue_mln": 120.0, "net_profit_mln": 12.0},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    cleaned = build_smartlab_fundamentals_cleaned(tmp_path, now="2026-06-30T00:00:00+00:00")
    rows = {(r.year, r.field): r for r in cleaned.itertuples()}

    assert rows[(2023, "revenue")].clean_value == 100000.0
    assert rows[(2023, "revenue")].quality_status == "yoy_scale_review"
    assert rows[(2023, "revenue")].needs_manual_review is True
    assert rows[(2023, "revenue")].excluded_from_site is False
    assert rows[(2023, "net_income")].quality_status == "clean"
