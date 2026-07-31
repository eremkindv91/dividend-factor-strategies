from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MODEL_STATUSES = {"production", "research_only", "rejected", "failed"}
DATA_STATUSES = {"pass", "degraded", "fail", "stale"}
SIGNAL_STATUSES = {"valid", "rejected", "no_signal", "stale", "solver_failed"}
ACTION_STATUSES = {"rebalance", "hold", "no_trade", "frozen"}


@dataclass(frozen=True)
class StrategyDecision:
    model_status: str
    data_status: str
    signal_status: str
    action_status: str
    title: str
    reason: str
    publish_candidate: bool


def decide_strategy_state(
    *,
    model_status: str,
    data_status: str,
    predictive_gate_passed: bool,
    portfolio_gate_passed: bool,
    solver_succeeded: bool,
    has_published_portfolio: bool,
    material_change: bool,
) -> StrategyDecision:
    """Resolve one auditable strategy state without mixing candidate and published weights."""
    if not solver_succeeded:
        return StrategyDecision(
            "failed", data_status, "solver_failed", "frozen" if has_published_portfolio else "no_trade",
            "Расчёт не завершён",
            "Оптимизатор не сформировал допустимый кандидат. Последний подтверждённый состав не изменён."
            if has_published_portfolio else "Оптимизатор не сформировал допустимый кандидат; целевой состав отсутствует.",
            False,
        )
    if data_status in {"fail", "stale", "degraded"}:
        return StrategyDecision(
            model_status, data_status, "stale" if data_status == "stale" else "no_signal",
            "frozen" if has_published_portfolio else "no_trade",
            "Расчёт заморожен" if has_published_portfolio else "Целевой состав не сформирован",
            "Входы модели не прошли контроль качества. Новые веса и операции не публикуются.",
            False,
        )
    if model_status != "production" or not predictive_gate_passed or not portfolio_gate_passed:
        failed = []
        if model_status != "production":
            failed.append("модель не имеет production-статуса")
        if not predictive_gate_passed:
            failed.append("не пройден прогнозный gate")
        if not portfolio_gate_passed:
            failed.append("не пройден after-cost portfolio gate")
        return StrategyDecision(
            model_status, data_status, "rejected", "no_trade",
            "Новый сигнал отклонён",
            "; ".join(failed).capitalize() + ". Последний подтверждённый состав не изменён."
            if has_published_portfolio else "; ".join(failed).capitalize() + ". Исполнимый целевой состав не сформирован.",
            False,
        )
    return StrategyDecision(
        "production", data_status, "valid", "rebalance" if material_change or not has_published_portfolio else "hold",
        "Сформирован новый модельный состав" if material_change or not has_published_portfolio else "Изменений не требуется",
        "Все обязательные gates пройдены; опубликованы исполнимые веса."
        if material_change or not has_published_portfolio else "Изменение ниже установленной no-trade zone.",
        material_change or not has_published_portfolio,
    )


def public_candidate(position: dict[str, Any]) -> dict[str, Any]:
    """Candidate diagnostics deliberately exclude executable quantities and trade instructions."""
    return {
        "ticker": position.get("ticker"),
        "name": position.get("name"),
        "sector": position.get("sector"),
        "calculated_weight": position.get("target_weight"),
        "expected_excess_return_20d": position.get("expected_excess_return_20d"),
        "sector_drivers": position.get("sector_drivers") or [],
    }


def extract_published_portfolio(previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(previous, dict):
        return None
    published = previous.get("published_portfolio")
    if isinstance(published, dict) and published.get("positions"):
        return published
    # Legacy snapshots are accepted only when they were explicitly production and actionable.
    model_status = str((previous.get("model") or {}).get("status") or "").upper()
    action = str((previous.get("signal") or {}).get("action") or "").upper()
    portfolio = previous.get("portfolio")
    if model_status == "APPROVED" and action in {"REBALANCE", "NO_ACTION"} and isinstance(portfolio, dict):
        return portfolio if portfolio.get("positions") else None
    return None


def strip_execution(portfolio: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": portfolio.get("method"),
        "positions": [],
        "cash_weight": 1.0,
        "turnover": None,
        "estimated_cost_rub": None,
        "one_way_cost_bps": portfolio.get("one_way_cost_bps"),
        "annualized_volatility": None,
        "beta": None,
        "fallback_reason": portfolio.get("fallback_reason"),
    }
