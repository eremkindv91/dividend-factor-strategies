#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P/E рынка по последней ГОДОВОЙ прибыли — ежедневно пересчитываемый показатель.

ЧЕСТНАЯ методика (не официальный P/E Индекса МосБиржи, не LTM):
    P/E_t = Σ MarketCap(i, t) / Σ NetIncome(company)
где
  • числитель — рыночная капитализация КАЖДОЙ бумаги корзины на конец последней
    завершённой сессии: MarketCap = close_price × ISSUESIZE (кол-во бумаг в выпуске,
    MOEX ISS). Обыкновенные и привилегированные акции одного эмитента складываются
    в общую капитализацию компании (группировка по base_ticker из security_master).
  • знаменатель — последняя доступная ГОДОВАЯ чистая прибыль по МСФО (site_financials),
    учитывается ОДИН раз на эмитента, даже если торгуется несколько классов акций.

Universe: текущая корзина Индекса МосБиржи (ISS analytics, машиночитаемый состав).
Fallback universe: покрываемая выборка (тикеры data.json), если состав недоступен.

Гейты качества (иначе итог НЕ обновляется — остаётся last-good):
  • покрытие капитализации < 85 %  → не обновлять (карзина неполна из-за сбоя цен);
  • отсутствующая цена НИКОГДА не подменяется нулём — бумага исключается;
  • одна прибыль не учитывается дважды для разных классов акций;
  • Σ прибыль ≤ 0  → P/E = null (н/д), но метаданные пишутся;
  • при сбое источника — сохраняется предыдущее корректное значение (is_stale);
  • атомарная замена JSON только после прохождения всех проверок;
  • если value и market_date не изменились — файл не переписывается (нет git-шума).

CLI:
  python scripts/build_market_pe.py                 # → site/market_pe_current.json
  python scripts/build_market_pe.py --out /tmp/x.json --dry-run
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "site", "market_pe_current.json")
SECURITY_MASTER = os.path.join(ROOT, "data", "security_master.json")
FINANCIALS = os.path.join(ROOT, "site", "site_financials.json")
DATA_JSON = os.path.join(ROOT, "site", "data.json")

COVERAGE_MIN = 0.85
METHODOLOGY_VERSION = "1.0.0"
UA = {"User-Agent": "dividend-site/market-pe", "Cache-Control": "no-cache"}

ISS_INDEX = "https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/IMOEX.json?limit=100&iss.meta=off"
ISS_BOARD = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
             "?iss.meta=off&securities.columns=SECID,PREVLEGALCLOSEPRICE,PREVPRICE,PREVDATE,ISSUESIZE"
             "&marketdata.columns=SECID")


def log(msg: str) -> None:
    sys.stderr.write(f"[market-pe] {msg}\n")


def fetch_json(url: str, retries: int = 5):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url + ("&_=" + str(int(time.time()))), headers=UA)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(20, 3 * (attempt + 1)))
    raise RuntimeError(f"ISS недоступен: {url[:70]} ({last})")


