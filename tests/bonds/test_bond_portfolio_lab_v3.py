from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from bonds.integer_allocator import allocate_integer_lots
from bonds.fns_sector_enrichment import (
    _main_okved_from_extract_text,
    enrich_issuer_master,
    lookup_company_by_inn,
    sector_from_okved,
)
from bonds.portfolio_engine import solve_target_portfolio
from bonds.pipeline_v3 import _persist_verified_issuer_records
from bonds.universe_builder import (
    attach_peer_benchmarks,
    load_json,
    modified_duration_effective_annual,
    solve_effective_annual_ytm,
)
from bonds.validation import (
    quality_gate,
    validate_integer_allocation,
    validate_target_portfolio,
    validate_universe_schema,
)


def _bond(index: int, duration: float, instrument_type: str = "corp") -> dict:
    is_ofz = instrument_type == "ofz"
    dirty = 1010.0 + index % 7
    rating = None if is_ofz else "AA"
    return {
        "secid": f"B{index:03d}",
        "isin": f"RU{index:010d}",
        "name": f"Bond {index}",
        "instrument_type": instrument_type,
        "risk_class": "sovereign_rub" if is_ofz else "corporate",
        "issuer_id": "sovereign:minfin-rf" if is_ofz else f"issuer:{index}",
        "issuer_name": "Минфин России" if is_ofz else f"Issuer {index}",
        "sector": "Государственные облигации" if is_ofz else ["Транспорт", "Финансы", "Энергетика", "Телекоммуникации"][index % 4],
        "sector_source": "fixture",
        "board": "TQOB" if is_ofz else "TQCB",
        "rating": rating,
        "rating_rank": None if is_ofz else 18,
        "rating_group": None if is_ofz else "AA",
        "rating_scope": "sovereign" if is_ofz else "issue",
        "rating_agency": None if is_ofz else "АКРА",
        "rating_date": None if is_ofz else "2026-07-20",
        "rating_checked_at": None if is_ofz else "2026-08-02T00:00:00+00:00",
        "rating_records": [],
        "face_value_per_bond_rub": 1000.0,
        "lot_size": 1,
        "clean_price_pct": dirty / 10.0,
        "aci_per_bond_rub": 0.0,
        "dirty_price_per_bond_rub": dirty,
        "dirty_price_per_lot_rub": dirty,
        "ytm_gross_pct": 15.0 + index % 3,
        "ytm_net_est_pct": 13.0 + index % 3,
        "tax_model_version": "fixture",
        "g_curve_yield_pct": 12.0,
        "g_spread_pp": 0.2 if is_ofz else 3.0 + (index % 5) / 10,
        "peer_spread_pp": 3.0 if not is_ofz else 0.0,
        "excess_spread_pp": 0.0 if is_ofz else (index % 5) / 10,
        "z_spread_bp": None,
        "duration_value": duration,
        "duration_type": "modified_duration_effective_annual",
        "duration_source": "fixture_cashflows",
        "duration_as_of": "2026-08-02",
        "maturity_date": f"{2027 + index % 5}-12-15",
        "years_to_maturity": duration + 0.4,
        "coupon_pct": 14.0,
        "coupon_frequency": 2,
        "coupon_type": "fixed",
        "median_volume_20d_rub": 100_000_000.0,
        "history_sessions": 20,
        "value_today_rub": 120_000_000.0,
        "issue_size_rub": 10_000_000_000.0,
        "list_level": 1,
        "qualified_only": False,
        "new_placement": False,
        "has_put_offer": False,
        "has_call": False,
        "amortizing": False,
        "data_quality_flags": [],
        "source_dates": {"price": "2026-08-02", "history": "2026-08-02", "rating": "2026-08-02"},
        "cashflows_12m": [],
    }


def universe_fixture() -> dict:
    rows = []
    durations = [0.7, 1.6, 2.5, 3.1, 3.8]
    index = 0
    for duration in durations:
        for offset in range(12):
            rows.append(_bond(index, duration + ((offset % 3) - 1) * 0.08, "ofz" if offset < 3 else "corp"))
            index += 1
    return {
        "schema_version": "3.0",
        "generated_at": "2026-08-02T00:00:00+00:00",
        "build_sha": "fixture",
        "as_of": {"prices": "2026-08-02", "curve": "2026-08-02", "ratings": "2026-08-02", "history": "2026-08-02"},
        "source_status": {
            "ratings": {"sources": {"acra": {"status": "ok", "mode": "live"}}},
            "moex": {"status": "ok"},
        },
        "bonds": rows,
    }


