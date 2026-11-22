const assert = require('node:assert/strict');
const {age, freshness, safeUrl} = require('../site/data_foundry.js');
const now = new Date('2026-11-19T15:00:00Z');
assert.equal(age('2026-09-21', now), 59);
assert.equal(age(null, now), null);
assert.equal(age('2027-01-01', now), null);
assert.equal(freshness({price_asof:'2026-09-21', forecast_asof:'2026-06-19', prices_stale:false}, now).warning, true);
assert.equal(freshness({price_asof:'2026-11-18', forecast_asof:'2026-11-03'}, now).warning, false);
assert.equal(safeUrl('javascript:alert(1)'), null);
assert.equal(safeUrl('https://example.com/report.pdf'), 'https://example.com/report.pdf');
console.log('Freshness boundaries and source URL checks passed');

const actual = require('./fixtures/data_foundry_wush_units.json');
const ui = require('../site/data_foundry.js');
const roe = ui.formatFact(actual.roe);
assert.equal(roe.unit, '%');
assert.match(roe.value, /^30/);
console.log('Real WUSH ROE retains percent scale');
