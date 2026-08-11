from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from scripts.validate_research_state import validate_directory
from src.research.fingerprints import fingerprint
from src.research.sector_context import percentile_rank
from src.research.state_builder import build_research_state
from src.research.validators import validate_public_artifact

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 11, 9, tzinfo=timezone.utc)


def _write(root: Path, relative: str, payload) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=True), encoding="utf-8")


def _ticker(ticker: str, sector: str, momentum: float, price: float = 100.0) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "sector": sector,
        "price": price,
        "price_field": "LCLOSEPRICE",
        "price_fresh": True,
        "mom_score": momentum,
        "vol_ann": 0.2 + momentum / 100,
        "adv": 10_000_000 + momentum,
        "mcap": 1_000_000,
        "lot_size": 1,
        "quality_score": momentum,
        "quality_rank_pct": momentum,
        "dividend_yield_expected": 5 + momentum / 10,
        "dividend_forecast": 5,
        "dividend_forecast_lo": 4,
        "dividend_forecast_hi": 6,
        "cut_risk": 0.1,
        "stability_score": 0.8,
        "ranking_status": "eligible",
        "ranking_eligible": True,
        "ranking_review_reasons": [],
        "status": "ok",
        "valuation": {"method": "existing", "upside_pct": momentum},
    }


def _fixture(site: Path, *, price_asof: str = "2026-08-10", first_price: float = 100.0) -> None:
    tickers = [
        _ticker("AAA", "Финансы (Банки)", 10, first_price),
        _ticker("AAB", "Финансы (Банки)", 20),
        _ticker("BBB", "Sector B", 1000),
    ]
    _write(
        site,
        "data.json",
        {
            "meta": {"price_asof": price_asof, "forecast_asof": "2026-08-01"},
            "tickers": tickers,
        },
    )
    _write(
        site,
        "returns.json",
        {
            "meta": {"asof": "2026-08-10", "months": ["2026-07", "2026-08"]},
            "data": {"AAA": [0.01, 0.02], "AAB": [0.02, 0.04], "BBB": [0.03, -0.01], "MCFTR": [0.01, 0.01]},
        },
    )
    _write(
        site,
        "market_history.json",
        {
            "generated_at": "2026-08-10T20:00:00+00:00",
            "instruments": [
                {"id": "MCFTR", "name": "MCFTR", "data_last": "2026-08-10", "summary": {"last": 100, "change_pct": 1}},
                {"id": "IMOEX", "name": "IMOEX", "data_last": "2026-08-10", "summary": {"last": 200, "change_pct": 2}},
            ],
        },
    )
    _write(
        site,
        "marketsaw.json",
        {"generated_at": "2026-08-10T20:00:00+00:00", "data_last": "2026-08-10", "current_phase": {"label": "test"}},
    )
    _write(
        site,
        "macro_cbr.json",
        {
            "generated_at": "2026-08-10T20:00:00+00:00",
            "key_rate": {"asof": "2026-08-10", "value": 10},
            "inflation": {
                "mom": {
                    "latest": {"month": "2026-07", "mom_pct": 0.4},
                    "rows": [{"month": "2026-06", "mom_pct": 0.3}],
                    "source_file": "/storage/public.xlsx",
                }
            },
        },
    )
    quality_rows = [
        {
            "ticker": row["ticker"],
            "quality_score_sector": row["mom_score"],
            "publication_date": None,
            "report_period_end": "2025-12-31",
            "warnings": ["publication_date_unknown"],
        }
        for row in tickers
    ]
    _write(site, "quality.json", {"meta": {"as_of_date": "2026-08-10"}, "rows": quality_rows})
    fundamentals = {
        row["ticker"]: {
            "income": [
                {
                    "field": "revenue",
                    "unit": "mln_rub",
                    "source_name": "existing",
                    "source_status": "smartlab_fallback",
                    "source_url": "https://example.test/source",
                    "values": [{"year": 2025, "value": row["mom_score"]}],
                }
            ]
        }
        for row in tickers
    }
    _write(
        site,
        "site_financials.json",
        {"meta": {"generated_at": "2026-08-10T21:00:00+00:00"}, "fundamentals": fundamentals},
    )
    _write(
        site,
        "ml_strategy/latest.json",
        {
            "schema_version": 2,
            "generated_at": "2026-08-10T21:00:00+00:00",
            "data_as_of": "2026-08-10",
            "model_status": "research_only",
            "data_status": "degraded",
            "signal_status": "no_signal",
            "action_status": "no_trade",
            "model": {"champion": "elastic_net", "status": "research_only"},
            "diagnostics": {"portfolio_gate": {"status": "fail"}},
            "limitations": ["historical universe has survivorship risk"],
            "candidate_portfolio": {"positions": [{"ticker": "AAA", "calculated_weight": 1}]},
        },
    )
    _write(
        site,
        "ml_strategy/sector_features/latest_quality.json",
        {
            "schema_version": 1,
            "generated_at": "2026-08-10T21:00:00+00:00",
            "status": "DEGRADED",
            "point_in_time_policy": "available_at <= prediction_timestamp",
            "approved_feature_columns": [],
            "packs": [
                {
                    "pack_id": "BANKS_AND_FINANCIALS",
                    "status": "RESEARCH_ONLY",
                    "features": ["bank_key_rate_level"],
                    "used_in_production": False,
                    "latest_available_at": "2026-08-10",
                }
            ],
        },
    )
    _write(site, "news.json", {"date": "2026-08-11", "generated_at": "2026-08-11T08:00:00+00:00", "overnight": [], "yesterday": [], "today_agenda": []})
    _write(site, "bonds/screener.json", {"meta": {"data_date": "2026-08-09"}, "bonds": []})


