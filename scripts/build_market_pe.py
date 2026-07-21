#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Агрегированный P/E компаний текущей корзины IMOEX — с валидируемым контрактом прибыли.

Показатель НЕ является официальным P/E Индекса МосБиржи: расчёт использует ПОЛНУЮ
капитализацию эмитентов (цена×ISSUESIZE по всем классам), тогда как IMOEX учитывает
free-float и ограничивающие коэффициенты.

    P/E = Σ MarketCap(эмитент) / Σ NetIncome(эмитент)

DATA CONTRACT для NetIncome (иначе запись НЕ валидна и в сумму НЕ входит):
  • accounting_standard = IFRS (не РСБУ);
  • statement_scope     = profit/loss attributable to owners of the parent, consolidated
                          (не standalone, не total incl. NCI);
  • период              = полный финансовый год (не промежуточный);
  • показатель          = итоговый результат ВКЛЮЧАЯ отрицательные значения
                          (не comprehensive income, не continuing operations, не EBITDA);
  • валюта              = RUB.

ВАЛИДАЦИЯ (двумерная, причины сохраняются в reconciliation):
  A. earnings-quality (считается из истории слоя):
     • YoY изменение > 3x или смена знака прибыли  → review;
     • needs_manual_review / conflict_flag в слое   → review;
     • период не годовой                            → review.
  B. provenance (требует полей контракта в фундамент-слое):
     • accounting_standard / statement_scope / period_end / published_at ОТСУТСТВУЮТ в
       текущем слое → provenance_unverified (запись не подтверждена как IFRS-attributable).

ГЕЙТ ПУБЛИКАЦИИ: value публикуется ТОЛЬКО если КАЖДЫЙ эмитент с весом в индексе > 2 %
прошёл ОБЕ проверки (validation_status = validated). Иначе status = "validating",
value = null, и UI показывает «Расчёт временно недоступен: проводится проверка качества
финансовых данных». Значение не подставляется вручную — исправление в фундамент-слое.

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

WEIGHT_GATE_PCT = 2.0        # эмитенты с весом > 2 % обязаны быть validated для публикации
YOY_MAX_RATIO = 3.0          # YoY изменение прибыли крупнее — в review
METHODOLOGY_VERSION = "2.0.0"
UNAVAILABLE_MSG = "Расчёт временно недоступен: проводится проверка качества финансовых данных"
UA = {"User-Agent": "dividend-site/market-pe", "Cache-Control": "no-cache"}

# Контракт прибыли (декларируется в JSON для прозрачности)
NET_INCOME_CONTRACT = {
    "accounting_standard": "IFRS",
    "statement_scope": "profit_or_loss_attributable_to_owners_of_the_parent",
    "consolidation": "consolidated",
    "period": "full_financial_year",
    "includes_negative": True,
    "currency": "RUB",
    "excludes": ["comprehensive_income", "continuing_operations_only", "EBITDA", "RAS", "interim_period", "standalone"],
    "required_provenance_fields": ["accounting_standard", "statement_scope", "period_start", "period_end",
                                   "metric_name_raw", "value_raw", "currency", "unit_multiplier",
                                   "source_url", "published_at", "validation_status"],
}

ISS_INDEX = "https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/IMOEX.json?limit=100&iss.meta=off"
ISS_BOARD = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
             "?iss.meta=off&securities.columns=SECID,PREVLEGALCLOSEPRICE,PREVPRICE,PREVDATE,ISSUESIZE")


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
    """base_ticker → [активные TQBR-секиды]; secid → base_ticker."""
    master = load_local(SECURITY_MASTER).get("securities", [])
    classes: dict[str, list[str]] = {}
    base_of: dict[str, str] = {}
    for row in master:
        if row.get("instrument_type") != "share" or row.get("status") != "active" or row.get("board") != "TQBR":
            continue
        secid = row.get("secid")
        base = row.get("base_ticker") or secid
        if not secid:
            continue
        classes.setdefault(base, []).append(secid)
        base_of[secid] = base
    return classes, base_of


def income_history():
    """ticker → отсортированный по году список записей прибыли с метаданными слоя."""
    fin = load_local(FINANCIALS)
    rows = fin.get("rows", [])
    hist: dict[str, list[dict]] = {}
    for row in rows:
        ni, fy, tk = row.get("net_income"), row.get("fiscal_year"), row.get("ticker")
        if ni is None or fy is None or not tk:
            continue
        hist.setdefault(tk, []).append({
            "fy": int(fy), "value": float(ni),
            "period": row.get("period"), "currency": row.get("currency"),
            "source": row.get("source"), "source_status": row.get("source_status"),
            "verification_status": row.get("verification_status"),
            "needs_manual_review": bool(row.get("needs_manual_review")),
            "conflict_flag": bool(row.get("conflict_flag")),
            # Поля контракта, которых в текущем слое НЕТ (провенанс не подтверждён):
            "accounting_standard": row.get("accounting_standard"),
            "statement_scope": row.get("statement_scope"),
            "period_end": row.get("period_end"),
            "published_at": row.get("published_at"),
            "source_url": row.get("source_url"),
        })
    for tk in hist:
        hist[tk].sort(key=lambda r: r["fy"])
    return hist, (fin.get("meta") or {})


