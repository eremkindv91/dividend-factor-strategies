# -*- coding: utf-8 -*-
"""Тесты обобщённого backtest-движка, method comparison, sector risk contribution и edge-кейсов
EWMA/bootstrap/Cornish-Fisher (дифференцированный вердикт v1.1+v2, продолжение). Детерминированно."""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import daily_risk as dr  # noqa: E402
import daily_config as cfg  # noqa: E402


def _series(n, seed, scale=0.01, mean_shift=0.0):
    return list(np.random.default_rng(seed).normal(mean_shift, scale, n))


# ── one dominant position / equal-weight (risk contribution edge cases) ──

def test_one_dominant_position():
    matrix = {"A": _series(300, 1, 0.02), "B": _series(300, 2, 0.02)}
    rc = dr.risk_contribution(matrix, {"A": 0.95, "B": 0.05}, ["A", "B"])
    assert rc["ok"]
    a_row = next(r for r in rc["rows"] if r["secid"] == "A")
    assert a_row["pcr"] > 0.8                                # доминирующая позиция несёт основной риск


def test_equal_weight_portfolio_risk_contribution():
    matrix = {"A": _series(300, 1, 0.02), "B": _series(300, 2, 0.02), "C": _series(300, 3, 0.02)}
    w = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    rc = dr.risk_contribution(matrix, w, ["A", "B", "C"])
    assert rc["ok"]
    pcrs = [r["pcr"] for r in rc["rows"]]
    assert abs(sum(pcrs) - 1.0) < 1e-9
    for p in pcrs:                                            # некоррелированные равновесные — примерно равный вклад
        assert 0.2 < p < 0.5


def test_sector_risk_contribution_aggregation():
    matrix = {"A": _series(300, 1, 0.02), "B": _series(300, 2, 0.02), "C": _series(300, 3, 0.02)}
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    rc = dr.risk_contribution(matrix, w, ["A", "B", "C"])
    sectors = {"A": "Нефть", "B": "Нефть", "C": "Банки"}
    by_sec = dr.sector_risk_contribution(rc["rows"], sectors)
    total = sum(s["risk_contribution_pct"] for s in by_sec)
    assert abs(total - rc["pcr_sum"]) < 1e-9                 # агрегация не теряет и не удваивает риск
    oil = next(s for s in by_sec if s["sector"] == "Нефть")
    a = next(r for r in rc["rows"] if r["secid"] == "A")
    b = next(r for r in rc["rows"] if r["secid"] == "B")
    assert abs(oil["risk_contribution_pct"] - (a["pcr"] + b["pcr"])) < 1e-9


# ── Kupiec: exact / zero / excessive ──

def test_kupiec_zero_exceptions():
    kp = dr.kupiec_pof(breaches=0, obs=500, level=0.95)
    assert kp["ok"]
    assert kp["pvalue"] < 0.05 and kp["reject"]               # 0 пробоев из 500 при ожидаемых 25 — тоже подозрительно мало


def test_kupiec_exact_expected_count():
    kp = dr.kupiec_pof(breaches=25, obs=500, level=0.95)      # ровно 5%
    assert kp["ok"] and not kp["reject"] and kp["lr"] < 1e-9


def test_kupiec_excessive_exceptions():
    kp = dr.kupiec_pof(breaches=100, obs=500, level=0.95)     # 20% вместо 5%
    assert kp["reject"] and kp["pvalue"] < 0.01


# ── Christoffersen: independent vs clustered ──

def test_christoffersen_independent_not_rejected():
    rng = np.random.default_rng(7)
    seq = (rng.random(1000) < 0.05).astype(int).tolist()       # разбросанные, не кластеризованные
    ch = dr.christoffersen_independence(seq)
    assert ch["ok"] and not ch["reject"]


