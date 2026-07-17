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

    # v1.1: component risk contribution + concentration + sector risk contribution
    secids = list(al["matrix"])
    sectors = {p["secid"]: p.get("sector") for p in inc_positions}
    rc = risk_contribution(al["matrix"], weights, secids) if len(secids) >= 1 else {"ok": False}
    if rc.get("ok"):
        by_ticker = {p["secid"]: p.get("ticker") for p in inc_positions}
        for r in rc["rows"]:
            r["ticker"] = by_ticker.get(r["secid"], r["secid"])
        rc["rows"].sort(key=lambda r: -r["pcr"])
        rc["by_sector"] = sector_risk_contribution(rc["rows"], sectors)
    conc = concentration(weights, sectors)

    # v2: методы VaR (95/99, точечные оценки) — CF и bootstrap возвращают explicit-gate словари
    var_methods, method_diagnostics = {}, {}
    for lvl, key in [(0.95, "95"), (0.99, "99")]:
        cf = var_cornish_fisher(port, lvl)
        bs = var_bootstrap(port, lvl, n_sims=cfg.MC_SIMS_POINT, seed=cfg.MC_SEED)
        var_methods[key] = {
            "historical": v95["var"] if lvl == 0.95 else v99["var"],
            "normal": var_normal(port, lvl), "ewma": var_ewma(port, lvl),
            "cornish_fisher": cf["var"] if cf.get("ok") else None,
            "bootstrap": bs["var"] if bs.get("ok") else None,
        }
        method_diagnostics[key] = {"cornish_fisher": cf, "bootstrap": bs}

    # v2: строгий out-of-sample backtest (rolling, окно из config, БЕЗ look-ahead) + нейтральное
    # сравнение методов (не ранжируем по p-value; порядок фиксирован METHOD_META)
    backtest = backtest_method(port, 0.95, "historical", window=cfg.BACKTEST_WINDOW)
    comparison = method_comparison(port, var_methods["95"], window=cfg.BACKTEST_WINDOW)
    ewma_meta = {"lambda": cfg.EWMA_LAMBDA, "effective_memory_days": round(1.0 / (1.0 - cfg.EWMA_LAMBDA), 1),
                "confidence_level": 0.95, "current_forecast_date": common[-1]}

    base.update({
        "return_type": return_type_out, "observations": common_n,
        "common_start": common[0], "common_end": common[-1],
        "coverage": _coverage(value_cov, total_value, len(inc_positions), len(positions),
                              common[0], common[-1], number_cov, warnings, partial),
        "confidence": confidence, "metrics": metrics, "benchmark": bench_out,
        "included_positions": included_out, "warnings": warnings,
        "limiting_position": al["limiting"],
        "risk_contribution": rc, "concentration": conc,
        "var_methods": var_methods, "method_diagnostics": method_diagnostics,
        "backtest": backtest, "method_comparison": comparison, "ewma_meta": ewma_meta,
    })
    return base


def _coverage(value_cov, total_value, n_incl, n_total, cstart, cend, number_cov, warnings, partial):
    return {
        "value_coverage_ratio": round(value_cov, 4),
        "number_coverage_ratio": round(number_cov, 4) if number_cov is not None else None,
        "included_positions": n_incl, "excluded_positions": n_total - n_incl,
        "common_start": cstart, "common_end": cend, "partial": partial,
    }


# ── v1.1: component risk contribution + concentration ──

def covariance(matrix: dict[str, list[float]], secids: list[str]) -> list[list[float]]:
    """Дневная выборочная ковариация (ddof=1) выровненных рядов included-бумаг."""
    k = len(secids)
    n = len(matrix[secids[0]]) if secids else 0
    means = {s: mean(matrix[s]) for s in secids}
    S = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            si, sj = secids[i], secids[j]
            c = sum((matrix[si][t] - means[si]) * (matrix[sj][t] - means[sj]) for t in range(n))
            c = c / (n - cfg.DDOF) if n - cfg.DDOF > 0 else 0.0
            S[i][j] = S[j][i] = c
    return S


