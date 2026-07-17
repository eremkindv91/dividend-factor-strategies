#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bank price-vs-regulatory-capital history for the «Оценка сектора» tab.

Purpose: a timing chart — plot each bank's share price against its book value per share
(capital per share = regulatory capital / shares); where price dips below that line, market
capitalisation is below regulatory capital. This is not IFRS P/BV.

Sources (both real, stdlib only):
  MOEX ISS  — monthly CLOSE price, TQBR board, paginated /history endpoint.
  CBR SOAP  — capital (form 123, code 000) at quarter-ends, forward-filled to months (reused
              cbr_soap.py). Capital moves slowly; quarterly sampling keeps SOAP load light and
              lets the run stay incremental.

shares_eff (ordinary-equivalent share count) is anchored to the current snapshot:
  shares_eff = mcap_now / price_now  (from valuation.json) — so the latest history point matches
  the table's P/BV and prefs fold in automatically. It is held constant across the window, which
  is why each bank has a config `history_from` clamping the window to its current share structure
  (post split / redomiciliation / large secondary issue), plus a per-bank `history_warn`.

Honesty layer: capital is regulatory Ф.123 (Basel III, incl. subordinated debt), not accounting
equity, so the UI explicitly calls the ratio P/капитал ЦБ. CBR suspended bank disclosure most of
2022, so capital forward-fills flat across that gap —
surfaced as a note, not hidden. Output: site/cbr/history.json (incremental: capital cache persists,
only new quarter-ends are fetched on reruns).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cbr_soap import data123, get_dates_for_form  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(REPO, "site", "cbr", "history.json")
VALUATION = os.path.join(REPO, "site", "cbr", "valuation.json")
CONFIG = os.path.join(HERE, "banks_config.json")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "dividend-site/banks-history", "Accept": "application/json"}
TODAY = date.today()
NETWORK_TIMEOUT_SECONDS = 12
socket.setdefaulttimeout(NETWORK_TIMEOUT_SECONDS)


def log(m): sys.stderr.write(f"[banks-hist] {m}\n")


def http_json(url: str, retries: int = 2) -> dict:
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_SECONDS) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001  (ISS is flaky; retry with backoff)
            last = e
            time.sleep(1.5 * (a + 1))
    raise last


# ── MOEX monthly price history ───────────────────────────────────────────────
def monthly_close(secid: str, frm: str, till: str | None = None) -> dict[str, float]:
    """{'YYYY-MM': close} from the TQBR monthly candles, paginated (start ignored-safe loop)."""
    out: dict[str, float] = {}
    start = 0
    till_q = f"&till={till}" if till else ""
    while True:
        try:
            d = http_json(f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/"
                          f"{secid}/monthly.json?iss.meta=off&iss.only=history&from={frm}{till_q}&start={start}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            log(f"MOEX {secid} @start={start}: {str(e)[:60]}")
            break
        h = d.get("history") or {}
        cols, rows = h.get("columns") or [], h.get("data") or []
        if not rows:
            break
        ci = {c: i for i, c in enumerate(cols)}
        for r in rows:
            td, cl = r[ci["TRADEDATE"]], r[ci["CLOSE"]]
            if cl:
                out[td[:7]] = float(cl)          # last close of the month wins (rows are chronological)
        if len(rows) < 100:
            break
        start += 100
    return out


def combined_monthly_close(bank: dict, fetcher=monthly_close) -> tuple[dict[str, float], list[str]]:
    """Merge configured MOEX price sources in order. Later sources win on overlapping months."""
    frm = bank.get("history_from", "2018-01-01")
    sources = bank.get("price_history") or [{"ticker": bank["ticker"]}]
    out: dict[str, float] = {}
    labels: list[str] = []
    for src in sources:
        secid = src["ticker"]
        src_from = max(frm, src.get("from", frm))
        src_till = src.get("until")
        out.update(fetcher(secid, src_from, src_till))
        if src_till:
            labels.append(f"{secid} до {src_till}")
        elif src.get("from"):
            labels.append(f"{secid} с {src['from']}")
        else:
            labels.append(secid)
    return out, labels


def cached_point_prices(prev: dict | None, frm: str) -> dict[str, float]:
    """Previously published adjusted prices from history.json, used only as a network fallback."""
    floor = frm[:7]
    out: dict[str, float] = {}
    for p in (prev or {}).get("points") or []:
        ym, price = p.get("d"), p.get("p")
        if ym and ym >= floor and price is not None:
            out[ym] = float(price)
    return out


def cached_splits(prev: dict | None) -> list[dict]:
    """Previously applied official MOEX splits from history.json, used if the registry is down."""
    out = []
    for s in (prev or {}).get("splits_applied") or []:
        ratio = str(s.get("ratio") or "")
        if ":" not in ratio or not s.get("date"):
            continue
        before, after = ratio.split(":", 1)
        try:
            out.append({"date": s["date"], "before": float(before), "after": float(after)})
        except ValueError:
            continue
    return out


def cache_preferred_months(bank: dict) -> set[str]:
    """Transition months where cached current-ticker prices are safer than alias-source rows."""
    return {src["from"][:7] for src in bank.get("price_history") or [] if src.get("from")}


def issue_size(secid: str) -> float | None:
    try:
        d = http_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json"
                      f"?iss.meta=off&iss.only=securities")
        b = d.get("securities") or {}
        ci = {c: i for i, c in enumerate(b.get("columns") or [])}
        rows = b.get("data") or []
        if rows and "ISSUESIZE" in ci and rows[0][ci["ISSUESIZE"]]:
            return float(rows[0][ci["ISSUESIZE"]])
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        pass
    return None


