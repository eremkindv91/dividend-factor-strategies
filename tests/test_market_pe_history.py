#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты агрегации истории P/E рынка.

Покрывают три вещи, на которых расчёт ломается молча и незаметно:
дедупликацию преф-классов (иначе знаменатель двоится), лаг раскрытия отчётности
(иначе look-ahead bias) и базу расчёта покрытия (иначе «сверено 72%» врёт).
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from build_market_pe import collapse_to_issuers, issuer_income          # noqa: E402
from build_market_pe_history import (                                    # noqa: E402
    COVERAGE_MIN, aggregate_month, earnings_available_at, month_last_day, quantile, shift,
)


def rec(fy, value, verified=False):
    return {"fy": fy, "value": value,
            "verification_status": "verified" if verified else None,
            "source": "verified_ifrs_seed" if verified else "smartlab"}


class DisclosureLag(unittest.TestCase):
    """Прибыль за год не может быть известна рынку до её публикации."""

    def test_not_available_before_april_of_next_year(self):
        self.assertIsNone(earnings_available_at([rec(2024, 100)], date(2025, 3, 31)))

    def test_available_from_april_of_next_year(self):
        got = earnings_available_at([rec(2024, 100)], date(2025, 4, 1))
        self.assertEqual(got["fy"], 2024)

    def test_picks_latest_disclosed_not_latest_existing(self):
        rows = [rec(2023, 50), rec(2024, 100), rec(2025, 200)]
        got = earnings_available_at(rows, date(2025, 6, 30))
        self.assertEqual(got["fy"], 2024, "FY2025 ещё не опубликован — брать его значит смотреть в будущее")

    def test_empty_history(self):
        self.assertIsNone(earnings_available_at([], date(2025, 6, 30)))


class DualClassDedup(unittest.TestCase):
    """Об. и прив. акции одного эмитента: капитализация обе, прибыль — одна."""

    hist = {"SBER": [rec(2024, 1000)], "SBERP": [rec(2024, 1000)]}
    base_of = {"SBER": "SBER", "SBERP": "SBER"}

    def test_earnings_counted_once_cap_counted_twice(self):
        p = aggregate_month("2025-06", ["SBER", "SBERP"],
                            {"SBER": 8000.0, "SBERP": 2000.0}, self.hist, self.base_of)
        self.assertEqual(p["earnings"], 1000, "прибыль эмитента должна войти ровно один раз")
        self.assertEqual(p["market_cap"], 10000, "капитализация обоих классов входит в числитель")
        self.assertEqual(p["constituents_used"], 1)
        self.assertAlmostEqual(p["pe"], 10.0)

    def test_issuer_capitalization_is_sum_of_share_classes(self):
        """Правдоподобие прибыли проверяется против капитализации ЭМИТЕНТА, а не одного класса.

        Преф — малая доля компании: сравнив прибыль всего Сбера с капитализацией одних только
        SBERP, проверка сочла бы нормальную запись ошибкой единиц и выбросила бы её.
        """
        hist = {"SBER": [rec(2024, 3000)], "SBERP": [rec(2024, 3000)]}
        p = aggregate_month("2025-06", ["SBER", "SBERP"],
                            {"SBER": 20000.0, "SBERP": 1000.0}, hist, self.base_of)
        self.assertEqual(p["constituents_used"], 1)
        self.assertEqual(p["earnings"], 3000, "запись не должна отбраковываться из-за малого преф-класса")
        self.assertEqual(p["market_cap"], 21000)

    def test_distinct_issuers_with_equal_profit_are_not_merged(self):
        """Схлопывание идёт строго по справочнику: равная прибыль сама по себе ничего не значит."""
        hist = {"AAA": [rec(2024, 1000)], "BBB": [rec(2024, 1000)]}
        p = aggregate_month("2025-06", ["AAA", "BBB"], {"AAA": 5000.0, "BBB": 5000.0},
                            hist, {"AAA": "AAA", "BBB": "BBB"})
        self.assertEqual(p["constituents_used"], 2, "AAA и BBB — разные эмитенты, не классы акций")
        self.assertEqual(p["earnings"], 2000)


