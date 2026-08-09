"""Timeframe contracts for stock and market charts.

The 1D view must come from real MOEX intraday candles. Longer stock periods are
calendar windows measured from the latest available candle, not mislabeled trading
session counts or today's date.
"""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"
HTML = ROOT / "site" / "index.html"
CSS = ROOT / "site" / "styles.css"


def _source() -> str:
    return APP.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    signature = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source[start:])
    assert signature, f"function {name} signature not found"
    body_start = start + signature.end() - 1
    depth = 0
    for index in range(body_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"function {name} is not closed")


def _node(script: str):
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def test_stock_periods_include_real_intraday_and_calendar_windows():
    source = _source()
    block = source[source.index("const STOCK_CHART_PERIODS"):source.index("function stockChartFromDate")]

    for pair in ["['1d', '1Д']", "['31', '1М']", "['183', '6М']",
                 "['365', '1Г']", "['1096', '3Г']", "['0', 'Макс']"]:
        assert pair in block
    assert "['127', '6М']" not in block
    assert "['252', '1Г']" not in block
    assert "STOCK_CHART_DEFAULT = '365'" in block


def test_daily_rows_are_trimmed_from_latest_market_candle():
    source = _source()
    trim = _function(source, "stockTrimDailyRows")
    result = _node(
        f"""
{trim}
const rows = [
  ['2026-01-01'], ['2026-01-15'], ['2026-02-01'], ['2026-02-15']
];
console.log(JSON.stringify(stockTrimDailyRows(rows, 31).map((row) => row[0])));
"""
    )

    assert result == ["2026-01-15", "2026-02-01", "2026-02-15"]


def test_intraday_timestamp_is_explicitly_moscow_time():
    source = _source()
    parser = _function(source, "moexIntradayTimestamp")
    result = _node(
        f"""
{parser}
const timestamp = moexIntradayTimestamp('2026-08-07 10:00:00');
console.log(JSON.stringify(new Date(timestamp * 1000).toISOString()));
"""
    )

    assert result == "2026-08-07T07:00:00.000Z"


def test_index_dialog_has_one_day_and_one_month_controls():
    html = HTML.read_text(encoding="utf-8")

    assert 'data-period="1d">1Д</button>' in html
    assert 'data-period="22">1М</button>' in html


def test_intraday_market_support_is_explicit_and_mcftr_is_not_faked():
    source = _source()
    spec = source[source.index("const MARKET_INTRADAY_SPEC"):source.index("let MARKET_CHART_REQUEST")]

    assert "IMOEX:" in spec
    assert "RTSI:" in spec
    assert "MCFTR:" not in spec
    assert "!MARKET_INTRADAY_SPEC[item.id]" in _function(source, "renderMarketChartDialog")


def test_shared_collector_selects_latest_session_and_official_candles():
    source = _source()
    collector = _function(source, "fetchMoexIntradayCandles")

    assert "iss.moex.com/iss/engines/" in collector
    assert "interval=10" in collector
    assert "begin,open,high,low,close,volume,value" in collector
    assert "rows.filter" in collector and "slice(0, 10) === last" in collector
    assert "close*volume" not in collector.replace(" ", "").lower()


def test_intraday_does_not_mix_daily_overlays_or_fake_index_volume():
    source = _source()
    draw = _function(source, "drawMarketChart")

    assert "timeVisible: intraday" in draw
    assert "const enabled = intraday ? new Set()" in draw
    assert "!intraday && isNum(summary.low20)" in draw
    assert "!intraday && isNum(summary.high20)" in draw
    assert "addHistogramSeries" not in draw, "indices have no synthetic traded volume"
    assert "intraday ? '' : marketPositionsTooltipHTML" in draw


def test_stock_volume_has_a_separate_labeled_scale_pane():
    source = _source()
    css = CSS.read_text(encoding="utf-8")
    scale = _function(source, "scVolScaleDraw")

    assert "ОБОРОТ ·" in scale and "₽" in scale
    assert "sc-plot::before" in css
    assert "sc-volscale::before" in css
    assert "var(--sc-volume-top, 72%)" in css


def test_stock_intraday_hides_daily_profile_and_position_overlays():
    source = _source()
    render = _function(source, "renderStockChartData")

    assert "profileToggle.hidden = intraday" in render
    assert "futoiToggle.hidden = intraday" in render
    assert "!intraday && SC_PROFILE_ON" in render
    assert "!intraday && SC_FUTOI_ON" in render
