# -*- coding: utf-8 -*-
"""Контракт корпоративного правопреемства: склейка рядов только по доказательству.

Задача: продлить ряд действующей бумаги историей предшественника (YNDX→YDEX, FIVE→X5),
не создав при этом фиктивной доходности на стыке.

Проверенные факты (MOEX ISS, 30.07.2026):
  YNDX→YDEX — на МЕСЯЧНОЙ сетке дыры НЕТ (YNDX по 2024-06 close 4071,2; YDEX с 2024-07
              close 3892,0). Стык −4,40% — рыночное движение после возобновления торгов,
              обмен 1:1, сплита нет → склейка законна, ряд 25 → 96 мес (обрезано окном).
  FIVE→X5   — по ДНЕВНЫМ ценам стык почти идеален (2798,0 → 2803,0 = +0,18%), но на
              МЕСЯЧНОЙ сетке между 2024-04 и 2025-01 дыра в 8 месяцев остановленных
              торгов. Склейка дала бы один месяц +15,51%, накопившийся за 8 неторговых, —
              это фабрикация, поэтому гейт её отвергает. X5 остаётся short_history.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG = ROOT / "data" / "corporate_lineage.json"
RETURNS = ROOT / "model_output" / "returns.json"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lineage_meta() -> dict:
    meta = json.loads(RETURNS.read_text(encoding="utf-8")).get("meta") or {}
    return meta.get("lineage") or {}


def test_config_declares_evidence_for_every_lineage(cfg):
    """Каждая склейка обязана нести доказательство и коэффициент — не «похоже по названию»."""
    rows = cfg["lineages"]
    assert rows, "файл правопреемств пуст"
    for r in rows:
        assert r.get("predecessor") and r.get("successor")
        assert isinstance(r.get("ratio"), (int, float)) and r["ratio"] > 0
        assert r.get("junction") and r.get("verified_at")
        assert len(str(r.get("evidence") or "")) > 80, f"{r['successor']}: нет внятного обоснования"


def test_no_lineage_is_derived_from_name_similarity():
    """Резолвер тикеров не должен молча превращать один код в другой.

    Именно этот механизм создал бы незаметный дубль позиции (MMK→MAGN) или доходность
    на временном разрыве (FIVE→X5). Подсказки допустимы, автоподмена — нет.
    """
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    start = app.index("const PFX_ALIASES")
    aliases = app[start:app.index("};", start)]
    for forbidden in ("'MMK'", '"MMK"', "'YNDX'", '"YNDX"', "'FIVE'", '"FIVE"', "'RSTI'", '"RSTI"'):
        assert f"{forbidden}:" not in aliases, f"{forbidden} подменяется в PFX_ALIASES — запрещено"


def test_ydex_lineage_applied_without_gap(lineage_meta):
    """YNDX→YDEX: склейка применена, дыры нет, стык в пределах рыночного движения."""
    rec = lineage_meta.get("YDEX")
    assert rec, "провенанс YDEX отсутствует в meta.lineage"
    assert rec["status"] == "applied", rec.get("reason")
    assert rec["predecessor"] == "YNDX" and rec["ratio"] == 1.0
    assert rec["gap_months"] == 0, "склейка при дыре недопустима"
    assert abs(rec["junction_return"]) < 0.10, (
        f"стык {rec['junction_return']:+.2%} слишком велик для конверсии 1:1")
    assert rec["months_added"] > 100


def test_x5_lineage_rejected_with_reason(lineage_meta):
    """FIVE→X5: отказ ЗАФИКСИРОВАН в данных, а не пропущен молча."""
    rec = lineage_meta.get("X5")
    assert rec, "провенанс X5 отсутствует — отказ должен быть виден"
    assert rec["status"] == "rejected"
    assert rec["gap_months"] == 8, f"ожидалась дыра 8 мес, получено {rec.get('gap_months')}"
    assert "дыра" in rec["reason"]
    assert rec.get("applied") is False


def test_gate_rejects_gap_and_implausible_junction():
    """Гейты работают на синтетике: дыра и «сплитный» стык отвергаются."""
    import build_momentum as bm

    succ = [("2025-01-31", 100.0, 1), ("2025-02-28", 102.0, 1)]
    # 1) дыра в месяцах
    pred_gap = [("2024-01-31", 99.0, 1), ("2024-02-29", 100.0, 1)]
    # загрузчик свечей подменяем, чтобы гейты проверялись без сети
    orig = bm.fetch_candles
    try:
        bm.fetch_candles = lambda tk, **kw: pred_gap
        out, note = bm.apply_lineage("SUCC", {"predecessor": "PRED", "ratio": 1.0}, succ)
        assert note["status"] == "rejected" and note["gap_months"] == 10
        assert out == succ, "при отказе ряд преемника не должен меняться"

        # 2) стык вне порога (нераспознанный сплит 1:10)
        pred_split = [("2024-11-30", 900.0, 1), ("2024-12-31", 1000.0, 1)]
        bm.fetch_candles = lambda tk, **kw: pred_split
        out2, note2 = bm.apply_lineage("SUCC", {"predecessor": "PRED", "ratio": 1.0}, succ)
        assert note2["status"] == "rejected", "стык −90% обязан быть отвергнут"
        assert out2 == succ

        # 3) корректная склейка проходит
        pred_ok = [("2024-11-30", 98.0, 1), ("2024-12-31", 99.0, 1)]
        bm.fetch_candles = lambda tk, **kw: pred_ok
        out3, note3 = bm.apply_lineage("SUCC", {"predecessor": "PRED", "ratio": 1.0}, succ)
        assert note3["status"] == "applied" and note3["gap_months"] == 0
        assert len(out3) == 4 and out3[0][0] == "2024-11-30"
    finally:
        bm.fetch_candles = orig


def test_ratio_is_applied_to_predecessor_prices():
    """Коэффициент конверсии умножает цены предшественника, а не создаёт прыжок."""
    import build_momentum as bm

    succ = [("2025-01-31", 100.0, 1)]
    pred = [("2024-12-31", 1000.0, 1)]        # предшественник в 10× масштабе
    orig = bm.fetch_candles
    try:
        bm.fetch_candles = lambda tk, **kw: pred
        out, note = bm.apply_lineage("SUCC", {"predecessor": "PRED", "ratio": 0.1}, succ)
        assert note["status"] == "applied", note["reason"]
        assert out[0][1] == pytest.approx(100.0), "цена предшественника должна быть приведена"
        assert abs(note["junction_return"]) < 1e-9, "после приведения стык обязан быть ~0"
    finally:
        bm.fetch_candles = orig


def test_x5_keeps_short_history_status():
    """X5 без склейки остаётся с собственной короткой историей — не подделываем."""
    data = json.loads(RETURNS.read_text(encoding="utf-8"))["data"]
    x5 = [v for v in (data.get("X5") or []) if isinstance(v, (int, float))]
    assert x5, "у X5 должна быть собственная история"
    assert len(x5) < 36, f"X5 неожиданно получил {len(x5)} мес — проверьте, не склеили ли FIVE"
