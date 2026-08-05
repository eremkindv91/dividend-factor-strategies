#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диагностические тесты выборки облигаций (docs/bond-universe-selection-audit.md).

Тесты НЕ меняют production-поведение. Они фиксируют факты, установленные аудитом, чтобы
изменение любого из них было заметным, а не молчаливым:

  • универсум ограничен отсечкой, а не потерян по дороге;
  • у каждой исключённой бумаги есть конкретный код причины;
  • профили действительно различаются в конфиге;
  • ОФЗ присутствуют из-за обязательной квоты, а не протечки фильтра;
  • горизонт — это коридор дюрации, и он не выдаётся за срок погашения;
  • перед оптимизатором нет обрезки списка кандидатов.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bonds.portfolio_engine import _eligible  # noqa: E402

CONFIG = json.loads((ROOT / "bonds" / "portfolio_config.json").read_text(encoding="utf-8"))
SITE_BONDS = ROOT / "site" / "bonds"
BUDGET = 1_000_000.0


def _universe() -> list[dict]:
    return json.loads((SITE_BONDS / "universe.json").read_text(encoding="utf-8"))["bonds"]


def test_universe_size_matches_declared_enrichment_cap():
    """Число корпоратов в универсуме = отсечке из конфига, а не случайной величине.

    Если однажды окажется меньше — значит бумаги теряются где-то ещё, и это надо
    расследовать отдельно, а не списывать на лимит.
    """
    universe = _universe()
    corp = [row for row in universe if row.get("instrument_type") == "corp"]
    cap = int(CONFIG["universe"]["maximum_corporate_enrichment"])

    assert len(corp) <= cap, "корпоратов больше лимита — отсечка перестала работать"
    assert len(corp) == cap, (
        f"корпоратов {len(corp)} при лимите {cap}: если источник отдаёт меньше кандидатов, "
        "это отдельная проблема — проверьте _candidate и доступность MOEX"
    )


def test_every_excluded_bond_has_concrete_reason_code():
    """Ни одна бумага не может быть исключена без конкретной причины."""
    universe = _universe()
    for profile_key in ("defensive", "balanced", "income"):
        profile = CONFIG["profiles"][profile_key]
        for row in universe:
            ok, reasons = _eligible(row, profile, CONFIG, BUDGET)
            if ok:
                assert not reasons, f"{row['secid']}: прошла, но есть причины отказа"
            else:
                assert reasons, f"{row['secid']} ({profile_key}): исключена без причины"
                assert all(isinstance(r, str) and r for r in reasons)
                # запрет расплывчатых формулировок
                assert not any(r.strip().lower() in {"filtered", "excluded", "not_eligible"}
                               for r in reasons), f"{row['secid']}: неинформативная причина {reasons}"


def test_profiles_are_actually_different():
    """Три профиля обязаны различаться конфигом, иначе выбор пользователя ничего не значит."""
    profiles = CONFIG["profiles"]
    keys = ("minimum_corporate_rating", "minimum_ofz", "max_bbb",
            "minimum_median_volume_20d_rub", "minimum_trading_sessions")
    seen = {}
    for name in ("defensive", "balanced", "income"):
        signature = tuple(profiles[name][k] for k in keys)
        assert signature not in seen, f"{name} и {seen.get(signature)} имеют одинаковый профиль"
        seen[signature] = name

    # доходный обязан быть мягче защитного по рейтингу и ликвидности
    assert profiles["income"]["minimum_median_volume_20d_rub"] < profiles["defensive"]["minimum_median_volume_20d_rub"]
    assert profiles["income"]["minimum_ofz"] < profiles["defensive"]["minimum_ofz"]


def test_ofz_presence_is_a_declared_quota_not_a_leak():
    """ОФЗ в портфеле — следствие minimum_ofz, а не протечки фильтра типа инструмента."""
    presets = json.loads((SITE_BONDS / "portfolio_presets.json").read_text(encoding="utf-8"))
    universe = {row["secid"]: row for row in _universe()}

    for profile_key in ("defensive", "balanced", "income"):
        quota = float(CONFIG["profiles"][profile_key]["minimum_ofz"])
        allocation = (presets.get("allocations") or {}).get(f"{profile_key}:3y")
        if not allocation:
            continue
        positions = allocation.get("positions") or []
        ofz_weight = sum(
            float(p.get("actual_weight") or 0)
            for p in positions
            if (universe.get(p["secid"]) or {}).get("instrument_type") == "ofz"
        )
        assert quota > 0, f"{profile_key}: квота ОФЗ должна быть объявлена в конфиге"
        assert ofz_weight >= quota - 0.02, (
            f"{profile_key}: доля ОФЗ {ofz_weight:.1%} ниже объявленной квоты {quota:.0%}"
        )


def test_horizon_is_a_duration_corridor_not_a_maturity_limit():
    """Горизонт ограничивает ДЮРАЦИЮ. Тест фиксирует это, чтобы подпись в интерфейсе
    не начали трактовать как срок погашения: в портфеле «3 года» есть бумаги 2033–2038."""
    horizon = CONFIG["horizons"]["3y"]
    assert horizon["min"] < horizon["target"] < horizon["max"]
    assert horizon["max"] < 3.0, (
        "коридор дюрации для «3 года» меньше трёх лет — значит это НЕ срок погашения"
    )

    presets = json.loads((SITE_BONDS / "portfolio_presets.json").read_text(encoding="utf-8"))
    universe = {row["secid"]: row for row in _universe()}
    allocation = (presets.get("allocations") or {}).get("balanced:3y")
    if not allocation:
        pytest.skip("balanced:3y не опубликован")
    years = [
        str((universe.get(p["secid"]) or {}).get("maturity_date") or "")[:4]
        for p in (allocation.get("positions") or [])
    ]
    assert any(int(y) > 2029 for y in years if y), (
        "если все бумаги укладываются в 3 года, подпись горизонта перестала вводить "
        "в заблуждение — обновите docs/bond-universe-selection-audit.md"
    )


def test_no_top_n_cut_before_optimizer():
    """Перед MILP список кандидатов не обрезается.

    Проверяется по исходному коду: в solve_target_portfolio кандидаты собираются
    полным проходом по универсуму без срезов.
    """
    source = (ROOT / "bonds" / "portfolio_engine.py").read_text(encoding="utf-8")
    start = source.index("def solve_target_portfolio")
    end = source.index("\ndef ", start + 10)
    body = source[start:end]

    assert "for row in sorted(universe.get(\"bonds\")" in body
    for pattern in ("[:top", "[:max_", "[:limit", ".head(", "candidateLimit"):
        assert pattern not in body, f"в оптимизаторе появилась обрезка списка: {pattern}"


def test_screener_top_n_is_separate_from_portfolio_pipeline():
    """TOP_N=300 живёт в ветке скринера и НЕ влияет на портфели.

    Это разные пути: build_screener → screener.json, build_live_universe → universe.json.
    Смешать их — значит объяснять состав портфеля не тем ограничением.
    """
    update = (ROOT / "bonds" / "update_bonds.py").read_text(encoding="utf-8")
    engine = (ROOT / "bonds" / "portfolio_engine.py").read_text(encoding="utf-8")
    builder = (ROOT / "bonds" / "universe_builder.py").read_text(encoding="utf-8")

    assert "TOP_N" in update, "отсечка скринера должна оставаться явной константой"
    assert "TOP_N" not in engine and "TOP_N" not in builder, (
        "TOP_N скринера просочился в портфельный путь"
    )
    assert "maximum_corporate_enrichment" in builder, "отсечка универсума должна быть явной"
