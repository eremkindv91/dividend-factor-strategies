#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MOEX Bond Finder: monthly shortlist of attractive RUB bonds + allocation proposal.

Personal-use instrument, NOT a product: output is always "кандидаты для ручной проверки",
never "buy". Runs inside the heavy-deps workflow (requests/numpy available), publishes a
single JSON consumed by the site's Bonds tab. Every fallback / exclusion / degradation is
recorded in result["meta"]["warnings"] and rendered prominently (loud data-quality layer).

Pipeline (two-stage, per spec):
  1. cheap: all traded boards (dynamic discovery) -> merge securities+marketdata ->
     vectorized pre-filters (RUB face, price & duration present, positive rough G-spread,
     no offer in main list) -> rank by rough spread -> top max_candidates.
  2. fine: per-bond enrichment (bondization, description, 130-session history) with
     throttled workers -> honest cashflow YTM at dirty price, floater/amort/ПИР/qual flags,
     median 20-session RUB volume, cheapening percentile (historical ZSPREAD when available,
     else G-spread vs weekly ZCYC snapshots).

Scoring, risk proxy, buckets, profiles, portfolio caps: constants in finder_config.json.
Validation loop: journal of picks (repo-committed JSON) + review vs RUCBTRNS each run.
Endpoints and verified field names: docs/ISS_SCHEMA_NOTES.md (deliverable, kept in sync).
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import statistics
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from update_bonds import (  # noqa: E402  (verified v2 logic reused, see schema notes)
    ISS, RATING_RANK, TAX, block_rows, gcurve_rate, has_date, http_json, load_board,
    load_gcurve, solve_ytm,
)
from official_ratings import DEFAULT_CACHE, load_official_ratings  # noqa: E402

REPO = os.path.dirname(HERE)
OUT_JSON = os.path.join(REPO, "site", "bonds", "finder.json")
STATE_DIR = os.path.join(REPO, "data", "bond_finder")
JOURNAL = os.path.join(STATE_DIR, "journal.json")
UNIVERSE_PREV = os.path.join(STATE_DIR, "universe_prev.json")
CONFIG = os.path.join(HERE, "finder_config.json")
TODAY = date.today()

WARNINGS: list[str] = []


def warn(msg: str) -> None:
    if msg not in WARNINGS:
        WARNINGS.append(msg)
    sys.stderr.write(f"[finder][warn] {msg}\n")


def log(msg: str) -> None:
    sys.stderr.write(f"[finder] {msg}\n")


# ── universe (cheap stage) ────────────────────────────────────────────────────
def traded_boards() -> list[str]:
    d = http_json(f"{ISS}/engines/stock/markets/bonds/boards.json?iss.meta=off")
    rows = block_rows(d, "boards")
    return [r["boardid"] for r in rows if r.get("is_traded") == 1]


def price_clean(s: dict) -> float | None:
    md = s["_md"]
    for k in ("WAPRICE", "LAST", "MARKETPRICE", "LCLOSEPRICE"):
        v = md.get(k)
        if v not in (None, "", 0):
            return float(v)
    for k in ("PREVLEGALCLOSEPRICE", "PREVPRICE"):
        v = s.get(k)
        if v not in (None, "", 0):
            return float(v)
    return None