def validate_issuer(records: list[dict]):
    """Возвращает (validation_status, latest_record, reasons[])."""
    if not records:
        return "missing", None, ["нет чистой прибыли в фундамент-слое"]
    latest = records[-1]
    reasons: list[str] = []

    # ── A. earnings-quality ──
    if latest.get("needs_manual_review"):
        reasons.append("needs_manual_review в фундамент-слое")
    if latest.get("conflict_flag"):
        reasons.append("conflict_flag в фундамент-слое")
    if latest.get("period") is not None and not str(latest["period"]).strip().isdigit():
        reasons.append(f"период не годовой: {latest['period']}")
    prior = records[-2] if len(records) >= 2 else None
    if prior and prior["value"]:
        sign_flip = (latest["value"] < 0) != (prior["value"] < 0)
        ratio = abs(latest["value"]) / abs(prior["value"]) if prior["value"] else None
        if sign_flip:
            reasons.append(f"смена знака прибыли vs {prior['fy']}: "
                           f"{prior['value']/1e9:.0f}→{latest['value']/1e9:.0f} млрд ₽")
        elif ratio is not None and (ratio > YOY_MAX_RATIO or ratio < 1.0 / YOY_MAX_RATIO):
            reasons.append(f"YoY изменение >{YOY_MAX_RATIO:.0f}x vs {prior['fy']}: "
                           f"{prior['value']/1e9:.0f}→{latest['value']/1e9:.0f} млрд ₽")

    # ── B. provenance (поля контракта отсутствуют в текущем слое) ──
    missing_prov = [f for f in ("accounting_standard", "statement_scope", "period_end", "published_at")
                    if not latest.get(f)]
    if missing_prov:
        reasons.append("провенанс не подтверждён (нет полей: " + ", ".join(missing_prov) + ")")

    # validated — только без единой причины; иначе review (есть non-provenance проблема)
    # или provenance_unverified (единственная проблема — отсутствие полей контракта в слое).
    if not reasons:
        status = "validated"
    elif any("провенанс" not in r for r in reasons):
        status = "review"
    else:
        status = "provenance_unverified"
    return status, latest, reasons


def index_universe():
    """{base_ticker: weight_pct}, дата состава. None при сбое."""
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
    weights: dict[str, float] = {}
    dates = []
    for row in data:
        tk = str(row[cols["ticker"]] or "").upper()
        w = row[cols["weight"]] if "weight" in cols else None
        if tk:
            weights[tk] = weights.get(tk, 0.0) + (float(w) if isinstance(w, (int, float)) else 0.0)
        if "tradedate" in cols and row[cols["tradedate"]]:
            dates.append(str(row[cols["tradedate"]]))
    return weights, (max(dates) if dates else None)


def board_prices():
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
        prevdate = row[cols["PREVDATE"]] if "PREVDATE" in cols else None
        out[secid] = (
            float(price) if isinstance(price, (int, float)) and price > 0 else None,   # НЕ подменяем нулём
            float(issuesize) if isinstance(issuesize, (int, float)) and issuesize > 0 else None,
            prevdate,
        )
    return out


