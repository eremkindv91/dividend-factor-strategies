(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.BondRetail = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const SCHEMA_VERSION = 1;
  const STORAGE_KEY = 'dfs.bondPortfolio.v1';
  const SETTINGS_KEY = 'dfs.bondSettings.v1';
  const RATING_SCALE = Object.freeze({
    'BBB-': 1, BBB: 2, 'BBB+': 3,
    'A-': 4, A: 5, 'A+': 6,
    'AA-': 7, AA: 8, 'AA+': 9, AAA: 10,
  });

  const BOND_SAFETY_CONFIG = Object.freeze({
    minRating: 'A-',
    maxPriceAgeDays: 4,
    minMedianVolume20dRub: 2000000,
    minTradingSessions: 12,
    minLiquidityScore: 45,
    maxSuspiciousYtmGrossPct: 45,
    maxSuspiciousYtmNetPct: 40,
    minCleanPricePct: 1,
    maxCleanPricePct: 250,
    allowedCouponTypes: Object.freeze(['fixed']),
    excludedBondTypes: Object.freeze(['structured', 'perpetual', 'subordinated']),
    requireRetailAccess: true,
    requireValidCashflows: true,
    // Отрасль эмитента — пробел НАШИХ данных (покрытие ~40%), а не свойство выпуска.
    // Блокировать ею нельзя: портфельный движок трактует её так же — не исключает бумагу,
    // а ограничивает совокупную долю неизвестного сектора (max_unknown_sector 10–15%).
    requireKnownSector: false,
    allowAmortizing: false,
    allowPutOffer: false,
    allowCall: false,
    defaultCommissionBps: 5,
    defaultSlippageBps: 10,
    defaultTaxRate: 0.13,
    minPeerObservations: 5,
  });

  const REASON_LABELS = Object.freeze({
    MISSING_RATING: 'Рейтинг выпуска не найден или не подтверждён',
    RATING_BELOW_MINIMUM: 'Рейтинг ниже выбранного минимума',
    QUALIFIED_ONLY: 'Выпуск предназначен для квалифицированных инвесторов',
    STALE_PRICE: 'Цена устарела',
    MISSING_PRICE_DATE: 'Дата цены не подтверждена',
    INVALID_PRICE: 'Цена или номинал некорректны',
    INVALID_CASHFLOWS: 'Недостаточно данных для проверки денежных потоков',
    INVALID_YTM: 'Доходность не рассчитана или расчёт не сошёлся',
    SUSPICIOUS_YTM: 'Расчётная доходность аномальна и требует проверки',
    LOW_LIQUIDITY: 'Ликвидность ниже безопасного порога',
    LIQUIDITY_DATA_MISSING: 'Данных для оценки ликвидности недостаточно',
    COMPLEX_COUPON: 'Купонная структура не поддерживается безопасным режимом',
    PUT_OFFER: 'Есть оферта, которая требует отдельного расчёта',
    CALL_OPTION: 'Есть call-опцион эмитента',
    AMORTIZING: 'Амортизация требует проверки графика номинала',
    MATURITY_PASSED: 'Дата погашения уже прошла',
    UNKNOWN_SECTOR: 'Сектор эмитента не подтверждён',
    DATA_CONFLICT: 'В исходных данных есть конфликт или неполное критичное поле',
    GROUP_DATA_UNAVAILABLE: 'Группа компаний не подтверждена',
    PEER_COMPARISON_WEAK: 'Недостаточно сопоставимых выпусков для relative value',
  });

  const finite = (value) => Number.isFinite(Number(value));
  const number = (value, fallback = null) => finite(value) ? Number(value) : fallback;
  const round = (value, digits = 2) => Number(Number(value).toFixed(digits));
  const isoDay = (value) => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? String(value).slice(0, 10) : null;

  function ratingRank(value) {
    const rating = String(value || '').replace(/\s+/g, '').toUpperCase();
    return Object.prototype.hasOwnProperty.call(RATING_SCALE, rating) ? RATING_SCALE[rating] : null;
  }

  function daysBetween(older, newer) {
    const left = isoDay(older);
    const right = isoDay(newer);
    if (!left || !right) return null;
    const value = (Date.parse(right + 'T00:00:00Z') - Date.parse(left + 'T00:00:00Z')) / 86400000;
    return Number.isFinite(value) ? Math.floor(value) : null;
  }

  function liquidity(row, config = BOND_SAFETY_CONFIG) {
    const median = number(row && row.median_volume_20d_rub);
    const today = number(row && row.value_today_rub);
    const sessions = number(row && row.history_sessions);
    if (median == null && today == null && sessions == null) {
      return { score: null, category: 'insufficient', label: 'данных недостаточно', sufficient: false };
    }
    const floor = Math.max(1, Number(config.minMedianVolume20dRub));
    const volumePart = median == null ? 0 : Math.min(55, 55 * Math.max(0, median) / (floor * 4));
    const historyPart = sessions == null ? 0 : Math.min(30, 30 * Math.max(0, sessions) / 20);
    const activityPart = today != null && today >= Math.max(100000, floor * 0.25) ? 15 : 0;
    const score = round(Math.min(100, volumePart + historyPart + activityPart), 0);
    const category = score >= 75 ? 'high' : score >= 45 ? 'medium' : 'low';
    const labels = { high: 'высокая', medium: 'средняя', low: 'низкая' };
    return {
      score,
      category,
      label: labels[category],
      sufficient: median != null && sessions != null,
      median_volume_20d_rub: median,
      history_sessions: sessions,
      value_today_rub: today,
    };
  }

  function classifyBond(row, options = {}) {
    const config = { ...BOND_SAFETY_CONFIG, ...(options.config || options) };
    const asOf = isoDay(options.asOf || options.currentDate || new Date().toISOString()) || new Date().toISOString().slice(0, 10);
    const reasons = [];
    const warnings = [];
    const add = (code) => { if (!reasons.includes(code)) reasons.push(code); };
    const warn = (code) => { if (!warnings.includes(code)) warnings.push(code); };
    const isOfz = row && row.instrument_type === 'ofz';
    const rank = ratingRank(row && row.rating);
    const minimumRank = ratingRank(config.minRating);

    if (!isOfz && rank == null) add('MISSING_RATING');
    else if (!isOfz && minimumRank != null && rank < minimumRank) add('RATING_BELOW_MINIMUM');
    if (config.requireRetailAccess && row && row.qualified_only === true) add('QUALIFIED_ONLY');

    const priceDate = isoDay(row && row.source_dates && row.source_dates.price);
    const priceAgeDays = daysBetween(priceDate, asOf);
    if (!priceDate) add('MISSING_PRICE_DATE');
    else if (priceAgeDays != null && priceAgeDays > Number(config.maxPriceAgeDays)) add('STALE_PRICE');

    const clean = number(row && row.clean_price_pct);
    const dirty = number(row && row.dirty_price_per_lot_rub);
    const face = number(row && row.face_value_per_bond_rub);
    const lotSize = number(row && row.lot_size);
    if (clean == null || clean < config.minCleanPricePct || clean > config.maxCleanPricePct
      || dirty == null || dirty <= 0 || face == null || face <= 0 || lotSize == null || lotSize <= 0) add('INVALID_PRICE');

    const maturity = isoDay(row && row.maturity_date);
    if (!maturity || number(row && row.duration_value) == null) add('INVALID_CASHFLOWS');
    else if (daysBetween(asOf, maturity) < 0) add('MATURITY_PASSED');

    const gross = number(row && row.ytm_gross_pct);
    const net = number(row && row.ytm_net_est_pct);
    if (gross == null || net == null || gross <= -100 || net <= -100) add('INVALID_YTM');
    else if (gross > config.maxSuspiciousYtmGrossPct || net > config.maxSuspiciousYtmNetPct) add('SUSPICIOUS_YTM');

    const liquidityResult = liquidity(row || {}, config);
    if (!liquidityResult.sufficient) add('LIQUIDITY_DATA_MISSING');
    else if ((liquidityResult.median_volume_20d_rub || 0) < config.minMedianVolume20dRub
      || (liquidityResult.history_sessions || 0) < config.minTradingSessions
      || liquidityResult.score < config.minLiquidityScore) add('LOW_LIQUIDITY');

    if (!config.allowedCouponTypes.includes(String((row && row.coupon_type) || '').toLowerCase())) add('COMPLEX_COUPON');
    if (row && row.has_put_offer && !config.allowPutOffer) add('PUT_OFFER');
    if (row && row.has_call && !config.allowCall) add('CALL_OPTION');
    if (row && row.amortizing && !config.allowAmortizing) add('AMORTIZING');
    if (!row || !row.sector || row.sector === 'unknown') {
      // Не молчим: бумага проходит, но пользователь обязан видеть, что отрасль не подтверждена —
      // проверить отраслевую концентрацию портфеля по ней нельзя.
      if (config.requireKnownSector) add('UNKNOWN_SECTOR'); else warn('UNKNOWN_SECTOR');
    }

    const flags = Array.isArray(row && row.data_quality_flags) ? row.data_quality_flags : [];
    if (flags.some((flag) => /conflict|invalid|missing_(face|price|maturity)|calculation_failed/i.test(flag))) add('DATA_CONFLICT');
    if (!row || !row.ultimate_parent_id) warn('GROUP_DATA_UNAVAILABLE');
    if (!isOfz && number(row && row.peer_n, 0) < config.minPeerObservations) warn('PEER_COMPARISON_WEAK');

    const investable = reasons.length === 0;
    return {
      investable,
      riskLevel: investable ? 'checked' : reasons.includes('SUSPICIOUS_YTM') || reasons.includes('INVALID_YTM') || reasons.includes('INVALID_PRICE') ? 'high' : 'attention',
      reasonCodes: reasons,
      warningCodes: warnings,
      reasonLabels: reasons.map((code) => REASON_LABELS[code] || code),
      warningLabels: warnings.map((code) => REASON_LABELS[code] || code),
      liquidity: liquidityResult,
      priceAgeDays,
      ytmConfirmed: !reasons.includes('INVALID_YTM') && !reasons.includes('SUSPICIOUS_YTM'),
      configVersion: 'retail-safe-v1',
    };
  }

  function classifyUniverse(rows, options = {}) {
    return (rows || []).map((row) => {
      const safety = classifyBond(row, options);
      return {
        ...row,
        bond_safety: safety,
        liquidity_score: safety.liquidity.score,
        liquidity_category: safety.liquidity.category,
      };
    });
  }

  function coverage(rows) {
    const total = Math.max(1, (rows || []).length);
    const ratio = (predicate) => round((rows || []).filter(predicate).length / total * 100, 1);
    return {
      sectors_pct: ratio((row) => row.sector && row.sector !== 'unknown'),
      groups_pct: ratio((row) => Boolean(row.ultimate_parent_id)),
      ratings_pct: ratio((row) => row.instrument_type === 'ofz' || ratingRank(row.rating) != null),
      liquidity_pct: ratio((row) => liquidity(row).sufficient),
    };
  }

  function purchaseBreakdown(row, lots, settings = {}) {
    const lotCount = Math.max(0, Math.trunc(number(lots, 0)));
    const lotSize = Math.max(1, Math.trunc(number(row && row.lot_size, 1)));
    const bonds = lotCount * lotSize;
    const face = number(row && row.face_value_per_bond_rub, 0);
    const cleanPct = number(row && row.clean_price_pct, 0);
    const cleanCost = face * cleanPct / 100 * bonds;
    const accrued = number(row && row.aci_per_bond_rub, 0) * bonds;
    const dirtyCost = cleanCost + accrued;
    const costBps = number(settings.commissionBps, BOND_SAFETY_CONFIG.defaultCommissionBps)
      + number(settings.slippageBps, BOND_SAFETY_CONFIG.defaultSlippageBps);
    const costs = dirtyCost * costBps / 10000;
    return {
      lots: lotCount,
      bonds,
      clean_cost_rub: round(cleanCost),
      accrued_interest_rub: round(accrued),
      commission_and_slippage_rub: round(costs),
      total_rub: round(dirtyCost + costs),
    };
  }

  function detectDelimiter(line) {
    const candidates = ['\t', ';', ','];
    return candidates.sort((a, b) => line.split(b).length - line.split(a).length)[0];
  }

  function parsePortfolioText(text) {
    const lines = String(text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!lines.length) return { positions: [], errors: [] };
    const delimiter = detectDelimiter(lines[0]);
    const split = (line) => line.split(delimiter).map((part) => part.trim().replace(/^"|"$/g, ''));
    const first = split(lines[0]).map((part) => part.toLowerCase());
    const aliases = {
      id: ['secid', 'ticker', 'тикер', 'isin', 'бумага', 'инструмент'],
      quantity: ['quantity', 'qty', 'количество', 'бумаг', 'шт', 'лоты', 'lots'],
      average: ['average_price', 'avg_price', 'цена', 'средняя цена', 'средняя'],
    };
    const indexOf = (names) => first.findIndex((value) => names.includes(value));
    const header = indexOf(aliases.id) >= 0;
    const indexes = header
      ? { id: indexOf(aliases.id), quantity: indexOf(aliases.quantity), average: indexOf(aliases.average) }
      : { id: 0, quantity: 1, average: 2 };
    const positions = [];
    const errors = [];
    lines.slice(header ? 1 : 0).forEach((line, offset) => {
      const parts = split(line);
      const identifier = String(parts[indexes.id] || '').toUpperCase();
      const quantity = Number(String(parts[indexes.quantity] || '').replace(/\s/g, '').replace(',', '.'));
      const averagePrice = indexes.average >= 0 && parts[indexes.average] !== undefined && parts[indexes.average] !== ''
        ? Number(String(parts[indexes.average]).replace(/\s/g, '').replace(',', '.')) : null;
      if (!identifier || !Number.isFinite(quantity) || quantity <= 0 || (averagePrice != null && !Number.isFinite(averagePrice))) {
        errors.push({ line: offset + (header ? 2 : 1), raw: line, code: 'INVALID_ROW' });
        return;
      }
      positions.push({ identifier, quantity_bonds: Math.trunc(quantity), average_price: averagePrice });
    });
    return { positions, errors };
  }

  function resolvePortfolio(positions, universe) {
    const lookup = new Map();
    (universe || []).forEach((row) => {
      [row.secid, row.isin].filter(Boolean).forEach((key) => lookup.set(String(key).toUpperCase(), row));
    });
    const recognized = [];
    const unrecognized = [];
    (positions || []).forEach((position) => {
      const bond = lookup.get(String(position.identifier || position.secid || '').toUpperCase());
      if (!bond) unrecognized.push({ ...position, status: 'UNRECOGNIZED' });
      else recognized.push({ ...position, secid: bond.secid, isin: bond.isin, bond, status: 'RECOGNIZED' });
    });
    return { recognized, unrecognized };
  }

  function savePortfolio(storage, positions) {
    if (!storage || typeof storage.setItem !== 'function') return false;
    storage.setItem(STORAGE_KEY, JSON.stringify({ schema_version: SCHEMA_VERSION, saved_at: new Date().toISOString(), positions: positions || [] }));
    return true;
  }

  function loadPortfolio(storage) {
    if (!storage || typeof storage.getItem !== 'function') return { schema_version: SCHEMA_VERSION, positions: [] };
    try {
      const parsed = JSON.parse(storage.getItem(STORAGE_KEY) || 'null');
      if (!parsed || !Array.isArray(parsed.positions)) return { schema_version: SCHEMA_VERSION, positions: [] };
      return { schema_version: SCHEMA_VERSION, saved_at: parsed.saved_at || null, positions: parsed.positions };
    } catch (error) {
      return { schema_version: SCHEMA_VERSION, positions: [], error: 'MALFORMED_STORAGE' };
    }
  }

  function clearPortfolio(storage) {
    if (storage && typeof storage.removeItem === 'function') storage.removeItem(STORAGE_KEY);
  }

  function saveSettings(storage, settings) {
    if (!storage || typeof storage.setItem !== 'function') return false;
    storage.setItem(SETTINGS_KEY, JSON.stringify({ schema_version: SCHEMA_VERSION, ...settings }));
    return true;
  }

  function loadSettings(storage) {
    const defaults = {
      taxRate: BOND_SAFETY_CONFIG.defaultTaxRate,
      commissionBps: BOND_SAFETY_CONFIG.defaultCommissionBps,
      slippageBps: BOND_SAFETY_CONFIG.defaultSlippageBps,
      minRating: BOND_SAFETY_CONFIG.minRating,
      noTradeBandPct: 0.5,
      minTradeRub: 3000,
      reinvestRatePct: 0,
    };
    if (!storage || typeof storage.getItem !== 'function') return defaults;
    try { return { ...defaults, ...(JSON.parse(storage.getItem(SETTINGS_KEY) || '{}')) }; } catch (error) { return defaults; }
  }

  function reconcile(currentPositions, targetAllocation, universe, settings = {}) {
    const bySecid = Object.fromEntries((universe || []).map((row) => [row.secid, row]));
    const current = {};
    (currentPositions || []).forEach((row) => {
      const secid = row.secid || row.identifier;
      if (bySecid[secid]) current[secid] = (current[secid] || 0) + Math.max(0, Math.trunc(number(row.quantity_bonds, 0)));
    });
    const target = {};
    ((targetAllocation || {}).positions || []).forEach((row) => {
      const bond = bySecid[row.secid];
      if (bond) target[row.secid] = Math.max(0, Math.trunc(number(row.lots, 0))) * Math.max(1, Math.trunc(number(bond.lot_size, 1)));
    });
    const mode = settings.mode || 'full';
    const minTradeRub = Math.max(0, number(settings.minTradeRub, 3000));
    const noTradeBandPct = Math.max(0, number(settings.noTradeBandPct, 0.5));
    const budget = Math.max(1, number((targetAllocation || {}).budget_rub, 1));
    const secids = [...new Set([...Object.keys(current), ...Object.keys(target)])].sort();
    const trades = secids.map((secid) => {
      const bond = bySecid[secid];
      const lotSize = Math.max(1, Math.trunc(number(bond.lot_size, 1)));
      const currentQty = current[secid] || 0;
      const targetQty = target[secid] || 0;
      let lots = Math.round((targetQty - currentQty) / lotSize);
      if (mode === 'new_money' && lots < 0) lots = 0;
      const breakdown = purchaseBreakdown(bond, Math.abs(lots), settings);
      const signedRub = breakdown.total_rub * Math.sign(lots);
      const insideBand = Math.abs(signedRub) < minTradeRub || Math.abs(signedRub) / budget * 100 < noTradeBandPct;
      if (insideBand) lots = 0;
      const finalBreakdown = purchaseBreakdown(bond, Math.abs(lots), settings);
      const action = lots > 0 ? 'BUY' : lots < 0 ? 'SELL' : currentQty > 0 ? 'HOLD' : 'SKIP';
      return {
        secid,
        current_quantity: currentQty,
        target_quantity: targetQty,
        trade_lots: lots,
        trade_quantity: lots * lotSize,
        trade_amount_rub: round(finalBreakdown.total_rub * Math.sign(lots)),
        action,
        reason: insideBand ? 'NO_TRADE_BAND' : action === 'BUY' ? 'BELOW_TARGET' : action === 'SELL' ? 'ABOVE_TARGET' : 'AT_TARGET',
      };
    });
    return {
      trades,
      turnover_rub: round(trades.reduce((sum, row) => sum + Math.abs(row.trade_amount_rub), 0)),
      trade_count: trades.filter((row) => row.trade_lots !== 0).length,
    };
  }

  function cashflowSchedule(positions, universe, settings = {}) {
    const bySecid = Object.fromEntries((universe || []).map((row) => [row.secid, row]));
    const asOf = isoDay(settings.asOf || new Date().toISOString()) || new Date().toISOString().slice(0, 10);
    const end = new Date(Date.parse(asOf + 'T00:00:00Z'));
    end.setUTCFullYear(end.getUTCFullYear() + 1);
    const taxRate = Math.max(0, number(settings.taxRate, BOND_SAFETY_CONFIG.defaultTaxRate));
    const flows = [];
    const warnings = [];
    (positions || []).forEach((position) => {
      const row = bySecid[position.secid || position.identifier];
      if (!row) return;
      const quantity = Math.max(0, Math.trunc(number(position.quantity_bonds, number(position.lots, 0) * number(row.lot_size, 1))));
      (row.cashflows_12m || []).forEach((flow) => {
        const type = flow.flow_type || 'coupon';
        const gross = number(flow.amount_per_bond_rub, 0) * quantity;
        const tax = type === 'coupon' ? gross * taxRate : 0;
        flows.push({ secid: row.secid, date: isoDay(flow.date), type, gross_rub: round(gross), tax_rub: round(tax), net_rub: round(gross - tax) });
      });
      const maturity = isoDay(row.maturity_date);
      if (maturity && Date.parse(maturity) <= end.getTime() && Date.parse(maturity) > Date.parse(asOf) && !row.amortizing) {
        const already = flows.some((flow) => flow.secid === row.secid && flow.date === maturity && flow.type === 'principal');
        if (!already) {
          const principal = number(row.face_value_per_bond_rub, 0) * quantity;
          flows.push({ secid: row.secid, date: maturity, type: 'principal', gross_rub: round(principal), tax_rub: 0, net_rub: round(principal) });
        }
      }
      if (row.amortizing && !(row.cashflows_12m || []).some((flow) => flow.flow_type === 'principal')) warnings.push({ secid: row.secid, code: 'AMORTIZATION_SCHEDULE_UNAVAILABLE' });
    });
    flows.sort((a, b) => String(a.date).localeCompare(String(b.date)) || a.secid.localeCompare(b.secid));
    const months = {};
    flows.forEach((flow) => {
      const month = String(flow.date || '').slice(0, 7);
      if (!months[month]) months[month] = { coupon_gross_rub: 0, principal_rub: 0, net_rub: 0 };
      if (flow.type === 'coupon') months[month].coupon_gross_rub += flow.gross_rub;
      else months[month].principal_rub += flow.gross_rub;
      months[month].net_rub += flow.net_rub;
    });
    Object.values(months).forEach((value) => Object.keys(value).forEach((key) => { value[key] = round(value[key]); }));
    return {
      flows,
      months,
      warnings,
      coupon_gross_rub: round(flows.filter((flow) => flow.type === 'coupon').reduce((sum, flow) => sum + flow.gross_rub, 0)),
      coupon_net_rub: round(flows.filter((flow) => flow.type === 'coupon').reduce((sum, flow) => sum + flow.net_rub, 0)),
      principal_rub: round(flows.filter((flow) => flow.type !== 'coupon').reduce((sum, flow) => sum + flow.gross_rub, 0)),
    };
  }

  function concentration(positions, universe, totalRub = null) {
    const bySecid = Object.fromEntries((universe || []).map((row) => [row.secid, row]));
    const values = (positions || []).map((position) => {
      const row = bySecid[position.secid];
      if (!row) return null;
      const amount = number(position.total_amount_rub, number(position.market_value_rub, 0));
      return { row, amount };
    }).filter(Boolean);
    const total = totalRub || values.reduce((sum, item) => sum + item.amount, 0) || 1;
    const groups = {};
    let unknownGroupRub = 0;
    values.forEach(({ row, amount }) => {
      if (!row.ultimate_parent_id) unknownGroupRub += amount;
      else groups[row.ultimate_parent_id] = (groups[row.ultimate_parent_id] || 0) + amount;
    });
    return {
      groups: Object.entries(groups).map(([id, amount]) => ({ id, amount_rub: round(amount), weight_pct: round(amount / total * 100, 2) })).sort((a, b) => b.amount_rub - a.amount_rub),
      unknown_group_rub: round(unknownGroupRub),
      unknown_group_weight_pct: round(unknownGroupRub / total * 100, 2),
    };
  }

  function stress(positions, settings = {}) {
    const rateBp = number(settings.rateShockBp, 100);
    const spreadBp = number(settings.spreadShockBp, 0);
    let rate = 0;
    let spread = 0;
    (positions || []).forEach((row) => {
      const amount = number(row.dirty_amount_rub, number(row.total_amount_rub, 0));
      const duration = Math.max(0, number(row.duration_value, 0));
      rate -= amount * duration * rateBp / 10000;
      if (row.instrument_type !== 'ofz') spread -= amount * duration * spreadBp / 10000;
    });
    return { rate_impact_rub: round(rate), spread_impact_rub: round(spread), combined_impact_rub: round(rate + spread) };
  }

  function findAlternatives(source, universe, options = {}) {
    if (!source) return [];
    const safe = classifyUniverse(universe || [], options).filter((row) => row.bond_safety.investable && row.secid !== source.secid);
    const sourceRank = ratingRank(source.rating) || 0;
    return safe.map((row) => {
      const score = Math.abs((ratingRank(row.rating) || 0) - sourceRank) * 4
        + Math.abs(number(row.duration_value, 0) - number(source.duration_value, 0)) * 3
        + (row.sector === source.sector ? 0 : 2)
        + (row.coupon_type === source.coupon_type ? 0 : 3)
        - Math.min(5, number(row.liquidity_score, 0) / 20);
      return { ...row, alternative_score: round(score, 3), ytm_net_delta_pp: round(number(row.ytm_net_est_pct, 0) - number(source.ytm_net_est_pct, 0), 2) };
    }).sort((a, b) => a.alternative_score - b.alternative_score || String(a.secid).localeCompare(String(b.secid))).slice(0, options.limit || 5);
  }

  function alerts(positions, universe, options = {}) {
    const bySecid = Object.fromEntries((universe || []).map((row) => [row.secid, row]));
    const result = [];
    (positions || []).forEach((position) => {
      const row = bySecid[position.secid || position.identifier];
      if (!row) {
        result.push({ secid: position.secid || position.identifier, severity: 'attention', code: 'UNRECOGNIZED_POSITION', message: 'Позиция не найдена в свежем universe' });
        return;
      }
      const safety = classifyBond(row, options);
      safety.reasonCodes.forEach((code) => result.push({ secid: row.secid, severity: safety.riskLevel, code, message: REASON_LABELS[code] || code }));
      const days = daysBetween(options.asOf || new Date().toISOString(), row.maturity_date);
      if (days != null && days >= 0 && days <= 90) result.push({ secid: row.secid, severity: 'attention', code: 'MATURITY_APPROACHING', message: `До погашения ${days} дн.` });
    });
    return result;
  }

  return Object.freeze({
    SCHEMA_VERSION, STORAGE_KEY, SETTINGS_KEY, BOND_SAFETY_CONFIG, REASON_LABELS, RATING_SCALE,
    ratingRank, liquidity, classifyBond, classifyUniverse, coverage, purchaseBreakdown,
    parsePortfolioText, resolvePortfolio, savePortfolio, loadPortfolio, clearPortfolio,
    saveSettings, loadSettings, reconcile, cashflowSchedule, concentration, stress,
    findAlternatives, alerts,
  });
});