def risk_contribution(matrix, weights, secids, annualize=True) -> dict:
    """σ_p=√(wᵀΣw); MRC_i=(Σw)_i/σ_p; CRC_i=w_i·MRC_i; PCR_i=CRC_i/σ_p.
    Σ CRC ≈ σ_p, Σ PCR ≈ 1 (проверяется). Отрицательные вклады НЕ обрезаются."""
    k = len(secids)
    if k == 0:
        return {"ok": False}
    S = covariance(matrix, secids)
    w = [weights[s] for s in secids]
    Sw = [sum(S[i][j] * w[j] for j in range(k)) for i in range(k)]
    var_p = sum(w[i] * Sw[i] for i in range(k))
    sigma = math.sqrt(max(var_p, 0.0))
    scale = math.sqrt(cfg.TRADING_DAYS_YEAR) if annualize else 1.0
    if sigma <= cfg.FLOAT_TOL:
        return {"ok": False}
    rows = []
    crc_sum = 0.0
    for i, s in enumerate(secids):
        mrc = Sw[i] / sigma
        crc = w[i] * mrc
        crc_sum += crc
        rows.append({"secid": s, "weight": w[i], "mrc": mrc * scale,
                     "crc": crc * scale, "pcr": crc / sigma})
    converged = bool(abs(crc_sum - sigma) < cfg.RISK_CONTRIB_TOL * max(1.0, sigma))
    pcr_sum = float(sum(r["pcr"] for r in rows))
    return {"ok": True, "sigma_annual": float(sigma * scale), "rows": rows,
            "crc_sum_check": converged, "pcr_sum": pcr_sum}


def sector_risk_contribution(rc_rows: list[dict], sectors: dict[str, str]) -> list[dict]:
    """Агрегация PCR/веса по секторам из строк risk_contribution. Сортировка по убыванию вклада в риск."""
    agg: dict[str, dict] = {}
    for r in rc_rows:
        sec = sectors.get(r["secid"]) or "н/д"
        a = agg.setdefault(sec, {"sector": sec, "weight": 0.0, "risk_contribution_pct": 0.0})
        a["weight"] += r["weight"]
        a["risk_contribution_pct"] += r["pcr"]
    return sorted(agg.values(), key=lambda a: -a["risk_contribution_pct"])


def concentration(weights: dict[str, float], sectors: dict[str, str] | None = None) -> dict:
    """largest/top3/top5 вес, HHI по позициям и секторам, эффективное число бумаг."""
    ws = sorted(weights.values(), reverse=True)
    hhi = sum(w * w for w in weights.values())
    out = {"largest": ws[0] if ws else 0.0, "top3": sum(ws[:3]), "top5": sum(ws[:5]),
           "hhi": hhi, "effective_n": (1.0 / hhi) if hhi > 0 else 0.0, "sector_hhi": None, "sectors": None}
    if sectors:
        sw = {}
        for s, w in weights.items():
            sec = sectors.get(s) or "н/д"
            sw[sec] = sw.get(sec, 0.0) + w
        out["sector_hhi"] = sum(v * v for v in sw.values())
        out["sectors"] = dict(sorted(sw.items(), key=lambda kv: -kv[1]))
    return out


# ── v2: альтернативные оценки VaR (под backtest-валидацией) ──
_Z = {0.95: 1.6448536269514722, 0.99: 2.3263478740408408}


def var_normal(rets, level):
    return -(mean(rets) - _Z[level] * stdev(rets))


def var_ewma(rets, level, lam=0.94):
    """VaR по EWMA-волатильности (вес свежих наблюдений; λ=0.94 — RiskMetrics)."""
    if len(rets) < 2:
        return None
    m = mean(rets)
    var = (rets[0] - m) ** 2
    for r in rets[1:]:
        var = lam * var + (1 - lam) * (r - m) ** 2
    return _Z[level] * math.sqrt(var)


def _skew_kurtosis(rets, m, sd):
    n = len(rets)
    s = sum(((r - m) / sd) ** 3 for r in rets) / n
    kx = sum(((r - m) / sd) ** 4 for r in rets) / n - 3.0    # excess kurtosis (0 = нормальное)
    return s, kx