class UnitErrors(unittest.TestCase):
    """Ошибки единиц в источнике: тысячи рублей, принятые за рубли (данные 2011–2012)."""

    # NLMK: за 2011 в слое стоит 42,4 трлн ₽ при обычных для компании ~87 млрд
    NLMK = [rec(2011, 42_361_206_008_000), rec(2013, 80_000_000_000),
            rec(2014, 86_929_000_000), rec(2015, 90_000_000_000), rec(2016, 95_000_000_000)]

    def test_record_two_orders_off_its_own_history_is_rejected(self):
        hist = {"NLMK": self.NLMK, "GAZP": [rec(2011, 1_000_000_000)]}
        p = aggregate_month("2012-06", ["NLMK", "GAZP"], {"NLMK": 3.8e11, "GAZP": 1e12},
                            hist, {"NLMK": "NLMK", "GAZP": "GAZP"})
        self.assertEqual(p["earnings"], 1_000_000_000, "битая запись не должна попасть в знаменатель")
        self.assertEqual(p["constituents_used"], 1)

    def test_rejection_is_reported(self):
        hist = {"NLMK": self.NLMK, "GAZP": [rec(2011, 1_000_000_000)]}
        out = []
        aggregate_month("2012-06", ["NLMK", "GAZP"], {"NLMK": 3.8e11, "GAZP": 1e12},
                        hist, {"NLMK": "NLMK", "GAZP": "GAZP"}, None, out)
        self.assertEqual([r["ticker"] for r in out], ["NLMK"])

    def test_profit_above_capitalization_is_kept(self):
        """Сургутнефтегаз-2023: 1,32 трлн прибыли при капитализации 0,85 трлн — это ФАКТ.

        Проверка «нельзя заработать больше, чем стоишь» отбросила бы реальную запись.
        """
        hist = {"SNGS": [rec(2020, 100e9), rec(2021, 500e9), rec(2022, 60e9),
                         rec(2023, 1_322_113_000_000)]}
        p = aggregate_month("2024-06", ["SNGS"], {"SNGS": 852_600_000_000.0}, hist, {"SNGS": "SNGS"})
        self.assertEqual(p["constituents_used"], 1)
        self.assertEqual(p["earnings"], 1_322_113_000_000)

    def test_partially_listed_issuer_is_excluded(self):
        """У Транснефти обыкновенные акции не торгуются: биржевая капитализация — только преф.

        Против полной прибыли компании стояла бы лишь часть её стоимости, и агрегированный
        P/E оказался бы занижен. Эмитент выпадает из расчёта, а не искажает его.
        """
        hist = {"TRNFP": [rec(2022, 180e9), rec(2023, 200e9), rec(2024, 210e9), rec(2025, 226.1e9)],
                "GAZP": [rec(2025, 1e12)]}
        out = []
        p = aggregate_month("2026-06", ["TRNFP", "GAZP"],
                            {"TRNFP": 168_400_000_000.0, "GAZP": 5e12},
                            hist, {"TRNFP": "TRNF", "GAZP": "GAZP"}, None, out)
        self.assertEqual(p["constituents_used"], 1, "в знаменателе только Газпром")
        self.assertEqual(p["earnings"], 1e12)
        self.assertIn("TRNF", [r["ticker"] for r in out])

    def test_short_history_is_not_judged(self):
        """На двух точках медиана ничего не доказывает — молодую компанию не выбрасываем."""
        hist = {"NEW": [rec(2024, 1_000), rec(2025, 500_000)]}
        p = aggregate_month("2026-06", ["NEW"], {"NEW": 1e9}, hist, {"NEW": "NEW"})
        self.assertEqual(p["constituents_used"], 1)


class LossesAndDenominator(unittest.TestCase):
    def test_loss_lowers_aggregate_earnings(self):
        hist = {"AAA": [rec(2024, 1000)], "BBB": [rec(2024, -400)]}
        p = aggregate_month("2025-06", ["AAA", "BBB"], {"AAA": 5000.0, "BBB": 1000.0},
                            hist, {"AAA": "AAA", "BBB": "BBB"})
        self.assertEqual(p["earnings"], 600, "убыток входит со своим знаком, а не отбрасывается")
        self.assertAlmostEqual(p["pe"], 10.0)

    def test_non_positive_denominator_yields_no_pe(self):
        hist = {"AAA": [rec(2024, 100)], "BBB": [rec(2024, -500)]}
        p = aggregate_month("2025-06", ["AAA", "BBB"], {"AAA": 5000.0, "BBB": 1000.0},
                            hist, {"AAA": "AAA", "BBB": "BBB"})
        self.assertIsNone(p["pe"], "P/E при отрицательной суммарной прибыли не определён")
        self.assertEqual(p["quality_status"], "invalid_denominator")

    def test_absurd_pe_rejected_as_data_defect(self):
        hist = {"AAA": [rec(2024, 1)]}
        p = aggregate_month("2025-06", ["AAA"], {"AAA": 1e12}, hist, {"AAA": "AAA"})
        self.assertIsNone(p["pe"])
        self.assertEqual(p["quality_status"], "invalid_denominator")


