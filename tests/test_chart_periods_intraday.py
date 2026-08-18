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
    signature = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    assert signature, f"function {name} signature not found"
    start = signature.start()
    body_start = signature.end() - 1
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


def _current_session_helpers(source: str) -> str:
    return "\n".join([
        "const isNum = (value) => typeof value === 'number' && Number.isFinite(value);",
        _function(source, "stockIntradayToDailyRow"),
        _function(source, "mergeStockDailyWithIntraday"),
    ])


def test_stock_current_session_is_aggregated_from_real_intraday_candles():
    source = _source()
    result = _node(f"""
{_current_session_helpers(source)}
const intraday = [
  [1, 270, 273, 269, 272, 100, 1000, null, '2026-08-17 10:00:00'],
  [2, 272, 275, 271, 274, 150, 2500, null, '2026-08-17 10:10:00'],
];
console.log(JSON.stringify(stockIntradayToDailyRow(intraday)));
""")

    assert result == ["2026-08-17", 270, 275, 269, 274, 250, 3500,
                      None, "2026-08-17", "current_session"]


def test_stock_current_session_merge_is_stable_and_never_replaces_official_daily():
    source = _source()
    result = _node(f"""
{_current_session_helpers(source)}
const official = [['2026-08-14', 260, 265, 258, 264, 10, 20, 30]];
const intraday = [[1, 270, 275, 269, 274, 100, 1000, null, '2026-08-17 10:00:00']];
const appended = mergeStockDailyWithIntraday(official, intraday);
const finalized = mergeStockDailyWithIntraday(
  [['2026-08-17', 268, 276, 267, 275, 200, 3000, 40]], intraday
);
const refreshed = mergeStockDailyWithIntraday(appended.rows,
  [[2, 270, 277, 268, 276, 120, 1500, null, '2026-08-17 10:10:00']]);
console.log(JSON.stringify({{
  source: official,
  appended,
  finalized,
  refreshed,
}}));
""")

    assert result["source"] == [["2026-08-14", 260, 265, 258, 264, 10, 20, 30]]
    assert result["appended"]["currentSessionDate"] == "2026-08-17"
    assert len(result["appended"]["rows"]) == 2
    assert result["finalized"]["currentSessionDate"] is None
    assert len(result["finalized"]["rows"]) == 1
    assert result["refreshed"]["rows"][-1][4] == 276
    assert result["refreshed"]["rows"][-1][6] == 1500


def test_daily_stock_chart_revalidates_current_session_without_browser_cache():
    source = _source()
    fetcher = _function(source, "fetchMoexIntradayCandles")
    wire = _function(source, "wireStockChart")

    assert "cache: 'no-store'" in fetcher
    assert "refreshCurrentSession" in wire
    assert "5 * 60 * 1000" in wire
    assert "current_session" in _function(source, "stockOhlcReadout")


def test_stock_moex_requests_have_bounded_transient_retry():
    source = _source()
    for name in ["fetchStockOHLCFromMoex", "fetchMoexIntradayCandles"]:
        function = _function(source, name)
        assert "attempt = 0" in function
        assert "attempt < 1" in function
        assert "step(start, attempt + 1)" in function
        assert "750" in function
        assert "18000" in function
        assert "cache: 'no-store'" in function


def test_daily_stock_chart_prefers_published_foundation_and_keeps_moex_fallback():
    source = _source()
    published = _function(source, "fetchStockPublishedOHLC")
    fetcher = _function(source, "fetchStockOHLC")

    assert "daily/web/" in published
    assert "cache: 'no-store'" in published
    assert "stockPublishedOhlcRows" in published
    assert "coverageLimited" in published
    assert "fetchStockPublishedOHLC" in fetcher
    assert "fetchStockOHLCFromMoex" in fetcher
    assert fetcher.index("fetchStockPublishedOHLC") < fetcher.index("fetchStockOHLCFromMoex")
    assert "!meta.coverageLimited" in fetcher
    assert "if (!publishedError && publishedRows.length)" in fetcher


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
    assert "MCFTR может выходить позже ценового IMOEX" in source
    assert "ряд не достраивается искусственно" in source


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


