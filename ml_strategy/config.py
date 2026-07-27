from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StrategyConfig:
    horizon: int = 20
    min_history: int = 252
    feature_history: int = 252
    min_training_rows: int = 500
    min_cross_section: int = 12
    max_universe: int = 60
    holdings: int = 12
    min_adv_rub: float = 10_000_000.0
    max_zero_volume_ratio: float = 0.10
    max_abs_daily_return: float = 0.60
    max_weight: float = 0.15
    min_weight: float = 0.01
    sector_cap: float = 0.35
    top5_cap: float = 0.65
    turnover_cap: float = 0.40
    liquidity_participation: float = 0.02
    volatility_cap: float = 0.35
    beta_cap: float = 1.30
    forecast_shrinkage: float = 0.35
    capital_rub: float = 1_000_000.0
    commission_bps: float = 5.0
    spread_bps: float = 10.0
    slippage_bps: float = 10.0
    market_impact_bps: float = 5.0
    execution_lag_sessions: int = 1
    stale_calendar_days: int = 7
    rebalance_frequency_sessions: int = 20
    training_window_sessions: int = 1008
    evaluation_folds: int = 24
    random_seed: int = 42

    @property
    def one_way_cost_bps(self) -> float:
        return self.commission_bps + self.spread_bps + self.slippage_bps + self.market_impact_bps

    def to_dict(self) -> dict:
        return asdict(self)
