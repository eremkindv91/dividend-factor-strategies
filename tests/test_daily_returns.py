# -*- coding: utf-8 -*-
"""Тесты централизованных формул доходностей (Фаза 3). Детерминированно, reference-значения."""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import daily_returns as dr  # noqa: E402


def test_simple_returns_reference():
    r = dr.simple_returns([100.0, 110.0, 99.0])
    assert abs(r[0] - 0.10) < 1e-12
    assert abs(r[1] - (99.0 / 110.0 - 1)) < 1e-12


def test_log_returns_reference():
    r = dr.log_returns([100.0, 110.0])
    assert abs(r[0] - math.log(1.1)) < 1e-12


def test_simple_vs_log_differ():
    assert dr.simple_returns([100.0, 200.0]) != dr.log_returns([100.0, 200.0])


def test_gap_yields_none_not_zero():
    r = dr.simple_returns([100.0, None, 120.0])
    assert r[0] is None and r[1] is None            # пропуск → None, НЕ 0-доходность
    assert dr.drop_none(r) == []


def test_nonpositive_price_none():
    assert dr.simple_returns([100.0, 0.0])[0] is None
    assert dr.simple_returns([-5.0, 100.0])[0] is None


def test_total_return_includes_dividend():
    # цена не изменилась, но выплачен дивиденд 5 → total return = 5/100
    tr = dr.total_returns([100.0, 100.0], [0.0, 5.0])
    assert abs(tr[0] - 0.05) < 1e-12


def test_total_return_no_dividend_equals_price_return():
    pr = dr.simple_returns([100.0, 110.0])
    tr = dr.total_returns([100.0, 110.0], [0.0, 0.0])
    assert abs(pr[0] - tr[0]) < 1e-12
