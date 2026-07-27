from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import MarketData

FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "return_120d",
    "return_252d",
    "momentum_12_1",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_60d",
    "beta_120d",
    "correlation_120d",
    "drawdown_252d",
    "distance_to_high_252d",
    "adv_20d",
    "zero_volume_ratio_20d",
    "amihud_20d",
    "volume_trend_20_120",
    "mcftr_return_20d",
    "mcftr_volatility_20d",
    "market_above_sma200",
    "imoex_return_20d",
    "rgbi_return_20d",
    "usdrub_return_20d",
    "key_rate_pct",
]


def _total_return(close: pd.DataFrame, dividends: dict[str, list[dict]]) -> pd.DataFrame:
    returns = close.pct_change(fill_method=None)
    for ticker in returns:
        rows = dividends.get(ticker, [])
        if not rows:
            continue
        for row in rows:
            dt = pd.Timestamp(str(row.get("registryclosedate", ""))[:10])
            if dt not in returns.index:
                later = returns.index[returns.index >= dt]
                if later.empty:
                    continue
                dt = later[0]
            loc = returns.index.get_loc(dt)
            if not isinstance(loc, (int, np.integer)) or loc == 0:
                continue
            previous = close[ticker].iloc[loc - 1]
            value = pd.to_numeric(row.get("value"), errors="coerce")
            if pd.notna(previous) and previous > 0 and pd.notna(value) and value > 0:
                base = returns.at[dt, ticker]
                returns.at[dt, ticker] = (0.0 if pd.isna(base) else float(base)) + float(value) / float(previous)
    return returns


def _stack(values: pd.DataFrame, name: str) -> pd.Series:
    return values.rename_axis(index="date", columns="ticker").stack(future_stack=True).rename(name)


def build_feature_panel(data: MarketData, config: StrategyConfig) -> pd.DataFrame:
    dates = data.benchmark.index.intersection(data.close.index)
    close = data.close.reindex(dates)
    value = data.traded_value.reindex(dates)
    volume = data.volume.reindex(dates)
    total_returns = _total_return(close, data.dividends)
    price_returns = close.pct_change(fill_method=None)
    benchmark = data.benchmark.reindex(dates)
    benchmark_returns = benchmark.pct_change(fill_method=None)
    invalid_moves = price_returns.abs() > config.max_abs_daily_return
    total_returns = total_returns.mask(invalid_moves)

    features: dict[str, pd.DataFrame] = {
        "return_1d": price_returns,
        "return_5d": close.div(close.shift(5)).sub(1),
        "return_20d": close.div(close.shift(20)).sub(1),
        "return_60d": close.div(close.shift(60)).sub(1),
        "return_120d": close.div(close.shift(120)).sub(1),
        "return_252d": close.div(close.shift(252)).sub(1),
        "momentum_12_1": close.shift(20).div(close.shift(252)).sub(1),
        "volatility_20d": price_returns.rolling(20, min_periods=15).std().mul(np.sqrt(252)),
        "volatility_60d": price_returns.rolling(60, min_periods=45).std().mul(np.sqrt(252)),
        "downside_volatility_60d": price_returns.clip(upper=0).rolling(60, min_periods=45).std().mul(np.sqrt(252)),
        "drawdown_252d": close.div(close.rolling(252, min_periods=126).max()).sub(1),
        "distance_to_high_252d": close.div(close.rolling(252, min_periods=126).max()).sub(1),
        "adv_20d": value.rolling(20, min_periods=15).mean(),
        "zero_volume_ratio_20d": volume.fillna(0).le(0).rolling(20, min_periods=15).mean(),
        "amihud_20d": price_returns.abs().div(value.replace(0, np.nan)).rolling(20, min_periods=15).median().mul(1e9),
        "volume_trend_20_120": value.rolling(20, min_periods=15).mean().div(
            value.rolling(120, min_periods=60).mean()
        ).sub(1),
    }
    benchmark_variance = benchmark_returns.rolling(120, min_periods=80).var()
    features["beta_120d"] = price_returns.rolling(120, min_periods=80).cov(benchmark_returns).div(
        benchmark_variance, axis=0
    )
    features["correlation_120d"] = price_returns.rolling(120, min_periods=80).corr(benchmark_returns)
    market_return_20 = benchmark.div(benchmark.shift(20)).sub(1)
    market_vol_20 = benchmark_returns.rolling(20, min_periods=15).std().mul(np.sqrt(252))
    market_above_200 = benchmark.gt(benchmark.rolling(200, min_periods=150).mean()).astype(float)
    for name, series in (
        ("mcftr_return_20d", market_return_20),
        ("mcftr_volatility_20d", market_vol_20),
        ("market_above_sma200", market_above_200),
    ):
        features[name] = pd.DataFrame(
            np.repeat(series.to_numpy()[:, None], close.shape[1], axis=1),
            index=close.index,
            columns=close.columns,
        )
    for source_name, feature_name, as_return in (
        ("IMOEX", "imoex_return_20d", True),
        ("RGBI", "rgbi_return_20d", True),
        ("USDRUB", "usdrub_return_20d", True),
        ("KEY_RATE", "key_rate_pct", False),
    ):
        source = data.macro.get(source_name)
        if source is None:
            series = pd.Series(np.nan, index=close.index)
        else:
            series = source.reindex(close.index).ffill()
            series = series.div(series.shift(20)).sub(1) if as_return else series / 100.0
        features[feature_name] = pd.DataFrame(
            np.repeat(series.to_numpy()[:, None], close.shape[1], axis=1),
            index=close.index,
            columns=close.columns,
        )

    frame = pd.concat([_stack(values, name) for name, values in features.items()], axis=1)
    forward_total = total_returns.shift(-1).rolling(config.horizon, min_periods=config.horizon).apply(
        lambda x: float(np.prod(1 + x) - 1), raw=True
    ).shift(-(config.horizon - 1))
    forward_benchmark = benchmark_returns.shift(-1).rolling(
        config.horizon, min_periods=config.horizon
    ).apply(lambda x: float(np.prod(1 + x) - 1), raw=True).shift(-(config.horizon - 1))
    frame["forward_total_return_20d"] = _stack(forward_total, "target")
    aligned_market_target = pd.DataFrame(
        np.repeat(forward_benchmark.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    frame["forward_excess_total_return_20d_vs_mcftr"] = (
        frame["forward_total_return_20d"] - _stack(aligned_market_target, "benchmark_target")
    )
    target_end = pd.Series(dates, index=dates).shift(-config.horizon)
    target_end_frame = pd.DataFrame(
        np.repeat(target_end.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    frame["target_end_date"] = _stack(target_end_frame, "target_end_date")
    frame["close"] = _stack(close, "close")
    frame["adv_20d_raw"] = frame["adv_20d"]
    sectors = {ticker: data.master.get(ticker, {}).get("sector") or "Не определён" for ticker in close.columns}
    frame["sector"] = frame.index.get_level_values("ticker").map(sectors)
    return frame.sort_index()


def eligible_cross_section(panel: pd.DataFrame, as_of: pd.Timestamp, config: StrategyConfig) -> pd.DataFrame:
    try:
        rows = panel.xs(as_of, level="date").copy()
    except KeyError:
        return pd.DataFrame()
    rows = rows[
        (rows["adv_20d"] >= config.min_adv_rub)
        & (rows["zero_volume_ratio_20d"] <= config.max_zero_volume_ratio)
        & rows["close"].notna()
    ]
    rows = rows.dropna(subset=["return_20d", "volatility_60d", "beta_120d"])
    return rows.nlargest(config.max_universe, "adv_20d")
