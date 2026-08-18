from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from bonds.integer_allocator import allocate_integer_lots
from bonds.fns_sector_enrichment import (
    _main_okved_from_extract_text,
    enrich_issuer_master,
    lookup_company_by_inn,
    sector_from_okved,
)
from bonds.portfolio_engine import solve_target_portfolio
from bonds.pipeline_v3 import _persist_verified_issuer_records, build_and_publish
from bonds.universe_builder import (
    attach_peer_benchmarks,
    classify_bond_structure,
    load_json,
    modified_duration_effective_annual,
    normalize_bond,
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


def test_integer_allocation_failure_degrades_only_affected_preset(monkeypatch, tmp_path):
    universe = universe_fixture()
    preset_base = {
        "status": "OPTIMAL",
        "profile": "defensive",
        "horizon": "5y",
        "target_positions": [{"secid": universe["bonds"][0]["secid"], "target_weight": 1.0}],
    }
    presets = {
        "schema_version": "3.0",
        "universe_hash": "fixture-hash",
        "profiles": {},
        "horizons": {},
        "costs": {},
        "budget_limits": {},
        "presets": {
            "defensive:3y": {**preset_base, "key": "defensive:3y"},
            "defensive:5y": {**preset_base, "key": "defensive:5y"},
        },
    }
    config_path = tmp_path / "portfolio_config.json"
    config_path.write_text(json.dumps({"default_budget_rub": 1_000_000}), encoding="utf-8")

    monkeypatch.setattr("bonds.pipeline_v3.build_live_universe", lambda **_kwargs: universe)
    monkeypatch.setattr("bonds.pipeline_v3.quality_gate", lambda *_args: {"status": "PASS"})
    monkeypatch.setattr("bonds.pipeline_v3.build_preset_matrix", lambda *_args: deepcopy(presets))
    monkeypatch.setattr("bonds.pipeline_v3.validate_target_portfolio", lambda *_args: [])

    def allocation(target, *_args):
        if target["key"] == "defensive:5y":
            return {
                "status": "INFEASIBLE",
                "reason_codes": ["integer_allocation_failed"],
                "solver_message": "fixture infeasible",
                "positions": [],
            }
        return {"status": "VALIDATED", "positions": []}

    monkeypatch.setattr("bonds.pipeline_v3.allocate_integer_lots", allocation)
    monkeypatch.setattr(
        "bonds.pipeline_v3.validate_integer_allocation",
        lambda allocation, *_args: [] if allocation["status"] == "VALIDATED" else ["allocation_not_validated"],
    )

    validation = build_and_publish(
        load_board=lambda *_args: [],
        http_json=lambda *_args: {},
        iss="fixture",
        ratings={},
        ratings_meta={},
        gcurve_rate=lambda _years: 0.0,
        output_dir=tmp_path / "out",
        config_path=config_path,
    )

    assert validation["status"] == "PASS"
    assert validation["available_presets"] == 1
    assert validation["unavailable_presets"] == ["defensive:5y"]
    unavailable = validation["presets"]["defensive:5y"]
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["allocation_errors"] == ["allocation_not_validated"]
    assert unavailable["reason_codes"] == ["integer_allocation_failed"]
    published = json.loads((tmp_path / "out" / "portfolio_presets.json").read_text(encoding="utf-8"))
    assert set(published["allocations"]) == {"defensive:3y"}


def test_dirty_price_contract_and_schema_validation():
    universe = universe_fixture()
    assert validate_universe_schema(universe) == []

    broken = deepcopy(universe)
    broken["bonds"][0]["dirty_price_per_lot_rub"] += 1
    assert any("dirty_price_per_lot_mismatch" in item for item in validate_universe_schema(broken))


def _normalized_case(
    *, bond_type: str, current_face: float = 1034.70, initial_face: float = 1000.0,
    coupon_pct: float = 1.85, moex_yield_pct: float | None = 1.88,
    enrichment_overrides: dict | None = None,
) -> dict:
    as_of = date(2026, 8, 10)
    coupon_cash = round(current_face * coupon_pct / 100 / 2, 4)
    coupon_dates = [
        "2026-11-19", "2027-05-26", "2027-11-30",
        "2028-06-05", "2028-12-10", "2029-06-16",
    ]
    raw = {
        "SECID": "RU000A10F504",
        "ISIN": "RU000A10F504",
        "SHORTNAME": "ВЭБ2Р-58",
        "BONDTYPE": bond_type,
        "FACEUNIT": "SUR",
        "FACEVALUE": current_face,
        "FACEVALUEONSETTLEDATE": current_face,
        "ACCRUEDINT": 4.62,
        "LOTSIZE": 1,
        "MATDATE": "2029-06-16",
        "COUPONPERCENT": coupon_pct,
        "COUPONPERIOD": 188,
        "ISSUESIZE": 150_000_000,
        "PREVDATE": "2026-08-07",
        "_board": "TQCB",
        "_md": {
            "WAPRICE": 99.95,
            **({"YIELDATWAPRICE": moex_yield_pct} if moex_yield_pct is not None else {}),
            "DURATION": 1014,
            "VALTODAY_RUR": 50_000_000,
        },
    }
    enrichment = {
        "description": {
            "BOND_TYPE": bond_type,
            "INITIALFACEVALUE": initial_face,
            "COUPONFREQUENCY": 2,
            "EMITTER_ID": 123,
        },
        "cashflows": [[coupon_date, coupon_cash] for coupon_date in coupon_dates]
        + [["2029-06-16", current_face]],
        "cashflows_12m": [
            {"date": "2026-11-19", "amount_per_bond_rub": coupon_cash},
            {"date": "2027-05-26", "amount_per_bond_rub": coupon_cash},
        ],
        "future_coupon_count": 6,
        "future_amortization_count": 1,
        "amortizing": False,
        "has_offer": False,
        "history_values": [50_000_000] * 20,
        "history_sessions": 20,
        "history_as_of": "2026-08-07",
    }
    enrichment.update(enrichment_overrides or {})
    row = normalize_bond(
        raw,
        {"rating": "AAA", "rating_scope": "issue", "rating_records": []},
        {"INN": "7710489036", "SHORT_TITLE": "ВЭБ.РФ"},
        enrichment,
        lambda _duration: 14.8,
        load_json("bonds/portfolio_config.json"),
        {"issuers": {}},
        as_of,
    )
    assert row is not None
    return row


def test_ru000a10f504_is_an_index_linker_not_a_vanilla_fixed_bond():
    row = _normalized_case(bond_type="Линкер/облигации с индексируемым")

    assert row["bond_structure_type"] == "INDEX_LINKED"
    assert row["coupon_type"] == "index_linked"
    assert row["index_linked"] is True
    assert row["variable_nominal"] is True
    assert row["cashflows_deterministic"] is False
    assert row["initial_face_value_per_bond_rub"] == 1000
    assert row["face_value_per_bond_rub"] == 1034.7
    assert row["dirty_price_per_bond_rub"] == round(99.95 / 100 * 1034.7 + 4.62, 4)
    assert row["ytm_gross_pct"] is None
    assert row["ytm_net_est_pct"] is None
    assert row["duration_value"] is None
    assert row["g_spread_pp"] is None
    assert row["moex_yield_pct"] == 1.88
    assert row["moex_yield_source_field"] == "marketdata.YIELDATWAPRICE"
    assert {"INDEX_LINKED", "VARIABLE_NOMINAL", "INDETERMINATE_CASHFLOWS"} <= set(row["data_quality_flags"])


def test_vanilla_fixed_control_still_gets_a_reproducible_internal_ytm():
    row = _normalized_case(
        bond_type="Корпоративная облигация", current_face=1000, initial_face=1000,
        coupon_pct=12, moex_yield_pct=None,
    )

    assert row["bond_structure_type"] == "VANILLA_FIXED"
    assert row["cashflows_deterministic"] is True
    assert row["ytm_gross_pct"] is not None
    assert row["duration_value"] is not None
    assert row["g_spread_pp"] is not None


def test_structure_classifier_covers_supported_non_vanilla_cases():
    base = {
        "BONDTYPE": "Корпоративная облигация", "FACEVALUE": 1000,
        "FACEVALUEONSETTLEDATE": 1000, "COUPONPERCENT": 12,
    }
    cases = [
        ({**base, "BONDTYPE": "Флоатер с переменным купоном"}, {}, {}, "FLOATING"),
        (base, {}, {"amortizing": True}, "AMORTIZING"),
        ({**base, "OFFERDATE": "2027-01-01"}, {}, {}, "OFFER"),
        ({**base, "BONDTYPE": "Структурная облигация"}, {}, {}, "STRUCTURED_OR_COMPLEX"),
        ({**base, "COUPONPERCENT": None}, {}, {}, "UNKNOWN"),
    ]
    for raw, description, enrichment, expected in cases:
        result = classify_bond_structure(raw, description, enrichment)
        assert result["bond_structure_type"] == expected
        assert result["cashflows_deterministic"] is False


def test_offer_and_amortizing_bonds_do_not_publish_unmodelled_internal_ytm():
    offer = _normalized_case(
        bond_type="Корпоративная облигация",
        current_face=1000,
        initial_face=1000,
        coupon_pct=12,
        moex_yield_pct=None,
        enrichment_overrides={"has_offer": True},
    )
    amortizing = _normalized_case(
        bond_type="Амортизируемые облигации",
        current_face=1000,
        initial_face=1000,
        coupon_pct=12,
        moex_yield_pct=None,
        enrichment_overrides={"amortizing": True, "future_amortization_count": 4},
    )

    assert offer["bond_structure_type"] == "OFFER"
    assert amortizing["bond_structure_type"] == "AMORTIZING"
    for row in (offer, amortizing):
        assert row["cashflows_deterministic"] is False
        assert row["ytm_gross_pct"] is None
        assert row["ytm_net_est_pct"] is None
        assert row["duration_value"] is None
        assert row["g_spread_pp"] is None
        assert "INDETERMINATE_CASHFLOWS" in row["data_quality_flags"]


def test_vanilla_ytm_is_suppressed_when_it_disagrees_with_moex_reference():
    row = _normalized_case(
        bond_type="Корпоративная облигация",
        current_face=1000,
        initial_face=1000,
        coupon_pct=12,
        moex_yield_pct=30,
    )

    assert row["bond_structure_type"] == "VANILLA_FIXED"
    assert row["cashflows_deterministic"] is True
    assert row["ytm_calculation_status"] == "REFERENCE_MISMATCH"
    assert row["ytm_gross_pct"] is None
    assert row["ytm_net_est_pct"] is None
    assert row["duration_value"] is None
    assert row["g_spread_pp"] is None
    assert row["moex_yield_pct"] == 30
    assert "YTM_REFERENCE_MISMATCH" in row["data_quality_flags"]


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


def _config_with(tmp_path, **overrides):
    """Копия production-конфига с изменённым quality_gate — для проверки механизма гейта
    на маленькой фикстуре, где абсолютные пороги production-размера неприменимы."""
    config = load_json("bonds/portfolio_config.json")
    config["quality_gate"].update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def test_quality_gate_checks_sources_and_coverages(tmp_path):
    """Гейт проверяет свежесть источников и НАЛИЧИЕ материала для портфеля.

    Пороги рейтинга и сектора переведены с долей на абсолютные числа: доля мерила широту
    каталога, а не качество рекомендаций, и блокировала расширение универсума
    (см. docs/bond-universe-selection-audit.md). Фикстура здесь маленькая, поэтому
    production-пороги в ней заменяются соразмерными — проверяется механизм, а не значения.
    """
    universe = universe_fixture()
    rated = [row for row in universe["bonds"]
             if row.get("instrument_type") == "corp" and row.get("rating_rank") is not None]
    assert rated, "фикстура обязана содержать корпораты с рейтингом"

    fitted = _config_with(tmp_path,
                          minimum_rated_corporate_issues=1,
                          minimum_rated_corporate_issuers=1)
    gate = quality_gate(universe, config_path=fitted, today=date(2026, 8, 2))
    assert gate["status"] == "PASS", gate["failures"]
    assert gate["metrics"]["rated_corporate_issues"] == len(rated)

    # устаревшие цены ловятся независимо от порогов покрытия
    broken = deepcopy(universe)
    broken["as_of"]["prices"] = "2026-01-01"
    assert "prices_stale_or_missing" in quality_gate(
        broken, config_path=fitted, today=date(2026, 8, 2))["failures"]

    # абсолютный порог обязан срабатывать: это и есть защита от отказа источников рейтингов
    strict = _config_with(tmp_path, minimum_rated_corporate_issues=len(rated) + 1,
                          minimum_rated_corporate_issuers=1)
    assert "rated_corporate_issues_below_gate" in quality_gate(
        universe, config_path=strict, today=date(2026, 8, 2))["failures"]

    # доли рейтинга и сектора остаются В ОТЧЁТЕ, но больше не блокируют публикацию
    assert "rating_coverage" in gate["metrics"] and "sector_coverage" in gate["metrics"]
    assert not any(f.startswith(("rating_coverage", "sector_coverage")) for f in gate["failures"])


def test_duration_gate_excludes_only_explicitly_indeterminate_cashflows(tmp_path):
    universe = universe_fixture()
    complex_row = deepcopy(universe["bonds"][0])
    complex_row.update({
        "secid": "INDEX-LINKER",
        "cashflows_deterministic": False,
        "bond_structure_type": "INDEX_LINKED",
        "duration_value": None,
        "duration_type": "unavailable",
        "duration_source": "unavailable",
    })
    universe["bonds"].append(complex_row)
    config = _config_with(
        tmp_path,
        minimum_modified_duration_coverage=1.0,
        minimum_liquidity_history_coverage=0.0,
        minimum_rated_corporate_issues=1,
        minimum_rated_corporate_issuers=1,
    )

    gate = quality_gate(universe, config_path=config, today=date(2026, 8, 2))

    assert gate["status"] == "PASS", gate["failures"]
    assert gate["metrics"]["indeterminate_cashflow_issues"] == 1
    assert gate["metrics"]["duration_applicable_issues"] == len(universe["bonds"]) - 1
    assert gate["metrics"]["modified_duration_coverage"] == 1.0


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


def test_published_artifacts_do_not_expose_or_allocate_untrusted_ytm():
    site_bonds = Path("site/bonds")
    universe = json.loads((site_bonds / "universe.json").read_text(encoding="utf-8"))
    presets = json.loads((site_bonds / "portfolio_presets.json").read_text(encoding="utf-8"))
    rows = universe["bonds"]
    by_secid = {row["secid"]: row for row in rows}

    untrusted = [
        row for row in rows
        if row.get("bond_structure_type") != "VANILLA_FIXED"
        or row.get("ytm_calculation_status") == "REFERENCE_MISMATCH"
    ]
    assert untrusted
    for row in untrusted:
        assert row.get("ytm_gross_pct") is None, row["secid"]
        assert row.get("ytm_net_est_pct") is None, row["secid"]
        assert row.get("duration_value") is None, row["secid"]
        assert row.get("g_spread_pp") is None, row["secid"]

    allocated = {
        position["secid"]
        for allocation in (presets.get("allocations") or {}).values()
        for position in allocation.get("positions") or []
    }
    offenders = [
        secid for secid in allocated
        if secid in by_secid and (
            by_secid[secid].get("bond_structure_type") != "VANILLA_FIXED"
            or by_secid[secid].get("ytm_calculation_status") == "REFERENCE_MISMATCH"
        )
    ]
    assert offenders == []
