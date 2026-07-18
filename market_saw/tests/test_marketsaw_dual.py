# -*- coding: utf-8 -*-
"""Тесты двухконтурной сборки: IMOEX-билдер (валидация, live snapshot в payload),
manifest (failure isolation, зеркало MCFTR), без сети — fetch мокается."""
import json
import os
import sys
from datetime import date, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MS = os.path.dirname(HERE)
PROD = os.path.join(MS, "production")
sys.path.insert(0, os.path.join(MS, "shared"))
sys.path.insert(0, PROD)
import build_imoex_saw as bi  # noqa: E402
import build_market_saw_manifest as bm  # noqa: E402


def _long_series(n=200):
    """n дней, ЗАКАНЧИВАЯ сегодня (иначе STALE_FAIL в build()): рост до пика, затем спад."""
    end = date.today()
    out, price = [], 1000.0
    for i in range(n):
        price = 1000.0 + i * 5 if i < n * 0.6 else price * 0.99
        out.append({"date": (end - timedelta(days=n - 1 - i)).isoformat(), "close": round(price, 2)})
    return out


def test_build_payload_contract_and_live_snapshot():
    pts = _long_series()
    board = ("stock", "index", "SNDX")
    snap = {"price": pts[-1]["close"] * 0.98, "last_official": pts[-1]["close"], "systime": "2026-01-01 13:00:00", "is_intraday": True}
    payload = bi.build(pts, board, snap)
    bi.validate(pts, payload)                              # не бросает
    assert payload["index"] == "IMOEX" and payload["schema"] == 2
    assert payload["official_close"]["price"] == round(pts[-1]["close"], 2)
    assert payload["live_snapshot"]["provisional"] is True
    assert payload["current_state"]["price_type"] == "live"      # live-цена отличается от official
    assert payload["data_quality"]["board"] == "SNDX"


def test_build_without_snapshot_uses_official():
    pts = _long_series()
    payload = bi.build(pts, ("stock", "index", "SNDX"), None)
    assert payload["live_snapshot"] is None
    assert payload["current_state"]["price_type"] == "official"


def test_validate_rejects_mcftr_methodology_in_imoex():
    pts = _long_series()
    payload = bi.build(pts, ("stock", "index", "SNDX"), None)
    payload["current_phase"] = {"foo": 1}                  # чужая методика просочилась
    with pytest.raises(RuntimeError):
        bi.validate(pts, payload)


def test_validate_rejects_short_series():
    pts = _long_series(50)                                  # < MIN_POINTS
    payload = bi.build(pts, ("stock", "index", "SNDX"), None)
    with pytest.raises(RuntimeError):
        bi.validate(pts, payload)


def test_main_writes_atomic_via_mock(tmp_path, monkeypatch):
    # main() end-to-end без сети: fetch мокается, _long_series уже заканчивается сегодня (не stale)
    pts = _long_series()
    monkeypatch.setattr(bi.fetch, "discover_index_board", lambda s: ("stock", "index", "SNDX"))
    monkeypatch.setattr(bi.fetch, "fetch_index_history", lambda s, *a: [(p["date"], p["close"]) for p in pts])
    monkeypatch.setattr(bi.fetch, "fetch_index_snapshot", lambda s, *a: None)
    out = os.path.join(str(tmp_path), "marketsaw_imoex.json")
    monkeypatch.setattr(sys, "argv", ["build_imoex_saw.py", out])
    assert bi.main() == 0
    obj = json.load(open(out, encoding="utf-8"))
    assert obj["index"] == "IMOEX" and obj["schema"] == 2 and obj["current_state"]["zone"] in ("buy", "neutral", "fix")


# ── manifest: failure isolation ──

def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_manifest_both_available(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "marketsaw.json"), {"index": "MCFTR", "data_last": date.today().isoformat()})
    _write(os.path.join(d, "marketsaw_imoex.json"),
           {"index": "IMOEX", "official_close": {"date": date.today().isoformat()}})
    m = bm.build(d)
    assert m["default_index"] == "MCFTR"
    assert m["indices"]["MCFTR"]["available"] and m["indices"]["IMOEX"]["available"]
    assert os.path.exists(os.path.join(d, "marketsaw_mcftr.json"))   # зеркало создано


def test_manifest_imoex_broken_mcftr_survives(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "marketsaw.json"), {"index": "MCFTR", "data_last": date.today().isoformat()})
    with open(os.path.join(d, "marketsaw_imoex.json"), "w") as f:
        f.write("{ broken json")
    m = bm.build(d)
    assert m["indices"]["MCFTR"]["available"] is True             # MCFTR не пострадал
    assert m["indices"]["IMOEX"]["available"] is False
    assert m["indices"]["IMOEX"]["freshness_status"] == "unavailable"


def test_manifest_mcftr_missing_imoex_survives(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "marketsaw_imoex.json"),
           {"index": "IMOEX", "official_close": {"date": date.today().isoformat()}})
    m = bm.build(d)
    assert m["indices"]["MCFTR"]["available"] is False
    assert m["indices"]["IMOEX"]["available"] is True            # IMOEX не пострадал


def test_manifest_reports_fallback_flag(tmp_path):
    d = str(tmp_path)
    _write(os.path.join(d, "marketsaw.json"), {"index": "MCFTR", "data_last": date.today().isoformat()})
    _write(os.path.join(d, "marketsaw_imoex.json"),
           {"index": "IMOEX", "official_close": {"date": date.today().isoformat()}, "fallback": True})
    m = bm.build(d)
    assert m["indices"]["IMOEX"]["fallback"] is True
