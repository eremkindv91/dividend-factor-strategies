from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.build_forward_yield import build_rows
from src.strategies.forward_repricing import (
    BacktestConfig,
    build_backtest_from_files,
    run_forward_repricing_backtest,
)


ROOT = Path(__file__).resolve().parents[1]


def _prices_from_returns(dates: pd.DatetimeIndex, returns: list[float]) -> pd.Series:
    values = [100.0]
    for value in returns:
        values.append(values[-1] * (1.0 + value))
    return pd.Series(values, index=dates)


def test_backtest_uses_previous_financial_year_and_keeps_empty_slots_in_cash():
    panel = pd.DataFrame(
        [
            {"ticker": "AAA", "year": 2019, "div_yield_pct": 12.0, "deposit_rate_pct": 5.0, "market_cap_mln": 20_000},
            {"ticker": "BBB", "year": 2019, "div_yield_pct": 6.0, "deposit_rate_pct": 5.0, "market_cap_mln": 20_000},
            {"ticker": "AAA", "year": 2020, "div_yield_pct": 6.0, "deposit_rate_pct": 5.0, "market_cap_mln": 20_000},
            {"ticker": "BBB", "year": 2020, "div_yield_pct": 14.0, "deposit_rate_pct": 5.0, "market_cap_mln": 20_000},
        ]
    )
    dates = pd.date_range("2020-04-30", "2022-04-30", freq="ME")
    aaa_returns = [0.03, -0.02, 0.01, 0.02, -0.01, 0.01] * 4
    bbb_returns = [-0.01, 0.02, 0.01, -0.02, 0.03, 0.00] * 4
    prices = pd.DataFrame(
        {
            "AAA": _prices_from_returns(dates, aaa_returns),
            "BBB": _prices_from_returns(dates, bbb_returns),
        }
    )
    benchmark = pd.Series(
        np.resize([0.01, -0.015, 0.008, 0.012], 24),
        index=pd.date_range("2020-05-31", "2022-04-30", freq="ME"),
    )
    config = BacktestConfig(
        start_year=2020,
        end_year=2021,
        target_holdings=2,
        entry_spread_pct=3.0,
    )

    result = run_forward_repricing_backtest(panel, prices, benchmark, config)

    assert result["point_in_time"] is False
    assert result["rebalances"][0]["signal_year"] == 2019
    assert result["rebalances"][0]["holdings"] == ["AAA"]
    assert result["rebalances"][0]["cash_weight"] == 0.5
    assert result["rebalances"][1]["signal_year"] == 2020
    assert result["rebalances"][1]["holdings"] == ["BBB"]
    assert result["metrics"]["months"] == 24
    assert result["metrics"]["average_holdings"] == 1.0
    assert result["metrics"]["average_cash_weight"] == 0.5
    assert result["parameters"]["entry_spread_pct"] == 3.0
    assert len(result["series"]) == 24


def test_current_repricing_score_is_gross_and_does_not_depend_on_div2_placeholder():
    universe = {
        "AAA": {
            "price": 100.0,
            "div1_model": 10.0,
            "name": "AAA",
            "sector": "Test",
            "status": "ok",
            "mcap": 100_000,
            "yield_expected": 10.0,
        }
    }
    first = build_rows(
        universe,
        {"AAA": {"div_1": 10.0, "div_2": 10.0, "note": "placeholder"}},
        0.08,
    )[0]
    second = build_rows(
        universe,
        {"AAA": {"div_1": 10.0, "div_2": 30.0, "note": "changed"}},
        0.08,
    )[0]

    assert first["gross_yield1"] == 0.1
    assert first["gross_spread"] == 0.02
    assert first["gross_spread"] == second["gross_spread"]
    assert first["yield2"] != second["yield2"]


def test_div2_placeholder_never_creates_two_year_signal():
    universe = {
        "AAA": {
            "price": 100.0,
            "div1_model": 20.0,
            "name": "AAA",
            "sector": "Test",
            "status": "ok",
            "mcap": 100_000,
            "yield_expected": 20.0,
            "yield_if_paid": 20.0,
        }
    }
    row = build_rows(
        universe,
        {"AAA": {"div_1": 20.0, "div_2": 20.0, "forecast_status": "placeholder", "note": "заглушка"}},
        0.10,
    )[0]
    assert row["div2"] is None
    assert row["yield2"] is None
    assert row["forecast_status"] == "insufficient_forecast"
    assert row["signal"] == "Недостаточно данных"
    assert row["note"] == "Независимый прогноз Div2 отсутствует"
    assert row["expected_net_yield"] == pytest.approx(0.174)
    assert row["matched_rfr"] == pytest.approx(0.087)


def test_repository_data_builds_a_nonempty_reproducible_backtest():
    result = build_backtest_from_files(
        ROOT / "data" / "panels_final" / "panel_russia_final.csv",
        ROOT / "data" / "russian_stocks_adjusted.xlsx",
        ROOT / "data" / "индекс_мосбиржи_полн_дох.csv",
    )

    assert result["status"] == "research_lagged_baseline_not_point_in_time"
    assert result["point_in_time"] is False
    assert result["metrics"]["months"] >= 120
    assert result["metrics"]["active_rebalances"] >= 8
    assert result["metrics"]["minimum_return_coverage"] >= 0.9
    assert result["benchmark"]["name"] == "MCFTR"
    assert result["limitations"]
