#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INFERENCE-этап (cron). Собирает site/data.json из ЗАМОРОЖЕННОГО артефакта прогноза
(model_output/forecast_rf.json) + свежих цен MOEX ISS.

Что делает:
  • cut_risk / stability / прогнозный дивиденд / payout — берёт из артефакта (заморожены);
  • дивдоходность — пересчитывает к СВЕЖЕЙ цене: ожидаемая = P·DPS/price и «при выплате» = DPS/price;
  • sanity-валидация (флаг, не тихое удаление): невозможная доходность → reject поля + флаг;
    высокая (>30%) → флаг; payout<0 → флаг; статусы ok|insufficient_data;
  • НЕ модель: никакого дообучения; артефакта нет → падение.

Условия падения (CI краснеет, деплоя нет):
  • нет/битый артефакт forecast_rf.json;
  • ни одной цены (источник недоступен И кэш пуст).
Если источник недоступен, но кэш есть → собираемся на кэше, meta.prices_stale=true (сайт честно
показывает «цены не обновились»), CI зелёный — чтобы публичный URL не падал на транзиентном 403.

Запуск:  python scripts/build_data.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from moex_iss import get_prices  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
OUT_JSON = os.path.join(REPO, "site", "data.json")

YIELD_MAX = 100.0   # >100% или <0 — невозможно → reject поля
YIELD_HIGH = 30.0   # возможно, но требует внимания → флаг
ND = "нет данных"

DISCLAIMER = (
    "Информационный сервис, не индивидуальная инвестиционная рекомендация. Прогноз вероятностный. "
    "Прогноз модели заморожен на дату прогона ВКР; ежедневно обновляется только рыночная цена и "
    "дивидендная доходность. Фундаментальные данные приведены с лагом. SHAP показывает реальные "
    "факторы вероятности выплаты — вклад усреднён по моделям ансамбля (top-3). Цены — MOEX ISS "
    "(борд TQBR), с задержкой/на закрытие. Реальная доходность ограничена ликвидностью и издержками."
)


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    sys.stderr.write(f"[build_data] ОШИБКА: {msg}\n")
    sys.exit(1)


def num(x):
    """Число или ND по контракту."""
    return x if isinstance(x, (int, float)) and x is not None else ND


def valuation_row(v, price):
    """Проброс блока оценки из артефакта + пересчёт upside к СВЕЖЕЙ цене."""
    if not isinstance(v, dict):
        return None
    v = dict(v)
    fair = v.get("fair_price")
    v["upside_pct"] = (round((fair / price - 1) * 100, 1)
                       if (isinstance(fair, (int, float)) and price and price > 0) else None)
    return v


