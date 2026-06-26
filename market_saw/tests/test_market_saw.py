#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-тесты Phase Engine. Запуск: python -m unittest market_saw.tests.test_market_saw -v
   или: python market_saw/tests/test_market_saw.py"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "production"))
from market_saw import (  # noqa: E402
    calculate_historical_reach_probability, calculate_market_saw,
    classify_market_phase, format_pct,
)


def pts(closes, start="2020-01-01"):
    """Список {date, close} с последовательными датами (шаг 1 день)."""
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=i)).isoformat(), "close": float(c)}
            for i, c in enumerate(closes)]


def ramp(a, b, n):
    """n точек линейно от a до b (включительно)."""
    return [a + (b - a) * i / (n - 1) for i in range(n)]


class TestFormatPct(unittest.TestCase):
    def test_sign_and_one_decimal(self):
        self.assertEqual(format_pct(0.1712), "+17.1%")
        self.assertEqual(format_pct(-0.132), "-13.2%")
        self.assertEqual(format_pct(0.0), "+0.0%")
        self.assertEqual(format_pct(0.095), "+9.5%")


class TestClassify(unittest.TestCase):
    def test_rally_reduce_zone(self):           # +17% → reduce_zone
        self.assertEqual(classify_market_phase("up", 0.17)["risk_level"], "reduce_zone")

    def test_rally_strong_reduce_zone(self):    # +22% → strong_reduce_zone
        self.assertEqual(classify_market_phase("up", 0.22)["risk_level"], "strong_reduce_zone")

    def test_drawdown_buy_zone(self):           # -13% → buy_zone
        self.assertEqual(classify_market_phase("down", -0.13)["risk_level"], "buy_zone")

    def test_drawdown_strong_buy_zone(self):    # -18% → strong_buy_zone
        self.assertEqual(classify_market_phase("down", -0.18)["risk_level"], "strong_buy_zone")

    def test_boundaries(self):
        self.assertEqual(classify_market_phase("up", 0.05)["risk_level"], "neutral")
        self.assertEqual(classify_market_phase("up", 0.169)["risk_level"], "positive")
        self.assertEqual(classify_market_phase("down", -0.05)["risk_level"], "neutral")
        self.assertEqual(classify_market_phase("down", -0.129)["risk_level"], "watch")


class TestZigzagDirection(unittest.TestCase):
    def test_rise_over_threshold_confirms_up(self):     # рост >9.5% от минимума → up
        res = calculate_market_saw(pts(ramp(100, 112, 40)))
        self.assertEqual(res["current_phase"]["direction"], "up")

    def test_fall_over_threshold_confirms_down(self):   # падение >9.5% от максимума → down
        res = calculate_market_saw(pts(ramp(100, 88, 40)))
        self.assertEqual(res["current_phase"]["direction"], "down")

    def test_completed_up_wave_then_current_down(self):
        # вверх 100→120 (+20%), затем вниз 120→100 (-16.7%): завершённая up-волна, текущая фаза down
        closes = ramp(100, 120, 30) + ramp(120, 100, 30)[1:]
        res = calculate_market_saw(pts(closes))
        self.assertEqual(res["current_phase"]["direction"], "down")
        ups = [w for w in res["waves"] if w["direction"] == "up"]
        self.assertGreaterEqual(len(ups), 1)
        self.assertGreater(ups[0]["amplitude_pct"], 0)

    def test_no_lookahead_current_wave_excluded(self):
        # завершённая up-волна (100→120, подтверждена спадом -12.5%), затем НЕЗАВЕРШЁННЫЙ
        # отскок +3.8% (<9.5%): текущая падающая волна не подтверждена и НЕ попадает в waves
        closes = ramp(100, 120, 30) + ramp(120, 105, 20)[1:] + ramp(105, 109, 5)[1:]
        res = calculate_market_saw(pts(closes))
        self.assertEqual(res["current_phase"]["direction"], "down")     # текущая фаза — падение от 120
        self.assertEqual(len(res["waves"]), 1)                          # лишь одна ЗАВЕРШЁННАЯ волна
        self.assertEqual(res["waves"][-1]["direction"], "up")          # и это up-волна 100→120
        self.assertNotIn("down", [w["direction"] for w in res["waves"]])  # незавершённый спад исключён


class TestReachProbability(unittest.TestCase):
    def test_no_waves_returns_none(self):
        self.assertIsNone(calculate_historical_reach_probability([], "up", 0.1))

    def test_no_waves_of_direction_returns_none(self):
        waves = [{"direction": "down", "amplitude_pct": -0.2}]
        self.assertIsNone(calculate_historical_reach_probability(waves, "up", 0.1))

    def test_survival_fraction(self):
        waves = [{"direction": "up", "amplitude_pct": a} for a in (0.10, 0.20, 0.30, 0.40)]
        # волн с амплитудой ≥0.25 — две из четырёх
        self.assertAlmostEqual(calculate_historical_reach_probability(waves, "up", 0.25), 0.5)
        # все ≥0.05
        self.assertAlmostEqual(calculate_historical_reach_probability(waves, "up", 0.05), 1.0)

    def test_down_uses_magnitude(self):
        waves = [{"direction": "down", "amplitude_pct": a} for a in (-0.10, -0.20, -0.30)]
        self.assertAlmostEqual(calculate_historical_reach_probability(waves, "down", 0.15), 2 / 3)


class TestValidation(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            calculate_market_saw([])

    def test_nonpositive_close_raises(self):
        with self.assertRaises(ValueError):
            calculate_market_saw(pts([100, 0, 100]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