def test_market_current_session_is_appended_without_recalculating_sma():
    source = _source()
    helpers = "\n".join([
        "const isNum = (value) => typeof value === 'number' && Number.isFinite(value);",
        _function(source, "marketIntradayToDailyRow"),
        _function(source, "mergeMarketDailyWithIntraday"),
    ])
    result = _node(f"""
{helpers}
const official = [['2026-08-17', 2100, 2140, 2080, 2093, 2000, 2100, 2200]];
const live = [
  [1, 2094, 2110, 2090, 2105, 10, 20, null, '2026-08-18 10:00:00'],
  [2, 2105, 2120, 2100, 2115, 15, 25, null, '2026-08-18 10:10:00'],
];
console.log(JSON.stringify(mergeMarketDailyWithIntraday(official, live)));
""")

    assert result["currentSessionDate"] == "2026-08-18"
    assert result["rows"][-1] == ["2026-08-18", 2094, 2120, 2090, 2115,
                                   None, None, None, "2026-08-18", "current_session"]


def test_published_market_current_session_survives_direct_iss_timeout():
    source = _source()
    helpers = "\n".join([
        "const isNum = (value) => typeof value === 'number' && Number.isFinite(value);",
        "const MARKET_CHART_STATE = {period: 30};",
        _function(source, "marketPublishedCurrentSessionRow"),
        _function(source, "marketChartRows"),
    ])
    result = _node(f"""
{helpers}
const item = {{data_last: '2026-08-17', series: [['2026-08-17', 2100, 2140, 2080, 2093]],
  current_session: {{date: '2026-08-18', open: 2094, high: 2120, low: 2090,
    close: 2115, last_candle_at: '2026-08-18 14:50:00'}}}};
console.log(JSON.stringify(marketChartRows(item)));
""")

    assert result[-1] == ["2026-08-18", 2094, 2120, 2090, 2115,
                           None, None, None, "2026-08-18 14:50:00", "current_session"]


def test_direct_intraday_replaces_same_date_published_partial_candle():
    source = _source()
    helpers = "\n".join([
        "const isNum = (value) => typeof value === 'number' && Number.isFinite(value);",
        _function(source, "marketIntradayToDailyRow"),
        _function(source, "mergeMarketDailyWithIntraday"),
    ])
    result = _node(f"""
{helpers}
const published = [
  ['2026-08-17', 2100, 2140, 2080, 2093],
  ['2026-08-18', 2094, 2110, 2090, 2105, null, null, null,
    '2026-08-18 12:00:00', 'current_session'],
];
const direct = [[1, 2094, 2120, 2088, 2115, 10, 20, null, '2026-08-18 15:00:00']];
console.log(JSON.stringify(mergeMarketDailyWithIntraday(published, direct)));
""")

    assert result["currentSessionDate"] == "2026-08-18"
    assert result["rows"][-1][1:5] == [2094, 2120, 2088, 2115]


def test_multiday_market_chart_refreshes_from_real_intraday_but_not_for_mcftr():
    source = _source()
    render = _function(source, "renderMarketChartDialog")
    status = _function(source, "updateDataStatus")

    assert "mergeMarketDailyWithIntraday" in render
    assert "fetchMarketIntraday" in render
    assert "MARKET_INTRADAY_SPEC[item.id]" in render
    assert "5 * 60 * 1000" in render
    assert "Акции · close" in status
    assert "MCFTR · close" in status


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


def _futoi_helpers(source: str) -> str:
    return "\n".join([
        "const isNum = (value) => typeof value === 'number' && Number.isFinite(value);",
        _function(source, "stockVisibleRange"),
        _function(source, "scFutoiVisibleData"),
        _function(source, "scFutoiDomain"),
    ])


def test_futoi_is_clipped_to_the_same_six_month_price_range():
    """TEST A: no position point may escape the visible price window."""
    source = _source()
    result = _node(f"""
{_futoi_helpers(source)}
const range = stockVisibleRange([['2026-02-07'], ['2026-08-07']]);
const data = scFutoiVisibleData({{
  dates: ['2023-06-20', '2026-02-06', '2026-02-07', '2026-05-01', '2026-08-07', '2026-08-08'],
  net: [1, 2, 3, 4, 5, 6],
}}, range);
console.log(JSON.stringify({{ range, dates: data.map((item) => item.time) }}));
""")

    assert result["range"] == {"visibleStart": "2026-02-07", "visibleEnd": "2026-08-07"}
    assert result["dates"] == ["2026-02-07", "2026-05-01", "2026-08-07"]


