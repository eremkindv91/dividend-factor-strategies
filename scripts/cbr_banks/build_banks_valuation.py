#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bank sector valuation: P/BV, ROE, P/E, payout, dividend yield, Н1.0 for MOEX-listed banks.

Static pipeline (stdlib only, runs in update-cbr-banks workflow). Two real sources + optional manual:
  MOEX ISS  — capitalization (marketdata.ISSUECAPITALIZATION, ordinary + pref summed) and actual paid
              dividends (/iss/securities/{secid}/dividends.json) → trailing-12m DPS, yield, payout.
  CBR SOAP  — capital (form 123, code 000, monthly), net profit ttm (form 102, monthly, accum), Н1.0
              (form 135). Reused via cbr_soap.py.
  forecasts.json (manual, optional) — forward profit/ROE/payout; rendered separately, never mixed.

Honesty layer (non-negotiable): every figure stamped with its as-of date; ticker<->regnum verified
against CBR names (fail loudly); RSBU-solo vs IFRS-group gap, регуляторный-vs-бухгалтерский капитал,
DOM.RF/Т-Технологии perimeter — all per-bank warnings, not buried footnotes. A source failing renders
the row with dashes + warning, never drops the bank. Output: site/cbr/valuation.json.

Methodological note (per spec principle #4, flagged not silently "fixed"): form 123 «собственные
средства (капитал)» is REGULATORY capital (Basel III, incl. subordinated debt), not accounting book
value; P/BV to it is biased low (Sber ~0.8 vs ~1.0 to IFRS equity). Used as the only clean SOAP capital
source, with a standing per-bank warning. This is a documented approximation, not a hidden choice.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cbr_soap import data102f, data123, data135, get_dates_for_form  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(REPO, "site", "cbr", "valuation.json")
CONFIG = os.path.join(HERE, "banks_config.json")
FORECASTS = os.path.join(REPO, "data", "cbr_banks", "forecasts.json")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "dividend-site/banks-valuation", "Accept": "application/json"}
TODAY = date.today()


def log(m): sys.stderr.write(f"[banks] {m}\n")


def http_json(url: str, retries: int = 4) -> dict:
    import time
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001  (ISS is flaky; retry with backoff)
            last = e
            time.sleep(1.5 * (a + 1))
    raise last


def block(d: dict, name: str) -> list[dict]:
    b = d.get(name) or {}
    cols = b.get("columns") or []
    return [dict(zip(cols, row)) for row in (b.get("data") or [])]


# ── MOEX ISS ─────────────────────────────────────────────────────────────────
def fetch_share(secid: str) -> dict | None:
    """Ordinary/pref share snapshot: price, capitalization, issue size."""
    try:
        d = http_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json"
                      f"?iss.meta=off&iss.only=securities,marketdata")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        log(f"MOEX {secid}: {str(e)[:60]}")
        return None
    sec = block(d, "securities")
    md = block(d, "marketdata")
    if not sec:
        return None
    s, m = sec[0], (md[0] if md else {})
    price = m.get("LAST") or m.get("MARKETPRICE") or s.get("PREVPRICE")     # LAST is None off-hours
    mcap = m.get("ISSUECAPITALIZATION")
    if not mcap and price and s.get("ISSUESIZE"):                            # spec fallback: price × issue size
        mcap = float(price) * float(s["ISSUESIZE"])
    return {"price": price, "mcap": mcap,
            "issue_size": s.get("ISSUESIZE"), "shortname": s.get("SHORTNAME"),
            "asof": m.get("SYSTIME") or s.get("PREVDATE")}


def fetch_dividends(secid: str) -> list[dict]:
    try:
        d = http_json(f"{ISS}/securities/{secid}/dividends.json?iss.meta=off")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    return [{"date": r.get("registryclosedate"), "value": r.get("value")}
            for r in block(d, "dividends") if r.get("value") is not None]


def div_ttm(divs: list[dict]) -> tuple[float, list]:
    """Trailing-12m dividend per share + the contributing records."""
    cutoff = date(TODAY.year - 1, TODAY.month, TODAY.day).isoformat()
    recent = [d for d in divs if d["date"] and d["date"] >= cutoff]
    return round(sum(float(d["value"]) for d in recent), 4), recent


# ── CBR forms (via cbr_soap) ─────────────────────────────────────────────────
def f102_accum(regnum: int, dt: str, cache: dict) -> float | None:
    key = ("102", regnum, dt)
    if key not in cache:
        d = data102f(regnum, dt)
        cache[key] = next((r["value"] for r in (d["rows"] if d else []) if r["symbol"] == "61101"), None)
    return cache[key]


