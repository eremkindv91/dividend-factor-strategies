from news.collectors.fetch_markets import market_line_from_closes, moex_line_from_row, pct_from_absolute_change


def test_moex_index_line_uses_current_value_and_absolute_change():
    row = {
        "CURRENTVALUE": 2242.84,
        "LASTCHANGE": -13.22,
        "SYSTIME": "2026-07-03 19:00:11",
    }

    assert moex_line_from_row("IMOEX", row) == "IMOEX | 2242.84 | -0.59% | 2026-07-03 19:00:11"


def test_moex_currency_zero_percent_placeholder_is_recomputed_from_change():
    row = {
        "LAST": 78.32,
        "LASTCHANGEPRCNT": 0,
        "CHANGE": 0.08,
        "SYSTIME": "2026-07-03 23:55:00",
    }

    assert moex_line_from_row("USD/RUB", row) == "USD/RUB | 78.32 | +0.10% | 2026-07-03 23:55:00"


def test_moex_missing_comparison_data_does_not_emit_zero_placeholder():
    row = {
        "LAST": 78.32,
        "LASTCHANGEPRCNT": 0,
        "SYSTIME": "2026-07-03 23:55:00",
    }

    assert moex_line_from_row("USD/RUB", row) == "USD/RUB | 78.32 |  | 2026-07-03 23:55:00"


def test_pct_from_absolute_change_handles_missing_or_zero_previous_value():
    assert pct_from_absolute_change(100, None) == ""
    assert pct_from_absolute_change(0, 0) == ""
    assert pct_from_absolute_change(78.32, 0.08) == "+0.10%"


def test_market_line_from_closes_requires_real_comparison_point():
    assert market_line_from_closes("S&P 500", [("2026-07-04", 7483.23)]) == ""
    assert market_line_from_closes("S&P 500", [("2026-07-03", 7483.23), ("2026-07-04", 7483.24)]) == (
        "S&P 500 | 7483.24 | +0.0001% | 2026-07-04"
    )
