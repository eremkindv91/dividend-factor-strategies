# -*- coding: utf-8 -*-
import csv
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dividend_calendar_core as core  # noqa: E402
import dividend_disclosure as disclosure  # noqa: E402
import tinvest_dividends as tinvest  # noqa: E402
import build_dividend_calendar as builder  # noqa: E402
from src.data_sources.disclosure_adapter import discover_dividend_decision_links  # noqa: E402


def test_tinvest_uses_current_official_rest_host():
    assert tinvest.SDK_TARGET == "invest-public-api.tbank.ru"
    assert tinvest.SDK_PACKAGE == "t-tech-investments==1.49.2"


def test_tinvest_normalizes_official_sdk_responses():
    found = tinvest._sdk_response_payload("FindInstrument", SimpleNamespace(instruments=[
        SimpleNamespace(isin="RU000TEST000", ticker="TEST", uid="uid-1", figi="figi-1"),
    ]))
    assert found["instruments"][0]["uid"] == "uid-1"

    dividend = SimpleNamespace(
        record_date=__import__("datetime").datetime(2026, 7, 20, tzinfo=__import__("datetime").timezone.utc),
        last_buy_date=None, payment_date=None, declared_date=None,
        dividend_net=SimpleNamespace(units=10, nano=500_000_000), dividend_type="Regular Cash",
    )
    payload = tinvest._sdk_response_payload("GetDividends", SimpleNamespace(dividends=[dividend]))
    assert payload["dividends"][0]["recordDate"].startswith("2026-07-20")
    assert tinvest.quotation_to_float(payload["dividends"][0]["dividendNet"]) == 10.5


def _event():
    result = core.normalize_moex_event(
        {"secid": "TEST", "isin": "RU000TEST000", "registryclosedate": "2026-07-20", "value": 10.0, "currencyid": "RUB"},
        {"ticker": "TEST", "secid": "TEST", "isin": "RU000TEST000", "name": "Test", "board": "TQBR"},
        {"price": 100.0, "asof": "2026-07-10", "price_field": "LAST", "fresh": True},
        "2026-07-10T00:00:00+03:00", date(2026, 7, 10),
    )
    assert result is not None
    return result


def test_tinvest_normalization_and_exact_match_only():
    raw = {"recordDate": {"seconds": "1784505600"}, "lastBuyDate": {"seconds": "1784332800"},
           "paymentDate": {"seconds": "1785542400"}, "dividendType": "DIVIDEND_TYPE_REGULAR"}
    parsed = tinvest.normalize_dividend(raw)
    assert parsed["record_date"] == "2026-07-20"
    assert parsed["last_buy_date"] == "2026-07-18"
    assert tinvest.select_matching_dividend(_event(), [raw]) == parsed
    assert tinvest.select_matching_dividend(_event(), [{**raw, "recordDate": {"seconds": "1784592000"}}]) is None


def test_tinvest_cancellation_is_explicit_and_never_creates_event():
    event = _event()
    rows = {"RU000TEST000": [{"recordDate": "2026-07-20", "dividendType": "DIVIDEND_TYPE_CANCELLED"}]}
    result, count = tinvest.apply_tinvest_enrichment([event], rows, "2026-07-10T01:00:00+03:00")
    assert count == 1
    assert result[0]["decision_status"] == "cancelled"
    assert result[0]["verification_status"] == "broker_structured_cancellation"


def test_tinvest_collect_uses_structured_find_and_get_without_exposing_token():
    calls = []
    def post(_token, method, payload):
        calls.append((method, payload))
        if method == "FindInstrument":
            return {"instruments": [{"isin": "RU000TEST000", "uid": "uid-1"}]}
        return {"dividends": [{"recordDate": "2026-07-20"}]}
    payloads, stats = tinvest.collect_payloads([_event()], "secret", "2026-01-01", "2026-12-31", post=post)
    assert payloads["RU000TEST000"][0]["recordDate"] == "2026-07-20"
    assert stats["success"] == 1 and stats["failed"] == 0
    assert calls[1][1]["instrumentId"] == "uid-1"