def build_universe(cfg: dict) -> tuple[list[dict], dict]:
    boards = traded_boards()
    log(f"торгуемых бордов: {len(boards)} (динамическое обнаружение)")
    rows: list[dict] = []
    empty_boards = []
    for b in boards:
        try:
            got = load_board(b)
        except Exception as e:  # noqa: BLE001
            warn(f"борд {b} не загрузился ({str(e)[:60]}) — пропущен")
            continue
        if not got:
            empty_boards.append(b)
            continue
        rows.extend(got)
    counts = {"universe_raw": len(rows), "boards": len(boards), "boards_empty": len(empty_boards)}

    gt, gy = load_gcurve()
    out = []
    c = {"fx": 0, "pir_board": 0, "no_price": 0, "no_duration": 0, "offer_put": 0,
         "call_only": 0, "nonpos_spread": 0}
    seen = set()
    for s in rows:
        sid = s["SECID"]
        if sid in seen:
            continue
        seen.add(sid)
        if (s.get("FACEUNIT") or "").upper() not in ("SUR", "RUB", "RUR"):
            c["fx"] += 1
            continue
        if "валют" in (s.get("BONDTYPE") or "").lower():
            c["fx"] += 1
            continue
        if s["_board"] in ("TQRD", "TQUD"):          # Д-sector proxy; fine stage re-checks HIGHRISK
            c["pir_board"] += 1
            continue
        p = price_clean(s)
        if p is None:
            c["no_price"] += 1
            continue
        md = s["_md"]
        dur_days = md.get("DURATION")
        if dur_days in (None, "", 0):
            c["no_duration"] += 1
            continue
        dur_y = float(dur_days) / 365.0
        rough_ytm = md.get("YIELD") or md.get("YIELDATWAPRICE") or s.get("YIELDATPREVWAPRICE")
        if rough_ytm in (None, "", 0):
            c["no_price"] += 1
            continue
        rough_spread = float(rough_ytm) - gcurve_rate(dur_y, gt, gy)
        rec = {"secid": sid, "board": s["_board"], "name": s.get("SHORTNAME"),
               "secname": s.get("SECNAME"), "price": p,
               "face": float(s.get("FACEVALUE") or 1000),
               "lot_value": float(s.get("LOTVALUE") or s.get("FACEVALUE") or 1000),
               "accrued": float(s.get("ACCRUEDINT") or 0.0),
               "listlevel": s.get("LISTLEVEL"),
               "issue_rub": (float(s.get("ISSUESIZEPLACED") or s.get("ISSUESIZE") or 0)
                             * float(s.get("FACEVALUE") or 0)),
               "matdate": s.get("MATDATE"), "duration_years": round(dur_y, 3),
               "rough_ytm": float(rough_ytm), "rough_spread": round(rough_spread, 3),
               "valtoday": float(md.get("VALTODAY") or 0),
               "coupon_pct": s.get("COUPONPERCENT"), "prevdate": s.get("PREVDATE"),
               "offer": None}
        has_put = has_date(s.get("OFFERDATE")) or has_date(s.get("PUTOPTIONDATE"))
        has_call = has_date(s.get("CALLOPTIONDATE")) or has_date(s.get("BUYBACKDATE"))
        if has_put:
            c["offer_put"] += 1
            rec["offer"] = {"date": s.get("OFFERDATE") if has_date(s.get("OFFERDATE")) else s.get("PUTOPTIONDATE"),
                            "ytm_to_offer": md.get("YIELDTOOFFER")}
            out.append(rec)                            # goes to the offer section, not main
            continue
        if has_call:
            c["call_only"] += 1                        # call-risk without investor put -> excluded
            continue
        if rough_spread <= 0:
            c["nonpos_spread"] += 1
            continue
        out.append(rec)
    counts.update(c)
    counts["universe_clean"] = len([r for r in out if r["offer"] is None])
    counts["offer_candidates"] = len([r for r in out if r["offer"] is not None])
    return out, {"counts": counts, "gt": gt, "gy": gy}


# ── fine stage: per-bond enrichment ──────────────────────────────────────────
def fetch_bondization(secid: str) -> dict:
    d = http_json(f"{ISS}/securities/{secid}/bondization.json?iss.meta=off&limit=unlimited")
    return {"coupons": block_rows(d, "coupons"), "amort": block_rows(d, "amortizations")}


def fetch_description(secid: str) -> dict:
    d = http_json(f"{ISS}/securities/{secid}.json?iss.meta=off&iss.only=description")
    out = {}
    for r in (d.get("description") or {}).get("data", []):
        if len(r) >= 3:
            out[r[0]] = r[2]
    return out


def fetch_history(secid: str, board: str) -> list[dict]:
    d = http_json(f"{ISS}/history/engines/stock/markets/bonds/boards/{board}/securities/{secid}.json"
                  f"?iss.meta=off&sort_order=desc&limit=130")
    return block_rows(d, "history")


