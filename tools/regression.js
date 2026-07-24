// Регрессия расчётного слоя (§7). Извлекает метрики ЧЕРЕЗ page.evaluate из PFX_STATE и VIEW,
// а не из отформатированного DOM (форматирование меняется — значения нет).
//   node tools/regression.js --mode=baseline   # зафиксировать baseline
//   node tools/regression.js --mode=check       # exit 1 при любом расхождении > TOL
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { BASE, REPO, startServer, stopServer, waitPortfolioReady } = require('./lib');

const TOL = 1e-9;
const BASELINE_DIR = path.join(REPO, 'tests', 'baseline');
const FIXTURE = require(path.join(REPO, 'tests', 'fixtures', 'portfolio.json'));
const MODE = (process.argv.find((a) => a.startsWith('--mode=')) || '--mode=check').split('=')[1];

// Плоский снимок конечных чисел явного метрик-объекта (уже без массовых рядов).
function flattenNumbers(obj) {
  const out = {};
  const walk = (node, pfx) => {
    if (node == null) return;
    if (typeof node === 'number') { if (Number.isFinite(node)) out[pfx] = node; return; }
    if (typeof node === 'boolean') { out[pfx] = node ? 1 : 0; return; }
    if (Array.isArray(node)) { node.forEach((v, i) => walk(v, `${pfx}[${i}]`)); return; }
    if (typeof node === 'object') { for (const k of Object.keys(node)) walk(node[k], pfx ? `${pfx}.${k}` : k); }
  };
  walk(obj, '');
  return out;
}

async function extract(page) {
  // 1) Портфель — грузим фикстуру в реальный конвейер UI, читаем глобал PFX_STATE
  await page.goto(`${BASE}/#my-portfolio`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof DATA !== 'undefined' && DATA && DATA.tickers, { timeout: 20000 });
  await page.evaluate((input) => {
    const ta = document.getElementById('mp-input');
    ta.value = input;
    // renderMyPortfolio читает #mp-input и кладёт результат в PFX_STATE
    renderMyPortfolio();
  }, FIXTURE.input);
  await waitPortfolioReady(page);
  // дать асинхронным дозагрузкам (returns/marketsaw/marlamov/bonds) устаканиться и пересчитать
  await page.waitForTimeout(1500);

  // Явный спек-список метрик (§7.2) из расчётного слоя PFX_STATE (месячный конвейер).
  // Извлекаем ровно значения, форматирование не трогаем.
  const portfolio = await page.evaluate(() => {
    const c = PFX_STATE;
    const num = (x) => (typeof x === 'number' && Number.isFinite(x) ? x : null);
    const perf = c.perf || {}, capm = c.capm || {}, vaR = c.vaR || {}, div = c.div || {},
      boot = c.boot || {}, bt = c.backtest || {}, pf = c.pf || {}, rb = c.riskBudget || {};
    const inRisk = new Set(pf.tickers || []);
    const excluded = (c.positions || []).filter((p) => !inRisk.has(p.ticker))
      .map((p) => ({ ticker: p.ticker, reason: (Object.keys(p.returns || {}).length ? 'excluded' : 'no_data') }));
    // концентрация из весов риск-корзины
    const w = (pf.weights || []).slice().sort((a, b) => b - a);
    const hhi = w.reduce((s, x) => s + x * x, 0);
    const largest = w[0] ?? null;
    const top5 = w.slice(0, 5).reduce((s, x) => s + x, 0);
    // component VaR по позициям (riskBudget.rows: ticker, weight, share)
    const componentVar = (rb.rows || []).map((r) => ({ ticker: r.ticker, weight: num(r.weight), share: num(r.share), mrc: num(r.mrc), crc: num(r.crc), pcr: num(r.pcr) }));
    return {
      excluded,
      positions_included: pf.tickers || [],
      pf_covered: num(pf.covered), pf_n: num(pf.n),
      metrics: {
        value_total: num(c.total), cost: num(c.cost),
        pnl_absolute: num(c.total) != null && num(c.cost) != null ? c.total - c.cost : null,
        pnl_percent: num(c.cost) ? (c.total - c.cost) / c.cost : null,
        total_return: num(perf.totalRet), cagr: num(perf.cagr), volatility_ann: num(perf.volAnn),
        max_drawdown: num(perf.mdd), sharpe: num(perf.sharpe), sortino: num(perf.sortino), calmar: num(perf.calmar),
        win_pct: num(perf.winPct), ret1m: num(perf.ret1m), ret3m: num(perf.ret3m), ret6m: num(perf.ret6m), ret1y: num(perf.ret1y), ret3y: num(perf.ret3y),
        beta: num(capm.beta), alpha_ann: num(capm.alphaAnn), r2: num(capm.r2), corr: num(capm.corr),
        tracking_error: num(capm.te), information_ratio: num(capm.ir), treynor: num(capm.treynor),
        t_alpha: num(capm.tAlpha), up_capture: num(capm.upCapture), dn_capture: num(capm.dnCapture),
        var95_hist: num(vaR.hist95), var99_hist: num(vaR.hist99), cvar95: num(vaR.cvar95), cvar99: num(vaR.cvar99),
        var95_normal: num(vaR.gauss95), var99_normal: num(vaR.gauss99), var95_cf: num(vaR.cf95), var99_cf: num(vaR.cf99),
        var_skew: num(vaR.skew), var_kurt: num(vaR.kurt),
        hhi, effN: num(c.effN), largest, top3: num(c.top3), top5, wBeta: num(c.wBeta), grossYield: num(c.grossYield),
        dividend_gross_12m: num(div.baseIncome), dividend_prob_adjusted_12m: num(div.riskAdj), dividend_at_risk: num(div.atRisk), dividend_top_share: num(div.topShare),
        backtest_breaches: num(bt.breaches), backtest_freq: num(bt.freq), backtest_expected: num(bt.expected), backtest_obs: num(bt.obs),
        dq_score: num((c.dq || {}).score), dq_lowWeight: num((c.dq || {}).lowWeight),
      },
      // Нессравниваемое (стохастика): месячный bootstrap НЕ seed-детерминирован (находка §7.3).
      // Фиксируем для справки, но НЕ включаем в regression-check (tol 1e-9 невыполним).
      stochastic: {
        boot_pBeat: num(boot.pBeat), boot_pLowerDD: num(boot.pLowerDD), boot_pLoss: num(boot.pLoss),
        boot_cagr: num(boot.cagr), boot_mdd: num(boot.mdd), boot_sharpe: num(boot.sharpe), boot_sims: num(boot.sims),
      },
      component_var: componentVar,
    };
  });

  // 2) Скринер акций — топ-20 глобала VIEW при дефолтных фильтрах
  await page.goto(`${BASE}/#stocks`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof VIEW !== 'undefined' && Array.isArray(VIEW) && VIEW.length > 0, { timeout: 20000 });
  const screener = await page.evaluate(() => VIEW.slice(0, 20).map((t, i) => ({
    rank: i + 1, ticker: t.ticker,
    verdict_score: typeof t.verdict_score === 'number' ? t.verdict_score : null,
    dividend_yield_expected: typeof t.dividend_yield_expected === 'number' ? t.dividend_yield_expected : null,
  })));

  return {
    portfolio: {
      excluded: portfolio.excluded,
      positions_included: portfolio.positions_included,
      pf_covered: portfolio.pf_covered, pf_n: portfolio.pf_n,
      metrics: flattenNumbers({ ...portfolio.metrics, component_var: portfolio.component_var }),
      stochastic_not_compared: portfolio.stochastic,
    },
    screener_top20: screener,
  };
}

