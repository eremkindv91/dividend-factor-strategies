"""Source and mathematical compatibility checks for the automatic-data release."""
import ast
import runpy
from typing import List, Tuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_optional_sdk_cannot_block_core_updates():
    text = (ROOT / '.github/workflows/update.yml').read_text()
    assert 'optional enrichment skipped' in text
    assert '--trusted-host' not in text


def test_safe_features_accepts_pandas_string_dtype_on_real_panel():
    import pandas as pd
    import numpy as np
    # Load the actual selector without importing optional binary ML libraries.
    module = ast.parse((ROOT / 'divmodel/pipeline.py').read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'safe_features')
    config = runpy.run_path(str(ROOT / 'divmodel/config.py'))
    namespace = {'pd': pd, 'np': np, 'List': List, 'Tuple': Tuple,
                 'EXCLUDE_ALWAYS': config['EXCLUDE_ALWAYS'], 'EXCLUDE_PREFIXES': config['EXCLUDE_PREFIXES']}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'safe_features', 'exec'), namespace)
    safe_features = namespace['safe_features']
    path = ROOT / 'data/panels_final/panel_russia_final.csv'
    panel = pd.read_csv(path, low_memory=False)
    before = safe_features(panel)
    for col in panel.select_dtypes(include=['object']).columns:
        panel[col] = panel[col].astype('string')
    after = safe_features(panel)
    assert before == after


def test_wush_reference_when_snapshot_exists():
    import json
    path = ROOT / 'site/data_foundry.json'
    if not path.exists():
        import pytest
        pytest.skip('Live snapshot is generated separately')
    data = json.loads(path.read_text())
    company = next(c for c in data['companies'] if c['ticker'] == 'WUSH')
    facts = [f for f in company['facts'] if f['metric_code'] == 'fin.revenue'
             and f['period']['key'] == '2024-FY'
             and f['consolidation_scope'] == 'consolidated'
             and f['accounting_basis'] == 'IFRS']
    assert facts
    from decimal import Decimal
    assert all(Decimal(f['value']) == Decimal('14304301000') for f in facts)
    assert all(data['evidence'][f['source_id']]['official_url'] for f in facts)


def test_summary_preserves_real_facts_and_coverage(tmp_path):
    import json
    import importlib.util
    import pytest
    path = ROOT / 'site/data_foundry.json'
    if not path.exists():
        pytest.skip('Accepted live snapshot is generated separately')
    spec = importlib.util.spec_from_file_location('foundry_export', ROOT / 'scripts/build_data_foundry.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    original = json.loads(path.read_text())
    output = tmp_path / 'summary.json'
    summary = exporter.build_summary(original, output)
    assert summary['dataset'] == original['dataset']
    assert summary['meta'] == original['meta']
    assert len(summary['companies']) == len(original['companies'])
    for compact, full in zip(summary['companies'], original['companies']):
        assert compact['fact_count'] == len(full['facts'])
        original_by_id = {fact['id']: fact for fact in full['facts']}
        assert all(all(value == original_by_id[fact['id']].get(key) for key, value in fact.items()) for fact in compact['facts'])
        assert all(fact['source_id'] in summary['evidence'] for fact in compact['facts'])
    assert output.stat().st_size < path.stat().st_size
