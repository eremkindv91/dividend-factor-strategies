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


def test_daily_deploy_builds_and_publishes_coverage_json():
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert "python -m src.pipeline.run_all --smartlab-only --skip-ocr" in workflow
    assert "python -m src.pipeline.validate_financials" in workflow
    assert "site/site_coverage.json" in workflow
    assert "site/site_financials.json" in workflow