def var_cornish_fisher(rets, level) -> dict:
    """CF-скорректированный квантиль (skew/kurt). EXPLICIT GATE — никогда не клампится молча:
    либо валидное число + диагностика, либо ok=False с человеческой причиной (недостаточная
    история / неустойчивый excess kurtosis / численная нестабильность / нарушение инварианта)."""
    n = len(rets)
    if n < cfg.CF_MIN_OBS:
        return {"ok": False, "var": None, "reason": "insufficient_history",
                "detail": f"нужно ≥{cfg.CF_MIN_OBS} набл. для устойчивой оценки 3-4 момента, есть {n}"}
    m, sd = mean(rets), stdev(rets)
    if sd <= cfg.FLOAT_TOL:
        return {"ok": False, "var": None, "reason": "numerical_instability", "detail": "нулевая дисперсия"}
    s, kx = _skew_kurtosis(rets, m, sd)
    if not (math.isfinite(s) and math.isfinite(kx)):
        return {"ok": False, "var": None, "reason": "numerical_instability",
                "detail": "skew/excess kurtosis не определены"}
    if abs(kx) > cfg.CF_MAX_ABS_EXCESS_KURTOSIS:
        return {"ok": False, "var": None, "reason": "unstable_kurtosis",
                "detail": f"|excess kurtosis|={abs(kx):.1f} > порога {cfg.CF_MAX_ABS_EXCESS_KURTOSIS}"}
    z = -_Z[level]
    zc = z + (s / 6) * (z * z - 1) + (kx / 24) * (z ** 3 - 3 * z) - (s * s / 36) * (2 * z ** 3 - 5 * z)
    if not math.isfinite(zc) or zc > 0 or zc < 4 * z:   # адаптированный квантиль обязан остаться
        return {"ok": False, "var": None, "reason": "numerical_instability",             # в разумном диапазоне убыточного хвоста (z<0)
                "detail": "скорректированный квантиль вне допустимого диапазона (CF explosive)"}
    var = -(m + zc * sd)
    if not math.isfinite(var) or var <= 0:
        return {"ok": False, "var": None, "reason": "invariant_violation",
                "detail": "CF VaR ≤ 0 или не определён — нарушает базовый инвариант положительности убытка"}
    return {"ok": True, "var": var, "skewness": s, "excess_kurtosis": kx, "z_adjusted": zc}


def _autocorr_lag1(xs: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    m = mean(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den <= cfg.FLOAT_TOL:
        return 0.0
    num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, n))
    return num / den


def _volatility_clustering_autocorr(rets: list[float]) -> float:
    """Lag-1 автокорреляция КВАДРАТОВ доходностей — ARCH-эффект/кластеризация волатильности.
    Информативнее автокорреляции самих доходностей (та у дневных акций близка к 0 по эффективности
    рынка) для решения IID vs block bootstrap."""
    m = mean(rets)
    return _autocorr_lag1([(r - m) ** 2 for r in rets])


def var_bootstrap(rets, level, n_sims=None, seed=None) -> dict:
    """Historical Monte Carlo — SEEDED NON-PARAMETRIC bootstrap (НЕ предполагает нормальность,
    НЕ генерирует синтетическую доходность из распределения — ресэмплирует РЕАЛЬНЫЕ исторические
    значения с возвращением). IID vs block bootstrap выбирается автоматически по диагностике
    кластеризации волатильности (см. _volatility_clustering_autocorr); выбор и обоснование
    сохраняются в результате, не молчаливые."""
    import random
    n = len(rets)
    if n < 20:
        return {"ok": False, "var": None, "reason": "insufficient_history"}
    n_sims = n_sims or cfg.MC_SIMS_POINT
    seed = cfg.MC_SEED if seed is None else seed
    ac_sq = _volatility_clustering_autocorr(rets)
    use_block = abs(ac_sq) >= cfg.MC_AUTOCORR_BLOCK_THRESHOLD
    block_len = cfg.MC_BLOCK_LENGTH if use_block else 1
    rng = random.Random(seed)
    sims: list[float] = []
    if use_block:
        # circular moving block bootstrap: блоки последовательных дней сохраняют локальную
        # кластеризацию волатильности при ресэмплинге (в отличие от независимого IID-семплинга)
        while len(sims) < n_sims:
            start = rng.randrange(0, n)
            for k in range(block_len):
                sims.append(rets[(start + k) % n])
                if len(sims) >= n_sims:
                    break
    else:
        for _ in range(n_sims):
            sims.append(rets[rng.randrange(0, n)])
    sims.sort()
    var = -quantile(sims, 1 - level)
    return {"ok": True, "var": var, "bootstrap_method": "block" if use_block else "iid",
            "block_length": block_len if use_block else None,
            "autocorr_sq_lag1": ac_sq, "n_sims": n_sims, "seed": seed}


# ── v2: VaR backtest (Kupiec POF + Christoffersen independence) ──

def _chi2_sf_1df(x):
    return math.erfc(math.sqrt(x / 2.0)) if x >= 0 else 1.0


def kupiec_pof(breaches, obs, level):
    """LR теста Kupiec (proportion of failures). p<0.05 → VaR некорректно калиброван."""
    p = 1 - level
    x, n = breaches, obs
    if n == 0:
        return {"ok": False}
    ph = x / n
    def _ln(v):
        return math.log(v) if v > 0 else 0.0
    ll0 = (n - x) * _ln(1 - p) + x * _ln(p)
    ll1 = (n - x) * _ln(1 - ph) + x * _ln(ph)
    lr = -2 * (ll0 - ll1)
    return {"ok": True, "lr": lr, "pvalue": _chi2_sf_1df(lr), "reject": lr > cfg.KUPIEC_REJECT_LR}


