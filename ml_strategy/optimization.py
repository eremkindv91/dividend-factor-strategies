from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from .config import StrategyConfig


@dataclass
class PortfolioResult:
    method: str
    theoretical_weights: pd.Series
    executable_weights: pd.Series
    shares: pd.Series
    cash_weight: float
    turnover: float
    estimated_cost_rub: float
    annualized_volatility: float | None
    beta: float | None
    fallback_reason: str | None


def _cluster_variance(covariance: pd.DataFrame, members: list[str]) -> float:
    block = covariance.loc[members, members]
    diagonal = np.diag(block.to_numpy())
    inverse = np.divide(1.0, diagonal, out=np.zeros_like(diagonal), where=diagonal > 0)
    weights = inverse / inverse.sum() if inverse.sum() else np.full(len(members), 1 / len(members))
    return float(weights @ block.to_numpy() @ weights)


def hrp_weights(returns: pd.DataFrame) -> pd.Series:
    clean = returns.dropna(axis=1, thresh=max(20, int(len(returns) * 0.7))).fillna(0.0)
    if clean.shape[1] < 2:
        return pd.Series(1.0, index=clean.columns)
    covariance = clean.cov() * 252
    correlation = clean.corr().clip(-1, 1).fillna(0)
    distance = np.sqrt((1 - correlation) / 2)
    order = leaves_list(linkage(squareform(distance.to_numpy(), checks=False), method="single"))
    ordered = list(clean.columns[order])
    weights = pd.Series(1.0, index=ordered)
    clusters = [ordered]
    while clusters:
        next_clusters: list[list[str]] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            midpoint = len(cluster) // 2
            left, right = cluster[:midpoint], cluster[midpoint:]
            left_var, right_var = _cluster_variance(covariance, left), _cluster_variance(covariance, right)
            alpha = 1 - left_var / (left_var + right_var) if left_var + right_var > 0 else 0.5
            weights[left] *= alpha
            weights[right] *= 1 - alpha
            next_clusters.extend((left, right))
        clusters = next_clusters
    return weights.reindex(clean.columns).fillna(0)


def _constraint_adjust(
    weights: pd.Series,
    sectors: pd.Series,
    config: StrategyConfig,
    iterations: int = 40,
) -> pd.Series:
    weights = weights.clip(lower=0).astype(float)
    if weights.sum() <= 0:
        weights[:] = 1.0
    weights /= weights.sum()
    for _ in range(iterations):
        weights = weights.clip(upper=config.max_weight)
        for sector in sectors.dropna().unique():
            names = sectors[sectors == sector].index.intersection(weights.index)
            total = weights.loc[names].sum()
            if total > config.sector_cap:
                weights.loc[names] *= config.sector_cap / total
        top = weights.nlargest(min(5, len(weights))).index
        if weights.loc[top].sum() > config.top5_cap:
            weights.loc[top] *= config.top5_cap / weights.loc[top].sum()
        residual = 1.0 - weights.sum()
        if residual <= 1e-10:
            break
        capacity = (config.max_weight - weights).clip(lower=0)
        if capacity.sum() <= 1e-10:
            break
        addition = residual * capacity / capacity.sum()
        weights += np.minimum(addition, capacity)
    return weights / weights.sum() if weights.sum() else weights


def constrained_max_sharpe(
    expected_returns: pd.Series,
    covariance: pd.DataFrame,
    sectors: pd.Series,
    config: StrategyConfig,
) -> pd.Series:
    names = list(expected_returns.index)
    cov = covariance.loc[names, names].fillna(0).to_numpy()
    mu = expected_returns.to_numpy()

    def objective(weights: np.ndarray) -> float:
        risk = float(np.sqrt(max(weights @ cov @ weights, 1e-12)))
        return -float(weights @ mu) / risk

    constraints: list[dict] = [{"type": "eq", "fun": lambda w: float(w.sum() - 1)}]
    for sector in sectors.reindex(names).dropna().unique():
        mask = (sectors.reindex(names).to_numpy() == sector).astype(float)
        constraints.append({"type": "ineq", "fun": lambda w, m=mask: float(config.sector_cap - w @ m)})
    result = minimize(
        objective,
        np.full(len(names), 1 / len(names)),
        method="SLSQP",
        bounds=[(0, config.max_weight)] * len(names),
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-9},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise ValueError(f"max-sharpe failed: {result.message}")
    return pd.Series(result.x, index=names)


def minimum_variance(
    covariance: pd.DataFrame,
    sectors: pd.Series,
    config: StrategyConfig,
) -> pd.Series:
    names = list(covariance.columns)
    cov = covariance.to_numpy()
    constraints: list[dict] = [{"type": "eq", "fun": lambda w: float(w.sum() - 1)}]
    for sector in sectors.reindex(names).dropna().unique():
        mask = (sectors.reindex(names).to_numpy() == sector).astype(float)
        constraints.append({"type": "ineq", "fun": lambda w, m=mask: float(config.sector_cap - w @ m)})
    result = minimize(
        lambda w: float(w @ cov @ w),
        np.full(len(names), 1 / len(names)),
        method="SLSQP",
        bounds=[(0, config.max_weight)] * len(names),
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-10},
    )
    if not result.success:
        raise ValueError(f"minimum-variance failed: {result.message}")
    return pd.Series(result.x, index=names)