def enrich(rec: dict, gt, gy, weekly_curve: dict, cfg: dict) -> dict | None:
    """Fine-stage enrichment; returns enriched rec or a rec with rec['_excluded']=reason."""
    sid, board = rec["secid"], rec["board"]
    try:
        bz = fetch_bondization(sid)
        desc = fetch_description(sid)
        hist = fetch_history(sid, board)
    except Exception as e:  # noqa: BLE001
        rec["_excluded"] = f"enrich_error:{str(e)[:50]}"
        return rec

    # ПИР / qualified-only (verified field names, see schema notes §4)
    if str(desc.get("HIGHRISK") or "") == "1":
        rec["_excluded"] = "pir_highrisk"
        return rec
    rec["qual_only"] = str(desc.get("ISQUALIFIEDINVESTORS") or "0") == "1"
    rec["issuer"] = desc.get("EMITTER_TITLE") or desc.get("NAME") or rec.get("secname") or rec["name"]
    rec["inn"] = desc.get("INN") or desc.get("EMITTER_ID")
    if not rec["inn"]:
        rec["inn"] = rec["issuer"]
        rec["_inn_fallback"] = True
    if rec.get("listlevel") in (None, ""):
        rec["listlevel"] = desc.get("LISTLEVEL")

    # coupon schedule -> fixed/floater/amortized + future cashflows
    fut = [cp for cp in bz["coupons"] if cp.get("coupondate") and str(cp["coupondate"]) > TODAY.isoformat()]
    known_prc = [cp.get("valueprc") for cp in fut if cp.get("valueprc") not in (None, "")]
    is_floater = ("флоатер" in str(desc.get("BOND_TYPE") or "").lower()
                  or (fut and len(known_prc) < len(fut))
                  or (len({round(float(x), 4) for x in known_prc}) > 1 if len(known_prc) > 1 else False))
    if is_floater:
        rec["_excluded"] = "floater"
        return rec
    amort_rows = [a for a in bz["amort"] if a.get("amortdate")]
    rec["amortized"] = len(amort_rows) > 1               # final redemption is the last row (verified)
    if rec["amortized"] and not cfg.get("include_amortized"):
        rec["_excluded"] = "amortized"
        return rec

    cfs = [(date.fromisoformat(str(cp["coupondate"])), float(cp["value"]))
           for cp in fut if cp.get("value") not in (None, "")]
    face_final = None
    if amort_rows:
        last = sorted(amort_rows, key=lambda a: str(a["amortdate"]))[-1]
        face_final = float(last.get("value") or last.get("facevalue") or 0) or None
    face_final = face_final or rec["face"]
    try:
        mat = date.fromisoformat(str(rec["matdate"]))
    except (ValueError, TypeError):
        rec["_excluded"] = "no_maturity"
        return rec
    flows = cfs + [(mat, face_final)]
    dirty = rec["price"] / 100.0 * rec["face"] + rec["accrued"]
    ytm = solve_ytm(flows, dirty)
    lo, hi = cfg["ytm_sanity"]
    if not (isinstance(ytm, float) and lo <= ytm <= hi):
        rec["_excluded"] = f"ytm_insane:{ytm}"
        return rec
    rec["ytm"] = round(ytm, 3)
    # simplified tax layer (ranking approximation, NOT a tax engine): coupons taxed at tax_rate,
    # pull-to-par gain taxed at redemption at the same rate
    gain = max(0.0, face_final - rec["price"] / 100.0 * rec["face"])
    net_flows = [(d_, v * (1 - TAX)) for d_, v in cfs] + [(mat, face_final - gain * TAX)]
    ytm_net = solve_ytm(net_flows, dirty)
    rec["ytm_net"] = round(ytm_net, 3) if isinstance(ytm_net, float) and not math.isnan(ytm_net) else round(ytm * (1 - TAX), 3)
    rec["dirty_price"] = round(dirty, 2)
    rec["g_spread"] = round(rec["ytm"] - gcurve_rate(rec["duration_years"], gt, gy), 3)

    # liquidity + history-based signals
    sessions = [h for h in hist if h.get("TRADEDATE")]
    rec["sessions"] = len(sessions)
    vols20 = [float(h["VALUE"]) for h in sessions[:20] if h.get("VALUE") not in (None, "")]
    rec["median_vol"] = round(statistics.median(vols20), 0) if vols20 else 0.0
    rec["new_placement"] = rec["sessions"] < 20

    # cheapening percentile: prefer ISS historical ZSPREAD (bp), else G-spread vs weekly ZCYC
    zs = [float(h["ZSPREAD"]) for h in sessions if h.get("ZSPREAD") not in (None, "")]
    pctl = None
    src = None
    min_obs = cfg["cheapening"]["min_history_obs"]
    if len(zs) >= min_obs:
        cur, series = zs[0], zs
        pctl = round(100.0 * sum(1 for x in series if x <= cur) / len(series), 1)
        src = "iss_zspread"
    else:
        gs = []
        for h in sessions:
            y_, d_ = h.get("YIELDCLOSE"), h.get("DURATION")
            td = str(h.get("TRADEDATE") or "")
            wk = td[:8] + "01" if td else None          # monthly key into weekly_curve (see build)
            if y_ in (None, "") or d_ in (None, "", 0) or wk not in weekly_curve:
                continue
            t_, yv_ = weekly_curve[wk]
            gs.append(float(y_) - gcurve_rate(float(d_) / 365.0, t_, yv_))
        if len(gs) >= min_obs:
            cur, series = gs[0], gs
            pctl = round(100.0 * sum(1 for x in series if x <= cur) / len(series), 1)
            src = "gspread_monthly_curve"
        else:
            rec["cheap_note"] = "история спреда короче 60 сессий — перцентиль не рассчитан"
    rec["spread_pctl"] = pctl
    rec["spread_pctl_src"] = src
    return rec


