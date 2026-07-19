# -*- coding: utf-8 -*-
import json
import math
import os
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_dividend_calendar as builder  # noqa: E402
import dividend_calendar_core as core  # noqa: E402
import validate_dividend_calendar as validator  # noqa: E402
import trading_calendar as tc  # noqa: E402
import build_site_status as site_status  # noqa: E402


def weekdays(value):
    return value.weekday() < 5


def security(ticker="SBER", isin="RU0009029540"):
    return {"ticker": ticker, "secid": ticker, "isin": isin, "name": "Сбербанк",
            "share_class": "ordinary", "board": "TQBR"}


def row(record="2026-07-20", amount=37.64, ticker="SBER", isin="RU0009029540", currency="RUB"):
    return {"secid": ticker, "isin": isin, "registryclosedate": record,
            "value": amount, "currencyid": currency}


def event(record="2026-07-20", amount=37.64, today=date(2026, 7, 10), **kwargs):
    result = core.normalize_moex_event(
        row(record, amount, kwargs.get("ticker", "SBER"), kwargs.get("isin", "RU0009029540"),
            kwargs.get("currency", "RUB")),
        security(kwargs.get("ticker", "SBER"), kwargs.get("isin", "RU0009029540")),
        kwargs.get("price", {"price": 322.0, "asof": "2026-07-10", "price_field": "LCLOSEPRICE", "fresh": True}),
        "2026-07-10T12:00:00+03:00", today, calendar_is_fallback=kwargs.get("calendar_fallback", False))
    assert result is not None
    return result


def payload(events):
    actionable = sum(1 for e in events if e["decision_status"] in core.ACTIONABLE_DECISIONS and not e.get("has_conflict"))
    return {"schema_version": "2.0", "meta": {
        "generated_at": "2026-07-10T12:00:00+03:00", "timezone": "Europe/Moscow", "status": "fresh",
        "event_count": len(events), "actionable_count": actionable,
        "recommended_count": sum(e["decision_status"] == "board_recommended" for e in events),
        "cancelled_count": sum(e["decision_status"] == "cancelled" for e in events),
        "conflict_count": sum(bool(e.get("has_conflict")) for e in events),
        "no_price_count": sum(e.get("price_status") == "missing" for e in events),
    }, "events": events}


def test_settlement_monday_record_uses_friday_last_buy():
    last_buy, source = core.calculate_last_buy_date(date(2026, 7, 20), 1, weekdays)
    assert last_buy == date(2026, 7, 17)
    assert source == "calculated_settlement"


def test_non_trading_record_date_and_holiday_gap():
    holidays = {date(2026, 7, 17)}
    calendar = lambda value: value.weekday() < 5 and value not in holidays
    last_buy, _ = core.calculate_last_buy_date(date(2026, 7, 19), 1, calendar)
    assert last_buy == date(2026, 7, 15)


def test_special_trading_override_and_explicit_date_priority():
    special = lambda value: value.weekday() < 5 or value == date(2026, 7, 18)
    assert core.settlement_date(date(2026, 7, 17), 1, special) == date(2026, 7, 18)
    explicit = date(2026, 7, 16)
    assert core.calculate_last_buy_date(date(2026, 7, 20), 1, special, explicit) == (explicit, "explicit_source")


def test_lifecycle_boundaries():
    base = event(today=date(2026, 7, 10))
    assert core.derive_event_status(base, date(2026, 7, 16)) == "buyable"
    assert core.derive_event_status(base, date(2026, 7, 17)) == "last_buy_today"
    assert core.derive_event_status(base, date(2026, 7, 20)) == "ex_dividend"
    assert core.derive_event_status(base, date(2026, 7, 21)) == "payment_deadline_open"
    assert core.derive_event_status(base, date(2026, 9, 1)) == "payment_deadline_passed_unknown"


def test_legal_deadlines_exclude_starting_day():
    start = date(2026, 7, 20)
    assert core.add_working_days(start, 10, weekdays) == date(2026, 8, 3)
    assert core.add_working_days(start, 25, weekdays) == date(2026, 8, 24)


def test_stable_id_does_not_change_with_amount():
    assert event(amount=37.64)["id"] == event(amount=40.0)["id"]


