#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Risk Engine v1 — КАНОНИЧЕСКОЕ ядро (Python, stdlib). Численно валидируемая
референс-реализация; клиентский JS зеркалит эти формулы (портфель не покидает браузер).

Считает по ПОДТВЕРЖДЁННЫМ дневным рядам: daily/annualized volatility, historical VaR/CVaR
95/99, max model drawdown, downside deviation, beta/correlation к бенчмарку, coverage,
confidence, excluded positions. Детерминированно (единый config), без сети/random.

ЧЕСТНОСТЬ: это «историческая модель ТЕКУЩЕГО состава» на фиксированных текущих весах —
НЕ фактическая история сделок пользователя. Не прогноз. Не рекомендация.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_config as cfg  # noqa: E402

SCHEMA_VERSION = 1
METHODOLOGY_VERSION = "daily-risk-v1"

# машинный код исключения → человеческая причина (в UI показываем человеческую)
EXCLUSION_REASON = {
    "invalid_value": "нулевая или некорректная стоимость позиции",
    "no_data": "данные отсутствуют",
    "stale": "ряд устарел",
    "insufficient_history": "недостаточная история",
    "corporate_action_unresolved": "нераспознанное корпоративное действие",
    "not_mapped": "тикер не сопоставлен",
    "quality_not_usable": "качество данных недостаточно",
    "insufficient_common_dates": "мало общих торговых дат с портфелем",
}
_RETURN_RANK = {"total_return": 3, "adjusted_price_return": 2, "raw_price_return": 1}


# ── базовая статистика (ddof из config) ──

