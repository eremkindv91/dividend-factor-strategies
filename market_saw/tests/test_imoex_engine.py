# -*- coding: utf-8 -*-
"""Тесты IMOEX-контура: эталонные уровни 87/81.9, зоны buy/neutral/fix, live snapshot не
подтверждает экстремумы, параметры не смешиваются с MCFTR."""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MS, "shared"))
sys.path.insert(0, os.path.join(MS, "production"))
import imoex_engine as ie  # noqa: E402


def _series(pairs):
    return [{"date": d, "close": float(c)} for d, c in pairs]


# ряд, где подтверждаются high=100 (день 2) и low=70 (день 4):
REF = _series([("2020-01-01", 90), ("2020-01-02", 100),
               ("2020-01-03", 92), ("2020-01-04", 70), ("2020-01-05", 76)])


def test_reference_levels_87_and_819():
    r = ie.compute_imoex(REF)
    assert r["last_confirmed_high"] == {"date": "2020-01-02", "price": 100.0}
    assert r["last_confirmed_low"] == {"date": "2020-01-04", "price": 70.0}
    assert r["levels"] == {"buy": 87.0, "fix": 81.9}       # 100·0.87=87 ; 70·1.17=81.9


def test_zone_buy():
    r = ie.compute_imoex(REF, live_price=86.0)              # 86 ≤ 87 → buy
    assert r["current_state"]["zone"] == "buy"
    assert r["current_state"]["zone_label"] == "Зона покупки"


def test_zone_fix():
    r = ie.compute_imoex(REF, live_price=90.0)              # 90 ≥ 81.9 → fix
    assert r["current_state"]["zone"] == "fix"
    assert r["current_state"]["zone_label"] == "Зона фиксации"


# узкий диапазон (high=100, low=80): buy=87, fix=93.6 → buy<fix, есть НАСТОЯЩАЯ neutral-зона [87,93.6]
NARROW = _series([("2020-01-01", 90), ("2020-01-02", 100),
                  ("2020-01-03", 90), ("2020-01-04", 80), ("2020-01-05", 86)])


def test_zone_neutral_between_levels():
    r = ie.compute_imoex(NARROW)                            # уровни buy=87, fix=93.6
    assert r["levels"] == {"buy": 87.0, "fix": 93.6}
    assert ie.classify_zone(90.0, 87.0, 93.6) == "neutral"   # 90 между уровнями (узкий диапазон)


def test_classify_zone_direct():
    # непересекающиеся уровни (узкий диапазон): buy=87 < fix=93.6
    assert ie.classify_zone(86, 87, 93.6) == "buy"
    assert ie.classify_zone(95, 87, 93.6) == "fix"
    assert ie.classify_zone(90, 87, 93.6) == "neutral"


def test_overlapping_levels_buy_wins():
    # широкий диапазон (REF high=100/low=70): buy=87 > fix=81.9 → перекрытие [81.9,87] → buy (детерминированно)
    assert ie.classify_zone(84, 87.0, 81.9) == "buy"


def test_distance_formulas_signs():
    r = ie.compute_imoex(REF, live_price=84.0)
    cs = r["current_state"]
    # 84/87-1 <0 (ниже уровня покупки), 84/81.9-1 >0 (выше уровня фиксации)
    assert cs["distance_to_buy_pct"] < 0
    assert cs["distance_to_fix_pct"] > 0
    # move_from_high 84/100-1<0, move_from_low 84/70-1>0
    assert cs["move_from_high_pct"] < 0 and cs["move_from_low_pct"] > 0


def test_live_price_does_not_confirm_extremes():
    # экстремальная live-цена НЕ должна порождать новый подтверждённый экстремум
    r_no = ie.compute_imoex(REF)
    r_live = ie.compute_imoex(REF, live_price=999.0)
    assert r_no["extremes"] == r_live["extremes"]          # extremes идентичны
    assert r_no["waves"] == r_live["waves"]                # волны идентичны
    assert r_live["current_state"]["price"] == 999.0       # но текущая позиция обновилась
    assert r_live["current_state"]["price_type"] == "live"


def test_official_price_when_no_live():
    r = ie.compute_imoex(REF)
    assert r["current_state"]["price_type"] == "official"
    assert r["current_state"]["price"] == 76.0             # последний CLOSE


def test_imoex_uses_own_thresholds_not_mcftr():
    r = ie.compute_imoex(REF)
    assert r["reversal_threshold"] == 0.07                 # НЕ 0.095 (MCFTR)
    assert r["buy_drawdown"] == 0.13 and r["fix_rally"] == 0.17
    assert r["index_type"] == "price" and r["methodology"] == "price_saw"
    assert "current_phase" not in r                        # не методика MCFTR


def test_levels_not_crossing_buy_below_high_fix_above_low():
    r = ie.compute_imoex(REF)
    assert r["levels"]["buy"] < r["last_confirmed_high"]["price"]
    assert r["levels"]["fix"] > r["last_confirmed_low"]["price"]
