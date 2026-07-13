# -*- coding: utf-8 -*-
"""Тесты v1.1 (Risk Contribution + Concentration) и v2 (методы VaR + backtest) Daily Risk Engine."""
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


def _series(n, seed, scale=0.01):
    return list(np.random.default_rng(seed).normal(0, scale, n))


# ── v1.1: risk contribution ──

def test_risk_contribution_sums():
    d = _dates(400)
    matrix = {"A": _series(400, 1, 0.02), "B": _series(400, 2, 0.015), "C": _series(400, 3, 0.03)}
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    rc = dr.risk_contribution(matrix, w, ["A", "B", "C"])
    assert rc["ok"]
    crc_sum = sum(r["crc"] for r in rc["rows"])
    assert abs(crc_sum - rc["sigma_annual"]) < 1e-9        # Σ CRC ≈ σ_p
    assert abs(rc["pcr_sum"] - 1.0) < 1e-9                 # Σ PCR ≈ 1
    assert rc["crc_sum_check"]


def test_risk_contribution_vs_numpy():
    matrix = {"A": _series(300, 5, 0.02), "B": _series(300, 6, 0.02)}
    w = {"A": 0.6, "B": 0.4}
    rc = dr.risk_contribution(matrix, w, ["A", "B"], annualize=False)
    X = np.array([matrix["A"], matrix["B"]]); S = np.cov(X, ddof=1); wv = np.array([0.6, 0.4])
    sigma = float(np.sqrt(wv @ S @ wv))
    assert abs(rc["sigma_annual"] - sigma) < 1e-9


def test_risk_contribution_allows_negative():
    base = _series(300, 9, 0.02); noise = _series(300, 10, 0.02)
    matrix = {"A": base, "B": [-0.8 * base[i] + 0.2 * noise[i] for i in range(300)]}   # ЧАСТИЧНЫЙ хедж (σ_p>0)
    rc = dr.risk_contribution(matrix, {"A": 0.5, "B": 0.5}, ["A", "B"])
    assert rc["ok"]                                        # не падает; отрицательные вклады не обрезаны
    assert any(r["pcr"] < 0 for r in rc["rows"])           # хедж даёт отрицательный вклад в риск


# ── v1.1: concentration ──

def test_concentration_hhi():
    c = dr.concentration({"A": 0.5, "B": 0.3, "C": 0.2})
    assert abs(c["hhi"] - (0.25 + 0.09 + 0.04)) < 1e-12
    assert c["largest"] == 0.5 and abs(c["top3"] - 1.0) < 1e-12
    assert abs(c["effective_n"] - 1 / 0.38) < 1e-9


def test_concentration_sectors():
    c = dr.concentration({"A": 0.5, "B": 0.3, "C": 0.2}, {"A": "Нефть", "B": "Нефть", "C": "Банки"})
    assert abs(c["sector_hhi"] - (0.8 ** 2 + 0.2 ** 2)) < 1e-12


# ── v2: методы VaR ──

def test_var_methods_sane_and_ordered():
    r = _series(500, 11, 0.02)
    hist = dr.var_cvar(r, 0.95)["var"]
    for f in [dr.var_normal(r, 0.95), dr.var_ewma(r, 0.95), dr.var_cornish_fisher(r, 0.95), dr.var_monte_carlo(r, 0.95)]:
        assert f is not None and f > 0
    assert hist > 0


def test_monte_carlo_deterministic():
    r = _series(400, 3, 0.02)
    assert dr.var_monte_carlo(r, 0.95, seed=42) == dr.var_monte_carlo(r, 0.95, seed=42)


def test_cornish_fisher_clamps_on_short():
    assert dr.var_cornish_fisher(_series(6, 1), 0.95) is None    # <8 наблюдений


def test_var_normal_reference():
    r = _series(500, 7, 0.02)
    ref = -(np.mean(r) - 1.6448536269514722 * np.std(r, ddof=1))
    assert abs(dr.var_normal(r, 0.95) - ref) < 1e-9


# ── v2: backtest (Kupiec / Christoffersen) ──

def test_kupiec_calibrated_not_reject():
    kp = dr.kupiec_pof(breaches=50, obs=1000, level=0.95)     # ровно ожидаемые 5%
    assert kp["ok"] and not kp["reject"] and kp["lr"] < 1e-6


def test_kupiec_miscalibrated_reject():
    kp = dr.kupiec_pof(breaches=150, obs=1000, level=0.95)    # 15% пробоев — слишком много
    assert kp["reject"] and kp["pvalue"] < 0.05


def test_christoffersen_clustering_reject():
    seq = [0] * 200 + [1] * 20                                # все пробои кластером
    ch = dr.christoffersen_independence(seq)
    assert ch["ok"] and ch["reject"]


def test_var_backtest_iid_calibrated():
    r = _series(1000, 21, 0.02)
    bt = dr.var_backtest(r, 0.95, window=252)
    assert bt["ok"] and 0.02 < bt["freq"] < 0.10             # ~5% на iid
    assert bt["kupiec"]["ok"] and bt["verdict"] == "калиброван"


# ── интеграция в compute ──

def _pos(t, q, p, rets, d):
    return {"ticker": t, "secid": t, "quantity": q, "price": p, "returns": dict(zip(d, rets)),
            "quality_status": "good", "corporate_action_status": "resolved",
            "return_type": "adjusted_price_return", "fallback_status": "none", "sector": "Тест"}


def test_compute_attaches_advanced():
    d = _dates(400)
    r = dr.compute([_pos("A", 10, 100, _series(400, 1), d), _pos("B", 20, 50, _series(400, 2), d)], now=NOW)
    assert r["risk_contribution"]["ok"] and abs(r["risk_contribution"]["pcr_sum"] - 1.0) < 1e-9
    assert 0 <= r["concentration"]["hhi"] <= 1
    assert r["var_methods"]["95"]["historical"] > 0 and r["var_methods"]["95"]["monte_carlo_normal"] > 0
    assert r["backtest"]["ok"]
    assert dr.validate(r) == []
