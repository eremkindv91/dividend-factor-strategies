"""Deterministic RU Quality factor calculations and portfolio construction.

This is an independent model. It follows publicly documented quality-factor
principles, but it is not an MSCI or Barra product and does not use proprietary
factor exposures.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil, floor, isfinite
from statistics import fmean, pstdev, stdev
from typing import Any, Iterable, Mapping, Sequence


METHODOLOGY_VERSION = "ru_quality_sector_v2"
LEGACY_METHODOLOGY_VERSION = "ru_quality_legacy_v1"


QUALITY_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "industrial_core": {
        "label": "Корпоративная Quality",
        "required": ("roe",),
        "descriptors": (
            {"key": "roe", "label": "ROE", "inverse": False, "format": "percent", "strength": "высокая рентабельность", "weakness": "рентабельность ниже рынка"},
            {"key": "debt_to_equity", "label": "Debt/Equity ↓", "inverse": True, "format": "multiple", "strength": "контролируемая долговая нагрузка", "weakness": "повышенная долговая нагрузка"},
            {"key": "earnings_variability", "label": "Изменчивость EPS ↓", "inverse": True, "format": "percent", "strength": "стабильная динамика EPS", "weakness": "нестабильная динамика EPS"},
        ),
    },
    "bank_quality": {
        "label": "Банки · ЦБ РФ",
        "required": ("bank_roe", "capital_headroom"),
        "descriptors": (
            {"key": "bank_roe", "label": "ROE банка", "inverse": False, "format": "percent", "strength": "высокий ROE банка", "weakness": "низкий ROE банка"},
            {"key": "capital_headroom", "label": "Запас Н1.0 к порогу", "inverse": False, "format": "percentage_points", "strength": "сильный запас капитала Н1.0", "weakness": "тонкий запас капитала Н1.0"},
            {"key": "bank_profit_variability", "label": "Изменчивость прибыли ↓", "inverse": True, "format": "percent", "strength": "стабильная месячная прибыль", "weakness": "высокая изменчивость месячной прибыли"},
        ),
    },
    "it_quality": {
        "label": "IT Quality",
        "required": ("ebitda_margin",),
        "descriptors": (
            {"key": "ebitda_margin", "label": "EBITDA margin", "inverse": False, "format": "percent", "strength": "высокая EBITDA margin", "weakness": "низкая EBITDA margin"},
            {"key": "fcf_margin", "label": "FCF margin", "inverse": False, "format": "percent", "strength": "сильная FCF margin", "weakness": "слабая или отрицательная FCF margin"},
            {"key": "net_debt_to_ebitda", "label": "Net debt/EBITDA ↓", "inverse": True, "format": "multiple", "strength": "низкая долговая нагрузка или net cash", "weakness": "повышенная долговая нагрузка"},
        ),
    },
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def deterministic_percentile_bounds(
    values: Iterable[float | None], lower: float = 0.05, upper: float = 0.95
) -> tuple[float | None, float | None]:
    """Return order-statistic bounds without version-dependent interpolation."""
    clean = sorted(v for item in values if (v := _number(item)) is not None)
    if not clean:
        return None, None
    n = len(clean)
    lower_idx = max(0, min(n - 1, ceil(lower * n) - 1))
    upper_idx = max(0, min(n - 1, floor(upper * n)))
    return clean[lower_idx], clean[upper_idx]


def winsorize(
    values: Sequence[float | None], lower: float = 0.05, upper: float = 0.95
) -> tuple[list[float | None], tuple[float | None, float | None]]:
    lo, hi = deterministic_percentile_bounds(values, lower, upper)
    if lo is None or hi is None:
        return [None for _ in values], (lo, hi)
    out = []
    for item in values:
        value = _number(item)
        out.append(None if value is None else min(max(value, lo), hi))
    return out, (lo, hi)


def cross_section_zscores(
    values: Sequence[float | None], *, inverse: bool = False
) -> tuple[list[float | None], dict[str, float] | None]:
    clean = [v for item in values if (v := _number(item)) is not None]
    if not clean:
        return [None for _ in values], None
    mean = fmean(clean)
    sigma = pstdev(clean)
    if sigma == 0:
        return [None for _ in values], {"mean": mean, "std": 0.0}
    sign = -1.0 if inverse else 1.0
    out = []
    for item in values:
        value = _number(item)
        out.append(None if value is None else sign * (value - mean) / sigma)
    return out, {"mean": mean, "std": sigma}


def eps_growth(current: float | None, previous: float | None) -> float | None:
    current_value, previous_value = _number(current), _number(previous)
    if current_value is None or previous_value is None or previous_value == 0:
        return None
    raw = (current_value - previous_value) / previous_value
    return raw if previous_value > 0 else -raw


def earnings_variability(eps_history: Sequence[float | None]) -> float | None:
    """Sample std of four comparable EPS growth observations from five EPS."""
    clean = [_number(value) for value in eps_history]
    if len(clean) != 5 or any(value is None for value in clean):
        return None
    growth = [eps_growth(clean[i], clean[i - 1]) for i in range(1, 5)]
    if any(value is None for value in growth):
        return None
    return stdev(growth)  # type: ignore[arg-type]


def piecewise_quality_score(z_score: float | None) -> float | None:
    value = _number(z_score)
    if value is None:
        return None
    return 1.0 + value if value >= 0 else 1.0 / (1.0 - value)


def percentile_rank(values: Sequence[float | None], value: float | None) -> float | None:
    current = _number(value)
    clean = [v for item in values if (v := _number(item)) is not None]
    if current is None or not clean:
        return None
    below = sum(v < current for v in clean)
    equal = sum(v == current for v in clean)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def infer_security_type(ticker: str) -> str:
    return "preferred" if ticker.upper().endswith("P") else "common"


KNOWN_SHARE_CLASSES = {
    "SBERP": "SBER",
    "SNGSP": "SNGS",
    "TATNP": "TATN",
    "RTKMP": "RTKM",
    "BSPBP": "BSPB",
    "BANEP": "BANE",
    "KZOSP": "KZOS",
    "LSNGP": "LSNG",
    "NKNCP": "NKNC",
}


def infer_issuer_id(ticker: str, inn: str | None = None) -> str:
    clean_inn = str(inn or "").strip()
    if clean_inn and clean_inn.lower() not in {"nan", "none"}:
        return f"inn:{clean_inn}"
    normalized = ticker.upper().strip()
    return f"ticker:{KNOWN_SHARE_CLASSES.get(normalized, normalized)}"


def _round(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(float(value), digits)


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def compute_quality_scores(
    input_rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute model-specific descriptors and one comparable sector score.

    Descriptor cross-sections and sector peers are de-duplicated by issuer, so
    ordinary/preferred share classes cannot change an issuer's factor exposure.
    """
    rows = [dict(row) for row in input_rows]
    winsor_limits = config.get("winsorization", [0.05, 0.95])
    lower, upper = float(winsor_limits[0]), float(winsor_limits[1])
    stats: dict[str, Any] = {"descriptors": {}, "models": {}, "warnings": []}

    def issuer_key(row: Mapping[str, Any]) -> str:
        return str(row.get("issuer_id") or row.get("ticker") or "Unknown")

    for row in rows:
        row.setdefault("warnings", [])
        row.setdefault("exclusion_reasons", [])
        model = str(row.get("quality_model") or "industrial_core")
        if model not in QUALITY_MODEL_SPECS:
            _append_unique(row["warnings"], f"unknown_quality_model:{model}")
            model = "industrial_core"
        spec = QUALITY_MODEL_SPECS[model]
        row["quality_model"] = model
        row["quality_model_label"] = spec["label"]
        row["factor_definitions"] = [dict(item) for item in spec["descriptors"]]
        row["winsorized"] = {}
        row["z"] = {}

    models = sorted({str(row["quality_model"]) for row in rows})
    for model in models:
        spec = QUALITY_MODEL_SPECS[model]
        indexes = [index for index, row in enumerate(rows) if row["quality_model"] == model]
        stats["descriptors"][model] = {}
        for descriptor in spec["descriptors"]:
            key, inverse = descriptor["key"], bool(descriptor["inverse"])
            issuer_raw: dict[str, float | None] = {}
            for index in indexes:
                row = rows[index]
                issuer = issuer_key(row)
                value = _number((row.get("raw") or {}).get(key))
                if issuer not in issuer_raw or issuer_raw[issuer] is None:
                    issuer_raw[issuer] = value
                elif value is not None and abs(float(issuer_raw[issuer]) - value) > 1e-12:
                    _append_unique(row["warnings"], f"share_class_descriptor_mismatch:{key}")
            issuer_order = sorted(issuer_raw)
            values = [issuer_raw[issuer] for issuer in issuer_order]
            winsorized, bounds = winsorize(values, lower, upper)
            zscores, z_stats = cross_section_zscores(winsorized, inverse=inverse)
            by_issuer = {
                issuer: (_round(winsorized[pos]), _round(zscores[pos]))
                for pos, issuer in enumerate(issuer_order)
            }
            stats["descriptors"][model][key] = {
                "p05": _round(bounds[0]),
                "p95": _round(bounds[1]),
                "mean": _round((z_stats or {}).get("mean")),
                "std": _round((z_stats or {}).get("std")),
                "n": sum(value is not None for value in values),
            }
            if z_stats and z_stats.get("std") == 0:
                warning = f"zero_cross_section_variance:{model}:{key}"
                stats["warnings"].append(warning)
            for index in indexes:
                row = rows[index]
                win, zscore = by_issuer.get(issuer_key(row), (None, None))
                row["winsorized"][key] = win
                row["z"][key] = zscore
                if z_stats and z_stats.get("std") == 0 and (row.get("raw") or {}).get(key) is not None:
                    _append_unique(row["warnings"], f"zero_cross_section_variance:{key}")

    min_coverage = float(config.get("min_quality_coverage", 0.67))
    for row in rows:
        spec = QUALITY_MODEL_SPECS[str(row["quality_model"])]
        keys = [item["key"] for item in spec["descriptors"]]
        available = [row["z"].get(key) for key in keys if row["z"].get(key) is not None]
        descriptor_count = sum(_number((row.get("raw") or {}).get(key)) is not None for key in keys)
        coverage = round(descriptor_count / len(keys), 2) if keys else 0.0
        row["coverage_ratio"] = coverage
        missing_required = [key for key in spec["required"] if row["z"].get(key) is None]
        can_score = not missing_required and len(available) >= min(2, len(keys)) and coverage >= min_coverage
        if not can_score:
            for key in missing_required:
                _append_unique(row["exclusion_reasons"], f"missing_required_descriptor:{key}")
                if key == "roe":
                    _append_unique(row["exclusion_reasons"], "missing_required_roe")
            if len(available) < min(2, len(keys)):
                _append_unique(row["exclusion_reasons"], "fewer_than_two_core_descriptors")
            row["quality_z_absolute"] = None
            row["quality_score_absolute"] = None
            row["score_eligible"] = False
            continue
        composite = fmean(available)
        row["quality_z_absolute"] = _round(composite)
        row["quality_score_absolute"] = _round(piecewise_quality_score(composite))
        row["score_eligible"] = True

    issuer_absolute: dict[str, float | None] = {}
    for row in rows:
        issuer = issuer_key(row)
        value = row.get("quality_z_absolute")
        if issuer not in issuer_absolute or issuer_absolute[issuer] is None:
            issuer_absolute[issuer] = value
    absolute_values = list(issuer_absolute.values())
    for row in rows:
        row["quality_rank_pct"] = _round(
            percentile_rank(absolute_values, row.get("quality_z_absolute")), 4
        )

    min_peers = int(config.get("min_sector_peers", 5))
    scored_by_issuer: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("quality_z_absolute") is not None:
            scored_by_issuer.setdefault(issuer_key(row), row)
    scored_issuers = list(scored_by_issuer.values())
    sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    super_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_issuers:
        sector_groups[str(row.get("sector") or "Unknown")].append(row)
        super_groups[str(row.get("super_sector") or "Market")].append(row)

    market_values = [float(row["quality_z_absolute"]) for row in scored_issuers]
    for row in rows:
        absolute = _number(row.get("quality_z_absolute"))
        if absolute is None:
            row["quality_z_sector"] = None
            row["quality_score_sector"] = None
            row["sector_rank_pct"] = None
            row["normalization_scope"] = None
            continue
        sector = str(row.get("sector") or "Unknown")
        super_sector = str(row.get("super_sector") or "Market")
        if len(sector_groups[sector]) >= min_peers:
            peers, scope = sector_groups[sector], "sector"
        elif len(super_groups[super_sector]) >= min_peers:
            peers, scope = super_groups[super_sector], "super_sector"
            _append_unique(row["warnings"], "sector_peer_fallback:super_sector")
        else:
            peers, scope = scored_issuers, "market"
            _append_unique(row["warnings"], "sector_peer_fallback:market")
        values = [float(peer["quality_z_absolute"]) for peer in peers]
        sigma = pstdev(values) if len(values) > 1 else 0.0
        if sigma == 0:
            sector_z = None
            _append_unique(row["warnings"], "zero_sector_variance")
        else:
            sector_z = max(-3.0, min(3.0, (absolute - fmean(values)) / sigma))
        row["quality_z_sector"] = _round(sector_z)
        row["quality_score_sector"] = _round(piecewise_quality_score(sector_z))
        row["sector_rank_pct"] = _round(percentile_rank(values, absolute), 4)
        row["normalization_scope"] = scope

    for row in rows:
        investability = row.get("investability") or {}
        confidence = str(row.get("confidence") or "low")
        model_missing = bool(row.get("financial_model_required"))
        row["investable"] = bool(investability.get("eligible"))
        row["eligible"] = bool(
            row.get("score_eligible") and row["investable"] and not model_missing
            and confidence in {"high", "medium"}
        )
        if model_missing:
            row["status"] = "sector_specific_model_required"
        elif row["eligible"]:
            row["status"] = "eligible"
        elif row.get("score_eligible") and row["investable"] and confidence == "low":
            row["status"] = "low_confidence_review"
            _append_unique(row["exclusion_reasons"], "low_confidence_default_exclusion")
        else:
            row["status"] = "excluded"
        row["methodology_version"] = METHODOLOGY_VERSION

    stats["n_universe"] = len(rows)
    stats["n_issuers"] = len({issuer_key(row) for row in rows})
    stats["n_scored"] = sum(bool(row.get("score_eligible")) for row in rows)
    stats["n_scored_issuers"] = len({
        issuer_key(row) for row in rows if row.get("score_eligible")
    })
    stats["n_investable_scored"] = sum(
        bool(row.get("score_eligible") and row.get("investable")) for row in rows
    )
    stats["n_investable_scored_issuers"] = len({
        issuer_key(row) for row in rows
        if row.get("score_eligible") and row.get("investable")
    })
    stats["n_eligible"] = sum(bool(row.get("eligible")) for row in rows)
    stats["n_eligible_issuers"] = len({
        issuer_key(row) for row in rows if row.get("eligible")
    })
    stats["data_coverage"] = round(
        fmean([float(row.get("coverage_ratio") or 0.0) for row in rows]), 4
    ) if rows else 0.0
    issuer_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        issuer_rows.setdefault(issuer_key(row), row)
    stats["issuer_data_coverage"] = round(
        fmean([float(row.get("coverage_ratio") or 0.0) for row in issuer_rows.values()]), 4
    ) if issuer_rows else 0.0
    stats["confidence"] = {
        level: sum(str(row.get("confidence")) == level for row in rows)
        for level in ("high", "medium", "low")
    }
    stats["normalization_scopes"] = {
        scope: sum(row.get("normalization_scope") == scope for row in rows)
        for scope in ("sector", "super_sector", "market")
    }
    for model in models:
        model_rows = [row for row in rows if row["quality_model"] == model]
        stats["models"][model] = {
            "label": QUALITY_MODEL_SPECS[model]["label"],
            "n_universe": len(model_rows),
            "n_issuers": len({issuer_key(row) for row in model_rows}),
            "n_scored": sum(bool(row.get("score_eligible")) for row in model_rows),
            "n_scored_issuers": len({
                issuer_key(row) for row in model_rows if row.get("score_eligible")
            }),
            "n_investable_scored": sum(
                bool(row.get("score_eligible") and row.get("investable")) for row in model_rows
            ),
            "n_investable_scored_issuers": len({
                issuer_key(row) for row in model_rows
                if row.get("score_eligible") and row.get("investable")
            }),
            "n_full_coverage": sum(float(row.get("coverage_ratio") or 0.0) >= 1 for row in model_rows),
            "n_full_coverage_issuers": len({
                issuer_key(row) for row in model_rows
                if float(row.get("coverage_ratio") or 0.0) >= 1
            }),
            "n_eligible_issuers": len({
                issuer_key(row) for row in model_rows if row.get("eligible")
            }),
        }
    stats["market_scored_values"] = len(market_values)
    return rows, stats


