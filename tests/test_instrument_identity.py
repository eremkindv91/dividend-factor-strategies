from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
MODULE = SITE / "instrument_identity.js"
MANIFEST = SITE / "assets" / "instruments" / "manifest.json"


def node_identity_snapshot() -> dict:
    script = r"""
const listeners = [];
const doc = {
  documentElement: { dataset: {} },
  addEventListener: (name, fn, capture) => listeners.push({ name, fn, capture }),
  querySelectorAll: () => [],
};
global.document = doc;
const api = require('./site/instrument_identity.js');
const original = Object.freeze({ secid: 'SBERP', name: 'Сбербанк-п', type: 'preferred_equity' });
const before = JSON.stringify(original);
const sber = api.resolve({ secid: 'SBER', name: 'Сбербанк' });
const sberp = api.resolve(original);
const unknownA = api.resolve({ secid: 'ABCD', name: '' });
const unknownB = api.resolve({ secid: 'ABCD', name: '' });
api.mount(doc);
api.mount(doc);
const toggles = [];
const avatar = { classList: { toggle: (name, value) => toggles.push([name, value]) } };
const image = {
  complete: true,
  naturalWidth: 0,
  hidden: false,
  matches: (selector) => selector === '[data-instrument-logo]',
  closest: () => avatar,
};
listeners.find((row) => row.name === 'error').fn({ target: image });
console.log(JSON.stringify({
  sizes: api.SIZE_PX,
  t: api.resolve({ secid: 'TCSG', name: 'Тинькофф' }),
  sber,
  sberp,
  banep: api.resolve({ secid: 'BANEP', type: 'equity' }),
  yndx: api.resolve('YNDX'),
  five: api.resolve('FIVE'),
  eqmx: api.resolve('EQMX'),
  divd: api.resolve('DIVD'),
  imoex: api.resolve('IMOEX'),
  unknownA,
  unknownB,
  bond: api.resolve('RU000A123456'),
  externalLogo: api.resolve({ secid: 'EVIL', logo_path: 'https://tracker.invalid/logo.svg' }),
  decorative: api.avatarHTML({ secid: 'T', name: 'Т-Технологии' }),
  standalone: api.avatarHTML({ secid: 'T', name: 'Т-Технологии', standalone: true }),
  ordinaryIdentity: api.identityHTML({ secid: 'SBER', name: 'Сбербанк' }),
  preferredIdentity: api.identityHTML(original),
  missingName: api.identityHTML({ secid: 'ZZZZ' }),
  colorsA: api.fallbackColors('ABCD'),
  colorsB: api.fallbackColors('ABCD'),
  inputUnchanged: before === JSON.stringify(original),
  listenerCount: listeners.length,
  errorHidden: image.hidden,
  errorToggles: toggles,
}));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def test_canonical_assets_types_and_explicit_lineage():
    snap = node_identity_snapshot()

    assert snap["t"]["secid"] == "T"
    assert snap["t"]["logo_path"] == "assets/instruments/t.svg"
    assert snap["t"]["lineage"]["kind"] == "rename"
    assert snap["sberp"]["asset_secid"] == "SBER"
    assert snap["sberp"]["type"] == "preferred_equity"
    assert snap["banep"]["type"] == "preferred_equity"
    assert snap["banep"]["asset_secid"] == "BANE"
    assert snap["sber"]["type"] == "equity"
    assert "instrument-avatar__type" not in snap["ordinaryIdentity"]
    assert ">АП<" in snap["preferredIdentity"]
    assert snap["yndx"]["secid"] == "YDEX"
    assert snap["yndx"]["lineage"]["kind"] == "redomiciliation_identity"
    assert snap["five"]["secid"] == "X5"
    assert snap["five"]["lineage"]["kind"] == "redomiciliation_identity"
    assert snap["eqmx"]["type"] == "fund"
    assert snap["divd"]["type"] == "fund"
    assert snap["imoex"]["type"] == "index"
    assert snap["bond"]["type"] == "bond"
    assert snap["externalLogo"]["logo_path"] == ""


def test_fallback_is_deterministic_resilient_and_does_not_mutate_input():
    snap = node_identity_snapshot()

    assert snap["unknownA"]["logo_status"] == "fallback"
    assert snap["unknownA"]["fallback_label"] == "AB"
    assert snap["unknownA"] == snap["unknownB"]
    assert snap["colorsA"] == snap["colorsB"]
    assert snap["inputUnchanged"] is True
    assert snap["errorHidden"] is True
    assert ["has-logo", False] in snap["errorToggles"]
    assert snap["listenerCount"] == 2, "повторный mount не должен дублировать обработчики"
    assert "undefined" not in snap["missingName"]


def test_accessibility_and_size_contract():
    snap = node_identity_snapshot()

    assert snap["sizes"] == {"xs": 20, "sm": 24, "md": 32, "lg": 40}
    assert 'alt=""' in snap["decorative"]
    assert 'aria-hidden="true"' in snap["decorative"]
    assert 'alt="Т-Технологии"' in snap["standalone"]
    assert 'aria-label="Т-Технологии"' in snap["standalone"]


def test_manifest_assets_exist_are_small_and_sanitized():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["assets"]

    paths = [row["logo_path"] for row in manifest["assets"]]
    paths.extend(manifest["generic_assets"].values())
    for row in manifest["assets"]:
        assert row["logo_source"]
        assert row["logo_status"] in {"official", "generated"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["updated_at"])

    total_bytes = 0
    for relative in paths:
        path = SITE / relative
        assert path.is_file(), f"missing logo asset: {relative}"
        raw = path.read_text(encoding="utf-8")
        total_bytes += path.stat().st_size
        assert "<script" not in raw.lower()
        assert "javascript:" not in raw.lower()
        assert "<image" not in raw.lower()
        assert "xlink:href" not in raw.lower()
        assert not re.search(r"(?:href|src)=[\"']https?://", raw, re.I)
    assert total_bytes < 20_000
    assert MODULE.stat().st_size < 25_000


def test_single_component_is_used_across_investor_workflows():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    app = (SITE / "app.js").read_text(encoding="utf-8")
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert html.index('src="instrument_identity.js"') < html.index('src="app.js"')
    assert "window.InstrumentIdentity.identityHTML" in app
    assert "window.InstrumentIdentity.avatarHTML" in app
    for marker in (
        "renderTable", "stockDetailSummaryHTML", "renderMlStrategy", "renderPortfolio",
        "pfxWireAutocomplete", "dividendRowsHTML", "evRowHTML", "marketInstrumentCardHTML",
        "stockPriceChartHTML", "cbrBankDeckHTML", "bvalRows", "bondsTableHTML", "newsCardHTML",
    ):
        assert marker in app
    assert app.count("instrumentIdentityHTML(") >= 14
    assert app.count("instrumentAvatarHTML(") >= 8
    assert ".instrument-avatar--xs" in css
    assert ".instrument-avatar--lg" in css
    assert "forced-colors: active" in css
    assert "site/instrument_identity.js" in deploy
    assert "site/assets" in deploy
    assert "site/instrument_identity.js" in workflow
    assert "site/assets" in workflow