def test_tinvest_uses_cache_when_broker_api_is_unavailable():
    cache = {"schema_version": 1, "records": {"RU000TEST000": {"rows": [{"recordDate": "2026-07-20"}]}}}
    payloads, stats = tinvest.collect_payloads([_event()], "secret", "2026-01-01", "2026-12-31", post=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")), cache=cache)
    assert payloads["RU000TEST000"][0]["recordDate"] == "2026-07-20"
    assert stats["status"] == "fallback" and stats["cache_used"] == 1


def test_tinvest_reports_only_sanitized_error_codes():
    def unauthorized(_token, _method, _payload):
        raise tinvest.TInvestRequestError("http_401")

    payloads, stats = tinvest.collect_payloads(
        [_event()], "secret-value-must-not-leak", "2026-01-01", "2026-12-31", post=unauthorized,
    )

    assert payloads == {}
    assert stats["errors"] == {"find_instrument_http_401": 1}
    assert "secret" not in str(stats)


def test_tinvest_reports_instrument_lookup_miss_separately():
    payloads, stats = tinvest.collect_payloads(
        [_event()], "secret", "2026-01-01", "2026-12-31",
        post=lambda _token, _method, _payload: {"instruments": []},
    )

    assert payloads == {}
    assert stats["errors"] == {"instrument_not_found": 1}


def test_tinvest_reports_failure_stage_and_exception_class_without_message():
    def broken_find(_token, _method, _payload):
        raise TypeError("response containing a sensitive value")

    _payloads, stats = tinvest.collect_payloads(
        [_event()], "secret", "2026-01-01", "2026-12-31", post=broken_find,
    )

    assert stats["errors"] == {"find_instrument_unexpected_typeerror": 1}
    assert "sensitive" not in str(stats)


def test_tinvest_stops_after_consecutive_infrastructure_failures():
    calls = []
    events = [_event() for _ in range(3)]
    for index, event in enumerate(events):
        event["isin"] = f"RU000TEST00{index}"
        event["secid"] = f"TEST{index}"

    def timeout(_token, method, payload):
        calls.append((method, payload))
        raise tinvest.TInvestRequestError("timeout")

    payloads, stats = tinvest.collect_payloads(
        events, "secret", "2026-01-01", "2026-12-31", post=timeout,
        max_consecutive_failures=2,
    )

    assert payloads == {}
    assert len(calls) == 2
    assert stats["failed"] == 3
    assert stats["errors"] == {
        "find_instrument_timeout": 2,
        "skipped_after_circuit_breaker": 1,
    }


def test_builder_keeps_p1_sources_optional_without_token(monkeypatch):
    monkeypatch.setattr(builder.disclosure, "load_verified_decisions", lambda _root: ([], []))
    result = builder.build_payload(
        {"TEST": {"ticker": "TEST", "secid": "TEST", "isin": "RU000TEST000", "name": "Test", "board": "TQBR"}},
        {"TEST": [{"secid": "TEST", "isin": "RU000TEST000", "registryclosedate": "2026-07-20", "value": 10.0, "currencyid": "RUB"}]},
        {"success": 1, "cache_used": 0, "failed": 0},
        {"TEST": {"price": 100.0, "asof": "2026-07-10", "price_field": "LAST", "fresh": True}},
        {"source_ok": True, "price_asof": "2026-07-10", "n_fresh": 1, "n_cached": 0, "n_missing": 0},
        None, __import__("datetime").datetime(2026, 7, 10),
    )
    assert result["meta"]["source_health"]["tinvest"]["status"] == "disabled"
    assert result["meta"]["source_health"]["disclosure"]["status"] == "not_configured"


def test_verified_disclosure_promotes_only_with_document_evidence(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "company_sources.csv").write_text("ticker,source_url\nTEST,https://issuer.example/disclosure\n", encoding="utf-8")
    with (data / "dividend_disclosure_decisions.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=disclosure.REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerow({"ticker": "TEST", "isin": "RU000TEST000", "decision_status": "shareholders_approved", "decision_date": "2026-06-30", "dividend_value": "10", "currency": "RUB", "record_date": "2026-07-20", "payment_date": "2026-07-31", "last_buy_date": "", "source_url": "https://issuer.example/disclosure", "document_title": "Решение ГОСА", "review_status": "verified", "reviewed_at": "2026-07-01", "notes": ""})
    decisions, rejected = disclosure.load_verified_decisions(tmp_path)
    assert not rejected and len(decisions) == 1
    result, applied = disclosure.apply_verified_decisions([_event()], decisions, {"TEST": {"ticker": "TEST", "secid": "TEST", "isin": "RU000TEST000", "name": "Test", "board": "TQBR"}}, {"TEST": {"price": 100.0, "asof": "2026-07-10", "fresh": True, "price_field": "LAST"}}, "2026-07-10T00:00:00+03:00", date(2026, 7, 10))
    assert applied == 1
    assert result[0]["decision_status"] == "shareholders_approved"
    assert result[0]["payment_date"] == "2026-07-31"
    assert result[0]["field_provenance"]["decision_status"] == "official_disclosure"


def test_disclosure_rejects_untrusted_or_unreviewed_rows(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "dividend_disclosure_decisions.csv").write_text(
        ",".join(disclosure.REQUIRED_COLUMNS) + "\nTEST,,shareholders_approved,2026-06-30,10,RUB,2026-07-20,,,https://untrusted.example/x,decision,verified,2026-07-01,\n",
        encoding="utf-8",
    )
    decisions, rejected = disclosure.load_verified_decisions(tmp_path)
    assert decisions == []
    assert rejected[0]["reason"] == "source_url_not_in_verified_issuer_registry"


def test_disclosure_candidate_parser_needs_manual_verification():
    html = '<a href="/doc/1">Решение годового общего собрания акционеров о выплате дивидендов</a><a href="/policy">Дивидендная политика</a>'
    rows = discover_dividend_decision_links(html, "https://issuer.example/", {"ticker": "TEST", "company_name": "Test"}, "2026-07-10T00:00:00+03:00")
    assert len(rows) == 1
    assert rows[0]["candidate_status"] == "shareholders_approved"
    assert rows[0]["discovery_status"] == "needs_manual_verification"
