# -*- coding: utf-8 -*-
"""Тесты веб-моста дневных доходностей (Фаза B Risk Engine). Детерминированно, без сети."""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_daily_returns_web as web  # noqa: E402


def _rows(n, base=100.0, split_at=None, factor=10.0):
    rows, d = [], date(2024, 1, 1)
    for i in range(n):
        c = base + i
        if split_at is not None and i >= split_at:
            c = (base + i) / factor
        rows.append({"trade_date": d.isoformat(), "close": c})
        d = date.fromordinal(d.toordinal() + 1)
    return rows


def test_build_one_adjusted_returns():
    rec = web.build_one("SBER", _rows(300), splits=None, quality_status="good")
    assert rec["return_type"] == "adjusted_price_return"
    assert rec["observations"] == 299                    # n-1 доходностей
    assert rec["corporate_action_status"] == "resolved"


def test_build_one_publishes_official_ohlcv_without_synthetic_fields():
    rows = [
        {"trade_date": "2026-08-14", "open": 270.0, "high": 274.0, "low": 269.0,
         "close": 272.0, "volume": 1000.0, "value": 271500.0},
        {"trade_date": "2026-08-17", "open": 272.0, "high": 273.0, "low": 269.0,
         "close": 270.0, "volume": 1200.0, "value": 325000.0},
    ]

    rec = web.build_one("SBER", rows, splits=None, quality_status="good")

    assert rec["schema_version"] == 2
    assert rec["source_as_of"] == "2026-08-17"
    assert rec["ohlcv_observations"] == 2
    assert rec["ohlcv"][-1] == ["2026-08-17", 272.0, 273.0, 269.0, 270.0, 1200.0, 325000.0]


def test_window_truncation():
    rec = web.build_one("SBER", _rows(1000), splits=None, quality_status="good", window=504)
    assert rec["observations"] == 504 and len(rec["dates"]) == 504


def test_split_applied_no_fake_jump():
    rows = _rows(300, split_at=150, factor=10.0)
    split_date = rows[150]["trade_date"]
    rec = web.build_one("SPK", rows, splits={split_date: 10.0}, quality_status="good")
    assert all(abs(r) < 0.05 for r in rec["returns"])    # сплит скорректирован, нет −90%
    assert rec["corporate_action_status"] == "resolved"


def test_suspected_marks_unresolved():
    rows = _rows(300, split_at=150, factor=10.0)          # разрыв БЕЗ подтверждённого сплита
    rec = web.build_one("SPK", rows, splits=None, quality_status="usable_with_warning")
    assert rec["corporate_action_status"] == "unresolved"


def test_benchmark_from_market_history():
    inst = [{"id": "IMOEX", "series": [[d, 0, 0, 0, 100 + i] for i, d in enumerate(
        [date.fromordinal(date(2024,1,1).toordinal()+j).isoformat() for j in range(20)])]}]
    b = web.build_benchmark(inst, "IMOEX")
    assert b["ticker"] == "IMOEX" and b["type"] == "price_index" and len(b["returns"]) == 19


def test_build_writes_files_and_index(tmp_path):
    out = str(tmp_path)
    series = {"SBER": _rows(300), "OLD": _rows(300), "GAZP": _rows(300)}
    quality = {"SBER": "good", "OLD": "stale", "GAZP": "usable_with_warning"}
    mh = [{"id": "IMOEX", "series": [[date.fromordinal(date(2024,1,1).toordinal()+j).isoformat(), 0, 0, 0, 100 + j] for j in range(300)]}]
    idx = web.build(series, {}, quality, mh, out)
    assert os.path.exists(os.path.join(out, "SBER.json"))
    assert os.path.exists(os.path.join(out, "_benchmark.json"))
    assert not os.path.exists(os.path.join(out, "OLD.json"))       # stale не публикуется
    assert idx["securities"]["OLD"]["published"] is False
    assert idx["securities"]["SBER"]["published"] is True
    rec = json.load(open(os.path.join(out, "SBER.json")))
    assert rec["secid"] == "SBER" and rec["returns"]


def test_deterministic(tmp_path):
    series = {"SBER": _rows(300)}
    a = web.build(series, {}, {"SBER": "good"}, [], str(tmp_path / "a"))
    b = web.build(series, {}, {"SBER": "good"}, [], str(tmp_path / "b"))
    ra = json.load(open(os.path.join(str(tmp_path / "a"), "SBER.json")))
    rb = json.load(open(os.path.join(str(tmp_path / "b"), "SBER.json")))
    assert ra["returns"] == rb["returns"]
