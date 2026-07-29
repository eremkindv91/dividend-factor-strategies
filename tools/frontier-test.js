// Тесты математики границы эффективности. Функции чистые, но живут в app.js
// (single-file фронт), поэтому гоняем их в реальной странице через page.evaluate —
// тем же Playwright-харнессом, что и регрессия. Отдельный test framework не вводим.
//
// Запуск: node tools/frontier-test.js
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { BASE, REPO, startServer, stopServer } = require('./lib');

const FIXTURE = require(path.join(REPO, 'tests', 'fixtures', 'ledoit_wolf.json'));

// Допуск parity с sklearn. Разница берётся только из порядка суммирования float64
// плюс округления самой фикстуры (12 знаков) — то есть ~1e-12 неизбежно. Порог 1e-10
// на 8 порядков ниже любой экономически различимой величины, но всё ещё ловит опечатку
// в формуле: неверный множитель сдвинул бы значения на проценты, а не на 1e-12.
const TOL_SHRINK = 1e-9;
const TOL_COV = 1e-10;

let passed = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); }
  else { failures.push({ name, detail }); console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`); }
}

(async () => {
  const server = await startServer();
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof efLedoitWolf === 'function', { timeout: 20000 });

  // ── CSS-токены ────────────────────────────────────────────────────────────
  // Баг, который ловим: --act-ink объявлена ЛОКАЛЬНО внутри .mp-action-*, а не в
  // :root. Ссылка на неё из .ef-frontier молча давала stroke:none — линия границы
  // просто не рисовалась, при полностью корректной геометрии пути. Ни один
  // математический тест такое не поймает, поэтому проверяем разрешимость токенов.
  console.log('\n── CSS-токены модуля разрешаются глобально ──');
  {
    const css = fs.readFileSync(path.join(REPO, 'site', 'styles.css'), 'utf8');
    const root = new Set([...css.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gmi)].map((m) => m[1]));
    // блоков :root в файле НЕСКОЛЬКО (базовая палитра + добавленная редизайном),
    // поэтому собираем токены из всех, а не только из первого
    const rootBlocks = [...css.matchAll(/:root\s*\{[\s\S]*?\}/g)].map((m) => m[0]).join('\n');
    const globals = new Set([...rootBlocks.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));
    const efRules = [...css.matchAll(/^\.ef-[^{]*\{([^}]*)\}/gm)].map((m) => m[1]).join(' ');
    const used = [...new Set([...efRules.matchAll(/var\((--[a-z0-9-]+)/gi)].map((m) => m[1]))];
    const missing = used.filter((t) => !globals.has(t));
    check(`все ${used.length} токенов .ef-* объявлены в :root`, missing.length === 0,
      missing.length ? `не глобальные: ${missing.join(', ')} (объявлены ли вообще: ${missing.map((t) => root.has(t)).join('/')})` : '');
  }

  console.log('\n── Ledoit–Wolf: parity с sklearn.covariance.LedoitWolf ──');
  for (const c of FIXTURE.cases) {
    const got = await page.evaluate((returns) => {
      const r = efLedoitWolf(returns);
      return { shrinkage: r.shrinkage, cov: r.cov };
    }, c.returns);

    const dS = Math.abs(got.shrinkage - c.expected_shrinkage);
    check(`${c.name}: shrinkage ${got.shrinkage.toFixed(9)} vs ${c.expected_shrinkage.toFixed(9)}`,
      dS <= TOL_SHRINK, `Δ=${dS.toExponential(2)} > ${TOL_SHRINK}`);

    let maxD = 0;
    for (let i = 0; i < c.expected_covariance.length; i++) {
      for (let j = 0; j < c.expected_covariance.length; j++) {
        maxD = Math.max(maxD, Math.abs(got.cov[i][j] - c.expected_covariance[i][j]));
      }
    }
    check(`${c.name}: матрица ковариации (max|Δ|=${maxD.toExponential(2)})`, maxD <= TOL_COV);

    // симметрия и PSD — свойства, которые обязаны выполняться независимо от sklearn
    const props = await page.evaluate((returns) => {
      const { cov } = efLedoitWolf(returns);
      const N = cov.length;
      let asym = 0;
      for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) asym = Math.max(asym, Math.abs(cov[i][j] - cov[j][i]));
      // PSD-проба: xᵀΣx ≥ 0 на детерминированном наборе векторов
      let minQ = Infinity;
      for (let s = 0; s < 60; s++) {
        const x = Array.from({ length: N }, (_, i) => Math.sin(s * 1.7 + i * 0.9));
        let q = 0;
        for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) q += x[i] * cov[i][j] * x[j];
        minQ = Math.min(minQ, q);
      }
      return { asym, minQ };
    }, c.returns);
    check(`${c.name}: симметрична`, props.asym < 1e-15);
    check(`${c.name}: PSD (min xᵀΣx = ${props.minQ.toExponential(2)})`, props.minQ >= -1e-12);
  }

  console.log('\n── James–Stein: сжатие средних ──');
  const js = await page.evaluate(() => {
    // одна бумага с аномально удачной выборкой — сжатие обязано её притянуть
    const T = 60, N = 5;
    const X = [];
    for (let t = 0; t < T; t++) {
      const row = [];
      for (let i = 0; i < N; i++) row.push(Math.sin(t * 0.7 + i) * 0.05 + (i === 0 ? 0.04 : 0.001));
      X.push(row);
    }
    const r = efJamesStein(X);
    return { lambda: r.lambda, anchor: r.anchor, mu: r.mu, sample: r.sample };
  });
  check(`λ в [0,1] (λ=${js.lambda.toFixed(4)})`, js.lambda >= 0 && js.lambda <= 1);
  check('выброс стянут к якорю', Math.abs(js.mu[0] - js.anchor) < Math.abs(js.sample[0] - js.anchor));
  check('порядок бумаг сохранён', js.mu[0] === Math.max(...js.mu));
  const meanPreserved = await page.evaluate(() => {
    const T = 60, N = 5, X = [];
    for (let t = 0; t < T; t++) { const row = []; for (let i = 0; i < N; i++) row.push(Math.sin(t * 0.7 + i) * 0.05 + (i === 0 ? 0.04 : 0.001)); X.push(row); }
    const r = efJamesStein(X);
    const a = r.mu.reduce((x, y) => x + y, 0) / N, b = r.sample.reduce((x, y) => x + y, 0) / N;
    return Math.abs(a - b);
  });
  check(`среднее по бумагам не сдвинулось (Δ=${meanPreserved.toExponential(2)})`, meanPreserved < 1e-12);

  console.log('\n── Проекция на {Σw=1, lo≤w≤hi} ──');
  const proj = await page.evaluate(() => {
    const out = [];
    const cases = [
      // потолок обязан быть достижим: hi·N ≥ 1, иначе множество пусто (проверяется ниже)
      { v: [0.9, 0.05, 0.05, 0.0], lo: 0, hi: 0.4 },
      { v: [-2, 5, 0.1, 0.3], lo: 0, hi: 1 },
      { v: [0.25, 0.25, 0.25, 0.25], lo: 0.1, hi: 0.4 },
    ];
    for (const c of cases) {
      const w = efProjectCapped(c.v, c.lo, c.hi);
      out.push({ sum: w.reduce((a, b) => a + b, 0), min: Math.min(...w), max: Math.max(...w), lo: c.lo, hi: c.hi });
    }
    out.push({ infeasible: efProjectCapped([0.5, 0.5], 0, 0.2) === null });
    return out;
  });
  proj.slice(0, 3).forEach((p, i) => {
    check(`случай ${i + 1}: Σw=1 (${p.sum.toFixed(12)})`, Math.abs(p.sum - 1) < 1e-9);
    check(`случай ${i + 1}: границы соблюдены`, p.min >= p.lo - 1e-9 && p.max <= p.hi + 1e-9);
  });
  check('пустое множество распознаётся (hi·N < 1)', proj[3].infeasible === true);

  console.log('\n── Граница эффективности ──');
  const fr = await page.evaluate(() => {
    const T = 91, N = 6, X = [];
    for (let t = 0; t < T; t++) {
      const row = [];
      for (let i = 0; i < N; i++) row.push(Math.sin(t * 0.37 + i * 1.3) * (0.03 + i * 0.01) + 0.004 + i * 0.0015);
      X.push(row);
    }
    const { cov } = efLedoitWolf(X);
    const { mu } = efJamesStein(X);
    const f = efFrontier(mu, cov, 0, 0.35, 0.16);
    return {
      ok: f.ok, n: f.points.length,
      vols: f.points.map((p) => p.vol), rets: f.points.map((p) => p.ret),
      gmvVol: f.gmv.vol, tangSharpe: f.tangency ? f.tangency.sharpe : null,
      sums: f.points.map((p) => p.w.reduce((a, b) => a + b, 0)),
      mins: f.points.map((p) => Math.min(...p.w)), maxs: f.points.map((p) => Math.max(...p.w)),
      sharpes: f.points.map((p) => p.sharpe),
    };
  });
  check(`граница построена (${fr.n} точек)`, fr.ok && fr.n >= 2);
  check('доходность монотонно растёт с риском', fr.rets.every((r, i) => i === 0 || r > fr.rets[i - 1]));
  check('волатильность монотонно растёт', fr.vols.every((v, i) => i === 0 || v >= fr.vols[i - 1] - 1e-12));
  check('GMV — точка минимального риска', Math.abs(fr.gmvVol - Math.min(...fr.vols)) < 1e-9);
  check('во всех точках Σw=1', fr.sums.every((s) => Math.abs(s - 1) < 1e-6));
  check('во всех точках соблюдён потолок веса', fr.maxs.every((m) => m <= 0.35 + 1e-6));
  check('нет отрицательных весов (long-only)', fr.mins.every((m) => m >= -1e-9));
  check('Max Sharpe выбран как максимум по границе',
    fr.tangSharpe != null && Math.abs(fr.tangSharpe - Math.max(...fr.sharpes.filter((s) => s != null))) < 1e-12);
  check('нет NaN/Infinity', [...fr.vols, ...fr.rets].every(Number.isFinite));

  console.log('\n── Детерминированность ──');
  const det = await page.evaluate(() => {
    const T = 60, N = 5, X = [];
    for (let t = 0; t < T; t++) { const row = []; for (let i = 0; i < N; i++) row.push(Math.cos(t * 0.5 + i) * 0.04 + 0.003); X.push(row); }
    const run = () => {
      const { cov } = efLedoitWolf(X); const { mu } = efJamesStein(X);
      return JSON.stringify(efFrontier(mu, cov, 0, 0.4, 0.16).points.map((p) => [p.vol, p.ret]));
    };
    return run() === run();
  });
  check('повторный расчёт даёт идентичный результат', det === true);

  check('нет новых ошибок в консоли', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '));

  await browser.close();
  await stopServer(server);

  console.log(`\n[frontier-test] пройдено ${passed}, провалено ${failures.length}`);
  if (failures.length) {
    console.log('\nПРОВАЛЫ:');
    failures.forEach((f) => console.log(`  · ${f.name}${f.detail ? ` — ${f.detail}` : ''}`));
    process.exit(1);
  }
  process.exit(0);
})().catch((e) => { console.error('[frontier-test] сбой:', e); process.exit(2); });