def test_merge_by_isin_detects_amount_conflict():
    left = event(amount=37.64)
    right = event(amount=40.0)
    right["source_evidence"] = [{"source": "tinvest", "source_url": "https://tbank.ru/",
                                 "observed_at": "2026-07-10T12:00:00+03:00", "fields": ["dividend_value"]}]
    merged = core.merge_normalized_events([left, right])
    assert len(merged) == 1
    assert merged[0]["has_conflict"] is True
    assert merged[0]["conflicts"][0]["field"] == "dividend_value"


def test_one_day_record_disagreement_is_not_auto_merged():
    first, second = event("2026-07-20"), event("2026-07-21")
    second["source_evidence"] = [{"source": "tinvest", "source_url": "https://tbank.ru/",
                                  "observed_at": "2026-07-10T12:00:00+03:00", "fields": ["record_date"]}]
    merged = core.merge_normalized_events([first, second])
    assert len(merged) == 2
    assert all(item["has_conflict"] for item in merged)


def test_same_source_consecutive_dividends_are_distinct_not_conflicts():
    merged = core.merge_normalized_events([event("2026-07-20", 9.13), event("2026-07-21", 4.58)])
    assert len(merged) == 2
    assert not any(item["has_conflict"] for item in merged)


def test_explicit_cancellation_wins():
    active = event()
    cancelled = deepcopy(active)
    cancelled["decision_status"] = "cancelled"
    cancelled["verification_status"] = "official_cancellation"
    cancelled["source_evidence"] = [{"source": "explicit_cancellation", "source_url": "https://e-disclosure.ru/",
                                     "observed_at": "2026-07-10T12:00:00+03:00", "fields": ["decision_status"]}]
    result = core.merge_normalized_events([active, cancelled])[0]
    assert result["decision_status"] == result["event_status"] == "cancelled"


def test_lifecycle_recommendation_to_approval_to_cancellation():
    recommended = event()
    recommended["decision_status"] = "board_recommended"
    recommended["source_evidence"] = [{"source": "board_disclosure", "source_url": "https://e-disclosure.ru/",
                                       "observed_at": "2026-07-10T12:00:00+03:00", "fields": ["decision_status"]}]
    approved = deepcopy(recommended)
    approved["decision_status"] = "shareholders_approved"
    approved["decision_date"] = "2026-06-30"
    approved["source_evidence"] = [{"source": "e_disclosure", "source_url": "https://e-disclosure.ru/",
                                    "observed_at": "2026-07-10T12:00:00+03:00",
                                    "fields": ["decision_status", "decision_date", "record_date", "dividend_value"]}]
    assert core.merge_normalized_events([recommended, approved])[0]["decision_status"] == "shareholders_approved"
    cancelled = deepcopy(approved)
    cancelled["decision_status"] = "cancelled"
    cancelled["source_evidence"] = [{"source": "explicit_cancellation", "source_url": "https://e-disclosure.ru/",
                                     "observed_at": "2026-07-10T12:00:00+03:00", "fields": ["decision_status"]}]
    assert core.merge_normalized_events([recommended, approved, cancelled])[0]["decision_status"] == "cancelled"


def test_currency_mismatch_and_fallback_flag():
    result = event(currency="USD", calendar_fallback=True)
    assert result["yield_pct"] is None
    assert "currency_mismatch" in result["quality_flags"]
    assert "settlement_calendar_fallback" in result["quality_flags"]


def test_change_tracking_preserves_first_seen_and_marks_update():
    old = event(amount=37.64)
    old["first_seen_at"] = "2026-07-01T08:00:00+03:00"
    old["last_changed_at"] = old["first_seen_at"]
    current = event(amount=40.0)
    core.apply_change_tracking([current], payload([old]), "2026-07-10T12:00:00+03:00")
    assert current["change_type"] == "updated"
    assert "dividend_value" in current["changed_fields"]
    assert current["first_seen_at"] == old["first_seen_at"]


def test_atomic_writer_keeps_last_good_on_invalid_number(tmp_path):
    target = tmp_path / "calendar.json"
    target.write_text('{"last_good":true}', encoding="utf-8")
    try:
        core.atomic_write_json(str(target), {"bad": math.nan})
    except ValueError:
        pass
    assert json.loads(target.read_text(encoding="utf-8")) == {"last_good": True}