def test_dirty_price_contract_and_schema_validation():
    universe = universe_fixture()
    assert validate_universe_schema(universe) == []

    broken = deepcopy(universe)
    broken["bonds"][0]["dirty_price_per_lot_rub"] += 1
    assert any("dirty_price_per_lot_mismatch" in item for item in validate_universe_schema(broken))


def test_cashflow_ytm_and_modified_duration_are_numerical_and_deterministic():
    as_of = date(2026, 1, 1)
    flows = [
        (as_of + timedelta(days=182), 50.0),
        (as_of + timedelta(days=365), 1050.0),
    ]
    ytm = solve_effective_annual_ytm(flows, 1000.0, as_of)
    duration = modified_duration_effective_annual(flows, 1000.0, ytm, as_of)

    assert ytm is not None and 0.09 < ytm < 0.11
    assert duration is not None and 0.85 < duration < 0.95
    assert modified_duration_effective_annual(flows, 1000.0, ytm, as_of) == duration


def test_peer_benchmark_uses_observation_count_and_neutral_fallback():
    universe = universe_fixture()
    rows = universe["bonds"][:8]
    config = load_json("bonds/portfolio_config.json")
    attach_peer_benchmarks(rows, config)

    corporate = [row for row in rows if row["instrument_type"] == "corp"]
    assert all(row["peer_spread_pp"] is not None for row in corporate)
    assert all(row["peer_fallback_level"] in {"rating_notch_duration", "rating_group_duration", "rating_group", "fixed_rating_fallback"} for row in corporate)


def test_quality_gate_checks_sources_and_coverages():
    universe = universe_fixture()
    gate = quality_gate(universe, today=date(2026, 8, 2))
    assert gate["status"] == "PASS"

    broken = deepcopy(universe)
    broken["as_of"]["prices"] = "2026-01-01"
    assert "prices_stale_or_missing" in quality_gate(broken, today=date(2026, 8, 2))["failures"]


def test_all_fifteen_presets_satisfy_target_constraints_and_are_deterministic():
    universe = universe_fixture()
    config = load_json("bonds/portfolio_config.json")
    for profile in config["profiles"]:
        for horizon in config["horizons"]:
            first = solve_target_portfolio(universe, profile, horizon)
            second = solve_target_portfolio(universe, profile, horizon)
            assert first["status"] in {"OPTIMAL", "FEASIBLE"}, (profile, horizon, first)
            assert validate_target_portfolio(first, universe) == []
            assert first["target_positions"] == second["target_positions"]
            assert abs(sum(item["target_weight"] for item in first["target_positions"]) + first["cash_target_weight"] - 1) <= 1e-8


def test_integer_allocation_never_exceeds_budget_and_rechecks_caps():
    universe = universe_fixture()
    target = solve_target_portfolio(universe, "balanced", "3y")
    allocation = allocate_integer_lots(target, universe, 1_000_000)

    assert allocation["status"] == "VALIDATED"
    assert allocation["invested_with_costs_rub"] + allocation["cash_rub"] <= 1_000_000.01
    assert validate_integer_allocation(allocation, target, universe) == []
    assert all(item["lots"] >= 1 for item in allocation["positions"])
    assert all(item["dirty_amount_rub"] > 0 for item in allocation["positions"])


def test_multiple_issues_of_one_issuer_are_aggregated_for_cap():
    universe = universe_fixture()
    for row in universe["bonds"][:4]:
        if row["instrument_type"] == "corp":
            row["issuer_id"] = "issuer:same"
    target = solve_target_portfolio(universe, "balanced", "3y")
    assert target["status"] in {"OPTIMAL", "FEASIBLE"}
    same_weight = sum(item["target_weight"] for item in target["target_positions"] if item["issuer_id"] == "issuer:same")
    assert same_weight <= 0.15 + 1e-8


def test_infeasible_universe_returns_diagnostics_without_relaxing_constraints():
    universe = universe_fixture()
    universe["bonds"] = universe["bonds"][:3]
    result = solve_target_portfolio(universe, "defensive", "1y")
    assert result["status"] == "INFEASIBLE"
    assert result["reason_codes"] == ["insufficient_eligible_issues_or_issuers"]