def _turnover_limit(target: pd.Series, previous: pd.Series, cap: float) -> tuple[pd.Series, float]:
    universe = target.index.union(previous[previous > 0].index)
    target = target.reindex(universe).fillna(0)
    previous = previous.reindex(universe).fillna(0)
    gross = float((target - previous).abs().sum())
    if gross <= cap or gross == 0:
        return target, gross
    blended = previous + (target - previous) * (cap / gross)
    return blended.clip(lower=0), cap


def build_portfolio(
    forecasts: pd.Series,
    returns: pd.DataFrame,
    metadata: pd.DataFrame,
    previous_weights: pd.Series,
    config: StrategyConfig,
    prefer_max_sharpe: bool,
) -> PortfolioResult:
    all_returns = returns
    ranked = forecasts.sort_values(ascending=False).head(config.holdings)
    names = ranked.index.intersection(returns.columns)
    ranked = ranked.reindex(names).dropna()
    if len(ranked) < 2:
        raise ValueError("fewer than two investable forecast rows")
    returns = returns.reindex(columns=ranked.index).tail(config.feature_history)
    covariance = returns.cov(min_periods=60).fillna(0) * 252
    sectors = metadata["sector"].reindex(ranked.index).fillna("Не определён")
    fallback_reason = None
    method = "HRP"
    if prefer_max_sharpe:
        try:
            theoretical = constrained_max_sharpe(
                ranked.clip(-0.25, 0.25) * config.forecast_shrinkage,
                covariance,
                sectors,
                config,
            )
            method = "constrained_max_sharpe"
        except ValueError as exc:
            fallback_reason = str(exc)
            theoretical = hrp_weights(returns)
    else:
        try:
            theoretical = hrp_weights(returns)
        except Exception as exc:  # noqa: BLE001
            fallback_reason = str(exc)
            theoretical = pd.Series(1 / len(ranked), index=ranked.index)
            method = "equal_weight_fallback"
    theoretical = _constraint_adjust(theoretical.reindex(ranked.index).fillna(0), sectors, config)
    actionable_previous = previous_weights.reindex(metadata.index).dropna()
    theoretical, turnover = _turnover_limit(
        theoretical,
        actionable_previous,
        config.turnover_cap,
    )
    if theoretical.sum() > 1:
        theoretical /= theoretical.sum()
    execution_returns = all_returns.reindex(columns=theoretical.index).tail(config.feature_history)
    execution_covariance = execution_returns.cov(min_periods=60).fillna(0) * 252
    preliminary_volatility = float(
        np.sqrt(
            max(
                0.0,
                theoretical.reindex(execution_covariance.index).fillna(0).to_numpy()
                @ execution_covariance.to_numpy()
                @ theoretical.reindex(execution_covariance.index).fillna(0).to_numpy(),
            )
        )
    )
    beta_values = metadata.get("beta_120d", pd.Series(dtype=float)).reindex(theoretical.index).fillna(0)
    preliminary_beta = float((theoretical * beta_values).sum())
    risk_scale = 1.0
    if preliminary_volatility > config.volatility_cap > 0:
        risk_scale = min(risk_scale, config.volatility_cap / preliminary_volatility)
    if abs(preliminary_beta) > config.beta_cap > 0:
        risk_scale = min(risk_scale, config.beta_cap / abs(preliminary_beta))
    theoretical *= risk_scale
    theoretical[theoretical < config.min_weight] = 0.0

    prices = metadata["close"].reindex(theoretical.index).astype(float)
    lots = metadata.get("lot_size", pd.Series(1, index=metadata.index)).reindex(theoretical.index).fillna(1).astype(int)
    adv = metadata["adv_20d"].reindex(theoretical.index).astype(float)
    max_trade_value = adv * config.liquidity_participation
    target_value = theoretical * config.capital_rub
    previous_value = previous_weights.reindex(theoretical.index).fillna(0) * config.capital_rub
    bounded_value = previous_value + (target_value - previous_value).clip(
        lower=-max_trade_value, upper=max_trade_value
    )
    lot_value = prices * lots
    lot_counts = np.floor(bounded_value.clip(lower=0) / lot_value.replace(0, np.nan)).fillna(0)
    shares = (lot_counts * lots).astype(int)
    executable_value = shares * prices
    executable_weights = executable_value / config.capital_rub
    cash_weight = max(0.0, 1.0 - float(executable_weights.sum()))
    executed_turnover = float(
        (executable_weights - previous_weights.reindex(executable_weights.index).fillna(0)).abs().sum()
    )
    estimated_cost = executed_turnover * config.capital_rub * config.one_way_cost_bps / 10_000
    portfolio_variance = float(
        executable_weights.reindex(execution_covariance.index).fillna(0).to_numpy()
        @ execution_covariance.to_numpy()
        @ executable_weights.reindex(execution_covariance.index).fillna(0).to_numpy()
    )
    beta = metadata.get("beta_120d", pd.Series(dtype=float)).reindex(executable_weights.index)
    portfolio_beta = float((executable_weights * beta.fillna(0)).sum()) if not beta.empty else None
    return PortfolioResult(
        method=method,
        theoretical_weights=theoretical,
        executable_weights=executable_weights,
        shares=shares,
        cash_weight=cash_weight,
        turnover=executed_turnover,
        estimated_cost_rub=estimated_cost,
        annualized_volatility=float(np.sqrt(max(0, portfolio_variance))),
        beta=portfolio_beta,
        fallback_reason=fallback_reason,
    )