def test_static_contract_has_no_portfolio_data_and_validates():
    document = payload([event()])
    assert "in_portfolio" not in json.dumps(document)
    assert validator.validate_payload(document) == []


def test_validator_rejects_unproven_shareholder_approval():
    approved = event()
    approved["decision_status"] = "shareholders_approved"
    errors = validator.validate_payload(payload([approved]))
    assert any("без official decision evidence" in error for error in errors)


def test_validator_accepts_official_shareholder_approval():
    approved = event()
    approved["decision_status"] = "shareholders_approved"
    approved["decision_date"] = "2026-06-30"
    approved["source_evidence"].append({"source": "e_disclosure", "source_url": "https://e-disclosure.ru/",
                                        "observed_at": "2026-07-10T12:00:00+03:00",
                                        "fields": ["decision_status", "decision_date", "record_date", "dividend_value"]})
    assert validator.validate_payload(payload([approved])) == []


def test_cache_fallback_and_empty_success_are_distinct():
    universe = {"AAA": security("AAA", None), "BBB": security("BBB", None)}
    cache = {"schema_version": "2.0", "records": {"BBB": {"rows": [row(ticker="BBB", isin=None)],
                                                              "observed_at": "old"}}}
    def fetcher(ticker):
        if ticker == "AAA":
            return []
        raise RuntimeError("timeout")
    rows, stats = builder.collect_moex_records(universe, cache, "now", fetcher=fetcher, max_workers=2)
    assert rows["AAA"] == []
    assert rows["BBB"][0]["secid"] == "BBB"
    assert stats == {"success": 1, "cache_used": 1, "failed": 0}


def test_build_payload_summary_counters():
    universe = {"SBER": security()}
    source = {"SBER": [row()]}
    prices = {"SBER": {"price": 322.0, "asof": "2026-07-10", "price_field": "LCLOSEPRICE", "fresh": True}}
    result = builder.build_payload(universe, source, {"success": 1, "cache_used": 0, "failed": 0},
                                   prices, {"source_ok": True, "price_asof": "2026-07-10", "n_fresh": 1,
                                            "n_cached": 0, "n_missing": 0}, None,
                                   datetime(2026, 7, 10, 12, tzinfo=tc.MSK))
    assert result["meta"]["event_count"] == result["meta"]["actionable_count"] == 1
    assert result["meta"]["conflict_count"] == 0
    assert result["meta"]["status"] == "fresh"


def test_site_status_exposes_dividend_metrics():
    document = payload([event()])
    document["meta"].update({"universe_coverage_pct": 99.0, "source_health": {"moex": {"success": 1}}})
    block = site_status.classify_block(
        ("dividend_calendar", "Дивидендный календарь", "dividend_calendar.json", ["meta.generated_at"], "market", 0),
        document, False, datetime(2026, 7, 10, 13, tzinfo=tc.MSK))
    assert block["metrics"]["event_count"] == 1
    assert block["metrics"]["universe_coverage_pct"] == 99.0


def test_frontend_and_deploy_contracts_are_wired():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "dividend_calendar.yml").read_text(encoding="utf-8")
    update = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")
    assert 'id="dividend-calendar"' in html and 'id="dividend-calendar-body"' in html
    assert "fetch('dividend_calendar.json" in js and "myPortfolioLoad()" in js
    assert "downloadDividendExport" in js and "params.get('calendar')" in js and "field_provenance" in js
    assert "navigator.sendBeacon" not in js
    assert ".dc-mobile-card" in css and "@media (max-width: 640px)" in css
    assert 'cron: "0 5 * * *"' in workflow and "group: gh-pages-publish" in workflow
    assert "t-tech-investments==1.49.2" in workflow and 'SSL_TBANK_VERIFY: "True"' in workflow
    assert "t-tech-investments==1.49.2" in update and 'SSL_TBANK_VERIFY: "True"' in update
    assert "validate_dividend_calendar.py" in workflow
    assert "site/dividend_calendar.json" in update and "site/dividend_calendar.json" in deploy