def test_okved_sector_mapping_is_explicit_and_conservative():
    assert sector_from_okved("19.20.1") == "Нефть и газ"
    assert sector_from_okved("49.20") == "Транспорт"
    assert sector_from_okved("64.19") == "Финансы"
    assert sector_from_okved("61.10") == "Телекоммуникации"
    assert sector_from_okved("68.20") == "Недвижимость"
    assert sector_from_okved("01.11") is None
    assert sector_from_okved(None) is None


def test_main_okved_parser_is_scoped_to_primary_activity_block():
    text = """
Сведения об основном виде деятельности
ОКВЭД ОК 029-2014 (КДЕС Ред. 2)
40 Код и наименование вида деятельности 19.20.1 Производство жидкого топлива
41 ГРН и дата внесения в ЕГРЮЛ записи 123
Сведения о дополнительных видах деятельности
42 Код и наименование вида деятельности 64.19 Денежное посредничество
"""
    assert _main_okved_from_extract_text(text) == ("19.20.1", "Производство жидкого топлива")


def test_fns_enrichment_requires_exact_inn_and_keeps_failures_unknown():
    records = {
        "5504036333": {
            "inn": "5504036333",
            "issuer_name": "ПАО Газпром нефть",
            "okved_main": "19.20.1",
            "okved_main_name": "Производство жидкого топлива",
            "okved_main_type": "отчетный",
            "source_url": "https://pb.nalog.ru/",
            "checked_at": "2026-08-02T00:00:00+00:00",
        },
        "7703104630": {"inn": "0000000000", "okved_main": "64.20"},
    }

    enriched, status = enrich_issuer_master(
        {"issuers": {}},
        [
            {"issuer_inn": "5504036333", "issuer_name": "Газпром нефть", "value_today_rub": 2},
            {"issuer_inn": "7703104630", "issuer_name": "АФК Система", "value_today_rub": 1},
        ],
        lookup=lambda inn: records[inn],
        request_interval_seconds=0,
    )
    assert enriched["issuers"]["5504036333"]["sector"] == "Нефть и газ"
    assert enriched["issuers"]["5504036333"]["sector_source"] == "fns_main_okved"
    assert "7703104630" not in enriched["issuers"]
    assert status["mapped"] == 1
    assert len(status["errors"]) == 1


def test_fns_lookup_parser_uses_only_exact_company_row():
    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.status_code = 200
            self.text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.headers = {}
            self.posts = 0

        def get(self, *_args, **_kwargs):
            return Response({})

        def request(self, method, _url, **_kwargs):
            assert method == "POST"
            self.posts += 1
            if self.posts == 1:
                return Response({"id": "request-1", "captchaRequired": False})
            return Response({"ul": {"data": [
                {"inn": "5504036333", "namep": "ПАО Газпром нефть", "ogrn": "1025501701686", "okved2main": "19.20.1", "okved2mainname": "Производство жидкого топлива", "okved2maintype": "отчетный"},
                {"inn": "0000000000", "okved2main": "64.19"},
            ]}})

    result = lookup_company_by_inn("5504036333", session=Session(), sleep=lambda _seconds: None)
    assert result["inn"] == "5504036333"
    assert result["okved_main"] == "19.20.1"


def test_verified_fns_records_are_persisted_atomically(tmp_path):
    path = tmp_path / "issuer_master.json"
    path.write_text('{"schema_version":"1.0","issuers":{}}', encoding="utf-8")
    universe = {
        "source_status": {"sector_mapping": {"fns_enrichment": {"resolved": [{
            "issuer_inn": "5504036333",
            "issuer_name": "ПАО Газпром нефть",
            "sector": "Нефть и газ",
            "sector_source": "fns_main_okved",
            "sector_source_url": "https://pb.nalog.ru/",
            "okved_main": "19.20.1",
            "checked_at": "2026-08-02T00:00:00+00:00",
        }]}}},
    }
    assert _persist_verified_issuer_records(universe, path) == 1
    persisted = load_json(path)
    assert persisted["issuers"]["5504036333"]["okved_main"] == "19.20.1"
    assert _persist_verified_issuer_records(universe, path) == 0


def test_infeasible_preset_keeps_constraints_and_has_user_diagnostics():
    universe = universe_fixture()
    universe["bonds"] = universe["bonds"][:3]
    target = solve_target_portfolio(universe, "defensive", "1y")
    assert target["status"] == "INFEASIBLE"
    assert target["reason_codes"] == ["insufficient_eligible_issues_or_issuers"]
    assert target["candidate_diagnostics"]["issues_inside_duration_corridor"] >= 0
