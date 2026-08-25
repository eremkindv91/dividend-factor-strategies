"""Contract tests for the incremental MOEX physical-position pipeline."""

import importlib.util
import http.client
import io
import json
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from scripts.moex_http import MoexHTTP, MoexTransportError


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "build_futures_positions.py"
    spec = importlib.util.spec_from_file_location("build_futures_positions", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_futures_positions"] = module
    spec.loader.exec_module(module)
    return module


pos = _load()
NOW = datetime(2026, 8, 11, 7, tzinfo=timezone.utc)
TRADING_DATES = ["2026-08-06", "2026-08-07", "2026-08-10"]


def _payload(rows):
    columns = ["tradedate", "asset", "is_fiz", "persons_long", "persons_short",
               "open_position_long", "open_position_short", "oichange_long", "oichange_short"]
    return {"open_positions": {"columns": columns,
                                "data": [[row.get(column) for column in columns] for row in rows]}}


def _row(day, long_value, short_value, *, asset="SBRF", is_fiz=1, pl=10, ps=5):
    return {"tradedate": day, "asset": asset, "is_fiz": is_fiz,
            "persons_long": pl, "persons_short": ps,
            "open_position_long": long_value, "open_position_short": short_value,
            "oichange_long": 0, "oichange_short": 0}


def _rows(start=1, count=40, *, asset="SBRF", base=1000):
    first = date(2026, 6, 1) + timedelta(days=start - 1)
    return [{"d": (first + timedelta(days=offset)).isoformat(),
             "long": base + start + offset, "short": base,
             "net": start + offset, "gross": 2 * base + start + offset,
             "persons_long": 10, "persons_short": 5}
            for offset in range(count)]


def _entry(rows, **extra):
    data = {
        "asset": "SBRF", "underlying": "SBRF", "current_futures": "SRU6", "secid": "SRU6",
        "status": "fresh", "latest_observation_date": rows[-1]["d"],
        "summary": pos.summarize(rows, None, None), **pos._series_fields(rows),
    }
    data.update(extra)
    return data


def _contract(asset="SBRF", secid="SRU6", expiration="2026-09-17"):
    return {"asset": asset, "secid": secid, "expiration": expiration,
            "trading_status": "trading", "open_interest": 1000,
            "value_today": 100_000.0, "volume_today": 100, "trades_today": 20}


# A-C: normal MOEX response and exact sign.
def test_positions_reads_only_physical_people_and_exact_net(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: _payload([
        _row("2026-08-10", 100, 80, is_fiz=1),
        _row("2026-08-10", 80, 100, is_fiz=0),
        _row("2026-08-11", 80, 100, is_fiz=1),
    ]))

    rows = pos.positions("SBRF", "2026-08-11")

    assert [(row["long"], row["short"], row["net"]) for row in rows] == [
        (100, 80, 20), (80, 100, -20),
    ]


# D: empty response is explicit, not synthetic zeroes.
def test_empty_moex_response_returns_no_observations(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: _payload([]))
    with pytest.raises(pos.RemoteEmpty):
        pos.positions("SBRF", "2026-08-10")


# E: timeout is retried and then raised.
def test_timeout_has_bounded_retries(monkeypatch):
    calls = []

    class Session:
        headers = {}
        def get(self, *_args, **_kwargs):
            calls.append(1)
            raise requests.Timeout("timeout")

    client = MoexHTTP(session=Session(), attempts=3, sleep=lambda _delay: None, jitter=lambda: 0)
    with pytest.raises(MoexTransportError):
        client.get_json("https://example.test")
    assert len(calls) == 3


def test_remote_disconnect_has_bounded_retries(monkeypatch):
    calls = []

    class Session:
        headers = {}
        def get(self, *_args, **_kwargs):
            calls.append(1)
            raise requests.ConnectionError("closed without response")

    client = MoexHTTP(session=Session(), attempts=3, sleep=lambda _delay: None, jitter=lambda: 0)
    with pytest.raises(MoexTransportError):
        client.get_json("https://example.test")
    assert len(calls) == 3


# F: 5xx is retried, while retry remains bounded.
def test_http_5xx_is_retried(monkeypatch):
    calls = []

    class Response:
        status_code = 503
        def raise_for_status(self):
            raise requests.HTTPError("503", response=self)

    class Session:
        headers = {}
        def get(self, *_args, **_kwargs):
            calls.append(1)
            return Response()

    client = MoexHTTP(session=Session(), attempts=2, sleep=lambda _delay: None, jitter=lambda: 0)
    with pytest.raises(MoexTransportError):
        client.get_json("https://example.test")
    assert len(calls) == 2


# G: duplicates are rejected before publication.
def test_duplicate_dates_fail_validation():
    row = {"d": "2026-08-10", "long": 100, "short": 80, "net": 20,
           "persons_long": 1, "persons_short": 1}
    with pytest.raises(pos.ValidationError, match="duplicate"):
        pos.validate_rows([row, dict(row)], now=NOW)


# H: weekend expected date is the latest completed MOEX session returned by ISS.
def test_weekend_calendar_uses_friday(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: {
        "history": {"columns": ["TRADEDATE"], "data": [["2026-08-06"], ["2026-08-07"]]}})
    saturday = datetime(2026, 8, 8, 9, tzinfo=timezone.utc)
    assert pos.moex_trading_dates(saturday)[-1] == "2026-08-07"


def _catalog_payload():
    securities_columns = ["SECID", "SHORTNAME", "ASSETCODE", "LASTTRADEDATE",
                          "PREVOPENPOSITION", "MINSTEP", "STEPPRICE"]
    market_columns = ["SECID", "OPENPOSITION", "VALTODAY", "VOLTODAY", "NUMTRADES", "TRADEDATE"]
    securities = [
        ["SRM6", "SBRF-6.26", "SBRF", "2026-06-18", 50_000, 1, 1],
        ["SRU6", "SBRF-9.26", "SBRF", "2026-09-17", 900_000, 1, 1],
        ["SRZ6", "SBRF-12.26", "SBRF", "2026-12-17", 20_000, 1, 1],
    ]
    market = [
        ["SRU6", 1_000_000, 5_000_000, 100_000, 10_000, "2026-08-11"],
        ["SRZ6", 25_000, 50_000, 1_000, 100, "2026-08-11"],
    ]
    return {"securities": {"columns": securities_columns, "data": securities},
            "marketdata": {"columns": market_columns, "data": market}}


# I/J: rollover resolution excludes expiry and picks the liquid live contract.
def test_rollover_selects_liquid_nonexpired_contract(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: _catalog_payload())
    catalog = pos.futures_catalog(NOW)
    assert catalog["SBRF"]["secid"] == "SRU6"
    assert catalog["SBRF"]["expiration"] == "2026-09-17"


def test_expired_contract_is_never_selected(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: _catalog_payload())
    assert pos.futures_catalog(NOW)["SBRF"]["secid"] != "SRM6"


def test_quarterly_underlying_is_not_silently_replaced_by_perpetual(monkeypatch):
    catalog = {
        "SBRF": _contract("SBRF", "SRU6"),
        "SBERF": _contract("SBERF", "SBERF", "2100-01-01"),
    }
    monkeypatch.setattr(pos, "shares_by_emitent", lambda: {"484": "SBER"})
    monkeypatch.setattr(pos, "_contract_description", lambda secid: {
        "GROUPTYPE": "Акции", "EMITTER_ID": "484",
        "CONTRACTNAME": (
            "Однодневный фьючерсный контракт с автопролонгацией на акции Сбербанк"
            if secid == "SBERF" else "Фьючерсный контракт на обыкновенные акции Сбербанк"
        ),
    })
    monkeypatch.setattr(pos.time, "sleep", lambda *_args: None)

    resolved, errors = pos.equity_assets(catalog, {"tickers": {"SBER": {"asset": "SBERF"}}})

    assert not errors
    assert resolved["SBER"]["asset"] == "SBRF"
    assert resolved["SBER"]["secid"] == "SRU6"


# K: missing current futures is explicit unavailable, not another asset's data.
def test_missing_futures_marks_previous_symbol_unavailable(monkeypatch):
    previous_rows = _rows()
    existing = {"tickers": {"SBER": _entry(previous_rows)}, "indices": {}}
    monkeypatch.setattr(pos, "moex_trading_dates", lambda _now: TRADING_DATES)
    monkeypatch.setattr(pos, "futures_catalog", lambda _now: {})
    monkeypatch.setattr(pos, "equity_assets", lambda _catalog, _existing: ({}, []))

    payload = pos.build(NOW, existing=existing)

    assert payload["tickers"]["SBER"]["status"] == "unavailable"
    assert payload["tickers"]["SBER"]["asset"] == "SBRF"


def _patch_build(monkeypatch, contracts, fetch):
    monkeypatch.setattr(pos, "moex_trading_dates", lambda _now: TRADING_DATES)
    monkeypatch.setattr(pos, "futures_catalog", lambda _now: {
        item["asset"]: item for item in contracts.values()})
    monkeypatch.setattr(pos, "equity_assets", lambda _catalog, _existing: (contracts, []))
    monkeypatch.setattr(pos, "positions", fetch)
    monkeypatch.setattr(pos.time, "sleep", lambda *_args: None)


# L: one symbol can fail while another is updated.
def test_partial_batch_failure_is_isolated(monkeypatch):
    contracts = {"SBER": _contract(), "GAZP": _contract("GAZR", "GZU6")}

    def fetch(asset, *_args):
        if asset == "GAZR":
            raise pos.IssError("temporary")
        return _rows(asset=asset)

    _patch_build(monkeypatch, contracts, fetch)
    payload = pos.build(NOW, full_refresh=True)

    assert payload["tickers"]["SBER"]["status"] == "stale"  # July is older than expected.
    assert payload["tickers"]["GAZP"]["status"] == "unavailable"
    assert payload["meta"]["update_counts"]["failed"] == 1


def test_complete_live_short_history_is_not_a_pagination_failure(monkeypatch):
    incoming = pos.PositionRows(_rows(count=2), diagnostics={
        "complete": True,
        "pages_fetched": 1,
        "raw_rows": 4,
        "physical_person_rows": 2,
        "first_source_date": "2026-07-01",
        "remote_max_date": "2026-07-02",
        "output_max_date": "2026-07-02",
    })
    monkeypatch.setattr(pos, "positions", lambda *_args, **_kwargs: incoming)

    result = pos._build_entry(
        contract=_contract(), existing=None, trading_dates=TRADING_DATES,
        now=NOW, full_refresh=True,
    )

    assert result["source_status"] == "live"
    assert result["pagination"]["complete"] is True
    assert result["analysis_ready"] is False
    assert result["status"] == "unavailable"
    assert result["update_status"] == "updated"

    imoex = {**result, "asset": "IMOEX", "underlying": "IMOEX"}
    payload = {
        "meta": {"schema_version": 3, "calendar_status": "available"},
        "tickers": {"SBER": result},
        "indices": {"IMOEX": imoex},
    }
    assert pos.strict_failures(payload, now=NOW) == []


def test_issuer_mapping_failure_uses_last_good_lineage(monkeypatch):
    old = _entry(_rows(), status="fresh")
    existing = {"tickers": {"SBER": old}, "indices": {}}
    monkeypatch.setattr(pos, "moex_trading_dates", lambda _now: TRADING_DATES)
    monkeypatch.setattr(pos, "futures_catalog", lambda _now: {"SBRF": _contract()})
    monkeypatch.setattr(
        pos,
        "equity_assets",
        lambda *_args: (_ for _ in ()).throw(pos.IssError("remote disconnected")),
    )
    monkeypatch.setattr(pos, "positions", lambda *_args, **_kwargs: _rows())
    monkeypatch.setattr(pos.time, "sleep", lambda *_args: None)

    payload = pos.build(NOW, existing=existing)

    entry = payload["tickers"]["SBER"]
    assert entry["mapping_source"] == "last_good emitter mapping"
    assert entry["status"] == "stale"
    assert "mapping validation unavailable" in entry["reason"]


# M: cache fallback is retained and truthfully marked stale.
def test_cache_fallback_is_marked_stale(monkeypatch):
    old = _entry(_rows())
    monkeypatch.setattr(pos, "positions", lambda *_args, **_kwargs: (_ for _ in ()).throw(pos.IssError("down")))
    entry = pos._build_entry(
        contract=_contract(), existing=old, trading_dates=TRADING_DATES, now=NOW,
        full_refresh=False,
    )
    assert entry["status"] == "stale"
    assert entry["source_status"] == "last_good"
    assert entry["freshness_status"] == "cache_fallback"
    assert entry["latest_observation_date"] == old["latest_observation_date"]


# N: incremental merge requests from the last date, replaces overlap and appends once.
def test_incremental_update_merges_and_deduplicates(monkeypatch):
    old_rows = _rows(start=1, count=40)
    old = _entry(old_rows)
    seen_from = []

    def fetch(_asset, _to, date_from):
        seen_from.append(date_from)
        overlap = dict(old_rows[-1])
        overlap["long"] += 10
        overlap["net"] += 10
        new = {"d": "2026-08-10", "long": 1200, "short": 1000, "net": 200,
               "gross": 2200, "persons_long": 12, "persons_short": 6}
        return [overlap, new]

    monkeypatch.setattr(pos, "positions", fetch)
    entry = pos._build_entry(
        contract=_contract(), existing=old, trading_dates=TRADING_DATES, now=NOW,
        full_refresh=False,
    )
    assert seen_from == [old_rows[-1]["d"]]
    assert entry["dates"].count(old_rows[-1]["d"]) == 1
    assert entry["latest_observation_date"] == "2026-08-10"
    assert entry["status"] == "fresh"


# O: full rebuild ignores cached rows and starts at the configured history origin.
def test_full_refresh_ignores_cached_history(monkeypatch):
    seen_from = []

    def fetch(_asset, _to, date_from):
        seen_from.append(date_from)
        return _rows()

    monkeypatch.setattr(pos, "positions", fetch)
    pos._build_entry(
        contract=_contract(), existing=_entry(_rows()), trading_dates=TRADING_DATES,
        now=NOW, full_refresh=True,
    )
    assert seen_from == [pos.HISTORY_FROM]


# P: freshness uses trading sessions, not calendar-day equality.
@pytest.mark.parametrize("latest, expected_status, lag", [
    ("2026-08-10", "fresh", 0),
    ("2026-08-07", "delayed_by_exchange", 1),
    ("2026-08-06", "stale", 2),
])
def test_latest_observation_freshness(latest, expected_status, lag):
    assert pos.freshness_status(latest, TRADING_DATES) == (expected_status, lag)


# Q: frontend contract has one authoritative date and exact long/short/net arrays.
def test_frontend_data_contract_is_consistent(monkeypatch):
    rows = _rows()
    rows[-1] = {**rows[-1], "d": "2026-08-10", "long": 100, "short": 80,
                "net": 20, "gross": 180}
    monkeypatch.setattr(pos, "positions", lambda *_args, **_kwargs: rows)
    entry = pos._build_entry(
        contract=_contract(), existing=None, trading_dates=TRADING_DATES, now=NOW,
        full_refresh=True,
    )
    assert entry["latest_observation_date"] == entry["summary"]["as_of"] == entry["dates"][-1]
    assert entry["net"][-1] == entry["long"][-1] - entry["short"][-1] == 20
    assert entry["source"] == "MOEX ISS openpositions"
    assert entry["status"] == "fresh"


def test_atomic_write_never_emits_nan(tmp_path):
    path = tmp_path / "positions.json"
    with pytest.raises(ValueError):
        pos.atomic_write({"value": float("nan")}, path)
    assert not path.exists()


def test_contract_multiplier_uses_official_step_fields(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda *_args, **_kwargs: {
        "securities": {"columns": ["SECID", "MINSTEP", "STEPPRICE"],
                       "data": [["IMOEXF", 0.05, 0.5]]}})
    multiplier, _ = pos.contract_multiplier("IMOEXF")
    assert multiplier == pytest.approx(10.0)


def test_meta_forbids_cross_asset_contract_sum(monkeypatch):
    monkeypatch.setattr(pos, "moex_trading_dates", lambda _now: TRADING_DATES)
    monkeypatch.setattr(pos, "futures_catalog", lambda _now: {})
    monkeypatch.setattr(pos, "equity_assets", lambda _catalog, _existing: ({}, []))
    payload = pos.build(NOW)
    assert "never summed" in payload["meta"]["no_cross_series_sum"]
    assert payload["meta"]["net_formula"] == "long_phys - short_phys"


def test_daily_workflow_owns_positioning_and_manual_workflow_has_pytest():
    daily = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    manual = (ROOT / ".github" / "workflows" / "update-futures-positions.yml").read_text(encoding="utf-8")

    assert "python scripts/build_futures_positions.py --audit" in daily
    assert "schedule:" not in manual
    assert "python -m pip install --quiet pytest" in manual
    assert "python scripts/build_futures_positions.py --audit" in manual