def test_futoi_max_to_one_month_does_not_keep_stale_observations():
    """TEST B: deriving a shorter view is pure and cannot retain the prior series."""
    source = _source()
    result = _node(f"""
{_futoi_helpers(source)}
const row = {{
  dates: ['2024-01-01', '2026-07-07', '2026-07-20', '2026-08-07'],
  net: [10, 20, 30, 40],
}};
const maximum = scFutoiVisibleData(row, {{ visibleStart: '2024-01-01', visibleEnd: '2026-08-07' }});
const month = scFutoiVisibleData(row, {{ visibleStart: '2026-07-07', visibleEnd: '2026-08-07' }});
console.log(JSON.stringify({{
  maximum: maximum.map((item) => item.time),
  month: month.map((item) => item.time),
  sourceDates: row.dates,
}}));
""")

    assert result["maximum"][0] == "2024-01-01"
    assert result["month"] == ["2026-07-07", "2026-07-20", "2026-08-07"]
    assert result["sourceDates"][0] == "2024-01-01", "helper must not mutate source data"


def test_volume_profile_request_and_levels_change_with_the_range():
    """TEST C: a new range gets a new cache key and independently computed levels."""
    source = _source()
    request = _function(source, "scProfileVisibleRequest")
    compute = _function(source, "scProfileCompute")
    result = _node(f"""
const isNum = (value) => typeof value === 'number' && Number.isFinite(value);
const SC_VALUE_AREA = 0.70;
const scIntradayInterval = (days) => days <= 100 ? 10 : 60;
{request}
{compute}
const rows = [['2026-01-01'], ['2026-02-01'], ['2026-03-01'], ['2026-04-01']];
const shortRequest = scProfileVisibleRequest('SBER', rows, {{ from: 2, to: 3 }});
const longRequest = scProfileVisibleRequest('SBER', rows, {{ from: 0, to: 3 }});
const low = scProfileCompute([{{ low: 90, high: 100, value: 1000 }}], 10);
const high = scProfileCompute([{{ low: 190, high: 200, value: 1000 }}], 10);
console.log(JSON.stringify({{
  keys: [shortRequest.key, longRequest.key],
  low: [low.poc, low.vah, low.val],
  high: [high.poc, high.vah, high.val],
}}));
""")

    assert result["keys"][0] != result["keys"][1]
    assert result["low"] != result["high"]
    refresh = _function(source, "scProfileRefresh")
    key_pos = refresh.index("container._scProfileKey = key")
    clear_pos = refresh.index("container._scProfile = null", key_pos)
    fetch_pos = refresh.index("scFetchIntraday", clear_pos)
    assert key_pos < clear_pos < fetch_pos, "old POC/VAH/VAL must disappear before the new request"


def test_futoi_domain_uses_only_visible_observations():
    """TEST D: hidden historical extremes cannot flatten the current overlay."""
    source = _source()
    result = _node(f"""
{_futoi_helpers(source)}
const row = {{
  dates: ['2024-01-01', '2026-07-01', '2026-08-01'],
  net: [-1000000, -120, 340],
}};
const visible = scFutoiVisibleData(row, {{ visibleStart: '2026-07-01', visibleEnd: '2026-08-07' }});
console.log(JSON.stringify(scFutoiDomain(visible)));
""")

    assert result == {"min": -120, "max": 340}


def test_futoi_draws_fresh_delayed_and_truthful_last_good_states():
    source = _source()
    drawable = _function(source, "futoiDrawable")
    result = _node(f"""
{drawable}
const row = (status) => ({{ status, dates: ['2026-08-10'], net: [20] }});
console.log(JSON.stringify({{
  fresh: futoiDrawable(row('fresh')),
  delayed: futoiDrawable(row('delayed_by_exchange')),
  stale: futoiDrawable(row('stale')),
  unavailable: futoiDrawable(row('unavailable')),
}}));
""")

    assert result == {"fresh": True, "delayed": True, "stale": True, "unavailable": False}


def test_futoi_uses_one_authoritative_observation_date_and_revalidates_cache():
    source = _source()
    note = _function(source, "scFutoiNoteHTML")
    loader = _function(source, "loadFutoi")
    market = _function(source, "marketDrawPositions")

    assert "row.latest_observation_date || s.as_of" in note
    assert "futoiFreshnessText(row)" in note
    assert "cache: 'no-store'" in loader
    assert "insightLatest !== rowLatest" in market, "old commentary must not replace a newer series"


def test_futoi_does_not_fill_the_start_from_a_future_observation():
    """TEST E: missing start data stays missing; the line starts at the first real point."""
    source = _source()
    result = _node(f"""
{_futoi_helpers(source)}
const data = scFutoiVisibleData({{
  dates: ['2026-01-05', '2026-01-15', '2026-01-20'],
  net: [10, 15, 20],
}}, {{ visibleStart: '2026-01-10', visibleEnd: '2026-01-31' }});
console.log(JSON.stringify(data));
""")

    assert result == [
        {"time": "2026-01-15", "value": 15},
        {"time": "2026-01-20", "value": 20},
    ]