def profit_ttm(regnum: int, dates: list[str], cache: dict) -> tuple[float | None, str | None]:
    """Trailing-12m net profit from accumulated form-102 (monthly): accum(D)+FY(prev)-accum(D-12m)."""
    if not dates:
        return None, None
    last = dates[-1]
    ly, lm = int(last[:4]), int(last[5:7])
    fy_prev = f"{ly}-01-01"                              # accum full previous calendar year
    same_prev = f"{ly - 1}-{lm:02d}-01"                 # accum same period last year
    if fy_prev not in dates or same_prev not in dates:
        cur = f102_accum(regnum, last, cache)
        return cur, last                                # fallback: report accum (labeled), not ttm
    a_last = f102_accum(regnum, last, cache)
    a_fy = f102_accum(regnum, fy_prev, cache)
    a_same = f102_accum(regnum, same_prev, cache)
    if None in (a_last, a_fy, a_same):
        return None, last
    return round(a_last + a_fy - a_same, 2), last


def f123_capital(regnum: int, dt: str) -> float | None:
    d = data123(regnum, dt)
    return next((r["value"] for r in (d["rows"] if d else []) if r["code"] == "000"), None)


def n10(regnum: int, dt: str) -> float | None:
    d = data135(regnum, dt)
    return next((r["value"] for r in (d["rows"] if d else []) if r["code"] == "Н1.0"), None)


def year_ago(dt: str) -> str:
    return f"{int(dt[:4]) - 1}-{dt[5:7]}-01"


# ── per-bank valuation ───────────────────────────────────────────────────────
def value_bank(b: dict, cache: dict) -> dict:
    tk, rn = b["ticker"], b["regnum"]
    row = {"ticker": tk, "regnum": rn, "name": b["name"], "color": b["color"],
           "sib": b.get("sib"), "warnings": [], "vintages": {}}

    # verify ticker<->regnum against CBR name (fail loud, but keep bank with warning)
    d102_dates = []
    try:
        d102_dates = get_dates_for_form("102", rn)
        chk = data102f(rn, d102_dates[-1]) if d102_dates else None
        cname = (chk or {}).get("name") or ""
        if b["expected_name"].upper() not in cname.upper():
            row["warnings"].append(f"⚠ regnum {rn} → «{cname[:30]}» не содержит «{b['expected_name']}» — проверьте маппинг")
    except Exception as e:  # noqa: BLE001
        row["warnings"].append(f"ЦБ по рег.{rn} недоступен ({str(e)[:40]})")

    # MOEX: cap (ordinary + pref), price, dividends
    sh = fetch_share(tk)
    pref = fetch_share(b["pref"]) if b.get("pref") else None
    mcap = None
    if sh and sh["mcap"]:
        mcap = float(sh["mcap"]) + (float(pref["mcap"]) if pref and pref.get("mcap") else 0.0)
        row["vintages"]["moex"] = str(sh.get("asof") or "")[:10]
    else:
        row["warnings"].append("Рыночная капитализация MOEX недоступна")
    price = float(sh["price"]) if sh and sh["price"] else None
    row["price"] = round(price, 2) if price else None
    row["mcap_rub"] = round(mcap, 0) if mcap else None

    divs = fetch_dividends(tk)
    dttm, drecs = div_ttm(divs)
    row["div_ttm"] = dttm or None
    row["div_yield"] = round(100 * dttm / price, 2) if (dttm and price) else None
    row["div_history"] = sorted(divs, key=lambda x: x["date"] or "")[-6:]

    # CBR: capital, profit ttm, Н1.0
    d123_dates = get_dates_for_form("123", rn) if d102_dates else []
    d135_dates = get_dates_for_form("135", rn) if d102_dates else []
    cap_last = f123_capital(rn, d123_dates[-1]) if d123_dates else None      # thousand RUB
    cap_prev = f123_capital(rn, year_ago(d123_dates[-1])) if len(d123_dates) >= 12 else None
    prof_ttm, prof_date = profit_ttm(rn, d102_dates, cache)                  # thousand RUB
    cur_n10 = n10(rn, d135_dates[-1]) if d135_dates else None
    if d123_dates:
        row["vintages"]["cbr_123"] = d123_dates[-1]
        row["capital_rub"] = round(cap_last * 1000, 0) if cap_last else None
    if d102_dates:
        row["vintages"]["cbr_102"] = prof_date
    if d135_dates:
        row["vintages"]["cbr_135"] = d135_dates[-1]
    row["profit_ttm_rub"] = round(prof_ttm * 1000, 0) if prof_ttm else None
    row["n10"] = cur_n10

    # multiples
    if mcap and cap_last:
        row["p_bv"] = round(mcap / (cap_last * 1000), 3)
    if mcap and prof_ttm and prof_ttm > 0:
        row["p_e"] = round(mcap / (prof_ttm * 1000), 2)
    avg_cap = (cap_last + cap_prev) / 2 if (cap_last and cap_prev) else cap_last
    if prof_ttm and avg_cap:
        row["roe"] = round(100 * prof_ttm / avg_cap, 2)
    # payout (approx): total paid / net profit; pref dividend assumed equal to ordinary
    if dttm and prof_ttm and sh and sh.get("issue_size"):
        shares = float(sh["issue_size"]) + (float(pref["issue_size"]) if pref and pref.get("issue_size") else 0.0)
        total_div = dttm * shares
        row["payout"] = round(100 * total_div / (prof_ttm * 1000), 1)
    # capital headroom
    reg_min = b["cfg_reg_min"]
    if cur_n10 is not None:
        row["n10_headroom"] = round(cur_n10 - reg_min, 2)
        row["n10_reg_min"] = reg_min

    # honesty warnings
    gap = {"material": "РСБУ соло существенно ≠ МСФО группы (широкий небанковский периметр)",
           "moderate": "РСБУ соло умеренно ≠ МСФО группы",
           "low": "РСБУ соло близко к МСФО"}.get(b.get("ifrs_gap"), "")
    if gap:
        row["warnings"].append(gap)
    row["warnings"].append("Капитал — регуляторный Ф.123 (Базель III, с субордами), не бухгалтерский equity → P/BV смещён вниз")
    if b.get("perimeter_note"):
        row["warnings"].append(b["perimeter_note"])
    if prof_ttm and d102_dates and f"{int(d102_dates[-1][:4])}-01-01" not in d102_dates:
        row["warnings"].append("Прибыль — накопл. с начала года (не полный TTM: нет истории 12 мес)")
    return row