# ── CBR form 123 capital (quarterly) ─────────────────────────────────────────
def quarter_dates(form_dates: list[str], frm: str) -> list[str]:
    """Quarter-start report dates (Jan/Apr/Jul/Oct 1) available for the form within the window."""
    return [d for d in form_dates if d >= frm and d[5:7] in ("01", "04", "07", "10") and d[8:10] == "01"]


def capital_rub(regnum: int, dt: str) -> float | None:
    d = data123(regnum, dt)
    v = next((r["value"] for r in (d["rows"] if d else []) if r["code"] == "000"), None)
    return v * 1000 if v else None                        # form is thousand RUB → RUB


def detect_split_clamp(px: dict[str, float], hi: float = 5.0) -> str | None:
    """Month after the last price discontinuity (split / reverse-split / reorg). A ≥`hi`× or
    ≤1/`hi`× month-over-month jump is never organic for a large bank but would make current
    shares_eff nonsensical vs pre-event prices. Tight threshold clears the worst 2022 crash (~-55%/mo).
    Safety net only: known splits are first neutralised via the official MOEX registry
    (adjust_for_splits), so this fires only on events the registry does not know about."""
    months = sorted(px)
    clamp = None
    for i in range(1, len(months)):
        a, c = px[months[i - 1]], px[months[i]]
        if a > 0 and (c / a >= hi or c / a <= 1.0 / hi):
            clamp = months[i]
    return clamp


def fetch_splits() -> dict[str, list[dict]]:
    """Official MOEX splits registry (~55 rows total, one request for all banks):
    {secid: [{date, before, after}]} chronological. T 2026-04-17 1→10, VTBR 2024-07-15 5000→1."""
    try:
        d = http_json(f"{ISS}/statistics/engines/stock/splits.json?iss.meta=off")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        log(f"справочник сплитов MOEX недоступен: {str(e)[:50]} — без коррекции")
        return {}
    b = d.get("splits") or {}
    ci = {c: i for i, c in enumerate(b.get("columns") or [])}
    out: dict[str, list[dict]] = {}
    for r in b.get("data") or []:
        out.setdefault(r[ci["secid"]], []).append(
            {"date": r[ci["tradedate"]], "before": r[ci["before"]], "after": r[ci["after"]]})
    for v in out.values():
        v.sort(key=lambda s: s["date"])
    return out


def adjust_for_splits(px: dict[str, float], splits: list[dict]) -> tuple[dict[str, float], list[dict]]:
    """Bring pre-split prices to the CURRENT share base: months strictly before the split month
    get price × before/after (cumulative over multiple splits). The split month itself is left
    as-is — the monthly CLOSE prints at month-end, already after the event. T: ÷10 before 2026-04;
    VTBR: ×5000 before 2024-07. Official registry data, not synthetics."""
    adj = dict(px)
    applied = []
    def fmt_ratio_part(v: float) -> str:
        return str(int(v)) if float(v).is_integer() else str(v)

    for s in splits:
        ym, k = s["date"][:7], s["before"] / s["after"]
        touched = False
        for m in adj:
            if m < ym:
                adj[m] *= k
                touched = True
        if touched:
            applied.append({"date": s["date"], "ratio": f"{fmt_ratio_part(s['before'])}:{fmt_ratio_part(s['after'])}"})
    return adj, applied