def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: list[float], ddof: int = cfg.DDOF) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def quantile(sorted_asc: list[float], p: float) -> float:
    """Историческая квантиль, линейная интерполяция (тип-7, как numpy). sorted_asc — возр."""
    n = len(sorted_asc)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_asc[0]
    h = (n - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_asc[lo] + (h - lo) * (sorted_asc[hi] - sorted_asc[lo])


# ── выравнивание рядов по общим датам ──

def align(series_map: dict[str, dict]) -> dict:
    """series_map: {secid: {date: return}}. Возвращает common_dates + матрицу по included."""
    if not series_map:
        return {"dates": [], "matrix": {}, "limiting": None}
    date_sets = {s: set(d) for s, d in series_map.items()}
    common = sorted(set.intersection(*date_sets.values())) if date_sets else []
    matrix = {s: [series_map[s][d] for d in common] for s in series_map}
    # позиция, ограничивающая длину (наименьшая собственная история)
    limiting = min(series_map, key=lambda s: len(series_map[s])) if series_map else None
    return {"dates": common, "matrix": matrix, "limiting": limiting}


def portfolio_returns(matrix: dict[str, list[float]], weights: dict[str, float]) -> list[float]:
    """Σ w_i · r_i,t на общих датах (фиксированные веса, без скрытой ребалансировки)."""
    secids = list(matrix)
    if not secids:
        return []
    n = len(matrix[secids[0]])
    out = []
    for t in range(n):
        out.append(sum(weights[s] * matrix[s][t] for s in secids))
    return out


# ── метрики ──

def var_cvar(returns: list[float], level: float) -> dict:
    """Historical VaR/CVaR на уровне level (0.95/0.99). VaR/CVaR — положительные величины потерь."""
    if not returns:
        return {"var": None, "cvar": None, "tail_n": 0}
    s = sorted(returns)
    q = quantile(s, 1 - level)                 # нижний квантиль доходностей
    var = -q
    tail = [r for r in returns if r <= q]
    cvar = -mean(tail) if tail else var
    return {"var": var, "cvar": cvar, "tail_n": len(tail)}


def max_drawdown(returns: list[float], dates: list[str]) -> dict:
    """Max drawdown по кумулятивному wealth index (старт 1). Возвращает величину ≤ 0 + даты."""
    if not returns:
        return {"max_drawdown": 0.0, "peak_date": None, "trough_date": None,
                "recovery_date": None, "current_drawdown": 0.0}
    wealth, peak = 1.0, 1.0
    peak_i = 0
    mdd, mdd_peak_i, mdd_trough_i = 0.0, 0, 0
    wealth_series = [1.0]
    for r in returns:
        wealth *= (1.0 + r)
        wealth_series.append(wealth)
    peaks = []
    peak = wealth_series[0]
    peak_idx = 0
    for i, w in enumerate(wealth_series):
        if w > peak:
            peak, peak_idx = w, i
        dd = w / peak - 1.0
        if dd < mdd:
            mdd, mdd_peak_i, mdd_trough_i = dd, peak_idx, i
    # recovery: первый индекс после trough, где wealth >= peak на момент mdd
    peak_val = wealth_series[mdd_peak_i]
    recovery_i = None
    for i in range(mdd_trough_i + 1, len(wealth_series)):
        if wealth_series[i] >= peak_val:
            recovery_i = i
            break
    cur_peak = max(wealth_series)
    current_dd = wealth_series[-1] / cur_peak - 1.0

    def _d(idx):  # индекс wealth (0..n) → дата (dates длиной n; wealth[0]=старт до первой доходности)
        j = idx - 1
        return dates[j] if 0 <= j < len(dates) else None
    return {"max_drawdown": mdd, "peak_date": _d(mdd_peak_i), "trough_date": _d(mdd_trough_i),
            "recovery_date": _d(recovery_i) if recovery_i is not None else None,
            "current_drawdown": current_dd}


def downside_deviation(returns: list[float], mar: float = cfg.MAR_DAILY) -> float:
    """√(среднее квадратов отрицательных отклонений от MAR). Дневная частота, MAR=0 по умолчанию."""
    if not returns:
        return 0.0
    downs = [min(r - mar, 0.0) ** 2 for r in returns]
    return math.sqrt(sum(downs) / len(downs))


def beta_correlation(port: list[float], bench: list[float]) -> dict:
    """beta = cov(p,b)/var(b); Pearson corr. Требует совпадающей длины (общие даты)."""
    n = min(len(port), len(bench))
    if n < 2:
        return {"beta": None, "correlation": None, "n": n}
    p, b = port[:n], bench[:n]
    mp, mb = mean(p), mean(b)
    cov = sum((p[i] - mp) * (b[i] - mb) for i in range(n)) / (n - cfg.DDOF)
    var_b = sum((b[i] - mb) ** 2 for i in range(n)) / (n - cfg.DDOF)
    sd_p = math.sqrt(sum((p[i] - mp) ** 2 for i in range(n)) / (n - cfg.DDOF))
    sd_b = math.sqrt(var_b)
    if var_b <= cfg.FLOAT_TOL:
        return {"beta": None, "correlation": None, "n": n, "note": "нулевая дисперсия бенчмарка"}
    beta = cov / var_b
    corr = cov / (sd_p * sd_b) if sd_p > cfg.FLOAT_TOL and sd_b > cfg.FLOAT_TOL else None
    return {"beta": beta, "correlation": corr, "n": n}


# ── классификация позиций ──

def _clean_returns(pos: dict) -> dict[str, float]:
    """{date: float(return)} только валидные числовые значения (None/битые/будущие-как-строки — вон)."""
    out = {}
    for d, r in (pos.get("returns") or {}).items():
        if r is None:
            continue
        try:
            v = float(r)
        except (TypeError, ValueError):
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        out[str(d)[:10]] = v
    return out


def _classify_position(pos: dict) -> tuple[bool, str | None]:
    """(included, exclusion_code|None)."""
    mv = _market_value(pos)
    if mv is None or mv <= 0:
        return False, "invalid_value"
    if not _clean_returns(pos):
        return False, "no_data"
    q = pos.get("quality_status")
    if q == "unavailable":
        return False, "no_data"
    if q == "stale":
        return False, "stale"
    if q == "insufficient_history":
        return False, "insufficient_history"
    if pos.get("corporate_action_status") == "unresolved" or q == "corporate_action_unresolved":
        return False, "corporate_action_unresolved"
    if pos.get("secid") is None:
        return False, "not_mapped"
    return True, None


def _market_value(pos: dict):
    try:
        qty, price = float(pos.get("quantity")), float(pos.get("price"))
    except (TypeError, ValueError):
        return None
    return qty * price if qty > 0 and price > 0 else (0.0 if qty <= 0 else None)


def _confidence(common_n, value_cov, mixed_types, any_fallback, bench_ok, partial):
    order = ["unavailable", "low", "medium", "high"]
    cap = "high"

    def down_to(level):
        nonlocal cap
        if order.index(level) < order.index(cap):
            cap = level
    if common_n < cfg.MIN_COMMON_OBS:
        return "unavailable"
    if common_n < cfg.HISTORY_INSUFFICIENT:
        down_to("low")
    elif common_n < cfg.HISTORY_USABLE:
        down_to("medium")
    if value_cov < cfg.VALUE_COVERAGE_MIN:
        down_to("low")
    elif value_cov < cfg.VALUE_COVERAGE_PARTIAL:
        down_to("medium")
    if mixed_types or any_fallback:
        down_to("medium")
    if not bench_ok:
        down_to("medium")
    if partial:
        down_to("medium")
    return cap


def compute(positions: list[dict], benchmark: dict | None = None, now: datetime | None = None,
            source_as_of: str | None = None) -> dict:
    """Полный расчёт Daily Risk Engine v1. positions: [{ticker, secid, quantity, price, returns:{date:r},
    quality_status, corporate_action_status, return_type, fallback_status}]. benchmark: {ticker,type,
    source_as_of, returns:{date:r}}."""
    now = now or datetime.now(timezone.utc)
    total_value = sum(v for v in (_market_value(p) for p in positions) if v) or 0.0

    included, excluded = [], []
    for p in positions:
        ok, code = _classify_position(p)
        (included if ok else excluded).append((p, code))

    excluded_out = [{"ticker": p.get("ticker"), "secid": p.get("secid"),
                     "exclusion_reason": EXCLUSION_REASON.get(code, "недоступно"),
                     "inclusion_status": "excluded"} for p, code in excluded]

    base = {
        "schema_version": SCHEMA_VERSION, "methodology_version": METHODOLOGY_VERSION,
        "calculated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source_as_of": source_as_of,
        "weights_method": "current_market_value_fixed_weights",
        "annualization_factor": cfg.TRADING_DAYS_YEAR, "ddof": cfg.DDOF,
        "quantile_method": cfg.QUANTILE_METHOD,
        "disclaimer": "Историческая модель текущего состава на фиксированных текущих весах. "
                      "Не фактическая история ваших сделок. Не прогноз, не рекомендация.",
        "excluded_positions": excluded_out,
    }

    if len(included) < 1:
        base.update({"confidence": "unavailable", "return_type": None, "observations": 0,
                     "coverage": _coverage(0.0, total_value, 0, len(positions), None, None, None, [], False),
                     "metrics": None, "benchmark": None, "warnings": ["нет включаемых позиций"],
                     "included_positions": []})
        return base

    inc_positions = [p for p, _ in included]
    inc_value = sum(_market_value(p) for p in inc_positions)
    weights = {p["secid"]: _market_value(p) / inc_value for p in inc_positions}  # ренорм на included (явно)

    series_map = {p["secid"]: _clean_returns(p) for p in inc_positions}
    al = align(series_map)
    common = al["dates"]
    common_n = len(common)

    ret_ranks = [_RETURN_RANK.get(p.get("return_type", "raw_price_return"), 1) for p in inc_positions]
    mixed_types = len(set(ret_ranks)) > 1
    return_type_out = {3: "total_return", 2: "adjusted_price_return", 1: "raw_price_return"}[min(ret_ranks)]
    any_fallback = any(p.get("fallback_status") == "fallback" or p.get("return_type") == "raw_price_return"
                       for p in inc_positions)
    value_cov = inc_value / total_value if total_value > 0 else 0.0
    number_cov = len(inc_positions) / len(positions) if positions else 0.0
    partial = value_cov < cfg.VALUE_COVERAGE_PARTIAL

    included_out = [{"ticker": p.get("ticker"), "secid": p.get("secid"),
                     "weight": round(weights[p["secid"]], 6), "return_type": p.get("return_type"),
                     "observations": len(p.get("returns") or {}),
                     "quality_status": p.get("quality_status"),
                     "corporate_action_status": p.get("corporate_action_status", "resolved"),
                     "fallback_status": p.get("fallback_status", "none"),
                     "inclusion_status": "included"} for p in inc_positions]

    if common_n < cfg.MIN_COMMON_OBS:
        base.update({"confidence": "unavailable", "return_type": return_type_out, "observations": common_n,
                     "coverage": _coverage(value_cov, total_value, len(inc_positions), len(positions),
                                           common[0] if common else None, common[-1] if common else None,
                                           None, [], partial),
                     "metrics": None, "benchmark": None, "included_positions": included_out,
                     "warnings": [f"мало общих торговых дней ({common_n} < {cfg.MIN_COMMON_OBS})"]})
        return base

    port = portfolio_returns(al["matrix"], weights)

    daily_vol = stdev(port)
    ann_vol = daily_vol * math.sqrt(cfg.TRADING_DAYS_YEAR)
    v95, v99 = var_cvar(port, 0.95), var_cvar(port, 0.99)
    mdd = max_drawdown(port, common)
    dd_dev = downside_deviation(port)

    # бенчмарк — по общим датам с портфелем
    bench_out, bres = None, {"beta": None, "correlation": None}
    bench_ok = False
    if benchmark and benchmark.get("returns"):
        bser = {d: float(r) for d, r in benchmark["returns"].items()}
        bdates = [d for d in common if d in bser]
        if len(bdates) >= cfg.MIN_COMMON_OBS:
            pmap = dict(zip(common, port))
            bres = beta_correlation([pmap[d] for d in bdates], [bser[d] for d in bdates])
            bench_ok = bres.get("beta") is not None
        bench_out = {"benchmark_ticker": benchmark.get("ticker", cfg.BENCHMARK),
                     "benchmark_type": benchmark.get("type", "price_index"),
                     "benchmark_source_as_of": benchmark.get("source_as_of"),
                     "observations": len(bdates), "common_start": bdates[0] if bdates else None,
                     "common_end": bdates[-1] if bdates else None,
                     "beta": bres.get("beta"), "correlation": bres.get("correlation")}

    confidence = _confidence(common_n, value_cov, mixed_types, any_fallback, bench_ok, partial)
    covered_value = inc_value
    cvar99_ok = v99["tail_n"] >= cfg.CVAR_MIN_TAIL

    metrics = {
        "daily_volatility": daily_vol,
        "annualized_volatility": ann_vol,
        "var_95": {"pct": v95["var"], "rub": v95["var"] * covered_value},
        "var_99": {"pct": v99["var"], "rub": v99["var"] * covered_value},
        "cvar_95": {"pct": v95["cvar"], "rub": v95["cvar"] * covered_value, "tail_n": v95["tail_n"],
                    "confidence": "ok" if v95["tail_n"] >= cfg.CVAR_MIN_TAIL else "low"},
        "cvar_99": ({"pct": v99["cvar"], "rub": v99["cvar"] * covered_value, "tail_n": v99["tail_n"],
                     "confidence": "ok"} if cvar99_ok else {"pct": None, "rub": None,
                     "tail_n": v99["tail_n"], "confidence": "low"}),
        "max_drawdown": mdd,
        "downside_deviation": dd_dev,
        "covered_value": covered_value,
    }
    warnings = []
    if mixed_types:
        warnings.append("смешение типов рядов — надёжность не выше средней")
    if any_fallback:
        warnings.append("часть позиций на сырых (нескорректированных) ценах")
    if partial:
        warnings.append("покрыта не вся стоимость портфеля — метрики частичные")
    if not bench_ok:
        warnings.append("бенчмарк не выровнен — beta/корреляция недоступны")

    base.update({
        "return_type": return_type_out, "observations": common_n,
        "common_start": common[0], "common_end": common[-1],
        "coverage": _coverage(value_cov, total_value, len(inc_positions), len(positions),
                              common[0], common[-1], number_cov, warnings, partial),
        "confidence": confidence, "metrics": metrics, "benchmark": bench_out,
        "included_positions": included_out, "warnings": warnings,
        "limiting_position": al["limiting"],
    })
    return base


def _coverage(value_cov, total_value, n_incl, n_total, cstart, cend, number_cov, warnings, partial):
    return {
        "value_coverage_ratio": round(value_cov, 4),
        "number_coverage_ratio": round(number_cov, 4) if number_cov is not None else None,
        "included_positions": n_incl, "excluded_positions": n_total - n_incl,
        "common_start": cstart, "common_end": cend, "partial": partial,
    }


def validate(result: dict) -> list[str]:
    """Валидатор контракта результата + численные инварианты. [] = валиден."""
    errs = []
    if result.get("schema_version") != SCHEMA_VERSION:
        errs.append("schema_version")
    if result.get("confidence") not in ("high", "medium", "low", "unavailable"):
        errs.append(f"confidence={result.get('confidence')}")
    cov = result.get("coverage") or {}
    vc = cov.get("value_coverage_ratio")
    if vc is not None and not (0 <= vc <= 1.0001):
        errs.append(f"value_coverage {vc} вне [0,1]")
    cs, ce = cov.get("common_start"), cov.get("common_end")
    if cs and ce and cs > ce:
        errs.append("common_start > common_end")
    m = result.get("metrics")
    if m:
        if m["daily_volatility"] < -cfg.FLOAT_TOL or m["annualized_volatility"] < -cfg.FLOAT_TOL:
            errs.append("volatility < 0")
        if m["max_drawdown"]["max_drawdown"] > cfg.FLOAT_TOL:
            errs.append("max_drawdown > 0")
        for lvl in ("95", "99"):
            var, cv = m[f"var_{lvl}"]["pct"], m[f"cvar_{lvl}"]["pct"]
            if var is not None and cv is not None and abs(cv) + cfg.FLOAT_TOL < abs(var):
                errs.append(f"|CVaR{lvl}| < |VaR{lvl}|")
        cov_val = m.get("covered_value")
        if cov_val and m["var_95"]["pct"] is not None:
            if abs(m["var_95"]["rub"] - m["var_95"]["pct"] * cov_val) > 1e-6:
                errs.append("VaR rub ≠ pct·value")
    b = result.get("benchmark")
    if b and b.get("correlation") is not None and not (-1.0001 <= b["correlation"] <= 1.0001):
        errs.append("correlation вне [-1,1]")
    return errs


def portfolio_input_hash(positions: list[dict]) -> str:
    """Локальный непубличный хеш состава (для детерминизма/кеша). НЕ логировать состав."""
    canon = sorted((str(p.get("secid")), float(p.get("quantity") or 0)) for p in positions)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]