def _normalize(weights: list[float]) -> list[float]:
    total = sum(weights)
    if total <= 0:
        raise ValueError("portfolio raw weights sum to zero")
    return [value / total for value in weights]


def _group_sums(items: Sequence[Mapping[str, Any]], weights: Sequence[float], key: str) -> dict[str, float]:
    sums: dict[str, float] = defaultdict(float)
    for item, weight in zip(items, weights):
        sums[str(item.get(key) or "Unknown")] += weight
    return dict(sums)


def enforce_caps(
    items: Sequence[Mapping[str, Any]],
    raw_weights: Sequence[float],
    *,
    max_security_weight: float,
    max_issuer_weight: float,
    max_sector_weight: float,
    tolerance: float = 1e-10,
) -> list[float]:
    """Iteratively redistribute while preserving all final caps."""
    if not items:
        return []
    if len(items) * max_security_weight < 1.0 - tolerance:
        raise ValueError("security cap is infeasible for selected portfolio size")
    weights = _normalize([max(0.0, float(value)) for value in raw_weights])
    priorities = _normalize([max(1e-12, float(value)) for value in raw_weights])

    for _ in range(500):
        changed = False
        for index, value in enumerate(weights):
            if value > max_security_weight + tolerance:
                weights[index] = max_security_weight
                changed = True
        for key, cap in (("issuer_id", max_issuer_weight), ("sector", max_sector_weight)):
            sums = _group_sums(items, weights, key)
            for group, total in sums.items():
                if total > cap + tolerance:
                    scale = cap / total
                    for index, item in enumerate(items):
                        if str(item.get(key) or "Unknown") == group:
                            weights[index] *= scale
                    changed = True

        missing = 1.0 - sum(weights)
        if missing <= tolerance:
            if abs(sum(weights) - 1.0) <= 1e-8:
                break
        issuer_sums = _group_sums(items, weights, "issuer_id")
        sector_sums = _group_sums(items, weights, "sector")
        rooms = []
        for index, item in enumerate(items):
            issuer = str(item.get("issuer_id") or "Unknown")
            sector = str(item.get("sector") or "Unknown")
            room = min(
                max_security_weight - weights[index],
                max_issuer_weight - issuer_sums.get(issuer, 0.0),
                max_sector_weight - sector_sums.get(sector, 0.0),
            )
            rooms.append(max(0.0, room))
        candidates = [index for index, room in enumerate(rooms) if room > tolerance]
        if missing > tolerance and not candidates:
            raise ValueError("portfolio caps are jointly infeasible")
        if missing > tolerance:
            priority_total = sum(priorities[index] for index in candidates)
            allocated = 0.0
            for index in candidates:
                addition = min(rooms[index], missing * priorities[index] / priority_total)
                weights[index] += addition
                allocated += addition
            if allocated <= tolerance:
                raise ValueError("portfolio cap redistribution stalled")
            changed = True
        if not changed:
            break

    if abs(sum(weights) - 1.0) > 1e-8:
        raise ValueError(f"portfolio weights do not sum to one: {sum(weights):.12f}")
    if any(weight > max_security_weight + 1e-8 for weight in weights):
        raise ValueError("security cap violated after redistribution")
    if any(value > max_issuer_weight + 1e-8 for value in _group_sums(items, weights, "issuer_id").values()):
        raise ValueError("issuer cap violated after redistribution")
    if any(value > max_sector_weight + 1e-8 for value in _group_sums(items, weights, "sector").values()):
        raise ValueError("sector cap violated after redistribution")
    return weights


