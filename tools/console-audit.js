// Console-audit (§4.3): по всем разделам собирает console error/warning, pageerror,
// requestfailed, unhandled rejections → artifacts/console-report.json.
// Пустой массив errors — обязательное условие завершения итерации.
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { BASE, REPO, SECTIONS, startServer, stopServer } = require('./lib');

(async () => {
  const outDir = path.join(REPO, 'artifacts');
  fs.mkdirSync(outDir, { recursive: true });
  const tag = (process.argv.find((a) => a.startsWith('--tag=')) || '--tag=console-report').split('=')[1];
  const srv = await startServer();
  const browser = await chromium.launch();
  const report = { generated_at: new Date().toISOString(), base: BASE, sections: {}, totals: { errors: 0, warnings: 0, pageerrors: 0, requestfailed: 0 } };
  try {
    for (const [sec, label] of SECTIONS) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      const rec = { label, errors: [], warnings: [], pageerrors: [], requestfailed: [] };
      page.on('console', (m) => { if (m.type() === 'error') rec.errors.push(m.text()); else if (m.type() === 'warning') rec.warnings.push(m.text()); });
      page.on('pageerror', (e) => rec.pageerrors.push(String(e.message)));
      page.on('requestfailed', (r) => { const u = r.url(); if (!/iss\.moex\.com/.test(u)) rec.requestfailed.push(`${u} (${r.failure() && r.failure().errorText})`); });
      await page.goto(`${BASE}/#${sec}`, { waitUntil: 'networkidle', timeout: 25000 }).catch(() => {});
      await page.waitForTimeout(1200);
      report.sections[sec] = rec;
      report.totals.errors += rec.errors.length;
      report.totals.warnings += rec.warnings.length;
      report.totals.pageerrors += rec.pageerrors.length;
      report.totals.requestfailed += rec.requestfailed.length;
      await page.close();
    }
    fs.writeFileSync(path.join(outDir, `${tag}.json`), JSON.stringify(report, null, 2) + '\n');
    console.log(`[console-audit] errors=${report.totals.errors} warnings=${report.totals.warnings} pageerrors=${report.totals.pageerrors} requestfailed=${report.totals.requestfailed} → artifacts/${tag}.json`);
    process.exit(report.totals.errors + report.totals.pageerrors + report.totals.requestfailed > 0 ? 1 : 0);
  } finally {
    await browser.close();
    stopServer(srv);
  }
})().catch((e) => { console.error('[console-audit] FAIL:', e.message); process.exit(3); });
