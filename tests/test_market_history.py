import json
from pathlib import Path

from scripts import build_market_history
from scripts.build_market_history import INSTRUMENTS, calculate_instrument, history_url


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


def test_market_drilldown_is_built_and_deployed_daily():
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    update = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert "market_history.json?t=" in app
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

    payload = build_market_history.build(output)

    cny = next(row for row in payload["instruments"] if row["id"] == "CNYRUB_TOM")
    assert cny["fallback"] is True
    assert payload["errors"] == [{"instrument": "CNYRUB_TOM", "error": "ISS timeout", "fallback": True}]
