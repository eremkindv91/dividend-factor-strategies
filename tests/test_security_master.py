# -*- coding: utf-8 -*-
"""Тесты Security Master (Фаза 1 Daily Market Data Foundation).
Детерминированно: фиксированный now, синтетические универсум/overrides для edge-кейсов,
плюс инварианты на реальном сгенерированном артефакте. Без сети."""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_security_master as bsm  # noqa: E402
import validate_security_master as vsm  # noqa: E402

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _write(tmp, universe_tickers, overrides):
    dj = os.path.join(tmp, "data.json")
    json.dump({"tickers": [{"ticker": t, "name": t + " ао", "sector": "Тест"} for t in universe_tickers]},
              open(dj, "w"))
    ov = os.path.join(tmp, "overrides.json")
    json.dump(overrides, open(ov, "w"))
    return dj, ov


def _build(tmp, tickers, overrides):
    dj, ov = _write(tmp, tickers, overrides)
    return bsm.build(now=NOW, data_json_path=dj, overrides_path=ov)


def _map(payload):
    return {s["canonical_ticker"]: s for s in payload["securities"]}


# ── классы акций: суффикс 'P' — не признак префа ──

def test_pref_only_when_ord_twin_present(tmp_path):
    m = _map(_build(str(tmp_path), ["SBER", "SBERP", "GAZP", "NMTP", "RASP"], {}))
    assert m["SBERP"]["share_class"] == "preferred" and m["SBERP"]["base_ticker"] == "SBER"
    for common in ("SBER", "GAZP", "NMTP", "RASP"):
        assert m[common]["share_class"] == "ordinary", common   # GAZP/NMTP/RASP — НЕ префы


def test_ord_and_pref_not_merged(tmp_path):
    m = _map(_build(str(tmp_path), ["TATN", "TATNP"], {}))
    assert m["TATN"]["canonical_ticker"] != m["TATNP"]["canonical_ticker"]
    assert m["TATN"]["share_class"] == "ordinary" and m["TATNP"]["share_class"] == "preferred"
    assert m["TATNP"]["base_ticker"] == "TATN"                  # связка без слияния


def test_pref_without_ord_via_override(tmp_path):
    ov = {"preferred_no_ord": ["TRNFP"], "base_overrides": {"TRNFP": "TRNF"}}
    m = _map(_build(str(tmp_path), ["TRNFP", "LKOH"], ov))
    assert m["TRNFP"]["share_class"] == "preferred" and m["TRNFP"]["base_ticker"] == "TRNF"


# ── алиасы / корпоративные действия ──

def test_rename_yndx_ydex(tmp_path):
    ov = {"aliases": [{"old": "YNDX", "new": "YDEX", "type": "redomiciliation",
                       "date": "2024-07-24", "date_confidence": "exact", "source": "MOEX", "note": ""}]}
    m = _map(_build(str(tmp_path), ["YDEX"], ov))
    assert m["YDEX"]["previous_tickers"] == ["YNDX"]
    assert "YNDX" in m                                          # исторический тикер сохранён (survivorship)
    assert m["YNDX"]["status"] == "renamed" and m["YNDX"]["successor_ticker"] == "YDEX"
    assert m["YNDX"]["listing_end"] == "2024-07-24"


def test_alias_chain_tcs_tcsg_t(tmp_path):
    ov = {"aliases": [
        {"old": "TCS", "new": "TCSG", "type": "rename", "date": None, "date_confidence": "unknown", "source": "s", "note": ""},
        {"old": "TCSG", "new": "T", "type": "rename", "date": "2025", "date_confidence": "approximate", "source": "s", "note": ""},
    ]}
    m = _map(_build(str(tmp_path), ["T"], ov))
    assert m["T"]["previous_tickers"] == ["TCS", "TCSG"]        # старейший первым
    assert m["TCS"]["successor_ticker"] == "T" and m["TCSG"]["successor_ticker"] == "T"


def test_historical_ticker_retained(tmp_path):
    ov = {"aliases": [{"old": "MAIL", "new": "VKCO", "type": "rename", "date": "2021-10",
                       "date_confidence": "approximate", "source": "MOEX", "note": ""}]}
    m = _map(_build(str(tmp_path), ["VKCO"], ov))
    assert "MAIL" in m and m["MAIL"]["status"] == "renamed"     # не теряем делистингованный/переименованный


# ── контракт полей ──

def test_secid_equals_ticker_and_isin_null(tmp_path):
    m = _map(_build(str(tmp_path), ["SBER"], {}))
    assert m["SBER"]["secid"] == "SBER"
    assert m["SBER"]["isin"] is None and m["SBER"]["figi"] is None   # offline → не выдумываем