def christoffersen_independence(seq):
    """LR независимости пробоев (нет кластеризации). seq — список 0/1 (пробой VaR)."""
    n00 = n01 = n10 = n11 = 0
    for i in range(1, len(seq)):
        a, b = seq[i - 1], seq[i]
        if a == 0 and b == 0: n00 += 1
        elif a == 0 and b == 1: n01 += 1
        elif a == 1 and b == 0: n10 += 1
        else: n11 += 1
    if (n00 + n01) == 0 or (n10 + n11) == 0:
        return {"ok": False}
    pi01 = n01 / (n00 + n01); pi11 = n11 / (n10 + n11)
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    def _ln(v):
        return math.log(v) if v > 0 else 0.0
    ll0 = (n00 + n10) * _ln(1 - pi) + (n01 + n11) * _ln(pi)
    ll1 = n00 * _ln(1 - pi01) + n01 * _ln(pi01) + n10 * _ln(1 - pi11) + n11 * _ln(pi11)
    lr = -2 * (ll0 - ll1)
    return {"ok": True, "lr": lr, "pvalue": _chi2_sf_1df(lr), "reject": lr > cfg.KUPIEC_REJECT_LR}


def var_backtest(rets, level=0.95, window=252):
    """Rolling historical VaR → пробои → Kupiec + Christoffersen. Валидирует ОСНОВНОЙ (historical) VaR."""
    n = len(rets)
    if n < window + 20:
        return {"ok": False, "reason": "short"}
    seq, breaches = [], 0
    for i in range(window, n):
        s = sorted(rets[i - window:i])
        var = -quantile(s, 1 - level)
        br = 1 if rets[i] < -var else 0
        seq.append(br); breaches += br
    obs = len(seq)
    kp = kupiec_pof(breaches, obs, level)
    ch = christoffersen_independence(seq)
    verdict = "калиброван" if (kp.get("ok") and not kp["reject"]) else "калибровка под вопросом"
    return {"ok": True, "obs": obs, "breaches": breaches, "expected": (1 - level) * obs,
            "freq": breaches / obs if obs else None, "kupiec": kp, "christoffersen": ch, "verdict": verdict}


# ── обобщённый rolling out-of-sample backtest (для ЛЮБОГО метода VaR, для method comparison) ──

def rolling_backtest(rets, level, window, forecast_fn) -> dict:
    """Строго out-of-sample: прогноз на день i использует ТОЛЬКО rets[i-window:i] (нет look-ahead).
    forecast_fn(window_returns) -> var|None; None (напр. CF gate не пройден на этом окне) —
    окно пропускается (skipped_gate), не считается ни пробоем, ни не-пробоем.
    < cfg.MIN_BACKTEST_FORECASTS валидных прогнозов → status=insufficient_backtest_history,
    p-value не публикуется как значимый (kupiec/christoffersen отсутствуют)."""
    n = len(rets)
    seq, breaches, skipped = [], 0, 0
    for i in range(window, n):
        var = forecast_fn(rets[i - window:i])          # ТОЛЬКО прошлое — no look-ahead
        if var is None:
            skipped += 1
            continue
        br = 1 if rets[i] < -var else 0
        seq.append(br)
        breaches += br
    obs = len(seq)
    if obs < cfg.MIN_BACKTEST_FORECASTS:
        return {"ok": False, "status": "insufficient_backtest_history", "obs": obs,
                "skipped_gate": skipped, "window": window, "level": level,
                "min_required": cfg.MIN_BACKTEST_FORECASTS}
    kp = kupiec_pof(breaches, obs, level)
    ch = christoffersen_independence(seq)
    calibrated = bool(kp.get("ok") and not kp["reject"])
    return {"ok": True, "status": "complete", "window": window, "level": level,
            "horizon_days": cfg.BACKTEST_HORIZON_DAYS, "policy": "rolling",
            "obs": obs, "skipped_gate": skipped, "breaches": breaches,
            "expected_breaches": (1 - level) * obs, "freq": breaches / obs,
            "kupiec": kp, "christoffersen": ch, "calibrated": calibrated,
            "verdict": "калиброван" if calibrated else "калибровка под вопросом"}


