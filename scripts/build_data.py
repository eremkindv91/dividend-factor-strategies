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
import shutil
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from moex_iss import get_prices  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
MOMENTUM = os.path.join(REPO, "model_output", "momentum.json")
QUALITY = os.path.join(REPO, "model_output", "quality_rf.json")
OUT_JSON = os.path.join(REPO, "site", "data.json")

YIELD_MAX = 100.0   # >100% или <0 — невозможно → reject поля
YIELD_HIGH = 30.0   # возможно, но требует внимания → флаг
PAYOUT_REVIEW = 100.0  # выплата выше прибыли возможна, но не должна попадать в основной рейтинг без проверки
RANKING_REVIEW_FLAGS = {
    "y_paid_invalid", "y_exp_invalid", "y_paid_high", "y_exp_high",
    "payout_negative", "payout_high", "price_stale",
}
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


def pct_rank(vals, x):
    """Перцентиль x в vals (kind='mean'), 0..100. Чистый stdlib."""
    n = len(vals)
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return 100.0 * (below + 0.5 * equal) / n


def classify_ranking_quality(status, flags):
    """Fail-closed gate для публичного рейтинга акций.

    Полнота модельных полей и пригодность для ранжирования — разные вещи. Даже строка
    status=ok уходит на ручную проверку при экстремальной доходности, payout или старой цене.
    """
    if status != "ok":
        return {"status": "insufficient", "eligible": False, "reasons": ["incomplete_data"]}
    reasons = sorted({flag for flag in (flags or []) if flag in RANKING_REVIEW_FLAGS})
    if reasons:
        return {"status": "review", "eligible": False, "reasons": reasons}
    return {"status": "eligible", "eligible": True, "reasons": []}


def copy_sp(sp):
    """Копия блока сектор-перцентилей из артефакта (чтобы дописать upside, не мутируя артефакт)."""
    if not isinstance(sp, dict):
        return None
    return {"sector": sp.get("sector"), "metrics": [dict(m) for m in sp.get("metrics", [])]}


# ── Composite Verdict: надёжность(Q=stability) × оценка(V=upside), флаги мягко штрафуют ──
STAB_HI, STAB_LO = 0.67, 0.34       # тиры надёжности (как в таблице)
VAL_BAND = 15.0                     # margin of safety, % (±15 → справедливо)
VAL_CRED_MAX = 150.0                # потолок ПРАВДОПОДОБНОЙ недооценки: upside>150% — артефакт → «оценка н/д»
VAL_CLAMP = 50.0                    # клэмп upside в скоре (гасит шумные мега-значения)
FLAG_PENALTY = 0.8                  # множитель score при жёстком долге(>2.5)/governance
DEBT_SOFT_LO, DEBT_SOFT_HI = 2.0, 2.5   # зона УМЕРЕННОГО левериджа (ниже жёсткого гейта 2.5)
DEBT_SOFT_MAXPEN = 0.15             # макс штраф к score на верхней границе зоны
VERDICT_LABELS = {                  # (тир надёжности, бэнд оценки) → (полный ярлык, короткий, цвет)
    ("hi", "under"): ("★ Надёжный и недооценён", "★ Надёжный+дёшево", "good"),
    ("hi", "fair"):  ("Надёжный, оценён справедливо", "Надёжный", "good"),
    ("hi", "over"):  ("Надёжный, но дорог", "Надёжный, дорог", "neut"),
    ("hi", "na"):    ("Надёжный · оценка н/д", "Надёжный", "neut"),
    ("mid", "under"):("Недооценён, надёжность средняя", "Недооценён", "good"),
    ("mid", "fair"): ("Средний профиль", "Средний", "neut"),
    ("mid", "over"): ("Средний профиль, дорог", "Средний, дорог", "neut"),
    ("mid", "na"):   ("Средний профиль · оценка н/д", "Средний", "neut"),
    ("lo", "under"): ("⚠ Дёшево, но риск выплаты", "⚠ Дёшево/риск", "warn"),
    ("lo", "fair"):  ("Слабый дивиденд", "Слабый", "risk"),
    ("lo", "over"):  ("Слабый дивиденд, дорог", "Слабый", "risk"),
    ("lo", "na"):    ("Низкая надёжность · оценка н/д", "Низкая надёжн.", "risk"),
}


