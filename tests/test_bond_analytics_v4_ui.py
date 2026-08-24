from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def test_three_product_modes_keep_legacy_tab_ids_and_new_labels():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert html.count("data-bondlab-tab=") == 3
    for stable_id in ("portfolios", "screener", "analytics"):
        assert f'data-bondlab-tab="{stable_id}"' in html
    for label in ("Надёжный портфель", "Все возможности", "Все выпуски"):
        assert label in html


def test_v4_script_is_loaded_before_monolithic_app():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert html.index('src="bond_analytics_v4.js"') < html.index('src="app.js"')


def test_detail_is_lazy_and_initial_loader_does_not_fetch_every_detail():
    source = (SITE / "app.js").read_text(encoding="utf-8")
    assert "bonds/details/${encodeURIComponent(secid)}.json" in source
    load_block = source[source.index("function loadBonds"):source.index("let BOND_LAB_TAB")]
    assert "bonds/details/" not in load_block


def test_financial_math_is_not_reimplemented_in_browser():
    source = (SITE / "bond_analytics_v4.js").read_text(encoding="utf-8")
    for forbidden in ("brentq", "newton", "calculateYtm", "discountFactor", "solveYield"):
        assert forbidden not in source
    assert "scenario_lab" in source
    assert "duration_convexity_one_year_sensitivity" not in source


def test_mobile_cards_and_no_body_level_overflow_contract():
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    assert ".bav4-cards { display: none; }" in css
    assert "@media (max-width: 760px)" in css
    assert ".bav4-cards { display: grid;" in css
    assert ".bav4-table { display: none;" in css


def test_v4_module_renders_partial_without_nan_or_undefined():
    script = r"""
global.window = {};
require('./site/bond_analytics_v4.js');
const html = window.BondAnalyticsV4.explorerHTML({bonds:[{
  secid:'TEST', name:'Тест', issuer_name:'Эмитент', structure_class:'FLOATER',
  analysis_status:'PARTIAL', clean_price_pct:null, primary_metric:null,
  primary_metric_label:'Discount Margin', rating:null, liquidity_score:null
}]});
if (!html.includes('PARTIAL') || html.includes('NaN') || html.includes('undefined')) process.exit(2);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_public_v4_contracts_and_all_detail_paths_exist():
    universe = json.loads((SITE / "bonds/universe_v4.json").read_text(encoding="utf-8"))
    manifest = json.loads((SITE / "bonds/analytics_manifest.json").read_text(encoding="utf-8"))
    assert universe["schema_version"] == "4.0"
    assert manifest["details_lazy"] is True
    assert manifest["detail_count"] == len(universe["bonds"])
    for row in universe["bonds"]:
        assert (SITE / f"bonds/details/{row['secid']}.json").exists()


def test_k2_values_are_fixture_only_not_production_js():
    source = (SITE / "bond_analytics_v4.js").read_text(encoding="utf-8")
    assert "RU000A1039A8" not in source
    assert "91.18" not in source
