// Общие утилиты харнесса редизайна (Итерация 0). Playwright + локальный http.server на site/.
'use strict';
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const SITE_DIR = path.join(REPO, 'site');
const PORT = Number(process.env.DFS_PORT || 8080);
const BASE = `http://localhost:${PORT}`;

// 9 разделов сайта (data-section) + человекочитаемый ярлык
const SECTIONS = [
  ['news', 'Новости'], ['market', 'Обзор/Рынок'], ['my-portfolio', 'Портфель'],
  ['stocks', 'Акции'], ['strategies', 'Стратегии'], ['bonds', 'Облигации'],
  ['cbr', 'Банки РФ'], ['methodology', 'Методология'], ['pro', 'О проекте'],
];

// Вьюпорты по §4.3
const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '1440x1000', width: 1440, height: 1000 },
  { name: '1280x800', width: 1280, height: 800 },
  { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 },
  { name: '390x844', width: 390, height: 844 },
  { name: '360x800', width: 360, height: 800 },
];

function startServer() {
  return new Promise((resolve, reject) => {
    const srv = spawn('python3', ['-m', 'http.server', String(PORT), '--directory', SITE_DIR],
      { stdio: 'ignore' });
    const t0 = Date.now();
    const ping = () => {
      http.get(BASE + '/', (r) => { r.resume(); resolve(srv); })
        .on('error', () => { if (Date.now() - t0 > 8000) reject(new Error('server timeout')); else setTimeout(ping, 200); });
    };
    setTimeout(ping, 300);
  });
}

function stopServer(srv) { try { srv.kill('SIGTERM'); } catch (_e) { /* noop */ } }

// Ждём, пока портфельный движок догрузит returns/marketsaw/marlamov и PFX_STATE стабилизируется
async function waitPortfolioReady(page, timeoutMs = 25000) {
  await page.waitForFunction(() => typeof PFX_STATE !== 'undefined' && PFX_STATE && PFX_STATE.perf,
    { timeout: timeoutMs });
}

module.exports = { REPO, SITE_DIR, PORT, BASE, SECTIONS, VIEWPORTS, startServer, stopServer, waitPortfolioReady };