def test_christoffersen_clustered_rejected():
    seq = [0] * 300 + [1] * 25 + [0] * 300                     # все пробои одним блоком
    ch = dr.christoffersen_independence(seq)
    assert ch["ok"] and ch["reject"]


# ── rolling backtest: no look-ahead, insufficient forecasts ──

def test_rolling_backtest_no_lookahead():
    rets = _series(500, 1, 0.02)
    seen_max_index = {"v": -1}
    calls = []

    def fn(window):
        calls.append(len(window))
        return -np.quantile(window, 0.05)

    dr.rolling_backtest(rets, 0.95, 252, fn)
    assert all(c == 252 for c in calls)                        # всегда фиксированное окно прошлого
    assert len(calls) == 500 - 252


def test_rolling_backtest_insufficient_forecasts():
    rets = _series(300, 1, 0.02)                                # 300-252=48 < MIN_BACKTEST_FORECASTS(100)
    bt = dr.rolling_backtest(rets, 0.95, 252, lambda w: -np.quantile(w, 0.05))
    assert not bt["ok"] and bt["status"] == "insufficient_backtest_history"
    assert bt["obs"] == 48


def test_rolling_backtest_skips_none_forecasts():
    rets = _series(500, 1, 0.02)
    calls = {"n": 0}

    def fn(w):
        calls["n"] += 1
        return None if calls["n"] % 2 == 0 else -np.quantile(w, 0.05)

    bt = dr.rolling_backtest(rets, 0.95, 252, fn)
    assert bt["skipped_gate"] > 0
    assert bt["obs"] + bt["skipped_gate"] == 500 - 252


def test_backtest_method_iid_calibrated():
    rets = _series(1000, 21, 0.02)
    bt = dr.backtest_method(rets, 0.95, "historical", window=252)
    assert bt["ok"] and bt["calibrated"] and bt["policy"] == "rolling" and bt["horizon_days"] == 1


# ── EWMA: recursion / lambda применяется из config ──

def test_ewma_recursion_matches_manual():
    r = [0.01, -0.02, 0.03, -0.01, 0.02]
    lam = 0.9
    m = sum(r) / len(r)
    manual = (r[0] - m) ** 2
    for x in r[1:]:
        manual = lam * manual + (1 - lam) * (x - m) ** 2
    expected = dr._Z[0.95] * math.sqrt(manual)
    assert abs(dr.var_ewma(r, 0.95, lam) - expected) < 1e-12


def test_ewma_uses_config_lambda_by_default():
    r = _series(200, 5, 0.02)
    assert dr.var_ewma(r, 0.95) == dr.var_ewma(r, 0.95, cfg.EWMA_LAMBDA)


def test_ewma_lambda_bounds_sane():
    assert 0.0 < cfg.EWMA_LAMBDA < 1.0                          # λ вне (0,1) не имеет смысла
    r = _series(200, 5, 0.02)
    lo = dr.var_ewma(r, 0.95, 0.5)                               # короткая память
    hi = dr.var_ewma(r, 0.95, 0.99)                              # долгая память
    assert lo is not None and hi is not None and lo != hi


# ── bootstrap: tail behavior, block vs iid ──

def test_bootstrap_tail_behavior_99_worse_than_95():
    r = _series(500, 9, 0.02)
    b95 = dr.var_bootstrap(r, 0.95, seed=1)
    b99 = dr.var_bootstrap(r, 0.99, seed=1)
    assert b95["ok"] and b99["ok"]
    assert b99["var"] > b95["var"]                               # 99% хвост хуже 95%


def test_bootstrap_uses_iid_when_no_clustering():
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.01, 400).tolist()                        # iid шум — без ARCH-эффекта
    bs = dr.var_bootstrap(r, 0.95, seed=1)
    assert bs["ok"] and bs["bootstrap_method"] == "iid"


