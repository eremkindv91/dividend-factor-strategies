from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .freshness import age_days


@dataclass(frozen=True)
class FreshnessPolicy:
    fresh_days: int
    acceptable_days: int
    current_descriptive_without_asof: bool = False


# Acceptable windows reuse the existing pipeline cadence. The shorter `fresh`
# windows distinguish current data from still-usable latest available reports.
DOMAIN_POLICIES: dict[str, FreshnessPolicy] = {
    "market": FreshnessPolicy(1, 7),
    "stocks": FreshnessPolicy(1, 7),
    "news": FreshnessPolicy(1, 4),
    "bonds": FreshnessPolicy(2, 10),
    "ml": FreshnessPolicy(7, 14),
    "sectors": FreshnessPolicy(14, 45),
    "banks": FreshnessPolicy(31, 60),
    "fundamentals": FreshnessPolicy(45, 180, current_descriptive_without_asof=True),
}


def classify_component(
    component: str,
    row: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    policy = DOMAIN_POLICIES[component]
    component_age = age_days(row.get("asof"), now)
    available = row.get("status") != "unavailable"
    warnings: list[str] = []

    if component_age is None:
        freshness_class = "unknown"
        current_usable = bool(available and policy.current_descriptive_without_asof)
        cross_domain_usable = current_usable
        warnings.append(f"{component}:source_asof_unknown")
    elif component_age < -0.01:
        freshness_class = "stale"
        current_usable = False
        cross_domain_usable = False
        warnings.append(f"{component}:future_asof")
    elif component_age <= policy.fresh_days:
        freshness_class = "fresh"
        current_usable = available
        cross_domain_usable = available
    elif component_age <= policy.acceptable_days:
        freshness_class = "acceptable"
        current_usable = available
        cross_domain_usable = available
        warnings.append(f"{component}:latest_available_not_fresh")
    else:
        freshness_class = "stale"
        current_usable = False
        cross_domain_usable = False
        warnings.append(f"{component}:stale_for_current_research")

    if component == "fundamentals" and current_usable:
        warnings.append("fundamentals:publication_timestamp_unavailable_point_in_time_partial")
    return {
        "freshness_class": freshness_class,
        "age_days": component_age,
        "usable_for_current_research": current_usable,
        "usable_for_cross_domain_synthesis": cross_domain_usable,
        "warnings": warnings,
    }


def evaluate_research_eligibility(
    components: dict[str, dict[str, Any]],
    *,
    schema_ready: bool,
    research_hash: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    states = {
        component: classify_component(component, row, now=current)
        for component, row in components.items()
        if component in DOMAIN_POLICIES
    }
    market_usable = bool((states.get("market") or {}).get("usable_for_current_research"))
    stock_usable = bool((states.get("stocks") or {}).get("usable_for_current_research"))
    ai_input_ready = bool(schema_ready and research_hash.startswith("sha256:") and market_usable)

    optional_domains = ("fundamentals", "sectors", "ml", "banks", "bonds", "news")
    usable_optional = sum(
        bool((states.get(component) or {}).get("usable_for_cross_domain_synthesis"))
        for component in optional_domains
    )
    cross_domain_ready = bool(ai_input_ready and stock_usable and usable_optional >= 2)
    warnings = sorted(
        {
            warning
            for row in states.values()
            for warning in row.get("warnings", [])
        }
    )
    if not cross_domain_ready:
        warnings.append("cross_domain_requirements_not_met")
    return {
        "schema_ready": schema_ready,
        "ai_input_ready": ai_input_ready,
        "cross_domain_ready": cross_domain_ready,
        "component_eligibility": states,
        "temporal_warnings": sorted(set(warnings)),
        "policy": {
            component: {
                "fresh_days": policy.fresh_days,
                "acceptable_days": policy.acceptable_days,
                "current_descriptive_without_asof": policy.current_descriptive_without_asof,
            }
            for component, policy in DOMAIN_POLICIES.items()
        },
    }
