// Разовая проверка блока «Сегодня важные события» после наполнения корпоративных типов.
// node tools/verify-events.js
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { BASE, REPO, startServer, stopServer } = require('./lib');

const FIXTURE = require(path.join(REPO, 'tests', 'fixtures', 'portfolio.json'));

(async () => {
  const srv = await startServer();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  try {
    // Портфель заводим тем же способом, что и харнесс скриншотов: через #mp-input
    await page.goto(`${BASE}/#my-portfolio`, { waitUntil: 'networkidle', timeout: 25000 });
    await page.evaluate((input) => {
      const ta = document.getElementById('mp-input');
      if (ta) { ta.value = input; if (typeof renderMyPortfolio === 'function') renderMyPortfolio(); }
    }, FIXTURE.input);
    await page.waitForTimeout(2000);
    await page.goto(`${BASE}/#market`, { waitUntil: 'networkidle', timeout: 25000 });
    await page.waitForSelector('#events-today .ev-row', { timeout: 20000 });
    await page.waitForTimeout(600);

    const rows = await page.$$eval('#events-today .ev-row', (els) => els.map((el) => ({
      chip: (el.querySelector('.ev-chip') || {}).textContent || '',
      main: (el.querySelector('.ev-row-main') || {}).textContent || '',
      mine: !!el.querySelector('.ev-chip-pf'),
      announced: !!el.querySelector('.ev-chip-warn'),
    })));
    const cats = {};
    rows.forEach((r) => { cats[r.chip.trim()] = (cats[r.chip.trim()] || 0) + 1; });

    console.log(`строк в блоке: ${rows.length}`);
    console.log('категории:', JSON.stringify(cats, null, 0));
    console.log(`с чипом «анонс»: ${rows.filter((r) => r.announced).length}`);
    console.log(`подсвечено «моё»: ${rows.filter((r) => r.mine).length}`);
    console.log('\nпервые строки:');
    rows.slice(0, 8).forEach((r) => console.log(
      `  [${r.chip.trim()}] ${r.main.trim().slice(0, 72)}${r.mine ? '  ← моё' : ''}${r.announced ? '  (анонс)' : ''}`));

    const out = path.join(REPO, 'artifacts', 'verify');
    fs.mkdirSync(out, { recursive: true });
    const block = await page.$('#events-today');
    if (block) await block.screenshot({ path: path.join(out, 'events-today.png') });
    console.log(`\nскриншот: artifacts/verify/events-today.png`);
    console.log(errors.length ? `\nОШИБКИ КОНСОЛИ (${errors.length}):\n  ` + errors.slice(0, 8).join('\n  ')
      : '\nошибок консоли нет');
  } finally {
    await browser.close();
    stopServer(srv);
  }
})();