def compute_verdict(stability, upside, alert, nd_eb=None):
    """Сводит надёжность × оценку в категориальный вердикт + score для сортировки.
    score = Q·(1+clamp(V,±50)/100)·penalty·lev_pen — без свободных весов, экономически интерпретируемо.
    nd_eb — Net Debt/EBITDA: градуированный штраф за умеренный леверидж [2.0,2.5) ниже жёсткого гейта."""
    if not isinstance(stability, (int, float)):
        return None
    al = (alert or "").lower()
    f_debt = "в долг" in al
    f_gov = "governance" in al
    f_unrel = "ненадёжна" in al
    q = float(stability)
    v_raw = float(upside) if isinstance(upside, (int, float)) else None
    # надёжность оценки: флаг «ненадёжна» ИЛИ запредельный upside (>150% — артефакт) → V не используем
    v = v_raw if (v_raw is not None and not f_unrel and -100.0 < v_raw <= VAL_CRED_MAX) else None
    stab_tier = "hi" if q >= STAB_HI else ("lo" if q < STAB_LO else "mid")
    band = "na" if v is None else ("under" if v >= VAL_BAND else ("over" if v <= -VAL_BAND else "fair"))
    label, short, color = VERDICT_LABELS[(stab_tier, band)]
    flags = (["в долг"] if f_debt else []) + (["governance"] if f_gov else [])
    lev_pen = 1.0
    if not f_debt and isinstance(nd_eb, (int, float)) and DEBT_SOFT_LO <= nd_eb < DEBT_SOFT_HI:
        frac = (nd_eb - DEBT_SOFT_LO) / (DEBT_SOFT_HI - DEBT_SOFT_LO)   # 2.0→0, 2.5→1
        lev_pen = 1.0 - DEBT_SOFT_MAXPEN * frac
        flags.append("повышенный долг")        # леверидж близко к гейту, но ниже «в долг»
    if flags:
        if color in ("good", "neut"):
            color = "warn"                     # мягко: цвет ≤ амбер
        label += " · ⚠ " + "/".join(flags)
        short += " ⚠"
    hard_pen = FLAG_PENALTY if (f_debt or f_gov) else 1.0
    vclamp = max(-VAL_CLAMP, min(VAL_CLAMP, v)) if v is not None else 0.0
    score = q * (1 + vclamp / 100.0) * hard_pen * lev_pen
    return {"label": label, "short": short, "color": color, "score": round(score, 4),
            "q": round(q, 4), "v": (round(v, 1) if v is not None else None),
            "flags": flags, "unreliable": f_unrel, "tier": stab_tier, "band": band}


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

    momentum = {}                                    # WML 12-1 + vol_ann (месячный pipeline, опц.)
    if os.path.exists(MOMENTUM):
        try:
            momentum = json.load(open(MOMENTUM, encoding="utf-8")).get("data", {})
            print(f"[build_data] momentum: {len(momentum)} тикеров")
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[build_data] momentum.json битый ({e}) — без momentum\n")

    quality = {}
    quality_meta = {}
    if os.path.exists(QUALITY):
        try:
            quality_payload = json.load(open(QUALITY, encoding="utf-8"))
            quality_meta = quality_payload.get("meta") or {}
            quality = {row["ticker"]: row for row in quality_payload.get("rows", []) if row.get("ticker")}
            print(f"[build_data] RU Quality: {len(quality)} строк, method={quality_meta.get('methodology_version')}")
        except Exception as e:  # noqa: BLE001
            fail(f"битый quality_rf.json: {e}")

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
    n_ok = n_insuff = n_yield_rejected = n_yield_high = n_payout_neg = n_payout_high = 0

    for r in records:
        tk = r["ticker"]
        p = prices.get(tk, {})
        qr = quality.get(tk, {})
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
        elif isinstance(payout, (int, float)) and payout > PAYOUT_REVIEW:
            flags.append("payout_high")            # возможна разовая выплата, но только review
            n_payout_high += 1

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

        ranking_quality = classify_ranking_quality(status, flags)

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
            "ranking_status": ranking_quality["status"],
            "ranking_eligible": ranking_quality["eligible"],
            "ranking_review_reasons": ranking_quality["reasons"],
            "forecast_note": r.get("forecast_note"),
            "flags": flags,
            "shap_top5": r.get("shap_top5", []),
            "valuation": valuation_row(r.get("valuation"), price),
            "history": r.get("history"),
            "sector_percentiles": copy_sp(r.get("sector_percentiles")),
            "nd_ebitda": r.get("nd_ebitda"),
            "mcap": (round(r["shares"] * price / 1e6) if (isinstance(r.get("shares"), (int, float))
                     and isinstance(price, (int, float)) and price) else ND),   # живая капитализация, млн ₽
            "mom_score": (momentum.get(tk) or {}).get("mom"),     # WML 12-1 (месячный pipeline)
            "vol_ann": (momentum.get(tk) or {}).get("vol_ann"),   # годовая волатильность (inverse-vol)
            "adv": (momentum.get(tk) or {}).get("adv"),           # ADV ₽/день (ликвидность-фильтр)
            "lot_size": p.get("lot_size") or 1,
            "quality_score": qr.get("quality_score_sector"),
            "quality_rank_pct": qr.get("sector_rank_pct"),
            "quality_confidence": qr.get("confidence"),
            "quality_eligible": bool(qr.get("eligible")),
            "quality_methodology_version": qr.get("methodology_version"),
            "quality_ru_legacy": r.get("quality_ru_legacy", r.get("quality_barra")),
            "quality_barra": r.get("quality_ru_legacy", r.get("quality_barra")),  # one-release alias
        })

    # ── upside-перцентиль внутри сектора (цено-зависим → считаем ЗДЕСЬ, к свежей цене, ежедневно) ──
    UP_MIN_PEERS = 5
    up_pools: dict = {}
    for row in out_rows:
        up = (row.get("valuation") or {}).get("upside_pct")
        sec = row.get("sector")
        if isinstance(up, (int, float)) and sec and sec != ND:
            up_pools.setdefault(sec, {})[row["ticker"]] = float(up)
    n_up = 0
    for row in out_rows:
        up = (row.get("valuation") or {}).get("upside_pct")
        sec = row.get("sector")
        pool = up_pools.get(sec, {})
        if not (isinstance(up, (int, float)) and len(pool) >= UP_MIN_PEERS and row["ticker"] in pool):
            continue
        good = pct_rank(list(pool.values()), float(up))     # выше upside = лучше
        metric = {"key": "upside", "label": "Недооценка (upside)", "unit": "%", "polarity": "up",
                  "raw": round(float(up), 1), "good_pct": int(round(good)), "n": len(pool)}
        sp = row.get("sector_percentiles")
        if isinstance(sp, dict) and isinstance(sp.get("metrics"), list):
            sp["metrics"].append(metric)
        else:
            row["sector_percentiles"] = {"sector": sec, "metrics": [metric]}
        n_up += 1
    print(f"[build_data] upside-перцентиль: {n_up}")

    # ── Composite Verdict (после upside — он цено-зависим) ──
    n_verdict = 0
    for row in out_rows:
        val = row.get("valuation") or {}
        vd = compute_verdict(row.get("stability_score"), val.get("upside_pct"), val.get("alert"), row.get("nd_ebitda"))
        row["verdict"] = vd
        row["verdict_score"] = vd["score"] if vd else ND
        if vd:
            n_verdict += 1
    print(f"[build_data] вердикт: {n_verdict}")

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
            "n_ranking_eligible": sum(1 for row in out_rows if row.get("ranking_eligible")),
            "n_ranking_review": sum(1 for row in out_rows if row.get("ranking_status") == "review"),
            "n_price_fresh": pmeta["n_fresh"],
            "n_price_cached": pmeta["n_cached"],
            "n_price_missing": pmeta["n_missing"],
            "auc_oof_rf": meta_a.get("auc_oof_rf"),
            "model": meta_a.get("model"),
            "shap_note": meta_a.get("shap_note"),
            "quality_methodology_version": quality_meta.get("methodology_version"),
            "quality_as_of": quality_meta.get("as_of_date"),
            "quality_n_scored": quality_meta.get("n_scored"),
            "quality_n_eligible": quality_meta.get("n_eligible"),
            "disclaimer": DISCLAIMER,
        },
        "tickers": out_rows,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[build_data] записано: {os.path.relpath(OUT_JSON, REPO)}")
    print(f"  ok={n_ok} insufficient={n_insuff} | доходность: reject={n_yield_rejected} "
          f"high={n_yield_high} | payout<0={n_payout_neg} payout>100={n_payout_high} "
          f"| ranking_eligible={data['meta']['n_ranking_eligible']} "
          f"review={data['meta']['n_ranking_review']} | prices_stale={prices_stale}")

    # ряд доходностей для риск-метрик конструктора (ленивая подгрузка фронтом)
    ret_src = os.path.join(REPO, "model_output", "returns.json")
    if os.path.exists(ret_src):
        shutil.copyfile(ret_src, os.path.join(REPO, "site", "returns.json"))
        print("[build_data] returns.json → site/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
