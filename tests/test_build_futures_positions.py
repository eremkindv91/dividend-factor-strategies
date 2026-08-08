"""Открытые позиции физлиц во фьючерсах (scripts/build_futures_positions.py).

Сеть подменяется. Стережётся то, где эта область легко врёт: сторона участников,
единицы измерения и запрет складывать контракты разного размера.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "build_futures_positions.py"
    spec = importlib.util.spec_from_file_location("build_futures_positions", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_futures_positions"] = module
    spec.loader.exec_module(module)
    return module


pos = _load()


def _payload(rows):
    cols = ["tradedate", "asset", "is_fiz", "persons_long", "persons_short",
            "open_position_long", "open_position_short", "oichange_long", "oichange_short"]
    return {"open_positions": {"columns": cols,
                               "data": [[r.get(c) for c in cols] for r in rows]}}


def _row(date, long, short, is_fiz=1, pl=10, ps=5):
    return {"tradedate": date, "asset": "MIX", "is_fiz": is_fiz,
            "persons_long": pl, "persons_short": ps,
            "open_position_long": long, "open_position_short": short,
            "oichange_long": 0, "oichange_short": 0}


def _series(n=60, base=1000):
    return [_row(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", base + i * 10, base)
            for i in range(n)]


# ─────────────────────────── сторона участников ───────────────────────────


def test_only_individuals_are_taken(monkeypatch):
    """is_fiz=0 — юрлица; их ряд зеркален и в публикуемый ряд физлиц попадать не должен."""
    monkeypatch.setattr(pos, "http_json", lambda url, tries=3: _payload([
        _row("2026-08-06", 86086, 86461, is_fiz=1),
        _row("2026-08-06", 73767, 73392, is_fiz=0),
    ]))

    rows = pos.positions("MIX", "2026-08-06")

    assert len(rows) == 1
    assert rows[0]["long"] == 86086 and rows[0]["short"] == 86461


def test_net_is_long_minus_short_and_may_be_negative(monkeypatch):
    """Нетто-позиция обязана проходить через ноль: и лонг, и шорт — законные состояния."""
    monkeypatch.setattr(pos, "http_json", lambda url, tries=3: _payload([
        _row("2026-08-05", 151828, 104303),
        _row("2026-08-06", 100000, 126389),
    ]))

    rows = pos.positions("SBRF", "2026-08-06")

    assert rows[0]["net"] == pytest.approx(47525)
    assert rows[1]["net"] == pytest.approx(-26389)


def test_incomplete_row_is_skipped_not_zeroed(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda url, tries=3: _payload([
        _row("2026-08-05", 100, 50),
        _row("2026-08-06", None, 50),
    ]))

    rows = pos.positions("MIX", "2026-08-06")

    assert [r["d"] for r in rows] == ["2026-08-05"]


def test_source_refusal_is_raised_not_swallowed(monkeypatch):
    monkeypatch.setattr(pos, "http_json", lambda url, tries=3: {
        "open_positions": {"columns": ["ERROR_MESSAGE"], "data": [["Invalid date value."]]}})

    with pytest.raises(pos.IssError):
        pos.positions("MIX", "2026-08-06")


# ─────────────────────────── единицы и рубли ───────────────────────────


def test_multiplier_comes_from_the_contract_spec(monkeypatch):
    """Рублей за пункт = STEPPRICE / MINSTEP. У мини-контракта это 10, у основного 1."""
    monkeypatch.setattr(pos, "http_json", lambda url, tries=3: {
        "securities": {"columns": ["SECID", "MINSTEP", "STEPPRICE"],
                       "data": [["MMU6", 0.05, 0.5]]}})

    multiplier, _spec = pos.contract_multiplier("MMU6")

    assert multiplier == pytest.approx(10.0)


def test_no_roubles_without_a_verified_multiplier():
    """Без спецификации рублёвых величин в выходе быть не должно — только контракты."""
    rows = [{"d": f"2026-01-{i:02d}", "long": 100 + i, "short": 50,
             "net": 50 + i, "persons_long": 1, "persons_short": 1} for i in range(1, 41)]

    summary = pos.summarize(rows, None, None)

    for key in ("net_rub", "long_rub", "short_rub", "price", "multiplier"):
        assert key not in summary


def test_notional_uses_contracts_price_and_multiplier():
    rows = [{"d": f"2026-01-{i:02d}", "long": 100, "short": 50,
             "net": 205650, "persons_long": 1, "persons_short": 1} for i in range(1, 41)]

    summary = pos.summarize(rows, 10.0, 2268.0)

    assert summary["net_rub"] == pytest.approx(205650 * 2268.0 * 10.0)
    assert summary["multiplier"] == 10.0 and summary["price"] == 2268.0


def test_meta_forbids_summing_contracts_across_series(monkeypatch):
    """MIX, MXI и IMOEX — один индекс, но разный размер контракта."""
    monkeypatch.setattr(pos, "positions", lambda asset, date_to: [])
    monkeypatch.setattr(pos, "equity_assets", lambda: {})

    meta = pos.build(datetime(2026, 8, 8, tzinfo=timezone.utc))["meta"]

    assert "не складываются" in meta["no_cross_series_sum"]
    assert "STEPPRICE" in meta["notional_formula"]
    assert "контракты" in meta["unit"]
    assert "предыдущий торговый день" in meta["freshness"]
    assert "открытом интересе" in meta["no_oi_share"]


# ─────────────────────────── статистика ───────────────────────────


def test_percentile_and_zscore_need_enough_history():
    assert pos.percentile([1, 2, 3]) is None
    assert pos.z_score([1, 2, 3]) is None


def test_percentile_places_the_last_point_among_observations():
    assert pos.percentile(list(range(100))) == pytest.approx(100.0)
    assert pos.percentile([50] * 99 + [1]) == pytest.approx(0.0)


def test_change_is_measured_against_an_existing_point():
    rows = [{"long": 100, "short": 0, "net": 100}, {"long": 120, "short": 0, "net": 120},
            {"long": 150, "short": 0, "net": 150}]

    assert pos.change_over(rows, 1) == 30
    assert pos.change_over(rows, 2) == 50
    assert pos.change_over(rows, 5) is None, "нет сопоставимой точки — нет числа"


def test_summary_reports_direction_inside_individuals():
    rows = [{"d": f"2026-01-{i:02d}", "long": 300, "short": 100, "net": 200,
             "persons_long": 7, "persons_short": 3} for i in range(1, 41)]

    summary = pos.summarize(rows, None, None)

    assert summary["long_share"] == pytest.approx(0.75)
    assert summary["net_ratio"] == pytest.approx(0.5)
    assert summary["persons_long"] == 7 and summary["persons_short"] == 3


# ─────────────────────────── сборка ───────────────────────────


def test_index_series_carries_roubles_only_for_dates_with_a_price(monkeypatch):
    rows = [{"d": f"2026-01-{i:02d}", "long": 100, "short": 50, "net": 50,
             "persons_long": 1, "persons_short": 1} for i in range(1, 41)]
    monkeypatch.setattr(pos, "positions", lambda asset, date_to: rows)
    monkeypatch.setattr(pos, "contract_multiplier", lambda secid: (10.0, {}))
    monkeypatch.setattr(pos, "price_history", lambda secid, date_to: {"2026-01-05": 2268.0})
    monkeypatch.setattr(pos, "equity_assets", lambda: {})
    monkeypatch.setattr(pos.time, "sleep", lambda _s: None)

    payload = pos.build(datetime(2026, 8, 8, tzinfo=timezone.utc))
    imoex = payload["indices"]["IMOEX"]

    assert imoex["status"] == "ok"
    filled = [v for v in imoex["net_rub"] if v is not None]
    assert len(filled) == 1, "рубли считаются только там, где известна цена ЭТОГО дня"
    assert filled[0] == pytest.approx(50 * 2268.0 * 10.0)


def test_short_series_is_marked_unavailable(monkeypatch):
    monkeypatch.setattr(pos, "positions", lambda asset, date_to: [
        {"d": "2026-08-06", "long": 1, "short": 1, "net": 0,
         "persons_long": 1, "persons_short": 1}])
    monkeypatch.setattr(pos, "equity_assets", lambda: {})
    monkeypatch.setattr(pos.time, "sleep", lambda _s: None)

    payload = pos.build(datetime(2026, 8, 8, tzinfo=timezone.utc))

    assert payload["indices"]["MIX"]["status"] == "unavailable"
    assert "меньше" in payload["indices"]["MIX"]["reason"]


def test_bom_in_the_response_does_not_break_parsing(monkeypatch):
    """ISS отдаёт часть ответов с BOM — на нём обычный json.load падает."""
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return '﻿{"open_positions": {"columns": [], "data": []}}'.encode("utf-8")

    monkeypatch.setattr(pos.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    assert pos.http_json("https://example.test") == {"open_positions": {"columns": [], "data": []}}
