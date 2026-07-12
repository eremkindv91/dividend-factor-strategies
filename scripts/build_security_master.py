#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 1: Security Master.

Единый детерминированный реестр бумаг универсума MOEX для будущего дневного слоя данных
и point-in-time бэктеста. Чистый stdlib, БЕЗ сети: строится из уже имеющихся артефактов
(site/data.json — активный универсум) + курируемой таблицы корпдействий
(data/security_master_overrides.json). Идемпотентно, атомарная запись, версия схемы.

Для каждой бумаги: canonical_ticker, secid, isin/figi (offline → null, не выдумываем),
board, instrument_type, share_class, base_ticker (ord/pref-связка БЕЗ слияния классов),
listing_start/end, previous_tickers, successor_ticker, corporate_action_refs, status.

Обыкновенные и привилегированные — ВСЕГДА отдельные записи (не сливаем классы).
Исторические/переименованные тикеры сохраняются (survivorship: их нельзя терять из
исторического универсума). Артефакт — data/security_master.json (tracked).

CLI: python scripts/build_security_master.py [out_path]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.path.join(REPO, "site", "data.json")
OVERRIDES = os.path.join(REPO, "data", "security_master_overrides.json")
DEFAULT_OUT = os.path.join(REPO, "data", "security_master.json")

SCHEMA_VERSION = 1
METHODOLOGY_VERSION = "sec-master-1"
DEFAULT_BOARD = "TQBR"           # розничная доска акций MOEX; secid == тикер по соглашению доски
STATUS_ACTIVE = "active"
STATUS_RENAMED = "renamed"
STATUS_DELISTED = "delisted"


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _load_universe(data_json_path):
    """{ticker: {name, sector}} из активного универсума site/data.json."""
    d = _load(data_json_path, {}) or {}
    uni = {}
    for t in d.get("tickers", []):
        tk = t.get("ticker")
        if tk:
            uni[tk] = {"name": t.get("name"), "sector": t.get("sector")}
    return uni


def _resolve_alias_chains(aliases):
    """old→new граф → {successor: [ancestors...]}, {old: final_successor}. Разрешает цепочки TCS→TCSG→T."""
    nxt = {}
    for a in aliases:
        o, n = a.get("old"), a.get("new")
        if o and n:
            nxt[o] = n
    final = {}
    for o in nxt:
        seen, cur = set(), o
        while cur in nxt and cur not in seen:
            seen.add(cur)
            cur = nxt[cur]
        final[o] = cur                       # финальный преемник цепочки
    ancestors = {}                           # successor → все предки (в порядке давности: старейший первым)
    for o, fin in final.items():
        ancestors.setdefault(fin, [])
        ancestors[fin].append(o)
    # порядок: чем дальше от финала, тем «старее» → сортируем по длине пути до финала (убыв.)
    def dist(o):
        d, cur = 0, o
        while cur in nxt and cur != final[o]:
            d += 1
            cur = nxt[cur]
        return d
    for fin in ancestors:
        ancestors[fin] = sorted(set(ancestors[fin]), key=dist, reverse=True)
    return final, ancestors, nxt


def _refs_for(ticker, aliases):
    """corporate_action_refs, где тикер участвует как old ИЛИ new."""
    out = []
    for a in aliases:
        if a.get("old") == ticker or a.get("new") == ticker:
            out.append({"type": a.get("type"), "from": a.get("old"), "to": a.get("new"),
                        "date": a.get("date"), "date_confidence": a.get("date_confidence"),
                        "source": a.get("source"), "note": a.get("note")})
    return out


def _classify(ticker, universe, preferred_no_ord, base_overrides, itype_overrides):
    """(instrument_type, share_class, base_ticker). Преф ТОЛЬКО если ord-близнец в универсуме
    или тикер в курируемом preferred_no_ord — НЕ по суффиксу 'P' (GAZP/NMTP/RASP — обыкновенные)."""
    is_pref = (ticker.endswith("P") and ticker[:-1] in universe) or ticker in preferred_no_ord
    share_class = "preferred" if is_pref else "ordinary"
    if ticker in base_overrides:
        base = base_overrides[ticker]
    elif is_pref and ticker.endswith("P") and ticker[:-1] in universe:
        base = ticker[:-1]
    else:
        base = ticker
    itype = itype_overrides.get(ticker, "share")
    return itype, share_class, base


