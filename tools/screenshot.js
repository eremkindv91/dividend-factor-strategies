// Скриншоты всех разделов во всех вьюпортах (§4.3).
//   node tools/screenshot.js --tag=before        # artifacts/screenshots/before/<section>-<viewport>.png
//   node tools/screenshot.js --tag=after-iter2 --sections=market,my-portfolio --viewports=1440x1000,390x844
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { BASE, REPO, SECTIONS, VIEWPORTS, startServer, stopServer } = require('./lib');

const arg = (name, def) => { const a = process.argv.find((x) => x.startsWith(`--${name}=`)); return a ? a.split('=')[1] : def; };
const TAG = arg('tag', 'before');
const secFilter = arg('sections', '');
const vpFilter = arg('viewports', '');
const FIXTURE = require(path.join(REPO, 'tests', 'fixtures', 'portfolio.json'));

(async () => {
  const sections = SECTIONS.filter(([s]) => !secFilter || secFilter.split(',').includes(s));
  const viewports = VIEWPORTS.filter((v) => !vpFilter || vpFilter.split(',').includes(v.name));
  const outDir = path.join(REPO, 'artifacts', 'screenshots', TAG);
  fs.mkdirSync(outDir, { recursive: true });
  const srv = await startServer();
  const browser = await chromium.launch();
  let n = 0;
  try {
    for (const vp of viewports) {
      const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
      for (const [sec, label] of sections) {
        await page.goto(`${BASE}/#${sec}`, { waitUntil: 'networkidle', timeout: 25000 }).catch(() => {});
        // Портфель: снимаем заполненное состояние (грузим фикстуру)
        if (sec === 'my-portfolio') {
          await page.evaluate((input) => { const ta = document.getElementById('mp-input'); if (ta) { ta.value = input; if (typeof renderMyPortfolio === 'function') renderMyPortfolio(); } }, FIXTURE.input).catch(() => {});
          await page.waitForTimeout(1800);
        } else {
          await page.waitForTimeout(900);
        }
        await page.screenshot({ path: path.join(outDir, `${sec}-${vp.name}.png`), fullPage: true }).catch(() => {});
        n++;
      }
      await page.close();
    }
    console.log(`[screenshot] снято ${n} → artifacts/screenshots/${TAG}/`);
    process.exit(0);
  } finally {
    await browser.close();
    stopServer(srv);
  }
})().catch((e) => { console.error('[screenshot] FAIL:', e.message); process.exit(3); });
