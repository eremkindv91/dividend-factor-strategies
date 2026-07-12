from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_quality_ui_uses_independent_ru_quality_name_and_not_public_barra_label():
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    assert "RU Quality · секторные модели" in html
    assert "Quality (Barra" not in html
    assert "не являются индексом или продуктом MSCI/Barra" in html
    assert "Sector v2" in html


def test_quality_ui_loads_precomputed_json_and_has_explainability_drawer():
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    assert "fetch('quality.json" in app
    assert "function buildQualityPortfolio(config)" in app
    assert 'id="quality-drawer"' in html
    assert "openQualityDrawer" in app


def test_quality_ui_has_no_hard_coded_quality_backtest_metrics():
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    assert "бэктест ВКР 2012–2025: CAGR 11,8%" not in app
    assert "полный point-in-time backtest пока не рассчитан" in app


def test_quality_empty_state_explains_filter_funnel_and_keeps_preview_explicit():
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    assert 'id="quality-filter-status"' in html
    assert 'id="quality-allow-low"' in html
    assert "2 из 3 своей модели" in html
    assert "Все 3 своей модели" in html
    assert "function qualityCandidateAnalysis(config)" in app
    assert "function qualityEmptyStateHTML(config, analysis)" in app
    assert 'data-quality-action="two-factor-preview"' in app
    assert "стабильность прибыли у банков" in app
    assert "Net debt/EBITDA у IT" in app


def test_quality_ui_renders_sector_specific_factor_contracts():
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    methodology = (ROOT / "site/methodology.json").read_text(encoding="utf-8")
    assert "function qualityFactorDefinitions(row)" in app
    assert "quality_model_label" in app
    assert "Винтажи факторов" in app
    assert 'id="quality-model"' in html
    assert 'data-quality-filter-action="live-preview"' in app
    assert "bank_roe" in app
    assert "fcf_margin" in app
    assert "ru_quality_sector_v2" in methodology
    assert "формы Банка России 102/123/135" in methodology


def test_legacy_alias_is_marked_deprecated_and_new_portfolio_ignores_it():
    valuations = (ROOT / "scripts/build_valuations.py").read_text(encoding="utf-8")
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    assert '"methodology_version": "ru_quality_legacy_v1"' in valuations
    assert '"deprecated": True' in valuations
    quality_function = app[app.index("function qualityScore"):app.index("function eligibleForPortfolio")]
    assert "quality_barra" not in quality_function
