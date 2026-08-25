import json
from pathlib import Path

from scripts import build_market_history
from scripts.build_market_history import (
    INSTRUMENTS,
    aggregate_current_session,
    calculate_instrument,
    history_url,
)


ROOT = Path(__file__).resolve().parents[1]


def real_mcftr_rows(limit=320):
    payload = json.loads((ROOT / "site" / "market_history.json").read_text(encoding="utf-8"))
    instrument = next(row for row in payload["instruments"] if row["id"] == "MCFTR")
    return [
        {"date": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4]}
        for row in instrument["series"][-limit:]
    ]


def test_market_indicators_are_computed_from_real_tracked_mcftr_series():
    spec = next(row for row in INSTRUMENTS if row["id"] == "MCFTR")

    result = calculate_instrument(spec, real_mcftr_rows(), "2026-07-10T00:00:00+00:00")

    assert result["source"] == "MOEX ISS"
    assert result["columns"] == ["date", "open", "high", "low", "close", "sma20", "sma50", "sma200"]
    assert len(result["series"]) == 320
    assert result["summary"]["sma200"] is not None
    assert 0 <= result["summary"]["rsi14"] <= 100
    assert result["summary"]["low20"] <= result["summary"]["last"] <= result["summary"]["high20"]


def test_market_history_sources_are_exact_official_moex_endpoints():
    for spec in INSTRUMENTS:
        source = history_url(spec)
        assert source.startswith("https://iss.moex.com/iss/history/")
        assert f"/boards/{spec['board']}/securities/{spec['id']}.json" in source


def test_current_session_aggregates_only_real_latest_moex_candles():
    payload = {
        "candles": {
            "columns": ["begin", "open", "high", "low", "close"],
            "data": [
                ["2026-08-17 18:40:00", 2100, 2105, 2098, 2102],
                ["2026-08-18 10:00:00", 2094, 2110, 2090, 2105],
                ["2026-08-18 10:10:00", 2105, 2120, 2100, 2115],
            ],
        },
    }

    result = aggregate_current_session(payload)

    assert result == {
        "date": "2026-08-18",
        "open": 2094.0,
        "high": 2120.0,
        "low": 2090.0,
        "close": 2115.0,
        "last_candle_at": "2026-08-18 10:10:00",
        "candle_count": 2,
        "status": "current_session",
    }


def test_market_drilldown_is_built_and_deployed_daily():
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    update = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    # редизайн, Итерация 3 (§6.1): статичные JSON грузятся через dataURL(path) → ?v=<build.version>
    # (раньше был ручной кэш-бастер ?t=Date.now(), полностью обходивший кэш браузера)
    assert "dataURL('market_history.json')" in app
    assert "addCandlestickSeries" in app
    assert "addAreaSeries" in app
    assert "marketUsesCloseLine" in app
    assert "axisLabelVisible: false" in app
    assert 'id="market-chart-mode"' in html
    assert "market-chart-dialog" in html
    assert "python scripts/build_market_history.py" in update
    assert "site/market_history.json" in update


def test_market_history_keeps_one_instrument_last_good_on_partial_iss_failure(tmp_path, monkeypatch):
    output = tmp_path / "market_history.json"
    output.write_text(
        json.dumps({"instruments": [{"id": "CNYRUB_TOM", "data_last": "2026-07-09", "series": [["2026-07-09", 1, 1, 1, 1, None, None, None]]}]}),
        encoding="utf-8",
    )
    rows = real_mcftr_rows()

    def fake_fetch(spec, from_date, session=None):
        if spec["id"] == "CNYRUB_TOM":
            raise TimeoutError("ISS timeout")
        return rows

    monkeypatch.setattr(build_market_history, "fetch_history", fake_fetch)
    monkeypatch.setattr(build_market_history, "fetch_current_session", lambda *_args, **_kwargs: None)

    payload = build_market_history.build(output)

    cny = next(row for row in payload["instruments"] if row["id"] == "CNYRUB_TOM")
    assert cny["fallback"] is True
    assert payload["fallback_instruments"] == ["CNYRUB_TOM"]
    assert payload["fallback_source"] == "tracked_bootstrap"
    assert payload["errors"] == [{"instrument": "CNYRUB_TOM", "error": "ISS timeout", "fallback": True}]


def test_strict_allows_recent_valid_last_good_but_rejects_unknown_or_old_fallback():
    instrument = {
        "id": "IMOEX", "data_last": "2026-08-24", "live_fetch_status": "failed",
        "fallback_used": True, "fallback_source": "gh_pages_last_good",
    }
    payload = {
        "data_asof": "2026-08-24", "lag_trading_sessions": 1,
        "instruments": [instrument],
    }
    assert build_market_history.validate_strict(payload) == []

    payload["lag_trading_sessions"] = 2
    assert "not live within SLA" in build_market_history.validate_strict(payload)[0]
    payload["lag_trading_sessions"] = None
    assert "not live within SLA" in build_market_history.validate_strict(payload)[0]


def test_build_publishes_current_session_without_changing_completed_close_asof(tmp_path, monkeypatch):
    output = tmp_path / "market_history.json"
    rows = real_mcftr_rows()
    monkeypatch.setattr(build_market_history, "fetch_history", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(build_market_history, "fetch_current_session", lambda *_args, **_kwargs: {
        "date": "2026-08-25", "open": 2100.0, "high": 2120.0, "low": 2090.0,
        "close": 2110.0, "last_candle_at": "2026-08-25 14:50:00",
        "candle_count": 30, "status": "current_session",
    })

    payload = build_market_history.build(output, today=build_market_history.date(2026, 8, 25))

    assert payload["data_asof"] == rows[-1]["date"]
    assert payload["current_session_asof"] == "2026-08-25"
    assert all(row["current_session"]["date"] == "2026-08-25" for row in payload["instruments"])
