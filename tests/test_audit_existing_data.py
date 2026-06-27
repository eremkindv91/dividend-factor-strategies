from pathlib import Path

import pandas as pd

from src.pipeline.audit_existing_data import MANUAL_REVIEW_FILES, audit_existing_data


def test_audit_initializes_manual_review_files(tmp_path: Path):
    source = tmp_path / "results" / "smartlab"
    source.mkdir(parents=True)
    pd.DataFrame([{"ticker": "TEST", "year": 2025}]).to_csv(source / "live_forecast.csv", index=False)

    summary = audit_existing_data(tmp_path)

    assert any(item["path"] == "results/smartlab/live_forecast.csv" and item["exists"] for item in summary["smartlab_files"])
    for name in MANUAL_REVIEW_FILES:
        assert (tmp_path / "data" / "manual_review" / name).exists()
