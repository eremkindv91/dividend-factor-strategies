# -*- coding: utf-8 -*-
"""Тесты Daily Risk Engine v1 (scripts/daily_risk.py). Детерминированно, без сети.
Численная валидация — независимая reference-реализация на numpy."""
import os
import sys
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import daily_risk as dr  # noqa: E402
import daily_config as cfg  # noqa: E402

NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _dates(n, start=737000):
    return [datetime.fromordinal(start + i).date().isoformat() for i in range(n)]


def _pos(ticker, qty, price, rets, dates, quality="good", ca="resolved",
         rtype="total_return", fb="none", secid=None):
    return {"ticker": ticker, "secid": secid or ticker, "quantity": qty, "price": price,
            "returns": dict(zip(dates, rets)), "quality_status": quality,
            "corporate_action_status": ca, "return_type": rtype, "fallback_status": fb}


def _series(n, seed, scale=0.01):
    return list(np.random.default_rng(seed).normal(0, scale, n))


# ── состав / веса ──

def test_1_single_security():
    d = _dates(300)
    r = dr.compute([_pos("SBER", 10, 100, _series(300, 1), d)], now=NOW)
    assert r["metrics"] is not None and r["coverage"]["included_positions"] == 1
    assert abs(sum(p["weight"] for p in r["included_positions"]) - 1.0) < 1e-9


def test_5_6_weights_sum_and_normalization():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d), _pos("B", 30, 50, _series(300, 2), d)], now=NOW)
    w = {p["ticker"]: p["weight"] for p in r["included_positions"]}
    assert abs(w["A"] + w["B"] - 1.0) < 1e-9
    assert abs(w["A"] - (1000 / 2500)) < 1e-9      # 10*100 / (1000+1500)


def test_7_zero_value_excluded():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d), _pos("Z", 0, 100, _series(300, 2), d)], now=NOW)
    ex = [e["ticker"] for e in r["excluded_positions"]]
    assert "Z" in ex


def test_8_negative_quantity_excluded():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d), _pos("N", -5, 100, _series(300, 2), d)], now=NOW)
    assert "N" in [e["ticker"] for e in r["excluded_positions"]]


def test_9_missing_price_excluded():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d), _pos("M", 5, None, _series(300, 2), d)], now=NOW)
    assert "M" in [e["ticker"] for e in r["excluded_positions"]]


def test_10_partial_value_coverage():
    d = _dates(300)
    # большая исключаемая позиция (stale) → покрытие стоимости < порога → partial
    r = dr.compute([_pos("A", 1, 100, _series(300, 1), d),
                    _pos("BIG", 100, 100, _series(300, 2), d, quality="stale")], now=NOW)
    assert r["coverage"]["partial"] is True and r["coverage"]["value_coverage_ratio"] < cfg.VALUE_COVERAGE_PARTIAL
    assert r["confidence"] in ("medium", "low")


def test_11_insufficient_history_unavailable():
    d = _dates(40)
    r = dr.compute([_pos("A", 10, 100, _series(40, 1), d)], now=NOW)
    assert r["confidence"] == "unavailable" and r["metrics"] is None


def test_12_common_date_intersection():
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), _dates(300, 737000)),
                    _pos("B", 10, 100, _series(300, 2), _dates(300, 737050))], now=NOW)  # сдвиг на 50 дней
    assert r["observations"] == 250 and r["coverage"]["common_start"] <= r["coverage"]["common_end"]


# ── volatility / annualization ──

def test_14_15_daily_and_annualized_vol():
    d = _dates(300)
    rets = _series(300, 7)
    r = dr.compute([_pos("A", 10, 100, rets, d)], now=NOW)
    ref = float(np.std(rets, ddof=1))
    assert abs(r["metrics"]["daily_volatility"] - ref) < 1e-9
    assert abs(r["metrics"]["annualized_volatility"] - ref * np.sqrt(cfg.TRADING_DAYS_YEAR)) < 1e-9


# ── VaR / CVaR (reference numpy) ──

