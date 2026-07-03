#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for bank valuation metric math. Pure functions, no network (cache pre-filled).
Run: python3 scripts/cbr_banks/test_banks_valuation.py"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_banks_valuation as bv  # noqa: E402


class BanksConfig(unittest.TestCase):
    def test_domrf_is_in_public_bank_valuation_universe_with_perimeter_warning(self):
        with open(bv.CONFIG, encoding="utf-8") as f:
            cfg = bv.json.load(f)

        dom = next((b for b in cfg["banks"] if b["ticker"] == "DOMRF"), None)

        self.assertIsNotNone(dom)
        self.assertEqual(dom["regnum"], 2312)
        self.assertIn("ДОМ.РФ", dom["expected_name"])
        self.assertIn("периметр", dom["perimeter_note"].lower())


class ProfitTTM(unittest.TestCase):
    def test_ttm_formula(self):
        # accum monthly form 102: ttm(D) = accum(D) + accum(FYprev) - accum(D-12m)
        # H1'26 = 800, FY'25 = 1500, H1'25 = 700  ->  ttm = 800 + 1500 - 700 = 1600
        dates = ["2025-01-01", "2025-06-01", "2026-01-01", "2026-06-01"]
        cache = {("102", 1, "2026-06-01"): 800.0, ("102", 1, "2026-01-01"): 1500.0,
                 ("102", 1, "2025-06-01"): 700.0}
        val, dt = bv.profit_ttm(1, dates, cache)
        self.assertEqual(val, 1600.0)
        self.assertEqual(dt, "2026-06-01")

    def test_ttm_missing_history_falls_back_to_accum(self):
        # no prior-year dates -> report accum(last), labeled (not a full ttm)
        dates = ["2026-04-01", "2026-06-01"]
        cache = {("102", 1, "2026-06-01"): 490.0}
        val, dt = bv.profit_ttm(1, dates, cache)
        self.assertEqual(val, 490.0)


class DivTTM(unittest.TestCase):
    def test_window_12m(self):
        today = date.today()
        inside = (today - timedelta(days=60)).isoformat()
        old = (today - timedelta(days=800)).isoformat()
        divs = [{"date": old, "value": 10.0}, {"date": inside, "value": 34.84},
                {"date": (today - timedelta(days=300)).isoformat(), "value": 5.0}]
        total, recs = bv.div_ttm(divs)
        self.assertAlmostEqual(total, 39.84, places=2)   # 34.84 + 5.0, old excluded
        self.assertEqual(len(recs), 2)

    def test_empty(self):
        self.assertEqual(bv.div_ttm([]), (0.0, []))


class Multiples(unittest.TestCase):
    def test_pbv_roe_units(self):
        # capital form-123 is in THOUSAND rub; mcap in rub. P/BV = mcap / (cap_ths*1000).
        mcap = 6.8e12                       # 6.8 trln rub
        cap_ths = 8_535_000_000             # 8.535 trln in thousands
        p_bv = round(mcap / (cap_ths * 1000), 3)
        self.assertAlmostEqual(p_bv, 0.797, places=2)
        prof_ths = 1_838_000_000            # 1.838 trln net profit ttm (thousands)
        roe = round(100 * prof_ths / cap_ths, 2)
        self.assertTrue(20 < roe < 25)      # Sber-like ~21.5%

    def test_year_ago(self):
        self.assertEqual(bv.year_ago("2026-06-01"), "2025-06-01")


class DivergenceFlag(unittest.TestCase):
    def test_relative_divergence(self):
        # >20% relative divergence between two sources is information, not error
        def diverges(a, b, thr=0.20):
            return a and b and abs(a - b) / max(abs(a), abs(b)) > thr
        self.assertTrue(diverges(22.0, 14.0))    # RSBU ROE 22 vs IFRS 14 -> flag
        self.assertFalse(diverges(23.0, 22.0))   # close -> no flag


if __name__ == "__main__":
    unittest.main(verbosity=2)