def compute():
    classes, base_of = build_masters()
    hist, fin_meta = income_history()
    weights, composition_date = index_universe()

    universe_label = "IMOEX_CURRENT"
    if not weights:
        universe_label = "COVERED_SAMPLE"
        try:
            tickers = [t.get("ticker") for t in load_local(DATA_JSON).get("tickers", []) if t.get("ticker")]
        except Exception:  # noqa: BLE001
            tickers = []
        weights = {base_of.get(tk, tk): 0.0 for tk in tickers if base_of.get(tk, tk)}
        composition_date = None
        log(f"fallback-universe: {len(weights)} эмитентов (веса недоступны)")

    prices = board_prices()
    price_dates = []

    recon = []                 # reconciliation по каждому эмитенту
    sum_weight = sum(weights.values()) or 0.0
    w_priced = w_earn_valid = w_validated = 0.0
    n_priced = n_earn_valid = n_validated = 0
    blocking = []              # эмитенты weight>2%, не прошедшие валидацию

    for base, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        share_secids = classes.get(base, [base])
        company_cap = 0.0
        ord_priced = False
        for secid in share_secids:
            price, issuesize, prevdate = prices.get(secid, (None, None, None))
            if price is None or issuesize is None:
                continue
            company_cap += price * issuesize
            if prevdate:
                price_dates.append(str(prevdate))
            if secid == base:
                ord_priced = True
        has_cap = company_cap > 0 and ord_priced

        v_status, latest, reasons = validate_issuer(hist.get(base, []))
        earn_ok = v_status == "validated"
        included = has_cap and earn_ok

        if has_cap:
            w_priced += weight; n_priced += 1
        if earn_ok:
            w_earn_valid += weight; n_earn_valid += 1
        if included:
            w_validated += weight; n_validated += 1
        elif weight > WEIGHT_GATE_PCT:
            reason = "; ".join(reasons) if reasons else ("нет валидной капитализации" if not has_cap else "не валидировано")
            blocking.append({"ticker": base, "weight_pct": round(weight, 2), "reason": reason})

        recon.append({
            "ticker": base,
            "weight_pct": round(weight, 3),
            "fy": latest["fy"] if latest else None,
            "net_income_rub": round(latest["value"]) if latest else None,
            "accounting_standard": (latest or {}).get("accounting_standard"),   # None пока слой не обогащён
            "statement_scope": (latest or {}).get("statement_scope"),
            "source": (latest or {}).get("source"),
            "source_url": (latest or {}).get("source_url"),
            "market_cap_rub": round(company_cap) if has_cap else None,
            "included": included,
            "validation_status": v_status,
            "reason": "; ".join(reasons) if reasons else ("нет капитализации" if not has_cap else "ok"),
        })

    def cov(w):
        return round(w / sum_weight, 4) if sum_weight > 0 else None

    market_date = max(price_dates) if price_dates else None
    fundamentals_year = max((r["fy"] for r in recon if r["fy"]), default=None)

    # ГЕЙТ: публикуем только если нет блокеров weight>2% И есть хоть один валидный эмитент
    can_publish = not blocking and n_validated > 0
    value = None
    if can_publish:
        total_cap = sum(r["market_cap_rub"] for r in recon if r["included"])
        total_ni = sum((r["net_income_rub"] or 0) for r in recon if r["included"])
        value = round(total_cap / total_ni, 2) if total_ni > 0 else None

    return {
        "universe_label": universe_label,
        "composition_date": composition_date,
        "market_date": market_date,
        "fundamentals_as_of": f"{fundamentals_year}-12-31" if fundamentals_year else None,
        "status": "ok" if can_publish else "validating",
        "value": value,
        "blocking": blocking,
        "coverage": {
            "price_coverage": cov(w_priced), "price_coverage_n": f"{n_priced}/{len(weights)}",
            "earnings_coverage": cov(w_earn_valid), "earnings_coverage_n": f"{n_earn_valid}/{len(weights)}",
            "issuer_coverage": cov(w_validated), "issuer_coverage_n": f"{n_validated}/{len(weights)}",
        },
        "reconciliation": sorted(recon, key=lambda r: -(r["weight_pct"] or 0)),
    }


def load_existing(path: str):
    try:
        return load_local(path)
    except Exception:  # noqa: BLE001
        return None


def build_payload(result, generated_at):
    return {
        "metric": "aggregate_pe_imoex_basket",
        "title_ru": "Агрегированный P/E компаний текущей корзины IMOEX",
        "status": result["status"],
        "value": result["value"],
        "unavailable_message": UNAVAILABLE_MSG if result["status"] != "ok" else None,
        "blocking_reasons": result["blocking"],
        "universe": result["universe_label"],
        "universe_name": "текущая корзина Индекса МосБиржи (полная капитализация эмитентов, не free-float)",
        "market_date": result["market_date"],
        "composition_date": result["composition_date"],
        "fundamentals_as_of": result["fundamentals_as_of"],
        "calculated_at": generated_at,
        "generated_at": generated_at,
        "coverage": result["coverage"],
        "earnings_basis": "latest_fy_ifrs_attributable_to_parent",
        "contract": NET_INCOME_CONTRACT,
        "reconciliation": result["reconciliation"],
        "is_stale": False,
        "methodology_version": METHODOLOGY_VERSION,
        "note": "Не официальный P/E Индекса МосБиржи: расчёт по ПОЛНОЙ капитализации эмитентов "
                "(цена×ISSUESIZE), тогда как IMOEX учитывает free-float и коэффициенты. Значение "
                "публикуется только после прохождения контракта качества прибыли (IFRS, attributable "
                "to owners of the parent, полный год, включая убытки).",
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
        if existing:
            existing["is_stale"] = True
            existing["stale_reason"] = str(exc)[:120]
            existing["last_attempt_at"] = generated_at
            if not dry:
                atomic_write(out, existing)
            log("сохранён last-good (is_stale=true)")
            return 0
        return 1

    payload = build_payload(result, generated_at)
    log(f"status={payload['status']} value={payload['value']} "
        f"issuer_cov={result['coverage']['issuer_coverage_n']} blockers={len(result['blocking'])}")
    for b in result["blocking"][:6]:
        log(f"  BLOCK {b['ticker']} (вес {b['weight_pct']}%): {b['reason'][:90]}")

    # Не переписывать при отсутствии материальных изменений (нет git-шума)
    if existing and existing.get("status") == payload["status"] \
            and existing.get("value") == payload["value"] \
            and existing.get("market_date") == payload["market_date"] \
            and len(existing.get("blocking_reasons") or []) == len(payload["blocking_reasons"]):
        log("статус/значение/дата не изменились — файл не переписан")
        return 0

    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    atomic_write(out, payload)
    log(f"записан {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