def test_16_17_18_19_var_cvar_reference():
    d = _dates(500)
    rets = _series(500, 11, scale=0.02)
    r = dr.compute([_pos("A", 10, 100, rets, d)], now=NOW)
    arr = np.array(rets)
    for lvl, key in [(0.95, "95"), (0.99, "99")]:
        q = np.quantile(arr, 1 - lvl, method="linear")
        assert abs(r["metrics"][f"var_{key}"]["pct"] - (-q)) < 1e-9
        tail = arr[arr <= q]
        assert abs(r["metrics"][f"cvar_{key}"]["pct"] - (-tail.mean())) < 1e-9


def test_20_insufficient_tail_cvar99_hidden():
    d = _dates(120)                                    # 1% хвост = ~1 наблюдение < CVAR_MIN_TAIL
    r = dr.compute([_pos("A", 10, 100, _series(120, 3), d)], now=NOW)
    assert r["metrics"]["cvar_99"]["pct"] is None and r["metrics"]["cvar_99"]["confidence"] == "low"


# ── drawdown ──

def test_21_max_drawdown_reference():
    d = _dates(6)
    rets = [0.1, -0.2, -0.1, 0.05, 0.3, -0.05]
    r = dr.compute([_pos("A", 10, 100, rets, d, quality="good")], now=NOW)
    # ручной wealth index
    w = [1.0]
    for x in rets:
        w.append(w[-1] * (1 + x))
    peak, mdd = w[0], 0.0
    for x in w:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    # движок требует ≥60 наблюдений → тут проверяем функцию напрямую
    got = dr.max_drawdown(rets, d)
    assert abs(got["max_drawdown"] - mdd) < 1e-9 and got["max_drawdown"] <= 0


def test_22_no_recovery():
    rets = [0.05, -0.5, -0.1]                           # падение без восстановления
    got = dr.max_drawdown(rets, _dates(3))
    assert got["recovery_date"] is None


# ── downside deviation ──

def test_23_downside_deviation_reference():
    rets = [0.02, -0.03, 0.01, -0.04, 0.0]
    ref = np.sqrt(np.mean([min(x, 0) ** 2 for x in rets]))
    assert abs(dr.downside_deviation(rets) - ref) < 1e-12


# ── beta / correlation ──

def test_24_25_beta_correlation_reference():
    n = 300
    port = _series(n, 5, 0.01)
    bench = _series(n, 6, 0.012)
    got = dr.beta_correlation(port, bench)
    p, b = np.array(port), np.array(bench)
    cov = np.cov(p, b, ddof=1)[0, 1]
    beta_ref = cov / np.var(b, ddof=1)
    corr_ref = np.corrcoef(p, b)[0, 1]
    assert abs(got["beta"] - beta_ref) < 1e-9 and abs(got["correlation"] - corr_ref) < 1e-9


def test_2_fully_correlated():
    base = _series(300, 9)
    got = dr.beta_correlation(base, base)
    assert abs(got["correlation"] - 1.0) < 1e-9


def test_4_negatively_correlated():
    base = _series(300, 9)
    got = dr.beta_correlation(base, [-x for x in base])
    assert abs(got["correlation"] + 1.0) < 1e-9


def test_26_benchmark_zero_variance():
    got = dr.beta_correlation(_series(100, 1), [0.0] * 100)
    assert got["beta"] is None


def test_27_benchmark_insufficient_history():
    d = _dates(300)
    bench = {"ticker": "IMOEX", "type": "price_index", "returns": dict(zip(_dates(10, 740000), _series(10, 1)))}
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d)], benchmark=bench, now=NOW)
    assert r["benchmark"]["beta"] is None            # нет пересечения ≥ порога


# ── типы рядов / fallback / корпдействия ──

def test_28_mixed_return_types_caps_confidence():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d, rtype="total_return"),
                    _pos("B", 10, 100, _series(300, 2), d, rtype="raw_price_return")], now=NOW)
    assert r["confidence"] in ("medium", "low")
    assert any("смешение" in w for w in r["warnings"])


def test_29_fallback_series_warning():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d, rtype="raw_price_return", fb="fallback")], now=NOW)
    assert any("сыр" in w for w in r["warnings"]) and r["confidence"] in ("medium", "low")


def test_30_unresolved_corporate_action_excluded():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d),
                    _pos("CA", 10, 100, _series(300, 2), d, ca="unresolved")], now=NOW)
    assert "CA" in [e["ticker"] for e in r["excluded_positions"]]