# ── assemble one bank ────────────────────────────────────────────────────────
def build_bank(b: dict, val_row: dict | None, prev: dict | None, splits: list[dict] | None = None) -> dict:
    tk, rn = b["ticker"], b["regnum"]
    frm = b.get("history_from", "2018-01-01")
    out = {"ticker": tk, "name": b["name"], "color": b.get("color"),
           "history_from": frm, "warn": b.get("history_warn"),
           "capital": dict((prev or {}).get("capital") or {}),  # persisted cache {YYYY-MM: rub}
           "points": []}

    # shares_eff: anchor to current snapshot so the last point matches the table's P/BV
    shares = None
    if val_row and val_row.get("mcap_rub") and val_row.get("price"):
        shares = float(val_row["mcap_rub"]) / float(val_row["price"])
        out["shares_src"] = "valuation"
    if not shares:
        isz = issue_size(tk)
        if b.get("pref"):
            p = issue_size(b["pref"])
            isz = (isz or 0) + (p or 0) if (isz or p) else None
        shares = isz
        out["shares_src"] = "moex_issuesize"
    if not shares:
        out["warn"] = (out.get("warn") + " · " if out.get("warn") else "") + "нет числа акций — график недоступен"
        return out
    out["shares_eff"] = round(shares, 0)

    # capital: fetch only quarter-ends not already cached (incremental)
    try:
        f123 = get_dates_for_form("123", rn)
    except Exception as e:  # noqa: BLE001
        out["warn"] = (out.get("warn") + " · " if out.get("warn") else "") + f"ЦБ Ф.123 недоступен ({str(e)[:30]})"
        f123 = []
    for q in quarter_dates(f123, frm):
        ym = q[:7]
        if ym in out["capital"]:
            continue
        try:
            c = capital_rub(rn, q)
        except Exception as e:  # noqa: BLE001
            log(f"{tk} Ф.123 {q}: {str(e)[:40]}")
            continue
        if c:
            out["capital"][ym] = c

    # price history
    px, price_sources = combined_monthly_close(b)
    if b.get("price_history"):
        out["price_sources"] = price_sources

    # neutralise known splits via the official MOEX registry (prices → current share base),
    # then auto-clamp only what the registry does not explain
    px, applied = adjust_for_splits(px, splits or [])
    if applied:
        out["splits_applied"] = applied
    cached_px = cached_point_prices(prev, frm)
    fallback_months = 0
    for ym, price in cached_px.items():
        if ym not in px:
            px[ym] = price
            fallback_months += 1
    for ym in cache_preferred_months(b):
        if ym in cached_px and px.get(ym) != cached_px[ym]:
            px[ym] = cached_px[ym]
            fallback_months += 1
    if fallback_months:
        out["price_cache_fallback_months"] = fallback_months
    if not px:
        out["warn"] = (out.get("warn") + " · " if out.get("warn") else "") + "нет истории цены MOEX"
        return out
    split_at = detect_split_clamp(px)
    if split_at:
        out["split_clamp"] = split_at
        px = {k: v for k, v in px.items() if k >= split_at}

    cap_keys = sorted(out["capital"])
    if not cap_keys:
        return out

    def cap_at(ym: str) -> float | None:
        prior = [k for k in cap_keys if k <= ym]           # forward-fill last known capital
        return out["capital"][prior[-1]] if prior else None

    first_cap = cap_keys[0]
    for ym in sorted(px):
        if ym < max(frm[:7], first_cap):
            continue
        c = cap_at(ym)
        if not c:
            continue
        bvps = c / shares
        out["points"].append({
            "d": ym, "p": round(px[ym], 2),
            "bv": round(bvps, 2), "pbv": round(px[ym] / bvps, 3),
        })
    if out["points"]:
        last = out["points"][-1]
        out["last_pbv"] = last["pbv"]
        out["cheap"] = last["pbv"] < 1.0                   # currently below one capital
    return out


def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    val = {}
    if os.path.exists(VALUATION):
        try:
            with open(VALUATION, encoding="utf-8") as f:
                val = {b["ticker"]: b for b in json.load(f).get("banks", [])}
        except (ValueError, OSError):
            log("valuation.json недоступен — shares_eff из MOEX issuesize")
    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                prev = {b["ticker"]: b for b in json.load(f).get("banks", [])}
        except (ValueError, OSError):
            log("history.json битый — полный бэкфилл")

    all_splits = fetch_splits()
    banks = []
    for b in cfg["banks"]:
        try:
            prev_bank = prev.get(b["ticker"])
            splits = all_splits.get(b["ticker"]) or cached_splits(prev_bank)
            banks.append(build_bank(b, val.get(b["ticker"]), prev_bank, splits))
        except Exception as e:  # noqa: BLE001
            log(f"{b['ticker']}: сбой {str(e)[:60]}")
            banks.append({"ticker": b["ticker"], "name": b["name"], "color": b.get("color"),
                          "warn": f"Сбой сбора истории: {str(e)[:50]}", "points": [], "capital": {}})

    with_pts = sum(1 for b in banks if b.get("points"))
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cap_cadence": cfg.get("history_cap_cadence", "quarterly"),
            "banks_count": len(banks), "banks_with_history": with_pts,
            "method": "P/капитал ЦБ(t) = цена(t) × shares_eff / капитал_Ф.123(t); капитал на акцию = капитал/shares_eff. "
                      "shares_eff = текущая капитализация / цена (из valuation.json), константа на окне.",
            "caveats": [
                "Капитал — регуляторный Ф.123 (Базель III, с субордами), не бухгалтерский equity; P/капитал ЦБ не равен IFRS P/BV",
                "ЦБ не раскрывал отчётность банков большую часть 2022 — капитал на графике держится плоско в этот период (forward-fill)",
                "Число акций взято текущим; официальные сплиты (реестр MOEX) скорректированы в ценах, доп.эмиссии акций — нет (см. пометку банка)",
            ],
            "disclaimer": "Ориентир для тайминга, не инвестиционная рекомендация. Перед решением сверяйте с МСФО-отчётностью банка.",
        },
        "banks": banks,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"OK → {OUT}: {with_pts}/{len(banks)} банков с историей")
    return 0 if with_pts > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
