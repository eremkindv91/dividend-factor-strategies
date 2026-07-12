from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.build_quality import build_quality_artifact, validate_quality_artifact


def _write_fixture(tmp_path: Path):
    panel_rows = []
    registry_rows = []
    momentum = {"meta": {}, "data": {}}
    returns = {"meta": {"months": [f"202{i // 12}-{i % 12 + 1:02d}" for i in range(60)]}, "data": {}}
    report_rows = []
    official_rows = []
    site_rows = []
    for company in range(8):
        ticker = f"Q{company}"
        sector = "Alpha" if company < 4 else "Beta"
        for offset, year in enumerate(range(2020, 2025)):
            eps = 5 + company + offset * (0.3 + company / 100)
            shares = 100_000_000
            panel_rows.append({
                "ticker": ticker, "year": year, "sector": sector,
                "net_profit_mln": eps * shares / 1_000_000,
                "equity_mln": 1_000 + company * 50 + offset * 20,
                "total_debt_mln": 300 + company * 20,
                "market_cap_mln": 20_000 + company * 1_000,
                "price_end": 100 + company,
                "free_float_pct": 40,
                "shares_outstanding": shares,
                "eps": eps,
            })
        registry_rows.append({"ticker": ticker, "short_name": f"Company {company}", "sector": sector, "moex_board": "TQBR", "inn": ""})
        momentum["data"][ticker] = {"adv": 30_000_000 + company * 1_000_000, "vol_ann": 0.2}
        returns["data"][ticker] = [0.01] * 60
        report_rows += [
            {"ticker": ticker, "fiscal_year": 2024, "publication_date": "2025-03-15", "reporting_standard": "IFRS", "source_url": f"https://issuer.test/{ticker}/2024", "report_id": f"{ticker}-2024", "source_name": "issuer"},
            {"ticker": ticker, "fiscal_year": 2024, "publication_date": "2025-08-15", "reporting_standard": "IFRS", "source_url": f"https://issuer.test/{ticker}/future", "report_id": f"{ticker}-future", "source_name": "issuer"},
        ]
        official_rows.append({"ticker": ticker, "fiscal_year": 2024, "reporting_standard": "IFRS", "source_url": f"https://issuer.test/{ticker}/2024", "report_id": f"{ticker}-2024", "source_name": "issuer"})
        site_rows.append({"ticker": ticker, "name": f"Company {company}", "price": 100 + company, "mcap": 20_000 + company * 1_000, "adv": 30_000_000 + company * 1_000_000, "lot_size": 10})

    paths = {
        "panel": tmp_path / "panel.csv", "registry": tmp_path / "registry.csv",
        "momentum": tmp_path / "momentum.json", "returns": tmp_path / "returns.json",
        "reports": tmp_path / "reports.parquet", "official": tmp_path / "official.csv",
        "site": tmp_path / "data.json", "config": tmp_path / "config.json",
    }
    pd.DataFrame(panel_rows).to_csv(paths["panel"], index=False)
    pd.DataFrame(registry_rows).to_csv(paths["registry"], index=False)
    paths["momentum"].write_text(json.dumps(momentum), encoding="utf-8")
    paths["returns"].write_text(json.dumps(returns), encoding="utf-8")
    pd.DataFrame(report_rows).to_parquet(paths["reports"], index=False)
    pd.DataFrame(official_rows).to_csv(paths["official"], index=False)
    paths["site"].write_text(json.dumps({"tickers": site_rows}), encoding="utf-8")
    config = json.loads((Path(__file__).parents[1] / "src/config/quality_ru.json").read_text())
    config["min_sector_peers"] = 4
    config["excluded_sectors"] = []
    config["default"].update({"top_n": 8, "max_security_weight": 0.2, "max_sector_weight": 0.6})
    paths["config"].write_text(json.dumps(config), encoding="utf-8")
    return paths


def test_fixture_to_quality_json_is_point_in_time_and_schema_valid(tmp_path):
    paths = _write_fixture(tmp_path)
    payload = build_quality_artifact(
        panel_path=paths["panel"], registry_path=paths["registry"], momentum_path=paths["momentum"],
        returns_path=paths["returns"], report_index_path=paths["reports"], official_facts_path=paths["official"],
        site_data_path=paths["site"], config_path=paths["config"], as_of=date(2025, 6, 30),
    )
    validate_quality_artifact(payload)
    assert payload["meta"]["n_universe"] == 8
    assert payload["meta"]["n_scored"] == 8
    assert payload["meta"]["n_eligible"] == 8
    assert all(row["publication_date"] == "2025-03-15" for row in payload["rows"])
    assert all(row["diagnostics"]["roe_method"] == "eps_over_bvps" for row in payload["rows"])
    assert all(row["coverage_ratio"] == 1.0 for row in payload["rows"])
    assert payload["default_portfolio"]["target_weight_sum"] == 1.0


def test_negative_equity_is_excluded_without_infinite_debt_ratio(tmp_path):
    paths = _write_fixture(tmp_path)
    panel = pd.read_csv(paths["panel"])
    panel.loc[(panel.ticker == "Q0") & (panel.year == 2024), "equity_mln"] = -10
    panel.to_csv(paths["panel"], index=False)
    payload = build_quality_artifact(
        panel_path=paths["panel"], registry_path=paths["registry"], momentum_path=paths["momentum"],
        returns_path=paths["returns"], report_index_path=paths["reports"], official_facts_path=paths["official"],
        site_data_path=paths["site"], config_path=paths["config"], as_of=date(2025, 6, 30),
    )
    row = next(item for item in payload["rows"] if item["ticker"] == "Q0")
    assert row["raw"]["debt_to_equity"] is None
    assert row["eligible"] is False
    assert "non_positive_common_equity" in row["exclusion_reasons"]


def test_financials_never_receive_industrial_debt_to_equity(tmp_path):
    paths = _write_fixture(tmp_path)
    config = json.loads(paths["config"].read_text())
    config["excluded_sectors"] = ["Alpha"]
    paths["config"].write_text(json.dumps(config), encoding="utf-8")
    payload = build_quality_artifact(
        panel_path=paths["panel"], registry_path=paths["registry"], momentum_path=paths["momentum"],
        returns_path=paths["returns"], report_index_path=paths["reports"], official_facts_path=paths["official"],
        site_data_path=paths["site"], config_path=paths["config"], as_of=date(2025, 6, 30),
    )
    financial_rows = [row for row in payload["rows"] if row["sector"] == "Alpha"]
    assert all(row["raw"]["debt_to_equity"] is None for row in financial_rows)
    assert all(row["status"] == "sector_specific_model_required" for row in financial_rows)
