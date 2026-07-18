# -*- coding: utf-8 -*-
"""Тесты общего zigzag_engine: anti-look-ahead + бит-идентичность каноническому MCFTR-движку
(доказывает миграцию без изменения результатов — production-путь MCFTR не переписан)."""
import os
import random
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MS, "shared"))
sys.path.insert(0, os.path.join(MS, "production"))
import zigzag_engine as zz  # noqa: E402
import market_saw as ms  # noqa: E402


def _series(prices, start="2020-01-01"):
    d = date.fromisoformat(start)
    out = []
    for p in prices:
        out.append({"date": d.isoformat(), "close": float(p)})
        d += timedelta(days=1)
    return out


# ── эквивалентность каноническому MCFTR (миграция без изменения результатов) ──

def test_zigzag_equivalent_to_canonical_mcftr():
    random.seed(42)
    pts, price = [], 1000.0
    d = date(2004, 1, 1)
    for _ in range(700):
        price *= (1 + random.uniform(-0.03, 0.032))
        pts.append({"date": d.isoformat(), "close": round(price, 2)})
        d += timedelta(days=1)
    canon = ms.calculate_market_saw(pts)
    shared = zz.run_zigzag(pts, ms.REVERSAL_THRESHOLD)
    assert shared["extremes"] == canon["extremes"]        # бит-идентично
    assert shared["waves"] == canon["waves"]
    assert len(shared["extremes"]) > 5                     # не тривиально пусто


# ── anti-look-ahead ──

def test_candidate_high_not_confirmed_until_threshold_drop():
    # мелкие колебания < 7% в обе стороны → ни одного подтверждения (мин. движение недостаточно)
    z = zz.run_zigzag(_series([100, 103, 105, 102, 101, 104]), 0.07)
    assert z["extremes"] == []                             # ни разу не набрали ±7%
    assert z["cand_high"]["close"] == 105                  # кандидат-максимум отслеживается


def test_high_confirmed_after_threshold_drop_with_correct_date():
    # [100,108,100]: 100→108 (+8%) подтверждает low=100@день0 и ставит up; 108→100 (−7.4%)
    # подтверждает high=108@день1. Проверяем именно подтверждённый high (дата/цена).
    z = zz.run_zigzag(_series([100, 108, 100]), 0.07)
    highs = [e for e in z["extremes"] if e["type"] == "high"]
    assert highs and highs[-1] == {"date": "2020-01-02", "price": 108.0, "type": "high"}
    assert any(e["type"] == "low" and e["price"] == 100.0 for e in z["extremes"])


def test_low_confirmed_after_threshold_rebound():
    # вниз до 70, отскок до 76 (+8.6%) → low=70 подтверждён
    z = zz.run_zigzag(_series([100, 92, 70, 76]), 0.07)
    lows = [e for e in z["extremes"] if e["type"] == "low"]
    assert lows and lows[-1]["price"] == 70.0


def test_future_data_does_not_move_confirmed_pivot():
    base = [90, 100, 96, 92, 95]           # подтверждает high=100@день2
    z1 = zz.run_zigzag(_series(base), 0.07)
    z2 = zz.run_zigzag(_series(base + [110, 120]), 0.07)   # будущие данные добавлены
    first_high1 = next(e for e in z1["extremes"] if e["type"] == "high")
    first_high2 = next(e for e in z2["extremes"] if e["type"] == "high")
    assert first_high1 == first_high2      # дата/цена ранее подтверждённого pivot неизменны


def test_incremental_equals_batch():
    # последовательное добавление по одной точке даёт тот же результат, что разовый прогон
    prices = [100, 108, 99, 92, 101, 110, 100, 90, 97]
    full = zz.run_zigzag(_series(prices), 0.07)
    # префиксный прогон до предпоследней точки не должен «знать» о будущей
    prefix = zz.run_zigzag(_series(prices[:6]), 0.07)
    assert full["extremes"][:len(prefix["extremes"])] == prefix["extremes"]


def test_empty_and_invalid_raise():
    import pytest
    with pytest.raises(ValueError):
        zz.run_zigzag([], 0.07)
    with pytest.raises(ValueError):
        zz.run_zigzag([{"date": "2020-01-01", "close": 0}], 0.07)


def test_last_confirmed_helper():
    z = zz.run_zigzag(_series([90, 100, 96, 92, 70, 76]), 0.07)
    hi = zz.last_confirmed(z["extremes"], "high")
    lo = zz.last_confirmed(z["extremes"], "low")
    assert hi and hi["type"] == "high"
    assert lo and lo["type"] == "low"
    assert zz.last_confirmed([], "high") is None
