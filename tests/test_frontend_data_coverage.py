from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_has_data_coverage_block_and_loader():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert 'id="data-coverage"' in html
    assert "site_coverage.json" in js
    assert "renderDataCoverage" in js
    assert ".coverage-grid" in css
    assert "source_status_counts" in js
    assert "Official IFRS" in js
    assert "SmartLab fallback" in js
    assert "Conflict" in js
    assert ".coverage-badge" in css


def test_daily_deploy_builds_and_publishes_coverage_json():
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert "python -m src.pipeline.discover_companies" in workflow
    assert "python -m src.pipeline.fetch_reports --from-year 2024 --to-year 2026" in workflow
    assert "python -m src.pipeline.extract_financials" in workflow
    assert "python -m src.pipeline.validate_financials" in workflow
    assert "python -m src.pipeline.unify_financial_data" in workflow
    assert "python -m src.pipeline.build_site_data" in workflow
    assert "site/site_coverage.json" in workflow
    assert "site/site_financials.json" in workflow


def test_manual_deploy_publishes_additive_site_json_layers():
    deploy = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")

    assert "python3 -m src.pipeline.run_all --skip-ocr" in deploy
    assert "python3 scripts/validate_site_data.py" in deploy
    assert "site/methodology.json" in deploy
    assert "site/marketsaw.json" in deploy
    assert "site/marlamov.json" in deploy
    assert "site/site_coverage.json" in deploy
    assert "site/site_financials.json" in deploy
    assert "site/bonds/*.json" in deploy
    assert "app.js?v=" in deploy
    assert "styles.css?v=" in deploy
