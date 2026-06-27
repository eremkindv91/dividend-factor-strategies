from pathlib import Path

import pandas as pd

from src.pipeline.migrate_existing_smartlab_data import migrate_smartlab


def test_migrate_smartlab_panel_to_fact_contract(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
            "net_profit_mln": 2,
            "dps_rub": 1.5,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    result = migrate_smartlab(tmp_path, make_backup=False)
    facts = pd.read_parquet(tmp_path / "data" / "processed" / "smartlab_fundamentals.parquet")
    registry = pd.read_csv(tmp_path / "data" / "companies_registry.csv")

    assert result["facts_written"] == 3
    assert set(facts["line_item_std"]) == {"revenue", "net_income", "dividend_per_share"}
    assert facts.loc[facts["line_item_std"] == "revenue", "value_normalized"].iloc[0] == 10_000_000
    assert registry.loc[0, "ticker"] == "TEST"
    assert registry.loc[0, "coverage_status"] == "smartlab_only"