def backtest_method(rets, level, method, window=None) -> dict:
    """rolling_backtest для конкретного именованного метода VaR (historical/normal/ewma/
    cornish_fisher/bootstrap). Единая точка входа для method comparison."""
    window = window or cfg.BACKTEST_WINDOW
    if method == "historical":
        fn = lambda w: -quantile(sorted(w), 1 - level)  # noqa: E731
    elif method == "normal":
        fn = lambda w: var_normal(w, level)  # noqa: E731
    elif method == "ewma":
        fn = lambda w: var_ewma(w, level, cfg.EWMA_LAMBDA)  # noqa: E731
    elif method == "cornish_fisher":
        def fn(w):
            r = var_cornish_fisher(w, level)
            return r["var"] if r.get("ok") else None
    elif method == "bootstrap":
        def fn(w):
            r = var_bootstrap(w, level, n_sims=cfg.MC_SIMS_BACKTEST, seed=cfg.MC_SEED)
            return r["var"] if r.get("ok") else None
    else:
        raise ValueError(f"неизвестный метод {method}")
    return rolling_backtest(rets, level, window, fn)


METHOD_META = {
    "historical": {"label": "Исторический", "assumptions":
                   "эмпирическое распределение прошлых доходностей, без параметрических допущений"},
    "normal": {"label": "Нормальное распределение", "assumptions":
               "доходности распределены нормально (Gauss); недооценивает жирные хвосты"},
    "ewma": {"label": "EWMA", "assumptions":
             f"нормальное распределение с экспоненциально взвешенной волатильностью (λ={cfg.EWMA_LAMBDA})"},
    "cornish_fisher": {"label": "Cornish-Fisher", "assumptions":
                       "поправка нормального квантиля на скошенность и эксцесс выборки"},
    "bootstrap": {"label": "Monte Carlo (bootstrap)", "assumptions":
                  "непараметрический ресэмплинг реальных исторических доходностей (не синтетика)"},
}


def _neutral_verdict(bt: dict) -> str:
    """Простой пользовательский вердикт из результата backtest. Нейтрален — не хвалит/не пугает
    сверх того, что показывает статистика."""
    if not bt.get("ok"):
        return "Истории недостаточно для надёжной проверки"
    kp_ok = bt["kupiec"].get("ok") and not bt["kupiec"]["reject"]
    ch = bt.get("christoffersen") or {}
    ch_ok = ch.get("ok") and not ch.get("reject")
    if kp_ok and ch_ok:
        return "VaR откалиброван приемлемо"
    if not kp_ok:
        underestimates = bt["freq"] > (1 - bt["level"])
        return "VaR занижает риск" if underestimates else "VaR завышает риск (слишком консервативен)"
    return "Наблюдается кластеризация плохих дней"          # kupiec ok, christoffersen — нет


def method_comparison(port_returns, point_estimates: dict, window=None) -> list[dict]:
    """Нейтральная таблица сравнения методов VaR 95%: допущения + точечная оценка + backtest +
    простой вердикт. Порядок ФИКСИРОВАН (METHOD_META) — НЕ ранжируем по p-value."""
    window = window or cfg.BACKTEST_WINDOW
    rows = []
    for method, meta in METHOD_META.items():
        est = point_estimates.get(method)
        bt = backtest_method(port_returns, 0.95, method, window=window)
        rows.append({
            "method": method, "label": meta["label"], "assumptions": meta["assumptions"],
            "current_estimate": est, "available": est is not None,
            "backtest": bt, "verdict": _neutral_verdict(bt),
        })
    return rows


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
    rc = result.get("risk_contribution")
    if rc and rc.get("ok"):
        if not rc.get("crc_sum_check"):
            errs.append("Σ CRC не сходится к σ_p")
        if abs(rc.get("pcr_sum", 0) - 1.0) > 1e-4:
            errs.append(f"Σ PCR={rc.get('pcr_sum')} далеко от 100%")
        if "by_sector" in rc:
            sec_sum = sum(s["risk_contribution_pct"] for s in rc["by_sector"])
            if abs(sec_sum - rc["pcr_sum"]) > 1e-6:
                errs.append("сумма sector risk contribution ≠ Σ PCR по позициям")
    bt = result.get("backtest")
    if bt and bt.get("ok"):
        if not (0 <= bt.get("freq", 0) <= 1):
            errs.append("backtest.freq вне [0,1]")
        if bt.get("breaches", 0) > bt.get("obs", 0):
            errs.append("backtest.breaches > obs")
    for row in result.get("method_comparison") or []:
        est = row.get("current_estimate")
        if est is not None and est <= 0:
            errs.append(f"method_comparison[{row.get('method')}].current_estimate ≤ 0")
    return errs


def portfolio_input_hash(positions: list[dict]) -> str:
    """Локальный непубличный хеш состава (для детерминизма/кеша). НЕ логировать состав."""
    canon = sorted((str(p.get("secid")), float(p.get("quantity") or 0)) for p in positions)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]
