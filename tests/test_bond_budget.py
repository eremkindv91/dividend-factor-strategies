"""Budget scenarios run against the checked-in MOEX universe, without invented prices."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_budget_input_allocation_and_cashflows_on_real_universe():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const allocator = require('./site/bond_allocator.js');
const retail = require('./site/bond_retail.js');
const universe = JSON.parse(fs.readFileSync('site/bonds/universe.json'));
const presets = JSON.parse(fs.readFileSync('site/bonds/portfolio_presets.json'));
const limits = presets.budget_limits;
assert.equal(allocator.parseBudget('275 123,45 ₽', limits).value, 275123.45);
assert.equal(allocator.parseBudget('275\u202f000', limits).value, 275000);
assert.equal(allocator.parseBudget('', limits).ok, false);
assert.equal(allocator.parseBudget('1e6', limits).ok, false);
assert.equal(allocator.parseBudget(String(limits.minimum_rub - 1), limits).code, 'below_minimum');
assert.equal(allocator.parseBudget(String(limits.maximum_rub + 1), limits).code, 'above_maximum');
let checked = 0, rejected = 0;
for (const [key, target] of Object.entries(presets.presets)) {
  if (!presets.allocations[key]) continue;
  const [profile, horizon] = key.split(':');
  for (const budget of [250000, 275000, 275123.45, 500000, 1000000, 3000000]) {
    const result = allocator.allocate(target, universe, budget, presets.profiles[profile], presets.horizons[horizon], presets.costs);
    if (result.status !== 'CLIENT_VALIDATED') {
      assert.ok(result.reason_codes.length > 0);
      assert.equal(result.positions.length, 0);
      rejected++;
      continue;
    }
    checked++;
    assert.equal(result.positions.length, target.target_positions.length);
    assert.ok(result.positions.every(p => Number.isInteger(p.lots) && p.lots > 0));
    assert.ok(result.invested_with_costs_rub <= budget + 0.01);
    assert.ok(Math.abs(result.invested_with_costs_rub + result.cash_rub - budget) < 0.011);
    assert.ok(result.cash_rub / budget <= presets.profiles[profile].cash_max + 0.001);
    const byId = new Map(universe.bonds.map(b => [b.secid,b]));
    for (const row of result.positions) {
      const actualCost = row.lots * byId.get(row.secid).dirty_price_per_lot_rub * (1 + (result.commission_bps + result.slippage_bps) / 10000);
      assert.ok(Math.abs(actualCost - row.total_amount_rub) < 0.011);
    }
  }
}
assert.ok(checked > 0);
// A real target must not silently lose issues absent from a mismatched universe.
const key = Object.keys(presets.allocations)[0];
const target = presets.presets[key], [profile,horizon] = key.split(':');
const incomplete = {...universe,bonds:universe.bonds.filter(b => b.secid !== target.target_positions[0].secid)};
assert.ok(allocator.allocate(target,incomplete,1000000,presets.profiles[profile],presets.horizons[horizon],presets.costs).reason_codes.includes('missing_target_issue'));
// Shift the cut-off relative to the published price date: already-paid flows must disappear.
const asOfDate = new Date(universe.as_of.prices + 'T00:00:00Z');
asOfDate.setUTCDate(asOfDate.getUTCDate() + 10);
const asOf = asOfDate.toISOString().slice(0,10);
const endDate = new Date(asOfDate); endDate.setUTCFullYear(endDate.getUTCFullYear()+1);
const schedule = retail.cashflowSchedule(presets.allocations[key].positions,universe.bonds,{asOf,taxRate:0.13});
assert.ok(schedule.flows.every(f => f.date > asOf && Date.parse(f.date) <= endDate.getTime()));
assert.ok(Math.abs(schedule.coupon_net_rub + schedule.principal_rub - schedule.flows.reduce((s,f)=>s+f.net_rub,0)) < 0.02);
const freshness = retail.priceFreshness(universe.bonds,asOf);
assert.equal(freshness.stale,universe.bonds.length);
// Missing metrics in real positions are excluded from the denominator, never replaced by zero.
const realPositions = presets.allocations[key].positions;
const partial = realPositions.map((p,i)=> i === 0 ? {...p,ytm_net_est_pct:null} : p);
const metric = retail.weightedMetric(partial,'ytm_net_est_pct');
const known = partial.filter(p => p.ytm_net_est_pct != null);
const expected = known.reduce((s,p)=>s+p.ytm_net_est_pct*p.actual_weight,0)/known.reduce((s,p)=>s+p.actual_weight,0);
assert.ok(Math.abs(metric.value-expected) < 1e-10);
assert.ok(metric.coverage < 1);
const classified = retail.classifyUniverse(universe.bonds,{asOf:universe.as_of.prices});
const filtered = retail.filterRows(classified,{mode:'risk',filters:{maxYtm:100,maxDuration:10}});
assert.ok(filtered.every(r => r.ytm_net_est_pct != null && r.duration_value != null && r.bond_safety.ytmConfirmed));
console.log(JSON.stringify({checked,rejected,flows:schedule.flows.length}));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)
    assert output["checked"] > 0