def load_local(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_masters():
    """base_ticker → [активные TQBR-секиды всех классов]; secid → base_ticker."""
    master = load_local(SECURITY_MASTER).get("securities", [])
    classes: dict[str, list[str]] = {}
    base_of: dict[str, str] = {}
    for row in master:
        if row.get("instrument_type") != "share" or row.get("status") != "active":
            continue
        if row.get("board") != "TQBR":
            continue
        secid = row.get("secid")
        base = row.get("base_ticker") or secid
        if not secid:
            continue
        classes.setdefault(base, []).append(secid)
        base_of[secid] = base
    return classes, base_of


def latest_fy_net_income():
    """ticker → (fiscal_year, net_income) по ПОСЛЕДНЕМУ доступному году."""
    rows = load_local(FINANCIALS).get("rows", [])
    best: dict[str, tuple[int, float]] = {}
    for row in rows:
        ni, fy, tk = row.get("net_income"), row.get("fiscal_year"), row.get("ticker")
        if ni is None or fy is None or not tk:
            continue
        if tk not in best or fy > best[tk][0]:
            best[tk] = (int(fy), float(ni))
    return best


def index_universe():
    """Текущая корзина IMOEX: [base_ticker...] + дата состава. None при сбое."""
    try:
        payload = fetch_json(ISS_INDEX)
    except RuntimeError as exc:
        log(f"состав IMOEX недоступен: {exc}")
        return None, None
    block = payload.get("analytics", {})
    cols = {c: i for i, c in enumerate(block.get("columns", []))}
    data = block.get("data", [])
    if not data or "ticker" not in cols:
        return None, None
    tickers, dates = [], []
    for row in data:
        tk = row[cols["ticker"]]
        if tk:
            tickers.append(str(tk).upper())
        if "tradedate" in cols and row[cols["tradedate"]]:
            dates.append(str(row[cols["tradedate"]]))
    composition_date = max(dates) if dates else None
    return sorted(set(tickers)), composition_date


def board_prices():
    """secid → (close_price, issuesize, prevdate). close = PREVLEGALCLOSEPRICE|PREVPRICE."""
    payload = fetch_json(ISS_BOARD)
    block = payload["securities"]
    cols = {c: i for i, c in enumerate(block["columns"])}
    out = {}
    for row in block["data"]:
        secid = row[cols["SECID"]]
        price = row[cols["PREVLEGALCLOSEPRICE"]]
        if price is None:
            price = row[cols["PREVPRICE"]]
        issuesize = row[cols["ISSUESIZE"]]
        prevdate = row[cols.get("PREVDATE", -1)] if "PREVDATE" in cols else None
        # НЕ подменяем отсутствующую цену/объём нулём — оставляем None, бумага выпадет
        out[secid] = (
            float(price) if isinstance(price, (int, float)) and price > 0 else None,
            float(issuesize) if isinstance(issuesize, (int, float)) and issuesize > 0 else None,
            prevdate,
        )
    return out


def compute():
    classes, base_of = build_masters()
    net_income = latest_fy_net_income()
    universe, composition_date = index_universe()

    universe_label = "IMOEX_CURRENT"
    universe_name = "текущая корзина Индекса МосБиржи"
    if not universe:
        # Fallback: покрываемая выборка из data.json (тикеры с рейтингом/капой)
        universe_label, universe_name = "COVERED_SAMPLE", "покрываемая выборка российского рынка"
        try:
            tickers = [t.get("ticker") for t in load_local(DATA_JSON).get("tickers", []) if t.get("ticker")]
        except Exception:  # noqa: BLE001
            tickers = []
        universe = sorted({base_of.get(tk, tk) for tk in tickers if base_of.get(tk, tk)})
        composition_date = None
        log(f"использую fallback-universe: {len(universe)} эмитентов")

    prices = board_prices()
    price_dates = []

    companies = []          # включённые (есть капа И прибыль)
    total_cap_all = 0.0     # капа всех эмитентов корзины с валидной ценой (знаменатель покрытия)
    total_market_cap = 0.0  # капа включённых
    total_net_income = 0.0
    n_no_cap = n_no_income = 0

    seen_base = set()
    for member in universe:
        base = base_of.get(member, member)
        if base in seen_base:
            continue
        seen_base.add(base)

        share_secids = classes.get(base, [member])
        company_cap = 0.0
        ord_priced = False
        for secid in share_secids:
            price, issuesize, prevdate = prices.get(secid, (None, None, None))
            if price is None or issuesize is None:
                continue
            company_cap += price * issuesize
            if prevdate:
                price_dates.append(str(prevdate))
            if secid == base:  # обыкновенная бумага эмитента оценена
                ord_priced = True
        # Капа считается валидной, если оценена хотя бы обыкновенная бумага эмитента
        if company_cap <= 0 or not ord_priced:
            n_no_cap += 1
            continue
        total_cap_all += company_cap

        ni = net_income.get(base)
        if ni is None:
            n_no_income += 1
            continue  # нет прибыли → не в числителе и не в знаменателе (честно)

        total_market_cap += company_cap
        total_net_income += ni[1]
        companies.append({"ticker": base, "cap": company_cap, "net_income": ni[1], "fy": ni[0]})

    coverage = (total_market_cap / total_cap_all) if total_cap_all > 0 else 0.0
    market_date = max(price_dates) if price_dates else None
    fundamentals_year = max((c["fy"] for c in companies), default=None)
    fundamentals_as_of = f"{fundamentals_year}-12-31" if fundamentals_year else None

    value = None
    if total_net_income > 0:
        value = round(total_market_cap / total_net_income, 2)

    return {
        "coverage": coverage,
        "universe_label": universe_label,
        "universe_name": universe_name,
        "composition_date": composition_date,
        "market_date": market_date,
        "fundamentals_as_of": fundamentals_as_of,
        "value": value,
        "total_market_cap": total_market_cap,
        "total_net_income": total_net_income,
        "companies_included": len(companies),
        "companies_total": len(seen_base),
        "n_no_cap": n_no_cap,
        "n_no_income": n_no_income,
        "companies": sorted(companies, key=lambda c: -c["cap"]),
    }


def load_existing(path: str):
    try:
        return load_local(path)
    except Exception:  # noqa: BLE001
        return None


def build_payload(result, generated_at):
    return {
        "metric": "market_pe_fy",
        "title_ru": "P/E текущей корзины Индекса МосБиржи по последней годовой прибыли",
        "value": result["value"],
        "universe": result["universe_label"],
        "universe_name": result["universe_name"],
        "market_date": result["market_date"],
        "composition_date": result["composition_date"],
        "fundamentals_as_of": result["fundamentals_as_of"],
        "calculated_at": generated_at,
        "generated_at": generated_at,  # алиас для build_site_status (GEN_FIELDS)
        "total_market_cap_rub": round(result["total_market_cap"]),
        "total_net_income_rub": round(result["total_net_income"]),
        "companies_total": result["companies_total"],
        "companies_included": result["companies_included"],
        "market_cap_coverage": round(result["coverage"], 4),
        "earnings_basis": "latest_fy_ifrs",
        "is_stale": False,
        "methodology_version": METHODOLOGY_VERSION,
        "note": "Не официальный P/E Индекса МосБиржи и не LTM. Прибыль — последняя годовая "
                "по МСФО (source: SmartLab), капитализация — close×ISSUESIZE (MOEX ISS).",
        "top_contributors": [
            {"ticker": c["ticker"], "market_cap_rub": round(c["cap"]),
             "net_income_rub": round(c["net_income"]), "fy": c["fy"]}
            for c in result["companies"][:12]
        ],
    }


def atomic_write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def main() -> int:
    out = DEFAULT_OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    dry = "--dry-run" in sys.argv

    generated_at = datetime.now(MSK).isoformat(timespec="seconds")
    existing = load_existing(out)

    try:
        result = compute()
    except RuntimeError as exc:
        log(f"расчёт не удался: {exc}")
        if existing:  # last-good с пометкой устаревания
            existing["is_stale"] = True
            existing["stale_reason"] = str(exc)[:120]
            existing["last_attempt_at"] = generated_at
            if not dry:
                atomic_write(out, existing)
            log("сохранён last-good (is_stale=true)")
            return 0
        log("нет last-good — файл не создан")
        return 1

    coverage = result["coverage"]
    log(f"universe={result['universe_label']} companies={result['companies_included']}/{result['companies_total']} "
        f"coverage={coverage:.1%} value={result['value']} no_cap={result['n_no_cap']} no_income={result['n_no_income']}")

    # ГЕЙТ покрытия: неполная корзина (сбой цен) → не обновлять, держать last-good
    if coverage < COVERAGE_MIN:
        log(f"покрытие {coverage:.1%} < {COVERAGE_MIN:.0%} — итог НЕ обновляется")
        if existing:
            existing["is_stale"] = True
            existing["stale_reason"] = f"coverage {coverage:.1%} < {COVERAGE_MIN:.0%}"
            existing["last_attempt_at"] = generated_at
            if not dry:
                atomic_write(out, existing)
        return 0

    payload = build_payload(result, generated_at)

    # Не переписывать при отсутствии материальных изменений (нет git-шума)
    if existing and not existing.get("is_stale") \
            and existing.get("value") == payload["value"] \
            and existing.get("market_date") == payload["market_date"] \
            and existing.get("companies_included") == payload["companies_included"]:
        log("значение и дата не изменились — файл не переписан")
        return 0

    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    atomic_write(out, payload)
    log(f"записан {out}: P/E={payload['value']} (покрытие {coverage:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