function readBaseline(name) {
  const p = path.join(BASELINE_DIR, name);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
}

function compareNumbers(base, cur) {
  const diffs = [];
  const keys = new Set([...Object.keys(base), ...Object.keys(cur)]);
  for (const k of keys) {
    const a = base[k], b = cur[k];
    if (a === undefined) { diffs.push({ key: k, baseline: '—', current: b, note: 'новая метрика' }); continue; }
    if (b === undefined) { diffs.push({ key: k, baseline: a, current: '—', note: 'исчезла метрика' }); continue; }
    const rel = Math.abs(a) > 1e-12 ? Math.abs((b - a) / a) : Math.abs(b - a);
    if (rel > TOL) diffs.push({ key: k, baseline: a, current: b, delta: b - a, rel });
  }
  return diffs;
}

(async () => {
  fs.mkdirSync(BASELINE_DIR, { recursive: true });
  const srv = await startServer();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    const data = await extract(page);

    if (MODE === 'baseline') {
      fs.writeFileSync(path.join(BASELINE_DIR, 'portfolio.json'), JSON.stringify(data.portfolio, null, 2) + '\n');
      fs.writeFileSync(path.join(BASELINE_DIR, 'screener.json'), JSON.stringify(data.screener_top20, null, 2) + '\n');
      const n = Object.keys(data.portfolio.metrics).length;
      console.log(`[baseline] portfolio: ${n} числовых метрик; исключено ${data.portfolio.excluded.length}; скринер топ-20 зафиксирован`);
      if (errors.length) console.log(`[baseline] ВНИМАНИЕ pageerror: ${errors.length}`);
      process.exit(0);
    }

    // check
    const basePf = readBaseline('portfolio.json');
    const baseScr = readBaseline('screener.json');
    if (!basePf) { console.error('[check] нет baseline — сначала --mode=baseline'); process.exit(2); }
    const pfDiffs = compareNumbers(basePf.metrics, data.portfolio.metrics);
    const scrDiffs = JSON.stringify(baseScr) === JSON.stringify(data.screener_top20) ? [] : [{ key: 'screener_top20', note: 'состав/порядок/значения изменились' }];
    const all = [...pfDiffs, ...scrDiffs];
    if (!all.length) {
      console.log('[check] 0 расхождений — расчётный слой не изменился ✓');
      process.exit(0);
    }
    console.error(`[check] РАСХОЖДЕНИЙ: ${all.length}`);
    console.error('метрика | baseline | current | Δ | rel');
    all.slice(0, 40).forEach((d) => console.error(`${d.key} | ${d.baseline} | ${d.current} | ${d.delta ?? ''} | ${d.rel ?? d.note ?? ''}`));
    process.exit(1);
  } finally {
    await browser.close();
    stopServer(srv);
  }
})().catch((e) => { console.error('[regression] FAIL:', e.message); process.exit(3); });
