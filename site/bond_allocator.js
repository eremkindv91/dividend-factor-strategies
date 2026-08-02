(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.BondLotAllocator = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const round = (value, digits = 2) => Number(Number(value).toFixed(digits));

  function allocate(target, universe, budgetRub, profile, horizon, costs) {
    const bySecid = Object.fromEntries((universe.bonds || []).map((row) => [row.secid, row]));
    const source = (target.target_positions || []).filter((item) => bySecid[item.secid]);
    const budget = round(budgetRub);
    if (!source.length || !Number.isFinite(budget) || budget <= 0) return failure('invalid_input');
    const costRate = (Number(costs.commission_bps || 0) + Number(costs.slippage_bps || 0)) / 10000;
    const items = source.map((item) => {
      const bond = bySecid[item.secid];
      const lotCost = Number(bond.dirty_price_per_lot_rub) * (1 + costRate);
      return { item, bond, lotCost, targetRub: Number(item.target_weight) * budget };
    });
    if (items.some((row) => !Number.isFinite(row.lotCost) || row.lotCost <= 0)) return failure('invalid_dirty_lot_price');

    const lots = items.map((row) => Math.max(1, Math.floor(row.targetRub / row.lotCost)));
    const total = () => lots.reduce((sum, value, index) => sum + value * items[index].lotCost, 0);
    while (total() > budget + 0.005) {
      let choice = -1;
      let excess = -Infinity;
      lots.forEach((value, index) => {
        if (value <= 1) return;
        const current = value * items[index].lotCost - items[index].targetRub;
        if (current > excess) { excess = current; choice = index; }
      });
      if (choice < 0) return failure('budget_below_minimum_lots');
      lots[choice] -= 1;
    }

    const maxLot = Math.max(...items.map((row) => row.lotCost));
    for (let guard = 0; guard < 200000; guard += 1) {
      const cash = budget - total();
      let best = -1;
      let bestScore = Infinity;
      items.forEach((row, index) => {
        if (row.lotCost > cash + 0.005) return;
        lots[index] += 1;
        const check = validate(items, lots, budget, profile, horizon, maxLot, false);
        lots[index] -= 1;
        if (!check.maximumsOk) return;
        const before = Math.abs(lots[index] * row.lotCost - row.targetRub);
        const after = Math.abs((lots[index] + 1) * row.lotCost - row.targetRub);
        const score = after - before + check.minimumPenalty * budget * 10;
        if (score < bestScore - 1e-9 || (Math.abs(score - bestScore) < 1e-9 && row.item.secid < items[best]?.item.secid)) {
          best = index; bestScore = score;
        }
      });
      if (best < 0 || bestScore > 0.05 * maxLot) break;
      lots[best] += 1;
    }

    const finalCheck = validate(items, lots, budget, profile, horizon, maxLot, true);
    if (!finalCheck.ok) return failure('post_rounding_constraints_failed', finalCheck.reasons);
    let gross = 0;
    let estimatedCosts = 0;
    const positions = items.map((row, index) => {
      const dirtyAmount = lots[index] * Number(row.bond.dirty_price_per_lot_rub);
      const estimatedCost = dirtyAmount * costRate;
      const amount = dirtyAmount + estimatedCost;
      gross += dirtyAmount; estimatedCosts += estimatedCost;
      return {
        ...row.item,
        lots: lots[index],
        dirty_amount_rub: round(dirtyAmount),
        estimated_costs_rub: round(estimatedCost),
        total_amount_rub: round(amount),
        actual_weight: round(amount / budget, 10),
        target_actual_deviation: round(amount / budget - Number(row.item.target_weight), 10),
      };
    });
    const spent = round(gross + estimatedCosts);
    return {
      schema_version: '3.0', status: 'CLIENT_VALIDATED', solver: 'deterministic_constrained_rounding',
      budget_rub: budget, gross_purchase_rub: round(gross), estimated_costs_rub: round(estimatedCosts),
      invested_with_costs_rub: spent, cash_rub: round(budget - spent), cash_weight: round((budget - spent) / budget, 10),
      commission_bps: Number(costs.commission_bps || 0), slippage_bps: Number(costs.slippage_bps || 0), positions,
    };
  }

  function validate(items, lots, budget, profile, horizon, maxLot, requireMinimums) {
    const byIssuer = {}, bySector = {}, byYear = {};
    let invested = 0, duration = 0, ofz = 0, bbb = 0, fresh = 0;
    const reasons = [];
    items.forEach((row, index) => {
      const amount = lots[index] * row.lotCost;
      const weight = amount / budget;
      invested += amount; duration += amount * Number(row.bond.duration_value || 0);
      byIssuer[row.bond.issuer_id] = (byIssuer[row.bond.issuer_id] || 0) + amount;
      const sector = row.bond.sector || 'unknown';
      bySector[sector] = (bySector[sector] || 0) + amount;
      const year = String(row.bond.maturity_date || '').slice(0, 4);
      byYear[year] = (byYear[year] || 0) + amount;
      if (row.bond.instrument_type === 'ofz') ofz += amount;
      if (row.bond.rating_group === 'BBB') bbb += amount;
      if (row.bond.new_placement) fresh += amount;
      const liquidityCap = Number(row.bond.median_volume_20d_rub || 0) * Number(profile.maximum_participation_rate || 0) / budget;
      if (weight > Math.min(Number(profile.max_issue), liquidityCap) + 0.01) reasons.push('issue_or_liquidity_cap');
    });
    Object.entries(byIssuer).forEach(([issuer, amount]) => {
      if (!issuer.startsWith('sovereign:') && amount / budget > Number(profile.max_issuer) + 0.01) reasons.push('issuer_cap');
    });
    Object.entries(bySector).forEach(([sector, amount]) => {
      if (sector === 'Государственные облигации') return;
      const cap = sector === 'unknown' ? profile.max_unknown_sector : profile.max_sector;
      if (amount / budget > Number(cap) + 0.01) reasons.push('sector_cap');
    });
    Object.values(byYear).forEach((amount) => { if (amount / budget > Number(profile.maximum_maturity_year_bucket) + 0.01) reasons.push('maturity_bucket_cap'); });
    if (bbb / budget > Number(profile.max_bbb) + 0.01) reasons.push('bbb_cap');
    if (fresh / budget > Number(profile.maximum_new_issues) + 0.01) reasons.push('new_issue_cap');
    const portfolioDuration = invested ? duration / invested : 0;
    const ofzTolerance = maxLot / budget + 0.001;
    const minimumPenalty = Math.max(0, Number(profile.minimum_ofz) - ofz / budget - ofzTolerance)
      + Math.max(0, Number(horizon.min) - portfolioDuration)
      + Math.max(0, portfolioDuration - Number(horizon.max));
    if (requireMinimums && ofz / budget + ofzTolerance < Number(profile.minimum_ofz)) reasons.push('minimum_ofz');
    if (requireMinimums && (portfolioDuration < Number(horizon.min) - 0.01 || portfolioDuration > Number(horizon.max) + 0.01)) reasons.push('duration_corridor');
    return { ok: reasons.length === 0, maximumsOk: reasons.length === 0, minimumPenalty, reasons: [...new Set(reasons)] };
  }

  function failure(code, reasons = []) {
    return { status: 'INFEASIBLE', reason_codes: [code, ...reasons], positions: [] };
  }

  return Object.freeze({ allocate });
});
