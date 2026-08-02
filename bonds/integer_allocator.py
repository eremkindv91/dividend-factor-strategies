#!/usr/bin/env python3
"""Integer-lot allocation and post-rounding checks for Bond Portfolio Lab."""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .universe_builder import load_json

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "portfolio_config.json"


def allocate_integer_lots(
    target: dict,
    universe: dict,
    budget_rub: float,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict:
    config = load_json(config_path)
    profile = config["profiles"][target["profile"]]
    horizon = config["horizons"][target["horizon"]]
    by_secid = {row["secid"]: row for row in universe.get("bonds") or []}
    source_positions = [item for item in target.get("target_positions") or [] if item["secid"] in by_secid]
    if target.get("status") not in {"OPTIMAL", "FEASIBLE"} or not source_positions:
        return {"status": "INFEASIBLE", "reason_codes": ["target_portfolio_unavailable"], "positions": []}

    budget_rub = round(float(budget_rub), 2)
    if budget_rub <= 0:
        return {"status": "INFEASIBLE", "reason_codes": ["budget_must_be_positive"], "positions": []}
    commission = float(config["costs"]["commission_bps"]) / 10000.0
    slippage = float(config["costs"]["slippage_bps"]) / 10000.0
    total_cost_rate = commission + slippage
    n = len(source_positions)
    lot_costs = np.array([
        float(by_secid[item["secid"]]["dirty_price_per_lot_rub"]) * (1.0 + total_cost_rate)
        for item in source_positions
    ])
    target_rub = np.array([float(item["target_weight"]) * budget_rub for item in source_positions])
    if np.any(lot_costs <= 0):
        return {"status": "INFEASIBLE", "reason_codes": ["invalid_dirty_lot_price"], "positions": []}

    # lot counts, positive deviations, negative deviations, cash
    pos0, neg0, cash_i = n, 2 * n, 3 * n
    size = 3 * n + 1
    objective = np.zeros(size)
    objective[pos0:pos0 + n] = 1.0 / budget_rub
    objective[neg0:neg0 + n] = 1.0 / budget_rub
    objective[cash_i] = 0.05 / budget_rub
    lower = np.zeros(size)
    upper = np.full(size, np.inf)
    lower[:n] = 1.0
    for index, item in enumerate(source_positions):
        row = by_secid[item["secid"]]
        cap_weight = min(
            float(profile["max_issue"]),
            float(row["median_volume_20d_rub"]) * float(profile["maximum_participation_rate"]) / budget_rub,
        )
        upper[index] = math.floor(cap_weight * budget_rub / lot_costs[index] + 1e-12)
        if upper[index] < 1:
            return {"status": "INFEASIBLE", "reason_codes": ["budget_or_liquidity_below_one_lot"], "positions": []}
    integrality = np.zeros(size, dtype=int)
    integrality[:n] = 1

    rows: list[np.ndarray] = []
    lows: list[float] = []
    highs: list[float] = []

    def add(entries: dict[int, float], low=-np.inf, high=np.inf):
        row = np.zeros(size)
        for index, value in entries.items():
            row[index] = value
        rows.append(row)
        lows.append(low)
        highs.append(high)

    for index in range(n):
        add({index: lot_costs[index], pos0 + index: -1.0, neg0 + index: 1.0}, target_rub[index], target_rub[index])
    add({**{index: lot_costs[index] for index in range(n)}, cash_i: 1.0}, budget_rub, budget_rub)

    by_issuer: dict[str, list[int]] = defaultdict(list)
    by_sector: dict[str, list[int]] = defaultdict(list)
    by_year: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(source_positions):
        row = by_secid[item["secid"]]
        by_issuer[str(row["issuer_id"])].append(index)
        by_sector[str(row.get("sector") or "unknown")].append(index)
        by_year[str(row.get("maturity_date") or "")[:4]].append(index)
    for issuer_id, indexes in by_issuer.items():
        if not issuer_id.startswith("sovereign:"):
            add({index: lot_costs[index] for index in indexes}, high=float(profile["max_issuer"]) * budget_rub)
    for sector, indexes in by_sector.items():
        if sector == "Государственные облигации":
            continue
        cap = float(profile["max_unknown_sector"] if sector == "unknown" else profile["max_sector"])
        add({index: lot_costs[index] for index in indexes}, high=cap * budget_rub)
    for indexes in by_year.values():
        add({index: lot_costs[index] for index in indexes}, high=float(profile["maximum_maturity_year_bucket"]) * budget_rub)
    ofz = [i for i, item in enumerate(source_positions) if by_secid[item["secid"]]["instrument_type"] == "ofz"]
    bbb = [i for i, item in enumerate(source_positions) if by_secid[item["secid"]].get("rating_group") == "BBB"]
    new = [i for i, item in enumerate(source_positions) if by_secid[item["secid"]].get("new_placement")]
    add({i: lot_costs[i] for i in ofz}, low=float(profile["minimum_ofz"]) * budget_rub - max(lot_costs))
    add({i: lot_costs[i] for i in bbb}, high=float(profile["max_bbb"]) * budget_rub + 0.01)
    add({i: lot_costs[i] for i in new}, high=float(profile["maximum_new_issues"]) * budget_rub + 0.01)
    add({i: (float(by_secid[item["secid"]]["duration_value"]) - float(horizon["min"])) * lot_costs[i] for i, item in enumerate(source_positions)}, low=-0.01)
    add({i: (float(by_secid[item["secid"]]["duration_value"]) - float(horizon["max"])) * lot_costs[i] for i, item in enumerate(source_positions)}, high=0.01)

    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(np.vstack(rows), np.array(lows), np.array(highs)),
        options={"time_limit": float(config["solver"]["time_limit_seconds"]), "presolve": True},
    )
    if not result.success or result.x is None:
        return {"status": "INFEASIBLE", "reason_codes": ["integer_allocation_failed"], "solver_message": str(result.message), "positions": []}

    lots = np.rint(result.x[:n]).astype(int)
    positions = []
    gross_purchase = 0.0
    total_costs = 0.0
    for index, item in enumerate(source_positions):
        row = by_secid[item["secid"]]
        dirty_amount = lots[index] * float(row["dirty_price_per_lot_rub"])
        costs = dirty_amount * total_cost_rate
        total = dirty_amount + costs
        gross_purchase += dirty_amount
        total_costs += costs
        positions.append({
            **item,
            "lots": int(lots[index]),
            "dirty_amount_rub": round(dirty_amount, 2),
            "estimated_costs_rub": round(costs, 2),
            "total_amount_rub": round(total, 2),
            "actual_weight": round(total / budget_rub, 10),
            "target_actual_deviation": round(total / budget_rub - float(item["target_weight"]), 10),
        })
    spent = round(gross_purchase + total_costs, 2)
    cash = round(budget_rub - spent, 2)
    return {
        "schema_version": "3.0",
        "status": "VALIDATED",
        "solver": "scipy.optimize.milp-highs",
        "budget_rub": budget_rub,
        "gross_purchase_rub": round(gross_purchase, 2),
        "estimated_costs_rub": round(total_costs, 2),
        "invested_with_costs_rub": spent,
        "cash_rub": cash,
        "cash_weight": round(cash / budget_rub, 10),
        "commission_bps": config["costs"]["commission_bps"],
        "slippage_bps": config["costs"]["slippage_bps"],
        "positions": positions,
    }
