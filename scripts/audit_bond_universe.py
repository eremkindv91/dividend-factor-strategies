#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Доказательный аудит выборки облигаций: от источника MOEX до итогового портфеля.

Скрипт ТОЛЬКО ЧИТАЕТ. Он импортирует production-функции (_eligible, solve_target_portfolio,
_candidate) и прогоняет их на опубликованных данных, чтобы ответить на вопросы фактами,
а не чтением названий функций:

  • сколько выпусков отдаёт MOEX и сколько доходит до конструктора;
  • на каком именно шаге и почему выпадает каждая бумага;
  • есть ли скрытый top-N и объясняет ли он размер портфеля;
  • почему «Сбалансированный» и «Доходный» дают почти одинаковый состав;
  • почему в портфеле есть ОФЗ;
  • что означает горизонт «3 года».

Запуск:
    python scripts/audit_bond_universe.py                 # на опубликованных site/bonds
    python scripts/audit_bond_universe.py --live          # + сверка с живым MOEX ISS
    python scripts/audit_bond_universe.py --json out.json # машиночитаемый audit trail
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bonds.portfolio_engine import _eligible                     # noqa: E402
from bonds.universe_builder import RATING_RANK, _candidate       # noqa: E402

SITE_BONDS = ROOT / "site" / "bonds"
CONFIG = json.loads((ROOT / "bonds" / "portfolio_config.json").read_text(encoding="utf-8"))
ISS = "https://iss.moex.com/iss"


def load(name: str) -> dict:
    return json.loads((SITE_BONDS / name).read_text(encoding="utf-8"))


# ── Этап 2: полнота исходной базы ────────────────────────────────────────────
def live_board_counts() -> dict:
    """Сколько бумаг реально отдаёт MOEX и сколько из них проходит _candidate.

    Пагинация проверяется отдельно: запрашиваем start=0 и start=100 и сравниваем
    множества SECID. Комментарий в load_board утверждает, что борд отдаёт всё разом
    и игнорирует start — это утверждение и проверяем, а не принимаем на веру.
    """
    import urllib.request

    def fetch(board: str, start: int) -> list[dict]:
        url = (f"{ISS}/engines/stock/markets/bonds/boards/{board}/securities.json"
               f"?iss.meta=off&start={start}")
        req = urllib.request.Request(url, headers={"User-Agent": "bond-audit"})
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        block = payload.get("securities") or {}
        cols = block.get("columns") or []
        rows = [dict(zip(cols, row)) for row in (block.get("data") or [])]
        md_block = payload.get("marketdata") or {}
        md_cols = md_block.get("columns") or []
        md = {r[md_cols.index("SECID")]: dict(zip(md_cols, r)) for r in (md_block.get("data") or [])}
        for row in rows:
            row["_md"] = md.get(row.get("SECID"), {})
        return rows

    out = {}
    for board in ("TQCB", "TQOB"):
        page0 = fetch(board, 0)
        page1 = fetch(board, 100)
        ids0 = {r["SECID"] for r in page0}
        ids1 = {r["SECID"] for r in page1}
        minimum = float(CONFIG["universe"]["minimum_value_today_rub"])
        passed = [r for r in page0 if _candidate(r, minimum)]
        out[board] = {
            "total_rows": len(page0),
            "start_100_rows": len(page1),
            "start_100_identical": ids0 == ids1,
            "start_100_new_secids": len(ids1 - ids0),
            "passed_candidate_filter": len(passed),
            "rejected_by_candidate": len(page0) - len(passed),
        }
    return out


def candidate_rejection_reasons(board_rows: list[dict], minimum: float) -> Counter:
    """Почему бумага не проходит _candidate. Повторяет условия production-функции
    по одному, чтобы получить разбивку (сама функция возвращает только bool)."""
    from bonds.universe_builder import _iso, _market_clean, _num

    reasons: Counter = Counter()
    for raw in board_rows:
        if str(raw.get("FACEUNIT") or "").upper() not in {"SUR", "RUB", "RUR"}:
            reasons["NOT_RUBLE"] += 1
            continue
        if "валют" in str(raw.get("BONDTYPE") or "").lower():
            reasons["FX_BOND"] += 1
            continue
        market = raw.get("_md") or {}
        value_today = _num(market.get("VALTODAY_RUR")) or _num(market.get("VALTODAY"), 0.0) or 0.0
        if not _market_clean(raw):
            reasons["NO_MARKET_PRICE"] += 1
        elif not _num(raw.get("FACEVALUE")):
            reasons["NO_FACE_VALUE"] += 1
        elif not _iso(raw.get("MATDATE")):
            reasons["NO_MATURITY_DATE"] += 1
        elif value_today < minimum:
            reasons["VOLUME_BELOW_FLOOR"] += 1
        else:
            reasons["PASSED"] += 1
    return reasons