@dataclass(frozen=True)
class PortfolioConfig:
    top_n: int = 15
    sector_neutral: bool = True
    weighting: str = "factor_tilt"
    min_coverage: float = 0.67
    min_adv_rub: float = 10_000_000.0
    min_market_cap_rub: float = 5_000_000_000.0
    max_security_weight: float = 0.15
    max_issuer_weight: float = 0.20
    max_sector_weight: float = 0.40
    capital: float = 1_000_000.0
    allow_low_confidence: bool = False
    one_security_per_issuer: bool = True
    buffer_enabled: bool = False
    previous_constituents: tuple[str, ...] = ()


def _buffered_selection(candidates: Sequence[dict[str, Any]], config: PortfolioConfig) -> list[dict[str, Any]]:
    if not config.buffer_enabled or not config.previous_constituents:
        return list(candidates[: config.top_n])
    add_rank = max(1, floor(config.top_n * 0.8))
    keep_rank = ceil(config.top_n * 1.2)
    previous = set(config.previous_constituents)
    selected = [row for index, row in enumerate(candidates, 1) if index <= add_rank]
    selected_tickers = {row["ticker"] for row in selected}
    for index, row in enumerate(candidates, 1):
        if len(selected) >= config.top_n:
            break
        if index <= keep_rank and row["ticker"] in previous and row["ticker"] not in selected_tickers:
            selected.append(row)
            selected_tickers.add(row["ticker"])
    for row in candidates:
        if len(selected) >= config.top_n:
            break
        if row["ticker"] not in selected_tickers:
            selected.append(row)
            selected_tickers.add(row["ticker"])
    return selected


