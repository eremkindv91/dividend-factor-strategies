# -*- coding: utf-8 -*-
"""Тесты Фазы 4: quality + coverage artifact. Детерминированно, ряды инъектируются."""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_daily_quality as bdq  # noqa: E402
import daily_config as cfg  # noqa: E402
import trading_calendar as tc  # noqa: E402

TODAY = date(2026, 7, 10)   # пятница


def _series(end, n, base=100.0, jump_at=None, factor=20.0):
    """n последовательных ТОРГОВЫХ дней, заканчивая end; опц. УСТОЙЧИВЫЙ сдвиг уровня
    (как сплит) с позиции jump_at — один экстремум на переходе, дальше уровень держится."""
    days, d = [], end
    while len(days) < n:
        if tc.is_trading_day(d):
            days.append(d)
        d = date.fromordinal(d.toordinal() - 1)
    days.sort()
    rows = []
    for i, dd in enumerate(days):
        c = base + i
        if jump_at is not None and i >= jump_at:
            c = (base + i) / factor          # уровень падает и держится (сплит-подобно)
        rows.append({"trade_date": dd.isoformat(), "close": c})
    return rows


def test_good_series():
    q = bdq.quality_one("SBER", _series(TODAY, 300), TODAY)
    assert q["quality_status"] == "good"
    assert q["coverage_ratio"] == 1.0 and q["observations"] == 300


def test_insufficient_history():
    q = bdq.quality_one("NEW", _series(TODAY, 100), TODAY)     # < 125
    assert q["quality_status"] == "insufficient_history"


def test_stale_series():
    q = bdq.quality_one("OLD", _series(date(2026, 6, 1), 300), TODAY)
    assert q["quality_status"] == "stale"
    assert q["stale_sessions"] >= cfg.HISTORY_UNAVAILABLE - cfg.HISTORY_UNAVAILABLE  # >0
    assert q["stale_sessions"] > 0


def test_corporate_action_unresolved():
    q = bdq.quality_one("SPK", _series(TODAY, 200, jump_at=150), TODAY)   # экстремум без сплита
    assert q["quality_status"] == "corporate_action_unresolved"
    assert q["suspected_corporate_actions"] >= 1


def test_confirmed_split_not_unresolved():
    rows = _series(TODAY, 200, jump_at=150)
    split_date = rows[150]["trade_date"]
    q = bdq.quality_one("SPK", rows, TODAY, splits={split_date: 20.0})
    assert q["quality_status"] != "corporate_action_unresolved"
    assert q["suspected_corporate_actions"] == 0 and q["confirmed_corporate_actions"] == 1


def test_unavailable_empty():
    q = bdq.quality_one("NONE", [], TODAY)
    assert q["quality_status"] == "unavailable" and q["observations"] == 0


def test_coverage_aggregate():
    series = {
        "GOOD": _series(TODAY, 300),
        "SHORT": _series(TODAY, 80),
        "OLD": _series(date(2026, 6, 1), 300),
        "EMPTY": [],
    }
    payload = bdq.compute(series, TODAY, universe_size=4)
    c = payload["coverage"]
    assert c["universe_size"] == 4
    assert c["available"] == 3                       # EMPTY недоступна
    assert c["usable_for_risk"] == 1                 # только GOOD (≥125 и good/warn)
    assert c["excluded"] >= 2                         # SHORT(insufficient)+OLD(stale)+EMPTY(unavailable)
    assert c["history_len_max"] == 300 and c["history_len_min"] == 80
    assert payload["schema_version"] == 1


def test_coverage_distribution_sums():
    series = {"A": _series(TODAY, 300), "B": _series(TODAY, 100), "C": []}
    payload = bdq.compute(series, TODAY)
    dist = payload["status_distribution"]
    assert sum(dist.values()) == len(payload["securities"]) == 3


def test_validate_ok_and_catches_bad():
    payload = bdq.compute({"A": _series(TODAY, 300)}, TODAY)
    assert bdq.validate(payload) == []
    bad = {"schema_version": 2, "securities": []}
    assert bdq.validate(bad)
