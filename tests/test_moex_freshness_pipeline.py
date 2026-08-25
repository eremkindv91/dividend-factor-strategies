"""Production regressions for MOEX daily history and FUTOI freshness contracts."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from scripts import build_futures_positions as fut
from scripts import build_market_history as market
from scripts import build_positioning_interpretation as positioning
from scripts import check_predeploy_contract as predeploy
from scripts.moex_http import MoexHTTP, MoexTransportError

NOW = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)
DATES = ["2026-08-21", "2026-08-24"]


def source_row(day: str, long_value: int, short_value: int, *, is_fiz: int = 1):
    return [day, "IMOEX", is_fiz, 10, 5, long_value, short_value, 0, 0]


def page(rows, *, index=None, total=None, pagesize=None):
    payload = {"open_positions": {
        "columns": ["tradedate", "asset", "is_fiz", "persons_long", "persons_short",
                    "open_position_long", "open_position_short", "oichange_long", "oichange_short"],
        "data": rows,
    }}
    if index is not None:
        payload["open_positions.cursor"] = {
            "columns": ["INDEX", "TOTAL", "PAGESIZE"],
            "data": [[index, total, pagesize]],
        }
    return payload


def compact_rows(days=("2026-08-21", "2026-08-24")):
    return [{"d": day, "long": 100 + i, "short": 80, "net": 20 + i,
             "gross": 180 + i, "persons_long": 10, "persons_short": 5}
            for i, day in enumerate(days)]


def entry(rows=None, **overrides):
    rows = rows or compact_rows()
    result = {
        "asset": "IMOEX", "underlying": "IMOEX", "status": "fresh",
        "freshness_status": "fresh", "source_status": "live", "update_status": "updated",
        "latest_observation_date": rows[-1]["d"], "data_asof": rows[-1]["d"],
        "summary": fut.summarize(rows, None, None), "pagination": {"complete": True},
        **fut._series_fields(rows),
    }
    result.update(overrides)
    return result


def history_rows(count=230, start=date(2025, 1, 1)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": 100 + i,
             "high": 102 + i, "low": 99 + i, "close": 101 + i}
            for i in range(count)]


def test_01_openpositions_single_page(monkeypatch):
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: page([
        source_row("2026-08-24", 120, 90, is_fiz=0), source_row("2026-08-24", 100, 80),
    ]))
    rows = fut.positions("IMOEX", "2026-08-25")
    assert len(rows) == 1 and rows.diagnostics["pages_fetched"] == 1
    assert rows.diagnostics["complete"] is True


def test_02_openpositions_multiple_cursor_pages(monkeypatch):
    payloads = [
        page([source_row("2026-08-21", 100, 80)], index=0, total=2, pagesize=1),
        page([source_row("2026-08-24", 110, 80)], index=1, total=2, pagesize=1),
    ]
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: payloads.pop(0))
    rows = fut.positions("IMOEX", "2026-08-25")
    assert [row["d"] for row in rows] == DATES
    assert rows.diagnostics["pages_fetched"] == 2


def test_03_overlapping_pages_are_deduplicated(monkeypatch):
    payloads = [
        page([source_row("2026-08-21", 100, 80)], index=0, total=3, pagesize=1),
        page([source_row("2026-08-21", 101, 80), source_row("2026-08-24", 110, 80)],
             index=1, total=3, pagesize=2),
    ]
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: payloads.pop(0))
    rows = fut.positions("IMOEX", "2026-08-25")
    assert [row["d"] for row in rows] == DATES
    assert rows[0]["long"] == 101


def test_04_duplicate_dates_fail_contract_validation():
    rows = compact_rows(("2026-08-24", "2026-08-24"))
    with pytest.raises(fut.ValidationError, match="duplicate"):
        fut.validate_rows(rows, now=NOW)


def test_05_pagination_without_progress_fails(monkeypatch):
    repeated = page([source_row("2026-08-21", 100, 80)], index=0, total=3, pagesize=1)
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: repeated)
    monkeypatch.setattr(fut.time, "sleep", lambda _delay: None)
    with pytest.raises(fut.PaginationIncomplete, match="no progress"):
        fut.positions("IMOEX", "2026-08-25")


class _Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {"ok": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self._payload


def test_06_http_429_retries_then_succeeds():
    responses = [_Response(429), _Response(200)]
    session = type("Session", (), {"headers": {}, "get": lambda self, *_a, **_k: responses.pop(0)})()
    client = MoexHTTP(session=session, attempts=2, sleep=lambda _d: None, jitter=lambda: 0)
    assert client.get_json("https://iss.moex.com/test") == {"ok": True}


def test_07_timeout_retries_then_succeeds():
    calls = 0
    class Session:
        headers = {}
        def get(self, *_a, **_k):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise requests.Timeout("slow")
            return _Response(200)
    client = MoexHTTP(session=Session(), attempts=2, sleep=lambda _d: None, jitter=lambda: 0)
    assert client.get_json("https://iss.moex.com/test") == {"ok": True}


def test_08_permanent_timeout_uses_last_good(monkeypatch):
    monkeypatch.setattr(fut, "positions", lambda *_a, **_k: (_ for _ in ()).throw(fut.IssError("timeout")))
    result = fut._build_entry(contract={"asset": "IMOEX", "secid": "IMOEXF"},
                              existing=entry(), trading_dates=DATES, now=NOW, full_refresh=False)
    assert result["freshness_status"] == "cache_fallback" and result["fallback_used"] is True


def test_09_http_200_empty_is_remote_empty(monkeypatch):
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: page([]))
    with pytest.raises(fut.RemoteEmpty):
        fut.positions("IMOEX", "2026-08-25")


def test_10_malformed_columns_are_incomplete(monkeypatch):
    monkeypatch.setattr(fut, "http_json", lambda *_a, **_k: {
        "open_positions": {"columns": ["tradedate"], "data": [["2026-08-24"]]}})
    with pytest.raises(fut.PaginationIncomplete, match="columns incomplete"):
        fut.positions("IMOEX", "2026-08-25")


def test_11_newer_remote_tail_replaces_stale_cache(monkeypatch):
    old_days = [(date(2026, 7, 23) + timedelta(days=i)).isoformat() for i in range(30)]
    old = entry(compact_rows(old_days))
    incoming = fut.PositionRows(compact_rows((old_days[-1], "2026-08-24")), diagnostics={
        "complete": True, "pages_fetched": 1, "raw_rows": 4, "physical_person_rows": 2,
        "first_source_date": "2026-08-21", "remote_max_date": "2026-08-24",
        "output_max_date": "2026-08-24",
    })
    monkeypatch.setattr(fut, "positions", lambda *_a, **_k: incoming)
    result = fut._build_entry(contract={"asset": "IMOEX", "secid": "IMOEXF"}, existing=old,
                              trading_dates=DATES, now=NOW, full_refresh=False)
    assert result["data_asof"] == result["remote_max_date"] == "2026-08-24"


def test_12_legitimate_exchange_lag_is_not_transport_failure():
    assert fut.freshness_status("2026-08-21", DATES) == ("delayed_by_exchange", 1)


def test_13_unavailable_calendar_keeps_live_source_but_unknown_lag(monkeypatch):
    days = [(date(2026, 7, 26) + timedelta(days=i)).isoformat() for i in range(30)]
    incoming = fut.PositionRows(compact_rows(days), diagnostics={"complete": True, "remote_max_date": "2026-08-24"})
    monkeypatch.setattr(fut, "positions", lambda *_a, **_k: incoming)
    result = fut._build_entry(contract={"asset": "IMOEX", "secid": "IMOEXF"}, existing=None,
                              trading_dates=[], now=NOW, full_refresh=True)
    assert result["source_status"] == "live"
    assert result["expected_trading_date"] is None and result["lag_trading_sessions"] is None


def test_14_old_expected_date_is_not_reused_when_calendar_fails(monkeypatch):
    monkeypatch.setattr(fut, "moex_trading_dates", lambda _now: (_ for _ in ()).throw(fut.IssError("down")))
    monkeypatch.setattr(fut, "futures_catalog", lambda _now: {})
    monkeypatch.setattr(fut, "equity_assets", lambda *_a: ({}, []))
    payload = fut.build(NOW, existing={"meta": {"expected_trading_date": "2026-08-10"}})
    assert payload["meta"]["expected_trading_date"] is None
    assert payload["meta"]["calendar_status"] == "unavailable"


def test_15_market_live_failure_uses_published_last_good(tmp_path, monkeypatch):
    tracked, published = tmp_path / "tracked.json", tmp_path / "published.json"
    published.write_text(json.dumps({"instruments": [{
        "id": "IMOEX", "data_last": "2026-08-24", "series": [["2026-08-24", 1, 1, 1, 1]]}]}))
    monkeypatch.setattr(market, "fetch_history", lambda spec, *_a, **_k: (
        (_ for _ in ()).throw(TimeoutError("down")) if spec["id"] == "IMOEX" else history_rows()))
    monkeypatch.setattr(market, "fetch_current_session", lambda *_a, **_k: None)
    payload = market.build(tracked, today=date(2026, 8, 25), last_good=published)
    imoex = next(row for row in payload["instruments"] if row["id"] == "IMOEX")
    assert imoex["fallback_source"] == "gh_pages_last_good" and imoex["data_last"] == "2026-08-24"


def test_16_published_newer_than_tracked_is_selected(tmp_path):
    tracked, published = tmp_path / "tracked.json", tmp_path / "published.json"
    tracked.write_text(json.dumps({"instruments": [{"id": "IMOEX", "data_last": "2026-08-10"}]}))
    published.write_text(json.dumps({"instruments": [{"id": "IMOEX", "data_last": "2026-08-24"}]}))
    assert market.load_best_previous(tracked, published)["IMOEX"][1] == "gh_pages_last_good"


def test_17_intraday_does_not_change_completed_daily_asof(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "fetch_history", lambda *_a, **_k: history_rows())
    monkeypatch.setattr(market, "fetch_current_session", lambda *_a, **_k: {
        "date": "2026-08-25", "open": 1, "high": 2, "low": 1, "close": 2,
        "last_candle_at": "2026-08-25 12:00:00", "candle_count": 2, "status": "current_session"})
    payload = market.build(tmp_path / "history.json", today=date(2026, 8, 25))
    assert payload["current_session_asof"] == "2026-08-25"
    assert payload["daily_history_asof"] != payload["current_session_asof"]


def _write_positioning_inputs(tmp_path, monkeypatch, position_days, price_days):
    rows = compact_rows(position_days)
    (tmp_path / "futures.json").write_text(json.dumps({"indices": {"IMOEX": entry(rows)}}))
    series = [[day, 100 + i, 100 + i, 100 + i, 100 + i] for i, day in enumerate(price_days)]
    (tmp_path / "history.json").write_text(json.dumps({"instruments": [{"id": "IMOEX", "series": series}]}))
    monkeypatch.setattr(positioning, "POSITIONS", tmp_path / "futures.json")
    monkeypatch.setattr(positioning, "HISTORY", tmp_path / "history.json")
    monkeypatch.setattr(positioning, "OUT", tmp_path / "commentary.json")


def test_18_positions_newer_than_price_uses_common_day(tmp_path, monkeypatch):
    _write_positioning_inputs(tmp_path, monkeypatch, DATES, ["2026-08-21"])
    assert positioning.build(NOW)["meta"]["analysis_date"] == "2026-08-21"


def test_19_price_newer_than_positions_uses_common_day(tmp_path, monkeypatch):
    _write_positioning_inputs(tmp_path, monkeypatch, ["2026-08-21"], DATES)
    assert positioning.build(NOW)["meta"]["analysis_date"] == "2026-08-21"


def test_20_weekend_intersection_uses_last_completed_friday(tmp_path, monkeypatch):
    _write_positioning_inputs(tmp_path, monkeypatch, ["2026-08-21"], ["2026-08-21"])
    assert positioning.build(datetime(2026, 8, 23, tzinfo=timezone.utc))["meta"]["analysis_date"] == "2026-08-21"


def test_21_no_common_date_is_explicit_unavailable(tmp_path, monkeypatch):
    _write_positioning_inputs(tmp_path, monkeypatch, ["2026-08-21"], ["2026-08-24"])
    result = positioning.build(NOW)
    assert result["meta"]["status"] == "unavailable" and result["meta"]["analysis_date"] is None


def test_22_predeploy_rejects_internally_inconsistent_futures():
    bad = {"meta": {"as_of": "2026-08-24"}, "indices": {"IMOEX": entry(
        latest_observation_date="2026-08-21")}, "tickers": {}}
    assert "latest_observation_date" in predeploy.check_futures_positions(bad)


def test_23_predeploy_distinguishes_degraded_valid_from_broken():
    degraded = entry(fallback_used=True, source_status="last_good", freshness_status="cache_fallback")
    payload = {"meta": {"as_of": "2026-08-24", "calendar_status": "available"},
               "indices": {"IMOEX": degraded}, "tickers": {}}
    result = predeploy.check_futures_positions(payload)
    assert result == ("degraded", "IMOEX использует валидный cache fallback")
    assert predeploy.is_broken_result(result) is False