def test_bootstrap_uses_block_when_volatility_clusters():
    rng = np.random.default_rng(4)
    r = []
    vol = 0.005
    for i in range(600):
        if i % 40 == 0:
            vol = 0.04 if vol < 0.02 else 0.005                  # переключение режима волатильности блоками
        r.append(float(rng.normal(0, vol)))
    bs = dr.var_bootstrap(r, 0.95, seed=1)
    assert bs["ok"]
    assert bs["autocorr_sq_lag1"] >= cfg.MC_AUTOCORR_BLOCK_THRESHOLD
    assert bs["bootstrap_method"] == "block" and bs["block_length"] == cfg.MC_BLOCK_LENGTH


# ── Cornish-Fisher: неустойчивый excess kurtosis ──

def test_cornish_fisher_unstable_kurtosis_blocked():
    rng = np.random.default_rng(2)
    r = rng.normal(0, 0.01, 300).tolist()
    r[10] = 5.0                                                   # экстремальный выброс → огромный excess kurtosis
    cf = dr.var_cornish_fisher(r, 0.95)
    assert not cf["ok"]
    assert cf["reason"] in ("unstable_kurtosis", "numerical_instability", "invariant_violation")
    assert cf["var"] is None


# ── method comparison: схема, нейтральность, фиксированный порядок ──

def test_method_comparison_schema_and_order():
    rets = _series(700, 1, 0.02)
    point = {"historical": 0.02, "normal": 0.021, "ewma": 0.022, "cornish_fisher": None, "bootstrap": 0.019}
    rows = dr.method_comparison(rets, point, window=252)
    assert [r["method"] for r in rows] == list(dr.METHOD_META)   # фиксированный порядок, не по p-value
    for r in rows:
        for f in ("method", "label", "assumptions", "current_estimate", "available", "backtest", "verdict"):
            assert f in r
    cf_row = next(r for r in rows if r["method"] == "cornish_fisher")
    assert cf_row["available"] is False                          # точечная оценка недоступна (None) → отражено честно


def test_method_comparison_verdict_neutral_text():
    rets = _series(700, 1, 0.02)
    point = {"historical": 0.02, "normal": 0.021, "ewma": 0.022, "cornish_fisher": None, "bootstrap": 0.019}
    rows = dr.method_comparison(rets, point, window=252)
    for r in rows:
        assert r["verdict"] in ("VaR откалиброван приемлемо", "VaR занижает риск",
                                "VaR завышает риск (слишком консервативен)",
                                "Наблюдается кластеризация плохих дней",
                                "Истории недостаточно для надёжной проверки")


# ── интеграция: полный compute() отдаёт все новые поля согласованно ──

def _pos(t, q, p, rets, d, sector="Тест"):
    return {"ticker": t, "secid": t, "quantity": q, "price": p, "returns": dict(zip(d, rets)),
            "quality_status": "good", "corporate_action_status": "resolved",
            "return_type": "adjusted_price_return", "fallback_status": "none", "sector": sector}


def test_compute_full_v2_fields_present():
    from datetime import datetime, timedelta, timezone
    start = datetime(2023, 1, 2)
    d = [(start + timedelta(days=i)).date().isoformat() for i in range(700)]   # 700 уникальных дат подряд
    r = dr.compute([_pos("A", 10, 100, _series(700, 1), d, "Нефть"),
                    _pos("B", 20, 50, _series(700, 2), d, "Банки")],
                   now=datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert r["risk_contribution"]["ok"] and "by_sector" in r["risk_contribution"]
    assert set(r["var_methods"]["95"]) == {"historical", "normal", "ewma", "cornish_fisher", "bootstrap"}
    assert "cornish_fisher" in r["method_diagnostics"]["95"] and "bootstrap" in r["method_diagnostics"]["95"]
    assert r["backtest"]["ok"] and r["backtest"]["policy"] == "rolling"
    assert len(r["method_comparison"]) == 5
    assert r["ewma_meta"]["lambda"] == cfg.EWMA_LAMBDA
    assert dr.validate(r) == []