def build_quality_portfolio(
    rows: Sequence[Mapping[str, Any]], config: PortfolioConfig
) -> dict[str, Any]:
    candidates = []
    for source in rows:
        row = dict(source)
        if not row.get("score_eligible") or not row.get("investable"):
            continue
        if float(row.get("coverage_ratio") or 0.0) < config.min_coverage:
            continue
        if float(row.get("market_cap_rub") or 0.0) < config.min_market_cap_rub:
            continue
        if float(row.get("adv_rub") or 0.0) < config.min_adv_rub:
            continue
        if row.get("financial_model_required"):
            continue
        if row.get("confidence") == "low" and not config.allow_low_confidence:
            continue
        score_key = "quality_score_sector" if config.sector_neutral else "quality_score_absolute"
        rank_key = "sector_rank_pct" if config.sector_neutral else "quality_rank_pct"
        if _number(row.get(score_key)) is None:
            continue
        row["portfolio_score"] = float(row[score_key])
        row["portfolio_rank"] = float(row.get(rank_key) or 0.0)
        candidates.append(row)

    candidates.sort(
        key=lambda row: (
            -row["portfolio_rank"],
            -float(row.get("adv_rub") or 0.0),
            str(row.get("ticker")),
        )
    )
    if config.one_security_per_issuer:
        best_by_issuer: dict[str, dict[str, Any]] = {}
        for row in candidates:
            issuer = str(row.get("issuer_id") or row.get("ticker"))
            current = best_by_issuer.get(issuer)
            if current is None or float(row.get("adv_rub") or 0.0) > float(current.get("adv_rub") or 0.0):
                best_by_issuer[issuer] = row
        candidates = [row for row in candidates if best_by_issuer[str(row.get("issuer_id") or row.get("ticker"))]["ticker"] == row["ticker"]]
        candidates.sort(key=lambda row: (-row["portfolio_rank"], str(row["ticker"])))

    selected = _buffered_selection(candidates, config)
    if not selected:
        return {"items": [], "cash_rub": config.capital, "warnings": ["no_eligible_companies"]}

    raw_weights = []
    for row in selected:
        if config.weighting == "equal":
            raw = 1.0
        elif config.weighting == "score_only":
            raw = row["portfolio_score"]
        elif config.weighting == "inverse_volatility":
            volatility = _number(row.get("volatility"))
            raw = 1.0 / volatility if volatility and volatility > 0 else 0.0
        elif config.weighting == "market_cap":
            raw = float(row.get("market_cap_rub") or 0.0)
        else:
            capitalization = float(
                row.get("free_float_market_cap_rub") or row.get("market_cap_rub") or 0.0
            )
            raw = capitalization * row["portfolio_score"]
        raw_weights.append(raw)

    if config.sector_neutral:
        parent = [row for row in candidates if float(row.get("market_cap_rub") or 0.0) > 0]
        parent_sector = defaultdict(float)
        for row in parent:
            parent_sector[str(row.get("sector") or "Unknown")] += float(row["market_cap_rub"])
        total_parent = sum(parent_sector.values()) or 1.0
        selected_sector = defaultdict(list)
        for index, row in enumerate(selected):
            selected_sector[str(row.get("sector") or "Unknown")].append(index)
        represented_total = sum(parent_sector[sector] for sector in selected_sector) or 1.0
        adjusted = [0.0 for _ in selected]
        for sector, indices in selected_sector.items():
            target = (parent_sector[sector] / represented_total) if total_parent else (1 / len(selected_sector))
            subtotal = sum(raw_weights[index] for index in indices) or len(indices)
            for index in indices:
                base = raw_weights[index] if raw_weights[index] > 0 else 1.0
                adjusted[index] = target * base / subtotal
        raw_weights = adjusted

    try:
        weights = enforce_caps(
            selected,
            raw_weights,
            max_security_weight=config.max_security_weight,
            max_issuer_weight=config.max_issuer_weight,
            max_sector_weight=config.max_sector_weight,
        )
    except ValueError as exc:
        return {
            "items": [],
            "cash_rub": config.capital,
            "warnings": [f"caps_infeasible:{exc}"],
        }
    items = []
    invested = 0.0
    warnings = []
    for row, weight in zip(selected, weights):
        target_rub = config.capital * weight
        price = float(row.get("price") or 0.0)
        lot_size = int(row.get("lot_size") or 1)
        lot_value = price * lot_size
        lots = floor(target_rub / lot_value) if lot_value > 0 else 0
        quantity = lots * lot_size
        actual_rub = quantity * price
        invested += actual_rub
        if row.get("free_float_market_cap_rub") is None and config.weighting == "factor_tilt":
            _append_unique(warnings, "market_cap_fallback")
        items.append(
            {
                "ticker": row["ticker"],
                "issuer_id": row.get("issuer_id"),
                "sector": row.get("sector"),
                "quality_rank_pct": row.get("quality_rank_pct"),
                "sector_rank_pct": row.get("sector_rank_pct"),
                "target_weight": _round(weight),
                "target_rub": round(target_rub, 2),
                "price": price or None,
                "lot_size": lot_size,
                "lots": lots,
                "quantity": quantity,
                "actual_rub": round(actual_rub, 2),
            }
        )
    return {
        "items": items,
        "cash_rub": round(config.capital - invested, 2),
        "target_weight_sum": round(sum(weights), 12),
        "warnings": warnings,
    }