def load_forecasts() -> dict:
    if os.path.exists(FORECASTS):
        try:
            with open(FORECASTS, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            log("forecasts.json битый — пропуск")
    return {}


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    forecasts = load_forecasts()
    cache: dict = {}
    banks = []
    for b in cfg["banks"]:
        b["cfg_reg_min"] = cfg["reg_min_n10_sib"] if b.get("sib") else cfg["reg_min_n10_other"]
        try:
            row = value_bank(b, cache)
        except Exception as e:  # noqa: BLE001
            log(f"{b['ticker']}: сбой {str(e)[:60]} — строка с прочерками")
            row = {"ticker": b["ticker"], "name": b["name"], "color": b["color"],
                   "warnings": [f"Сбой сбора: {str(e)[:50]}"], "vintages": {}}
        if b["ticker"] in forecasts:
            row["forecast"] = forecasts[b["ticker"]]
        banks.append(row)

    # oldest vintage across all figures (header freshness line)
    all_v = [v for b in banks for v in b.get("vintages", {}).values() if v]
    cbr_dates = [v for b in banks for k, v in b.get("vintages", {}).items() if k.startswith("cbr") and v]
    moex_dates = [b["vintages"].get("moex") for b in banks if b.get("vintages", {}).get("moex")]

    with_p_bv = sum(1 for b in banks if b.get("p_bv") is not None)
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cbr_asof": min(cbr_dates) if cbr_dates else None,
            "moex_asof": min(moex_dates) if moex_dates else None,
            "oldest_vintage": min(all_v) if all_v else None,
            "coe_default": cfg["coe_default"], "coe_range": cfg["coe_range"],
            "reg_min_note": cfg["reg_min_note"],
            "has_forecasts": bool(forecasts),
            "banks_count": len(banks), "banks_valued": with_p_bv,
            "disclaimer": "РСБУ-метрики — приближение; перед решением сверяйте с МСФО-отчётностью банка. "
                          "Это не инвестиционная рекомендация.",
            "tooltips": {
                "p_bv": "За сколько собственных капиталов торгуется банк. Главный мультипликатор сектора: банк — это плечо на капитал, его активы и пассивы переоцениваются близко к рынку, поэтому балансовая стоимость осмысленна. P/BV < 1 — рынок закладывает ROE ниже стоимости капитала или не доверяет балансу.",
                "roe": "Прибыль на средний собственный капитал — двигатель оценки. Справедливый P/BV ≈ (ROE − g) / (COE − g): при стоимости капитала ~20%+ банк с ROE ~22% честно стоит около одного капитала, ROE ~12% — заслуженный глубокий дисконт. Сравнивайте дисконт с риском снижения ROE, а не с \"дёшево/дорого\".",
                "p_e": "Для банков вторичен: прибыль шумит от резервов и переоценок. Использовать только вместе с P/BV и только внутри сектора.",
                "payout": "Доля прибыли на дивиденды. Ограничена не щедростью, а достаточностью капитала: рост активов требует капитала, поэтому быстрорастущий банк платит меньше. Обещания высокого пэйаута при тонком Н1.0 — красный флаг.",
                "div_yield": "Факт — по реально выплаченным дивидендам; прогноз — ручной ввод. Высокая доходность при слабом капитале чаще предвестник отмены, чем подарок.",
                "n10": "Достаточность капитала. Запас над регуляторным минимумом — это одновременно буфер под рост и источник дивидендов; его исчерпание бьёт по обоим.",
                "profit": "РСБУ соло-банка по данным ЦБ (форма 102), скользящие 4 квартала. Для групп с большим небанковским периметром (ВТБ, Т-Технологии) расходится с МСФО — расхождение показано в строке банка.",
            },
        },
        "banks": banks,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"OK → {OUT}: {with_p_bv}/{len(banks)} банков оценено, ЦБ на {payload['meta']['cbr_asof']}, "
        f"MOEX на {payload['meta']['moex_asof']}")
    return 0 if with_p_bv > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
