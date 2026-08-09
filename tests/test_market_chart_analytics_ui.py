"""Presentation contracts for the market chart analytics dialog.

The financial calculations live in the existing market/FUTOI/profile pipelines. These
tests intentionally protect the UI hierarchy and make sure presentation code does not
silently replace source values with a second calculation layer.
"""

from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"
INDEX = ROOT / "site" / "index.html"
STYLES = ROOT / "site" / "styles.css"


def _app():
    return APP.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    next_function = source.find("\nfunction ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


def test_dialog_orders_insight_before_technical_context_and_methodology():
    html = INDEX.read_text(encoding="utf-8")
    dialog = html[html.index('id="market-chart-dialog"') :]

    plot = dialog.index('id="market-chart-canvas"')
    positioning = dialog.index('id="market-pos-note"')
    technical = dialog.index('id="market-chart-levels"')
    profile_status = dialog.index('id="market-pf-note"')

    assert plot < positioning < technical < profile_status


def test_positioning_is_enabled_by_default_for_the_immediate_verdict():
    html = INDEX.read_text(encoding="utf-8")
    assert '<input type="checkbox" value="fizpos" checked>' in html


def test_positioning_uses_contracts_as_primary_kpi_and_notional_only_in_details():
    source = _app()
    insight = _function(source, "marketPositioningInsightHTML")
    details = _function(source, "marketPositioningDetailsHTML")

    assert "marketContracts(f.net)" in insight
    assert "Нетто-позиция" in insight
    assert "net_rub" not in insight
    assert "net_rub" in details and "Расчётный notional" in details
    assert "Не является денежным потоком" in details


def test_position_details_are_closed_by_default_and_named_clearly():
    details = _function(_app(), "marketPositioningDetailsHTML")

    assert '<details class="mpi-details">' in details
    assert '<details class="mpi-details" open>' not in details
    assert "Подробнее о позициях" in details
    for label in ("Длинные", "Короткие", "Чистая позиция", "Участников"):
        assert label in details


def test_positioning_percentile_rules_are_deterministic():
    strength = _function(_app(), "marketPositioningStrength")

    assert "percentile < 20 || percentile > 80" in strength
    assert "percentile < 40 || percentile > 60" in strength
    assert "Обычный уровень" in strength


def test_direction_and_activity_are_separate():
    source = _app()
    direction = _function(source, "marketPositioningDirection")
    activity = _function(source, "marketPositioningActivity")

    assert "net > 0" in direction and "Нетто-лонг" in direction
    assert "net < 0" in direction and "Нетто-шорт" in direction
    assert "two_sided_expansion" in activity
    assert "рост общей активности, а не однозначный направленный сигнал" in activity


def test_commentary_replaces_fallback_instead_of_duplicating_it():
    draw = _function(_app(), "marketDrawPositions")

    assert "box.innerHTML = marketPositioningInsightHTML" in draw
    assert "insertAdjacentHTML" not in draw


def test_instrument_without_futoi_hides_positioning_block():
    source = _app()
    draw = _function(source, "marketDrawPositions")
    render = _function(source, "renderMarketChartDialog")
    unsupported = draw[draw.index("if (item.id !== 'IMOEX')") : draw.index("loadFutoi")]

    assert "marketPositionsSay('')" in unsupported
    assert "такого ряда нет" not in unsupported
    assert "positionToggle.hidden = item.id !== 'IMOEX'" in render


def test_technical_cards_have_interpretive_labels_without_trade_signals():
    source = _app()
    trend = _function(source, "marketTrendSummary")
    momentum = _function(source, "marketMomentumSummary")
    analytics = _function(source, "marketTechnicalAnalyticsHTML")

    assert "s.last > s.sma200 && s.sma20 > s.sma50" in trend
    assert "s.last < s.sma200 && s.sma20 < s.sma50" in trend
    assert "rsi >= 70" in momentum and "rsi <= 30" in momentum
    for label in ("Тренд", "Momentum", "Волатильность"):
        assert label in analytics
    assert "BUY" not in analytics and "SELL" not in analytics


def test_range_position_is_derived_from_existing_current_low_high_and_clamped():
    range_html = _function(_app(), "marketRangeHTML")

    assert "(current - low) / (high - low)" in range_html
    assert "Math.max(0, Math.min(100, raw))" in range_html
    assert "market-range-scale" in range_html


def test_profile_levels_are_named_without_support_resistance_claims():
    analytics = _function(_app(), "marketTechnicalAnalyticsHTML")

    for label in ("POC", "VAL", "VAH", "20д low", "20д high"):
        assert label in analytics
    assert "поддерж" not in analytics.lower()
    assert "сопротив" not in analytics.lower()


def test_missing_metrics_never_render_nan():
    trend = _function(_app(), "marketTrendSummary")
    momentum = _function(_app(), "marketMomentumSummary")
    range_html = _function(_app(), "marketRangeHTML")

    assert "Нет данных" in trend
    assert "Нет данных" in momentum
    assert "every(isNum)" in range_html


def test_mobile_layout_stacks_kpis_and_concept_cards_without_page_overflow():
    css = STYLES.read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 640px)", css.index(".market-pos-note:empty")) :]

    assert ".mpi-kpis { grid-template-columns: 1fr; }" in mobile
    assert ".market-concepts { grid-template-columns: 1fr; }" in css
    assert ".mpi-table-wrap { max-width: 780px; overflow-x: auto;" in css


def test_source_financial_series_and_indicator_algorithms_are_not_reimplemented():
    source = _app()
    analytics = _function(source, "marketTechnicalAnalyticsHTML")
    positioning = _function(source, "marketPositioningFacts")

    assert "item.summary" in analytics
    assert "row || {}).summary" in positioning
    assert "scProfileCompute" not in analytics
    assert "sma(" not in analytics.lower()
    assert "rsi(" not in analytics.lower()
