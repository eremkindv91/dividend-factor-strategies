#!/usr/bin/env python3
"""Build the normalized Bond Portfolio Lab universe from MOEX ISS records.

The module keeps source semantics explicit. In particular, the raw MOEX `DURATION`
field is retained only as source metadata; portfolio duration is calculated from
cash flows and the market YTM under an effective annual convention.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Callable, Iterable

import numpy as np
from scipy.optimize import brentq

from .fns_sector_enrichment import enrich_issuer_master

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "portfolio_config.json"
DEFAULT_ISSUER_MASTER = HERE / "issuer_master.json"

RATING_RANK = {
    "AAA": 20, "AA+": 19, "AA": 18, "AA-": 17, "A+": 16, "A": 15,
    "A-": 14, "BBB+": 13, "BBB": 12, "BBB-": 11, "BB+": 10,
    "BB": 9, "BB-": 8, "B+": 7, "B": 6, "B-": 5, "CCC+": 4,
    "CCC": 3, "CCC-": 2, "CC": 1, "C": 0,
}


def load_json(path: str | os.PathLike) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _num(value, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _iso(value) -> str | None:
    if not value or str(value) == "0000-00-00":
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _block_rows(payload: dict, block: str) -> list[dict]:
    item = payload.get(block) or {}
    columns = item.get("columns") or []
    return [dict(zip(columns, row)) for row in item.get("data") or []]


def _description_map(payload: dict) -> dict:
    return {str(row.get("name")): row.get("value") for row in _block_rows(payload, "description")}


def _rating_group(rating: str | None) -> str | None:
    if not rating:
        return None
    if rating.startswith("AAA"):
        return "AAA"
    if rating.startswith("AA"):
        return "AA"
    if rating.startswith("A"):
        return "A"
    if rating.startswith("BBB"):
        return "BBB"
    return "below_bbb"


def _market_clean(row: dict) -> float | None:
    market = row.get("_md") or {}
    for key in ("WAPRICE", "MARKETPRICE", "LCLOSEPRICE", "MARKETPRICE2", "LAST"):
        value = _num(market.get(key))
        if value and value > 0:
            return value
    for key in ("PREVWAPRICE", "PREVLEGALCLOSEPRICE", "PREVPRICE"):
        value = _num(row.get(key))
        if value and value > 0:
            return value
    return None


def solve_effective_annual_ytm(
    flows: Iterable[tuple[date, float]], dirty_price: float, as_of: date
) -> float | None:
    future = [(dt, float(amount)) for dt, amount in flows if dt > as_of and amount > 0]
    if not future or dirty_price <= 0:
        return None

    def npv(rate: float) -> float:
        return sum(
            amount / (1.0 + rate) ** ((dt - as_of).days / 365.0)
            for dt, amount in future
        ) - dirty_price

    try:
        return float(brentq(npv, -0.95, 5.0, maxiter=250))
    except (ValueError, RuntimeError):
        return None


def modified_duration_effective_annual(
    flows: Iterable[tuple[date, float]], dirty_price: float, ytm: float, as_of: date
) -> float | None:
    if dirty_price <= 0 or ytm <= -1:
        return None
    numerator = 0.0
    for dt, amount in flows:
        if dt <= as_of or amount <= 0:
            continue
        years = (dt - as_of).days / 365.0
        numerator += years * amount / (1.0 + ytm) ** (years + 1.0)
    value = numerator / dirty_price
    return value if math.isfinite(value) and value >= 0 else None


def _duration_bucket(value: float, edges: list[float]) -> str:
    for left, right in zip(edges, edges[1:]):
        if left <= value < right:
            return f"{left:g}-{right:g}"
    return f"{edges[-1]:g}+"


def _winsorized_median(values: list[float], lower: float, upper: float) -> float | None:
    clean = np.array([value for value in values if math.isfinite(value)], dtype=float)
    if not clean.size:
        return None
    lo, hi = np.quantile(clean, [lower, upper])
    return float(np.median(np.clip(clean, lo, hi)))


def attach_peer_benchmarks(bonds: list[dict], config: dict) -> None:
    cfg = config["peer_benchmark"]
    edges = [float(value) for value in cfg["duration_edges_years"]]
    minimum = int(cfg["minimum_observations"])
    lower = float(cfg["winsor_lower"])
    upper = float(cfg["winsor_upper"])

    eligible = [
        row for row in bonds
        if row.get("instrument_type") == "corp"
        and row.get("rating")
        and _num(row.get("g_spread_pp")) is not None
        and _num(row.get("duration_value")) is not None
    ]
    for row in bonds:
        if row.get("instrument_type") == "ofz":
            row.update({"peer_spread_pp": 0.0, "excess_spread_pp": row.get("g_spread_pp"),
                        "peer_n": 0, "peer_fallback_level": "sovereign_curve"})
            continue
        rating = row.get("rating")
        group = _rating_group(rating)
        duration = _num(row.get("duration_value"))
        spread = _num(row.get("g_spread_pp"))
        if not rating or duration is None or spread is None:
            row.update({"peer_spread_pp": None, "excess_spread_pp": None,
                        "peer_n": 0, "peer_fallback_level": "unavailable"})
            continue
        bucket = _duration_bucket(duration, edges)
        levels = [
            ("rating_notch_duration", [item for item in eligible if item.get("rating") == rating and _duration_bucket(float(item["duration_value"]), edges) == bucket]),
            ("rating_group_duration", [item for item in eligible if _rating_group(item.get("rating")) == group and _duration_bucket(float(item["duration_value"]), edges) == bucket]),
            ("rating_group", [item for item in eligible if _rating_group(item.get("rating")) == group]),
        ]
        benchmark = None
        peer_n = 0
        fallback_level = "fixed_rating_fallback"
        for level, peers in levels:
            if len(peers) >= minimum:
                peer_n = len(peers)
                benchmark = _winsorized_median(
                    [float(item["g_spread_pp"]) for item in peers], lower, upper
                )
                fallback_level = level
                break
        if benchmark is None:
            benchmark = _num(cfg["fixed_fallback_pp"].get(group))
        row.update({
            "peer_spread_pp": round(benchmark, 4) if benchmark is not None else None,
            "excess_spread_pp": round(spread - benchmark, 4) if benchmark is not None else None,
            "peer_n": peer_n,
            "peer_fallback_level": fallback_level if benchmark is not None else "unavailable",
        })


def _sector_for(issuer: dict, issuer_master: dict) -> tuple[str, str]:
    inn = str(issuer.get("INN") or "").strip()
    item = (issuer_master.get("issuers") or {}).get(inn)
    if item:
        return str(item["sector"]), str(item["sector_source"])
    return "unknown", "unmapped"


def normalize_bond(
    raw: dict,
    rating_record: dict | None,
    issuer: dict | None,
    enrichment: dict,
    gcurve_rate: Callable[[float], float],
    config: dict,
    issuer_master: dict,
    as_of: date,
) -> dict | None:
    market = raw.get("_md") or {}
    board = str(raw.get("_board") or raw.get("BOARDID") or "")
    instrument_type = "ofz" if board == "TQOB" else "corp"
    face_unit = str(raw.get("FACEUNIT") or "").upper()
    if face_unit not in {"SUR", "RUB", "RUR"}:
        return None
    if "валют" in str(raw.get("BONDTYPE") or "").lower():
        return None
    clean_price = _market_clean(raw)
    face = _num(raw.get("FACEVALUEONSETTLEDATE")) or _num(raw.get("FACEVALUE"))
    aci = _num(raw.get("ACCRUEDINT"), 0.0)
    lot_size = int(_num(raw.get("LOTSIZE"), 1) or 1)
    maturity_text = _iso(raw.get("MATDATE"))
    if clean_price is None or not face or not maturity_text or lot_size < 1:
        return None

    dirty_per_bond = clean_price / 100.0 * face + float(aci or 0.0)
    dirty_per_lot = dirty_per_bond * lot_size
    cashflows = enrichment.get("cashflows") or []
    flows: list[tuple[date, float]] = []
    for item in cashflows:
        try:
            flows.append((date.fromisoformat(str(item[0])[:10]), float(item[1])))
        except (TypeError, ValueError):
            continue
    maturity = date.fromisoformat(maturity_text)
    if not any(dt == maturity and amount >= face * 0.5 for dt, amount in flows):
        flows.append((maturity, face))

    ytm = solve_effective_annual_ytm(flows, dirty_per_bond, as_of)
    duration = modified_duration_effective_annual(flows, dirty_per_bond, ytm, as_of) if ytm is not None else None
    raw_duration_days = _num(market.get("DURATION"))
    flags: list[str] = []
    if duration is None:
        flags.append("modified_duration_unavailable")
    if not enrichment.get("history_sessions"):
        flags.append("liquidity_history_unavailable")

    description = enrichment.get("description") or {}
    is_qualified = str(description.get("ISQUALIFIEDINVESTORS") or "0") == "1"
    coupon_type_raw = str(description.get("BOND_TYPE") or raw.get("BONDTYPE") or "").lower()
    coupon_type = "floating" if "перем" in coupon_type_raw or "флоат" in coupon_type_raw else "fixed"
    if not _num(raw.get("COUPONPERCENT")):
        coupon_type = "zero"
    has_put = any(_iso(raw.get(key)) for key in ("OFFERDATE", "PUTOPTIONDATE", "BUYBACKDATE"))
    has_call = bool(_iso(raw.get("CALLOPTIONDATE")))
    amortizing = bool(enrichment.get("amortizing"))
    if is_qualified:
        flags.append("qualified_only")
    if has_put:
        flags.append("put_offer")
    if has_call:
        flags.append("callable")
    if amortizing:
        flags.append("amortizing")
    if coupon_type != "fixed":
        flags.append(f"coupon_type_{coupon_type}")

    if instrument_type == "ofz":
        issuer_id = "sovereign:minfin-rf"
        issuer_name = "Минфин России"
        sector, sector_source = "Государственные облигации", "instrument_class"
        risk_class = "sovereign_rub"
        rating = None
        rating_rank = None
        rating_scope = "sovereign"
        rating_records: list[dict] = []
    else:
        issuer = issuer or {}
        emitter_id = description.get("EMITTER_ID") or issuer.get("EMITTER_ID")
        inn = str(issuer.get("INN") or "").strip()
        issuer_id = f"inn:{inn}" if inn else f"moex-emitter:{emitter_id}" if emitter_id else f"name-fallback:{hashlib.sha256(str(raw.get('SHORTNAME') or '').encode()).hexdigest()[:12]}"
        issuer_name = str(issuer.get("SHORT_TITLE") or issuer.get("TITLE") or raw.get("SHORTNAME") or "")
        if not inn and not emitter_id:
            flags.append("issuer_id_fallback")
        sector, sector_source = _sector_for(issuer, issuer_master)
        if sector == "unknown":
            flags.append("sector_unknown")
        risk_class = "corporate"
        rating_record = rating_record or {}
        rating = rating_record.get("rating")
        rating_rank = RATING_RANK.get(str(rating)) if rating else None
        rating_scope = str(rating_record.get("rating_scope") or "issue") if rating else None
        rating_records = list(rating_record.get("rating_records") or [])
        if not rating:
            flags.append("official_issue_rating_unavailable")

    history_values = [float(value) for value in enrichment.get("history_values") or [] if _num(value) is not None and float(value) >= 0]
    median_volume = float(median(history_values)) if history_values else 0.0
    history_sessions = int(enrichment.get("history_sessions") or len(history_values))
    value_today = _num(market.get("VALTODAY_RUR")) or _num(market.get("VALTODAY"), 0.0) or 0.0
    g_rate = gcurve_rate(duration) if duration is not None else None
    g_spread = ytm * 100.0 - g_rate if ytm is not None and g_rate is not None else None
    start_date = _iso(description.get("STARTDATEMOEX") or raw.get("PREVDATE"))
    new_placement = bool(start_date and (as_of - date.fromisoformat(start_date)).days <= 90)
    price_date = _iso(raw.get("PREVDATE")) or _iso(str(market.get("SYSTIME") or "")[:10])

    row = {
        "secid": str(raw.get("SECID") or ""),
        "isin": str(raw.get("ISIN") or raw.get("SECID") or ""),
        "name": str(raw.get("SHORTNAME") or raw.get("SECNAME") or raw.get("SECID") or ""),
        "instrument_type": instrument_type,
        "risk_class": risk_class,
        "issuer_id": issuer_id,
        "issuer_name": issuer_name,
        "issuer_inn": str((issuer or {}).get("INN") or "") or None,
        "ultimate_parent_id": None,
        "sector": sector,
        "sector_source": sector_source,
        "board": board,
        "rating": rating,
        "rating_rank": rating_rank,
        "rating_group": _rating_group(rating),
        "rating_scope": rating_scope,
        "rating_agency": (rating_record or {}).get("rating_agency") if instrument_type == "corp" else None,
        "rating_date": (rating_record or {}).get("rating_date") if instrument_type == "corp" else None,
        "rating_checked_at": (rating_record or {}).get("rating_checked_at") if instrument_type == "corp" else None,
        "rating_source_url": (rating_record or {}).get("rating_source_url") if instrument_type == "corp" else None,
        "rating_records": rating_records,
        "face_value_per_bond_rub": round(face, 4),
        "lot_size": lot_size,
        "clean_price_pct": round(clean_price, 4),
        "aci_per_bond_rub": round(float(aci or 0.0), 4),
        "dirty_price_per_bond_rub": round(dirty_per_bond, 4),
        "dirty_price_per_lot_rub": round(dirty_per_lot, 4),
        "ytm_gross_pct": round(ytm * 100.0, 4) if ytm is not None else None,
        "ytm_net_est_pct": round(ytm * (1.0 - float(config["tax_model"]["tax_rate"])) * 100.0, 4) if ytm is not None else None,
        "tax_model_version": config["tax_model"]["version"],
        "g_curve_yield_pct": round(g_rate, 4) if g_rate is not None else None,
        "g_spread_pp": round(g_spread, 4) if g_spread is not None else None,
        "peer_spread_pp": None,
        "excess_spread_pp": None,
        "z_spread_bp": _num(market.get("ZSPREADATWAPRICE")) or _num(market.get("ZSPREAD")),
        "duration_value": round(duration, 6) if duration is not None else 0.0,
        "duration_type": "modified_duration_effective_annual" if duration is not None else "unavailable",
        "duration_source": "calculated_from_moex_bondization_and_market_price" if duration is not None else "unavailable",
        "duration_as_of": price_date,
        "moex_duration_raw_days": raw_duration_days,
        "maturity_date": maturity_text,
        "years_to_maturity": round((maturity - as_of).days / 365.0, 4),
        "coupon_pct": round(_num(raw.get("COUPONPERCENT"), 0.0) or 0.0, 4),
        "coupon_frequency": int(_num(description.get("COUPONFREQUENCY")) or (round(365 / float(raw.get("COUPONPERIOD"))) if _num(raw.get("COUPONPERIOD")) else 0)),
        "coupon_type": coupon_type,
        "median_volume_20d_rub": round(median_volume, 2),
        "history_sessions": history_sessions,
        "value_today_rub": round(value_today, 2),
        "issue_size_rub": round((_num(raw.get("ISSUESIZEPLACED")) or _num(raw.get("ISSUESIZE"), 0.0) or 0.0) * face, 2),
        "list_level": int(_num(raw.get("LISTLEVEL")) or 0) or None,
        "qualified_only": is_qualified,
        "new_placement": new_placement,
        "has_put_offer": has_put,
        "has_call": has_call,
        "amortizing": amortizing,
        "data_quality_flags": sorted(set(flags)),
        "source_dates": {"price": price_date, "history": enrichment.get("history_as_of"), "rating": (rating_record or {}).get("rating_checked_at")},
        "cashflows_12m": enrichment.get("cashflows_12m") or [],
    }
    return row


def _candidate(raw: dict, minimum_value: float) -> bool:
    if str(raw.get("FACEUNIT") or "").upper() not in {"SUR", "RUB", "RUR"}:
        return False
    if "валют" in str(raw.get("BONDTYPE") or "").lower():
        return False
    market = raw.get("_md") or {}
    value_today = _num(market.get("VALTODAY_RUR")) or _num(market.get("VALTODAY"), 0.0) or 0.0
    return bool(
        _market_clean(raw)
        and _num(raw.get("FACEVALUE"))
        and _iso(raw.get("MATDATE"))
        and value_today >= minimum_value
    )


def _fetch_enrichment(raw: dict, http_json: Callable[[str], dict], iss: str, today: date, history_limit: int) -> dict:
    secid = raw["SECID"]
    board = raw.get("_board") or raw.get("BOARDID")
    description_payload = http_json(f"{iss}/securities/{secid}.json?iss.meta=off")
    description = _description_map(description_payload)
    bondization = http_json(f"{iss}/securities/{secid}/bondization.json?iss.meta=off&limit=unlimited")
    coupons = _block_rows(bondization, "coupons")
    amortizations = _block_rows(bondization, "amortizations")
    flows: list[list] = []
    cashflows_12m: list[dict] = []
    for coupon in coupons:
        coupon_date = _iso(coupon.get("coupondate"))
        amount = _num(coupon.get("value"))
        if not coupon_date or amount is None or date.fromisoformat(coupon_date) <= today:
            continue
        flows.append([coupon_date, amount])
        if (date.fromisoformat(coupon_date) - today).days <= 366:
            cashflows_12m.append({"date": coupon_date, "amount_per_bond_rub": round(amount, 4)})
    amort_rows = [row for row in amortizations if _iso(row.get("amortdate"))]
    for amort in amort_rows:
        amort_date = _iso(amort.get("amortdate"))
        amount = _num(amort.get("value")) or _num(amort.get("facevalue"))
        if amort_date and amount and date.fromisoformat(amort_date) > today:
            flows.append([amort_date, amount])
    history = http_json(
        f"{iss}/history/engines/stock/markets/bonds/boards/{board}/securities/{secid}.json"
        f"?iss.meta=off&limit={history_limit}&history.columns=TRADEDATE,VALUE"
    )
    history_rows = _block_rows(history, "history")[-history_limit:]
    history_values = [float(row["VALUE"]) for row in history_rows if _num(row.get("VALUE")) is not None]
    return {
        "description": description,
        "emitter_id": description.get("EMITTER_ID"),
        "cashflows": sorted(flows),
        "cashflows_12m": sorted(cashflows_12m, key=lambda item: item["date"]),
        "amortizing": len(amort_rows) > 1,
        "history_values": history_values,
        "history_sessions": len(history_values),
        "history_as_of": _iso(history_rows[-1].get("TRADEDATE")) if history_rows else None,
    }


def build_live_universe(
    load_board: Callable[[str], list[dict]],
    http_json: Callable[[str], dict],
    iss: str,
    ratings: dict[str, dict],
    ratings_meta: dict,
    gcurve_rate: Callable[[float], float],
    config_path: str | os.PathLike = DEFAULT_CONFIG,
    issuer_master_path: str | os.PathLike = DEFAULT_ISSUER_MASTER,
    as_of: date | None = None,
    fns_lookup: Callable[[str], dict] | None = None,
    sector_sleep: Callable[[float], None] | None = None,
) -> dict:
    as_of = as_of or date.today()
    config = load_json(config_path)
    issuer_master = load_json(issuer_master_path)
    universe_cfg = config["universe"]
    raw_corp = load_board("TQCB")
    raw_ofz = load_board("TQOB")
    minimum_value = float(universe_cfg["minimum_value_today_rub"])
    corp = [row for row in raw_corp if _candidate(row, minimum_value)]
    ofz = [row for row in raw_ofz if _candidate(row, minimum_value)]
    corp.sort(key=lambda row: -float((row.get("_md") or {}).get("VALTODAY_RUR") or (row.get("_md") or {}).get("VALTODAY") or 0))
    max_corp = int(universe_cfg["maximum_corporate_enrichment"])
    selected = ofz + corp[:max_corp]
    workers = int(universe_cfg["workers"])
    history_limit = int(universe_cfg["history_sessions"])

    enrichments: dict[str, dict] = {}
    errors: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_fetch_enrichment, row, http_json, iss, as_of, history_limit): row
            for row in selected
        }
        for future in concurrent.futures.as_completed(future_map):
            row = future_map[future]
            try:
                enrichments[row["SECID"]] = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append({"secid": row.get("SECID"), "reason": str(exc)[:180]})

    emitter_ids = sorted({str(item.get("emitter_id")) for item in enrichments.values() if item.get("emitter_id")})
    emitters: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(http_json, f"{iss}/emitters/{emitter_id}.json?iss.meta=off"): emitter_id
            for emitter_id in emitter_ids
        }
        for future in concurrent.futures.as_completed(future_map):
            emitter_id = future_map[future]
            try:
                rows = _block_rows(future.result(), "emitter")
                if rows:
                    emitters[emitter_id] = rows[0]
            except Exception as exc:  # noqa: BLE001
                errors.append({"emitter_id": emitter_id, "reason": str(exc)[:180]})

    value_by_emitter: dict[str, float] = {}
    for raw in corp[:max_corp]:
        enrichment = enrichments.get(str(raw.get("SECID"))) or {}
        emitter_id = str(enrichment.get("emitter_id") or "")
        value = _num((raw.get("_md") or {}).get("VALTODAY_RUR")) or _num((raw.get("_md") or {}).get("VALTODAY"), 0.0) or 0.0
        if emitter_id:
            value_by_emitter[emitter_id] = value_by_emitter.get(emitter_id, 0.0) + value
    sector_candidates = sorted(
        (
            {
                "issuer_inn": str(emitter.get("INN") or "").strip(),
                "issuer_name": emitter.get("SHORT_TITLE") or emitter.get("TITLE"),
                "value_today_rub": value_by_emitter.get(emitter_id, 0.0),
            }
            for emitter_id, emitter in emitters.items()
        ),
        key=lambda item: (-float(item["value_today_rub"]), str(item["issuer_inn"])),
    )
    mapped_selected = 0
    eligible_selected = 0
    for raw in corp[:max_corp]:
        enrichment = enrichments.get(str(raw.get("SECID"))) or {}
        issuer = emitters.get(str(enrichment.get("emitter_id") or "")) or {}
        inn = str(issuer.get("INN") or "").strip()
        if not inn:
            continue
        eligible_selected += 1
        if inn in (issuer_master.get("issuers") or {}):
            mapped_selected += 1
    estimated_sector_coverage = mapped_selected / eligible_selected if eligible_selected else 0.0
    sector_gate = float(config["quality_gate"]["minimum_sector_coverage"])
    if estimated_sector_coverage + 1e-12 >= sector_gate:
        sector_enrichment_status = {
            "status": "skipped",
            "reason": "existing_exact_inn_mapping_meets_quality_gate",
            "estimated_issue_coverage": estimated_sector_coverage,
            "requested": 0,
            "mapped": 0,
            "unmapped": 0,
            "errors": [],
            "resolved": [],
        }
    else:
        enrichment_kwargs = {
            "lookup": fns_lookup,
            "limit": int(universe_cfg.get("sector_enrichment_limit", 30)),
            "request_interval_seconds": float(universe_cfg.get("sector_enrichment_interval_seconds", 1.0)),
        }
        if sector_sleep is not None:
            enrichment_kwargs["sleep"] = sector_sleep
        issuer_master, sector_enrichment_status = enrich_issuer_master(
            issuer_master, sector_candidates, **enrichment_kwargs
        )

    bonds: list[dict] = []
    for raw in sorted(selected, key=lambda item: str(item.get("SECID"))):
        enrichment = enrichments.get(str(raw.get("SECID")))
        if not enrichment:
            continue
        emitter_id = str(enrichment.get("emitter_id") or "")
        row = normalize_bond(
            raw=raw,
            rating_record=ratings.get(str(raw.get("ISIN") or raw.get("SECID") or "")),
            issuer=emitters.get(emitter_id),
            enrichment=enrichment,
            gcurve_rate=gcurve_rate,
            config=config,
            issuer_master=issuer_master,
            as_of=as_of,
        )
        if row:
            bonds.append(row)
    attach_peer_benchmarks(bonds, config)

    unknown_by_issuer: dict[str, dict] = {}
    for row in bonds:
        if row.get("instrument_type") != "corp" or row.get("sector") != "unknown":
            continue
        issuer_id = str(row["issuer_id"])
        item = unknown_by_issuer.setdefault(issuer_id, {
            "issuer_id": issuer_id,
            "issuer_inn": row.get("issuer_inn"),
            "issuer_name": row.get("issuer_name"),
            "issues": 0,
            "value_today_rub": 0.0,
        })
        item["issues"] += 1
        item["value_today_rub"] += float(row.get("value_today_rub") or 0.0)
    unknown_issuers = sorted(
        unknown_by_issuer.values(), key=lambda item: (-item["value_today_rub"], item["issuer_id"])
    )
    for item in unknown_issuers:
        item["value_today_rub"] = round(item["value_today_rub"], 2)

    price_dates = [row["source_dates"]["price"] for row in bonds if row["source_dates"].get("price")]
    history_dates = [row["source_dates"]["history"] for row in bonds if row["source_dates"].get("history")]
    checked_at = ratings_meta.get("checked_at")
    return {
        "schema_version": "3.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_sha": os.environ.get("GITHUB_SHA", "local")[:40],
        "as_of": {
            "prices": max(price_dates) if price_dates else None,
            "curve": as_of.isoformat(),
            "ratings": str(checked_at)[:10] if checked_at else None,
            "history": max(history_dates) if history_dates else None,
        },
        "source_status": {
            "moex": {"status": "ok", "corporate_raw": len(raw_corp), "ofz_raw": len(raw_ofz)},
            "ratings": ratings_meta,
            "enrichment": {"requested": len(selected), "completed": len(enrichments), "errors": errors[:50]},
            "sector_mapping": {
                "fns_enrichment": sector_enrichment_status,
                "unknown_issuers_count": len(unknown_issuers),
                "unknown_issuers": unknown_issuers,
            },
        },
        "bonds": bonds,
    }
