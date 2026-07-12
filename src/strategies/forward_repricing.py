"""Lagged research backtest for the dividend repricing strategy.

The repository does not yet have publication dates for the historical
fundamental panel. The backtest therefore follows the existing factor-study
calendar (financial year t, rebalance in May t+1) and labels the result as a
lagged research baseline, not as a strict point-in-time reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    start_year: int = 2012
    end_year: int = 2025
    rebalance_month: int = 5
    target_holdings: int = 10
    min_market_cap_mln: float = 5_000.0
    max_trailing_yield_pct: float = 30.0
    entry_spread_pct: float = 3.0
    transaction_cost_bps: float = 50.0


REQUIRED_PANEL_COLUMNS = {
    "ticker",
    "year",
    "div_yield_pct",
    "deposit_rate_pct",
    "market_cap_mln",
}


def load_adjusted_prices(path: str | Path) -> pd.DataFrame:
    prices = pd.read_excel(path, index_col=0, engine="openpyxl")
    prices.index = pd.to_datetime(prices.index, errors="coerce")
    prices = prices.loc[prices.index.notna()].sort_index()
    prices.columns = prices.columns.astype(str).str.strip().str.upper()
    return prices.apply(pd.to_numeric, errors="coerce")


def load_mcftr_returns(path: str | Path) -> pd.Series:
    frame = pd.read_csv(path, sep=";")
    if not {"TRADEDATE", "CLOSE"}.issubset(frame.columns):
        raise ValueError("MCFTR file must contain TRADEDATE and CLOSE")
    dates = pd.to_datetime(frame["TRADEDATE"], errors="coerce")
    closes = pd.to_numeric(
        frame["CLOSE"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    monthly = pd.Series(closes.to_numpy(), index=dates, name="MCFTR").dropna()
    monthly = monthly.loc[monthly.index.notna()].sort_index().resample("ME").last()
    return monthly.pct_change(fill_method=None).dropna()


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _rounded(value: object, digits: int = 6) -> float | None:
    return round(float(value), digits) if _finite(value) else None


def _cagr(returns: pd.Series) -> float | None:
    clean = returns.dropna()
    if clean.empty or (clean <= -1).any():
        return None
    terminal = float((1.0 + clean).prod())
    return terminal ** (12.0 / len(clean)) - 1.0 if terminal > 0 else None


def _max_drawdown(returns: pd.Series) -> float | None:
    clean = returns.dropna()
    if clean.empty:
        return None
    equity = (1.0 + clean).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def _core_metrics(returns: pd.Series, risk_free: pd.Series) -> dict:
    aligned = pd.concat(
        [returns.rename("returns"), risk_free.rename("risk_free")], axis=1
    ).dropna()
    if len(aligned) < 6:
        raise ValueError("at least six monthly observations are required")
    ret = aligned["returns"]
    excess = ret - aligned["risk_free"]
    volatility = float(ret.std(ddof=1) * math.sqrt(12))
    excess_vol = float(excess.std(ddof=1) * math.sqrt(12))
    annual_excess = float(excess.mean() * 12)
    downside = float(
        math.sqrt(float(np.mean(np.minimum(excess.to_numpy(), 0.0) ** 2)))
        * math.sqrt(12)
    )
    cagr = _cagr(ret)
    max_drawdown = _max_drawdown(ret)
    positive = float(ret.loc[ret > 0].sum())
    negative = abs(float(ret.loc[ret < 0].sum()))
    return {
        "cagr": _rounded(cagr),
        "volatility": _rounded(volatility),
        "sharpe": _rounded(annual_excess / excess_vol if excess_vol > 0 else None),
        "sortino": _rounded(annual_excess / downside if downside > 0 else None),
        "max_drawdown": _rounded(max_drawdown),
        "calmar": _rounded(
            cagr / abs(max_drawdown)
            if cagr is not None and max_drawdown is not None and max_drawdown < 0
            else None
        ),
        "hit_rate": _rounded(float((ret > 0).mean())),
        "profit_factor": _rounded(positive / negative if negative > 0 else None),
    }


def _month_end(year: int, month: int) -> pd.Timestamp:
    return pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)


def _previous_price_date(index: pd.DatetimeIndex, start: pd.Timestamp) -> pd.Timestamp | None:
    previous = index[index < start]
    return previous[-1] if len(previous) else None


def _prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_PANEL_COLUMNS.difference(panel.columns)
    if missing:
        raise ValueError(f"panel is missing columns: {sorted(missing)}")
    out = panel[
        ["ticker", "year", "div_yield_pct", "deposit_rate_pct", "market_cap_mln"]
    ].copy()
    out["ticker"] = out["ticker"].astype(str).str.strip().str.upper()
    for column in ("year", "div_yield_pct", "deposit_rate_pct", "market_cap_mln"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["ticker", "year"]).drop_duplicates(
        ["ticker", "year"], keep="last"
    )
    out["year"] = out["year"].astype(int)
    out["trading_year"] = out["year"] + 1
    out["spread_pct"] = out["div_yield_pct"] - out["deposit_rate_pct"]
    return out


def _select_holdings(
    panel: pd.DataFrame,
    prices: pd.DataFrame,
    trading_year: int,
    previous_price_date: pd.Timestamp,
    config: BacktestConfig,
) -> pd.DataFrame:
    universe = panel.loc[
        (panel["trading_year"] == trading_year)
        & (panel["market_cap_mln"] >= config.min_market_cap_mln)
        & panel["div_yield_pct"].gt(0)
        & panel["div_yield_pct"].le(config.max_trailing_yield_pct)
        & panel["spread_pct"].ge(config.entry_spread_pct)
    ].copy()
    if universe.empty:
        return universe
    available = {
        ticker
        for ticker in universe["ticker"]
        if ticker in prices.columns and _finite(prices.at[previous_price_date, ticker])
    }
    universe = universe.loc[universe["ticker"].isin(available)]
    return (
        universe.sort_values(
            ["spread_pct", "div_yield_pct", "ticker"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .head(config.target_holdings)
        .reset_index(drop=True)
    )


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    previous_cash = 1.0 - sum(previous.values())
    current_cash = 1.0 - sum(current.values())
    securities = set(previous).union(current)
    gross_change = sum(abs(current.get(t, 0.0) - previous.get(t, 0.0)) for t in securities)
    return 0.5 * (gross_change + abs(current_cash - previous_cash))


def run_forward_repricing_backtest(
    panel: pd.DataFrame,
    adjusted_prices: pd.DataFrame,
    benchmark_returns: pd.Series,
    config: BacktestConfig | None = None,
) -> dict:
    """Build an annual Top-10 lagged dividend-spread research backtest."""

    cfg = config or BacktestConfig()
    if cfg.target_holdings < 1:
        raise ValueError("target_holdings must be positive")
    prepared = _prepare_panel(panel)
    prices = adjusted_prices.copy()
    prices.index = pd.to_datetime(prices.index, errors="coerce")
    prices = prices.loc[prices.index.notna()].sort_index()
    prices.columns = prices.columns.astype(str).str.strip().str.upper()
    price_returns = prices.pct_change(fill_method=None)

    benchmark = benchmark_returns.copy()
    benchmark.index = pd.to_datetime(benchmark.index, errors="coerce")
    benchmark = benchmark.loc[benchmark.index.notna()].sort_index()
    benchmark.index = benchmark.index + pd.offsets.MonthEnd(0)
    benchmark = benchmark.groupby(level=0).last()
    last_observation = min(price_returns.index.max(), benchmark.dropna().index.max())

    monthly_rows: list[dict] = []
    rebalance_rows: list[dict] = []
    previous_weights: dict[str, float] = {}
    active_sample = False

    for trading_year in range(cfg.start_year, cfg.end_year + 1):
        rebalance_date = _month_end(trading_year, cfg.rebalance_month)
        start = pd.Timestamp(trading_year, cfg.rebalance_month, 1)
        previous_date = _previous_price_date(prices.index, start)
        if previous_date is None or rebalance_date > last_observation:
            continue

        selected = _select_holdings(prepared, prices, trading_year, previous_date, cfg)
        if not selected.empty:
            active_sample = True
        if not active_sample:
            continue

        slot_weight = 1.0 / cfg.target_holdings
        weights = {ticker: slot_weight for ticker in selected["ticker"]}
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        turnover = _turnover(previous_weights, weights)
        rate_rows = prepared.loc[prepared["trading_year"] == trading_year, "deposit_rate_pct"]
        annual_rfr = float(rate_rows.median()) / 100.0 if rate_rows.notna().any() else 0.0
        monthly_rfr = (1.0 + max(annual_rfr, -0.99)) ** (1.0 / 12.0) - 1.0

        rebalance_rows.append(
            {
                "rebalance_date": rebalance_date.date().isoformat(),
                "signal_year": trading_year - 1,
                "holdings": selected["ticker"].tolist(),
                "holdings_count": int(len(selected)),
                "cash_weight": _rounded(cash_weight),
                "turnover": _rounded(turnover),
                "median_trailing_yield_pct": _rounded(selected["div_yield_pct"].median()),
                "median_spread_pct": _rounded(selected["spread_pct"].median()),
            }
        )

        for month_offset in range(12):
            date = start + pd.DateOffset(months=month_offset) + pd.offsets.MonthEnd(0)
            if date > last_observation:
                break
            benchmark_return = benchmark.get(date)
            if not _finite(benchmark_return):
                continue
            stock_return = 0.0
            covered_weight = cash_weight
            if date in price_returns.index:
                row = price_returns.loc[date]
                for ticker, weight in weights.items():
                    value = row.get(ticker)
                    if _finite(value):
                        stock_return += weight * float(value)
                        covered_weight += weight
            strategy_return = stock_return + cash_weight * monthly_rfr
            if month_offset == 0:
                strategy_return -= turnover * cfg.transaction_cost_bps / 10_000.0
            monthly_rows.append(
                {
                    "date": date,
                    "strategy": strategy_return,
                    "benchmark": float(benchmark_return),
                    "risk_free": monthly_rfr,
                    "covered_weight": covered_weight,
                }
            )
        previous_weights = weights

    if not monthly_rows:
        raise ValueError("no eligible historical rebalances")

    monthly = pd.DataFrame(monthly_rows).set_index("date").sort_index()
    monthly = monthly.loc[~monthly.index.duplicated(keep="last")]
    strategy = monthly["strategy"]
    benchmark_aligned = monthly["benchmark"]
    risk_free = monthly["risk_free"]

    metrics = _core_metrics(strategy, risk_free)
    benchmark_metrics = _core_metrics(benchmark_aligned, risk_free)
    rfr_cagr = _cagr(risk_free)
    metrics["excess_return_vs_rfr"] = _rounded(
        metrics["cagr"] - rfr_cagr
        if metrics["cagr"] is not None and rfr_cagr is not None
        else None
    )
    metrics["excess_return_vs_mcftr"] = _rounded(
        metrics["cagr"] - benchmark_metrics["cagr"]
        if metrics["cagr"] is not None and benchmark_metrics["cagr"] is not None
        else None
    )

    regression = pd.concat(
        [strategy, benchmark_aligned, risk_free],
        axis=1,
        keys=["strategy", "benchmark", "risk_free"],
    ).dropna()
    strategy_excess = regression["strategy"] - regression["risk_free"]
    benchmark_excess = regression["benchmark"] - regression["risk_free"]
    benchmark_variance = float(benchmark_excess.var(ddof=1))
    beta = (
        float(strategy_excess.cov(benchmark_excess)) / benchmark_variance
        if benchmark_variance > 0
        else None
    )
    alpha = (
        (float(strategy_excess.mean()) - beta * float(benchmark_excess.mean())) * 12
        if beta is not None
        else None
    )
    down_months = regression.loc[regression["benchmark"] < 0]
    downside_capture = (
        float(down_months["strategy"].mean() / down_months["benchmark"].mean())
        if not down_months.empty and abs(float(down_months["benchmark"].mean())) > 1e-12
        else None
    )
    metrics.update(
        {
            "alpha_vs_mcftr": _rounded(alpha),
            "beta_vs_mcftr": _rounded(beta),
            "downside_capture": _rounded(downside_capture),
            "average_turnover": _rounded(
                float(np.mean([row["turnover"] for row in rebalance_rows]))
            ),
            "holding_period_months": 12,
            "rebalances": len(rebalance_rows),
            "active_rebalances": sum(row["holdings_count"] > 0 for row in rebalance_rows),
            "average_holdings": _rounded(
                float(np.mean([row["holdings_count"] for row in rebalance_rows]))
            ),
            "average_cash_weight": _rounded(
                float(np.mean([row["cash_weight"] for row in rebalance_rows]))
            ),
            "minimum_return_coverage": _rounded(float(monthly["covered_weight"].min())),
            "months": int(len(monthly)),
        }
    )

    strategy_equity = (1.0 + strategy).cumprod() * 100.0
    benchmark_equity = (1.0 + benchmark_aligned).cumprod() * 100.0
    rfr_equity = (1.0 + risk_free).cumprod() * 100.0
    series = [
        {
            "date": date.date().isoformat(),
            "strategy": _rounded(strategy_equity.loc[date], 4),
            "mcftr": _rounded(benchmark_equity.loc[date], 4),
            "rfr": _rounded(rfr_equity.loc[date], 4),
        }
        for date in monthly.index
    ]

    return {
        "status": "research_lagged_baseline_not_point_in_time",
        "point_in_time": False,
        "name": "Дивидендная переоценка (формализованный baseline)",
        "period": {
            "start": monthly.index.min().date().isoformat(),
            "end": monthly.index.max().date().isoformat(),
            "months": int(len(monthly)),
        },
        "metrics": metrics,
        "benchmark": {"name": "MCFTR", **benchmark_metrics},
        "risk_free": {"name": "Ставка депозитов ЦБ, gross", "cagr": _rounded(rfr_cagr)},
        "parameters": {
            "target_holdings": cfg.target_holdings,
            "entry_spread_pct": cfg.entry_spread_pct,
            "min_market_cap_mln": cfg.min_market_cap_mln,
            "transaction_cost_bps": cfg.transaction_cost_bps,
            "rebalance_month": cfg.rebalance_month,
        },
        "methodology": {
            "signal": "Дивдоходность отчётного года минус ставка депозитов того же года",
            "lag": "Отчётный год t используется с ребаланса в мае t+1",
            "selection": (
                f"Top-{cfg.target_holdings}; spread >= {cfg.entry_spread_pct:g} п.п.; "
                f"капитализация >= {cfg.min_market_cap_mln / 1000:g} млрд ₽"
            ),
            "weighting": (
                f"{100 / cfg.target_holdings:g}% на слот; незаполненные слоты остаются в cash/RFR"
            ),
            "rebalance": "Ежегодно в мае",
            "transaction_cost_bps": cfg.transaction_cost_bps,
            "returns": "Backward-adjusted MOEX total-return prices",
        },
        "limitations": [
            "В панели нет publication_date/available_from: это лагированный research baseline, а не строгий point-in-time backtest.",
            "Это собственная формализация публичной идеи, а не реконструкция закрытого алгоритма Марламова/ALЁNKA.",
            "Исторический сигнал использует trailing dividend yield; архивных форвардных прогнозов в репозитории нет.",
            "Делистинги и сложные corporate actions ограничены качеством adjusted-price ряда и security master.",
        ],
        "rebalances": rebalance_rows,
        "series": series,
    }


def build_backtest_from_files(
    panel_path: str | Path,
    prices_path: str | Path,
    benchmark_path: str | Path,
    config: BacktestConfig | None = None,
) -> dict:
    panel = pd.read_csv(panel_path, low_memory=False)
    prices = load_adjusted_prices(prices_path)
    benchmark = load_mcftr_returns(benchmark_path)
    return run_forward_repricing_backtest(panel, prices, benchmark, config=config)