def main() -> int:
    if not os.path.exists(ARTIFACT):
        fail(f"нет артефакта {ARTIFACT}. Сначала запустите scripts/build_artifact.py (train).")
    try:
        with open(ARTIFACT, encoding="utf-8") as f:
            art = json.load(f)
        meta_a = art["meta"]
        records = art["tickers"]
        assert isinstance(records, list) and records, "пустой список тикеров"
    except Exception as e:  # noqa: BLE001
        fail(f"битый артефакт forecast_rf.json: {e}")

    tickers = [r["ticker"] for r in records]
    print(f"[build_data] артефакт: {len(tickers)} тикеров, forecast_asof={meta_a.get('forecast_asof')}")

    fetched = get_prices(tickers)
    prices, pmeta = fetched["prices"], fetched["meta"]
    n_usable = pmeta["n_fresh"] + pmeta["n_cached"]
    print(f"[build_data] цены: fresh={pmeta['n_fresh']} cached={pmeta['n_cached']} "
          f"missing={pmeta['n_missing']} source_ok={pmeta['source_ok']}")
    if n_usable == 0:
        fail("нет ни одной цены (источник недоступен И кэш пуст). Деплой отменён.")

    prices_stale = not pmeta["source_ok"]
    if prices_stale:
        sys.stderr.write("[build_data] ВНИМАНИЕ: ISS недоступен, использован кэш — цены НЕ свежие.\n")

    out_rows = []
    n_ok = n_insuff = n_yield_rejected = n_yield_high = n_payout_neg = 0

    for r in records:
        tk = r["ticker"]
        p = prices.get(tk, {})
        price = p.get("price")
        p_ens = r.get("p_ens")
        dps = r.get("dividend_forecast")
        flags = []

        # ── доходности к свежей цене ──
        y_exp = y_paid = None
        if price and price > 0 and isinstance(dps, (int, float)):
            y_paid = 100.0 * dps / price
            y_exp = (p_ens * y_paid) if isinstance(p_ens, (int, float)) else None
            for label in ("y_paid", "y_exp"):
                val = y_paid if label == "y_paid" else y_exp
                if val is None:
                    continue
                if val < 0 or val > YIELD_MAX:        # невозможно → reject поля
                    flags.append(f"{label}_invalid")
                    n_yield_rejected += 1
                    if label == "y_paid":
                        y_paid = None
                    else:
                        y_exp = None
                elif val > YIELD_HIGH:                 # возможно, но флаг
                    if f"{label}_high" not in flags:
                        flags.append(f"{label}_high")
        # высокая доходность считаем один раз для счётчика
        if any(f.endswith("_high") for f in flags):
            n_yield_high += 1

        payout = r.get("payout_last")
        if isinstance(payout, (int, float)) and payout < 0:
            flags.append("payout_negative")        # не reject — убыток при выплате реален
            n_payout_neg += 1

        if not p.get("fresh", False) and price is not None:
            flags.append("price_stale")

        # ── статус ──
        has_pred = isinstance(r.get("cut_risk"), (int, float))
        # DPS помечен ненадёжным в артефакте (dual-class / аномалия) → forecast_status
        dps_flagged = (r.get("forecast_status") == "insufficient_data") or not isinstance(dps, (int, float))
        status = "ok" if (has_pred and price and price > 0 and not dps_flagged) else "insufficient_data"
        if status == "ok":
            n_ok += 1
        else:
            n_insuff += 1
            if price is None:
                flags.append("no_price")
            if not has_pred:
                flags.append("no_forecast")
            if r.get("forecast_status") == "insufficient_data":
                flags.append("dps_unreliable")

        out_rows.append({
            "ticker": tk,
            "name": p.get("name") or tk,
            "sector": r.get("sector") or ND,
            "cut_risk": num(r.get("cut_risk")),
            "stability_score": num(r.get("stability_score")),
            "dividend_forecast": num(dps),
            "dividend_forecast_lo": num(r.get("dividend_forecast_lo")),
            "dividend_forecast_hi": num(r.get("dividend_forecast_hi")),
            "payout": num(payout),
            "payout_year": r.get("payout_last_year"),
            "payout_source": r.get("payout_source"),
            "dividend_yield_expected": num(round(y_exp, 2) if y_exp is not None else None),
            "dividend_yield_if_paid": num(round(y_paid, 2) if y_paid is not None else None),
            "price": num(round(price, 2) if isinstance(price, (int, float)) else None),
            "price_field": p.get("price_field"),
            "price_fresh": bool(p.get("fresh", False)),
            "div_streak": r.get("div_streak"),
            "current_dps": num(r.get("current_dps")),
            "current_paid": r.get("current_paid"),
            "status": status,
            "forecast_note": r.get("forecast_note"),
            "flags": flags,
            "shap_top5": r.get("shap_top5", []),
            "valuation": valuation_row(r.get("valuation"), price),
            "history": r.get("history"),
        })

    data = {
        "meta": {
            "обновлено": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "forecast_asof": meta_a.get("forecast_asof"),
            "price_asof": pmeta.get("price_asof"),
            "feature_year": meta_a.get("feature_year"),
            "forecast_year": meta_a.get("forecast_year"),
            "valuation_asof": meta_a.get("valuation_asof"),
            "rf_ofz": meta_a.get("rf_ofz"),
            "source": "MOEX ISS (борд TQBR), цены с задержкой/на закрытие",
            "source_ok": pmeta.get("source_ok"),
            "prices_stale": prices_stale,
            "n_total": len(out_rows),
            "n_ok": n_ok,
            "n_insufficient": n_insuff,
            "n_price_fresh": pmeta["n_fresh"],
            "n_price_cached": pmeta["n_cached"],
            "n_price_missing": pmeta["n_missing"],
            "auc_oof_rf": meta_a.get("auc_oof_rf"),
            "model": meta_a.get("model"),
            "shap_note": meta_a.get("shap_note"),
            "disclaimer": DISCLAIMER,
        },
        "tickers": out_rows,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[build_data] записано: {os.path.relpath(OUT_JSON, REPO)}")
    print(f"  ok={n_ok} insufficient={n_insuff} | доходность: reject={n_yield_rejected} "
          f"high={n_yield_high} | payout<0={n_payout_neg} | prices_stale={prices_stale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
