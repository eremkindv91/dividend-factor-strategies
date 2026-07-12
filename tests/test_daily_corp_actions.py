# -*- coding: utf-8 -*-
"""Тесты Фазы 3: corporate actions. Детерминированно. TRNFP/PLZL regression через
представительные синтетические ряды (методология НЕ хардкодится под тикер)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import daily_corp_actions as ca  # noqa: E402
import daily_returns as dr  # noqa: E402


def _rows(pairs):
    return [{"trade_date": d, "close": c} for d, c in pairs]


# ── нормальный ряд без корпдействий ──

def test_no_corporate_action_series_unchanged():
    rows = _rows([("2026-07-06", 100.0), ("2026-07-07", 101.0), ("2026-07-08", 102.0)])
    res = ca.apply_corporate_actions(rows)
    for e in res["rows"]:
        assert e["adjusted_close"] == e["raw_close"] and e["adjustment_factor"] == 1.0
    assert res["summary"]["obs_adjusted"] == 0
    assert res["summary"]["total_return_index"] == "unavailable"


# ── TRNFP-подобный сплит 100:1 ──

def test_trnfp_like_split_100_to_1():
    rows = _rows([("2026-07-06", 150000.0), ("2026-07-07", 151000.0),
                  ("2026-07-08", 1500.0), ("2026-07-09", 1510.0)])   # 08-го сплит ex-date
    res = ca.apply_corporate_actions(rows, splits={"2026-07-08": 100.0})
    adj = [e["adjusted_close"] for e in res["rows"]]
    assert abs(adj[0] - 1500.0) < 1e-6 and abs(adj[1] - 1510.0) < 1e-6   # до-сплитные /100
    assert abs(adj[2] - 1500.0) < 1e-6 and abs(adj[3] - 1510.0) < 1e-6   # пост-сплитные без изменений
    rets = dr.simple_returns(adj)
    assert all(abs(r) < 0.05 for r in rets)                             # НЕТ фейкового −99%
    assert res["summary"]["obs_adjusted"] == 2
    assert res["summary"]["suspected_corporate_actions"] == []          # сплит подтверждён, не suspected


# ── PLZL-подобный крупный дивиденд (НЕ сплит) ──

def test_plzl_like_large_dividend_not_split():
    # цена падает на ex-дату на величину дивиденда, но это НЕ сплит
    rows = _rows([("2026-07-06", 14000.0), ("2026-07-07", 13000.0), ("2026-07-08", 13010.0)])
    res = ca.apply_corporate_actions(rows, dividends={"2026-07-07": 1000.0})
    for e in res["rows"]:
        assert e["adjusted_close"] == e["raw_close"]                   # НЕ корректируем как сплит
    assert res["rows"][1]["dividend_cash"] == 1000.0                   # отдельный денежный поток
    assert res["summary"]["total_return_index"] == "confirmed"
    # total return на ex-дату ≈ (13000+1000)/14000 − 1 = 0
    tr = dr.total_returns([e["adjusted_close"] for e in res["rows"]],
                          [e["dividend_cash"] for e in res["rows"]])
    assert abs(tr[0]) < 1e-9


# ── suspected: экстремальный разрыв без подтверждённого сплита ──

def test_suspected_flagged_not_applied():
    rows = _rows([("2026-07-06", 1000.0), ("2026-07-07", 100.0), ("2026-07-08", 101.0)])  # −90% без сплита
    res = ca.apply_corporate_actions(rows, splits={})
    sus = res["summary"]["suspected_corporate_actions"]
    assert len(sus) == 1 and sus[0]["date"] == "2026-07-07" and sus[0]["status"] == "suspected"
    assert res["rows"][0]["adjusted_close"] == 1000.0                  # ряд НЕ изменён молча


def test_confirmed_split_not_flagged_suspected():
    rows = _rows([("2026-07-06", 1000.0), ("2026-07-07", 100.0)])
    res = ca.apply_corporate_actions(rows, splits={"2026-07-07": 10.0})
    assert res["summary"]["suspected_corporate_actions"] == []         # разрыв объяснён сплитом


# ── аномальный масштаб блокирует ──

def test_insane_factor_blocks():
    rows = _rows([("2026-07-06", 100.0), ("2026-07-07", 100.0)])
    with pytest.raises(ca.CorporateActionError):
        ca.apply_corporate_actions(rows, splits={"2026-07-07": 5000.0})


# ── воспроизводимость ──

def test_reproducible():
    rows = _rows([("2026-07-06", 150000.0), ("2026-07-07", 1500.0)])
    a = ca.apply_corporate_actions(rows, splits={"2026-07-07": 100.0})
    b = ca.apply_corporate_actions(rows, splits={"2026-07-07": 100.0})
    assert a == b


# ── парсинг ISS splits ──

def test_fetch_splits_iss_parsing():
    payload = {"splits": {"columns": ["secid", "tradedate", "before", "after"],
                          "data": [["TRNFP", "2024-02-08", 1, 100], ["OTHER", "2024-01-01", 1, 2]]}}
    got = ca.fetch_splits_iss("TRNFP", http=lambda *a, **k: payload)
    assert got == {"2024-02-08": 100.0}                               # только TRNFP, factor after/before
