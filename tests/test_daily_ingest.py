# -*- coding: utf-8 -*-
"""Тесты Фазы 2 (дневной ingestion). Детерминированно, БЕЗ сети: fetch инъектируется."""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import daily_ingest as di  # noqa: E402
import daily_store as store  # noqa: E402
import trading_calendar as tc  # noqa: E402
import moex_iss  # noqa: E402

TODAY = date(2026, 7, 10)   # пятница


_EPOCH = date(2018, 1, 1).toordinal()


def _candles(frm, till, base=100.0):
    """OHLCV только по торговым дням [frm,till]. Цена — функция ДАТЫ (не позиции в окне),
    как у реального ISS: перекрёстный дозабор даёт те же значения → идемпотентно."""
    out, d = [], date.fromisoformat(frm)
    end = date.fromisoformat(till)
    while d <= end:
        if tc.is_trading_day(d):
            n = d.toordinal() - _EPOCH
            c = base + (n % 500)
            out.append({"trade_date": d.isoformat(), "open": c - 0.5, "high": c + 1,
                        "low": c - 1, "close": c, "value": 1000.0 + n, "volume": 10.0 + (n % 100)})
        d = date.fromordinal(d.toordinal() + 1)
    return out


def _master(tmp, secids):
    p = os.path.join(tmp, "master.json")
    json.dump({"securities": [{"secid": s, "board": "TQBR", "status": "active"} for s in secids]}, open(p, "w"))
    return p


def _run(tmp, secids, fetch, today=TODAY, **kw):
    return di.run(master_path=_master(tmp, secids), out_dir=os.path.join(tmp, "daily"),
                  fetch=fetch, today=today, **kw)


# ── нормальный ряд / календарь ──

def test_normal_series_no_weekend_obs(tmp_path):
    tmp = str(tmp_path)
    m = _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till))
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    dates = [r["trade_date"] for r in rows]
    assert all(tc.is_trading_day(date.fromisoformat(d)) for d in dates)   # нет выходных наблюдений
    assert "2026-07-04" not in dates and "2026-07-05" not in dates        # сб/вс
    assert m["series"]["SBER"]["status"] == "ok"


def test_no_fake_observation_on_missing_session(tmp_path):
    tmp = str(tmp_path)
    # источник пропустил один торговый день (2026-07-08) → дыра остаётся, не заполняем
    def fetch(s, frm, till, b):
        return [r for r in _candles(frm, till) if r["trade_date"] != "2026-07-08"]
    _run(tmp, ["SBER"], fetch)
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    assert "2026-07-08" not in [r["trade_date"] for r in rows]            # пропуск не выдуман


# ── дубликаты / будущее ──

def test_duplicate_date_dedup(tmp_path):
    tmp = str(tmp_path)
    def fetch(s, frm, till, b):
        c = _candles(frm, till)
        return c + [dict(c[-1])]                                          # дубль последней даты
    _run(tmp, ["SBER"], fetch)
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    d = [r["trade_date"] for r in rows]
    assert len(d) == len(set(d))                                         # без дублей


def test_future_date_rejected(tmp_path):
    tmp = str(tmp_path)
    def fetch(s, frm, till, b):
        return _candles(frm, till) + [{"trade_date": "2026-07-20", "open": 1, "high": 1,
                                       "low": 1, "close": 1, "value": 1, "volume": 1}]
    _run(tmp, ["SBER"], fetch, today=TODAY)
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    assert "2026-07-20" not in [r["trade_date"] for r in rows]           # будущее отброшено


def test_utc_moscow_boundary_today_kept(tmp_path):
    tmp = str(tmp_path)
    # ряд включает пятницу (today MSK) — она должна остаться, будущее — нет
    _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till), today=TODAY)
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    assert "2026-07-10" in [r["trade_date"] for r in rows]


# ── инкремент / идемпотентность ──

def test_incremental_update_no_dup(tmp_path):
    tmp = str(tmp_path)
    _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till), today=date(2026, 7, 3))
    m2 = _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till), today=TODAY)
    rows = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    d = [r["trade_date"] for r in rows]
    assert len(d) == len(set(d))
    assert m2["series"]["SBER"]["status"] == "ok_incremental"
    assert "2026-07-10" in d and "2026-07-02" in d


def test_deterministic_rerun_same_checksum(tmp_path):
    tmp = str(tmp_path)
    m1 = _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till))
    m2 = _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till))
    assert m1["series"]["SBER"]["checksum"] == m2["series"]["SBER"]["checksum"]


# ── источник недоступен ──

def test_source_unavailable_with_cache_keeps_last_good(tmp_path):
    tmp = str(tmp_path)
    _run(tmp, ["SBER"], lambda s, frm, till, b: _candles(frm, till))
    before = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    def boom(s, frm, till, b):
        raise RuntimeError("ISS down")
    m = _run(tmp, ["SBER"], boom)
    after = store.read_parquet(os.path.join(tmp, "daily", "prices", "SBER.parquet"))
    assert after == before                                               # last-good сохранён
    assert m["series"]["SBER"]["status"] == "fallback"


def test_source_unavailable_without_cache(tmp_path):
    tmp = str(tmp_path)
    def boom(s, frm, till, b):
        raise RuntimeError("ISS down")
    m = _run(tmp, ["SBER"], boom)
    assert m["series"]["SBER"]["status"] == "unavailable"
    assert not os.path.exists(os.path.join(tmp, "daily", "prices", "SBER.parquet"))


# ── манифест / схема ──

def test_manifest_schema_version(tmp_path):
    tmp = str(tmp_path)
    m = _run(tmp, ["SBER", "GAZP"], lambda s, frm, till, b: _candles(frm, till))
    assert m["schema_version"] == store.SCHEMA_VERSION
    assert set(m["series"]) == {"SBER", "GAZP"}


def test_limit_and_only(tmp_path):
    tmp = str(tmp_path)
    m = _run(tmp, ["SBER", "GAZP", "LKOH"], lambda s, frm, till, b: _candles(frm, till), only=["GAZP"])
    assert set(m["series"]) == {"GAZP"}


# ── парсинг fetch_ohlcv (источник) через монки-патч http ──

def test_fetch_ohlcv_parsing_and_filtering(monkeypatch):
    page = {"candles": {"columns": ["begin", "open", "high", "low", "close", "value", "volume"],
                        "data": [
                            ["2026-07-08 00:00:00", 99, 101, 98, 100.0, 5000, 50],
                            ["2026-07-09 00:00:00", 100, 102, 99, 101.0, 6000, 60],
                            ["2026-07-09 00:00:00", 100, 102, 99, 101.5, 6100, 61],   # дубль даты → keep-last
                            ["2026-07-10 00:00:00", 0, 0, 0, 0.0, 0, 0],              # нулевая цена → drop
                        ]}}
    monkeypatch.setattr(moex_iss, "_http_get_json", lambda *a, **k: page)
    out = moex_iss.fetch_ohlcv("SBER", "2026-07-01", "2026-07-10")
    d = [r["trade_date"] for r in out]
    assert d == ["2026-07-08", "2026-07-09"]                             # дедуп + drop нулевой
    assert out[-1]["close"] == 101.5                                     # keep-last по дубликату