# ── Этапы 4–5: audit trail и воронка ─────────────────────────────────────────
def audit_profile(universe: dict, profile_key: str, horizon_key: str, budget: float) -> dict:
    """Прогоняет production-функцию _eligible по всему универсуму и собирает
    полный trail: кто прошёл, кто нет и по какой конкретной причине."""
    profile = CONFIG["profiles"][profile_key]
    horizon = CONFIG["horizons"][horizon_key]
    bonds = universe.get("bonds") or []

    trail = []
    eligible = []
    reason_counts: Counter = Counter()
    for row in bonds:
        ok, reasons = _eligible(row, profile, CONFIG, budget)
        entry = {
            "secid": row.get("secid"),
            "name": row.get("name"),
            "issuer": row.get("issuer_name"),
            "issuer_id": row.get("issuer_id"),
            "instrument_type": row.get("instrument_type"),
            "rating": row.get("rating"),
            "rating_rank": row.get("rating_rank"),
            "duration": row.get("duration_value"),
            "maturity_date": row.get("maturity_date"),
            "years_to_maturity": row.get("years_to_maturity"),
            "ytm_net_est_pct": row.get("ytm_net_est_pct"),
            "median_volume_20d_rub": row.get("median_volume_20d_rub"),
            "history_sessions": row.get("history_sessions"),
            "eligible": ok,
            "exclusion_reason_codes": reasons,
        }
        trail.append(entry)
        if ok:
            eligible.append(row)
        else:
            reason_counts[reasons[0]] += 1        # основная причина — первая по порядку проверок
            for extra in reasons[1:]:
                reason_counts[f"+{extra}"] += 1

    # горизонт применяется НЕ фильтром, а ограничением на средневзвешенную дюрацию
    inside = [r for r in eligible
              if float(horizon["min"]) <= float(r["duration_value"]) <= float(horizon["max"])]
    issuers = {str(r["issuer_id"]) for r in eligible}
    return {
        "profile": profile_key,
        "horizon": horizon_key,
        "universe_total": len(bonds),
        "eligible": len(eligible),
        "eligible_secids": sorted(r["secid"] for r in eligible),
        "issuers": len(issuers),
        "inside_duration_corridor": len(inside),
        "corridor": [horizon["min"], horizon["target"], horizon["max"]],
        "exclusion_reasons": dict(reason_counts.most_common()),
        "minimum_issues_required": profile["minimum_issues"],
        "minimum_issuers_required": profile["minimum_issuers"],
        "trail": trail,
    }


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def published_allocation(presets: dict, key: str) -> dict:
    return (presets.get("allocations") or {}).get(key) or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="сверить с живым MOEX ISS")
    parser.add_argument("--json", dest="json_out", help="куда положить машиночитаемый trail")
    parser.add_argument("--horizon", default="3y")
    args = parser.parse_args()

    universe = load("universe.json")
    presets = load("portfolio_presets.json")
    validation = load("portfolio_validation.json")
    budget = float(CONFIG["default_budget_rub"])

    print("=" * 78)
    print("АУДИТ ВЫБОРКИ ОБЛИГАЦИЙ")
    print("=" * 78)
    print(f"данные универсума: {universe.get('generated_at')}")
    print(f"бюджет по умолчанию: {budget:,.0f} ₽".replace(",", " "))
    print(f"выпусков в universe.json: {len(universe.get('bonds') or [])}")
    counts = Counter(r.get("instrument_type") for r in (universe.get("bonds") or []))
    print(f"  по типу: {dict(counts)}")

    if args.live:
        print("\n── ЭТАП 2: полнота исходной базы (живой MOEX ISS) ──")
        live = live_board_counts()
        for board, stats in live.items():
            print(f"  {board}: отдано {stats['total_rows']} бумаг, "
                  f"проходят _candidate {stats['passed_candidate_filter']}")
            print(f"    пагинация: start=100 вернул {stats['start_100_rows']} строк, "
                  f"идентично start=0: {stats['start_100_identical']}, "
                  f"новых SECID: {stats['start_100_new_secids']}")
        cap = int(CONFIG["universe"]["maximum_corporate_enrichment"])
        corp_pass = live["TQCB"]["passed_candidate_filter"]
        print(f"\n  ОТСЕЧКА: maximum_corporate_enrichment={cap}")
        print(f"    корпоратов прошло _candidate: {corp_pass}")
        print(f"    попадёт в универсум: {min(cap, corp_pass)}"
              f"{'  ← ОБРЕЗАНО' if corp_pass > cap else '  (обрезки нет)'}")

    print(f"\n── ЭТАПЫ 4–5: воронка по профилям, горизонт {args.horizon} ──")
    results = {}
    for profile_key in ("defensive", "balanced", "income"):
        res = audit_profile(universe, profile_key, args.horizon, budget)
        results[profile_key] = res
        key = f"{profile_key}:{args.horizon}"
        alloc = published_allocation(presets, key)
        positions = alloc.get("positions") or []
        label = CONFIG["profiles"][profile_key]["label"]
        print(f"\n  {label} ({profile_key})")
        print(f"    универсум: {res['universe_total']}")
        print(f"    прошли _eligible: {res['eligible']}  (эмитентов {res['issuers']}, "
              f"нужно ≥{res['minimum_issues_required']} выпусков / ≥{res['minimum_issuers_required']} эмитентов)")
        print(f"    внутри коридора дюрации {res['corridor'][0]}–{res['corridor'][2]}: "
              f"{res['inside_duration_corridor']}")
        print(f"    в опубликованном портфеле: {len(positions)}")
        print("    причины исключения (основные):")
        for reason, count in list(res["exclusion_reasons"].items())[:8]:
            if not reason.startswith("+"):
                print(f"      {reason:44} {count}")

    print("\n── ЭТАП 7: сходство профилей ──")
    sets = {k: set(v["eligible_secids"]) for k, v in results.items()}
    pairs = [("defensive", "balanced"), ("defensive", "income"), ("balanced", "income")]
    for a, b in pairs:
        print(f"  candidate universe  Jaccard({a}, {b}) = {jaccard(sets[a], sets[b]):.3f}"
              f"   ({len(sets[a] & sets[b])} общих из {len(sets[a] | sets[b])})")
    for a, b in pairs:
        pa = {p["secid"] for p in (published_allocation(presets, f"{a}:{args.horizon}").get("positions") or [])}
        pb = {p["secid"] for p in (published_allocation(presets, f"{b}:{args.horizon}").get("positions") or [])}
        if pa or pb:
            print(f"  итоговый портфель   Jaccard({a}, {b}) = {jaccard(pa, pb):.3f}"
                  f"   ({len(pa & pb)} общих)")

    print("\n── ЭТАП 8: ОФЗ в портфелях ──")
    for profile_key in ("defensive", "balanced", "income"):
        prof = CONFIG["profiles"][profile_key]
        alloc = published_allocation(presets, f"{profile_key}:{args.horizon}")
        positions = alloc.get("positions") or []
        by_secid = {r["secid"]: r for r in (universe.get("bonds") or [])}
        ofz = [p for p in positions if (by_secid.get(p["secid"]) or {}).get("instrument_type") == "ofz"]
        ofz_weight = sum(float(p.get("actual_weight") or 0) for p in ofz)
        print(f"  {prof['label']:16} minimum_ofz={prof['minimum_ofz']:.0%} → "
              f"фактически {ofz_weight:.1%} ({len(ofz)} из {len(positions)} позиций)")

    print("\n── ЭТАП 9: что такое горизонт ──")
    h = CONFIG["horizons"][args.horizon]
    print(f"  «{h['label']}» = коридор МОДИФИЦИРОВАННОЙ ДЮРАЦИИ {h['min']}–{h['max']} "
          f"с целью {h['target']}, а НЕ срок до погашения")
    for profile_key in ("defensive", "balanced", "income"):
        alloc = published_allocation(presets, f"{profile_key}:{args.horizon}")
        positions = alloc.get("positions") or []
        if not positions:
            continue
        by_secid = {r["secid"]: r for r in (universe.get("bonds") or [])}
        mats = [str((by_secid.get(p["secid"]) or {}).get("maturity_date") or "")[:4] for p in positions]
        print(f"  {CONFIG['profiles'][profile_key]['label']:16} годы погашения бумаг: "
              f"{dict(sorted(Counter(m for m in mats if m).items()))}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "generated_from": universe.get("generated_at"),
            "budget_rub": budget,
            "config_profiles": CONFIG["profiles"],
            "config_horizons": CONFIG["horizons"],
            "config_universe": CONFIG["universe"],
            "allowed_instruments": CONFIG["allowed_instruments"],
            "profiles": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nмашиночитаемый trail → {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
