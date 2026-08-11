from datetime import datetime, timezone

from src.research.eligibility import evaluate_research_eligibility


NOW = datetime(2026, 8, 11, 9, tzinfo=timezone.utc)


def _row(asof, status="available"):
    return {"asof": asof, "status": status}


def test_domain_aware_freshness_distinguishes_old_from_stale():
    components = {
        "market": _row("2026-08-09"),
        "stocks": _row("2026-08-09"),
        "news": _row("2026-08-11"),
        "bonds": _row("2026-08-10"),
        "ml": _row("2026-08-06"),
        "sectors": _row("2026-08-03"),
        "banks": _row("2026-07-01"),
        "fundamentals": _row(None, "degraded"),
    }
    result = evaluate_research_eligibility(
        components,
        schema_ready=True,
        research_hash="sha256:abc",
        now=NOW,
    )
    assert result["component_eligibility"]["market"]["freshness_class"] == "acceptable"
    assert result["component_eligibility"]["banks"]["freshness_class"] == "acceptable"
    assert result["component_eligibility"]["fundamentals"]["freshness_class"] == "unknown"
    assert result["component_eligibility"]["fundamentals"]["usable_for_current_research"] is True
    assert result["cross_domain_ready"] is True


def test_stale_market_blocks_ai_input_even_when_schema_is_valid():
    result = evaluate_research_eligibility(
        {"market": _row("2026-07-01"), "stocks": _row("2026-08-10")},
        schema_ready=True,
        research_hash="sha256:abc",
        now=NOW,
    )
    assert result["schema_ready"] is True
    assert result["ai_input_ready"] is False
    assert result["cross_domain_ready"] is False


def test_schema_failure_blocks_all_ai_gates():
    result = evaluate_research_eligibility(
        {"market": _row("2026-08-11"), "stocks": _row("2026-08-11")},
        schema_ready=False,
        research_hash="sha256:abc",
        now=NOW,
    )
    assert result["schema_ready"] is False
    assert result["ai_input_ready"] is False
    assert result["cross_domain_ready"] is False
