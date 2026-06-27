from pathlib import Path
import json

import pandas as pd

from src.pipeline.run_all import run_all


def test_run_all_smartlab_only_smoke(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
            "assets_mln": 25,
            "net_profit_mln": 2,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    summary = run_all(tmp_path, smartlab_only=True, skip_ocr=True)

    assert summary["facts_from_smartlab"] == 3
    assert summary["site_rows"] == 1
    assert (tmp_path / "data" / "unified" / "site_financials.json").exists()
    assert (tmp_path / "site" / "site_coverage.json").exists()


def test_run_all_falls_back_to_smartlab_if_ifrs_fetch_fails(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def broken_fetch_reports(*_args, **_kwargs):
        raise RuntimeError("issuer site unavailable")

    monkeypatch.setattr("src.pipeline.run_all.fetch_reports", broken_fetch_reports)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["mode"] == "default"
    assert summary["facts_from_smartlab"] == 1
    assert summary["site_rows"] == 1
    assert summary["ifrs_failures"] == [{"step": "fetch_reports", "error": "issuer site unavailable"}]
    assert (tmp_path / "site" / "site_financials.json").exists()


def test_run_all_summary_uses_registry_after_discovery(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def add_candidate(root: Path, **_kwargs):
        reg_path = root / "data" / "companies_registry.csv"
        registry = pd.read_csv(reg_path, dtype=str).fillna("")
        candidate = {col: "" for col in registry.columns}
        candidate.update({
            "ticker": "CAND",
            "secid": "CAND",
            "short_name": "Candidate",
            "is_public": "true",
            "is_traded": "true",
            "has_smartlab_data": "false",
            "status": "needs_mapping",
            "coverage_status": "candidate",
            "notes": "created_from_moex_discovery",
        })
        pd.concat([registry, pd.DataFrame([candidate])], ignore_index=True).to_csv(reg_path, index=False)
        return {"new_companies_added": 1, "source_rows": 0}

    monkeypatch.setattr("src.pipeline.run_all.discover_companies", add_candidate)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)
    coverage = json.loads((tmp_path / "site" / "site_coverage.json").read_text())

    assert summary["companies_total"] == 2
    assert coverage["meta"]["companies_total"] == 2


def test_run_all_no_network_disables_moex_discovery(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def assert_no_network_discovery(root: Path, *, use_moex: bool = True, **_kwargs):
        assert use_moex is False
        return {"new_companies_added": 0, "source_rows": 0}

    monkeypatch.setattr("src.pipeline.run_all.discover_companies", assert_no_network_discovery)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["mode"] == "default"
    assert summary["ifrs_failures"] == []