class CoverageBase(unittest.TestCase):
    """Покрытие считается от ВСЕЙ корзины — иначе доля сверенного завышается."""

    def test_coverage_is_share_of_whole_basket(self):
        hist = {"AAA": [rec(2024, 100)]}          # у BBB прибыли нет вовсе
        p = aggregate_month("2025-06", ["AAA", "BBB"], {"AAA": 600.0, "BBB": 400.0},
                            hist, {"AAA": "AAA", "BBB": "BBB"})
        self.assertAlmostEqual(p["coverage_pct"], 60.0)
        self.assertEqual(p["market_cap"], 600, "непокрытая капитализация в числитель не идёт")

    def test_verified_coverage_is_share_of_whole_basket_not_covered_part(self):
        hist = {"AAA": [rec(2024, 100, verified=True)], "BBB": [rec(2024, 100)]}
        p = aggregate_month("2025-06", ["AAA", "BBB", "CCC"],
                            {"AAA": 500.0, "BBB": 300.0, "CCC": 200.0},
                            hist, {"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"})
        self.assertAlmostEqual(p["coverage_pct"], 80.0)
        self.assertAlmostEqual(p["verified_coverage_pct"], 50.0,
                               msg="500 из 1000 всей корзины, а не 500 из 800 покрытой")

    def test_low_coverage_month_is_flagged(self):
        hist = {"AAA": [rec(2024, 100)]}
        cap = {"AAA": 100.0, "BBB": 900.0}
        p = aggregate_month("2025-06", ["AAA", "BBB"], cap, hist, {"AAA": "AAA", "BBB": "BBB"})
        self.assertLess(p["coverage_pct"], COVERAGE_MIN)
        self.assertEqual(p["quality_status"], "insufficient_coverage")

    def test_missing_capitalization_is_visible_not_hidden(self):
        """Бумага без капитализации не попадает в cap_total — иначе «покрытие 100%» врёт.

        Ровно этот случай однажды дал P/E 15,4 на трёх бумагах из 46 при покрытии «100%».
        """
        hist = {t: [rec(2024, 100)] for t in ("AAA", "BBB", "CCC", "DDD", "EEE")}
        base = {t: t for t in hist}
        p = aggregate_month("2025-06", ["AAA", "BBB", "CCC", "DDD", "EEE"],
                            {"AAA": 1000.0}, hist, base)      # реестр знает только одну бумагу
        self.assertEqual(p["coverage_pct"], 100.0, "по известным бумагам покрытие действительно полное")
        self.assertEqual(p["priced_pct"], 20.0, "но капитализация известна лишь для 1 из 5")
        self.assertEqual(p["quality_status"], "insufficient_coverage")

    def test_quality_tiers(self):
        for verified_share, expect in ((1.0, "verified"), (0.75, "mixed_sources"), (0.2, "estimated")):
            hist = {"AAA": [rec(2024, 100, verified=True)], "BBB": [rec(2024, 100)]}
            cap = {"AAA": 1000.0 * verified_share, "BBB": 1000.0 * (1 - verified_share)}
            p = aggregate_month("2025-06", ["AAA", "BBB"], cap, hist, {"AAA": "AAA", "BBB": "BBB"})
            self.assertEqual(p["quality_status"], expect, f"доля сверенного {verified_share}")


class IssuerIncomeFallback(unittest.TestCase):
    """У Транснефти обыкновенные акции не торгуются: base=TRNF, история — под TRNFP."""

    def test_falls_back_to_share_class_ticker(self):
        hist = {"TRNFP": [rec(2024, 300)]}
        self.assertEqual(issuer_income("TRNF", ["TRNFP"], hist), hist["TRNFP"])

    def test_prefers_base_ticker_when_present(self):
        hist = {"SBER": [rec(2024, 1)], "SBERP": [rec(2024, 2)]}
        self.assertEqual(issuer_income("SBER", ["SBER", "SBERP"], hist), hist["SBER"])

    def test_missing_everywhere(self):
        self.assertEqual(issuer_income("XXXX", ["XXXXP"], {}), [])


class CollapseWeights(unittest.TestCase):
    def test_preferred_weight_merges_into_issuer(self):
        got = collapse_to_issuers({"SBER": 14.0, "SBERP": 1.0, "GAZP": 10.0},
                                  {"SBER": "SBER", "SBERP": "SBER", "GAZP": "GAZP"})
        self.assertEqual(got, {"SBER": 15.0, "GAZP": 10.0})

    def test_ordinary_ticker_ending_in_p_is_not_treated_as_preferred(self):
        """GAZP кончается на P, но это обыкновенная акция — схлопывание идёт по справочнику."""
        got = collapse_to_issuers({"GAZP": 10.0}, {"GAZP": "GAZP"})
        self.assertEqual(got, {"GAZP": 10.0})


class DateHelpers(unittest.TestCase):
    def test_month_last_day(self):
        self.assertEqual(month_last_day("2024-02"), date(2024, 2, 29))
        self.assertEqual(month_last_day("2023-02"), date(2023, 2, 28))
        self.assertEqual(month_last_day("2025-12"), date(2025, 12, 31))

    def test_shift_across_year_boundary(self):
        self.assertEqual(shift("2026-07", 12), "2025-07")
        self.assertEqual(shift("2026-01", 1), "2025-12")
        self.assertEqual(shift("2026-07", 60), "2021-07")

    def test_quantile(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(quantile(vals, 0.0), 1.0)
        self.assertAlmostEqual(quantile(vals, 0.5), 3.0)
        self.assertAlmostEqual(quantile(vals, 1.0), 5.0)
        self.assertAlmostEqual(quantile(vals, 0.25), 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