def build(now=None, data_json_path=DATA_JSON, overrides_path=OVERRIDES) -> dict:
    now = now or datetime.now(timezone.utc)
    universe = _load_universe(data_json_path)
    ov = _load(overrides_path, {}) or {}
    aliases = ov.get("aliases", []) or []
    preferred_no_ord = set(ov.get("preferred_no_ord", []) or [])
    base_overrides = ov.get("base_overrides", {}) or {}
    itype_overrides = ov.get("instrument_type_overrides", {}) or {}
    delisted = ov.get("delisted", []) or []

    final, ancestors, nxt = _resolve_alias_chains(aliases)

    secs = {}

    def _new(ticker, status):
        itype, sclass, base = _classify(ticker, universe, preferred_no_ord, base_overrides, itype_overrides)
        meta = universe.get(ticker, {})
        return {
            "canonical_ticker": ticker,
            "secid": ticker,                 # соглашение доски TQBR
            "name": meta.get("name"),
            "sector": meta.get("sector"),
            "isin": None,                    # offline недоступно → null, не выдумываем
            "figi": None,
            "board": DEFAULT_BOARD,
            "instrument_type": itype,
            "share_class": sclass,
            "base_ticker": base,
            "listing_start": None,
            "listing_end": None,
            "previous_tickers": sorted(ancestors.get(ticker, [])),
            "successor_ticker": nxt.get(ticker),
            "corporate_action_refs": _refs_for(ticker, aliases),
            "status": status,
        }

    # 1) активный универсум
    for tk in universe:
        secs[tk] = _new(tk, STATUS_ACTIVE)
    # 2) исторические тикеры из алиасов (old, которых нет в активном универсуме) — сохраняем (survivorship)
    for a in aliases:
        old = a.get("old")
        if old and old not in secs:
            rec = _new(old, STATUS_RENAMED)
            rec["successor_ticker"] = final.get(old)
            rec["listing_end"] = a.get("date")     # приближённо: дата переименования как конец листинга старого
            secs[old] = rec
    # 3) явные делистинги вне универсума
    for d in delisted:
        tk = d.get("ticker") if isinstance(d, dict) else d
        if tk and tk not in secs:
            rec = _new(tk, STATUS_DELISTED)
            if isinstance(d, dict):
                rec["listing_end"] = d.get("date")
                rec["corporate_action_refs"].append({"type": "delisting", "date": d.get("date"),
                                                      "date_confidence": d.get("date_confidence"),
                                                      "source": d.get("source"), "note": d.get("note")})
            secs[tk] = rec

    securities = [secs[k] for k in sorted(secs)]     # детерминированный порядок

    n_pref = sum(1 for s in securities if s["share_class"] == "preferred")
    counts = {
        "total": len(securities),
        "active": sum(1 for s in securities if s["status"] == STATUS_ACTIVE),
        "renamed": sum(1 for s in securities if s["status"] == STATUS_RENAMED),
        "delisted": sum(1 for s in securities if s["status"] == STATUS_DELISTED),
        "preferred": n_pref,
        "ordinary": len(securities) - n_pref,
        "with_alias": sum(1 for s in securities if s["previous_tickers"] or s["successor_ticker"]),
        "with_isin": sum(1 for s in securities if s["isin"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source": "MOEX ISS universe (site/data.json) + curated corporate-action overrides "
                  "(data/security_master_overrides.json); secid=ticker (доска TQBR); isin/figi offline недоступны",
        "counts": counts,
        "securities": securities,
    }


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    payload = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)                      # атомарная замена
    c = payload["counts"]
    sys.stderr.write(f"[sec-master] {c['total']} бумаг (active={c['active']} renamed={c['renamed']} "
                     f"delisted={c['delisted']} pref={c['preferred']} alias={c['with_alias']}) → {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
