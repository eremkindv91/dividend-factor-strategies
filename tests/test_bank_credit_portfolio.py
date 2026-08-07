"""Кредитный портфель банков из формы 101 ЦБ (scripts/cbr_banks/build_banks_credit.py).

Сеть не трогается: SOAP подменяется. Проверяется то, на чём эта задача уже один раз
сломалась — сторона счёта, выбор остатка и граница между официальным и нашим сопоставлением.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "cbr_banks" / "build_banks_credit.py"
    spec = importlib.util.spec_from_file_location("build_banks_credit", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_banks_credit"] = module
    spec.loader.exec_module(module)
    return module


credit = _load()


def _soap(rows):
    """Ответ Data101FullV2: строки с dt/ap/vitg/iitg."""
    body = "".join(
        f"<row><dt>{r['dt']}T00:00:00+03:00</dt><pln>А</pln><ap>{r['ap']}</ap>"
        f"<vitg>{r.get('vitg', 0)}.0000</vitg><iitg>{r['iitg']}.0000</iitg></row>"
        for r in rows
    )
    return f'<?xml version="1.0"?><Envelope><Body><Result>{body}</Result></Body></Envelope>'


# ─────────────────────────── сторона счёта ───────────────────────────


def test_only_the_asset_side_counts_as_a_loan(monkeypatch):
    """Ровно та ошибка, что дала «розницу Сбербанка 0,70 трлн» вместо 19,30.

    Пассивная строка приходит тем же кодом 45.2 и выглядит как обычное значение —
    отличить её можно только по признаку ap.
    """
    monkeypatch.setattr(credit.cs, "soap_post", lambda *a, **k: _soap([
        {"dt": "2026-07-01", "ap": "1", "iitg": 19298694120},
        {"dt": "2026-07-01", "ap": "2", "iitg": 700560779},
    ]))

    values = credit.series(1481, "45.2", "2026-06-01", "2026-07-01")

    assert values == {"2026-07-01": 19298694120.0}, "пассивная сторона не должна попадать в портфель"


def test_closing_balance_is_taken_not_opening(monkeypatch):
    """vitg — входящий остаток, iitg — исходящий: доказано тем, что iitg(t) == vitg(t+1)."""
    monkeypatch.setattr(credit.cs, "soap_post", lambda *a, **k: _soap([
        {"dt": "2026-06-01", "ap": "1", "vitg": 18908158292, "iitg": 19044629192},
        {"dt": "2026-07-01", "ap": "1", "vitg": 19044629192, "iitg": 19298694120},
    ]))

    values = credit.series(1481, "45.2", "2026-06-01", "2026-07-01")

    assert values["2026-06-01"] == 19044629192.0
    assert values["2026-07-01"] == 19298694120.0
    # входящий остаток следующего месяца равен исходящему предыдущего — инвариант формы
    assert values["2026-06-01"] == 19044629192.0


# ─────────────────────────── сборка банка ───────────────────────────


def _stub_series(monkeypatch, by_code):
    def fake(reg_num, ind_code, date_from, date_to):
        return by_code.get(ind_code, {})
    monkeypatch.setattr(credit, "series", fake)


def test_gross_portfolio_sums_only_declared_parts(monkeypatch):
    """Просроченные проценты в валовый портфель не входят — это не тело кредита."""
    _stub_series(monkeypatch, {
        "45.0": {"2026-07-01": 24_627_000_000},      # тыс. ₽ → 24 627 млрд
        "45.2": {"2026-07-01": 19_298_000_000},
        "458": {"2026-07-01": 1_722_000_000},
        "459": {"2026-07-01": 332_000_000},
    })

    bank = credit.build_bank({"ticker": "SBER", "name": "Сбербанк", "regnum": 1481},
                             "2021-08-01", "2026-07-01")

    assert bank["status"] == "ok"
    assert bank["latest"]["retail"] == pytest.approx(19298.0)
    assert bank["latest"]["corporate"] == pytest.approx(24627.0)
    assert bank["latest"]["gross"] == pytest.approx(24627.0 + 19298.0 + 1722.0)
    assert bank["latest"]["overdue_interest"] == pytest.approx(332.0)


def test_missing_month_is_not_turned_into_zero(monkeypatch):
    """Пропуск в источнике — «нет данных», а не обнуление портфеля на графике."""
    _stub_series(monkeypatch, {
        "45.0": {"2026-06-01": 1_000_000, "2026-07-01": 1_100_000},
        "45.2": {"2026-07-01": 500_000},
    })

    bank = credit.build_bank({"ticker": "X", "name": "X", "regnum": 1}, "2026-06-01", "2026-07-01")

    june = next(r for r in bank["rows"] if r["d"] == "2026-06-01")
    assert "retail" not in june, "отсутствующий месяц не должен подменяться нулём"
    assert june["gross"] == pytest.approx(1.0)


def test_bank_without_form_101_is_marked_not_dropped(monkeypatch):
    """У МКБ формы 101 в сервисе нет — банк остаётся в файле со статусом."""
    _stub_series(monkeypatch, {})

    bank = credit.build_bank({"ticker": "CBOM", "name": "МКБ", "regnum": 1978},
                             "2021-08-01", "2026-07-01")

    assert bank["status"] == "unavailable" and bank["reason"]
    assert "rows" not in bank


def test_one_broken_code_does_not_break_the_bank(monkeypatch):
    def fake_series(reg_num, ind_code, date_from, date_to):
        if ind_code == "458":
            raise RuntimeError("SOAP timeout")
        return {"2026-07-01": 1_000_000}
    monkeypatch.setattr(credit, "series", fake_series)

    bank = credit.build_bank({"ticker": "X", "name": "X", "regnum": 1}, "2026-06-01", "2026-07-01")

    assert bank["status"] == "ok"
    assert bank["latest"]["overdue"] is None, "сбойный код — «нет данных», не ноль"
    assert bank["latest"]["retail"] == pytest.approx(1.0)


# ─────────────────────────── честность подачи ───────────────────────────


def test_meta_separates_official_names_from_our_matching(monkeypatch):
    """Кодов 45.0/45.2 нет в справочнике ЦБ — читатель обязан знать, откуда взято отнесение."""
    _stub_series(monkeypatch, {"45.0": {"2026-07-01": 1_000_000}})
    monkeypatch.setattr(credit, "month_starts", lambda n, today: ["2026-07-01", "2026-06-01"])

    meta = credit.build(datetime(2026, 8, 7, tzinfo=timezone.utc))["meta"]

    assert "РСБУ" in meta["accounting"] and "не МСФО" in meta["accounting"]
    assert "iitg" in meta["balance"]
    assert "ap=1" in meta["side"]
    assert "справочник" in meta["aggregate_note"].lower()
    assert "сходимостью" in meta["aggregate_note"]
    assert "Резервы" in meta["not_published"] and "cost of risk" in meta["not_published"]


def test_reserves_and_coverage_are_never_emitted(monkeypatch):
    """Пассивные строки похожи на резервы, но подтверждения нет — их не должно быть в выходе."""
    _stub_series(monkeypatch, {"45.0": {"2026-07-01": 1_000_000}})
    monkeypatch.setattr(credit, "month_starts", lambda n, today: ["2026-07-01"])

    payload = credit.build(datetime(2026, 8, 7, tzinfo=timezone.utc))
    blob = json.dumps(payload, ensure_ascii=False)

    for forbidden in ('"reserves"', '"coverage"', '"cost_of_risk"', '"stage3"'):
        assert forbidden not in blob


def test_month_starts_walks_back_across_the_year_boundary():
    dates = credit.month_starts(4, datetime(2026, 2, 15, tzinfo=timezone.utc))
    assert dates == ["2026-02-01", "2026-01-01", "2025-12-01", "2025-11-01"]