def load_weekly_curves(month_keys: list[str]) -> dict:
    """Historical ZCYC snapshots at monthly granularity for the G-spread fallback.
    Keyed by 'YYYY-MM-01'. ≤7 extra requests; documented approximation."""
    out = {}
    months = sorted(set(month_keys))
    for m in months:
        try:
            d = http_json(f"{ISS}/engines/stock/zcyc.json?iss.meta=off&date={m}")
            rows = block_rows(d, "yearyields")
            if rows:
                pts = sorted((float(r["period"]), float(r["value"])) for r in rows)
                import numpy as np
                out[m] = (np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
        except Exception:  # noqa: BLE001
            continue
    if not out:
        warn("исторические срезы КБД недоступны — cheapening считается только по ISS ZSPREAD")
    return out


# ── scoring / profiles / portfolio ───────────────────────────────────────────
def zscores(vals: list[float]) -> list[float]:
    ok = [v for v in vals if v is not None]
    if len(ok) < 3:
        return [0.0] * len(vals)
    mu = statistics.mean(ok)
    sd = statistics.pstdev(ok) or 1e-9
    return [((v - mu) / sd if v is not None else 0.0) for v in vals]


def bucket_of(dur: float, edges: list[float]) -> int:
    for i, e in enumerate(edges):
        if dur < e:
            return i
    return len(edges)


def issuer_key(name: str) -> str:
    """Грубый ключ эмитента из биржевого имени выпуска — для ограничения числа выпусков одного
    эмитента в пуле кандидатов. Имена MOEX устроены как «<эмитент><код выпуска>»:
    СамолетP13 / СамолетP18 → «самолет»; Брус 2Р08 → «брус»; iКарРус1P3 → «каррус»;
    УралСт1Р05 → «уралст». Режем по первой цифре, снимаем префикс «i» и пунктуацию.
    Приблизительно, но на этой стадии INN эмитента ещё не загружен (он приходит в fine stage)."""
    s = re.sub(r"^i", "", str(name).strip(), flags=re.IGNORECASE)
    s = re.split(r"\d", s, maxsplit=1)[0]
    return re.sub(r"[^a-zа-яё]", "", s.lower())


def attach_rating(rec: dict, ratings: dict[str, dict]) -> None:
    """Attach a current official issue rating matched strictly by ISIN/SECID."""
    official = ratings.get(str(rec["secid"]).upper())
    if not official:
        rec["rating_rank"] = None
        return
    rec.update(
        {
            "rating": official["rating"],
            "rating_source": "official_issue",
            "rating_agency": official.get("rating_agency"),
            "rating_scope": "issue",
            "rating_date": official.get("rating_date"),
            "rating_checked_at": official.get("rating_checked_at"),
            "rating_source_url": official.get("rating_source_url"),
            "rating_records": official.get("rating_records", []),
        }
    )
    rec["rating_rank"] = RATING_RANK.get(rec.get("rating"))


def rating_meets_profile(rec: dict, profile: dict) -> bool:
    floor = RATING_RANK.get(profile.get("min_rating")) if profile.get("min_rating") else None
    if floor is None:
        return True
    if rec.get("rating_rank") is None:
        return bool(profile.get("allow_unrated"))
    return rec["rating_rank"] >= floor


def score_all(cands: list[dict], cfg: dict) -> None:
    w = cfg["score_weights"]
    rp = cfg["risk_proxy"]
    edges = cfg["duration_buckets_years"]
    # risk proxy
    for r in cands:
        ll = str(r.get("listlevel") or "")
        term = rp["listlevel"].get(ll, rp["listlevel"]["missing"])
        term += rp["qualified_only_penalty"] if r.get("qual_only") else 0.0
        if r["issue_rub"] and r["issue_rub"] < 1e9:
            term += rp["issue_below_1bn"]
        elif r["issue_rub"] >= 3e9:
            term += rp["issue_above_3bn"]
        r["risk_proxy"] = round(term, 3)
    # z(G-spread) within duration buckets
    for b in range(len(edges) + 1):
        idx = [i for i, r in enumerate(cands) if bucket_of(r["duration_years"], edges) == b]
        if not idx:
            continue
        if len(idx) < 3:
            for i in idx:
                cands[i]["z_spread"] = 0.0
                cands[i]["bucket_note"] = "в дюрационной корзине <3 бумаг — z-спред не информативен"
            continue
        zs = zscores([cands[i]["g_spread"] for i in idx])
        for i, z in zip(idx, zs):
            cands[i]["z_spread"] = round(z, 3)
    zv = zscores([math.log1p(r["median_vol"]) if not r["new_placement"] else None for r in cands])
    zr = zscores([r.get("rating_rank") for r in cands])   # unrated → z=0 (нейтрально в скоре)
    for r, zvol, zrat in zip(cands, zv, zr):
        cheap = cfg["cheapening"]["bonus"] if (r.get("spread_pctl") or 0) >= cfg["cheapening"]["bonus_percentile"] else 0.0
        r["cheap_bonus"] = cheap
        r["score"] = round(w["z_spread"] * r.get("z_spread", 0.0)
                           + w["risk_proxy"] * r["risk_proxy"]
                           + w["z_volume"] * zvol
                           + w["cheapening_bonus"] * cheap
                           + w["z_rating"] * zrat, 3)


def allocate(picks: list[dict], cfg: dict) -> dict:
    """Weights ∝ positive-shifted score, cap per bond / per issuer (INN), renormalize."""
    pcfg = cfg["portfolio"]
    min_issuers = pcfg["min_issuers_meaningful"]
    if not picks:
        return {"weights": [], "issuers": 0, "constructible": False,
                "min_issuers_required": min_issuers, "note": "пусто"}
    smin = min(p["score"] for p in picks)
    raw = [p["score"] - smin + 0.1 for p in picks]
    tot = sum(raw)
    ws = [x / tot for x in raw]
    for _ in range(12):                                   # iterative capping
        changed = False
        over = [i for i, w_ in enumerate(ws) if w_ > pcfg["cap_per_bond"] + 1e-9]
        for i in over:
            ws[i] = pcfg["cap_per_bond"]
            changed = True
        by_inn: dict[str, list[int]] = {}
        for i, p in enumerate(picks):
            by_inn.setdefault(str(p["inn"]), []).append(i)
        for inn, idx in by_inn.items():
            s = sum(ws[i] for i in idx)
            if s > pcfg["cap_per_issuer"] + 1e-9:
                for i in idx:
                    ws[i] *= pcfg["cap_per_issuer"] / s
                changed = True
        free = [i for i, w_ in enumerate(ws) if w_ < pcfg["cap_per_bond"] - 1e-9]
        rem = 1.0 - sum(ws)
        if abs(rem) < 1e-9 or not free:
            break
        add = rem / len(free)
        for i in free:
            ws[i] += add
        if not changed and abs(rem) < 1e-6:
            break
    tot = sum(ws)
    ws = [w_ / tot for w_ in ws]
    n_issuers = len({str(p["inn"]) for p in picks})
    note = None
    constructible = n_issuers >= min_issuers
    if not constructible:
        note = (f"эмитентов всего {n_issuers} — осмысленная диверсификация не достигается, "
                "портфель не сформирован; список стоит расширить вручную")
    return {"weights": [round(w_, 4) for w_ in ws], "issuers": n_issuers,
            "constructible": constructible, "min_issuers_required": min_issuers, "note": note}


# ── journal / review / events ────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def index_history_map() -> dict:
    try:
        d = http_json(f"{ISS}/history/engines/stock/markets/index/boards/RTSI/securities/RUCBTRNS.json"
                      "?iss.meta=off&history.columns=TRADEDATE,CLOSE&sort_order=desc&limit=100")
        rows = block_rows(d, "history")
        # ISS may paginate; 100 recent sessions cover ~5 months of reviews
        return {r["TRADEDATE"]: float(r["CLOSE"]) for r in rows if r.get("CLOSE")}
    except Exception as e:  # noqa: BLE001
        warn(f"RUCBTRNS недоступен — сравнение с индексом пропущено ({str(e)[:50]})")
        return {}


def review_journal(journal: list, price_now: dict, cfg: dict) -> list:
    """Approximate forward tracking: price change + linear coupon accrual vs RUCBTRNS.
    Labeled approximation (купоны начислены линейно от купонной ставки), never silent."""
    idx = index_history_map()
    idx_dates = sorted(idx)
    out = []
    for entry in journal[-cfg["journal"]["review_max_entries"]:]:
        e_date = entry["snapshot_date"]
        held_days = (TODAY - date.fromisoformat(e_date)).days
        if held_days < 5:
            continue
        rets = []
        for p in entry.get("picks", []):
            now = price_now.get(p["secid"])
            if not now:
                continue
            dirty_then, dirty_now = p["dirty_price"], now["dirty_price"]
            coup = p.get("coupon_pct_approx", 0.0) / 100.0 * held_days / 365.0 * p.get("face", 1000)
            rets.append((dirty_now - dirty_then + coup) / dirty_then)
        if not rets:
            continue
        port_ret = sum(rets) / len(rets)
        i0 = next((idx[d_] for d_ in idx_dates if d_ >= e_date), None)
        i1 = idx[idx_dates[-1]] if idx_dates else None
        idx_ret = (i1 / i0 - 1.0) if (i0 and i1) else None
        out.append({"entry_date": e_date, "profile": entry.get("profile"), "held_days": held_days,
                    "n_reviewed": len(rets), "portfolio_return_pct": round(port_ret * 100, 2),
                    "rucbtrns_return_pct": (round(idx_ret * 100, 2) if idx_ret is not None else None),
                    "method": "приблизительно: изменение грязной цены + линейное начисление купона"})
    return out


def detect_events(universe: list[dict], cands: list[dict], cfg: dict) -> list:
    prev = load_json(UNIVERSE_PREV, {})
    prev_ids = set(prev.get("secids", []))
    cur_ids = {r["secid"] for r in universe}
    events = []
    if prev_ids:
        for r in universe:
            if r["secid"] not in prev_ids and r["offer"] is None:
                events.append({"type": "new_placement", "date": TODAY.isoformat(),
                               "secid": r["secid"], "name": r["name"],
                               "text": f"Новое размещение: {r['name']} ({r['secid']}), дюрация {r['duration_years']}г"})
    prev_pctl = prev.get("pctl", {})
    for r in sorted(cands, key=lambda x: -(x.get("score") or -9))[:30]:
        p = r.get("spread_pctl")
        if p is not None and p >= 90 and prev_pctl.get(r["secid"], 0) < 90:
            events.append({"type": "spread_event", "date": TODAY.isoformat(),
                           "secid": r["secid"], "name": r["name"],
                           "text": f"{r['name']}: G-спред у 90-го перцентиля своей истории ({p}%)"})
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(UNIVERSE_PREV, "w", encoding="utf-8") as f:
        json.dump({"date": TODAY.isoformat(), "secids": sorted(cur_ids),
                   "pctl": {r["secid"]: r.get("spread_pctl") for r in cands if r.get("spread_pctl") is not None}},
                  f, ensure_ascii=False)
    return events


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    universe, ctx = build_universe(cfg)
    counts = ctx["counts"]
    gt, gy = ctx["gt"], ctx["gy"]
    log(f"universe: {counts}")
    if counts["universe_clean"] == 0:
        log("СТОП: пустая вселенная — не публикуем")
        return 1

    # stratified candidate selection: pure top-by-spread is junk-heavy (ПИР/floaters), which
    # starves conservative/balanced pools. Quotas: 20 quality (LL1-2, ≥3bn), 20 mid (≥1bn),
    # rest by raw spread.
    # ВАЖНО (иначе шорт-лист вырождается): внутри каждой квоты сортировка по спреду, поэтому один
    # эмитент со множеством выпусков (Самолёт P13/P16/P18…) съедал все слоты — на выходе портфель
    # из одного имени, а `min_issuers_meaningful` не выполнялся и портфель не строился вообще.
    # Ограничиваем число выпусков ОДНОГО эмитента в пуле кандидатов (issuer_cap_candidates).
    pool = sorted([r for r in universe if r["offer"] is None], key=lambda r: -r["rough_spread"])
    qual_a = [r for r in pool if str(r.get("listlevel")) in ("1", "2") and r["issue_rub"] >= 3e9]
    qual_b = [r for r in pool if r["issue_rub"] >= 1e9]
    issuer_cap = int(cfg.get("issuer_cap_candidates", 2))
    per_issuer: dict[str, int] = {}
    main_cand, seen_sel = [], set()
    for src, quota in ((qual_a, 20), (qual_b, 20), (pool, cfg["max_candidates"])):
        for r in src:
            if len(main_cand) >= cfg["max_candidates"] and src is pool:
                break
            if r["secid"] in seen_sel:
                continue
            if src is not pool and quota <= 0:
                break
            ikey = issuer_key(r.get("name") or r.get("secid") or "")
            if per_issuer.get(ikey, 0) >= issuer_cap:
                continue                      # эмитент уже представлен — освобождаем слот другим
            main_cand.append(r)
            seen_sel.add(r["secid"])
            per_issuer[ikey] = per_issuer.get(ikey, 0) + 1
            if src is not pool:
                quota -= 1
    main_cand = main_cand[:cfg["max_candidates"]]
    offer_cand = sorted([r for r in universe if r["offer"] is not None],
                        key=lambda r: -r["rough_spread"])[:cfg["max_offer_candidates"]]

    # monthly historical curve snapshots for the cheapening fallback (last 7 months)
    mkeys = []
    y, m = TODAY.year, TODAY.month
    for _ in range(7):
        mkeys.append(f"{y:04d}-{m:02d}-01")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    weekly = load_weekly_curves(mkeys)
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg["throttle_workers"]) as ex:
        enriched = list(ex.map(lambda r: enrich(r, gt, gy, weekly, cfg), main_cand))
        offers = list(ex.map(lambda r: enrich(r, gt, gy, weekly, cfg), offer_cand))

    excl = {}
    cands = []
    for r in enriched:
        if r.get("_excluded"):
            excl[r["_excluded"].split(":")[0]] = excl.get(r["_excluded"].split(":")[0], 0) + 1
        else:
            cands.append(r)
    offers_ok = [r for r in offers if not r.get("_excluded")]
    log(f"fine stage: годных {len(cands)}, оферта {len(offers_ok)}, исключено {excl}")
    if excl.get("enrich_error", 0) > 5:
        warn(f"обогащение упало для {excl['enrich_error']} бумаг — список может быть неполным")
    if any(r.get("_inn_fallback") for r in cands):
        warn("у части бумаг ИНН эмитента недоступен — диверсификация посчитана по названию эмитента")
    if not (len(gt) and len(gy)):
        warn("КБД (ZCYC) недоступна — использована самодельная кривая ОФЗ")

    min_vol = cfg.get("min_median_volume_rub", 100000)
    illiq = [r for r in cands if not r["new_placement"] and r["median_vol"] < min_vol]
    if illiq:
        excl["illiquid"] = len(illiq)
        cands = [r for r in cands if r["new_placement"] or r["median_vol"] >= min_vol]
        log(f"фильтр ликвидности: исключено {len(illiq)} бумаг (медиана < {min_vol:,.0f} ₽/день)")

    rating_isins = {r["secid"] for r in cands + offers_ok}
    ratings, ratings_meta = load_official_ratings(
        rating_isins, cache_path=DEFAULT_CACHE, refresh=False
    )
    if ratings_meta.get("sources_ok", 0) == 0:
        log("СТОП: все официальные источники рейтингов недоступны — finder.json не перезаписываем")
        return 1
    for source, status in (ratings_meta.get("sources") or {}).items():
        if status.get("status") != "ok":
            warn(f"источник рейтингов {source} временно недоступен — использованы остальные агентства")
    for r in cands + offers_ok:
        attach_rating(r, ratings)
    n_unrated = sum(1 for r in cands if r.get("rating_rank") is None)
    scored = [r for r in cands if not r["new_placement"]]
    newpl = [r for r in cands if r["new_placement"]]
    score_all(scored + newpl, cfg)                       # new placements scored too (no vol z)
    for r in newpl:
        r["new_note"] = "нет истории торгов (<20 сессий) — ликвидность не проверена"

    profiles_out = {}
    for pid, p in cfg["profiles"].items():
        pool = scored + (newpl if p["score_new_placements"] else [])
        pool = [r for r in pool
                if r["duration_years"] <= p["max_duration_years"]
                and (int(r["listlevel"]) if str(r.get("listlevel") or "").isdigit() else 3) in p["listlevels"]
                and (not p["exclude_qualified_only"] or not r.get("qual_only"))
                and r["issue_rub"] >= p["min_issue_rub"]
                and rating_meets_profile(r, p)]
        top = sorted(pool, key=lambda r: -r["score"])[:p["top_n"]]
        alloc = allocate(top, cfg)
        keys = ("secid", "board", "name", "issuer", "inn", "rating", "rating_source", "rating_rank",
                "rating_agency", "rating_scope", "rating_date", "rating_checked_at",
                "rating_source_url", "rating_records",
                "price", "dirty_price", "accrued", "face", "coupon_pct",
                "lot_value", "ytm", "ytm_net", "g_spread", "spread_pctl", "spread_pctl_src",
                "duration_years", "median_vol", "sessions", "listlevel", "issue_rub", "risk_proxy",
                "z_spread", "cheap_bonus", "score", "qual_only", "new_placement", "matdate",
                "cheap_note", "bucket_note", "new_note")
        picks = []
        for r, w_ in zip(top, alloc["weights"]):
            row = {k: r.get(k) for k in keys if r.get(k) is not None or k in ("spread_pctl",)}
            row["weight"] = w_
            picks.append(row)
        wavg = lambda key: (round(sum((x.get(key) or 0) * x["weight"] for x in picks), 2) if picks else None)  # noqa: E731
        constructible = bool(alloc.get("constructible"))
        profiles_out[pid] = {"title": p["title"], "params": p, "picks": picks,
                             "aggregates": {
                                 "ytm_net_wavg": wavg("ytm_net") if constructible else None,
                                 "duration_wavg": wavg("duration_years") if constructible else None,
                                 "issuers": alloc.get("issuers"),
                                 "constructible": constructible,
                                 "min_issuers_required": alloc.get("min_issuers_required"),
                                 "note": alloc.get("note"),
                             }}

    events = detect_events(universe, scored + newpl, cfg)

    # journal append (balanced profile = the monthly decision list) + review of past entries
    journal = load_json(JOURNAL, [])
    price_now = {r["secid"]: {"dirty_price": r["dirty_price"]} for r in cands}
    review = review_journal(journal, price_now, cfg)
    snapshot = max((str(r.get("prevdate") or "") for r in universe), default=TODAY.isoformat()) or TODAY.isoformat()
    bal = profiles_out.get("balanced", {})
    if (bal.get("aggregates") or {}).get("constructible"):
        journal.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "snapshot_date": snapshot, "profile": "balanced",
                        "picks": [{"secid": x["secid"], "name": x["name"], "dirty_price": x["dirty_price"],
                                   "face": x["face"], "weight": x["weight"],
                                   "coupon_pct_approx": x.get("coupon_pct")}
                                  for x in bal.get("picks", [])]})
    else:
        log("balanced journal: портфель не сформирован, snapshot не добавлен")
    journal = journal[-cfg["journal"]["max_entries"]:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(JOURNAL, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "snapshot_date": snapshot,
            "warnings": WARNINGS,
            "counts": {**counts, "excluded_fine": excl, "enriched": len(cands)},
            "ratings_source": "official_issue_ratings",
            "ratings": ratings_meta,
            "rating_coverage": {
                "candidates": len(cands),
                "official_issue_ratings": len(cands) - n_unrated,
                "unrated": n_unrated,
                "policy": "minimum current issue rating across official agencies",
            },
            "disclaimer": "Это шорт-лист для ручной проверки, не инвестиционная рекомендация. "
                          "Рейтинг выпуска проверяется по официальным страницам АКРА, Эксперт РА и НКР; "
                          "отсутствие рейтинга означает, что действующая оценка выпуска не найдена.",
            "methodology": "Две стадии: дешёвый префильтр всех торгуемых бордов, затем обогащение топ-60 "
                           "(график купонов, описание, история 130 сессий). Скор = z(G-спред в дюрационной "
                           "корзине) + 0.4·риск-прокси(листинг/квал/размер) + 0.3·z(медианный объём) + "
                           "0.3·бонус за спред у верхней границы истории + 0.5·z(официальный рейтинг "
                           "выпуска; при нескольких агентствах берётся минимальный). Профили режут по мин-рейтингу "
                           "(консервативный ≥ A-, сбалансированный ≥ BBB-). Налог 13% — приближение "
                           "для ранжирования, не налоговый калькулятор. Флоатеры, амортизация, ПИР "
                           "(HIGHRISK), колл-опционы исключены; оферта — отдельным списком к дате оферты.",
        },
        "profiles": profiles_out,
        "new_placements": [{k: r.get(k) for k in ("secid", "name", "issuer", "price", "ytm", "ytm_net",
                                                  "g_spread", "duration_years", "score", "new_note")}
                           for r in sorted(newpl, key=lambda x: -(x.get("score") or -9))[:15]],
        "with_offer": [{k: r.get(k) for k in ("secid", "name", "issuer", "price", "ytm", "ytm_net",
                                              "g_spread", "duration_years", "median_vol")}
                       | {"offer_date": (r.get("offer") or {}).get("date"),
                          "ytm_to_offer": (r.get("offer") or {}).get("ytm_to_offer")}
                       for r in offers_ok[:15]],
        "events": events[:30],
        "journal_review": review,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"OK → {OUT_JSON}: профили {[ (k, len(v['picks'])) for k, v in profiles_out.items() ]}, "
        f"новые {len(payload['new_placements'])}, оферта {len(payload['with_offer'])}, "
        f"события {len(events)}, warnings {len(WARNINGS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
