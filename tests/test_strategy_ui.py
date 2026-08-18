from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_existing_strategy_constructor_exposes_marlamov_backtest_without_replacing_old_options():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert '<option value="quality">' in html
    assert '<option value="momentum"' in html
    assert '<option value="marlamov">Дивидендная переоценка (Марламов)</option>' in html
    assert '<option value="optmv">' in html
    assert 'id="pf-out"' in html

    assert "marlamovPortfolioCandidates" in app
    assert "expected_net_yield" in app
    assert "expected_net_spread" in app
    assert "MARLAMOV.backtest" in app
    assert "marlamovBacktestHTML" in app
    assert "marlamovEntryGateHTML" in app
    assert "модельный состав по ожидаемой чистой дивдоходности" in app
    assert "не point-in-time" in app
    assert ".pf-backtest-grid" in styles
    assert ".pf-entry-gate" in styles


def test_daily_builder_publishes_generated_backtest_in_existing_marlamov_layer():
    builder = (ROOT / "scripts" / "build_forward_yield.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_site_data.py").read_text(encoding="utf-8")

    assert "build_backtest_from_files" in builder
    assert '"backtest": backtest' in builder
    assert "gross_yield1" in builder
    assert "gross_spread" in builder
    assert "python scripts/build_forward_yield.py" in workflow
    assert "openpyxl" in workflow
    assert 'backtest.get("point_in_time") is not False' in validator


def test_ml_optimizer_tab_renders_validated_server_snapshots_only():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_site_data.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ml_strategy_daily.yml").read_text(encoding="utf-8")

    assert 'data-strategy="ml"' in html
    assert 'id="ml-strategy-panel"' in html
    for name in ("latest.json", "backtest.json", "model_card.json", "data_quality.json"):
        assert f"get('{name}')" in app
    assert "renderMlStrategy" in app
    assert "ML-оптимизатор" in html
    assert ".mls-table" in styles
    assert "check_ml_strategy" in validator
    assert "scripts/build_ml_strategy.py --allow-network" in workflow
    assert "Publish validated snapshot additively" in workflow


def test_ml_daily_computation_is_not_cancelled_by_gh_pages_publication_queue():
    workflow = (ROOT / ".github" / "workflows" / "ml_strategy_daily.yml").read_text(encoding="utf-8")

    assert 'cron: "30 22 * * 1-5"' in workflow
    assert "  inference:\n" in workflow
    assert "  publish:\n" in workflow
    assert "needs: inference" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions/download-artifact@v4" in workflow

    inference, publish = workflow.split("  publish:\n", 1)
    assert "group: gh-pages-publish" not in inference
    assert "group: gh-pages-publish" in publish
    assert "Publish validated snapshot additively" in publish