def test_31_excluded_reason_human_readable():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d),
                    _pos("S", 10, 100, _series(300, 2), d, quality="stale")], now=NOW)
    reason = [e["exclusion_reason"] for e in r["excluded_positions"] if e["ticker"] == "S"][0]
    assert reason == "ряд устарел"
    for e in r["excluded_positions"]:                # без техкодов
        for bad in ("KeyError", "NaN", "None", "split-like", "undefined"):
            assert bad not in e["exclusion_reason"]


def test_32_33_future_and_invalid_returns():
    d = _dates(300)
    rets = _series(300, 1)
    pos = _pos("A", 10, 100, rets, d)
    pos["returns"]["2030-01-01"] = None                # None-значение
    pos["returns"]["bad"] = "xyz"                       # битое
    r = dr.compute([pos], now=NOW)
    assert r["metrics"] is not None                    # не падает, битые отброшены


# ── детерминизм / схема ──

def test_34_deterministic_rerun():
    d = _dates(300)
    args = [_pos("A", 10, 100, _series(300, 1), d), _pos("B", 20, 50, _series(300, 2), d)]
    import json
    a = dr.compute(args, now=NOW); b = dr.compute(args, now=NOW)
    strip = lambda r: {k: v for k, v in r.items() if k != "calculated_at"}
    assert json.dumps(strip(a), sort_keys=True) == json.dumps(strip(b), sort_keys=True)


def test_35_schema_validation():
    d = _dates(300)
    r = dr.compute([_pos("A", 10, 100, _series(300, 1), d)], now=NOW)
    assert dr.validate(r) == []


# ── §23 три эталонных портфеля + инварианты ──

def _invariants(r):
    m = r["metrics"]
    assert m["daily_volatility"] >= 0 and m["annualized_volatility"] >= 0
    assert m["max_drawdown"]["max_drawdown"] <= 1e-12
    for lvl in ("95", "99"):
        var, cv = m[f"var_{lvl}"]["pct"], m[f"cvar_{lvl}"]["pct"]
        if var is not None and cv is not None:
            assert abs(cv) + 1e-9 >= abs(var)
    assert 0 <= r["coverage"]["value_coverage_ratio"] <= 1
    assert r["coverage"]["common_start"] <= r["coverage"]["common_end"]
    b = r["benchmark"]
    if b and b["correlation"] is not None:
        assert -1 <= b["correlation"] <= 1
    if m["var_95"]["pct"] is not None:
        assert abs(m["var_95"]["rub"] - m["var_95"]["pct"] * m["covered_value"]) < 1e-6


def test_ref_portfolio_single():
    d = _dates(400)
    bench = {"ticker": "IMOEX", "type": "price_index", "returns": dict(zip(d, _series(400, 99, 0.012)))}
    r = dr.compute([_pos("SBER", 10, 300, _series(400, 1, 0.015), d)], benchmark=bench, now=NOW)
    _invariants(r); assert dr.validate(r) == []


def test_ref_portfolio_equal_weight_diversified():
    d = _dates(400)
    bench = {"ticker": "IMOEX", "type": "price_index", "returns": dict(zip(d, _series(400, 99, 0.012)))}
    pos = [_pos(f"T{i}", 10, 100, _series(400, 100 + i, 0.02), d) for i in range(8)]
    r = dr.compute(pos, benchmark=bench, now=NOW)
    _invariants(r); assert dr.validate(r) == []
    assert abs(sum(p["weight"] for p in r["included_positions"]) - 1.0) < 1e-9


def test_ref_portfolio_concentrated():
    d = _dates(400)
    bench = {"ticker": "IMOEX", "type": "price_index", "returns": dict(zip(d, _series(400, 99, 0.012)))}
    pos = [_pos("BIG", 100, 1000, _series(400, 1, 0.03), d),
           _pos("small1", 1, 50, _series(400, 2, 0.02), d),
           _pos("small2", 1, 50, _series(400, 3, 0.02), d)]
    r = dr.compute(pos, benchmark=bench, now=NOW)
    _invariants(r); assert dr.validate(r) == []
    big_w = [p["weight"] for p in r["included_positions"] if p["ticker"] == "BIG"][0]
    assert big_w > 0.9                                  # концентрация корректно отражена