def test_no_duplicate_canonical(tmp_path):
    p = _build(str(tmp_path), ["SBER", "GAZP", "LKOH"], {})
    cts = [s["canonical_ticker"] for s in p["securities"]]
    assert len(cts) == len(set(cts))


def test_counts_consistency(tmp_path):
    ov = {"aliases": [{"old": "YNDX", "new": "YDEX", "type": "rename", "date": "2024-07-24",
                       "date_confidence": "exact", "source": "s", "note": ""}]}
    p = _build(str(tmp_path), ["YDEX", "SBER", "SBERP"], ov)
    c = p["counts"]
    assert c["total"] == len(p["securities"]) == 4             # 3 active + YNDX historical
    assert c["active"] == 3 and c["renamed"] == 1
    assert c["preferred"] == 1 and c["ordinary"] == 3


# ── детерминизм / идемпотентность ──

def test_deterministic_rerun(tmp_path):
    dj, ov = _write(str(tmp_path), ["SBER", "SBERP", "GAZP"], {})
    a = bsm.build(now=NOW, data_json_path=dj, overrides_path=ov)
    b = bsm.build(now=NOW, data_json_path=dj, overrides_path=ov)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_securities_sorted(tmp_path):
    p = _build(str(tmp_path), ["LKOH", "GAZP", "SBER"], {})
    cts = [s["canonical_ticker"] for s in p["securities"]]
    assert cts == sorted(cts)


# ── валидатор ──

def test_validator_passes_on_generated(tmp_path):
    p = _build(str(tmp_path), ["SBER", "SBERP", "GAZP"], {})
    assert vsm.validate(p, today=NOW.date()) == []


def test_validator_catches_duplicate():
    bad = {"schema_version": 1, "methodology_version": "x", "counts": {"total": 2}, "securities": [
        {"canonical_ticker": "SBER", "secid": "SBER", "board": "TQBR", "instrument_type": "share",
         "share_class": "ordinary", "base_ticker": "SBER", "previous_tickers": [], "successor_ticker": None,
         "corporate_action_refs": [], "status": "active"},
        {"canonical_ticker": "SBER", "secid": "SBER", "board": "TQBR", "instrument_type": "share",
         "share_class": "ordinary", "base_ticker": "SBER", "previous_tickers": [], "successor_ticker": None,
         "corporate_action_refs": [], "status": "active"},
    ]}
    errs = vsm.validate(bad, today=NOW.date())
    assert any("дубль" in e for e in errs)


def test_validator_rejects_future_corporate_action_date():
    bad = {"schema_version": 1, "methodology_version": "x", "counts": {"total": 1}, "securities": [
        {"canonical_ticker": "SBER", "secid": "SBER", "board": "TQBR", "instrument_type": "share",
         "share_class": "ordinary", "base_ticker": "SBER", "previous_tickers": [], "successor_ticker": None,
         "corporate_action_refs": [{"type": "rename", "date": "2030-01-01", "source": "x"}], "status": "active"},
    ]}
    errs = vsm.validate(bad, today=NOW.date())
    assert any("будущая" in e for e in errs)


def test_validator_catches_missing_successor():
    bad = {"schema_version": 1, "methodology_version": "x", "counts": {"total": 1}, "securities": [
        {"canonical_ticker": "YNDX", "secid": "YNDX", "board": "TQBR", "instrument_type": "share",
         "share_class": "ordinary", "base_ticker": "YNDX", "previous_tickers": [], "successor_ticker": "YDEX",
         "corporate_action_refs": [], "status": "renamed"},
    ]}
    errs = vsm.validate(bad, today=NOW.date())
    assert any("successor" in e for e in errs)


# ── инварианты на РЕАЛЬНОМ артефакте ──

def test_real_artifact_invariants():
    path = os.path.join(ROOT, "data", "security_master.json")
    if not os.path.exists(path):
        return
    obj = json.load(open(path, encoding="utf-8"))
    assert vsm.validate(obj) == []
    m = {s["canonical_ticker"]: s for s in obj["securities"]}
    assert m["GAZP"]["share_class"] == "ordinary"              # реальная ловушка суффикса
    assert m["SBERP"]["share_class"] == "preferred" and m["SBERP"]["base_ticker"] == "SBER"
    assert m["TRNFP"]["share_class"] == "preferred"
    assert m["YDEX"]["previous_tickers"] == ["YNDX"]
