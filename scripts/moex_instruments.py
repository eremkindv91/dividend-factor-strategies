#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discovery инструментов MOEX по SECID: тип, основная доска, лот, статус торгов.

ЗАЧЕМ ЭТО ПОЯВИЛОСЬ (аудит 30.07.2026). Портфель пользователя из 27 строк давал
покрытие историей 64%, и в исключения попадали действующие инструменты. Разбор показал,
что причина НЕ в доске: primary board у всех проблемных бумаг — TQBR, включая паи БПИФ
(проверено через iss/securities/<SECID>.json: EQMX и DIVD — «Пай биржевого ПИФа»,
engine=stock, market=shares, board=TQBR). Настоящая причина была в другом:

    build_momentum.py:  tickers = [r["ticker"] for r in art["tickers"]]

то есть универсум ИСТОРИИ брался строго из ML-артефакта (только акции, на которых
обучалась модель). Всё, чего в модели нет — паи БПИФ, SNGS/SNGSP, недавно размещённые
бумаги — не могло получить ряд доходностей ни при какой доске.

Этот модуль убирает вторую половину проблемы: доска, тип и лот больше не предполагаются,
а СПРАШИВАЮТСЯ у ISS. Так добавление нового класса инструментов (паи, префы, расписки)
не требует правок захардкоженных путей, а неверный тип видно сразу.

Возвращаемые типы (наши, стабильные) — см. classify():
    equity_ordinary | equity_preferred | fund | depositary_receipt | other
Статус торгов и дата начала торгов нужны, чтобы отличать «нет истории» от
«инструмент новый» и от «торги прекращены».

Чистый stdlib. Кэш на диске, чтобы не дёргать ISS на каждый прогон.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "data", "moex_instruments_cache.json")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "Mozilla/5.0 (compatible; dividend-factor-strategies/1.0)"}
CACHE_TTL_SEC = 7 * 24 * 3600          # состав инструмента меняется редко

# TYPE/GROUP из ISS → наш стабильный тип. Строки ISS могут пополняться, поэтому
# неизвестное НЕ угадываем: отдаём 'other' и инструмент честно не поддерживается.
ISS_TYPE_MAP = {
    "common_share": "equity_ordinary",
    "preferred_share": "equity_preferred",
    "etf_ppif": "fund",
    "public_ppif": "fund",
    "private_ppif": "fund",
    "exchange_ppif": "fund",
    "interval_ppif": "fund",
    "depositary_receipt": "depositary_receipt",
}


def _http_json(url: str, retries: int = 4, timeout: int = 30) -> dict:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"{url}: {last}")


def _load_cache() -> dict:
    if not os.path.exists(CACHE):
        return {}
    try:
        payload = json.load(open(CACHE, encoding="utf-8"))
        return payload.get("items", {}) if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(items: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 1, "items": items}, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE)


def classify(iss_type: str, iss_group: str) -> str:
    """ISS TYPE/GROUP → наш стабильный тип инструмента."""
    for key in (str(iss_type or "").strip().lower(), str(iss_group or "").strip().lower()):
        if key in ISS_TYPE_MAP:
            return ISS_TYPE_MAP[key]
    if "ppif" in str(iss_group or "").lower() or "ppif" in str(iss_type or "").lower():
        return "fund"
    return "other"


def describe(secid: str, use_cache: bool = True, allow_stale_cache: bool = False) -> dict:
    """Discovery одного инструмента. Ничего не выдумывает: чего нет в ISS — None.

    Возвращает:
      {secid, found, name, short_name, isin, instrument_type, iss_type, iss_group,
       board, engine, market, currency, lot_size, is_traded, listed_from, boards_all}
    """
    key = str(secid or "").strip().upper()
    if not key:
        return {"secid": secid, "found": False, "reason": "empty_secid"}

    cache = _load_cache() if use_cache else {}
    hit = cache.get(key)
    if hit and (allow_stale_cache or (time.time() - hit.get("_cached_at", 0)) < CACHE_TTL_SEC):
        return {k: v for k, v in hit.items() if k != "_cached_at"}

    try:
        payload = _http_json(f"{ISS}/securities/{key}.json?iss.meta=off")
    except Exception as e:  # noqa: BLE001
        # сетевая ошибка ≠ «инструмента нет»: сообщаем отдельным статусом,
        # чтобы вызывающий не пометил бумагу как несуществующую
        sys.stderr.write(f"[instruments] {key}: ISS не ответил ({e})\n")
        if hit:
            return {k: v for k, v in hit.items() if k != "_cached_at"}
        return {"secid": key, "found": False, "reason": "iss_unavailable"}

    desc_block = payload.get("description") or {}
    desc = {row[0]: row[2] for row in desc_block.get("data", []) if len(row) > 2}
    if not desc:
        result = {"secid": key, "found": False, "reason": "not_found_on_moex"}
        cache[key] = {**result, "_cached_at": time.time()}
        _save_cache(cache)
        return result

    boards_block = payload.get("boards") or {}
    cols = {c: i for i, c in enumerate(boards_block.get("columns", []))}
    rows = boards_block.get("data", [])

    def col(row: list, name: str) -> Any:
        idx = cols.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    traded = [r for r in rows if col(r, "is_traded") == 1]
    primary = [r for r in rows if col(r, "is_primary") == 1]
    # приоритет: primary И торгуется → primary → любая торгуемая → первая известная
    pick = None
    for candidate in (
        [r for r in primary if col(r, "is_traded") == 1],
        primary, traded, rows,
    ):
        if candidate:
            pick = candidate[0]
            break

    result = {
        "secid": key,
        "found": True,
        "name": desc.get("NAME") or desc.get("SECNAME"),
        "short_name": desc.get("SHORTNAME"),
        "isin": desc.get("ISIN"),
        "iss_type": desc.get("TYPE"),
        "iss_group": desc.get("GROUP"),
        "instrument_type": classify(desc.get("TYPE"), desc.get("GROUP")),
        "board": col(pick, "boardid") if pick else None,
        "engine": col(pick, "engine") if pick else None,
        "market": col(pick, "market") if pick else None,
        "currency": col(pick, "currencyid") if pick else None,
        "lot_size": desc.get("LOTSIZE"),
        "is_traded": bool(traded),
        "listed_from": col(pick, "history_from") if pick else None,
        "boards_all": sorted({col(r, "boardid") for r in rows if col(r, "boardid")}),
    }
    cache[key] = {**result, "_cached_at": time.time()}
    _save_cache(cache)
    return result


def describe_many(
    secids: list[str],
    use_cache: bool = True,
    pause: float = 0.12,
    allow_stale_cache: bool = False,
) -> dict:
    out = {}
    for i, tk in enumerate(secids):
        out[str(tk).upper()] = describe(
            tk,
            use_cache=use_cache,
            allow_stale_cache=allow_stale_cache,
        )
        if i + 1 < len(secids):
            time.sleep(pause)
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("использование: python scripts/moex_instruments.py SECID [SECID ...]", file=sys.stderr)
        return 2
    for tk, info in describe_many(args).items():
        if not info.get("found"):
            print(f"{tk:8} НЕ НАЙДЕН ({info.get('reason')})")
            continue
        print(f"{tk:8} {info['instrument_type']:20} board={info['board']:6} "
              f"lot={info['lot_size']} торгуется={info['is_traded']} "
              f"история с {info['listed_from']} · {info['short_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