def _build(tmp_path: Path, **kwargs):
    site = tmp_path / "site"
    output = tmp_path / "out"
    _fixture(site, **kwargs)
    return build_research_state(ROOT, site_dir=site, output_dir=output, now=NOW), output, site


def test_sector_research_only_cannot_be_tradable_signal(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    research_only = 0
    for sector in artifacts["sector_snapshot.json"]["sectors"]:
        model = sector["model"]
        if model["promotion_status"] == "RESEARCH_ONLY":
            research_only += 1
            assert model["tradable_signal"] is False
    assert research_only == 1


def test_sector_percentile_uses_only_sector_peers(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    aaa = artifacts["stocks/AAA.json"]
    metric = aaa["sector_position"]["momentum_percentile"]
    assert metric["n_peers"] == 2
    assert metric["raw_percentile"] == 50.0


def test_non_finite_values_excluded_from_percentile():
    assert percentile_rank(2, [1, 2, math.nan, math.inf, None]) == 100.0


def test_future_timestamp_blocks_ready_for_ai(tmp_path):
    artifacts, _, _ = _build(tmp_path, price_asof="2027-01-01")
    manifest = artifacts["research_manifest.json"]
    assert manifest["ready_for_ai"] is False
    assert any("future timestamp" in error for error in manifest["validation_errors"])


def test_missing_publication_date_marked_partial(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    aaa = artifacts["stocks/AAA.json"]
    assert aaa["data_quality"]["point_in_time_quality"] == "partial"
    assert aaa["data_quality"]["publication_timestamp_available"] is False
    fundamentals = artifacts["fundamentals_snapshot.json"]
    assert fundamentals["asof"] is None
    assert fundamentals["data_quality"]["fresh"] is False


def test_research_hash_stable_for_identical_input(tmp_path):
    first, _, site = _build(tmp_path / "a")
    second_out = tmp_path / "b" / "out"
    second = build_research_state(
        ROOT,
        site_dir=site,
        output_dir=second_out,
        now=datetime(2026, 8, 11, 9, 1, tzinfo=timezone.utc),
    )
    assert first["research_manifest.json"]["research_input_hash"] == second["research_manifest.json"]["research_input_hash"]


def test_research_hash_changes_on_material_input_change(tmp_path):
    first, _, _ = _build(tmp_path / "a", first_price=100)
    second, _, _ = _build(tmp_path / "b", first_price=101)
    assert first["research_manifest.json"]["research_input_hash"] != second["research_manifest.json"]["research_input_hash"]


def test_private_portfolio_not_present_in_research_state(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    text = json.dumps(artifacts, ensure_ascii=False).lower()
    for forbidden in ("portfolio_holdings", "purchase_price", "quantity", "user_weights", "localstorage", '"pnl"'):
        assert forbidden not in text
    injected = dict(artifacts["market_snapshot.json"])
    injected["portfolio_holdings"] = [{"ticker": "AAA"}]
    assert not validate_public_artifact("market_snapshot.json", injected, now=NOW).ok


def test_local_absolute_path_not_present_in_public_json(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    assert "/Users/" not in json.dumps(artifacts, ensure_ascii=False)
    assert artifacts["market_snapshot.json"]["rates"]["inflation"]["mom"]["source_file"] == (
        "https://cbr.ru/storage/public.xlsx"
    )
    assert "rows" not in artifacts["market_snapshot.json"]["rates"]["inflation"]["mom"]
    injected = dict(artifacts["news_snapshot.json"])
    injected["source_file"] = "/srv/research/private.json"
    assert not validate_public_artifact("news_snapshot.json", injected, now=NOW).ok


def test_no_secret_pattern_in_public_research_json(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    injected = dict(artifacts["market_snapshot.json"])
    injected["api_key"] = "AI" + "za012345678901234567890123456789"
    assert not validate_public_artifact("market_snapshot.json", injected, now=NOW).ok


def test_no_nan_or_infinity_in_research_json(tmp_path):
    artifacts, _, site = _build(tmp_path)
    data = json.loads((site / "data.json").read_text(encoding="utf-8"))
    data["tickers"][0]["adv"] = math.nan
    _write(site, "data.json", data)
    artifacts = build_research_state(ROOT, site_dir=site, output_dir=tmp_path / "clean", now=NOW)
    encoded = json.dumps(artifacts, allow_nan=False)
    assert "NaN" not in encoded and "Infinity" not in encoded


def test_stale_component_preserves_own_asof(tmp_path):
    artifacts, _, _ = _build(tmp_path)
    bonds = artifacts["research_manifest.json"]["components"]["bonds"]
    assert bonds["asof"] == "2026-08-09"
    assert artifacts["research_manifest.json"]["research_asof"] == "2026-08-10"


def test_research_manifest_preserves_component_dates(tmp_path):
    artifacts, output, _ = _build(tmp_path)
    manifest = artifacts["research_manifest.json"]
    assert manifest["components"]["news"]["asof"] == "2026-08-11"
    assert manifest["components"]["market"]["asof"] == "2026-08-10"
    assert validate_directory(output) == []


def test_fingerprint_ignores_volatile_metadata_but_not_values():
    first = {"generated_at": "2026-08-10T10:00:00Z", "asof": "2026-08-09", "value": 1}
    second = {"generated_at": "2026-08-11T10:00:00Z", "asof": "2026-08-09", "value": 1}
    assert fingerprint(first) == fingerprint(second)
    second["value"] = 2
    assert fingerprint(first) != fingerprint(second)
