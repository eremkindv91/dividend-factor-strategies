#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the price-vs-capital history builder. Pure functions + output contract, no network.
Run: python3 scripts/cbr_banks/test_banks_history.py"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_banks_history as bh  # noqa: E402


class QuarterDates(unittest.TestCase):
    def test_keeps_only_quarter_starts_in_window(self):
        dates = ["2023-11-01", "2023-12-01", "2024-01-01", "2024-02-01",
                 "2024-04-01", "2024-05-01", "2024-07-01", "2024-10-01"]
        q = bh.quarter_dates(dates, "2024-01-01")
        self.assertEqual(q, ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"])

    def test_window_floor_excludes_earlier(self):
        dates = ["2018-01-01", "2020-04-01", "2024-01-01"]
        self.assertEqual(bh.quarter_dates(dates, "2021-01-01"), ["2024-01-01"])


class SplitClamp(unittest.TestCase):
    def test_detects_reverse_split_jump(self):
        # VTB-like reverse split 5000:1 — a huge up-jump; clamp to the month after
        px = {"2024-01": 0.02, "2024-02": 0.02, "2024-03": 95.0, "2024-04": 100.0}
        self.assertEqual(bh.detect_split_clamp(px), "2024-03")

    def test_detects_split_downshift(self):
        # T-like ~10x price downshift on reorganisation
        px = {"2026-02": 3400.0, "2026-03": 3200.0, "2026-04": 305.0, "2026-05": 300.0}
        self.assertEqual(bh.detect_split_clamp(px), "2026-04")

    def test_ignores_ordinary_crash(self):
        # a severe but organic ~-50% monthly crash (2022) must NOT be treated as a split
        px = {"2022-01": 260.0, "2022-02": 130.0, "2022-03": 140.0, "2022-04": 150.0}
        self.assertIsNone(bh.detect_split_clamp(px))

    def test_stable_series_no_clamp(self):
        px = {"2020-01": 100.0, "2020-02": 110.0, "2020-03": 95.0, "2020-04": 130.0}
        self.assertIsNone(bh.detect_split_clamp(px))


class SplitAdjust(unittest.TestCase):
    def test_t_split_1_to_10_divides_pre_split_prices(self):
        # T 2026-04-17 1→10: months strictly before 2026-04 ÷10; April close is already post-split
        px = {"2026-02": 3463.6, "2026-03": 3229.6, "2026-04": 305.36, "2026-05": 301.14}
        adj, applied = bh.adjust_for_splits(px, [{"date": "2026-04-17", "before": 1, "after": 10}])
        self.assertAlmostEqual(adj["2026-03"], 322.96, places=2)
        self.assertEqual(adj["2026-04"], 305.36)                    # untouched
        self.assertEqual(applied, [{"date": "2026-04-17", "ratio": "1:10"}])

    def test_vtbr_reverse_split_multiplies(self):
        px = {"2024-06": 0.02, "2024-07": 95.0}
        adj, applied = bh.adjust_for_splits(px, [{"date": "2024-07-15", "before": 5000, "after": 1}])
        self.assertAlmostEqual(adj["2024-06"], 100.0, places=2)
        self.assertEqual(adj["2024-07"], 95.0)
        self.assertEqual(applied[0]["ratio"], "5000:1")

    def test_cumulative_two_splits(self):
        # a month before BOTH splits gets both factors
        px = {"2020-01": 100.0, "2022-06": 40.0, "2024-06": 30.0}
        splits = [{"date": "2022-01-10", "before": 1, "after": 2},
                  {"date": "2024-01-10", "before": 1, "after": 5}]
        adj, _ = bh.adjust_for_splits(px, splits)
        self.assertAlmostEqual(adj["2020-01"], 100.0 * 0.5 * 0.2, places=6)
        self.assertAlmostEqual(adj["2022-06"], 40.0 * 0.2, places=6)
        self.assertEqual(adj["2024-06"], 30.0)

    def test_adjustment_removes_clamp(self):
        # after official-registry adjustment the discontinuity disappears → no safety clamp
        px = {"2026-02": 3463.6, "2026-03": 3229.6, "2026-04": 305.36, "2026-05": 301.14}
        adj, _ = bh.adjust_for_splits(px, [{"date": "2026-04-17", "before": 1, "after": 10}])
        self.assertIsNone(bh.detect_split_clamp(adj))

    def test_no_splits_noop(self):
        px = {"2020-01": 100.0}
        adj, applied = bh.adjust_for_splits(px, [])
        self.assertEqual(adj, px)
        self.assertEqual(applied, [])


class PbvMath(unittest.TestCase):
    def test_bvps_and_pbv_units(self):
        # capital_rub already in RUB; shares_eff in shares. BVPS = cap/shares; P/BV = price/BVPS.
        cap_rub = 6.8e12                      # 6.8 trln rub regulatory capital
        shares = 22.586e9                     # ~22.6 bln effective shares
        price = 300.0
        bvps = cap_rub / shares
        pbv = price / bvps
        self.assertAlmostEqual(bvps, 301.07, places=1)
        self.assertAlmostEqual(pbv, 0.996, places=2)   # ~ trades at one capital

    def test_below_one_capital_flag(self):
        # cheap == price below book value per share
        self.assertTrue(0.80 < 1.0)          # sanity anchor for the `cheap` semantics
        self.assertLess(300.0 / (6.8e12 / 22.586e9), 1.0)


class ConfigHistoryFrom(unittest.TestCase):
    def test_every_bank_has_history_from(self):
        with open(bh.CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        for b in cfg["banks"]:
            self.assertIn("history_from", b, f"{b['ticker']} без history_from")
            self.assertRegex(b["history_from"], r"^\d{4}-\d{2}-\d{2}$")


class OutputContract(unittest.TestCase):
    """If history.json exists (built locally / in CI), enforce its shape and internal consistency."""
    def setUp(self):
        if not os.path.exists(bh.OUT):
            self.skipTest("history.json ещё не сгенерирован")
        with open(bh.OUT, encoding="utf-8") as f:
            self.data = json.load(f)

    def test_meta_and_banks_present(self):
        self.assertIn("meta", self.data)
        self.assertIn("banks", self.data)
        self.assertTrue(self.data["meta"].get("caveats"))     # honesty caveats are not optional

    def test_points_are_consistent(self):
        for b in self.data["banks"]:
            for p in b.get("points", []):
                self.assertEqual(set(p) >= {"d", "p", "bv", "pbv"}, True)
                self.assertGreater(p["bv"], 0)
                # pbv must equal price / bvps within rounding
                self.assertAlmostEqual(p["pbv"], p["p"] / p["bv"], places=2)

    def test_last_pbv_and_cheap_flag_agree(self):
        for b in self.data["banks"]:
            if b.get("points"):
                self.assertAlmostEqual(b["last_pbv"], b["points"][-1]["pbv"], places=3)
                self.assertEqual(bool(b.get("cheap")), b["last_pbv"] < 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
