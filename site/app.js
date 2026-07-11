'use strict';

// ── форматирование (RU-локаль, десятичная запятая, ₽) ──
const ND = 'нет данных';
const isNum = (x) => typeof x === 'number' && isFinite(x);
const ru = (x, d = 2) => x.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (x, d = 1) => isNum(x) ? ru(x, d) + '%' : ND;
const fmtRub = (x) => isNum(x) ? ru(x, 2) + ' ₽' : ND;
const fmtScore = (x) => isNum(x) ? ru(x * 100, 1) + '%' : ND;   // 0..1 → %
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const mdash = '<span class="muted" title="нет данных">—</span>';
const cellNum = (x, fmt) => isNum(x) ? fmt(x) : mdash;   // «—» с тултипом вместо «нет данных»
const debounce = (fn, ms = 130) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
// хойст глобалов данных в топ: renderMyPortfolio/pfx* читают их, а wireMyPortfolio() вызывается
// top-level до их прежних объявлений ниже по файлу → без хойста был бы TDZ ('use strict')
let PF_RETURNS = null, SAW_DATA = null, MARKET_HISTORY = null, MARLAMOV = null, SITE_FINANCIALS = null, SITE_STATUS = null, NEWS = null, EVENTS_DATA = null;

// Текст тултипа «Рейтинг» — меняй формулировку здесь:
const RATING_TOOLTIP = 'Вердикт-скор = надёжность дивиденда × оценка (недооценён ↑ / дорог ↓), со штрафом за долг и governance. По умолчанию таблица отсортирована по его убыванию: вверху — надёжные и недооценённые.';

let DATA = null;
let VIEW = [];
let sortKey = 'verdict_score';
let sortDir = -1; // -1 desc, 1 asc

// ── классификация для бейджей ──
function riskBadge(cr) {
  if (!isNum(cr)) return `<span class="badge b-neut">${ND}</span>`;
  const pct = ru(cr * 100, 1) + '%';
  let cls = 'b-good', word = 'низкий';
  if (cr >= 0.5) { cls = 'b-risk'; word = 'высокий'; }
  else if (cr >= 0.2) { cls = 'b-warn'; word = 'средний'; }
  return `<span class="badge ${cls}">${pct} · ${word}</span>`;
}
function stabilityCell(s) {
  if (!isNum(s)) return mdash;
  const pct = ru(s * 100, 1) + '%';
  let cls = 'b-good', word = 'высокая';
  if (s < 0.34) { cls = 'b-neut'; word = 'низкая'; }
  else if (s < 0.67) { cls = 'b-warn'; word = 'средняя'; }
  return `<span class="badge ${cls}">${pct} · ${word}</span>`;
}

// ── Composite Verdict: цветной чип (короткий ярлык в таблице, полный в карточке) ──
const VCOLORCLS = { good: 'b-good', neut: 'b-neut', warn: 'b-warn', risk: 'b-risk' };
function verdictChip(v, full) {
  if (!v) return mdash;
  const cls = VCOLORCLS[v.color] || 'b-neut';
  const tip = `Надёжность ${ru(v.q * 100, 0)}%`
    + (v.v != null ? ` · к справедливой цене ${v.v >= 0 ? '+' : ''}${ru(v.v, 1)}%` : ' · оценка н/д')
    + (v.flags && v.flags.length ? ` · ⚠ ${v.flags.join('/')}` : '');
  return `<span class="badge vchip ${cls}" data-tooltip="${esc(tip)}">${esc(full ? v.label : v.short)}</span>`;
}

// ── загрузка ──
fetch('data.json', { cache: 'no-store' })
  .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(init)
  .catch((e) => {
    document.getElementById('app').innerHTML =
      `<div class="error">Не удалось загрузить данные: ${esc(e.message)}</div>`;
  });

wireMarketSaw();   // блок «Помощник фазы рынка» независим от data.json (грузит свой marketsaw.json)
wireBonds();       // блок «Облигации» независим от data.json (грузит свои bonds/*.json)
wireMarlamov();    // блок «Форвардная доходность» (таблица Марламова) — грузит marlamov.json
wireMethodology(); // блок «Методология» (4 раздела) — грузит methodology.json
wireDataCoverage(); // блок прозрачности источников — грузит site_coverage.json, если он опубликован
// навигация по разделам — в initRouter() в конце файла (после let-объявлений)

function init(data) {
  DATA = data;
  const m = data.meta;

  // даты (ДВЕ явно)
  document.getElementById('dates').innerHTML =
    `<span class="date-chip"><span class="lbl">Прогноз модели:</span> <b>${esc(m.forecast_asof || '—')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Цены:</span> <b>${esc(m.price_asof || '—')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Горизонт:</span> <b>дивиденды ${esc(m.forecast_year || '')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Эмитентов:</span> <b>${m.n_total}</b></span>`;

  if (m.prices_stale) {
    document.getElementById('banner').innerHTML =
      `⚠️ Биржевой источник временно недоступен — показаны последние известные цены`
      + (m.price_asof ? ` (на ${esc(m.price_asof)})` : '') + `. Дивидендная доходность может быть неактуальной.`;
    document.getElementById('banner').className = 'banner';
  }

  // методология
  document.getElementById('method').hidden = false;
  document.getElementById('auc').textContent = isNum(m.auc_oof_rf) ? ru(m.auc_oof_rf, 3) : '—';
  document.getElementById('disclaimer').textContent = m.disclaimer || '';
  document.getElementById('attrib').textContent =
    'Источник цен: ' + (m.source || 'MOEX ISS') + '. Обновлено: ' + (m['обновлено'] || '').replace('T', ' ').slice(0, 16) + '.';

  // фильтр по отраслям
  const sectors = [...new Set(data.tickers.map((t) => t.sector).filter((s) => s && s !== ND))].sort((a, b) => a.localeCompare(b, 'ru'));
  const sel = document.getElementById('sector');
  sectors.forEach((s) => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });

  // события
  document.getElementById('controls').hidden = false;
  document.getElementById('mapwrap').hidden = false;
  document.getElementById('search').addEventListener('input', debounce(render, 130));
  document.getElementById('sector').addEventListener('change', render);
  document.getElementById('statusFilter').addEventListener('change', render);
  document.getElementById('csv').addEventListener('click', exportCSV);
  wirePortfolio();
  wireMyPortfolio();
  loadReturns(() => { const o = document.getElementById('pf-out'); if (o && o.dataset.shown) renderPortfolio(); });  // жадно грузим историю → мгновенный результат

  render();
  if (typeof updateDataStatus === 'function') updateDataStatus();   // даты цен/прогноза в global status bar
  if (typeof renderMarketKPI === 'function') renderMarketKPI();     // KPI «Акций в скринере» и т.д.
  if (typeof renderMarketSignals === 'function') renderMarketSignals();
  if (typeof loadSiteFinancials === 'function') loadSiteFinancials(() => { render(); renderMyPortfolio(); });
  if (typeof loadDataCoverage === 'function') loadDataCoverage(() => updateDataStatus());
  if (getSectionFromHash && getSectionFromHash() === 'my-portfolio') renderMyPortfolio();
}

// ── фильтрация + сортировка ──
function computeView() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const sec = document.getElementById('sector').value;
  const st = document.getElementById('statusFilter').value;
  let rows = DATA.tickers.filter((t) => {
    if (sec && t.sector !== sec) return false;
    if (st && t.status !== st) return false;
    if (q && !(t.ticker.toLowerCase().includes(q) || String(t.name).toLowerCase().includes(q))) return false;
    return true;
  });
  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    const xn = isNum(x), yn = isNum(y);
    if (!xn && !yn) return 0;
    if (!xn) return 1;          // «нет данных» всегда вниз
    if (!yn) return -1;
    if (typeof x === 'string') return x.localeCompare(y, 'ru') * sortDir;
    return (x - y) * sortDir;
  });
  return rows;
}

const COLS = [
  { key: 'ticker', label: 'Тикер', left: true },
  { key: 'verdict_score', label: 'Вердикт', left: true, title: 'Надёжность дивиденда × оценка, со штрафом за долг/governance. Сортировка по умолчанию.' },
  { key: 'stability_score', label: 'Устойчивость' },
  { key: 'cut_risk', label: 'Риск невыплаты' },
  { key: 'dividend_forecast', label: 'Прогноз дивиденда' },
  { key: 'payout', label: 'Payout' },
  { key: 'dividend_yield_expected', label: 'Дох. ожид.', title: 'Ожидаемая доходность = вероятность выплаты × прогнозный дивиденд / цена' },
  { key: 'dividend_yield_if_paid', label: 'Дох. при выпл.', title: 'Доходность при выплате = прогнозный дивиденд / цена (без поправки на вероятность)' },
  { key: 'status', label: 'Статус' },
];

// быстрое ранжирование (один клик → от большего к меньшему)
const RANK_PRESETS = [
  { key: 'verdict_score', label: 'Вердикт (общий)' },
  { key: 'stability_score', label: 'Устойчивость' },
  { key: 'dividend_yield_expected', label: 'Доходность (ожид.)' },
  { key: 'dividend_yield_if_paid', label: 'Доходность (при выплате)' },
];

function renderRanks() {
  const el = document.getElementById('ranks');
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `<span class="ranks-lbl" data-tooltip="${esc(RATING_TOOLTIP)}">Рейтинг ↓</span>` + RANK_PRESETS.map((p) =>
    `<button class="rank-chip${(sortKey === p.key && sortDir < 0) ? ' active' : ''}" data-key="${p.key}">${p.label}</button>`).join('');
  el.querySelectorAll('.rank-chip').forEach((b) => b.addEventListener('click', () => {
    sortKey = b.dataset.key; sortDir = -1; render();
  }));
}

// ── Карта рынка: scatter Надёжность(Y) × Оценка(X), цвет = вердикт ──
function renderMap() {
  const el = document.getElementById('map');
  if (!el) return;
  const pts = VIEW.filter((t) => t.verdict && t.verdict.v != null && isNum(t.stability_score));
  const na = VIEW.filter((t) => t.verdict && t.verdict.v == null).length;
  if (pts.length < 3) { el.innerHTML = `<p class="map-note muted">Недостаточно оценённых имён для карты (с оценкой: ${pts.length}).</p>`; return; }
  const W = 720, H = 430, mL = 54, mR = 18, mT = 30, mB = 46;
  const iw = W - mL - mR, ih = H - mT - mB, XCL = 100;
  const sx = (v) => mL + (Math.max(-XCL, Math.min(XCL, v)) + XCL) / (2 * XCL) * iw;
  const sy = (q) => mT + (1 - q) * ih;
  const x0 = sx(0), yHi = sy(0.67), yLo = sy(0.34);
  const COL = { good: '#10B981', warn: '#F59E0B', neut: '#94A3B8', risk: '#F43F5E' };
  const xticks = [-100, -50, 0, 50, 100].map((v) => {
    const x = sx(v);
    return `<line x1="${x}" y1="${mT}" x2="${x}" y2="${mT + ih}" stroke="#EEF1F6"/>`
      + `<text x="${x}" y="${mT + ih + 16}" class="mp-tick" text-anchor="middle">${v > 0 ? '+' : ''}${v}%</text>`;
  }).join('');
  const yticks = [0, 34, 67, 100].map((p) => `<text x="${mL - 8}" y="${(sy(p / 100) + 3).toFixed(1)}" class="mp-tick" text-anchor="end">${p}</text>`).join('');
  const dots = pts.map((t) => {
    const v = t.verdict, cx = sx(v.v), cy = sy(t.stability_score), star = v.label.startsWith('★');
    return `<circle class="mp-dot" data-tk="${esc(t.ticker)}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${star ? 4.6 : 3.1}" fill="${COL[v.color] || COL.neut}" fill-opacity="${star ? 0.95 : 0.62}" stroke="${star ? '#0F766E' : 'none'}" stroke-width="${star ? 1.3 : 0}"><title>${esc(t.ticker)} — ${esc(v.label)} · надёжн. ${ru(t.stability_score * 100, 0)}%, ${v.v >= 0 ? '+' : ''}${ru(v.v, 1)}%</title></circle>`;
  }).join('');
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="map-svg">`
    + `<rect x="${x0}" y="${mT}" width="${(mL + iw - x0).toFixed(1)}" height="${(yHi - mT).toFixed(1)}" fill="#10B981" fill-opacity="0.05"/>`
    + xticks + yticks
    + `<line x1="${x0}" y1="${mT}" x2="${x0}" y2="${mT + ih}" stroke="#CBD5E1" stroke-dasharray="3 3"/>`
    + `<line x1="${mL}" y1="${yHi}" x2="${mL + iw}" y2="${yHi}" stroke="#E2E8F0" stroke-dasharray="3 3"/>`
    + `<line x1="${mL}" y1="${yLo}" x2="${mL + iw}" y2="${yLo}" stroke="#E2E8F0" stroke-dasharray="3 3"/>`
    + `<text x="${mL + iw}" y="${mT + 14}" class="mp-corner" text-anchor="end">★ надёжные + дёшево</text>`
    + `<text x="${mL + 4}" y="${mT + 14}" class="mp-corner" text-anchor="start">надёжные, дорого</text>`
    + `<text x="${mL + iw}" y="${(mT + ih - 6).toFixed(1)}" class="mp-corner" text-anchor="end">дёшево, рискованно</text>`
    + `<text x="${mL + 4}" y="${(mT + ih - 6).toFixed(1)}" class="mp-corner" text-anchor="start">слабые</text>`
    + `<text x="${mL + iw / 2}" y="${H - 6}" class="mp-axis" text-anchor="middle">← дороже · недооценка к справедливой цене · дешевле →</text>`
    + `<text transform="translate(14 ${mT + ih / 2}) rotate(-90)" class="mp-axis" text-anchor="middle">надёжность дивиденда, %</text>`
    + dots + `</svg>`
    + `<div class="map-note muted">Точка — эмитент: X = недооценка к справедливой цене (клэмп ±100%), Y = надёжность дивиденда. Зелёные ★ — надёжные и недооценённые.${na ? ` ${na} без надёжной оценки не показаны.` : ''}</div>`;
  el.querySelectorAll('.mp-dot').forEach((c) => c.addEventListener('click', () => {
    const s = document.getElementById('search'); if (s) { s.value = c.dataset.tk; render(); }
  }));
}

function render() {
  VIEW = computeView();
  document.getElementById('count').textContent = `${VIEW.length} из ${DATA.tickers.length}`;
  renderRanks();
  renderMap();
  renderTable();
  renderCards();
}

function arrow(key) {
  if (key !== sortKey) return '';
  return ` <span class="arrow">${sortDir < 0 ? '▼' : '▲'}</span>`;
}

function renderTable() {
  const head = '<tr>' + COLS.map((c) =>
    `<th class="${c.left ? 'left' : ''}" data-key="${c.key}"${c.title ? ` title="${c.title}"` : ''}>${c.label}${arrow(c.key)}</th>`).join('') + '</tr>';

  const body = VIEW.length ? VIEW.map((t, i) => {
    const payoutTxt = isNum(t.payout)
      ? `${ru(t.payout, 1)}%${t.payout_year ? ` <span class="muted">(${t.payout_year})</span>` : ''}`
      : mdash;
    const statusChip = t.status === 'ok'
      ? '<span class="status-chip s-ok">✓ полные</span>'
      : '<span class="status-chip s-insuf">неполные</span>';
    return `<tr class="data-row" data-i="${i}">
      <td class="left"><span class="rank">${i + 1}</span><span class="tk">${esc(t.ticker)}</span><br><span class="nm">${esc(t.name)}</span></td>
      <td class="left">${verdictChip(t.verdict, false)}</td>
      <td>${stabilityCell(t.stability_score)}</td>
      <td>${riskBadge(t.cut_risk)}</td>
      <td class="tnum">${cellNum(t.dividend_forecast, fmtRub)}</td>
      <td class="tnum">${payoutTxt}</td>
      <td class="tnum">${cellNum(t.dividend_yield_expected, fmtPct)}</td>
      <td class="tnum">${cellNum(t.dividend_yield_if_paid, fmtPct)}</td>
      <td>${statusChip}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="${COLS.length}" class="empty">Ничего не найдено</td></tr>`;

  document.getElementById('app').innerHTML =
    `<div class="table-card"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`
    + `<div class="cards" id="cards"></div>`;

  document.querySelectorAll('thead th').forEach((th) => th.addEventListener('click', () => {
    const k = th.dataset.key;
    if (k === sortKey) sortDir *= -1; else { sortKey = k; sortDir = (k === 'ticker') ? 1 : -1; }
    render();
  }));
  document.querySelectorAll('tr.data-row').forEach((tr) =>
    tr.addEventListener('click', () => toggleDetail(tr, VIEW[+tr.dataset.i])));
  renderCards();
}

function shapHTML(t) {
  if (!t.shap_top5 || !t.shap_top5.length) return `<p class="muted">${ND}</p>`;
  const max = Math.max(...t.shap_top5.map((s) => Math.abs(s.impact || 0)), 1e-9);
  return t.shap_top5.map((s) => {
    const up = (s.impact || 0) >= 0;
    const w = Math.max(4, Math.abs(s.impact || 0) / max * 100).toFixed(0);
    const col = up ? 'var(--good-bar)' : 'var(--risk-bar)';
    return `<div class="shap-item">
      <span class="shap-dir ${up ? 'up' : 'down'}">${up ? '↑' : '↓'}</span>
      <span style="flex:1">${esc(s.feature_ru || s.feature)}</span>
      <span class="shap-bar"><span style="width:${w}%;background:${col}"></span></span>
    </div>`;
  }).join('') + `<p class="muted" style="margin-top:8px;font-size:.8rem">↑ повышает / ↓ снижает вероятность выплаты · усреднено по моделям ансамбля</p>`;
}

// ── премиум-графики (Bloomberg/SaaS-эстетика): структура капитала + small-multiples ──
const CH = { teal: '#0F766E', slate: '#1E293B', grey: '#64748B', up: '#10B981', down: '#F43F5E', ink: '#334155', faint: '#94A3B8', line: '#E2E8F0' };

function fmtShort(v) {
  if (v == null) return '—';
  const a = Math.abs(v), s = v < 0 ? '−' : '';
  if (a >= 1e6) return s + (a / 1e6).toFixed(a / 1e6 >= 10 ? 0 : 1) + ' трлн';
  if (a >= 1e3) return s + (a / 1e3).toFixed(a / 1e3 >= 10 ? 0 : 1) + ' млрд';
  return s + Math.round(a) + ' млн';
}

function smoothPath(pts) {                     // монотонно-кубическая сглаженная кривая
  if (pts.length < 2) return pts.length ? `M${pts[0][0]} ${pts[0][1]}` : '';
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

function areaSpark(vals, color, uid) {         // area-спарклайн с градиентом и end-точкой
  const W = 260, H = 48, pad = 6;
  const valid = vals.map((v, i) => [i, v]).filter((p) => p[1] != null);
  if (valid.length < 2) return `<svg viewBox="0 0 ${W} ${H}" class="pspark"></svg>`;
  const ys = valid.map((p) => p[1]); let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y0 === y1) { y0 -= 1; y1 += 1; }
  // ось X ребейзится на ДИАПАЗОН ДАННЫХ метрики (первый→последний непустой год), а не на весь ряд лет:
  // метрики с поздним стартом (ROE при отриц. капитале, EBITDA) заполняют ширину, а не жмутся вправо.
  const i0 = valid[0][0], i1 = valid[valid.length - 1][0], span = (i1 - i0) || 1;
  const sx = (i) => pad + (i - i0) / span * (W - 2 * pad);
  const sy = (v) => H - pad - (v - y0) / (y1 - y0) * (H - 2 * pad);
  const pts = valid.map((p) => [sx(p[0]), sy(p[1])]);
  const line = smoothPath(pts), last = pts[pts.length - 1];
  const area = `${line} L${last[0].toFixed(1)} ${H} L${pts[0][0].toFixed(1)} ${H} Z`;
  return `<svg viewBox="0 0 ${W} ${H}" class="pspark" preserveAspectRatio="none">`
    + `<defs><linearGradient id="ag${uid}" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0" stop-color="${color}" stop-opacity="0.18"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`
    + `<path d="${area}" fill="url(#ag${uid})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`
    + `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3.2" fill="${color}"/></svg>`;
}

function capitalStructure(h) {                  // Chart 1: накладывающиеся бары Активы/Капитал + леверидж
  const A = h.assets_mln, E = h.equity_mln;
  if (!A || !E || !A.some((v) => v != null)) return '';
  const ys = h.years, maxA = Math.max(...A.filter((v) => v != null)), n = ys.length;
  const W = Math.max(n * 50, 280), H = 158, base = H - 30, top = 12, bw = Math.min(38, W / n * 0.66);
  const g = ys.map((y, i) => {
    const a = A[i]; if (a == null) return '';
    const e = E[i], cx = (i + 0.5) / n * W;
    const ha = a / maxA * (base - top), ya = base - ha;
    const he = e != null ? e / maxA * (base - top) : 0, ye = base - he;
    const lev = (a && e != null) ? Math.round((a - e) / a * 100) : null;
    return `<rect x="${(cx - bw / 2).toFixed(1)}" y="${ya.toFixed(1)}" width="${bw.toFixed(1)}" height="${ha.toFixed(1)}" rx="3" fill="${CH.teal}" fill-opacity="0.20"/>`
      + (e != null ? `<rect x="${(cx - bw * 0.3).toFixed(1)}" y="${ye.toFixed(1)}" width="${(bw * 0.6).toFixed(1)}" height="${he.toFixed(1)}" rx="2.5" fill="${CH.slate}"/>` : '')
      + `<text x="${cx.toFixed(1)}" y="${(ya - 5).toFixed(1)}" class="cs-lbl" text-anchor="middle">${fmtShort(a)}</text>`
      + `<text x="${cx.toFixed(1)}" y="${base + 14}" class="cs-yr" text-anchor="middle">'${String(y).slice(2)}</text>`
      + (lev != null ? `<text x="${cx.toFixed(1)}" y="${base + 25}" class="cs-lev" text-anchor="middle">${lev}%</text>` : '');
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="cs-svg">${g}</svg>`;
}

function perfMultiples(h) {                      // Chart 2: small-multiples area + синхро-кросхейр
  const M = [['revenue_mln', 'Выручка', CH.teal, 0], ['net_profit_mln', 'Чистая прибыль', CH.slate, 0],
             ['ebitda_mln', 'EBITDA', CH.grey, 0], ['roe_pct', 'ROE', CH.slate, 1]];
  const rows = M.filter((m) => h[m[0]] && h[m[0]].some((v) => v != null)).map((m, i) => {
    const vals = h[m[0]], pct = m[3], valid = vals.filter((v) => v != null);
    const last = valid[valid.length - 1], prev = valid[valid.length - 2];
    const col = prev == null ? CH.ink : (last >= prev ? CH.up : CH.down);
    const disp = pct ? ru(last, 1) + '%' : fmtShort(last);
    return `<div class="pm-row" data-vals='${JSON.stringify(vals)}' data-pct="${pct}">`
      + `<div class="pm-name"><span>${m[1]}</span><span class="pm-sub">${h.years[h.years.length - 1]}</span></div>`
      + `<div class="pm-chart">${areaSpark(vals, m[2], 'p' + i)}<div class="pm-cross"></div></div>`
      + `<div class="pm-val" style="color:${col}">${disp}</div></div>`;
  }).join('');
  return `<div class="pm" data-years='${JSON.stringify(h.years)}'>${rows}</div>`;
}

function historyHTML(h) {
  if (!h || !h.years || !h.years.length) return `<p class="muted">${ND}</p>`;
  const cs = capitalStructure(h);
  const csBlock = cs ? `<div class="ch-block"><div class="ch-title">Структура капитала`
    + `<span class="ch-leg"><i style="background:${CH.teal};opacity:.4"></i>Активы<i style="background:${CH.slate}"></i>Капитал · % = долг/активы</span></div>${cs}</div>` : '';
  return `<div class="charts">${csBlock}<div class="ch-block"><div class="ch-title">Динамика показателей</div>${perfMultiples(h)}</div></div>`;
}

function fundamentalsOrHistoryHTML(t) {
  const html = fundamentalsHTML(t && t.ticker);
  return html || historyHTML(t && t.history);
}

function fundamentalsHTML(ticker) {
  const fundamentals = SITE_FINANCIALS && SITE_FINANCIALS.fundamentals;
  if (!fundamentals || !ticker || !fundamentals[ticker]) return '';
  const labels = {
    income: 'Финрезультаты',
    balance: 'Баланс',
    cashflow: 'Денежный поток',
    dividends: 'Дивиденды',
    ratios: 'Рентабельность и долг',
    valuation: 'Оценка',
    per_share: 'На акцию',
  };
  const groups = ['income', 'balance', 'cashflow', 'dividends', 'ratios', 'valuation', 'per_share'];
  const sections = groups.map((g) => {
    const metrics = fundamentals[ticker][g] || [];
    if (!metrics.length) return '';
    return `<div class="fund-group"><div class="fund-title">${esc(labels[g] || g)}</div>${metrics.map(fundMetricHTML).join('')}</div>`;
  }).join('');
  if (!sections) return '';
  return `<div class="fund">
    <div class="fund-note">SmartLab cleaned layer: raw values, mapped fields and derived metrics from clean base facts; blocked outliers are not published as clean values.</div>
    ${sections}
  </div>`;
}

function fundMetricHTML(metric) {
  const vals = metric.values || [];
  const last = [...vals].reverse().find((v) => v.value != null || v.raw_value != null) || {};
  const blocked = vals.some((v) => v.excluded_from_site);
  const review = vals.some((v) => v.needs_manual_review);
  const cls = blocked ? 'fund-bad' : (review ? 'fund-review' : 'fund-ok');
  const status = blocked ? 'blocked' : (review ? 'review' : 'clean');
  const sourceStatus = formatFundSourceStatus(metric.source_status || last.source_status || 'smartlab_fallback');
  return `<div class="fund-row">
    <div class="fund-meta">
      <span class="fund-name">${esc(metric.name_ru || metric.field)}</span>
      <span class="fund-source">${esc(metric.source_name || 'SmartLab')} · ${esc(sourceStatus)}</span>
    </div>
    <div class="fund-bars">${fundBars(vals, metric.display_format)}</div>
    <div class="fund-last">
      <b>${esc(fmtFund(last.value, metric.display_format))}</b>
      <span class="fund-status ${cls}" title="${esc(last.quality_reason || status)}">${esc(status)}</span>
    </div>
  </div>`;
}

function formatFundSourceStatus(status) {
  const labels = {
    smartlab_fallback: 'SmartLab fallback',
    mapped_from_raw_existing: 'mapped from existing SmartLab field',
    calculated_from_clean_base_facts: 'calculated from clean base facts',
    calculated_from_smartlab_base_facts: 'calculated from SmartLab base facts',
  };
  return labels[status] || status || 'SmartLab fallback';
}

function fundBars(vals, format) {
  const points = (vals || []).map((v) => ({
    year: v.year,
    value: isNum(v.value) ? v.value : null,
    raw: isNum(v.raw_value) ? v.raw_value : null,
    status: v.quality_status || 'clean',
    reason: v.quality_reason || '',
    blocked: !!v.excluded_from_site,
    review: !!v.needs_manual_review,
  }));
  if (!points.length) return '<div class="fund-empty">нет ряда</div>';
  const clean = points.map((p) => p.value).filter((v) => v != null);
  if (!clean.length) {
    return `<div class="fund-blocked-row">${points.map((p) => `<span title="${esc(fundPointTitle(p, format))}">×</span>`).join('')}</div>`;
  }
  let min = Math.min(0, ...clean), max = Math.max(0, ...clean);
  if (min === max) { min -= 1; max += 1; }
  const range = max - min;
  const W = Math.max(points.length * 30, 150), H = 54, pad = 6;
  const zero = H - pad - (0 - min) / range * (H - 2 * pad);
  const bw = Math.min(18, (W - pad * 2) / points.length * 0.62);
  const bars = points.map((p, i) => {
    const cx = pad + (i + 0.5) / points.length * (W - pad * 2);
    if (p.value == null) {
      return `<circle cx="${cx.toFixed(1)}" cy="${zero.toFixed(1)}" r="2.7" class="${p.blocked ? 'fb-blocked' : 'fb-missing'}"><title>${esc(fundPointTitle(p, format))}</title></circle>`;
    }
    const y = H - pad - (p.value - min) / range * (H - 2 * pad);
    const top = Math.min(y, zero), h = Math.max(2, Math.abs(zero - y));
    const klass = p.value >= 0 ? 'fb-pos' : 'fb-neg';
    return `<rect x="${(cx - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2" class="${klass}"><title>${esc(fundPointTitle(p, format))}</title></rect>`;
  }).join('');
  const labels = points.map((p, i) => {
    const cx = pad + (i + 0.5) / points.length * (W - pad * 2);
    return `<text x="${cx.toFixed(1)}" y="${H - 1}" text-anchor="middle" class="fb-year">${esc(String(p.year || '').slice(-2))}</text>`;
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="fund-svg" preserveAspectRatio="none"><line x1="${pad}" y1="${zero.toFixed(1)}" x2="${W - pad}" y2="${zero.toFixed(1)}" class="fb-zero"/>${bars}${labels}</svg>`;
}

function fundPointTitle(p, format) {
  const shown = p.value == null ? `raw ${fmtFund(p.raw, format)}` : fmtFund(p.value, format);
  const status = p.status ? ` · ${p.status}` : '';
  const reason = p.reason ? ` · ${p.reason}` : '';
  return `${p.year || ''}: ${shown}${status}${reason}`;
}

function fmtFund(v, format) {
  if (!isNum(v)) return '—';
  if (format === 'percent') return ru(v, 1) + '%';
  if (format === 'multiple') return ru(v, 2) + '×';
  if (format === 'rub') return ru(v, 2) + ' ₽';
  if (format === 'shares') return v >= 1e9 ? ru(v / 1e9, 2) + ' млрд' : (v >= 1e6 ? ru(v / 1e6, 1) + ' млн' : ru(v, 0));
  if (format === 'money_mln') return fmtShort(v);
  return ru(v, 2);
}

function wireCharts(root) {                      // кросхейр по small-multiples (по диапазону данных каждой строки)
  root.querySelectorAll('.pm').forEach((pm) => {
    const rows = [...pm.querySelectorAll('.pm-row')].map((r) => {
      const vals = JSON.parse(r.dataset.vals);
      const vi = vals.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0);   // индексы непустых лет
      return {
        vals, pct: r.dataset.pct === '1',
        i0: vi.length ? vi[0] : 0, i1: vi.length ? vi[vi.length - 1] : 0,           // диапазон данных строки
        cross: r.querySelector('.pm-cross'), val: r.querySelector('.pm-val'), chart: r.querySelector('.pm-chart'),
      };
    });
    const reset = () => rows.forEach((d) => {
      d.cross.style.opacity = 0;
      const valid = d.vals.filter((v) => v != null), last = valid[valid.length - 1];
      d.val.textContent = d.pct ? ru(last, 1) + '%' : fmtShort(last);
    });
    pm.addEventListener('mousemove', (e) => {
      const rect = rows[0].chart.getBoundingClientRect();
      let f = (e.clientX - rect.left) / rect.width; f = Math.max(0, Math.min(1, f));
      rows.forEach((d) => {                          // каждая строка мапит f на свой диапазон (зеркалит sx-ребейз)
        const w = d.chart.clientWidth, span = (d.i1 - d.i0) || 1;
        const idx = Math.round(d.i0 + f * span);
        const x = d.i1 === d.i0 ? w / 2 : 6 + (idx - d.i0) / span * (w - 12);
        d.cross.style.left = x + 'px'; d.cross.style.opacity = 1;
        const v = d.vals[idx];
        d.val.textContent = v == null ? '—' : (d.pct ? ru(v, 1) + '%' : fmtShort(v));
      });
    });
    pm.addEventListener('mouseleave', reset);
  });
}

// ── матрица чувствительности (мини-таблица) ──
function sensHTML(s) {
  if (!s) return '';
  const head = '<th></th>' + s.cols.map((c) => `<th>${(c * 100).toFixed(1)}</th>`).join('');
  const body = s.values.map((row, i) => `<tr><th>${(s.rows[i] * 100).toFixed(1)}</th>`
    + row.map((v) => `<td>${v == null ? '—' : ru(v, 0)}</td>`).join('') + '</tr>').join('');
  return `<div class="sens"><div class="sens-cap muted">Чувствительность, ₽ · строки ${esc(s.row_label)}% × столбцы ${esc(s.col_label)}%</div>`
    + `<table class="sens-tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ── блок оценки справедливой стоимости ──
function valuationHTML(v) {
  if (!v) return `<p class="muted">${ND}</p>`;
  const fair = isNum(v.fair_price) ? `<b class="tnum">${ru(v.fair_price, 1)} ₽</b>` : '<span class="muted">оценка вручную</span>';
  const up = isNum(v.upside_pct)
    ? `<span class="badge ${v.upside_pct >= 0 ? 'b-good' : 'b-risk'}">${v.upside_pct >= 0 ? '+' : ''}${ru(v.upside_pct, 1)}%</span>` : '';
  const alert = v.alert ? `<div class="flagline">⚠ ${esc(v.alert)}</div>` : '';
  return `<div class="val">
    <div class="val-top"><span class="val-method">${esc(v.method)}</span>${up}</div>
    <div class="val-fair">Справедливая цена: ${fair}</div>
    ${alert}
    <p class="val-note muted">${esc(v.note || '')}</p>
    ${sensHTML(v.sensitivity)}
  </div>`;
}

// ── позиция в секторе: перцентили метрик (бар ориентирован «к лучшему», 100 = лучший в секторе) ──
function sectorPercentilesHTML(t) {
  const sp = t.sector_percentiles;
  if (!sp || !Array.isArray(sp.metrics) || !sp.metrics.length) return `<p class="muted">${ND}</p>`;
  const rows = sp.metrics.map((m) => {
    const g = Math.max(0, Math.min(100, m.good_pct));
    const cls = g >= 67 ? 'p-good' : (g >= 34 ? 'p-mid' : 'p-low');
    const raw = m.key === 'stability' ? ru(m.raw * 100, 0) + '%'
      : ru(m.raw, m.unit === '×' ? 2 : 1) + (m.unit || '');
    const tip = `лучше ${g}% сектора · сравнение с ${m.n} компаниями`
      + (m.polarity === 'down' ? ' · ниже значение = лучше' : '');
    return `<div class="pctl-row" data-tooltip="${esc(tip)}">`
      + `<span class="pctl-lbl">${esc(m.label)}</span>`
      + `<span class="pctl-raw tnum">${raw}</span>`
      + `<span class="pctl-bar"><i class="${cls}" style="width:${g}%"></i></span>`
      + `<span class="pctl-pct tnum">${g}</span></div>`;
  }).join('');
  return `<div class="pctl"><div class="pctl-head muted">Сектор: ${esc(sp.sector)} · бар = «лучше N% сектора»</div>${rows}</div>`;
}

function detailKV(t) {
  const lohi = (isNum(t.dividend_forecast_lo) && isNum(t.dividend_forecast_hi))
    ? `${ru(t.dividend_forecast_lo, 1)}–${ru(t.dividend_forecast_hi, 1)} ₽` : ND;
  const flagMap = {
    y_paid_invalid: 'доходность при выплате вне диапазона — скрыта',
    y_exp_invalid: 'ожидаемая доходность вне диапазона — скрыта',
    y_paid_high: 'высокая доходность при выплате (>30%)',
    y_exp_high: 'высокая ожидаемая доходность (>30%)',
    payout_negative: 'payout отрицательный (убыток при выплате)',
    price_stale: 'цена не обновлена (кэш)',
    no_price: 'нет рыночной цены',
    no_forecast: 'нет прогноза модели',
    dps_unreliable: 'прогноз дивиденда скрыт как ненадёжный',
  };
  const flags = (t.flags || []).map((f) => flagMap[f] || f);
  return `<dl class="kv">
    <dt>Текущая цена</dt><dd class="tnum">${fmtRub(t.price)}${t.price_field ? ` <span class="muted">(${esc(t.price_field)})</span>` : ''}</dd>
    <dt>Прогноз дивиденда</dt><dd class="tnum">${fmtRub(t.dividend_forecast)}</dd>
    <dt>Интервал прогноза</dt><dd class="tnum">${lohi}</dd>
    <dt>Дивиденд за посл. год</dt><dd class="tnum">${fmtRub(t.current_dps)}</dd>
    <dt>Серия лет выплат</dt><dd class="tnum">${t.div_streak ?? ND}</dd>
    <dt>Payout (факт)</dt><dd class="tnum">${isNum(t.payout) ? ru(t.payout,1)+'%'+(t.payout_year?` (${t.payout_year})`:'')+(t.payout_source?` <span class="muted">· эмитент</span>`:'') : ND}</dd>
  </dl>`
    + (t.forecast_note ? `<div class="flagline">ℹ ${esc(t.forecast_note)}</div>` : '')
    + (flags.length ? `<div class="flagline">⚠ ${flags.map(esc).join('; ')}</div>` : '');
}

function statusChipHTML(t) {
  return t.status === 'ok'
    ? '<span class="status-chip s-ok">✓ полные</span>'
    : '<span class="status-chip s-insuf">неполные</span>';
}

function stockDetailSummaryHTML(t) {
  return `<div class="issuer-summary">
    <div class="issuer-title">
      <span class="issuer-ticker">${esc(t.ticker)}</span>
      <div>
        <b>${esc(t.name)}</b>
        <span>${esc(t.sector || ND)}</span>
      </div>
    </div>
    <div class="issuer-badges">
      ${verdictChip(t.verdict, true)}
      ${riskBadge(t.cut_risk)}
      ${statusChipHTML(t)}
    </div>
    <div class="issuer-kpis">
      <div><span>Устойчивость</span>${stabilityCell(t.stability_score)}</div>
      <div><span>Доходность ожид.</span><b class="tnum">${cellNum(t.dividend_yield_expected, fmtPct)}</b></div>
      <div><span>Прогноз дивиденда</span><b class="tnum">${cellNum(t.dividend_forecast, fmtRub)}</b></div>
      <div><span>Цена</span><b class="tnum">${cellNum(t.price, fmtRub)}</b></div>
    </div>
  </div>`;
}

function dividendMetricsHTML(t) {
  const payout = isNum(t.payout) ? `${ru(t.payout, 1)}%${t.payout_year ? ` <span class="muted">(${t.payout_year})</span>` : ''}` : mdash;
  const lohi = (isNum(t.dividend_forecast_lo) && isNum(t.dividend_forecast_hi))
    ? `${ru(t.dividend_forecast_lo, 1)}–${ru(t.dividend_forecast_hi, 1)} ₽` : mdash;
  return `<dl class="kv dividend-kv">
    <dt>Доходность при выплате</dt><dd class="tnum">${cellNum(t.dividend_yield_if_paid, fmtPct)}</dd>
    <dt>Payout факт</dt><dd class="tnum">${payout}</dd>
    <dt>Интервал прогноза</dt><dd class="tnum">${lohi}</dd>
    <dt>Серия лет выплат</dt><dd class="tnum">${t.div_streak ?? mdash}</dd>
  </dl>`;
}

function toggleDetail(tr, t) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains('detail-row')) { next.remove(); return; }
  document.querySelectorAll('tr.detail-row').forEach((r) => r.remove());
  const dr = document.createElement('tr');
  dr.className = 'detail-row';
  dr.innerHTML = `<td colspan="${COLS.length}"><div class="detail detail-investor">
    ${stockDetailSummaryHTML(t)}
    <div class="detail-card"><h4>Оценка стоимости</h4>${valuationHTML(t.valuation)}</div>
    <div class="detail-card"><h4>Дивидендные метрики</h4>${dividendMetricsHTML(t)}</div>
    <div class="detail-card"><h4>Позиция в секторе</h4>${sectorPercentilesHTML(t)}</div>
    <div class="detail-card"><h4>Фундаментальные показатели</h4>${fundamentalsOrHistoryHTML(t)}</div>
    <div class="detail-card"><h4>Ключевые факторы (SHAP)</h4>${shapHTML(t)}</div>
    <div class="detail-card"><h4>Детали и флаги</h4>${detailKV(t)}</div>
  </div></td>`;
  tr.after(dr);
  wireCharts(dr);
}

function renderCards() {
  const el = document.getElementById('cards');
  if (!el) return;
  el.innerHTML = VIEW.length ? VIEW.map((t, i) => {
    const statusChip = statusChipHTML(t);
    return `<div class="card">
      <div class="top"><span class="tk"><span class="rank">${i + 1}</span>${esc(t.ticker)}</span>${riskBadge(t.cut_risk)}</div>
      <div class="nm">${esc(t.name)} · ${esc(t.sector)}</div>
      <div class="card-verdict">${verdictChip(t.verdict, true)}</div>
      <div class="grid">
        <div><span class="lbl">Устойчивость</span>${stabilityCell(t.stability_score)}</div>
        <div><span class="lbl">Прогноз дивиденда</span><span class="tnum">${cellNum(t.dividend_forecast, fmtRub)}</span></div>
        <div><span class="lbl">Доходн. ожид.</span><span class="tnum">${cellNum(t.dividend_yield_expected, fmtPct)}</span></div>
        <div><span class="lbl">Доходн. при выплате</span><span class="tnum">${cellNum(t.dividend_yield_if_paid, fmtPct)}</span></div>
        <div><span class="lbl">Payout</span><span class="tnum">${isNum(t.payout) ? ru(t.payout,1)+'%' : mdash}</span></div>
        <div><span class="lbl">Статус</span>${statusChip}</div>
      </div>
      <details data-i="${i}"><summary>Оценка, динамика и факторы</summary>
        <div class="card-detail" style="margin-top:8px"></div>
      </details>
    </div>`;
  }).join('') : '<div class="empty">Ничего не найдено</div>';
  // ленивый рендер деталей карточки по первому открытию (236×графики — не строим заранее)
  el.querySelectorAll('details[data-i]').forEach((d) => d.addEventListener('toggle', function () {
    if (!this.open) return;
    const box = this.querySelector('.card-detail');
    if (!box || box.dataset.filled) return;
    const t = VIEW[+this.dataset.i];
    box.innerHTML = stockDetailSummaryHTML(t) + dividendMetricsHTML(t) + valuationHTML(t.valuation) + sectorPercentilesHTML(t) + fundamentalsOrHistoryHTML(t) + shapHTML(t) + detailKV(t);
    box.dataset.filled = '1';
    wireCharts(box);
  }));
}

// ── экспорт CSV (RU Excel: ; разделитель, запятая-десятичная, BOM) ──
function exportCSV() {
  const cols = [
    ['ticker', 'Тикер'], ['name', 'Название'], ['sector', 'Отрасль'],
    ['stability_score', 'Устойчивость'], ['cut_risk', 'Риск невыплаты'],
    ['dividend_forecast', 'Прогноз дивиденда, ₽'], ['payout', 'Payout, %'],
    ['dividend_yield_expected', 'Доходность ожидаемая, %'],
    ['dividend_yield_if_paid', 'Доходность при выплате, %'],
    ['price', 'Цена, ₽'], ['status', 'Статус'],
  ];
  const cell = (v) => {
    if (typeof v === 'number') return ru(v, 4).replace(/ /g, '');
    return '"' + String(v).replace(/"/g, '""') + '"';
  };
  const lines = [cols.map((c) => c[1]).join(';')];
  VIEW.forEach((t) => lines.push(cols.map((c) => cell(t[c[0]])).join(';')));
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dividend_forecast_rf.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ══════════ Конструктор портфеля ══════════
const NET_OF_TAX = 0.87;              // ×(1−НДФЛ 13%) — доходность «на руки»
const PF_MIN_MCAP = 5000;            // млн ₽ (5 млрд): лёгкий liquidity-floor
const PF_MIN_ADV = 10e6;             // ₽/день: ADV-фильтр — отсечь нетендерные (стоячие цены → ложный low-vol в оптимизаторе)
const FACTOR_BACKTEST = {            // статы из ВКР-бэктеста (results/), как пруф доверия
  quality: { label: 'Quality (Barra, 3 дескриптора: ROE / стабильность прибыли / леверидж) · бэктест ВКР 2012–2025: CAGR 11,8%, Sharpe 0,20; в 2019–25 фактор ослаб — историческая справка, не гарантия' },
  momentum: { label: 'Momentum (WML 12-1, ТОЛЬКО ЛОНГ top-N) · бэктест ВКР 2012–2025: +2,2%/год избыточной доходности над рынком (t≈0,3 — статистически незначима); единственный фактор, исторически работавший на РФ, но слабо. Ребаланс месячный.' },
  optmv: { label: 'Робастная оптимизация: минимум дисперсии портфеля по ковариации ВСЕХ бумаг (усадка ковариации к диагонали + box-ограничения) — портфельная теория, не факторный бэктест' },
  optrp: { label: 'Risk-parity: равный риск-вклад каждой бумаги (ковариация всех бумаг с усадкой) — не факторный бэктест' },
  optiv: { label: 'Inverse-volatility: вес ∝ 1/волатильность — простая робастная диверсификация' },
  optms: { label: 'Макс-Шарп (tangency, w∝Σ⁻¹μ): СВЯЗЫВАЕТ фактор и риск — ожидаемая доходность μ ∝ Quality-фактор (Barra-3), риск из ковариации. Тилт в качественные имена с хорошим risk/return, не голый min-var' },
};
const REBALANCE = {            // рекомендуемая частота ребаланса по стратегии
  quality: 'годовой (после годовых отчётов, как в ВКР — май)',
  momentum: 'месячный (фактор быстро затухает)',
  optmv: 'квартальный (ковариация медленная)', optms: 'квартальный (ковариация медленная)',
  optrp: 'квартальный (ковариация медленная)', optiv: 'квартальный (ковариация медленная)',
};

// верная 3-дескрипторная Barra Quality (ROE / стабильность прибыли / леверидж; винзор+z+сектор-нейтрализация в build_valuations)
function qualityScore(t) {
  return isNum(t.quality_barra) ? t.quality_barra : null;
}

function eligibleForPortfolio(t) {
  if (t.status !== 'ok' || !isNum(t.price) || t.price <= 0) return false;
  if (t.verdict && t.verdict.unreliable) return false;
  if (isNum(t.mcap) && t.mcap < PF_MIN_MCAP) return false;   // ND по mcap не отсекаем
  if (isNum(t.adv) && t.adv < PF_MIN_ADV) return false;      // нетендерные — вон (ND по adv не отсекаем)
  return true;
}

// проекция весов под лимиты. Секторный кап — best-effort (итеративно), затем
// индивидуальный — ГАРАНТИЯ через water-filling (maxW≥1/N всегда выполнимо).
function capWeights(items, maxW, secCap) {
  for (let k = 0; k < 60; k++) {            // 1. секторный кап
    let changed = false;
    const bySec = {};
    items.forEach((it) => { bySec[it.sector] = (bySec[it.sector] || 0) + it.w; });
    for (const sec in bySec) {
      if (bySec[sec] > secCap + 1e-9) {
        const removed = bySec[sec] - secCap, scale = secCap / bySec[sec];
        items.filter((it) => it.sector === sec).forEach((it) => { it.w *= scale; });
        const others = items.filter((it) => it.sector !== sec), os = others.reduce((s, it) => s + it.w, 0);
        if (os > 1e-12) { others.forEach((it) => { it.w += removed * it.w / os; }); changed = true; }
      }
    }
    if (!changed) break;
  }
  for (let k = 0; k < 200; k++) {           // 2. индивидуальный кап (water-filling по остатку до maxW)
    let excess = 0;
    items.forEach((it) => { if (it.w > maxW + 1e-12) { excess += it.w - maxW; it.w = maxW; } });
    if (excess < 1e-12) break;
    const bySec = {};
    items.forEach((it) => { bySec[it.sector] = (bySec[it.sector] || 0) + it.w; });
    // излишек льём преимущественно в бумаги секторов с запасом до secCap (чтобы не раздувать капнутый сектор)
    let room = items.filter((it) => it.w < maxW - 1e-12 && bySec[it.sector] < secCap - 1e-9);
    let rs = room.reduce((s, it) => s + (maxW - it.w), 0);
    if (rs < 1e-12) { room = items.filter((it) => it.w < maxW - 1e-12); rs = room.reduce((s, it) => s + (maxW - it.w), 0); }
    if (rs < 1e-12) break;
    room.forEach((it) => { it.w += excess * (maxW - it.w) / rs; });
  }
  const tot = items.reduce((s, it) => s + it.w, 0) || 1;
  items.forEach((it) => { it.w /= tot; });
}

function buildPortfolio(method, opts) {
  if (method.startsWith('opt')) return buildOptimized(method, opts);
  const uni = DATA.tickers.filter(eligibleForPortfolio);
  const scoreFn = method === 'momentum' ? ((t) => (isNum(t.mom_score) ? t.mom_score : null)) : qualityScore;
  const scored = uni.map((t) => ({ t, score: scoreFn(t) })).filter((x) => x.score != null);
  if (scored.length < 3) return null;
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, opts.n);
  const vols = top.map((x) => x.t.vol_ann).filter(isNum).sort((a, b) => a - b);   // для дозаполнения inverse-vol
  const medVol = vols.length ? vols[Math.floor(vols.length / 2)] : 0.3;
  const ss = top.map((x) => x.score), smin = Math.min(...ss), srange = (Math.max(...ss) - smin) || 1;  // диапазон для score-weight
  const items = top.map((x) => {
    let w = 1;
    if (opts.weight === 'score') w = (x.score - smin) + 0.15 * srange;   // ∝ фактору (сдвиг в плюс; низший ~15% шага, не ноль)
    else if (opts.weight === 'mcap') w = isNum(x.t.mcap) ? x.t.mcap : 1;
    else if (opts.weight === 'invvol') w = 1 / (isNum(x.t.vol_ann) && x.t.vol_ann > 0 ? x.t.vol_ann : medVol);
    return { ticker: x.t.ticker, name: x.t.name, sector: x.t.sector || ND, t: x.t, score: x.score, w };
  });
  const tot0 = items.reduce((s, it) => s + it.w, 0) || 1;
  items.forEach((it) => { it.w /= tot0; });
  capWeights(items, Math.max(opts.cap / 100, 1 / items.length), opts.seccap / 100);
  items.sort((a, b) => b.w - a.w);
  return items;
}

// ── робастный оптимизатор (ковариация из returns.json, весь eligible-универсум) ──
const OPT_WINDOW = 60;         // окно месяцев для ковариации
const OPT_SHRINK = 0.2;        // усадка ковариации к диагонали (Ledoit-Wolf-lite) → робастность
const OPT_VOLFLOOR = (0.12 ** 2) / 12;   // пол месячной дисперсии (≈12% год.) — чтобы min-var не эксплуатировал
                                          // занижённую волатильность неликвида (застывшие цены)

function covMatrix(mat) {       // mat: N имён × T месяцев → выборочная ковариация NxN
  const N = mat.length, T = mat[0].length;
  const mean = mat.map((row) => row.reduce((a, b) => a + b, 0) / T);
  const cov = Array.from({ length: N }, () => new Array(N).fill(0));
  for (let i = 0; i < N; i++) {
    for (let j = i; j < N; j++) {
      let s = 0;
      for (let t = 0; t < T; t++) s += (mat[i][t] - mean[i]) * (mat[j][t] - mean[j]);
      cov[i][j] = cov[j][i] = s / T;
    }
  }
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) if (i !== j) cov[i][j] *= (1 - OPT_SHRINK);  // усадка
  for (let i = 0; i < N; i++) cov[i][i] = Math.max(cov[i][i], OPT_VOLFLOOR);   // vol-floor против неликвид-артефакта
  return cov;
}
function _sigma(cov, w) { return w.map((_, i) => { let s = 0; for (let j = 0; j < w.length; j++) s += cov[i][j] * w[j]; return s; }); }
function invVolWeights(cov) {
  const w = cov.map((_, i) => 1 / Math.sqrt(Math.max(cov[i][i], 1e-9)));
  const s = w.reduce((a, b) => a + b, 0); return w.map((x) => x / s);
}
function riskParity(cov) {      // фикс-точка w_i ∝ 1/MRC_i (MRC=(Σw)_i) → равный риск-вклад
  const N = cov.length; let w = new Array(N).fill(1 / N);
  for (let k = 0; k < 400; k++) {
    const mrc = _sigma(cov, w);
    let s = 0; const nw = mrc.map((mi) => { const v = 1 / Math.max(mi, 1e-12); s += v; return v; });
    w = nw.map((x) => x / s);
  }
  return w;
}
function _projSimplex(v) {      // КОРРЕКТНАЯ евклидова проекция на {w≥0, Σw=1} (Duchi et al. 2008)
  const u = [...v].sort((a, b) => b - a);
  let css = 0, theta = 0;
  for (let i = 0; i < u.length; i++) { css += u[i]; const t = (css - 1) / (i + 1); if (u[i] - t > 0) theta = t; }
  return v.map((x) => Math.max(x - theta, 0));
}
function minVariance(cov) {     // min wᵀΣw s.t. Σw=1, w≥0 (проективный градиент; box/сектор — позже capWeights)
  const N = cov.length;
  let L = 1e-9; for (let i = 0; i < N; i++) { let s = 0; for (let j = 0; j < N; j++) s += Math.abs(cov[i][j]); L = Math.max(L, s); }
  const eta = 1 / L;            // шаг < 2/λmax (граница Гершгорина) → устойчивая сходимость
  let w = new Array(N).fill(1 / N);
  for (let k = 0; k < 600; k++) { const g = _sigma(cov, w); for (let i = 0; i < N; i++) w[i] -= eta * g[i]; w = _projSimplex(w); }
  return w;
}
function _solve(A, b) {         // решение СЛУ Ax=b (Гаусс с частичным пивотом)
  const n = A.length, M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const piv = M[c][c] || 1e-12;
    for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / piv; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
  }
  return M.map((r, i) => r[n] / (M[i][i] || 1e-12));
}
function maxSharpe(cov, mu) {   // tangency long-only: w ∝ Σ⁻¹μ, клип отриц., ренорм. μ=фактор как proxy ожид. доходности
  const x = _solve(cov, mu);
  const w = x.map((v) => Math.max(v, 0));
  const s = w.reduce((a, b) => a + b, 0);
  return s > 1e-9 ? w.map((v) => v / s) : cov.map(() => 1 / cov.length);
}

function buildOptimized(method, opts) {
  if (!PF_RETURNS || !PF_RETURNS.months || !PF_RETURNS.months.length) return null;
  const months = PF_RETURNS.months, R = PF_RETURNS.data;
  const i0 = Math.max(0, months.length - OPT_WINDOW), span = months.length - i0;
  const cand = DATA.tickers.filter(eligibleForPortfolio).filter((t) => {
    const a = R[t.ticker]; if (!a) return false;
    let c = 0; for (let j = i0; j < months.length; j++) if (isNum(a[j])) c++;
    return c >= span * 0.8;                          // ≥80% истории в окне
  });
  if (cand.length < 5) return null;
  const mat = cand.map((t) => { const a = R[t.ticker]; const row = []; for (let j = i0; j < months.length; j++) { const r = isNum(a[j]) ? a[j] : 0; row.push(Math.max(-RET_WINSOR, Math.min(RET_WINSOR, r))); } return row; });
  const cov = covMatrix(mat);
  let w;
  if (method === 'optiv') w = invVolWeights(cov);
  else if (method === 'optrp') w = riskParity(cov);
  else if (method === 'optms') w = maxSharpe(cov, cand.map((t) => (isNum(t.quality_barra) ? t.quality_barra : 0)));  // фактор-тилт
  else w = minVariance(cov);
  let items = cand.map((t, i) => ({ ticker: t.ticker, name: t.name, sector: t.sector || ND, t, score: w[i], w: w[i] }));
  items.sort((a, b) => b.w - a.w);
  if (opts.n && items.length > opts.n) {             // оптимизация видит ВЕСЬ универсум, держим top-N по весу
    items = items.slice(0, opts.n);
    const s = items.reduce((x, it) => x + it.w, 0) || 1; items.forEach((it) => { it.w /= s; });
  }
  capWeights(items, Math.max(opts.cap / 100, 1 / items.length), opts.seccap / 100);
  items.sort((a, b) => b.w - a.w);
  return items;
}

function portfolioMetrics(items, capital) {
  let gy = 0, stab = 0, wsum = 0;
  const sec = {};
  items.forEach((it) => {
    const y = isNum(it.t.dividend_yield_expected) ? it.t.dividend_yield_expected
      : (isNum(it.t.dividend_yield_if_paid) ? it.t.dividend_yield_if_paid : null);
    if (y != null) { gy += it.w * y; wsum += it.w; }
    if (isNum(it.t.stability_score)) stab += it.w * it.t.stability_score;
    sec[it.sector] = (sec[it.sector] || 0) + it.w;
  });
  const grossY = wsum ? gy / wsum : null;            // на покрытый вес
  const netY = grossY != null ? grossY * NET_OF_TAX : null;
  return {
    grossY, netY, stability: stab,
    incomeNet: (netY != null && capital) ? capital * netY / 100 : null,
    sectors: Object.entries(sec).sort((a, b) => b[1] - a[1]),
  };
}

// ── риск-метрики корзины (исторический ряд месячных доходностей, ленивая подгрузка) ──
// time-varying безрисковая: средняя ключевая ставка ЦБ по годам (а не одна цифра — период 2019-26 ставка гуляла 4-21%)
const RF_BY_YEAR = { 2019: 0.074, 2020: 0.051, 2021: 0.058, 2022: 0.106, 2023: 0.095, 2024: 0.175, 2025: 0.19, 2026: 0.165 };
const RET_WINSOR = 0.40;       // винзоризация месячных доходностей ±40% — гасит артефакт закрытия MOEX (фев-мар 2022)
PF_RETURNS = null;
let PF_RET_LOADING = false;
function loadReturns(cb) {
  if (PF_RETURNS) { if (cb) cb(); return; }
  if (PF_RET_LOADING) return;
  PF_RET_LOADING = true;
  fetch('returns.json?t=' + Date.now(), { cache: 'no-store' })   // cache-bust: уникальный URL обходит любой кэш/404
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
    .then((j) => { PF_RETURNS = { months: (j && j.meta && j.meta.months) || [], data: (j && j.data) || {}, div: (j && j.div) || null, series_status: (j && j.meta && j.meta.series_status) || {} }; PF_RET_LOADING = false; if (cb) cb(); })   // + series_status (needs_adjustment) из meta
    .catch((e) => { console.error('[pf] returns.json не загрузился:', e); PF_RETURNS = { months: [], data: {}, failed: true }; PF_RET_LOADING = false; if (cb) cb(); });
}
function _pstdev(a) { const m = a.reduce((x, y) => x + y, 0) / a.length; return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / a.length); }

function portfolioRisk(items, grossYieldPct) {
  if (!PF_RETURNS || !PF_RETURNS.months || !PF_RETURNS.months.length) return null;
  const months = PF_RETURNS.months, R = PF_RETURNS.data;
  const DIV = PF_RETURNS.div;                                 // реальные месячные дивдоходности (история выплат MOEX)
  const useReal = DIV && Object.keys(DIV).length > 0;
  const ydrip = useReal ? 0 : (isNum(grossYieldPct) ? grossYieldPct : 0) / 100 / 12;   // фолбэк-drip только без реальных
  const port = [], excess = [];
  for (let j = 0; j < months.length; j++) {
    let num = 0, dnum = 0, wsum = 0;
    items.forEach((it) => {
      const r = (R[it.ticker] || [])[j];
      if (isNum(r)) { num += it.w * r; if (useReal) dnum += it.w * ((DIV[it.ticker] || [])[j] || 0); wsum += it.w; }
    });
    if (wsum > 0.5) {                                         // ≥50% веса покрыто историей
      let r = (num + dnum) / wsum + ydrip;                    // тотал = цена + РЕАЛЬНЫЙ дивиденд того месяца
      r = Math.max(-RET_WINSOR, Math.min(RET_WINSOR, r));    // винзор (2022-разрыв)
      port.push(r);
      const rf = (RF_BY_YEAR[+months[j].slice(0, 4)] || 0.10) / 12;   // безрисковая того месяца
      excess.push(r - rf);
    }
  }
  if (port.length < 24) return null;                         // мало истории для риск-метрик
  const n = port.length;
  const cagr = Math.pow(port.reduce((p, r) => p * (1 + r), 1), 12 / n) - 1;
  const vol = _pstdev(port) * Math.sqrt(12);
  const exAnn = excess.reduce((a, b) => a + b, 0) / n * 12;  // годовая избыточная доходность (vs time-varying rf)
  const dd = Math.sqrt(excess.reduce((s, e) => s + Math.min(e, 0) ** 2, 0) / n) * Math.sqrt(12);   // downside-дев (MAR=rf)
  let eq = 1, peak = 1, mdd = 0; const curve = [];
  port.forEach((r) => { eq *= (1 + r); curve.push(eq); peak = Math.max(peak, eq); mdd = Math.min(mdd, eq / peak - 1); });
  return {
    months: n, cagr, vol, maxdd: mdd, equity: curve,
    sharpe: vol ? exAnn / vol : null,
    sortino: dd ? exAnn / dd : null,
    calmar: mdd < 0 ? cagr / Math.abs(mdd) : null,
  };
}

function riskPanelHTML(risk) {
  if (!risk) {
    if (!PF_RETURNS) return '<div class="pf-risk-note muted">Загрузка истории для риск-метрик…</div>';
    if (PF_RETURNS.failed) return '<div class="pf-risk-note muted">История (returns.json) не загрузилась — обнови страницу (Cmd+Shift+R).</div>';
    if (!PF_RETURNS.months || !PF_RETURNS.months.length) return '<div class="pf-risk-note muted">Историческая серия недоступна.</div>';
    return '<div class="pf-risk-note muted">Недостаточно истории для риск-метрик этой корзины.</div>';
  }
  const cell = (lbl, val, cls) => `<div class="pf-rc"><span>${lbl}</span><b class="${cls || ''}">${val}</b></div>`;
  const sg = (x) => (x >= 0.5 ? 'g' : (x < 0 ? 'r' : ''));
  return `<div class="pf-risk">
    <div class="pf-risk-head">⚠ Характеристики ТЕКУЩИХ бумаг корзины · ${risk.months} мес (НЕ бэктест стратегии)</div>
    <div class="pf-risk-grid">
      ${cell('CAGR (тотал)', ru(risk.cagr * 100, 1) + '%')}
      ${cell('Волатильность', ru(risk.vol * 100, 1) + '%')}
      ${cell('Sharpe', risk.sharpe != null ? ru(risk.sharpe, 2) : mdash, sg(risk.sharpe))}
      ${cell('Sortino', risk.sortino != null ? ru(risk.sortino, 2) : mdash, sg(risk.sortino))}
      ${cell('Max drawdown', ru(risk.maxdd * 100, 0) + '%', 'r')}
      ${cell('Calmar', risk.calmar != null ? ru(risk.calmar, 2) : mdash)}
    </div>
    <div class="pf-eq">${areaSpark(risk.equity, CH.teal, 'pfeq')}</div>
    <div class="pf-risk-note muted">Считается на исторических ценах ИМЕННО этих бумаг (survivorship: делистнутые/взорвавшиеся не учтены) — это НЕ бэктест стратегии с ребалансом, а характеристики сегодняшней корзины. Тотал-ретёрн = цена MOEX + ${(PF_RETURNS && PF_RETURNS.div && Object.keys(PF_RETURNS.div).length) ? 'РЕАЛЬНЫЕ дивиденды (история выплат MOEX по датам отсечки)' : 'тек. дивдоходность (drip, прибл.)'}; Sharpe/Sortino vs time-varying ключевой ставки ЦБ; месячные доходности винзоризованы ±40% (артефакт закрытия биржи 2022).</div>
  </div>`;
}

function renderPortfolio() {
  const out = document.getElementById('pf-out');
  if (!out) return;
  syncWeightControl();                               // синхронизируем доступность «Взвешивания»
  if (!PF_RETURNS) loadReturns(renderPortfolio);     // подгрузим историю и перерисуем с риск-метриками
  const method = document.getElementById('pf-method').value;
  const opts = {
    n: +document.getElementById('pf-n').value,
    weight: document.getElementById('pf-weight').value,
    cap: +document.getElementById('pf-cap').value,
    seccap: +document.getElementById('pf-seccap').value,
  };
  const capital = +document.getElementById('pf-capital').value || 0;
  const items = buildPortfolio(method, opts);
  if (!items) {
    let msg = 'Недостаточно подходящих бумаг для корзины.';
    if (method.startsWith('opt')) {
      if (!PF_RETURNS) msg = 'Загрузка истории…';
      else if (PF_RETURNS.failed) msg = 'Не удалось загрузить историю (returns.json) — обнови страницу (Cmd+Shift+R).';
      else if (!PF_RETURNS.months.length) msg = 'История недоступна.';
    }
    out.innerHTML = `<p class="muted" style="padding:8px">${msg}</p>`;
    return;
  }
  PF_LAST = { items, capital };
  const m = portfolioMetrics(items, capital);
  const risk = portfolioRisk(items, m.grossY);
  const bt = FACTOR_BACKTEST[method];
  const rows = items.map((it, i) => {
    const alloc = capital ? capital * it.w : null;
    const y = isNum(it.t.dividend_yield_expected) ? it.t.dividend_yield_expected : it.t.dividend_yield_if_paid;
    const inc = (alloc && isNum(y)) ? alloc * y / 100 * NET_OF_TAX : null;
    return `<tr><td class="left">${i + 1}</td><td class="left"><b>${esc(it.ticker)}</b> <span class="muted">${esc(it.sector)}</span></td>
      <td class="tnum">${ru(it.w * 100, 1)}%</td>
      <td class="tnum">${alloc != null ? fmtRub(Math.round(alloc)) : mdash}</td>
      <td class="tnum">${inc != null ? fmtRub(Math.round(inc)) : mdash}</td>
      <td class="left">${verdictChip(it.t.verdict, false)}</td></tr>`;
  }).join('');
  const secBars = m.sectors.map(([s, w]) =>
    `<div class="pf-secrow"><span>${esc(s)}</span><span class="pf-secbar"><i style="width:${(w * 100).toFixed(0)}%"></i></span><span class="tnum">${ru(w * 100, 0)}%</span></div>`).join('');
  out.innerHTML = `<div class="pf-summary">
      <div class="pf-card"><span class="lbl">Доходность (на руки)</span><b class="tnum">${m.netY != null ? ru(m.netY, 1) + '%' : mdash}</b><span class="muted">до НДФЛ ${m.grossY != null ? ru(m.grossY, 1) + '%' : '—'}</span></div>
      <div class="pf-card"><span class="lbl">Устойчивость портфеля</span><b class="tnum">${ru(m.stability * 100, 0)}%</b></div>
      <div class="pf-card"><span class="lbl">Доход в год (на руки)</span><b class="tnum">${m.incomeNet != null ? fmtRub(Math.round(m.incomeNet)) : mdash}</b><span class="muted">на ${capital ? fmtRub(capital) : '—'}</span></div>
      <div class="pf-card"><span class="lbl">Бумаг</span><b class="tnum">${items.length}</b></div>
    </div>
    ${bt ? `<div class="pf-bt muted">📈 ${esc(bt.label)}</div>` : ''}
    ${REBALANCE[method] ? `<div class="pf-reb muted">🔁 Рекомендуемый ребаланс: <b>${esc(REBALANCE[method])}</b></div>` : ''}
    ${riskPanelHTML(risk)}
    <div class="pf-grid"><div class="pf-holdings"><table class="pf-tbl"><thead><tr><th class="left">#</th><th class="left">Бумага</th><th>Вес</th><th>Сумма</th><th>Доход/год</th><th class="left">Вердикт</th></tr></thead><tbody>${rows}</tbody></table>
      <button class="btn" id="pf-csv" style="margin-top:10px">Экспорт корзины CSV</button></div>
      <div class="pf-sectors"><h4>Секторная концентрация</h4>${secBars}</div></div>`;
  document.getElementById('pf-csv').addEventListener('click', exportPortfolioCSV);
}

let PF_LAST = null;
function exportPortfolioCSV() {
  if (!PF_LAST) return;
  const lines = [['Тикер', 'Отрасль', 'Вес %', 'Сумма ₽', 'Вердикт'].join(';')];
  PF_LAST.items.forEach((it) => lines.push([it.ticker, '"' + String(it.sector).replace(/"/g, '""') + '"',
    ru(it.w * 100, 2).replace(/ /g, ''), PF_LAST.capital ? Math.round(PF_LAST.capital * it.w) : '',
    '"' + String((it.t.verdict || {}).label || '').replace(/"/g, '""') + '"'].join(';')));
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'portfolio.csv'; a.click(); URL.revokeObjectURL(a.href);
}

const MY_PORTFOLIO_STORAGE_KEY = 'dividendFactorStrategies.myPortfolio.v1';
const MY_PORTFOLIO_SAMPLE = 'SBER; 100; 310\nLKOH; 5; 6800\nMOEX; 50; 210\nNVTK; 8; 1250\nPHOR; 3; 6200';

function parseMyPortfolioInput(text) {
  return String(text || '').split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => {
      const parts = line.split(/[;,\t ]+/).map((x) => x.trim()).filter(Boolean);
      const ticker = String(parts[0] || '').toUpperCase().replace(/[^A-Z0-9._-]/g, '');
      const qty = Number(String(parts[1] || '').replace(',', '.'));
      const avg = Number(String(parts[2] || '').replace(',', '.'));
      return { ticker, quantity: qty, avg_price: avg };
    })
    .filter((p) => p.ticker && isFinite(p.quantity) && p.quantity > 0 && isFinite(p.avg_price) && p.avg_price >= 0);
}

function myPortfolioText(rows) {
  return (rows || []).map((p) => `${p.ticker}; ${p.quantity}; ${p.avg_price}`).join('\n');
}

function myPortfolioLoad() {
  try {
    const raw = localStorage.getItem(MY_PORTFOLIO_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_e) {
    return [];
  }
}

function myPortfolioSave(rows) {
  try {
    localStorage.setItem(MY_PORTFOLIO_STORAGE_KEY, JSON.stringify(rows || []));
  } catch (_e) {
    // localStorage может быть отключен; расчет все равно покажем в текущей сессии.
  }
}

function myPortfolioTickerMap() {
  const map = {};
  (DATA && DATA.tickers ? DATA.tickers : []).forEach((t) => { map[t.ticker] = t; });
  return map;
}

function myPortfolioRfrPct() {
  if (MARLAMOV && MARLAMOV.meta && isNum(MARLAMOV.meta.rfr)) return MARLAMOV.meta.rfr * 100;
  if (DATA && DATA.meta && isNum(DATA.meta.rf_ofz)) return DATA.meta.rf_ofz * 100;
  return null;
}

function myPortfolioDataQuality(ticker) {
  const rows = SITE_FINANCIALS && SITE_FINANCIALS.rows ? SITE_FINANCIALS.rows.filter((r) => r.ticker === ticker) : [];
  if (!rows.length) return { status: 'missing', label: 'нет фундаментального слоя', score: 40 };
  const statuses = new Set(rows.map((r) => r.source_status).filter(Boolean));
  const scores = rows.map((r) => r.quality_score).filter(isNum);
  const score = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 70;
  if (statuses.has('Conflict')) return { status: 'conflict', label: 'есть конфликт источников', score };
  if (statuses.has('Official IFRS')) return { status: 'official', label: 'есть official IFRS слой', score };
  return { status: 'smartlab', label: 'SmartLab fallback', score };
}

function myPortfolioEnrich(rows) {
  const map = myPortfolioTickerMap();
  return (rows || []).map((p) => {
    const t = map[p.ticker] || null;
    const currentPrice = t && isNum(t.price) ? t.price : null;
    const value = currentPrice != null ? currentPrice * p.quantity : p.avg_price * p.quantity;
    const cost = p.avg_price * p.quantity;
    const y = t && isNum(t.dividend_yield_expected) ? t.dividend_yield_expected
      : (t && isNum(t.dividend_yield_if_paid) ? t.dividend_yield_if_paid : null);
    return {
      ...p,
      t,
      sector: t ? (t.sector || ND) : 'нет в покрытии',
      current_price: currentPrice,
      value,
      cost,
      pnl_pct: currentPrice != null && p.avg_price > 0 ? currentPrice / p.avg_price - 1 : null,
      dividend_yield: y,
      data_quality: myPortfolioDataQuality(p.ticker),
    };
  });
}

function myPortfolioMetrics(rows) {
  const positions = myPortfolioEnrich(rows);
  const total = positions.reduce((s, p) => s + (isNum(p.value) ? p.value : 0), 0);
  positions.forEach((p) => { p.weight = total > 0 ? p.value / total : 0; });
  const known = positions.filter((p) => p.t);
  const sectors = {};
  positions.forEach((p) => { sectors[p.sector] = (sectors[p.sector] || 0) + p.weight; });
  const topWeight = positions.reduce((m, p) => Math.max(m, p.weight || 0), 0);
  const topSector = Object.entries(sectors).sort((a, b) => b[1] - a[1])[0] || ['—', 0];
  const grossYield = positions.reduce((s, p) => s + (isNum(p.dividend_yield) ? p.weight * p.dividend_yield : 0), 0);
  const netYield = grossYield * NET_OF_TAX;
  const stability = positions.reduce((s, p) => s + (p.t && isNum(p.t.stability_score) ? p.weight * p.t.stability_score : 0), 0);
  const conflictWeight = positions.reduce((s, p) => s + (p.data_quality.status === 'conflict' ? p.weight : 0), 0);
  const missingWeight = positions.reduce((s, p) => s + (!p.t ? p.weight : 0), 0);
  const riskWeight = positions.reduce((s, p) => s + (p.t && p.t.verdict && p.t.verdict.color === 'risk' ? p.weight : 0), 0);
  const rfr = myPortfolioRfrPct();
  const stress = SAW_DATA ? marketStressFromSaw(SAW_DATA) : null;
  let score = 100;
  score -= Math.max(0, topWeight - 0.20) * 120;
  score -= Math.max(0, topSector[1] - 0.40) * 90;
  score -= riskWeight * 35;
  score -= conflictWeight * 20;
  score -= missingWeight * 35;
  if (rfr != null && grossYield < rfr) score -= Math.min(20, (rfr - grossYield) * 0.8);
  if (stress && stress.score >= 70) score -= 8;
  score = Math.max(0, Math.min(100, Math.round(score)));
  return {
    positions,
    total,
    known_count: known.length,
    gross_yield: grossYield,
    net_yield: netYield,
    income_net: total * netYield / 100,
    stability,
    sectors: Object.entries(sectors).sort((a, b) => b[1] - a[1]),
    top_weight: topWeight,
    top_sector: topSector,
    conflict_weight: conflictWeight,
    missing_weight: missingWeight,
    risk_weight: riskWeight,
    rfr,
    spread_to_rfr: rfr != null ? grossYield - rfr : null,
    stress,
    score,
  };
}

function myPortfolioActions(m) {
  const actions = [];
  const add = (tone, title, body) => actions.push({ tone, title, body });
  if (!m.positions.length) return actions;
  if (m.top_weight > 0.25) {
    const p = m.positions.slice().sort((a, b) => b.weight - a.weight)[0];
    add('risk', 'Концентрация в одной бумаге', `${p.ticker}: ${ru(p.weight * 100, 0)}% портфеля. Проверь лимит на эмитента.`);
  }
  if (m.top_sector[1] > 0.40) {
    add('warn', 'Секторная концентрация', `${m.top_sector[0]}: ${ru(m.top_sector[1] * 100, 0)}% портфеля.`);
  }
  if (m.spread_to_rfr != null && m.spread_to_rfr < 0) {
    add('risk', 'Дивидендный case слабее RFR', `Ожидаемая gross yield ниже RFR на ${ru(Math.abs(m.spread_to_rfr), 1)} п.п.`);
  }
  m.positions.filter((p) => p.t && p.t.verdict && p.t.verdict.color === 'risk').slice(0, 4)
    .forEach((p) => add('risk', `Проверить ${p.ticker}`, `${p.t.verdict.label || 'risk verdict'} · вес ${ru(p.weight * 100, 1)}%.`));
  m.positions.filter((p) => p.data_quality.status === 'conflict').slice(0, 4)
    .forEach((p) => add('warn', `Данные ${p.ticker} требуют сверки`, 'В финансовом слое есть конфликт SmartLab / official IFRS.'));
  m.positions.filter((p) => !p.t).slice(0, 4)
    .forEach((p) => add('warn', `Нет покрытия ${p.ticker}`, 'Тикер не найден в текущем data.json; вес считается по средней цене.'));
  if (m.stress && m.stress.score >= 70) {
    add('warn', 'Рыночное напряжение высокое', `Market stress ${m.stress.score}/100: новые действия лучше сверять с ликвидностью и горизонтом.`);
  }
  if (!actions.length) add('good', 'Критичных проверок нет', 'Портфель выглядит сбалансированно по текущим правилам MVP.');
  return actions.slice(0, 8);
}

let PFX_STATE = null;              // последний расчёт (для кнопок экспорт/копирование)

function renderMyPortfolio() {
  const out = document.getElementById('mp-out');
  const input = document.getElementById('mp-input');
  if (!out || !input) return;
  if (!DATA) { out.innerHTML = '<div class="mp-empty muted">Загрузка data.json…</div>'; return; }
  const parsed = pfxParseValidate(input.value);
  const rows = parsed.rows;
  if (!rows.length) {
    out.innerHTML = `<div class="mp-empty mp-empty-rich">
      <b>Портфель пока пуст</b>
      <span>Вставьте позиции строками или загрузите пример. Формат: <code>тикер; количество; средняя цена</code></span>
      <em>Расчёт локальный, в браузере: файл никуда не отправляется. Не ИИР.</em>
    </div>`;
    return;
  }
  // риск-движок требует returns.json + MCFTR (marketsaw) + RFR (marlamov) — грузим лениво
  if (!PF_RETURNS && typeof loadReturns === 'function') { loadReturns(() => renderMyPortfolio()); }
  if (!SAW_DATA && typeof loadMarketSaw === 'function') { loadMarketSaw(() => renderMyPortfolio()); }
  if (!MARLAMOV && typeof loadMarlamov === 'function') { loadMarlamov(() => renderMyPortfolio()); }
  if (!NEWS && typeof loadNews === 'function') { loadNews(() => renderMyPortfolio()); }   // P2: новости по тикерам
  if (!BONDS && typeof loadBonds === 'function') { loadBonds(() => renderMyPortfolio()); }   // P4: ставка/облигации
  const c = pfxCompute(rows);
  c._warnings = (parsed.warnings || []).slice();
  // пост-enrich предупреждения по качеству
  const anom = c.positions.filter((p) => p._anomaly).map((p) => p.ticker);
  if (anom.length) c._warnings.push({ tone: 'warn', msg: `${anom.join(', ')}: найден split-like разрыв в истории цены (needs_adjustment). Риск-метрики по бумаге исключены до корректировки ряда.` });
  const noModel = c.positions.filter((p) => p.t && p.t.status === 'no_model_coverage').map((p) => p.ticker);
  if (noModel.length) c._warnings.push({ tone: 'warn', msg: `${noModel.join(', ')}: цена есть (рынок MOEX), но нет модельного покрытия и чистой истории — бумага исключена из VaR/CVaR и дивидендной модели.` });
  if (c._divSuspect && c._divSuspect.length) c._warnings.push({ tone: 'warn', msg: `${c._divSuspect.join(', ')}: дивиденд в данных выглядит завышенным (возможно, до сплита) — исключён из ожидаемого дивпотока, требует проверки.` });
  const shortHist = c.positions.filter((p) => p._tr && p._tr.length < 24).map((p) => p.ticker);
  if (shortHist.length) c._warnings.push({ tone: 'warn', msg: `Короткая история (<24 мес): ${shortHist.join(', ')} — риск-метрики low confidence` });
  if (c.pf && c.pf.covered < 0.9) c._warnings.push({ tone: 'warn', msg: `Risk metrics рассчитаны по ${Math.round(c.pf.covered * 100)}% веса портфеля. Бумаги без чистой истории исключены из VaR/CVaR.` });
  if (!c.bench) c._warnings.push({ tone: 'risk', msg: 'MCFTR не выровнен — alpha/beta/tracking error/active VaR недоступны' });
  if (!c.rf.ok) c._warnings.push({ tone: 'warn', msg: 'RFR недоступна — Sharpe/Sortino/alpha считаются без безрисковой ставки' });
  PFX_STATE = c;
  myPortfolioSave(rows);
  out.innerHTML = pfxRenderHTML(c);
  pfxDrawCharts(c);
  pfxWireButtons(c);
}

// ── формат-хелперы (frac = доля; PU = уже проценты) ──────────────────────────
const PP = (frac, d) => (isNum(frac) ? ((frac >= 0 ? '+' : '') + ru(frac * 100, d == null ? 1 : d) + '%') : mdash);
const PN = (frac, d) => (isNum(frac) ? (ru(frac * 100, d == null ? 1 : d) + '%') : mdash);   // без знака
const PU = (v, d) => (isNum(v) ? ru(v, d == null ? 2 : d) : mdash);
const NA = '<span class="pfx-na">недоступно</span>';
function pfxKpi(label, value, sub, tone) {
  return `<div class="pfx-kpi${tone ? ' pfx-' + tone : ''}"><span class="pfx-kl">${label}</span><b class="pfx-kv">${value}</b>${sub ? `<em class="pfx-ks">${sub}</em>` : ''}</div>`;
}
function pfxConfBadge(level) {
  const m = { high: ['высокая', 'good'], medium: ['средняя', 'neut'], low: ['низкая', 'warn'], very_low: ['очень низкая', 'risk'], unavailable: ['недоступно', 'risk'] };
  const v = m[level] || m.medium; return `<span class="pfx-conf pfx-${v[1]}">confidence: ${v[0]}</span>`;
}
function pfxDetails(title, sub, inner, open) {
  return `<details class="mapwrap pfx-mod"${open ? ' open' : ''}><summary>${esc(title)}${sub ? ` <span class="muted">${esc(sub)}</span>` : ''}</summary><div class="pfx-mod-body">${inner}</div></details>`;
}

// пересчёт метрик для набора весов (для сравнения current vs suggested)
function pfxScenarioMetrics(tickers, weights, bench, rf) {
  const series = [];
  const trs = tickers.map((tk) => pfxTickerTotalReturns(tk));
  if (trs.some((x) => !x)) return null;
  const minLen = Math.min(...trs.map((x) => x.length));
  for (let m = 0; m < minLen; m++) { let r = 0; trs.forEach((tr, i) => { r += weights[i] * tr[tr.length - minLen + m]; }); series.push(r); }
  const perf = pfxPerf(series, rf.monthly);
  const vaR = pfxVaR(series);
  const capm = bench ? pfxCapm(series, bench, rf.monthly) : null;
  const top1 = Math.max(...weights), top3 = weights.slice().sort((a, b) => b - a).slice(0, 3).reduce((a, b) => a + b, 0);
  return { perf, vaR, capm, top1, top3, series };
}

// ── главный рендер ───────────────────────────────────────────────────────────
function pfxRenderHTML(c) {
  const diag = pfxDiagnosis(c);
  const benchPerf = (c.bench && c.perf) ? pfxPerf(c.bench, c.rf.monthly) : null;
  c._rb = pfxRateBond(c);   // P4: связка со ставкой/облигациями (для модуля и графика)
  let html = '';

  // 0. Заголовок + дисклеймеры
  html += `<div class="pfx-top">
    <div class="pfx-type pfx-${c.cls.tone}">${esc(c.cls.type)}</div>
    <div class="pfx-diag">${esc(diag)}</div>
    <div class="pfx-btns">
      <button class="btn btn-secondary" id="pfx-copy">Скопировать отчёт</button>
      <button class="btn btn-secondary" id="pfx-export">Экспорт диагностики CSV</button>
    </div>
  </div>
  <div class="pfx-disc">Backfilled-портфель по <b>текущему составу</b> и историческим месячным ретёрнам — это НЕ фактическая история ваших сделок. Данные месячные (${c.pf ? c.pf.n + ' мес' : 'н/д'}); дневной риск не оценивается. Не индивидуальная инвестиционная рекомендация.</div>`;
  if (c._warnings && c._warnings.length) {
    html += `<div class="pfx-warns-panel">${c._warnings.map((w) => `<div class="pfx-wline pfx-w-${w.tone}">${esc(w.msg)}</div>`).join('')}</div>`;
  }

  // P2: Daily Portfolio Brief — «сегодня важно для портфеля» (герой-блок)
  const brief = pfxDailyBrief(c);
  if (brief.length) {
    html += `<div class="pfx-brief"><div class="pfx-brief-head">📌 Сегодня важно для портфеля</div>${
      brief.map((b) => `<div class="pfx-brief-item pfx-bi-${b.tone}"><span class="pfx-bi-dot"></span><span>${esc(b.text)}</span></div>`).join('')}
      <div class="pfx-brief-foot muted">Синтез по доступным данным (месячные ретёрны, новости, фаза MCFTR, RFR). Дат дивотсечек в наборе нет — см. раздел дивидендов. Не ИИР.</div></div>`;
  }

  // P1: три главных риска человеческим языком (сразу под диагнозом)
  const topRisks = pfxTopRisks(c);
  if (topRisks.length) {
    html += `<div class="pfx-toprisks"><div class="pfx-tr-head">Три главных риска портфеля</div>${
      topRisks.map((r, i) => `<div class="pfx-tr pfx-tr-${r.tone}"><b>${i + 1}</b><span>${esc(r.text)}</span></div>`).join('')}</div>`;
  }

  // 1. Portfolio X-Ray — KPI grid
  const pnlAbs = c.total - c.cost, pnlPct = c.cost > 0 ? c.total / c.cost - 1 : null;
  const g = [];
  g.push(pfxKpi('Стоимость', rub0(c.total)));
  g.push(pfxKpi('Вложено (по средней)', rub0(c.cost)));
  g.push(pfxKpi('Нереализ. P&L', rub0(pnlAbs), PP(pnlPct), pnlAbs >= 0 ? 'good' : 'risk'));
  g.push(pfxKpi('Ожид. дивдоходность', PU(c.grossYield, 1) + '%', 'gross', 'neut'));
  g.push(pfxKpi('Ожид. дивдоход/год', c.div ? rub0(c.div.baseIncome) : NA, 'risk-adj ' + (c.div ? rub0(c.div.riskAdj) : '')));
  g.push(pfxKpi('Beta к MCFTR', c.capm && c.capm.ok ? PU(c.capm.beta, 2) : NA, c.capm && c.capm.ok ? pfxBetaBucket(c.capm.beta) : ''));
  g.push(pfxKpi('Alpha (ист., год)', c.capm && c.capm.ok ? PP(c.capm.alphaAnn) : NA));
  g.push(pfxKpi('Sharpe', c.perf && isNum(c.perf.sharpe) ? PU(c.perf.sharpe, 2) : NA, c.rf.ok ? '' : 'RFR н/д'));
  g.push(pfxKpi('Sortino', c.perf && isNum(c.perf.sortino) ? PU(c.perf.sortino, 2) : NA));
  g.push(pfxKpi('Calmar', c.perf && isNum(c.perf.calmar) ? PU(c.perf.calmar, 2) : NA));
  g.push(pfxKpi('Max Drawdown', c.perf ? PN(c.perf.mdd) : NA, c.perf && c.perf.recovery != null ? `восст. ${c.perf.recovery} мес` : '', 'risk'));
  g.push(pfxKpi('VaR 95% (мес)', c.vaR && c.vaR.ok ? PN(c.vaR.hist95) : NA, c.vaR && c.vaR.ok ? rub0(c.vaR.hist95 * c.total) : '', 'risk'));
  g.push(pfxKpi('CVaR 95% (мес)', c.vaR && c.vaR.ok ? PN(c.vaR.cvar95) : NA, c.vaR && c.vaR.ok ? rub0(c.vaR.cvar95 * c.total) : '', 'risk'));
  g.push(pfxKpi('Tracking Error', c.capm && c.capm.ok ? PN(c.capm.te) : NA));
  g.push(pfxKpi('Information Ratio', c.capm && c.capm.ok && isNum(c.capm.ir) ? PU(c.capm.ir, 2) : NA));
  g.push(pfxKpi('Downside Capture', c.capm && c.capm.ok && isNum(c.capm.dnCapture) ? PN(c.capm.dnCapture, 0) : NA));
  g.push(pfxKpi('Top-3 концентрация', PN(c.top3, 0), '', c.top3 > 0.6 ? 'risk' : c.top3 > 0.45 ? 'warn' : 'good'));
  g.push(pfxKpi('Эфф. число бумаг', PU(c.effN, 1), `из ${c.positions.length}`));
  g.push(pfxKpi('Data Quality', c.dq.score + '/100', '', c.dq.score >= 70 ? 'good' : c.dq.score >= 45 ? 'warn' : 'risk'));
  const rscore = pfxRiskScore(c);
  g.push(pfxKpi('Portfolio Risk Score', rscore.score + '/100', rscore.label, rscore.score >= 66 ? 'risk' : rscore.score >= 40 ? 'warn' : 'good'));
  html += pfxDetails('Portfolio X-Ray', '(верхняя диагностика портфеля)', `<div class="pfx-grid">${g.join('')}</div>`, true);

  // 2. Performance vs MCFTR
  html += pfxDetails('Performance vs MCFTR', '(total return, риск-adjusted)', pfxPerfHTML(c, benchPerf));

  // 3. Alpha / Beta
  html += pfxDetails('Alpha / Beta / Risk-adjusted', '(CAPM-регрессия к MCFTR)', pfxCapmHTML(c));

  // 4. Dynamic Risk Engine (VaR)
  html += pfxDetails('Динамический риск: VaR / CVaR', '(месячная база — дневной VaR недоступен)', pfxVaRHTML(c));

  // 5. Risk Budget
  html += pfxDetails('Risk Budget', '(вклад бумаг в риск, component VaR)', pfxRiskBudgetHTML(c));

  // 6. Dividend Stress Test
  html += pfxDetails('Дивидендный стресс-тест', '(base / conservative / stress / crisis + yield trap)', pfxDivHTML(c));

  // 6a. P4: Ставка и облигации против портфеля (Rate & Bond Reality Check)
  html += pfxDetails('Ставка и облигации против портфеля', '(премия к безриску · порог замещения · дюрация к ставке)', pfxRateBondHTML(c), (c._rb && c._rb.ok && c._rb.substituted));

  // 6b. Факторная диагностика (P1)
  html += pfxDetails('Факторная диагностика', '(экспозиции vs рынок + вывод)', pfxFactorsHTML(c));

  // 7. Bootstrap
  html += pfxDetails('Веер сценариев года', '(1000 виртуальных лет из вашей истории · не прогноз)', pfxBootHTML(c));

  // 8. Smart Rebalancer
  html += pfxDetails('Smart Rebalancer', '(Suggested Diagnostic Weights — не рекомендация)', pfxRebalHTML(c));

  // 9. Allocation
  html += pfxDetails('Allocation / Exposure', '(разрезы книги + лимиты)', pfxAllocHTML(c));

  // 10. Position Diagnostics
  html += pfxDetails('Position Diagnostics', '(по каждой бумаге)', pfxPosHTML(c));

  // 11. Investment Committee Memo
  const memo = pfxMemo(c).map(([h, b]) => `<div class="pfx-memo-block"><h4>${esc(h)}</h4><p>${esc(b)}</p></div>`).join('');
  html += pfxDetails('Investment Committee Memo', '(rule-based, тон аналитика)', `<div class="pfx-memo">${memo}</div>`);

  // 12. Data Quality
  html += pfxDetails('Data Quality Layer', '(confidence по бумагам)', pfxDQHTML(c));

  // 13. Methodology
  html += pfxDetails('Методология и предупреждения', '', pfxMethodHTML());
  return html;
}

function pfxDiagnosis(c) {
  const parts = [];
  parts.push(`Портфель классифицирован как «${c.cls.type}»`);
  if (c.capm && c.capm.ok) parts.push(`beta к MCFTR = ${ru(c.capm.beta, 2)}`);
  parts.push(`top-3 = ${ru(c.top3 * 100, 0)}% портфеля`);
  if (c.riskBudget && c.riskBudget.ok) parts.push(`главный risk driver — ${c.riskBudget.rows[0].ticker} (${ru(c.riskBudget.rows[0].share * 100, 0)}% риска)`);
  if (c.capm && c.capm.ok && isNum(c.capm.dnCapture)) parts.push(`downside capture = ${ru(c.capm.dnCapture * 100, 0)}%`);
  if (c.div && c.div.baseIncome > 0) { const cutShare = c.div.topRisk.reduce((s, it) => s + it.base * (it.cr || 0), 0) / c.div.baseIncome;
    if (cutShare > 0.2) parts.push(`~${ru(cutShare * 100, 0)}% дивпотока — бумаги с повышенным cut risk`); }
  return parts.join('; ') + '.';
}

// ── модуль 2: Performance vs MCFTR ───────────────────────────────────────────
function pfxPerfHTML(c, bp) {
  if (!c.perf) return `<div class="pfx-note">${NA}: недостаточно истории по бумагам портфеля для перформанс-метрик.</div>`;
  const p = c.perf;
  const row = (lbl, a, b) => `<tr><td class="left">${lbl}</td><td class="tnum">${a}</td><td class="tnum">${b}</td></tr>`;
  const warn = p.n < 24 ? `<div class="pfx-warn">История ${p.n} мес (&lt; 2 лет): годовые метрики нестабильны.</div>` : '';
  const covered = c.pf && c.pf.covered < 0.99 ? `<div class="pfx-warn">Риск-метрики покрывают ${ru(c.pf.covered * 100, 0)}% веса (бумаги без истории исключены).</div>` : '';
  const t = `<table class="pfx-tbl"><thead><tr><th class="left">Метрика</th><th>Портфель</th><th>MCFTR</th></tr></thead><tbody>
    ${row('Total return (окно)', PP(p.totalRet), bp ? PP(bp.totalRet) : NA)}
    ${row('CAGR', PP(p.cagr), bp ? PP(bp.cagr) : NA)}
    ${row('Ann. volatility', PN(p.volAnn), bp ? PN(bp.volAnn) : NA)}
    ${row('Sharpe', PU(p.sharpe, 2), bp ? PU(bp.sharpe, 2) : NA)}
    ${row('Sortino', PU(p.sortino, 2), bp ? PU(bp.sortino, 2) : NA)}
    ${row('Calmar', PU(p.calmar, 2), bp ? PU(bp.calmar, 2) : NA)}
    ${row('Max Drawdown', PN(p.mdd), bp ? PN(bp.mdd) : NA)}
    ${row('Восстановление, мес', p.recovery == null ? 'не восстановился' : p.recovery, bp && bp.recovery != null ? bp.recovery : '—')}
    ${row('Win months', PN(p.winPct, 0), bp ? PN(bp.winPct, 0) : NA)}
    ${row('Лучший / худший мес', PP(p.best) + ' / ' + PP(p.worst), bp ? PP(bp.best) + ' / ' + PP(bp.worst) : NA)}
    ${row('1M / 3M / 6M', [p.ret1m, p.ret3m, p.ret6m].map((x) => PP(x)).join(' / '), '')}
    ${row('1Y / 3Y', [p.ret1y, p.ret3y].map((x) => PP(x)).join(' / '), '')}
  </tbody></table>`;
  const rolling = p.n >= 18 ? `<div class="pfx-riskrow">
    <div class="pfx-chart-wrap"><canvas id="pfx-roll-ret"></canvas></div>
    <div class="pfx-chart-wrap"><canvas id="pfx-roll-vol"></canvas></div></div>` : '';
  return `${warn}${covered}<div class="pfx-2col">${t}<div class="pfx-charts">
    <div class="pfx-chart-wrap"><canvas id="pfx-equity"></canvas></div>
    <div class="pfx-chart-wrap"><canvas id="pfx-drawdown"></canvas></div></div></div>
    ${rolling}
    <div class="pfx-note muted">Total return = ценовой ретёрн + реальные месячные дивиденды (returns.json). MCFTR — индекс полной доходности, сопоставим корректно. Rolling — скользящее окно 12 мес.</div>`;
}

// ── модуль 3: Alpha / Beta ───────────────────────────────────────────────────
function pfxCapmHTML(c) {
  if (!c.capm || !c.capm.ok) return `<div class="pfx-note">${NA}: ${c.bench ? (c.capm && c.capm.reason || 'мало наблюдений') : 'нет выравнивания с MCFTR'} — alpha/beta не считаются.</div>`;
  const m = c.capm;
  const cell = (l, v) => `<div class="pfx-mc"><span>${l}</span><b>${v}</b></div>`;
  let interp;
  if (m.beta > 1.4) interp = 'high beta / leveraged-like: в падениях просадка сильнее рынка';
  else if (m.beta > 1.1) interp = 'aggressive: выше рыночного риска';
  else if (m.beta < 0.8) interp = 'defensive: ниже рыночного риска';
  else interp = 'market-like';
  let al;
  if (m.alphaAnn > 0 && isNum(m.ir) && m.ir > 0.5) al = 'положительная alpha при IR>0.5 — заметное активное отклонение (исторически)';
  else if (m.alphaAnn > 0 && m.r2 < 0.5) al = 'положительная alpha при низком R² — результат может быть idiosyncratic, осторожно';
  else if (m.alphaAnn < 0 && m.beta > 1.1) al = 'отрицательная alpha при высокой beta — слабый профиль риск/доходность';
  else al = m.alphaAnn >= 0 ? 'положительная историческая alpha' : 'отрицательная историческая alpha';
  return `<div class="pfx-mcgrid">
    ${cell('Beta', PU(m.beta, 2))}${cell('Alpha (год)', PP(m.alphaAnn))}${cell('t-stat alpha', isNum(m.tAlpha) ? PU(m.tAlpha, 2) : mdash)}
    ${cell('R²', PU(m.r2, 2))}${cell('Корреляция', PU(m.corr, 2))}${cell('Residual vol', PN(m.residVolAnn))}
    ${cell('Tracking Error', PN(m.te))}${cell('Information Ratio', isNum(m.ir) ? PU(m.ir, 2) : mdash)}${cell('Treynor', isNum(m.treynor) ? PP(m.treynor) : mdash)}
    ${cell('Upside capture', isNum(m.upCapture) ? PN(m.upCapture, 0) : mdash)}${cell('Downside capture', isNum(m.dnCapture) ? PN(m.dnCapture, 0) : mdash)}${cell('Bull / Bear (год)', PP(m.bull) + ' / ' + PP(m.bear))}
  </div>
  <div class="pfx-interp"><b>Beta:</b> ${esc(interp)}. <b>Alpha:</b> ${esc(al)}.</div>
  <div class="pfx-note muted">Alpha рассчитана исторически на ${m.n} мес и не является прогнозом будущей доходности.</div>`;
}

// ── модуль 4: VaR ────────────────────────────────────────────────────────────
function pfxVaRHTML(c) {
  if (!c.vaR || !c.vaR.ok) return `<div class="pfx-note">${NA}: недостаточно истории для VaR.</div>`;
  const v = c.vaR, T = c.total;
  const card = (lbl, frac) => `<div class="pfx-varcard"><span>${lbl}</span><b>${PN(frac)}</b><em>${rub0(frac * T)}</em></div>`;
  const cards = [
    card('Historical VaR 95%', v.hist95), card('Historical VaR 99%', v.hist99),
    card('Historical CVaR 95%', v.cvar95), card('Historical CVaR 99%', v.cvar99),
    card('Parametric Gaussian 95%', v.gauss95), card('Cornish-Fisher 95%', v.cf95),
  ].join('');
  const bt = c.backtest && c.backtest.ok
    ? `Backtest (rolling 24-мес VaR 95%): ${c.backtest.breaches} пробитий из ${c.backtest.obs} (${ru(c.backtest.freq * 100, 1)}% при ожидаемых 5%). ${c.backtest.freq > 0.09 ? 'VaR может недооценивать риск.' : c.backtest.freq < 0.02 ? 'VaR консервативен.' : 'В пределах ожидания.'} ${c.backtest.obs < 24 ? 'Мало наблюдений — low confidence.' : ''}`
    : 'Backtest: недостаточно истории.';
  // risk regime по drawdown + текущей vol
  let regime = 'Data Insufficient', rtone = 'neut';
  if (c.perf) {
    const curDD = c.perf.cum[c.perf.cum.length - 1] / Math.max(...c.perf.cum) - 1;
    if (curDD < -0.15) { regime = 'Stress Regime'; rtone = 'risk'; }
    else if (v.sd > 0 && Math.abs(v.hist95) > Math.abs(v.mu) + 2.2 * v.sd) { regime = 'High Risk'; rtone = 'risk'; }
    else if (curDD < -0.08) { regime = 'Elevated Risk'; rtone = 'warn'; }
    else { regime = 'Normal Risk'; rtone = 'good'; }
  }
  const activeVar = (c.bench && c.pf) ? pfxPercentile(c.pf.series.map((r, i) => r - c.bench[c.bench.length - c.pf.series.length + i]), 0.05) : null;
  return `<div class="pfx-varcards">${cards}</div>
    <div class="pfx-varmeta">
      <div class="pfx-regime pfx-${rtone}">Режим риска: <b>${regime}</b></div>
      ${pfxConfBadge(v.conf)}
      <span>Active VaR vs MCFTR 95%: <b>${activeVar != null ? PN(activeVar) : NA}</b></span>
      <span>skew ${PU(v.skew, 2)} · kurt ${PU(v.kurt, 2)}</span>
    </div>
    <div class="pfx-note">${esc(bt)}</div>
    ${v.conf === 'low' || v.conf === 'very_low' ? `<div class="pfx-warn">VaR рассчитан на короткой истории (${v.n} мес); хвостовой риск может быть недооценён.</div>` : ''}
    <div class="pfx-note muted">Данные месячные: дневной VaR, rolling 63/126/252-дн, EWMA λ=0.94 daily и дневной backtest — <b>недоступны</b> (нет дневных рядов по бумагам). VaR не показывает максимальный возможный убыток; CVaR информативнее (средний убыток за порогом).</div>`;
}

// ── модуль 5: Risk Budget ────────────────────────────────────────────────────
function pfxRiskBudgetHTML(c) {
  const rb = c.riskBudget;
  if (!rb || !rb.ok) return `<div class="pfx-note">${NA}: нужно ≥2 бумаги с историей ≥12 мес.</div>`;
  const rows = rb.rows.map((r) => {
    const flag = r.share > 0.25 ? '<span class="pfx-tag risk">main risk driver</span>'
      : (r.share > r.weight * 1.3 ? '<span class="pfx-tag warn">hidden risk driver</span>' : '');
    return `<tr><td class="left"><b>${esc(r.ticker)}</b></td><td class="tnum">${PN(r.weight, 1)}</td>
      <td class="tnum">${PN(r.share, 1)}</td><td class="tnum">${PN(r.indivVol)}</td><td class="left">${flag}</td></tr>`;
  }).join('');
  return `${rb.approx ? '<div class="pfx-warn">Risk contribution approximated: ковариация усажена из-за короткой истории.</div>' : ''}
    <div class="pfx-2col"><div class="pfx-chart-wrap"><canvas id="pfx-riskbudget"></canvas></div>
    <table class="pfx-tbl"><thead><tr><th class="left">Бумага</th><th>Вес</th><th>Вклад в риск</th><th>Индив. vol</th><th class="left">Флаг</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pfx-note muted">Component risk contribution: RCᵢ = wᵢ·(Σw)ᵢ / (w'Σw). Портфельная волатильность ${PN(rb.sigmaAnn)} годовых.</div>`;
}

// ── модуль 6: Dividend Stress ────────────────────────────────────────────────
function pfxDivHTML(c) {
  const d = c.div;
  if (!d || d.baseIncome <= 0) return `<div class="pfx-note">${NA}: нет дивпрогноза по бумагам портфеля.</div>`;
  const sc = (lbl, val, tone) => `<div class="pfx-scen pfx-${tone}"><span>${lbl}</span><b>${rub0(val)}</b><em>${PN(val / d.baseIncome - 1)} к base</em></div>`;
  const scen = sc('Base', d.scen.base, 'good') + sc('Conservative', d.scen.conservative, 'neut') + sc('Stress', d.scen.stress, 'warn') + sc('Crisis', d.scen.crisis, 'risk');
  const topInc = d.topIncome.map((it) => `<li><b>${esc(it.ticker)}</b> ${rub0(it.base)} <span class="muted">(${PN(it.share, 0)})</span></li>`).join('');
  const topRisk = d.topRisk.map((it) => `<li><b>${esc(it.ticker)}</b> cut risk ${PN(it.cr, 0)} · ${rub0(it.base)}</li>`).join('');
  const traps = d.traps.length ? `<div class="pfx-warn">Yield trap risk (высокая доходность + высокий cut risk): ${d.traps.map(esc).join(', ')}.</div>` : '';
  const dep = d.topShare > 0.25 ? `<div class="pfx-warn">Зависимость от одного эмитента: ${esc(d.topIncome[0].ticker)} даёт ${PN(d.topShare, 0)} дивпотока.</div>` : '';
  return `<div class="pfx-scens">${scen}</div>
    <div class="pfx-riskrow"><div class="pfx-chart-wrap"><canvas id="pfx-divwater"></canvas></div>
    <div class="pfx-divlists"><div><h4>Top-5 дивпоток</h4><ul>${topInc}</ul></div><div><h4>Top-5 дивриск</h4><ul>${topRisk || '<li class="muted">н/д</li>'}</ul></div></div></div>
    <div class="pfx-kpi-inline">Income at risk: <b class="pfx-risk-ink">${rub0(d.atRisk)}</b> (base ${rub0(d.baseIncome)} → risk-adjusted ${rub0(d.riskAdj)})</div>
    ${traps}${dep}
    ${d.noData.length ? `<div class="pfx-note muted">Без дивданных: ${d.noData.slice(0, 8).map(esc).join(', ')}${d.noData.length > 8 ? '…' : ''}.</div>` : ''}
    <div class="pfx-note muted">payout probability = 1 − cut_risk (ML-оценка проекта). Сценарии — диагностические, не прогноз выплат.</div>
    ${(() => {
      const payers = c.positions.filter((p) => p.t && isNum(p.t.dividend_forecast) && p.t.dividend_forecast > 0)
        .sort((a, b) => (b.t.cut_risk || 0) - (a.t.cut_risk || 0));
      return payers.length ? `<div class="pfx-dr-list"><h4>Почему такой дивидендный риск — разбор по бумагам (P3)</h4>
        ${payers.map(pfxDivRiskCardHTML).join('')}</div>` : '';
    })()}`;
}

// ── P4: Ставка и облигации против портфеля (Rate & Bond Reality Check) ────────
// линейная интерполяция КБД (G-кривой ОФЗ MOEX) по сроку t (лет)
function pfxCurveYield(curve, t) {
  const pts = (curve || []).filter((p) => isNum(p.t) && isNum(p.yield)).sort((a, b) => a.t - b.t);
  if (!pts.length) return null;
  if (t <= pts[0].t) return pts[0].yield;
  if (t >= pts[pts.length - 1].t) return pts[pts.length - 1].yield;
  for (let i = 1; i < pts.length; i++) {
    if (t <= pts[i].t) { const a = pts[i - 1], b = pts[i]; return a.yield + (b.yield - a.yield) * (t - a.t) / (b.t - a.t); }
  }
  return pts[pts.length - 1].yield;
}
// связывает портфель с безрисковой кривой и корпоративными облигациями (BONDS)
function pfxRateBond(c) {
  if (!BONDS || !BONDS.chart || !BONDS.bonds || !(BONDS.chart.ofz_curve || []).length) return { ok: false };
  const curve = BONDS.chart.ofz_curve;
  const rf1 = pfxCurveYield(curve, 1), rf2 = pfxCurveYield(curve, 2), rf5 = pfxCurveYield(curve, 5);
  const grossY = isNum(c.grossYield) && c.grossYield > 0 ? c.grossYield : null;   // ожид. дивдоходность, gross %
  const netY = grossY != null ? grossY * NET_OF_TAX : null;                        // после НДФЛ 13%
  const erpGross = (grossY != null && rf1 != null) ? grossY - rf1 : null;          // премия gross к КБД 1г, п.п.
  // медианная ЧИСТАЯ (net-of-tax) YTM корпоратов инвест-грейда по рейтингам
  const grades = ['AAA', 'AA', 'A'];
  const byGrade = {};
  grades.forEach((g) => {
    const arr = BONDS.bonds.filter((b) => (b.rating_group || b.rating) === g && isNum(b.ytm_net));
    if (arr.length) byGrade[g] = { n: arr.length, medYtmNet: pfxPercentile(arr.map((b) => b.ytm_net), 0.5),
      medDur: pfxPercentile(arr.map((b) => b.duration_years).filter(isNum), 0.5) };
  });
  const aaaNet = byGrade.AAA ? byGrade.AAA.medYtmNet : null;
  const substituted = (netY != null && aaaNet != null && aaaNet >= netY);          // ААА net ≥ дивиденды net
  // конкретные бумаги: инвест-грейд, максимальная чистая YTM, недооценённые (рынок ≤ fair), ликвидные
  const picks = BONDS.bonds
    .filter((b) => grades.includes(b.rating_group || b.rating) && isNum(b.ytm_net) && isNum(b.duration_years)
      && (b.deviation == null || b.deviation <= 0.5) && (b.valtoday == null || b.valtoday > 5e6))
    .sort((a, b) => b.ytm_net - a.ytm_net).slice(0, 3);
  // стилизованная дюрация дивпотока (Gordon-перпетуитет): D_mod ≈ 1/div_yield; +1пп ставки → −D_mod%
  const gordonDur = grossY != null ? 100 / grossY : null;                          // лет
  const reprice1pp = gordonDur != null ? -gordonDur : null;                        // % переоценки при +1пп
  const rateSens = c.positions.reduce((s, p) => s + p.weight * pfxSectorRate(p.sector), 0);
  return { ok: true, date: (BONDS.meta && BONDS.meta.data_date) || (BONDS.chart.updated || '').slice(0, 10),
    curve, rf1, rf2, rf5, grossY, netY, erpGross, byGrade, aaaNet, substituted, picks, gordonDur, reprice1pp, rateSens };
}
function pfxRateBondHTML(c) {
  if (!BONDS) return `<div class="pfx-note muted">Загрузка слоя облигаций/ставки…</div>`;
  const r = c._rb || pfxRateBond(c);
  if (!r.ok) return `<div class="pfx-note">${NA}: не удалось загрузить безрисковую кривую/скринер облигаций.</div>`;
  const pp = (x, d) => isNum(x) ? ((x >= 0 ? '+' : '') + ru(x, d == null ? 1 : d)) : mdash;
  const pc = (x, d) => isNum(x) ? (ru(x, d == null ? 1 : d) + '%') : mdash;
  // 1. КБД чипы
  const curve = `<div class="pfx-rb-curve">
    <span class="pfx-rb-chip">КБД 1 год <b>${pc(r.rf1)}</b></span>
    <span class="pfx-rb-chip">2 года <b>${pc(r.rf2)}</b></span>
    <span class="pfx-rb-chip">5 лет <b>${pc(r.rf5)}</b></span>
    <span class="muted pfx-rb-src">безрисковая кривая ОФЗ (КБД MOEX)${r.date ? ' · ' + esc(r.date) : ''}</span></div>`;
  // 2. ERP-lite (премия к безриску)
  let erpTone = 'neut', erpNote = 'Дивидендная доходность близка к безрисковой ставке.';
  if (r.erpGross != null) {
    if (r.erpGross < 0) { erpTone = 'risk'; erpNote = 'Ожидаемые дивиденды НИЖЕ безрисковой ставки ОФЗ: по текущей доходности вы принимаете рыночный риск акций без премии. Апсайд — только в росте цены/дивиденда.'; }
    else if (r.erpGross < 3) { erpTone = 'warn'; erpNote = 'Умеренная премия за риск акций к безриску — тонкая подушка на случай снижения ставок/просадки.'; }
    else { erpTone = 'good'; erpNote = 'Существенная премия за риск акций к безрисковой ставке.'; }
  }
  const erp = `<div class="pfx-rb-erp pfx-${erpTone}">
    <div class="pfx-rb-erp-num">${pp(r.erpGross)} п.п.</div>
    <div class="pfx-rb-erp-lbl">дивидендная премия к безриску &nbsp;=&nbsp; gross дивдоходность ${pc(r.grossY)} − КБД 1г ${pc(r.rf1)}</div>
    <div class="pfx-rb-erp-note">${erpNote}</div></div>`;
  // 3. Таблица замещения (всё «на руки», после НДФЛ 13%)
  const gr = (g, lbl) => { const b = r.byGrade[g]; return b
    ? `<tr><td class="left">Корп. ${lbl} (медиана, ${b.n})</td><td class="tnum">${pc(b.medYtmNet)}</td><td class="tnum">${isNum(b.medDur) ? ru(b.medDur, 1) + ' г' : '—'}</td><td class="left muted">кредит ${lbl}; купон контрактный до погашения</td></tr>`
    : ''; };
  const subTbl = `<div class="pfx-tbl-scroll"><table class="pfx-tbl pfx-rb-tbl"><thead><tr>
    <th class="left">Инструмент</th><th>Доходность «на руки»</th><th>Дюрация/срок</th><th class="left">Природа</th></tr></thead><tbody>
    <tr class="pfx-rb-me"><td class="left"><b>Ваш портфель</b> (ожид. дивиденды)</td><td class="tnum"><b>${pc(r.netY)}</b></td><td class="tnum">бессрочно</td><td class="left muted">рыночный риск акций; дивиденд не гарантирован</td></tr>
    ${gr('AAA', 'AAA')}${gr('AA', 'AA')}${gr('A', 'A')}</tbody></table></div>`;
  // вердикт замещения
  const sub = r.aaaNet == null ? '' : (r.substituted
    ? `<div class="pfx-warn">Корпоблигации <b>AAA дают ${pc(r.aaaNet)} «на руки» — не меньше</b>, чем ожидаемая чистая дивдоходность портфеля (${pc(r.netY)}), при кредитном риске AAA, известном сроке и меньшей волатильности. Это весомая альтернатива части акций (дивиденд не гарантирован; купон облигации — контрактный).</div>`
    : `<div class="pfx-kpi-inline pfx-neut">Портфель платит чистыми на <b>${pp(r.netY - r.aaaNet)} п.п.</b> больше, чем корп. AAA (${pc(r.aaaNet)}) — это компенсация за рыночный риск акций и негарантированность дивиденда.</div>`);
  // 4. Конкретные бумаги
  const picks = r.picks.length ? `<div class="pfx-rb-picks"><h4>Облигации инвест-грейда с высокой чистой доходностью</h4>
    <ul>${r.picks.map((b) => `<li><b>${esc(b.name || b.secid)}</b> <span class="pfx-rb-rt">${esc(b.rating || '')}</span> — ${pc(b.ytm_net)} «на руки» · дюрация ${ru(b.duration_years, 1)} г · погашение ${esc((b.maturity || '').slice(0, 7))}</li>`).join('')}</ul>
    <div class="pfx-note muted">Источник: скринер облигаций${r.date ? ' · ' + esc(r.date) : ''} (рублёвые корпораты TQCB, ≥ BBB-, фикс-купон). Не ИИР — не оффер купить/продать.</div></div>` : '';
  // 5. Чувствительность к ставке
  const rateWarn = r.rateSens > 0.6;
  const rate = `<div class="pfx-rb-rate">
    <div class="pfx-rb-rr"><span>Стилизованная дюрация дивпотока</span><b>≈ ${isNum(r.gordonDur) ? ru(r.gordonDur, 1) + ' г' : mdash}</b>
      <em class="muted">рост требуемой доходности на +1 п.п. → переоценка ≈ ${pc(r.reprice1pp)}</em></div>
    <div class="pfx-rb-rr"><span>Секторная чувствительность к ставке</span><b class="${rateWarn ? 'pfx-warn-ink' : ''}">${isNum(r.rateSens) ? Math.round(r.rateSens * 100) + '%' : mdash}</b>
      <em class="muted">${rateWarn ? 'много банков/энергетики/недвижимости — портфель ведёт себя «облигационно»' : 'умеренная — доминируют менее ставко-зависимые сектора'}</em></div></div>`;
  return `${curve}${erp}
    <div class="pfx-rb-chart-wrap"><canvas id="pfx-rb-curve"></canvas></div>
    <h4 class="pfx-rb-h">Порог замещения облигациями — что даёт та же сумма «на руки»</h4>
    ${subTbl}${sub}${picks}
    <h4 class="pfx-rb-h">Чувствительность к ставке</h4>${rate}
    <div class="pfx-note muted">Дивдоходность — ожидаемая (прогноз проекта), не гарантирована и плавает; YTM облигации — контрактная к погашению при удержании (кредитный риск эмитента остаётся). Обе «на руки» = после НДФЛ 13%. Дюрация дивпотока — стилизованная оценка по модели Гордона (1/дивдоходность), не прогноз цены. КБД — G-кривая ОФЗ MOEX. Не ИИР.</div>`;
}

// ── модуль 6b: факторная диагностика (P1) ────────────────────────────────────
function pfxFactorsHTML(c) {
  const fx = pfxFactors(c);
  const riskFactors = { debt: 1, cutrisk: 1, rate: 1 };   // высокий перцентиль = плохо (красный)
  const rows = fx.factors.map((f) => {
    if (f.pct == null) return `<div class="pfx-secrow"><span>${esc(f.label)}</span><i></i><em class="pfx-na">${esc(f.note || 'н/д')}</em></div>`;
    const bad = riskFactors[f.key];
    const cls = bad ? (f.pct >= 66 ? 'bar-risk' : f.pct >= 40 ? 'bar-warn' : 'bar-good')
      : (f.pct >= 60 ? 'bar-good' : f.pct >= 34 ? 'bar-warn' : 'bar-risk');
    return `<div class="pfx-secrow"><span title="перцентиль средневзвешенной экспозиции против всего рынка (50 = медиана)">${esc(f.label)}</span>
      <i class="pfx-fbar"><b class="${cls}" style="width:${f.pct}%"></b></i><em>${f.pct}%</em></div>`;
  }).join('');
  const summary = fx.summary.map((s) => `<li>${esc(s)}</li>`).join('');
  return `<div class="pfx-factors"><div class="pfx-factbars">${rows}</div>
    <div class="pfx-factsum"><h4>Вывод</h4><ul>${summary}</ul></div></div>
    <div class="pfx-note muted">Перцентиль — где средневзвешенная экспозиция портфеля относительно всех ${(DATA && DATA.tickers ? DATA.tickers.length : 0)} бумаг рынка (50% = медиана). Value = потенциал к справедливой цене (DCF), Safety = обратная волатильность, Rate Sensitivity — секторная оценка. Не ИИР.</div>`;
}

// ── P3: Explainable Dividend Risk — карточка «почему такой риск» ──────────────
function pfxFinForTicker(tk) {
  if (!SITE_FINANCIALS || !SITE_FINANCIALS.rows) return null;
  const rows = SITE_FINANCIALS.rows.filter((r) => r.ticker === tk && isNum(r.fiscal_year));
  if (!rows.length) return null;
  rows.sort((a, b) => b.fiscal_year - a.fiscal_year);
  return rows[0];
}
function pfxDivRiskExplain(p) {
  const t = p.t || {};
  const cr = isNum(t.cut_risk) ? t.cut_risk : null;
  const level = cr == null ? null : cr >= 0.6 ? 'повышенный' : cr >= 0.35 ? 'умеренный' : 'низкий';
  const tone = cr == null ? 'neut' : cr >= 0.6 ? 'risk' : cr >= 0.35 ? 'warn' : 'good';
  const fin = pfxFinForTicker(p.ticker);
  let fcfCov = null;
  if (fin && isNum(fin.free_cash_flow) && isNum(fin.dividends_paid) && Math.abs(fin.dividends_paid) > 0)
    fcfCov = fin.free_cash_flow / Math.abs(fin.dividends_paid);
  const margin = (fin && isNum(fin.net_income) && isNum(fin.revenue) && fin.revenue > 0) ? fin.net_income / fin.revenue : null;
  // rule-based причины (дополняют/заменяют SHAP)
  const reasons = [];
  if (isNum(t.div_streak) && t.div_streak === 0) reasons.push('выплаты нерегулярны (серия прервана)');
  else if (isNum(t.div_streak) && t.div_streak >= 8) reasons.push(`длинная серия выплат (${t.div_streak} лет)`);
  if (fcfCov != null && fcfCov < 1) reasons.push('дивиденд не покрыт свободным денежным потоком');
  if (isNum(t.payout) && t.payout > 80) reasons.push(`высокий пэйаут (${Math.round(t.payout)}%)`);
  if (isNum(t.nd_ebitda) && t.nd_ebitda > 3) reasons.push(`высокий долг (ND/EBITDA ${ru(t.nd_ebitda, 1)})`);
  if (isNum(t.dividend_yield_expected) && t.dividend_yield_expected >= 9 && cr != null && cr >= 0.5) reasons.push('высокая доходность при высоком риске — возможная ловушка');
  const verdict = level
    ? `Модель оценивает риск среза как ${level} (cut risk ${Math.round(cr * 100)}%)${reasons.length ? ': ' + reasons.slice(0, 3).join(', ') : ''}.`
    : 'Недостаточно данных для оценки риска среза.';
  return { level, tone, cr, fin, fcfCov, margin, verdict, reasons };
}
function pfxDivRiskCardHTML(p) {
  const t = p.t || {};
  const ex = pfxDivRiskExplain(p);
  const m = (lbl, v) => `<div class="pfx-drm"><span>${lbl}</span><b>${v}</b></div>`;
  const dps = isNum(t.current_dps) ? ru(t.current_dps, 2) + '₽' : mdash;
  const fc = isNum(t.dividend_forecast) ? ru(t.dividend_forecast, 2) + '₽' : mdash;
  const metrics = [
    m('Stability Score', isNum(t.stability_score) ? PN(t.stability_score, 0) : mdash),
    m('Cut Risk', ex.cr != null ? PN(ex.cr, 0) : mdash),
    m('Вероятн. выплаты', ex.cr != null ? PN(1 - ex.cr, 0) : mdash),
    m('Последний дивиденд', dps),
    m('Прогноз дивиденда', fc),
    m('Ожид. доходность', isNum(t.dividend_yield_expected) ? PU(t.dividend_yield_expected, 1) + '%' : mdash),
    m('Серия выплат', isNum(t.div_streak) ? t.div_streak + ' лет' : mdash),
    m('Payout ratio', isNum(t.payout) ? Math.round(t.payout) + '%' : (ex.fin && isNum(ex.fin.payout_ratio) ? Math.round(ex.fin.payout_ratio * 100) + '%' : mdash)),
    m('Покрытие FCF', ex.fcfCov != null ? ru(ex.fcfCov, 1) + '×' : mdash),
    m('Долг ND/EBITDA', isNum(t.nd_ebitda) ? ru(t.nd_ebitda, 1) : mdash),
    m('Чистая маржа', ex.margin != null ? PN(ex.margin, 0) : mdash),
  ].join('');
  const shap = (t.shap_top5 || []).map((s) => {
    const up = /↑|повыш/.test(s.direction || '');
    return `<div class="pfx-shap"><span>${esc(s.feature_ru || s.feature || '')}</span>
      <i class="pfx-shbar ${up ? 'up' : 'down'}" style="width:${Math.min(100, Math.abs(s.impact || 0) * 300)}%"></i>
      <em class="${up ? 'saw-up' : 'saw-down'}">${up ? '↑' : '↓'} ${ru(Math.abs(s.impact || 0), 3)}</em></div>`;
  }).join('');
  const shapBlock = shap
    ? `<div class="pfx-dr-shap"><h5>Что повлияло на оценку (SHAP-факторы модели)</h5>${shap}</div>`
    : `<div class="pfx-note muted">SHAP-факторы недоступны — оценка объяснена правилами выше.</div>`;
  return `<details class="pfx-drcard"><summary><span class="pfx-tag ${ex.tone === 'good' ? 'good' : ex.tone === 'warn' ? 'warn' : 'risk'}">${ex.level || 'н/д'}</span>
    <b>${esc(p.ticker)}</b> <span class="muted">${esc(t.name || p.sector || '')}</span> — почему такой дивидендный риск?</summary>
    <div class="pfx-dr-body">
      <div class="pfx-dr-verdict pfx-bi-${ex.tone}">${esc(ex.verdict)}</div>
      <div class="pfx-drgrid">${metrics}</div>
      ${shapBlock}
      <div class="pfx-note muted">Данные: ${esc((p._dq && p._dq.level) || 'н/д')} · ${ex.fin ? 'фундамент ' + ex.fin.fiscal_year + ' (' + esc(ex.fin.source_status || ex.fin.source || '') + ')' : 'фундаментального слоя нет'}.
      Ограничения: модель оценивает вероятность СРЕЗА дивиденда, не доходность; результат не гарантирует будущую выплату и не является рекомендацией.</div>
    </div></details>`;
}

// ── модуль 7: Bootstrap ──────────────────────────────────────────────────────
function pfxBootHTML(c) {
  const b = c.boot;
  if (!b || !b.ok) return `<div class="pfx-note">${NA}: ${b && b.reason ? b.reason : 'нужно ≥18 мес истории и бенчмарк MCFTR'}.</div>`;
  const frag = Math.round(100 * (0.35 * (1 - b.pBeat) + 0.2 * b.pNegExcess + 0.15 * Math.min(1, Math.abs(b.mdd[0]) / 0.4) + 0.15 * Math.min(1, c.top3) + 0.15 * (c.vaR && c.vaR.ok ? Math.min(1, Math.abs(c.vaR.hist95) / 0.15) : 0.3)));
  const fragTone = frag > 60 ? 'risk' : frag > 40 ? 'warn' : 'good';
  const fragWord = frag > 60 ? 'высокая' : frag > 40 ? 'умеренная' : 'низкая';
  const yrs = (b.histMonths / 12);

  // 3 сценария-исхода: неблагоприятный (5% худших) / типичный (медиана) / благоприятный (5% лучших)
  const scen = (tone, tag, cagr, dd) => `<div class="pfx-boot-card pfx-${tone}">
    <span class="pfx-boot-tag">${tag}</span>
    <b class="pfx-boot-num">${PP(cagr, 0)}</b>
    <em>годовая доходность · просадка ${PN(dd)}</em></div>`;
  const scenRow = `<div class="pfx-boot-scen">
    ${scen('risk', 'Неблагоприятный год (5% худших)', b.cagr[0], b.mdd[0])}
    ${scen('neut', 'Типичный год (медиана)', b.cagr[1], b.mdd[1])}
    ${scen('good', 'Благоприятный год (5% лучших)', b.cagr[2], b.mdd[2])}</div>`;

  // человеческий вывод «как читать»
  const readParts = [
    `В типичный смоделированный год портфель даёт <b>${PP(b.cagr[1], 0)}</b>, но разброс исходов широкий: от <b>${PP(b.cagr[0], 0)}</b> в неблагоприятном году до <b>${PP(b.cagr[2], 0)}</b> в благоприятном.`,
    `Убыточным год оказывается в <b>${ru(b.pLoss * 100, 0)}%</b> сценариев; индекс MCFTR по доходности портфель обгоняет в <b>${ru(b.pBeat * 100, 0)}%</b>, а по глубине просадки оказывается мягче индекса в <b>${ru(b.pLowerDD * 100, 0)}%</b>.`,
    frag > 60 ? 'Высокая хрупкость: результат сильно зависит от того, как «легли» месяцы — портфель чувствителен к неудачному стечению.' :
      frag > 40 ? 'Умеренная хрупкость: разброс заметный, но управляемый.' : 'Низкая хрупкость: исходы кучные, портфель устойчив к перетасовке истории.',
  ];

  // Sharpe/просадка по трём сценариям (CAGR уже в картах)
  const perc = (lbl, arr, fmt) => `<tr><td class="left">${lbl}</td><td class="tnum">${fmt(arr[0])}</td><td class="tnum">${fmt(arr[1])}</td><td class="tnum">${fmt(arr[2])}</td></tr>`;

  return `<div class="pfx-boot">
    <p class="pfx-boot-intro">Берём <b>реальные месячные доходности</b> вашего портфеля за ${ru(yrs, 1)} года и <b>${b.sims} раз</b> случайно пересобираем из них виртуальный год. Получается «веер» того, как мог бы сложиться год у бумаг с такой же природой. Это <b>не прогноз</b>, а карта разброса, который уже был в истории.</p>

    ${scenRow}

    <div class="pfx-boot-chartwrap"><div class="pfx-chart-wrap"><canvas id="pfx-boothist"></canvas></div>
      <div class="pfx-boot-legend muted"><span class="pfx-boot-lg loss"></span>убыточные годы (слева от 0%) &nbsp; <span class="pfx-boot-lg gain"></span>прибыльные &nbsp; <span class="pfx-boot-lg med"></span>медиана. Высота столбца — сколько из ${b.sims} виртуальных лет попали в этот диапазон доходности.</div></div>

    <div class="pfx-boot-strip">
      <div class="pfx-boot-frag pfx-${fragTone}"><span class="pfx-boot-frag-lbl">Хрупкость портфеля <span class="pfx-help" data-tooltip="Насколько результат зависит от везения. Складывается из: как редко портфель обгоняет индекс, доли лет в минусе к нему, глубины типичной просадки, концентрации top-3 и месячного VaR. Выше — исход сильнее зависит от случая.">ⓘ</span></span><b>${frag}/100 · ${fragWord}</b></div>
      <table class="pfx-tbl pfx-boot-tbl"><thead><tr><th class="left"></th><th>Неблагопр.</th><th>Типичный</th><th>Благопр.</th></tr></thead><tbody>
        ${perc('Макс. просадка', b.mdd, PN)}${perc('Sharpe', b.sharpe, (x) => PU(x, 2))}
      </tbody></table>
    </div>

    <div class="pfx-boot-read">${readParts.map((t) => `<p>${t}</p>`).join('')}</div>

    <div class="pfx-note muted">Метод: bootstrap-ресэмпл (${b.sims} симуляций × 12 мес) фактических месячных доходностей — оценка <b>устойчивости по истории</b>, не прогноз будущей доходности и не ИИР. «Год» здесь виртуальный: месяцы берутся из прошлого случайно, поэтому хвосты шире одного реального года.</div>
  </div>`;
}

// ── модуль 8: Smart Rebalancer ───────────────────────────────────────────────
function pfxRebalHTML(c) {
  const modes = [['lowrisk', 'Lower Risk'], ['sharpe', 'Better Sharpe'], ['dividend', 'Dividend Stability'], ['benchmark', 'Benchmark-Aware'], ['balanced', 'Balanced']];
  c._rebal = {};
  modes.forEach(([m]) => { const r = pfxRebalance(c.positions, m); if (r) { r.metrics = pfxScenarioMetrics(r.tickers, r.weights, c.bench, c.rf); c._rebal[m] = r; } });
  if (!Object.keys(c._rebal).length) return `<div class="pfx-note">${NA}: нужно ≥2 бумаги с данными.</div>`;
  const btns = modes.filter(([m]) => c._rebal[m]).map(([m, l], i) => `<button class="pfx-rbtn${i === 0 ? ' on' : ''}" data-mode="${m}">${l}</button>`).join('');
  return `<div class="pfx-rbtns">${btns}</div><div id="pfx-rebal-body">${pfxRebalScenarioHTML(c, modes.find(([m]) => c._rebal[m])[0])}</div>
    <div class="pfx-note muted">Suggested Diagnostic Weights — аналитический сценарий (эвристический скоринг + лимит 15%/бумага), НЕ индивидуальная инвестиционная рекомендация.</div>`;
}
function pfxRebalScenarioHTML(c, mode) {
  const r = c._rebal[mode]; if (!r) return '';
  const cur = c.perf, m = r.metrics;
  const cmp = (l, a, b, fmt) => `<tr><td class="left">${l}</td><td class="tnum">${fmt(a)}</td><td class="tnum">${fmt(b)}</td></tr>`;
  const comp = m ? `<table class="pfx-tbl"><thead><tr><th class="left">Метрика</th><th>Текущий</th><th>Сценарий</th></tr></thead><tbody>
    ${cmp('CAGR', cur && cur.cagr, m.perf.cagr, PP)}${cmp('Ann. vol', cur && cur.volAnn, m.perf.volAnn, PN)}
    ${cmp('Sharpe', cur && cur.sharpe, m.perf.sharpe, (x) => PU(x, 2))}${cmp('Max DD', cur && cur.mdd, m.perf.mdd, PN)}
    ${cmp('VaR 95%', c.vaR && c.vaR.hist95, m.vaR && m.vaR.hist95, PN)}${cmp('Beta', c.capm && c.capm.beta, m.capm && m.capm.beta, (x) => PU(x, 2))}
    ${cmp('Top-1 / Top-3', null, null, () => '')}
    <tr><td class="left">Top-3 концентрация</td><td class="tnum">${PN(c.top3, 0)}</td><td class="tnum">${PN(m.top3, 0)}</td></tr>
    <tr><td class="left">Turnover</td><td class="tnum">—</td><td class="tnum">${PN(r.turnover, 0)}</td></tr>
  </tbody></table>` : `<div class="pfx-note">${NA} сравнение метрик сценария.</div>`;
  const chg = r.changes.filter((x) => Math.abs(x.delta) > 0.005).slice(0, 12).map((x) =>
    `<tr><td class="left"><b>${esc(x.ticker)}</b></td><td class="tnum">${PN(x.cur, 1)}</td><td class="tnum">${PN(x.sug, 1)}</td>
     <td class="tnum ${x.delta >= 0 ? 'saw-up' : 'saw-down'}">${PP(x.delta, 1)}</td><td class="left muted">${esc(x.reason)}</td></tr>`).join('');
  return `<div class="pfx-2col">${comp}
    <table class="pfx-tbl"><thead><tr><th class="left">Бумага</th><th>Тек.</th><th>Сцен.</th><th>Δ</th><th class="left">Причина</th></tr></thead><tbody>${chg}</tbody></table></div>`;
}

// ── модуль 9: Allocation ─────────────────────────────────────────────────────
function pfxAllocHTML(c) {
  const bucketBlock = (title, entries) => {
    const rows = entries.map(([k, w]) => `<div class="pfx-secrow"><span>${esc(k)}</span><i><b style="width:${Math.min(100, w * 100).toFixed(0)}%"></b></i><em>${PN(w, 0)}</em></div>`).join('');
    return `<div class="pfx-alloc-block"><h4>${esc(title)}</h4>${rows}</div>`;
  };
  const sectors = pfxBuckets(c.positions, (p) => p.sector || 'Unknown');
  const cutB = pfxBuckets(c.positions, (p) => pfxCutBucketLabel(p.t && isNum(p.t.cut_risk) ? p.t.cut_risk : null), ['low cut risk', 'medium cut risk', 'high cut risk', 'н/д']);
  const betaB = pfxBuckets(c.positions, (p) => pfxBetaBucket(p._beta));
  const yieldB = pfxBuckets(c.positions, (p) => pfxYieldBucket(p.dividend_yield), ['<3%', '3–6%', '6–9%', '≥9%', 'н/д']);
  const advB = pfxBuckets(c.positions, pfxAdvBucket);
  const flags = [];
  const top1 = Math.max(...c.positions.map((p) => p.weight));
  if (top1 > 0.15) flags.push(`один эмитент ${PN(top1, 0)} > 15%`);
  if (sectors[0] && sectors[0][1] > 0.35) flags.push(`сектор «${sectors[0][0]}» ${PN(sectors[0][1], 0)} > 35%`);
  if (c.top3 > 0.5) flags.push(`top-3 ${PN(c.top3, 0)} > 50%`);
  if (isNum(c.wBeta) && c.wBeta > 1.2) flags.push(`взвеш. beta ${PU(c.wBeta, 2)} > 1.2`);
  const highCut = c.positions.reduce((s, p) => s + (p.t && isNum(p.t.cut_risk) && p.t.cut_risk >= 0.6 ? p.weight : 0), 0);
  if (highCut > 0.3) flags.push(`high cut risk ${PN(highCut, 0)} > 30%`);
  const flagHtml = flags.length ? `<div class="pfx-warn">Лимиты: ${flags.join('; ')}.</div>` : '<div class="pfx-note pfx-good-ink">Портфель в пределах базовых лимитов.</div>';
  return `${flagHtml}<div class="pfx-alloc">${bucketBlock('Секторы', sectors)}${bucketBlock('Cut risk', cutB)}${bucketBlock('Beta', betaB)}${bucketBlock('Дивдоходность', yieldB)}${bucketBlock('Ликвидность (ADV)', advB)}</div>`;
}

// ── модуль 10: Position Diagnostics ──────────────────────────────────────────
function pfxPosHTML(c) {
  const rows = c.sorted.map((p) => {
    const flag = pfxPosFlag(p, c);
    const pnl = p.pnl_pct == null ? mdash : `<span class="${p.pnl_pct >= 0 ? 'saw-up' : 'saw-down'}">${PP(p.pnl_pct)}</span>`;
    return `<tr>
      <td class="left"><b>${esc(p.ticker)}</b><span class="muted"> ${esc(p.sector)}</span></td>
      <td class="tnum">${PN(p.weight, 1)}</td>
      <td class="tnum">${rub0(p.value)}</td>
      <td class="tnum">${pnl}</td>
      <td class="tnum">${(c._divSuspect && c._divSuspect.includes(p.ticker)) ? '<span class="pfx-na" title="дивиденд завышен, требует проверки">треб. проверки</span>' : (isNum(p.dividend_yield) ? PU(p.dividend_yield, 1) + '%' : mdash)}</td>
      <td class="tnum">${p.t && isNum(p.t.cut_risk) ? PN(p.t.cut_risk, 0) : mdash}</td>
      <td class="tnum">${isNum(p._beta) ? PU(p._beta, 2) : mdash}</td>
      <td class="tnum">${isNum(p._ivol) ? PN(p._ivol) : mdash}</td>
      <td class="tnum">${isNum(p._ivar) ? PN(p._ivar) : mdash}</td>
      <td class="tnum">${isNum(p._riskShare) ? PN(p._riskShare, 0) : mdash}</td>
      <td class="left"><span class="pfx-conf pfx-${p._dq.level === 'high' ? 'good' : p._dq.level === 'medium' ? 'neut' : p._dq.level === 'low' ? 'warn' : 'risk'}">${p._dq.level}</span></td>
      <td class="left">${flag}</td></tr>`;
  }).join('');
  return `<div class="pfx-tbl-scroll"><table class="pfx-tbl pfx-postbl"><thead><tr>
    <th class="left">Бумага</th><th>Вес</th><th>Стоим.</th><th>P&L</th><th>DY</th><th>Cut risk</th><th>Beta</th><th>Vol</th><th>VaR95</th><th>Risk share</th><th class="left">Данные</th><th class="left">Флаг</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pfx-note muted">Флаг — диагностический статус, не рекомендация. VaR/vol — индивидуальные месячные. Экспорт всей диагностики — кнопкой вверху.</div>`;
}
function pfxPosFlag(p, c) {
  const t = p.t || {};
  const tags = [];
  if (p.weight > 0.15) tags.push('<span class="pfx-tag risk">concentration</span>');
  if (isNum(p._riskShare) && p._riskShare > 0.25) tags.push('<span class="pfx-tag risk">main VaR contributor</span>');
  if (isNum(p._beta) && p._beta > 1.3) tags.push('<span class="pfx-tag warn">high beta</span>');
  if (isNum(p.dividend_yield) && p.dividend_yield >= 8 && isNum(t.cut_risk) && t.cut_risk >= 0.5) tags.push('<span class="pfx-tag risk">yield trap</span>');
  if (p._dq.level === 'low' || p._dq.level === 'unavailable') tags.push('<span class="pfx-tag warn">low data</span>');
  if (isNum(t.cut_risk) && t.cut_risk < 0.3 && isNum(p.dividend_yield) && p.dividend_yield >= 5) tags.push('<span class="pfx-tag good">defensive income</span>');
  if (!tags.length) tags.push('<span class="pfx-tag neut">core holding</span>');
  return tags.join(' ');
}

// ── модуль 12: Data Quality ──────────────────────────────────────────────────
function pfxDQHTML(c) {
  const warn = c.dq.lowWeight > 0.3 ? `<div class="pfx-warn">Часть выводов ограничена качеством данных: ${PN(c.dq.lowWeight, 0)} веса портфеля — бумаги с неполной историей или отсутствующими метриками.</div>` : '';
  const src = `<div class="pfx-note">Источники: цены/сектор/дивпрогноз/cut_risk — data.json (asof ${esc((DATA && DATA.meta && DATA.meta.price_asof) || '—')}); история ретёрнов — returns.json (${c.pf ? c.pf.n : 0} мес); бенчмарк — MCFTR (${c.bench ? 'есть' : NA}); RFR — ${c.rf.ok ? PU(c.rf.annual, 1) + '% (константа, истории нет)' : NA}.</div>`;
  const rows = c.positions.map((p) => `<tr><td class="left"><b>${esc(p.ticker)}</b></td>
    <td class="tnum">${p._dq.hist} мес</td>
    <td class="left"><span class="pfx-conf pfx-${p._dq.level === 'high' ? 'good' : p._dq.level === 'medium' ? 'neut' : p._dq.level === 'low' ? 'warn' : 'risk'}">${p._dq.level}</span></td>
    <td class="left muted">${p._dq.miss.length ? esc(p._dq.miss.join(', ')) : '—'}</td></tr>`).join('');
  return `${warn}${src}<table class="pfx-tbl"><thead><tr><th class="left">Бумага</th><th>История</th><th class="left">Confidence</th><th class="left">Пробелы</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ── модуль 13: Methodology ───────────────────────────────────────────────────
function pfxMethodHTML() {
  const defs = [
    ['Sharpe', 'избыточная доходность над RFR на единицу общей волатильности'],
    ['Sortino', 'как Sharpe, но в знаменателе только downside-волатильность'],
    ['Calmar', 'CAGR / |max drawdown| — доходность на единицу худшей просадки'],
    ['Beta', 'чувствительность к рынку (MCFTR); >1 — сильнее рынка'],
    ['Alpha', 'доходность сверх объяснённой рынком (CAPM); историческая'],
    ['Tracking Error', 'волатильность активного отклонения от бенчмарка'],
    ['Information Ratio', 'активная доходность / tracking error'],
    ['VaR 95%', 'убыток, который не превышается в 95% месяцев'],
    ['CVaR 95%', 'средний убыток в худших 5% месяцев (за порогом VaR)'],
    ['Cornish-Fisher VaR', 'VaR с поправкой на асимметрию и толстые хвосты'],
    ['Component VaR / Risk Contribution', 'вклад бумаги в общий риск портфеля с учётом корреляций'],
    ['Downside Capture', 'доля падения рынка, которую забирает портфель; >100% — хуже рынка в падениях'],
    ['Income at Risk', 'ожидаемый дивпоток минус risk-adjusted (× payout probability)'],
    ['Bootstrap', 'resampling исторических доходностей для оценки диапазона исходов'],
    ['Data Quality Score', 'взвешенная по капиталу оценка полноты данных и длины истории'],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  const warns = [
    'Историческая доходность не гарантирует будущую.',
    'VaR не показывает максимальный возможный убыток.',
    'CVaR показывает средний убыток за пределами VaR-порога.',
    'Alpha историческая, не прогнозная.',
    'Bootstrap — resampling истории, не предсказание.',
    'Suggested weights — диагностический сценарий, не ИИР.',
    'Backfilled-портфель по текущему составу ≠ фактическая история сделок.',
    'Price return и total return не смешиваются: total = цена + дивиденд, подписано.',
    'Данные месячные — дневной VaR и rolling-дни недоступны, а не заменены синтетикой.',
  ].map((w) => `<li>${esc(w)}</li>`).join('');
  return `<dl class="pfx-defs">${defs}</dl><ul class="pfx-warns">${warns}</ul>`;
}

// ── графики (Chart.js) ───────────────────────────────────────────────────────
function pfxDrawCharts(c) {
  loadChartJS((err) => {
    if (err || !window.Chart) return;
    (window.__pfxCharts || []).forEach((ch) => { try { ch.destroy(); } catch (e) { /* noop */ } });
    window.__pfxCharts = [];
    const mk = (id, cfg) => { const el = document.getElementById(id); if (!el) return; window.__pfxCharts.push(new window.Chart(el, cfg)); };
    const AX = { grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472', maxTicksLimit: 8, autoSkip: true, maxRotation: 0 } };
    const base = { responsive: true, maintainAspectRatio: false, elements: { point: { radius: 0 } }, plugins: { legend: { labels: { boxWidth: 18, color: '#3A424E', font: { size: 11 } } } } };
    // equity + drawdown
    if (c.perf && c.pf) {
      const labels = c.pf.months;
      const pe = c.perf.cum.map((v) => v * 100);
      const bEq = c.bench ? pfxEquity(c.bench).map((v) => v * 100) : null;
      mk('pfx-equity', { type: 'line', data: { labels, datasets: [
        { label: 'Портфель', data: pe, borderColor: '#4C5C86', borderWidth: 2, tension: 0.1 },
        ...(bEq ? [{ label: 'MCFTR', data: bEq, borderColor: '#8A93A3', borderWidth: 1.5, borderDash: [5, 4], tension: 0.1 }] : []),
      ] }, options: { ...base, scales: { x: AX, y: { ...AX, title: { display: true, text: 'старт = 100', color: '#5A6472' } } } } });
      const ddP = pfxDD(c.perf.cum);
      const ddB = c.bench ? pfxDD(pfxEquity(c.bench)) : null;
      mk('pfx-drawdown', { type: 'line', data: { labels, datasets: [
        { label: 'Просадка портфеля', data: ddP, borderColor: '#A2452C', backgroundColor: 'rgba(200,60,50,.12)', fill: true, borderWidth: 1.5 },
        ...(ddB ? [{ label: 'MCFTR', data: ddB, borderColor: '#8A93A3', borderWidth: 1, borderDash: [5, 4] }] : []),
      ] }, options: { ...base, scales: { x: AX, y: { ...AX, ticks: { ...AX.ticks, callback: (v) => v + '%' } } } } });
      // rolling 12M: доходность и волатильность (окно 12)
      if (c.perf.n >= 18) {
        const W = 12, rr = [], rv = [], rl = [];
        for (let i = W; i <= c.pf.series.length; i++) {
          const win = c.pf.series.slice(i - W, i);
          rr.push((pfxEquity(win)[W - 1] - 1) * 100);
          rv.push(pfxStd(win) * Math.sqrt(12) * 100);
          rl.push(labels[i - 1]);
        }
        mk('pfx-roll-ret', { type: 'line', data: { labels: rl, datasets: [{ label: 'Rolling 12М доходность', data: rr, borderColor: '#4C5C86', backgroundColor: 'rgba(76,92,134,.10)', fill: true, borderWidth: 1.5 }] },
          options: { ...base, plugins: { ...base.plugins, legend: { display: true, labels: base.plugins.legend.labels } }, scales: { x: AX, y: { ...AX, ticks: { ...AX.ticks, callback: (v) => v + '%' } } } } });
        mk('pfx-roll-vol', { type: 'line', data: { labels: rl, datasets: [{ label: 'Rolling 12М волатильность (год.)', data: rv, borderColor: '#8A6224', borderWidth: 1.5 }] },
          options: { ...base, plugins: { ...base.plugins, legend: { display: true, labels: base.plugins.legend.labels } }, scales: { x: AX, y: { ...AX, ticks: { ...AX.ticks, callback: (v) => v + '%' } } } } });
      }
    }
    // risk budget bar
    if (c.riskBudget && c.riskBudget.ok) {
      const rb = c.riskBudget.rows.slice(0, 12);
      mk('pfx-riskbudget', { type: 'bar', data: { labels: rb.map((r) => r.ticker), datasets: [
        { label: 'Вес', data: rb.map((r) => r.weight * 100), backgroundColor: '#A9B7D9' },
        { label: 'Вклад в риск', data: rb.map((r) => r.share * 100), backgroundColor: '#A2452C' },
      ] }, options: { ...base, scales: { x: AX, y: { ...AX, ticks: { ...AX.ticks, callback: (v) => v + '%' } } } } });
    }
    // dividend waterfall (scenarios)
    if (c.div && c.div.baseIncome > 0) {
      const s = c.div.scen;
      mk('pfx-divwater', { type: 'bar', data: { labels: ['Base', 'Conservative', 'Stress', 'Crisis'],
        datasets: [{ label: 'Дивпоток, ₽', data: [s.base, s.conservative, s.stress, s.crisis],
          backgroundColor: ['#1E6F4C', '#4C5C86', '#8A6224', '#A2452C'] }] },
        options: { ...base, plugins: { ...base.plugins, legend: { display: false } }, scales: { x: AX, y: AX } } });
    }
    // P4: КБД-кривая (ОФЗ) + корп-облигации + дивдоходность портфеля
    if (c._rb && c._rb.ok) {
      const r = c._rb;
      const ofz = (r.curve || []).map((p) => ({ x: p.t, y: p.yield }));
      const corp = ((BONDS && BONDS.chart && BONDS.chart.corp_points) || []).filter((p) => isNum(p.duration) && isNum(p.ytm)).map((p) => ({ x: p.duration, y: p.ytm }));
      const maxT = Math.max(5, ...ofz.map((p) => p.x), ...corp.map((p) => p.x));
      const ds = [
        { label: 'КБД ОФЗ (безриск)', type: 'line', data: ofz, parsing: false, borderColor: '#4C5C86', borderWidth: 2, tension: 0.2, pointRadius: 0, order: 1 },
        { label: 'Корп. облигации (YTM)', type: 'scatter', data: corp, parsing: false, backgroundColor: 'rgba(138,98,36,.55)', pointRadius: 3, order: 2 },
      ];
      if (isNum(r.grossY)) ds.push({ label: 'Дивдоходность портфеля (gross)', type: 'line', data: [{ x: 0, y: r.grossY }, { x: maxT, y: r.grossY }], parsing: false, borderColor: '#1E6F4C', borderWidth: 1.5, borderDash: [6, 4], pointRadius: 0, order: 0 });
      mk('pfx-rb-curve', { data: { datasets: ds }, options: { ...base,
        plugins: { ...base.plugins, legend: { display: true, labels: base.plugins.legend.labels },
          tooltip: { callbacks: { label: (i) => `${i.dataset.label}: ${ru(i.parsed.y, 2)}% @ ${ru(i.parsed.x, 2)}г` } } },
        scales: { x: { ...AX, type: 'linear', title: { display: true, text: 'дюрация/срок, лет', color: '#5A6472' } },
          y: { ...AX, title: { display: true, text: 'доходность, % год.', color: '#5A6472' }, ticks: { ...AX.ticks, callback: (v) => v + '%' } } } } });
    }
    // bootstrap: распределение ГОДОВОЙ доходности портфеля (1000 виртуальных лет)
    if (c.boot && c.boot.ok && c.boot.cagrs) {
      const vals = c.boot.cagrs, bins = 26;
      // диапазон обрезаем до 1–99 перцентиля, чтобы редкие хвосты не «размазывали» гистограмму
      const lo = pfxPercentile(vals, 0.01), hi = pfxPercentile(vals, 0.99), w = (hi - lo) / bins || 1;
      const med = c.boot.cagr[1];
      const counts = new Array(bins).fill(0);
      vals.forEach((x) => { let k = Math.floor((Math.max(lo, Math.min(hi, x)) - lo) / w); if (k >= bins) k = bins - 1; if (k < 0) k = 0; counts[k]++; });
      const centers = counts.map((_, i) => lo + (i + 0.5) * w);
      const labels = centers.map((v) => (v * 100).toFixed(0) + '%');
      const medIdx = Math.max(0, Math.min(bins - 1, Math.round((med - lo) / w - 0.5)));
      const medLine = {   // пунктирная линия медианы поверх столбцов
        id: 'bootMedian',
        afterDatasetsDraw(chart) {
          const xa = chart.scales.x, ya = chart.scales.y;
          const px = xa.getPixelForValue(medIdx);
          const g = chart.ctx; g.save();
          g.strokeStyle = '#3A424E'; g.lineWidth = 1.5; g.setLineDash([5, 4]);
          g.beginPath(); g.moveTo(px, ya.top); g.lineTo(px, ya.bottom); g.stroke();
          g.setLineDash([]); g.fillStyle = '#3A424E'; g.font = '600 10px system-ui,sans-serif'; g.textAlign = 'center';
          g.fillText('медиана ' + (med >= 0 ? '+' : '') + (med * 100).toFixed(0) + '%', px, ya.top + 10);
          g.restore();
        },
      };
      mk('pfx-boothist', { type: 'bar', data: { labels, datasets: [{ label: 'Виртуальных лет',
        data: counts, backgroundColor: centers.map((v) => v < 0 ? '#E2A48C' : '#A8D5C2'), borderRadius: 2 }] },
        options: { ...base, plugins: { ...base.plugins, legend: { display: false },
          tooltip: { callbacks: { title: (i) => `≈ ${i[0].label} годовых`, label: (i) => `${i.parsed.y} из ${c.boot.sims} сценариев` } } },
          scales: { x: { ...AX, title: { display: true, text: 'годовая доходность портфеля', color: '#5A6472' } },
            y: { ...AX, title: { display: true, text: 'число сценариев', color: '#5A6472' } } } },
        plugins: [medLine] });
    }
    // перерисовать при открытии свёрнутого модуля (иначе canvas 0-width)
    document.querySelectorAll('.pfx-mod').forEach((d) => {
      if (d.dataset.pfxWired) return; d.dataset.pfxWired = '1';
      d.addEventListener('toggle', () => { if (d.open) (window.__pfxCharts || []).forEach((ch) => { try { ch.resize(); } catch (e) { /* noop */ } }); });
    });
  });
}
function pfxDD(cum) { let peak = cum[0]; return cum.map((v) => { if (v > peak) peak = v; return (v / peak - 1) * 100; }); }

// ── кнопки: копировать отчёт / экспорт CSV / переключение сценария ребаланса ──
function pfxWireButtons(c) {
  const copyBtn = document.getElementById('pfx-copy');
  if (copyBtn) copyBtn.addEventListener('click', () => {
    const lines = [`Portfolio X-Ray — ${c.cls.type}`, pfxDiagnosis(c), ''];
    pfxMemo(c).forEach(([h, b]) => { lines.push('## ' + h, b, ''); });
    const txt = lines.join('\n');
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(() => { copyBtn.textContent = 'Скопировано ✓'; setTimeout(() => { copyBtn.textContent = 'Скопировать отчёт'; }, 1500); });
  });
  const expBtn = document.getElementById('pfx-export');
  if (expBtn) expBtn.addEventListener('click', () => {
    const head = ['ticker', 'sector', 'quantity', 'avg_price', 'current_price', 'market_value', 'weight', 'pnl_pct', 'div_yield', 'cut_risk', 'beta', 'indiv_vol', 'indiv_var95', 'risk_share', 'data_conf'];
    const rows = c.sorted.map((p) => [p.ticker, p.sector, p.quantity, p.avg_price, p.current_price ?? '', Math.round(p.value), (p.weight * 100).toFixed(2), p.pnl_pct != null ? (p.pnl_pct * 100).toFixed(2) : '', isNum(p.dividend_yield) ? p.dividend_yield.toFixed(2) : '', p.t && isNum(p.t.cut_risk) ? p.t.cut_risk.toFixed(3) : '', isNum(p._beta) ? p._beta.toFixed(2) : '', isNum(p._ivol) ? (p._ivol * 100).toFixed(1) : '', isNum(p._ivar) ? (p._ivar * 100).toFixed(1) : '', isNum(p._riskShare) ? (p._riskShare * 100).toFixed(1) : '', p._dq.level].join(','));
    const csv = [head.join(','), ...rows].join('\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'portfolio_diagnostics.csv'; a.click();
  });
  document.querySelectorAll('.pfx-rbtn').forEach((btn) => btn.addEventListener('click', () => {
    document.querySelectorAll('.pfx-rbtn').forEach((b) => b.classList.toggle('on', b === btn));
    const body = document.getElementById('pfx-rebal-body');
    if (body && PFX_STATE) body.innerHTML = pfxRebalScenarioHTML(PFX_STATE, btn.dataset.mode);
  }));
}

function wireMyPortfolio() {
  const input = document.getElementById('mp-input');
  if (!input) return;
  const saved = myPortfolioLoad();
  if (saved.length && !input.value.trim()) input.value = myPortfolioText(saved);
  const saveBtn = document.getElementById('mp-save');
  const sampleBtn = document.getElementById('mp-sample');
  const clearBtn = document.getElementById('mp-clear');
  const importInput = document.getElementById('mp-import');
  if (saveBtn) saveBtn.addEventListener('click', renderMyPortfolio);
  input.addEventListener('input', debounce(renderMyPortfolio, 250));
  if (sampleBtn) sampleBtn.addEventListener('click', () => { input.value = MY_PORTFOLIO_SAMPLE; renderMyPortfolio(); });
  if (clearBtn) clearBtn.addEventListener('click', () => { input.value = ''; myPortfolioSave([]); renderMyPortfolio(); });
  if (importInput) importInput.addEventListener('change', () => {
    const file = importInput.files && importInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { input.value = String(reader.result || ''); renderMyPortfolio(); };
    reader.readAsText(file);
  });
  pfxWireAutocomplete();
  renderMyPortfolio();
}

// P1: autocomplete-ввод тикеров (поиск по data.json → добавить строку в портфель)
function pfxWireAutocomplete() {
  const search = document.getElementById('mp-ticker-search');
  const box = document.getElementById('mp-suggest');
  const input = document.getElementById('mp-input');
  const qtyEl = document.getElementById('mp-add-qty');
  const priceEl = document.getElementById('mp-add-price');
  const addBtn = document.getElementById('mp-add-btn');
  if (!search || !box || !input) return;
  let active = -1, matches = [], selected = null;
  const close = () => { box.classList.remove('open'); box.innerHTML = ''; active = -1; };
  const pick = (t) => {                                 // выбор бумаги: тикер запомнен, цена подставлена, фокус на кол-во
    selected = t;
    search.value = `${t.ticker} — ${t.name || ''}`.trim();
    if (priceEl && isNum(t.price)) priceEl.value = t.price;
    close();
    if (qtyEl) qtyEl.focus();
  };
  const add = () => {                                   // «Добавить»: shares × price → строка внутреннего формата
    let tk = selected ? selected.ticker : pfxCanonTicker(String(search.value || '').split(/[—;,\s]/)[0]);
    const qty = Number(String((qtyEl && qtyEl.value) || '').replace(',', '.'));
    const price = Number(String((priceEl && priceEl.value) || '').replace(',', '.'));
    if (!tk || !/[A-Z0-9]/.test(tk)) { search.focus(); search.setCustomValidity && search.reportValidity(); return; }
    if (!isFinite(qty) || qty <= 0) { if (qtyEl) { qtyEl.focus(); } return; }
    if (!isFinite(price) || price < 0) { if (priceEl) { priceEl.focus(); } return; }
    const cur = input.value.replace(/\s+$/, '');
    input.value = (cur ? cur + '\n' : '') + `${tk}; ${qty}; ${price}`;   // дубли объединит pfxParseValidate
    selected = null; search.value = ''; if (qtyEl) qtyEl.value = ''; if (priceEl) priceEl.value = '';
    renderMyPortfolio(); search.focus();
  };
  const render = () => {
    if (!matches.length) { close(); return; }
    box.innerHTML = matches.map((t, i) => `<div class="mp-sug-item${i === active ? ' active' : ''}" data-i="${i}">
      <b>${esc(t.ticker)}</b><span>${esc(t.name || '')}${isNum(t.price) ? ' · ' + ru(t.price, 2) + '₽' : (t._extra ? ' · нет истории' : '')}</span></div>`).join('');
    box.classList.add('open');
    box.querySelectorAll('.mp-sug-item').forEach((el) => el.addEventListener('mousedown', (e) => { e.preventDefault(); pick(matches[+el.dataset.i]); }));
  };
  search.addEventListener('input', () => {
    selected = null;
    const q = search.value.trim().toUpperCase();
    if (q.length < 1) { matches = []; close(); return; }
    const uni = pfxUniverse();
    const m = uni.filter((t) => t.ticker.startsWith(q) || t.ticker.indexOf(q) >= 0 || (t.name && t.name.toUpperCase().indexOf(q) >= 0));
    Object.keys(PFX_ALIASES).filter((k) => k.indexOf(q) >= 0).forEach((k) => {   // поиск по алиасам (Тинькофф→T, Сургут ап→SNGSP)
      const tk = PFX_ALIASES[k]; if (!m.some((x) => x.ticker === tk)) { const u = uni.find((x) => x.ticker === tk); if (u) m.push(u); }
    });
    matches = m.slice(0, 8); active = -1; render();
  });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !matches.length) { e.preventDefault(); add(); return; }
    if (!matches.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, matches.length - 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(matches[active >= 0 ? active : 0]); }
    else if (e.key === 'Escape') close();
  });
  search.addEventListener('blur', () => setTimeout(close, 150));
  if (addBtn) addBtn.addEventListener('click', add);
  if (priceEl) priceEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
  if (qtyEl) qtyEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && priceEl && priceEl.value) add(); });
}

function syncWeightControl() {   // «Взвешивание» неприменимо к оптимизаторам — они сами считают веса
  const wsel = document.getElementById('pf-weight');
  if (!wsel) return;
  const isOpt = document.getElementById('pf-method').value.startsWith('opt');
  wsel.disabled = isOpt;
  wsel.title = isOpt ? 'Веса вычисляет оптимизатор — этот выбор не используется' : '';
  const lbl = wsel.closest('label');
  if (lbl) lbl.style.opacity = isOpt ? '0.4' : '';
}

function wirePortfolio() {
  const pf = document.getElementById('pf');
  if (!pf) return;
  pf.hidden = false;
  syncWeightControl();
  if (DATA.tickers.some((t) => isNum(t.mom_score))) {
    const o = document.querySelector('#pf-method option[value="momentum"]');
    if (o) { o.disabled = false; o.textContent = 'Momentum (импульс)'; }
  }
  document.getElementById('pf-gen').addEventListener('click', renderPortfolio);
  ['pf-method', 'pf-n', 'pf-weight', 'pf-cap', 'pf-seccap', 'pf-capital'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => { syncWeightControl(); if (document.getElementById('pf-out').dataset.shown) renderPortfolio(); });
  });
  pf.addEventListener('toggle', function () {
    if (this.open && !document.getElementById('pf-out').dataset.shown) {
      document.getElementById('pf-out').dataset.shown = '1';
      renderPortfolio();
    }
  });
}

// ══════════════════════════════════════════════════════════════════════════
// Помощник фазы рынка (swing/zigzag по MCFTR). Все значения — из marketsaw.json
// (генерит CI), никакого hardcode/пересчёта на фронте. Это индикатор фазы, не прогноз.
// ══════════════════════════════════════════════════════════════════════════
SAW_DATA = null;
MARKET_HISTORY = null;
let MARKET_CHART = null;
let MARKET_CHART_STATE = { id: 'IMOEX', period: 252 };
const LWC_SRC = 'https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js';

const sawPct = (v) => (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const sawDate = (s) => { const [y, m, d] = String(s).split('-'); return `${d}.${m}.${y}`; };
const SAW_SHORT = {                                   // короткие подписи сегментов gauge
  up: { neutral: 'Отскок', positive: 'Рост', reduce_zone: 'Сниж. риска', strong_reduce_zone: 'Перегрев' },
  down: { neutral: 'Нач. корр.', watch: 'Коррекция', buy_zone: 'Докупка', strong_buy_zone: 'Глуб. просадка' },
};

function wireMarketSaw() {
  const el = document.getElementById('marketsaw');
  if (!el) return;
  el.hidden = false;
  el.addEventListener('toggle', function () {
    if (this.open && !this.dataset.shown) { this.dataset.shown = '1'; renderMarketSaw(); }
  });
}

function loadMarketSaw(cb) {
  if (SAW_DATA) { cb(); return; }
  fetch('marketsaw.json?t=' + Date.now(), { cache: 'no-store' })   // cache-bust, как returns.json
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.current_phase || !j.series) throw new Error('пустой/битый marketsaw.json'); SAW_DATA = j; cb(); })
    .catch((e) => { console.error('[saw] marketsaw.json не загрузился:', e); cb(e); });
}

function marketStressFromSaw(d) {
  const points = (d && d.series ? d.series : [])
    .map((p) => ({ date: p[0], value: Number(p[1]) }))
    .filter((p) => isFinite(p.value) && p.value > 0);
  if (points.length < 45) return null;
  const rets = [];
  for (let i = 1; i < points.length; i++) {
    rets.push({ date: points[i].date, ret: points[i].value / points[i - 1].value - 1 });
  }
  const volWindow = (slice) => {
    const vals = slice.map((x) => x.ret).filter(isFinite);
    if (vals.length < 10) return null;
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const variance = vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length;
    return Math.sqrt(variance) * Math.sqrt(252);
  };
  const vols = [];
  for (let i = 19; i < rets.length; i++) {
    const v = volWindow(rets.slice(i - 19, i + 1));
    if (v != null) vols.push(v);
  }
  if (!vols.length) return null;
  const current = vols[vols.length - 1];
  const below = vols.filter((v) => v <= current).length;
  const percentile = below / vols.length;
  const score = Math.round(percentile * 100);
  const last5 = rets.slice(-5);
  const weekMove = last5.length ? last5.reduce((acc, x) => acc * (1 + x.ret), 1) - 1 : null;
  let level = 'calm', label = 'Спокойно', tone = 'good';
  if (score >= 80) { level = 'panic'; label = 'Паническая зона'; tone = 'risk'; }
  else if (score >= 60) { level = 'high'; label = 'Повышенное напряжение'; tone = 'warn'; }
  else if (score >= 30) { level = 'normal'; label = 'Обычная волатильность'; tone = 'neut'; }
  return {
    current_vol: current,
    percentile,
    score,
    level,
    label,
    tone,
    week_move: weekMove,
    last_date: points[points.length - 1].date,
  };
}

function marketSeriesPoints(d) {
  return (d && d.series ? d.series : [])
    .map((p) => ({ date: p[0], value: Number(p[1]) }))
    .filter((p) => isFinite(p.value) && p.value > 0);
}

function trailingMove(points, sessions) {
  if (!points || points.length <= sessions) return null;
  const current = points[points.length - 1].value;
  const past = points[points.length - 1 - sessions].value;
  return past > 0 ? current / past - 1 : null;
}

function marketDrawdown(points, sessions) {
  if (!points || points.length < 2) return null;
  const slice = points.slice(Math.max(0, points.length - sessions));
  const high = Math.max(...slice.map((p) => p.value));
  const current = points[points.length - 1].value;
  return high > 0 ? current / high - 1 : null;
}

function signalTone(value, goodWhenPositive, warnLevel, riskLevel) {
  if (!isNum(value)) return 'neut';
  if (goodWhenPositive) {
    if (value <= riskLevel) return 'risk';
    if (value <= warnLevel) return 'warn';
    return 'good';
  }
  if (value >= riskLevel) return 'risk';
  if (value >= warnLevel) return 'warn';
  return 'good';
}

function marketPulseHTML(d) {
  if (!d || !d.current_phase) {
    return '<div class="pulse-loading muted">Рыночный пульс недоступен: нет MCFTR.</div>';
  }
  const cp = d.current_phase;
  const stress = marketStressFromSaw(d);
  const moveCls = cp.move_pct >= 0 ? 'saw-up' : 'saw-down';
  const prob = cp.historical_reach_probability;
  const probStr = prob == null ? '—' : (prob * 100).toFixed(0) + '%';
  const score = stress ? stress.score : 0;
  const stressCls = stress ? `stress-${stress.tone}` : 'stress-neut';
  const stressLabel = stress ? stress.label : 'Нет данных';
  const stressVol = stress ? fmtPct(stress.current_vol * 100, 1) : '—';
  const weekMove = stress && stress.week_move != null ? sawPct(stress.week_move) : '—';
  const phaseLabel = cp.label || 'Фаза не определена';
  return `
    <div class="pulse-main phase-${esc(cp.risk_level || 'neutral')}">
      <div class="pulse-copy">
        <span class="pulse-eyebrow">Market pulse</span>
        <h2>${esc(phaseLabel)}</h2>
        <p>${esc(cp.explanation || 'Индикатор показывает положение рынка относительно последнего подтвержденного экстремума MCFTR.')}</p>
      </div>
      <div class="pulse-stress ${stressCls}">
        <div class="stress-head">
          <span>Рыночное напряжение</span>
          <b>${stress ? score : '—'}${stress ? '/100' : ''}</b>
        </div>
        <div class="stress-track" aria-label="Индикатор рыночного напряжения">
          <span style="width:${stress ? Math.max(3, Math.min(100, score)) : 0}%"></span>
        </div>
        <div class="stress-foot">
          <span>${esc(stressLabel)}</span>
          <span>vol 20d: ${stressVol}</span>
        </div>
      </div>
    </div>
    <div class="pulse-strip">
      <div class="pulse-item"><span>MCFTR</span><b class="tnum">${ru(cp.current_price, 0)}</b><em>${esc(sawDate(cp.current_date))}</em></div>
      <div class="pulse-item"><span>От экстремума</span><b class="tnum ${moveCls}">${sawPct(cp.move_pct)}</b><em>${esc(sawDate(cp.anchor_date))}</em></div>
      <div class="pulse-item"><span>Историческая частота</span><b class="tnum">${probStr}</b><em>волны такой глубины</em></div>
      <div class="pulse-item"><span>5 торговых дней</span><b class="tnum ${String(weekMove).startsWith('-') ? 'saw-down' : 'saw-up'}">${weekMove}</b><em>по MCFTR</em></div>
    </div>
  `;
}

function marketSignalsHTML() {
  const points = marketSeriesPoints(SAW_DATA);
  const move20 = trailingMove(points, 20);
  const move60 = trailingMove(points, 60);
  const dd1y = marketDrawdown(points, 252);
  const mlRows = MARLAMOV && MARLAMOV.rows ? MARLAMOV.rows : [];
  const spreads = mlRows.map((r) => r.spread).filter(isNum);
  const positiveSpread = spreads.filter((v) => v > 0).length;
  const sortedSpreads = spreads.slice().sort((a, b) => a - b);
  const mid = Math.floor(sortedSpreads.length / 2);
  const medianSpread = sortedSpreads.length
    ? (sortedSpreads.length % 2 ? sortedSpreads[mid] : (sortedSpreads[mid - 1] + sortedSpreads[mid]) / 2)
    : null;
  const meta = DATA && DATA.meta ? DATA.meta : {};
  const fresh = isNum(meta.n_price_fresh) ? meta.n_price_fresh : null;
  const total = isNum(meta.n_total) ? meta.n_total : null;
  const stale = Boolean(meta.prices_stale);
  const freshShare = fresh != null && total ? fresh / total : null;
  const goodVerdicts = DATA && DATA.tickers ? DATA.tickers.filter((t) => (t.verdict || {}).color === 'good').length : null;
  const okTickers = DATA && DATA.tickers ? DATA.tickers.filter((t) => t.status === 'ok').length : null;
  const card = (label, value, note, tone) => `
    <div class="signal-card signal-${tone || 'neut'}">
      <span>${esc(label)}</span>
      <b>${value}</b>
      <em>${esc(note || '')}</em>
    </div>`;
  if (!SAW_DATA && !DATA && !MARLAMOV) {
    return '<div class="pulse-loading muted">Загрузка ежедневных сигналов...</div>';
  }
  return [
    card(
      'Импульс MCFTR',
      move20 == null ? '—' : sawPct(move20),
      move60 == null ? '20 торговых дней' : `60 дней: ${sawPct(move60)}`,
      signalTone(move20, true, -0.03, -0.08)
    ),
    card(
      'Просадка от 1Y high',
      dd1y == null ? '—' : sawPct(dd1y),
      'по индексу полной доходности',
      signalTone(dd1y, true, -0.07, -0.15)
    ),
    card(
      'Дивидендный спред',
      spreads.length ? `${positiveSpread}/${spreads.length} выше RFR` : '—',
      medianSpread == null ? 'нужен marlamov.json' : `медиана: ${sawPct(medianSpread)}`,
      positiveSpread > 0 ? 'warn' : 'risk'
    ),
    card(
      'Свежесть цен',
      fresh != null && total != null ? `${fresh}/${total}` : '—',
      stale ? 'часть цен устарела' : (freshShare != null ? `${fmtPct(freshShare * 100, 0)} бумаг со свежей ценой` : 'MOEX snapshot'),
      stale ? 'warn' : 'good'
    ),
    card(
      'Сильные карточки',
      goodVerdicts != null && okTickers != null ? `${goodVerdicts}/${okTickers}` : '—',
      'вердикт good среди бумаг с данными',
      goodVerdicts && okTickers && goodVerdicts / okTickers >= 0.2 ? 'good' : 'neut'
    ),
  ].join('');
}

function loadLWC(cb) {
  if (window.LightweightCharts) { cb(); return; }
  if (window.__lwc) { window.__lwc.push(cb); return; }
  window.__lwc = [cb];
  const s = document.createElement('script');
  s.src = LWC_SRC; s.async = true;
  s.onload = () => { const q = window.__lwc; window.__lwc = null; q.forEach((f) => f()); };
  s.onerror = () => { const q = window.__lwc; window.__lwc = null; q.forEach((f) => f(new Error('LWC'))); };
  document.head.appendChild(s);
}

function renderMarketSaw() {
  const body = document.getElementById('saw-body');
  body.innerHTML = '<div class="saw-loading muted">Загрузка индекса MCFTR…</div>';
  loadMarketSaw((err) => {
    if (err || !SAW_DATA) { body.innerHTML = sawErrorHTML(); return; }
    body.innerHTML = sawUIHTML(SAW_DATA);
    loadLWC((lerr) => {
      const c = document.getElementById('saw-chart');
      if (lerr || !window.LightweightCharts) { if (c) c.innerHTML = '<div class="muted saw-chart-fallback">График недоступен (не загрузилась графическая библиотека). Расчёт фазы выше — корректен.</div>'; return; }
      try { sawChart(SAW_DATA); } catch (e) { console.error('[saw] chart:', e); if (c) c.innerHTML = '<div class="muted saw-chart-fallback">Не удалось построить график.</div>'; }
    });
  });
}

function sawErrorHTML() {
  return `<div class="saw-fallback">
    <b>Данные MCFTR временно недоступны.</b> Индикатор фазы рынка не обновлён.
    <div class="saw-disc">Индикатор не является индивидуальной инвестиционной рекомендацией. Он показывает историческую фазу рынка на основе индекса MCFTR и не прогнозирует будущую доходность.</div>
  </div>`;
}

function sawUIHTML(d) {
  const cp = d.current_phase;
  const TT_MCFTR = 'MCFTR — индекс полной доходности МосБиржи. В отличие от ценового IMOEX, он учитывает дивиденды, поэтому лучше подходит для оценки реального движения рынка.';
  const TT_FREQ = 'Доля прошлых завершённых волн, которые достигали такой же или большей амплитуды. Это не прогноз, а историческая статистика.';
  const prob = cp.historical_reach_probability;
  const probStr = (prob == null) ? '—' : (prob * 100).toFixed(0) + '%';
  const moveCls = cp.move_pct >= 0 ? 'saw-up' : 'saw-down';

  const stale = d.stale
    ? `<div class="saw-stale">⚠️ Данные могут быть неактуальны: последняя торговая дата MCFTR — ${esc(sawDate(d.data_last))} (${d.stale_days} дн. назад).</div>`
    : '';

  return `
    <p class="saw-sub">Модель показывает, где сейчас находится рынок относительно последнего значимого минимума или максимума индекса <span class="saw-help" data-tooltip="${esc(TT_MCFTR)}">MCFTR ⓘ</span>.</p>
    <div class="saw-fresh muted">Обновлено: ${esc((d.generated_at || '').replace('T', ' ').slice(0, 16))} · Последняя торговая дата MCFTR: <b>${esc(sawDate(d.data_last))}</b></div>
    ${stale}

    ${sawGaugeHTML(d)}

    <div class="saw-charts-row">
      <div class="saw-col saw-chart-col">
        <div class="saw-col-title">Динамика MCFTR и зигзаг волн</div>
        <div id="saw-chart"></div>
        <div class="saw-chart-legend muted"><span class="lg-line"></span> MCFTR &nbsp; <span class="lg-zz"></span> зигзаг &nbsp; <span class="lg-low">▲</span> минимум &nbsp; <span class="lg-high">▼</span> максимум</div>
      </div>
      <div class="saw-col saw-distro-col">
        <div class="saw-col-title">Распределение амплитуд волн <span class="saw-help" data-tooltip="Каждая точка — завершённая волна: её амплитуда (ось X, слева просадки, справа ралли) и доля волн с такой же или большей амплитудой (ось Y). Центр — полоса порога ±9,5%. Точка «сейчас» — текущее движение на survival-кривой.">ⓘ</span></div>
        <div class="saw-distro">${sawDistroSVG(d)}</div>
      </div>
    </div>

    ${sawMethodHTML(d)}

    <div class="saw-disc">Индикатор не является индивидуальной инвестиционной рекомендацией. Он показывает историческую фазу рынка на основе индекса полной доходности MCFTR и не прогнозирует будущую доходность.</div>
  `;
}

function sawGaugeHTML(d) {
  const cp = d.current_phase;
  const dir = cp.direction;
  const zones = d.zones[dir];
  const lastThr = zones[zones.length - 1].thr;
  const max = lastThr + 0.10;
  const moveAbs = Math.min(Math.abs(cp.move_pct), max);
  const pos = (moveAbs / max) * 100;
  const segs = zones.map((z, i) => {
    const from = z.thr;
    const to = (i + 1 < zones.length) ? zones[i + 1].thr : max;
    const w = ((to - from) / max) * 100;
    const short = (SAW_SHORT[dir] && SAW_SHORT[dir][z.risk]) || z.label;
    const active = z.risk === cp.risk_level ? ' saw-seg-active' : '';
    return `<span class="saw-seg phase-${esc(z.risk)}${active}" style="width:${w.toFixed(2)}%"><span class="saw-seg-lbl">${esc(short)}</span></span>`;
  }).join('');
  return `<div class="saw-gauge">
    <div class="saw-gauge-track">${segs}
      <span class="saw-gauge-marker" style="left:${pos.toFixed(2)}%"><span class="saw-gauge-marker-lbl">${sawPct(cp.move_pct)}</span></span>
    </div>
    <div class="saw-gauge-scale muted"><span>0%</span><span>порог ±9,5%</span><span>${(max * 100).toFixed(0)}%+</span></div>
  </div>`;
}

function sawMethodHTML(d) {
  const us = d.waves_stats.up || {}, ds = d.waves_stats.down || {};
  const yrs = ((new Date(d.data_last) - new Date(d.series[0][0])) / (365.25 * 864e5)).toFixed(0);
  return `<details class="saw-method card">
    <summary>Методика и ограничения</summary>
    <ul class="saw-method-list">
      <li><b>Как считается фаза.</b> Берём индекс полной доходности <b>MCFTR</b> (не ценовой IMOEX — иначе дивидендные гэпы давали бы ложные сигналы падения). Через zigzag-логику отмечаем локальные максимумы и минимумы: смена волны подтверждается обратным движением на <b>±9,5%</b> <span class="saw-help" data-tooltip="Порог 9,5% используется для подтверждения смены рыночной волны. Мелкие колебания игнорируются, чтобы не ловить шум.">ⓘ</span>. Текущее движение считается от последнего <b>подтверждённого</b> экстремума.</li>
      <li><b>Историческая частота.</b> Доля прошлых завершённых волн того же направления, чья амплитуда была такой же или больше текущей. Это survival-статистика прошлого, а не вероятность будущего движения.</li>
      <li><b>База волн.</b> За ~${yrs} лет: ${us.count || 0} завершённых ростовых волн (медиана ${us.median_amp != null ? '+' + (us.median_amp * 100).toFixed(0) + '%' : '—'}) и ${ds.count || 0} падающих (медиана ${ds.median_amp != null ? '−' + (ds.median_amp * 100).toFixed(0) + '%' : '—'}). Текущая незавершённая волна в статистику не входит.</li>
      <li><b>Ограничения.</b> Модель вдохновлена публично описанной swing/zigzag-логикой фаз рынка; это воспроизводимая публичная версия, не точная копия какой-либо закрытой методики. Порог и зоны фиксированы (без задней подгонки). Индикатор показывает фазу, а не прогноз, и не учитывает фундаментал отдельных бумаг.</li>
    </ul>
  </details>`;
}

// «Пила» — распределение амплитуд завершённых волн (survival-кривая): для каждой волны
// точка (амплитуда, доля волн с такой же/большей амплитудой). Слева просадки, справа ралли,
// центр — полоса порога ±9,5%. Маркер «сейчас» ложится ровно на кривую. Чистый SVG.
function sawDistroSVG(d) {
  const W = 720, H = 480, ML = 46, MR = 26, MT = 42, MB = 52;
  const px0 = ML, px1 = W - MR, py0 = MT, py1 = H - MB;
  const niceUp = (x) => Math.ceil(x / 10) * 10;
  const survPts = (arr) => arr.slice().sort((a, b) => a - b).map((a, i, A) => ({ amp: a, s: (A.length - i) / A.length }));
  const up = survPts(d.waves.filter((w) => w.direction === 'up').map((w) => Math.abs(w.amplitude_pct) * 100));
  const dn = survPts(d.waves.filter((w) => w.direction === 'down').map((w) => Math.abs(w.amplitude_pct) * 100));
  if (!up.length || !dn.length) return '';
  const xMaxUp = niceUp(Math.max(...up.map((p) => p.amp)));
  const xMaxDn = niceUp(Math.max(...dn.map((p) => p.amp)));
  const xMin = -xMaxDn, xMax = xMaxUp;
  const xS = (pct) => px0 + (pct - xMin) / (xMax - xMin) * (px1 - px0);
  const yS = (s) => py1 - s * (py1 - py0);

  // полилинии (по X слева направо)
  const dnPath = dn.map((p) => [xS(-p.amp), yS(p.s)]).sort((a, b) => a[0] - b[0]);
  const upPath = up.map((p) => [xS(p.amp), yS(p.s)]).sort((a, b) => a[0] - b[0]);
  const poly = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const dots = (pts, cls) => pts.map((p) => `<circle class="${cls}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6"/>`).join('');

  // оси
  let xticks = '';
  for (let t = Math.ceil(xMin / 30) * 30; t <= xMax; t += 30) {
    const x = xS(t).toFixed(1);
    xticks += `<line class="sd-grid" x1="${x}" y1="${py0}" x2="${x}" y2="${py1}"/>`
      + `<text class="sd-tick" x="${x}" y="${py1 + 16}" text-anchor="middle">${t > 0 ? '+' + t : t}%</text>`;
  }
  let yticks = '';
  for (let s = 0; s <= 100; s += 20) {
    const y = yS(s / 100).toFixed(1);
    yticks += `<line class="sd-grid" x1="${px0}" y1="${y}" x2="${px1}" y2="${y}"/>`
      + `<text class="sd-tick" x="${px0 - 6}" y="${(+y + 3).toFixed(1)}" text-anchor="end">${s}%</text>`;
  }
  const band = `<rect class="sd-band" x="${xS(-9.5).toFixed(1)}" y="${py0}" width="${(xS(9.5) - xS(-9.5)).toFixed(1)}" height="${py1 - py0}"/>`;
  const zero = `<line class="sd-zero" x1="${xS(0).toFixed(1)}" y1="${py0}" x2="${xS(0).toFixed(1)}" y2="${py1}"/>`;

  // маркер «сейчас»
  const cp = d.current_phase;
  let now = '';
  if (cp.historical_reach_probability != null) {
    const mx = xS(cp.move_pct * 100), my = yS(cp.historical_reach_probability);
    now = `<line class="sd-now-l" x1="${mx.toFixed(1)}" y1="${py1}" x2="${mx.toFixed(1)}" y2="${my.toFixed(1)}"/>`
      + `<circle class="sd-now" cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="4.5"/>`
      + `<text class="sd-now-t" x="${mx.toFixed(1)}" y="${(my - 10).toFixed(1)}" text-anchor="${cp.direction === 'down' ? 'start' : 'end'}">сейчас ${sawPct(cp.move_pct)} · ${(cp.historical_reach_probability * 100).toFixed(0)}%</text>`;
  }

  return `<svg class="sd-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Распределение амплитуд волн MCFTR">
    <text class="sd-side sd-side-dn" x="${px0}" y="${MT - 16}" text-anchor="start">◀ индекс падает</text>
    <text class="sd-side sd-side-up" x="${px1}" y="${MT - 16}" text-anchor="end">индекс растёт ▶</text>
    <text class="sd-axis-y" x="${px0 - 30}" y="${(py0 + py1) / 2}" text-anchor="middle" transform="rotate(-90 ${px0 - 30} ${(py0 + py1) / 2})">доля волн ≥ амплитуды</text>
    ${yticks}${xticks}${band}${zero}
    <path class="sd-line-dn" d="${poly(dnPath)}"/>${dots(dnPath, 'sd-dot-dn')}
    <path class="sd-line-up" d="${poly(upPath)}"/>${dots(upPath, 'sd-dot-up')}
    ${now}
  </svg>`;
}

function sawChart(d) {
  const el = document.getElementById('saw-chart');
  if (!el || !window.LightweightCharts) return;
  el.innerHTML = '';
  const LC = window.LightweightCharts;
  const chart = LC.createChart(el, {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#5A6472', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 11 },
    grid: { vertLines: { color: '#EEF1F6' }, horzLines: { color: '#EEF1F6' } },
    rightPriceScale: { borderColor: '#E6E9F0' },
    timeScale: { borderColor: '#E6E9F0', timeVisible: false, rightOffset: 6 },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });
  const line = chart.addLineSeries({ color: '#A9B7D9', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  line.setData(d.series.map(([t, v]) => ({ time: t, value: v })));
  const zz = chart.addLineSeries({ color: '#4C5C86', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  zz.setData(d.extremes.map((e) => ({ time: e.date, value: e.price })));
  const cp = d.current_phase;
  const markers = d.extremes.map((e) => ({
    time: e.date,
    position: e.type === 'high' ? 'aboveBar' : 'belowBar',
    color: e.type === 'high' ? '#A2452C' : '#1E6F4C',
    shape: e.type === 'high' ? 'arrowDown' : 'arrowUp',
  }));
  markers.sort((a, b) => (a.time < b.time ? -1 : 1));
  zz.setMarkers(markers);
  // «сейчас» — на линии MCFTR (у зигзага нет точки на текущей дате → маркер уехал бы к
  // последнему экстремуму). Кладём на реальную последнюю точку ряда.
  line.setMarkers([{ time: cp.current_date, position: 'belowBar', color: '#2E3440', shape: 'circle', text: 'сейчас' }]);
  line.createPriceLine({
    price: cp.anchor_price,
    color: cp.direction === 'down' ? '#A2452C' : '#1E6F4C',
    lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true,
    title: (cp.direction === 'down' ? 'макс ' : 'мин ') + ru(cp.anchor_price, 0) + ' (' + sawPct(cp.move_pct) + ')',
  });
  // по умолчанию — последние ~3 года (текущая фаза крупно) + запас баров справа,
  // чтобы подпись «сейчас» у последней точки не упиралась в край. История — скроллом/зумом.
  try {
    const n = d.series.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 1 - 760), to: n - 1 + 72 });
  } catch (e) { chart.timeScale().fitContent(); }

  // тултип при наведении: цена + просадка/ралли от ближайшего слева подтверждённого экстремума
  el.style.position = 'relative';
  const tip = document.createElement('div');
  tip.className = 'saw-chart-tip';
  tip.style.display = 'none';
  el.appendChild(tip);
  const ex = d.extremes;                                   // отсортированы по дате ↑
  const timeStr = (t) => (typeof t === 'string') ? t
    : (t && t.year) ? `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}` : null;
  const pivotAt = (ts) => { let p = null; for (let i = 0; i < ex.length; i++) { if (ex[i].date <= ts) p = ex[i]; else break; } return p; };
  chart.subscribeCrosshairMove((param) => {
    const pd = param.seriesData && param.seriesData.get(line);
    const ts = timeStr(param.time);
    if (!param.point || !ts || !pd || pd.value == null) { tip.style.display = 'none'; return; }
    const price = pd.value, piv = pivotAt(ts);
    let html = `<b>${ru(price, 0)}</b>`;
    if (piv) {
      const pct = price / piv.price - 1;
      html += ` <span class="${pct >= 0 ? 'up' : 'down'}">${sawPct(pct)}</span>`
        + ` <span class="dim">${piv.type === 'high' ? 'от макс' : 'от мин'} ${sawDate(piv.date)}</span>`;
    }
    tip.innerHTML = html;
    tip.style.display = 'block';
    let x = param.point.x + 14; if (x + tip.offsetWidth > el.clientWidth) x = param.point.x - tip.offsetWidth - 14;
    let y = param.point.y + 14; if (y + tip.offsetHeight > el.clientHeight) y = param.point.y - tip.offsetHeight - 14;
    tip.style.left = Math.max(2, x) + 'px';
    tip.style.top = Math.max(2, y) + 'px';
  });
}

// ══════════════════════════════════════════════════════════════════════════
// Облигации: квантовый скринер + кривая КБД + калькулятор портфеля.
// Все значения — из site/bonds/*.json (генерит bonds/update_bonds.py по реальному ISS).
// Это не ИИР; справедливая цена опирается на ПЛОСКИЙ спред рейтинга (модельное допущение).
// ══════════════════════════════════════════════════════════════════════════
let BONDS = null;
const CHARTJS_SRC = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';
const RATING_GROUP = (r) => { r = String(r || ''); return r.startsWith('AAA') ? 'aaa' : r.startsWith('AA') ? 'aa' : r.startsWith('A') ? 'a' : 'bbb'; };
const RATING_COLOR = { aaa: '#1E6F4C', aa: '#7FB069', a: '#D9A521', bbb: '#D77A33' };
const RATING_SOURCE_RU = { acra: 'АКРА', expert_ra: 'Эксперт РА', nkr: 'НКР' };
const HORIZON_RU = { short: 'Короткий (0–1 год)', mid: 'Средний (1–3 года)', long: 'Длинный (>3 лет)' };
const rub0 = (x) => isNum(x) ? Math.round(x).toLocaleString('ru-RU') + ' ₽' : ND;

function shortIsoDate(value) {
  const p = String(value || '').slice(0, 10).split('-');
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : String(value || '');
}

function ratingSources(meta) {
  const sources = ((meta || {}).ratings || {}).sources || {};
  return Object.entries(sources).filter(([, v]) => v && (v.status === 'ok' || v.status === 'cached'))
    .map(([k, v]) => (RATING_SOURCE_RU[k] || k) + (v.status === 'cached' ? ' (last-good)' : '')).join(' · ');
}

function officialRatingHTML(row, badgeClass) {
  if (!row || !row.rating) return '<span class="muted" title="Действующий официальный рейтинг выпуска не найден">—</span>';
  const records = Array.isArray(row.rating_records) ? row.rating_records : [];
  const detail = records.length
    ? records.map((r) => `${r.rating_agency || ''} ${r.raw_rating || r.rating || ''}${r.rating_date ? ' от ' + shortIsoDate(r.rating_date) : ''}`).join('; ')
    : `${row.rating_agency || 'официальное агентство'}${row.rating_date ? ' · ' + shortIsoDate(row.rating_date) : ''}`;
  const badge = `<span class="${badgeClass} r-${RATING_GROUP(row.rating)}" title="Рейтинг выпуска. ${esc(detail)}">${esc(row.rating)}</span>`;
  const linked = row.rating_source_url
    ? `<a class="rating-source-link" href="${esc(row.rating_source_url)}" target="_blank" rel="noopener" aria-label="Открыть официальный рейтинг выпуска">${badge}</a>`
    : badge;
  return `<span class="rating-official">${linked}<small>${esc(row.rating_agency || '')}${row.rating_date ? ' · ' + esc(shortIsoDate(row.rating_date)) : ''}</small></span>`;
}

function wireBonds() {
  const el = document.getElementById('bonds');
  if (!el) return;
  el.hidden = false;
  el.addEventListener('toggle', function () {
    if (this.open && !this.dataset.shown) { this.dataset.shown = '1'; renderBonds(); }
  });
}

function loadBonds(cb) {
  if (BONDS) { cb(); return; }
  const t = Date.now();
  Promise.all([
    fetch('bonds/screener.json?t=' + t, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('screener ' + r.status); return r.json(); }),
    fetch('bonds/chart_data.json?t=' + t, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('chart ' + r.status); return r.json(); }),
    fetch('bonds/portfolios.json?t=' + t, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('portfolios ' + r.status); return r.json(); }),
  ]).then(([screener, chart, portfolios]) => {
    if (!screener || !screener.bonds || !chart) throw new Error('пустые/битые JSON облигаций');
    BONDS = { meta: screener.meta || {}, bonds: screener.bonds, chart, portfolios: (portfolios && portfolios.portfolios) || {} };
    cb();
  }).catch((e) => { console.error('[bonds] не загрузились:', e); cb(e); });
}

function loadChartJS(cb) {
  if (window.Chart) { cb(); return; }
  if (window.__cjs) { window.__cjs.push(cb); return; }
  window.__cjs = [cb];
  const s = document.createElement('script');
  s.src = CHARTJS_SRC; s.async = true;
  s.onload = () => { const q = window.__cjs; window.__cjs = null; q.forEach((f) => f()); };
  s.onerror = () => { const q = window.__cjs; window.__cjs = null; q.forEach((f) => f(new Error('Chart.js'))); };
  document.head.appendChild(s);
}

function renderBonds() {
  const body = document.getElementById('bonds-body');
  body.innerHTML = '<div class="bonds-loading muted">Загрузка скринера облигаций…</div>';
  loadBonds((err) => {
    if (err || !BONDS) { body.innerHTML = bondsErrorHTML(); return; }
    body.innerHTML = bondsUIHTML(BONDS);
    wireBondsCalc();
    loadChartJS((cerr) => {
      const c = document.getElementById('bonds-chart-wrap');
      if (cerr || !window.Chart) { if (c) c.innerHTML = '<div class="muted bonds-chart-fallback">График КБД недоступен (не загрузилась Chart.js). Таблица и калькулятор ниже — работают.</div>'; return; }
      try { bondsChart(BONDS); } catch (e) { console.error('[bonds] chart:', e); }
    });
  });
}

function bondsErrorHTML() {
  return `<div class="bonds-fallback"><b>Данные облигаций временно недоступны.</b> Скринер не обновлён.
    <div class="bonds-disc">Не индивидуальная инвестиционная рекомендация.</div></div>`;
}

function bondsUIHTML(d) {
  const m = d.meta;
  const upd = (m.updated || '').replace('T', ' ').slice(0, 16);
  const checked = shortIsoDate(((m.ratings || {}).checked_at || '').slice(0, 10));
  const agencies = ratingSources(m);
  return `
    <div class="bonds-fresh muted">Обновлено: ${esc(upd)} · бумаг: <b>${d.bonds.length}</b> · цены: MOEX ISS · рейтинги выпусков: ${esc(agencies || 'источник временно недоступен')}${checked ? ' · проверено ' + esc(checked) : ''}</div>
    <div class="bonds-note">${esc(m.note || '')}</div>

    <div class="bonds-section-title">Карта рынка: кривая КБД (ОФЗ) и корпоративные облигации</div>
    <div id="bonds-chart-wrap" class="bonds-chart-wrap"><canvas id="bonds-chart"></canvas></div>
    <div class="bonds-chart-legend muted">Линия — бескупонная кривая ОФЗ (КБД MOEX). Точки — корпораты (цвет = рейтинг). Выше линии = премия к ОФЗ. Наведи на точку.</div>

    <div class="bonds-section-title">Калькулятор портфеля</div>
    <div class="bonds-calc-controls">
      <label>Горизонт (целевая дюрация)<select id="bonds-horizon">
        <option value="short">${HORIZON_RU.short}</option>
        <option value="mid" selected>${HORIZON_RU.mid}</option>
        <option value="long">${HORIZON_RU.long}</option>
      </select></label>
      <label>Сумма, ₽<input type="number" id="bonds-capital" value="1000000" min="0" step="100000"></label>
    </div>
    <div id="bonds-calc-out"></div>

    <div class="bonds-section-title">Скринер (${d.bonds.length} бумаг, сортировка по апсайду)</div>
    ${bondsTableHTML(d.bonds)}

    <div class="bonds-disc">Индикатор не является индивидуальной инвестиционной рекомендацией. Справедливая цена опирается на плоский спред рейтинга (модельное допущение) — крупный «апсайд» у имён A-/BBB отражает некомпенсированную в модели кредит-премию, а не гарантированную недооценку. Данные MOEX ISS.</div>
  `;
}

function bondsTableHTML(bonds) {
  const rows = bonds.slice().sort((a, b) => b.deviation - a.deviation).map((x) => {
    const dev = isNum(x.deviation) ? (x.deviation >= 0 ? '+' : '') + x.deviation.toFixed(1) + '%' : ND;
    return `<tr>
      <td class="b-name">${esc(x.name)}</td>
      <td>${officialRatingHTML(x, 'b-rating')}</td>
      <td class="tnum">${isNum(x.price_market) ? x.price_market.toFixed(2) : ND}</td>
      <td class="tnum">${isNum(x.ytm_market) ? x.ytm_market.toFixed(2) + '%' : ND}</td>
      <td class="tnum b-muted">${isNum(x.ytm_fair) ? x.ytm_fair.toFixed(2) + '%' : ND}</td>
      <td class="tnum ${x.deviation >= 0 ? 'b-up' : 'b-down'}">${dev}</td>
      <td class="tnum">${isNum(x.ytm_net) ? x.ytm_net.toFixed(2) + '%' : ND}</td>
      <td class="tnum">${isNum(x.duration_years) ? x.duration_years.toFixed(2) : ND}</td>
      <td class="tnum b-muted">${isNum(x.coupon_pct) ? x.coupon_pct.toFixed(1) + '%' : ND}</td>
      <td class="tnum b-muted">${esc(x.maturity || ND)}</td>
    </tr>`;
  }).join('');
  return `<div class="bonds-table-wrap"><table class="bonds-table">
    <thead><tr>
      <th>Бумага</th><th>Рейтинг</th><th>Цена</th>
      <th data-tooltip="Доходность к погашению по рыночной цене (WAPRICE), считается из реальных потоков">YTM</th>
      <th data-tooltip="Справедливая YTM по G-кривой MOEX + плоский спред рейтинга">Fair YTM</th>
      <th data-tooltip="Апсайд справедливой цены к рыночной. Плоский спред занижает кредит-премию A-/BBB → большой «+» = модельное допущение">Апсайд</th>
      <th data-tooltip="Чистая YTM после НДФЛ 13% (купоны и ценовой доход)">YTM−налог</th>
      <th>Дюрация</th><th>Купон</th><th>Погашение</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function bondsChart(d) {
  const ctx = document.getElementById('bonds-chart');
  if (!ctx || !window.Chart) return;
  const ofz = (d.chart.ofz_curve || []).map((p) => ({ x: p.t, y: p.yield }));
  const corp = (d.chart.corp_points || []).filter((c) => isNum(c.duration) && isNum(c.ytm));
  if (window.__bondsChart) { try { window.__bondsChart.destroy(); } catch (e) { /* noop */ } }
  window.__bondsChart = new window.Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        { label: 'Кривая ОФЗ (КБД)', type: 'line', data: ofz, parsing: false,
          borderColor: '#4C5C86', backgroundColor: 'transparent', pointRadius: 0, borderWidth: 2, tension: 0.3, order: 2 },
        { label: 'Корпораты', data: corp.map((c) => ({ x: c.duration, y: c.ytm, _c: c })), parsing: false,
          pointBackgroundColor: corp.map((c) => RATING_COLOR[RATING_GROUP(c.rating)]),
          pointBorderColor: '#fff', pointBorderWidth: 1, pointRadius: 5, pointHoverRadius: 7, order: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { title: { display: true, text: 'Дюрация, лет' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
        y: { title: { display: true, text: 'YTM, %' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
      },
      plugins: {
        legend: { labels: { color: '#5A6472', usePointStyle: true } },
        tooltip: {
          callbacks: {
            label: (item) => {
              const c = item.raw._c;
              if (!c) return `ОФЗ: ${item.parsed.y.toFixed(2)}% @ ${item.parsed.x} лет`;
              const dev = (c.deviation >= 0 ? '+' : '') + Number(c.deviation).toFixed(1) + '%';
              return [`${c.name} (${c.rating})`, `YTM: ${Number(c.ytm).toFixed(2)}%`,
                `Fair YTM: ${Number(c.ytm_fair).toFixed(2)}%`, `Апсайд: ${dev}`,
                `Дюрация: ${Number(c.duration).toFixed(2)} лет`];
            },
          },
        },
      },
    },
  });
}

function wireBondsCalc() {
  ['bonds-horizon', 'bonds-capital'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', bondsCalc);
  });
  bondsCalc();
}

function bondsCalc() {
  const out = document.getElementById('bonds-calc-out');
  if (!out) return;
  const horizon = document.getElementById('bonds-horizon').value;
  const capital = Math.max(0, +document.getElementById('bonds-capital').value || 0);
  const port = BONDS.portfolios[horizon];
  if (!port || !port.bonds || !port.bonds.length) {
    out.innerHTML = `<div class="bonds-calc-empty muted">Для горизонта «${esc(HORIZON_RU[horizon])}» недостаточно ликвидных кандидатов в скринере.</div>`;
    return;
  }
  let totalSpent = 0, capped = false;
  const lines = port.bonds.map((b) => {
    const costPerLot = b.price_market / 100 * b.lot_value;             // ₽ за лот (чистая цена)
    let allocRub = b.weight * capital;
    if (b.max_rub && allocRub > b.max_rub) { allocRub = b.max_rub; capped = true; }  // ≤5% дневного оборота
    const lots = costPerLot > 0 ? Math.floor(allocRub / costPerLot) : 0;
    const spent = lots * costPerLot;
    totalSpent += spent;
    return { b, costPerLot, lots, spent };
  });
  const cash = capital - totalSpent;
  const rows = lines.map(({ b, costPerLot, lots, spent }) => `<tr>
    <td class="b-name">${esc(b.name)}</td>
    <td>${officialRatingHTML(b, 'b-rating')}</td>
    <td class="tnum">${(b.weight * 100).toFixed(1)}%</td>
    <td class="tnum b-strong">${lots}</td>
    <td class="tnum">${rub0(costPerLot)}</td>
    <td class="tnum">${rub0(spent)}</td>
    <td class="tnum">${capital > 0 ? (spent / capital * 100).toFixed(1) + '%' : '—'}</td>
  </tr>`).join('');
  out.innerHTML = `
    <div class="bonds-calc-summary">
      <div class="bonds-kpi"><span class="k">Чистая YTM портфеля</span><span class="v">${isNum(port.port_ytm_net) ? port.port_ytm_net.toFixed(2) + '%' : ND}</span></div>
      <div class="bonds-kpi"><span class="k">Дюрация</span><span class="v">${isNum(port.port_duration) ? port.port_duration.toFixed(2) + ' лет' : ND}</span></div>
      <div class="bonds-kpi"><span class="k">Вложено</span><span class="v">${rub0(totalSpent)}</span></div>
      <div class="bonds-kpi"><span class="k">Остаток кэша</span><span class="v">${rub0(cash)}</span></div>
    </div>
    <div class="bonds-table-wrap"><table class="bonds-table bonds-calc-table">
      <thead><tr><th>Бумага</th><th>Рейтинг</th><th>Вес</th><th>Лотов</th><th>Цена лота</th><th>Сумма</th><th>Факт. доля</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${capped ? '<div class="bonds-calc-note muted">⚠️ Часть позиций ограничена 5% дневного оборота бумаги (ликвидность) → остаток ушёл в кэш.</div>' : ''}
    <div class="bonds-calc-note muted">Лоты округлены вниз до целого; цена лота — чистая (без НКД). Инструкция справочная, не ИИР.</div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// Форвардная доходность (таблица Марламова, 2 года). Всё из site/marlamov.json
// (генерит scripts/build_forward_yield.py). Yield2 — от ОЧИЩЕННОЙ базы P_adj=P−Div1·0.87.
// ══════════════════════════════════════════════════════════════════════════
MARLAMOV = null;
const ML_SIG = { 'ACCUMULATE': 'good', 'HOLD': 'neut', 'FIX PROFIT': 'risk', '—': 'neut' };

function wireMarlamov() {
  const el = document.getElementById('marlamov');
  if (!el) return;
  el.hidden = false;
  el.addEventListener('toggle', function () {
    if (this.open && !this.dataset.shown) { this.dataset.shown = '1'; renderMarlamov(); }
  });
}

function loadMarlamov(cb) {
  if (MARLAMOV) { cb(); return; }
  fetch('marlamov.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.rows) throw new Error('пустой marlamov.json'); MARLAMOV = j; cb(); })
    .catch((e) => { console.error('[marlamov] не загрузился:', e); cb(e); });
}

function renderMarlamov() {
  const body = document.getElementById('ml-body');
  body.innerHTML = '<div class="ml-loading muted">Загрузка форвардной доходности…</div>';
  loadMarlamov((err) => {
    body.innerHTML = (err || !MARLAMOV) ? mlErrorHTML() : mlUIHTML(MARLAMOV);
  });
}

function mlErrorHTML() {
  return `<div class="ml-fallback"><b>Данные форвардной доходности недоступны.</b>
    <div class="ml-disc">Не индивидуальная инвестиционная рекомендация.</div></div>`;
}

function mlUIHTML(d) {
  const m = d.meta;
  const regimeCls = m.regime === 'Risk-On' ? 'good' : m.regime === 'Risk-Off' ? 'risk' : 'neut';
  const upd = (m.updated || '').replace('T', ' ').slice(0, 16);
  const placeholders = d.rows.filter((r) => /заглушк/i.test(r.note || '')).length;
  const TT = 'Доходность 2-го года считается от ОЧИЩЕННОЙ базы: P_adj = Цена − Div1·0.87 (после получения первого чистого дивиденда ваша база затрат снижается).';
  return `
    <div class="ml-macro">
      <span class="ml-chip ${regimeCls}">Режим рынка: <b>${esc(m.regime || '—')}</b></span>
      <span class="ml-chip neut">IMOEX <b>${m.imoex != null ? ru(m.imoex, 0) : '—'}</b> / SMA200 <b>${m.sma200 != null ? ru(m.sma200, 0) : '—'}</b></span>
      <span class="ml-chip neut">RFR (КБД 1Y) <b>${(m.rfr * 100).toFixed(1)}%</b></span>
      <span class="ml-chip neut">Налог <b>${Math.round((1 - m.net_tax) * 100)}%</b></span>
    </div>
    <p class="ml-sub">Чистая (после НДФЛ) дивдоходность на 2 года вперёд по авторским прогнозам. Доходность 2-го года — от <span class="ml-help" data-tooltip="${esc(TT)}">очищенной базы ⓘ</span>. Сигнал — спред <b>Yield2 − RFR</b>: ACCUMULATE (&gt;+1пп) / HOLD / FIX PROFIT (&lt;0).</p>
    ${placeholders ? `<div class="ml-banner">⚠️ Прогнозы Div2 сейчас — <b>заглушки (=Div1, без роста)</b> для ${placeholders} бумаг, поэтому при RFR ${(m.rfr * 100).toFixed(1)}% почти всё = FIX PROFIT. Впиши свои прогнозы роста дивидендов на 2-й год в <code>my_dividend_forecasts.json</code> — недооценённые по форвард-доходности станут ACCUMULATE.</div>` : ''}
    ${mlTableHTML(d.rows)}
    <div class="ml-fresh muted">Обновлено: ${esc(upd)} · бумаг: ${m.n} (с прогнозом Div2: ${m.n_with_div2}) · источник: MOEX ISS + модель</div>
    <div class="ml-disc">Не индивидуальная инвестиционная рекомендация. Div1 — прогноз модели, Div2 — авторский ввод; форвардная доходность зависит от точности прогнозов. Сигнал сравнивает 2-летнюю чистую дивдоходность с безрисковой ставкой ОФЗ.</div>
  `;
}

function mlTableHTML(rows) {
  const pct = (x, d = 1) => isNum(x) ? (x * 100).toFixed(d) + '%' : ND;
  const pp = (x) => isNum(x) ? (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + 'пп' : ND;
  const body = rows.map((r) => `<tr>
    <td class="ml-name"><b>${esc(r.ticker)}</b> <span class="muted">${esc(r.name || '')}</span></td>
    <td class="tnum">${isNum(r.price) ? ru(r.price, 2) : ND}</td>
    <td class="tnum">${isNum(r.div1) ? ru(r.div1, 2) : ND}</td>
    <td class="tnum">${isNum(r.div2) ? ru(r.div2, 2) : ND}</td>
    <td class="tnum">${pct(r.yield1)}</td>
    <td class="tnum ml-y2">${pct(r.yield2)}</td>
    <td class="tnum">${pct(r.total2)}</td>
    <td class="tnum ${isNum(r.spread) ? (r.spread >= 0 ? 'ml-up' : 'ml-down') : ''}">${pp(r.spread)}</td>
    <td><span class="ml-sig s-${ML_SIG[r.signal] || 'neut'}">${esc(r.signal)}</span></td>
    <td class="ml-note muted">${esc(r.note || '')}</td>
  </tr>`).join('');
  return `<div class="ml-table-wrap"><table class="ml-table">
    <thead><tr>
      <th>Бумага</th><th>Цена</th><th>Div 1</th><th>Div 2</th>
      <th data-tooltip="Чистая дивдоходность 1-го года: Div1·0.87 / Цена">Дох. 1 год</th>
      <th class="ml-y2" data-tooltip="Чистая дивдоходность 2-го года от ОЧИЩЕННОЙ базы: Div2·0.87 / (Цена − Div1·0.87)">Дох. 2 год</th>
      <th data-tooltip="Сумма чистых дивидендов за 2 выплаты к исходной цене">За 2 выпл.</th>
      <th data-tooltip="Spread = Доходность 2 года − RFR (безриск ОФЗ)">Спред</th>
      <th>Сигнал</th><th>Примечание</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// Методология (4 раздела) — из site/methodology.json. Единый честный источник
// допущений/ограничений вместо разрозненных UI-текстов.
// ══════════════════════════════════════════════════════════════════════════
let METHODOLOGY = null;
let DATA_COVERAGE = null;
SITE_FINANCIALS = null;

function loadSiteFinancials(cb) {
  if (SITE_FINANCIALS) { cb && cb(); return; }
  fetch('site_financials.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { SITE_FINANCIALS = j; cb && cb(); })
    .catch((e) => { console.warn('[site_financials]', e); cb && cb(e); });
}

function wireMethodology() {
  const el = document.getElementById('methodology');
  if (!el) return;
  el.hidden = false;
  el.addEventListener('toggle', function () {
    if (this.open && !this.dataset.shown) { this.dataset.shown = '1'; renderMethodology(); }
  });
}

function renderMethodology() {
  const body = document.getElementById('method-body');
  if (METHODOLOGY) { body.innerHTML = methodologyHTML(METHODOLOGY); return; }
  fetch('methodology.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { METHODOLOGY = j; body.innerHTML = methodologyHTML(j); })
    .catch((e) => { console.error('[methodology]', e); body.innerHTML = '<div class="muted" style="padding:10px 2px">Методология временно недоступна.</div>'; });
}

function methodologyHTML(j) {
  const secs = (j.sections || []).map((s) => `<div class="method-sec">
    <h4>${esc(s.title)}</h4>
    <dl>${(s.items || []).map((it) => `<dt>${esc(it.label)}</dt><dd>${esc(it.text)}</dd>`).join('')}</dl>
  </div>`).join('');
  return `<div class="method-grid">${secs}</div>
    <div class="method-disc-all">${esc(j.disclaimer || '')}</div>`;
}

function wireDataCoverage() {
  const el = document.getElementById('data-coverage');
  if (!el) return;
  el.hidden = false;
  el.addEventListener('toggle', function () {
    if (this.open && !this.dataset.shown) { this.dataset.shown = '1'; renderDataCoverage(); }
  });
}

function loadDataCoverage(cb) {
  if (DATA_COVERAGE) { cb && cb(); return; }
  fetch('site_coverage.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { DATA_COVERAGE = j; cb && cb(); })
    .catch((e) => { cb && cb(e); });
}

function renderDataCoverage() {
  const body = document.getElementById('coverage-body');
  if (!body) return;
  body.innerHTML = '<div class="muted" style="padding:10px 2px">Загрузка покрытия данных…</div>';
  loadDataCoverage((err) => {
    if (err || !DATA_COVERAGE) {
      body.innerHTML = '<div class="muted" style="padding:10px 2px">Покрытие данных пока не опубликовано. Старый рабочий контракт сайта остаётся активным.</div>';
      return;
    }
    const m = DATA_COVERAGE.meta || {};
    const q = DATA_COVERAGE.quality || {};
    const c = DATA_COVERAGE.coverage_status_counts || {};
    const sources = m.source_counts || {};
    const statuses = DATA_COVERAGE.source_status_counts || m.source_status_counts || {};
    const funnel = m.data_quality_funnel || m;
    const fmtCount = (v) => isNum(v) ? ru(v, 0) : (v == null ? '—' : String(v));
    const card = (lbl, val, note) => `<div class="coverage-card"><span>${esc(lbl)}</span><b>${esc(val == null ? '—' : val)}</b>${note ? `<em>${esc(note)}</em>` : ''}</div>`;
    const funnelCard = (lbl, val, note) => `<div class="quality-card"><span>${esc(lbl)}</span><b>${esc(fmtCount(val))}</b>${note ? `<em>${esc(note)}</em>` : ''}</div>`;
    const statusOrder = ['Official IFRS', 'SmartLab fallback', 'Conflict', 'Needs review', 'OCR candidate'];
    const badgeRows = statusOrder.map((k) => {
      const cls = k.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      return `<div class="coverage-badge coverage-badge-${cls}"><span>${esc(k)}</span><b>${esc(statuses[k] || 0)}</b></div>`;
    }).join('');
    const sourceRows = Object.keys(sources).sort().map((k) =>
      `<div class="coverage-source"><span>${esc(k)}</span><b>${esc(sources[k])}</b></div>`).join('');
    body.innerHTML = `<div class="quality-funnel">
        ${funnelCard('SmartLab facts', funnel.smartlab_facts, 'baseline')}
        ${funnelCard('Official IFRS processed facts', funnel.official_ifrs_processed_facts, 'не обязательно verified')}
        ${funnelCard('Verified official facts', funnel.verified_official_facts, 'проверенный слой')}
        ${funnelCard('Official report links found', funnel.official_report_links_found, 'metadata')}
        ${funnelCard('Audited links OK', funnel.audited_links_ok, 'link audit')}
        ${funnelCard('Downloaded reports', funnel.downloaded_audited_reports, 'controlled opt-in download')}
        ${funnelCard('Extracted reports', funnel.extracted_reports, 'факты уже извлечены')}
        ${funnelCard('Conflicts', funnel.conflicts, 'SmartLab vs IFRS')}
        ${funnelCard('Disclosure errors', funnel.disclosure_errors, 'timeout/WAF/404')}
        ${funnelCard('Fundamental values', m.smartlab_fundamental_values_total, 'SmartLab cleaned layer')}
        ${funnelCard('Fundamentals clean', m.smartlab_fundamental_values_clean, 'passed sanity checks')}
        ${funnelCard('Fundamentals hidden', m.smartlab_fundamental_values_excluded, 'excluded from site')}
        ${funnelCard('Fundamentals review', m.smartlab_values_needs_review, 'source-check backlog')}
      </div>
      <div class="quality-explainer">
        <b>Как читать воронку:</b> Official IFRS processed facts — это обработанный слой, но не обязательно verified.
        Verified official facts — проверенный seed. Downloaded reports появляются только в controlled opt-in download,
        а Extracted reports — только там, где из файла уже извлечены факты. Official links не считаются verified reports автоматически.
      </div>
      <div class="coverage-grid">
        ${card('Компаний в реестре', m.companies_total, 'registry')}
        ${card('SmartLab baseline', c.smartlab_only || 0, 'агрегатор')}
        ${card('Reliable facts', q.reliable || 0, 'готово к публикации')}
        ${card('Manual review', q.manual_review || 0, 'нужна проверка')}
        ${card('Конфликты источников', m.conflicts_count || 0, 'SmartLab vs IFRS')}
        ${card('Средний quality score', m.average_quality_score == null ? '—' : m.average_quality_score, '0-100')}
      </div>
      <div class="coverage-statuses">
        ${badgeRows}
      </div>
      <div class="coverage-sources">
        <h4>Источники unified layer</h4>
        ${sourceRows || '<div class="muted">Источники пока не опубликованы.</div>'}
      </div>
      <div class="coverage-note muted">SmartLab сейчас используется как baseline. Official IFRS/OCR слой включается только через provenance, confidence score и manual review.</div>`;
  });
}

// ══════════════════════════════════════════════════════════════════════════
// Навигация: 4 раздела (Акции/Облигации/Стратегии/Рынок) НАД существующими блоками.
// Безопасный слой: клик раскрывает <details> и скроллит к секции; логика блоков не тронута.
// ══════════════════════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════════
// Роутер разделов (hash-навигация) + global data status bar + KPI «Текущий рынок».
// Vanilla, без фреймворка. Логика блоков не тронута — на активации секции форс-открываем
// нужные <details>, что запускает уже существующий lazy-render через их toggle-листенеры.
// ══════════════════════════════════════════════════════════════════════════
const SECTIONS = ['news', 'market', 'my-portfolio', 'stocks', 'strategies', 'bonds', 'cbr', 'methodology', 'pro'];

function getSectionFromHash() {
  const h = (location.hash || '').replace('#', '');
  return SECTIONS.includes(h) ? h : 'market';
}

function openDetails(id) {
  const d = document.getElementById(id);
  if (d && d.tagName === 'DETAILS') { d.hidden = false; if (!d.open) d.open = true; }
}

function onSectionShown(sec) {
  if (sec === 'market') { openDetails('marketsaw'); ensureKpiData(); renderMarketPulse(); renderMarketKPI(); renderMarketSignals(); renderEventsToday(); }
  else if (sec === 'my-portfolio') {
    ensureKpiData();
    if (!SITE_FINANCIALS && typeof loadSiteFinancials === 'function') loadSiteFinancials(() => renderMyPortfolio());
    renderMyPortfolio();
  }
  else if (sec === 'strategies') { openDetails('pf'); openDetails('marlamov'); }
  else if (sec === 'news') { renderNews(true); if (MARKET_HISTORY) renderMarketInstruments(); else loadMarketHistory(() => renderMarketInstruments()); }
  else if (sec === 'bonds') { openDetails('bonds'); renderFinder(); }
  else if (sec === 'cbr') {
    openDetails('cbr-timeseries');
    renderCbr();
    renderBanksValuation();
    const ts = document.getElementById('cbr-timeseries');
    if (ts && !ts.dataset.wired) {
      ts.dataset.wired = '1';
      ts.addEventListener('toggle', function () { if (this.open) renderCbr(); });
    }
  }
  else if (sec === 'methodology') {
    openDetails('data-coverage');
    const c = document.getElementById('data-coverage');
    if (c) c.dataset.shown = '1';
    renderDataCoverage();
    openDetails('methodology');
    const d = document.getElementById('methodology');
    if (d) d.dataset.shown = '1';
    renderMethodology();
  }
  // stocks: контролы/таблица рендерятся в init() при загрузке data.json (независимо от секции)
}

function setActiveSection(sec) {
  document.querySelectorAll('main.sections > section.app-section').forEach((s) => {
    s.hidden = (s.dataset.section !== sec);
  });
  document.querySelectorAll('.section-tab').forEach((t) => {
    const active = t.dataset.section === sec;
    t.classList.toggle('active', active);
    t.setAttribute('aria-current', active ? 'page' : 'false');
  });
  window.scrollTo({ top: 0, behavior: 'auto' });
  onSectionShown(sec);
}

function initRouter() {
  document.querySelectorAll('.section-tab').forEach((t) => {
    t.addEventListener('click', () => { location.hash = t.dataset.section; });
  });
  // P5-лендинг: карточки/кнопки с data-goto ведут на соответствующий раздел;
  // кнопки «ранний доступ» показывают честную заглушку (контакт-канал добавим позже)
  document.addEventListener('click', (e) => {
    const g = e.target.closest('[data-goto]');
    if (g && SECTIONS.includes(g.dataset.goto)) { location.hash = g.dataset.goto; return; }
    if (e.target.closest('#pro-waitlist, #pro-waitlist2, #pro-waitlist3')) {
      const n = document.getElementById('pro-waitnote');
      if (n) { n.hidden = false; n.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    }
  });
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.pro-card[data-goto]')) {
      e.preventDefault(); location.hash = e.target.dataset.goto;
    }
  });
  window.addEventListener('hashchange', () => setActiveSection(getSectionFromHash()));
  setActiveSection(getSectionFromHash());
}

// ── global data status bar (даты ТОЛЬКО из реальных JSON, не Date.now()) ──
function loadSiteStatus(cb) {
  if (SITE_STATUS) { if (cb) cb(); return; }
  fetch('site_status.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { SITE_STATUS = j; if (cb) cb(); })
    .catch(() => { SITE_STATUS = { failed: true, blocks: {} }; if (cb) cb(); });   // нет файла → деградируем к датам
}

// P0 Data Health: верхняя панель свежести. Цвет каждого блока по site_status.json
// (fresh/stale/fallback/broken); если файла нет — показываем даты как раньше (устойчиво).
function updateDataStatus() {
  const el = document.getElementById('data-status');
  if (!el) return;
  if (!SITE_STATUS) { loadSiteStatus(() => updateDataStatus()); }
  const d10 = (s) => (s ? String(s).slice(0, 10) : null);
  const st = (SITE_STATUS && SITE_STATUS.blocks) ? SITE_STATUS.blocks : {};
  const cls = { fresh: 'ds-fresh', stale: 'ds-stale', fallback: 'ds-fallback', broken: 'ds-broken' };
  const item = (lbl, v, key) => {
    const b = st[key]; const c = b ? (cls[b.status] || '') : '';
    const tip = b ? `${b.title}: ${b.status}${b.note ? ' — ' + b.note : ''}` : '';
    return `<span class="ds-item ${c}"${tip ? ` title="${esc(tip)}"` : ''}><span class="ds-lbl">${lbl}:</span> <b>${v || '—'}</b></span>`;
  };
  const price = DATA && DATA.meta ? d10(DATA.meta.price_asof) : null;
  const saw = SAW_DATA ? d10(SAW_DATA.data_last) : null;
  const bonds = (BONDS && BONDS.meta) ? d10(BONDS.meta.data_date || BONDS.meta.updated) : null;
  const fin = (DATA_COVERAGE && DATA_COVERAGE.meta) ? d10(DATA_COVERAGE.meta.generated_at) : null;
  const news = (SITE_STATUS && st.news && st.news.asof) ? d10(st.news.asof) : null;
  // overall health chip
  const overall = SITE_STATUS && !SITE_STATUS.failed ? SITE_STATUS.overall : null;
  const oLabel = { fresh: 'данные свежие', stale: 'данные устаревают', fallback: 'резервные данные', broken: 'сбой данных' };
  const chip = overall
    ? `<span class="ds-health ${cls[overall] || ''}" title="Data Health — свежесть по блокам">● ${oLabel[overall] || overall}</span>`
    : '';
  el.innerHTML = chip + item('Цены MOEX', price, 'market') + item('MCFTR', saw, 'marketsaw')
    + item('Новости', news, 'news') + item('Облигации', bonds, 'bonds') + item('Фундамент', fin, 'financials')
    + '<span class="ds-item ds-disc">Не ИИР</span>';
}

// ── KPI «Текущий рынок» ──
// ─────────────── «Сегодня важные события» (ежедневный инвесторский блок) ───────────────
// Фильтр «сегодня» — строго по фактической дате МСК на клиенте (вчерашнее НЕ показывается как
// сегодняшнее, даже если деплой отстал). Сортировка по importance; события по портфелю — выше.
const EV_CAT = {
  cbr_rate_decision: ['ЦБ', 'cbr'], dividend_registry_close: ['Дивиденды', 'div'],
  last_buy_day: ['Дивиденды', 'div'], board_dividend_recommendation: ['Совет директоров', 'board'],
  company_earnings: ['Отчётность', 'earn'], gosa: ['ГОСА', 'gosa'], vosa: ['ВОСА', 'gosa'],
  ofz_auction: ['ОФЗ', 'ofz'], macro_release: ['Макро', 'macro'], general_corporate_event: ['Событие', 'gen'],
};
const EV_SRC_LABEL = { moex_iss: 'MOEX ISS', cbr_schedule: 'график ЦБ РФ', company_disclosure: 'раскрытие эмитента' };

function loadEvents(cb) {
  if (EVENTS_DATA) { if (cb) cb(); return; }
  fetch('events_calendar.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !Array.isArray(j.events)) throw new Error('пустой/битый events_calendar'); EVENTS_DATA = j; if (cb) cb(); })
    .catch((e) => { console.error('[events] не загрузились:', e); EVENTS_DATA = { failed: true, events: [], meta: {} }; if (cb) cb(); });
}

function mskNow() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit',
    day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date());
  const g = {}; parts.forEach((p) => { g[p.type] = p.value; });
  return { iso: `${g.year}-${g.month}-${g.day}`, weekday: g.weekday, hm: `${g.hour}:${g.minute}` };
}
function evDayDiff(evIso, todayIso) {   // разница в днях (МСК-полночь)
  return Math.round((Date.parse(evIso + 'T00:00:00+03:00') - Date.parse(todayIso + 'T00:00:00+03:00')) / 86400000);
}
// контекст портфеля из localStorage: множество тикеров (канонизированных) + веса, если есть цены
function eventsPortfolioContext() {
  let rows = [];
  try { rows = (typeof myPortfolioLoad === 'function' ? myPortfolioLoad() : []) || []; } catch (e) { rows = []; }
  const canon = (t) => (typeof pfxCanonTicker === 'function' ? pfxCanonTicker(t) : String(t || '').toUpperCase());
  const set = new Set(rows.map((r) => canon(r.ticker)).filter(Boolean));
  const weights = {};
  const priceMap = {};
  if (DATA && DATA.tickers) DATA.tickers.forEach((t) => { priceMap[t.ticker] = t.price; });
  let total = 0; const vals = {};
  rows.forEach((r) => { const tk = canon(r.ticker); const p = isNum(priceMap[tk]) ? priceMap[tk] : r.avg_price;
    const v = isNum(p) && isNum(r.quantity) ? p * r.quantity : 0; vals[tk] = (vals[tk] || 0) + v; total += v; });
  if (total > 0) Object.keys(vals).forEach((tk) => { weights[tk] = vals[tk] / total; });
  return { set, weights, hasPortfolio: rows.length > 0 };
}

function evCardHTML(e, pf, todayIso) {
  const [catLbl, catCls] = EV_CAT[e.event_type] || ['Событие', 'gen'];
  const imp = isNum(e.importance) ? e.importance : 0;
  const impCls = imp >= 85 ? 'high' : imp >= 60 ? 'mid' : 'low';
  const impLbl = imp >= 85 ? 'высокая' : imp >= 60 ? 'средняя' : 'низкая';
  const inPf = e.ticker && pf.set.has(e.ticker);
  const wpct = inPf && isNum(pf.weights[e.ticker]) ? ` · вес ${ru(pf.weights[e.ticker] * 100, 0)}%` : '';
  const diff = evDayDiff(e.date, todayIso);
  const dmy = e.date.slice(8, 10) + '.' + e.date.slice(5, 7);
  const whenTxt = diff === 0 ? 'Сегодня' : (diff === 1 ? 'Завтра' : dmy);
  const timeTxt = e.time_msk ? `, ${esc(e.time_msk)} МСК` : ', время н/д';
  const who = e.ticker ? `${esc(e.company)} (${esc(e.ticker)})` : esc(e.company);
  const srcLbl = EV_SRC_LABEL[e.source] || esc(e.source || 'источник н/д');
  const src = e.source_url ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener">${srcLbl}</a>` : srcLbl;
  const stale = e.data_status && !['fresh', 'scheduled'].includes(e.data_status) ? ` <span class="ev-stale">данные ${esc(e.data_status)}</span>` : '';
  return `<div class="ev-card ev-imp-${impCls}${inPf ? ' ev-pf' : ''}">
    <div class="ev-badges">
      <span class="ev-badge ev-cat-${catCls}">${catLbl}</span>
      <span class="ev-badge ev-impbadge ev-imp-${impCls}">важность: ${impLbl}</span>
      ${inPf ? `<span class="ev-badge ev-pfbadge">По вашему портфелю${wpct}</span>` : ''}
    </div>
    <div class="ev-title">${who} — ${esc(e.title)}</div>
    <div class="ev-when">${whenTxt}${timeTxt}</div>
    <div class="ev-desc">${esc(e.description || '')}</div>
    <div class="ev-src muted">Источник: ${src}${stale}</div>
  </div>`;
}

function evSort(a, b, pf) {   // портфельные выше, затем по важности, затем по дате
  const pa = a.ticker && pf.set.has(a.ticker) ? 1 : 0;
  const pb = b.ticker && pf.set.has(b.ticker) ? 1 : 0;
  if (pa !== pb) return pb - pa;
  if ((b.importance || 0) !== (a.importance || 0)) return (b.importance || 0) - (a.importance || 0);
  return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
}

function renderEventsToday() {
  const el = document.getElementById('events-today');
  if (!el) return;
  if (!EVENTS_DATA) { loadEvents(() => renderEventsToday()); el.innerHTML = '<div class="pulse-loading muted">Загрузка событий на сегодня…</div>'; return; }
  const meta = EVENTS_DATA.meta || {};
  const events = Array.isArray(EVENTS_DATA.events) ? EVENTS_DATA.events : [];
  const { iso: todayIso, weekday } = mskNow();
  const pf = eventsPortfolioContext();

  // свежесть: возраст generated_at + статус
  const gen = meta.generated_at || null;
  const ageDays = gen ? (Date.now() - Date.parse(gen)) / 86400000 : null;
  const staleData = EVENTS_DATA.failed || meta.status === 'fallback' || meta.status === 'broken' || (ageDays != null && ageDays > 1.6);
  const updTxt = gen ? `${gen.slice(8, 10)}.${gen.slice(5, 7)}.${gen.slice(0, 4)} ${gen.slice(11, 16)} МСК` : 'н/д';

  const todays = events.filter((e) => e.date === todayIso).sort((a, b) => evSort(a, b, pf));
  const weekend = weekday === 'Mon'
    ? events.filter((e) => { const d = evDayDiff(e.date, todayIso); return d <= -1 && d >= -3; }).sort((a, b) => evSort(a, b, pf))
    : [];
  const upcoming = events.filter((e) => { const d = evDayDiff(e.date, todayIso); return d >= 1 && d <= 14; })
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : evSort(a, b, pf)));

  let html = `<div class="events-head">
    <h2>Сегодня важные события</h2>
    <span class="events-upd ${staleData ? 'events-stale' : ''}">Обновлено: ${updTxt}</span></div>`;

  if (EVENTS_DATA.failed) {
    html += `<div class="events-banner events-banner-risk">Календарь событий сейчас недоступен. Показать нечего — данные не загрузились.</div>`;
    el.innerHTML = html; return;
  }
  if (staleData) {
    html += `<div class="events-banner events-banner-warn">Календарь событий устарел или резервный. Последнее успешное обновление: ${updTxt}. Актуальность сверяйте с источником.</div>`;
  }

  // Сегодня
  if (!todays.length) {
    html += `<div class="events-empty">На сегодня подтверждённых корпоративных событий и дивидендных отсечек по данным MOEX не найдено. Дивидендные даты появляются здесь после официального объявления эмитентом и попадания в реестр MOEX; дивидендные <b>прогнозы</b> по всем бумагам — во вкладке «Акции».</div>`;
  } else {
    const top = todays.slice(0, 5), rest = todays.slice(5);
    html += `<div class="events-list">${top.map((e) => evCardHTML(e, pf, todayIso)).join('')}</div>`;
    if (rest.length) {
      html += `<details class="events-more"><summary>Показать все события сегодня (${todays.length})</summary>
        <div class="events-list">${rest.map((e) => evCardHTML(e, pf, todayIso)).join('')}</div></details>`;
    }
  }
  if (!pf.hasPortfolio) {
    html += `<div class="events-pfhint muted">События по вашим бумагам будут подсвечиваться после добавления портфеля во вкладке «Портфель».</div>`;
  }

  // Понедельник: что накопилось за выходные
  if (weekend.length) {
    html += `<div class="events-sub"><h3>Что накопилось за выходные</h3>
      <div class="events-list">${weekend.slice(0, 6).map((e) => evCardHTML(e, pf, todayIso)).join('')}</div></div>`;
  }

  // Ближайшие события (компактно)
  if (upcoming.length) {
    html += `<div class="events-sub"><h3>Ближайшие события <span class="muted">(до 14 дней)</span></h3>
      <ul class="events-upcoming">${upcoming.slice(0, 10).map((e) => {
        const [catLbl] = EV_CAT[e.event_type] || ['Событие'];
        const inPf = e.ticker && pf.set.has(e.ticker);
        const dmy = e.date.slice(8, 10) + '.' + e.date.slice(5, 7);
        return `<li class="${inPf ? 'ev-up-pf' : ''}"><b>${dmy}</b> <span class="ev-up-cat">${catLbl}</span> ${e.ticker ? esc(e.ticker) + ' · ' : ''}${esc(e.title)}${inPf ? ' <span class="ev-up-pfmark">в портфеле</span>' : ''}</li>`;
      }).join('')}</ul></div>`;
  }

  html += `<div class="events-foot muted">События — из открытых источников (${(meta.sources || []).map((s) => EV_SRC_LABEL[s] || s).join(', ') || 'MOEX ISS, ЦБ'}). Фильтр «сегодня» — по московскому времени. Информационно, не индивидуальная инвестиционная рекомендация.</div>`;
  el.innerHTML = html;
}

function ensureKpiData() {
  if (!EVENTS_DATA && typeof loadEvents === 'function') loadEvents(() => renderEventsToday());
  if (!SAW_DATA && typeof loadMarketSaw === 'function') loadMarketSaw(() => { renderMarketPulse(); renderMarketKPI(); renderMarketSignals(); updateDataStatus(); });
  if (!MARLAMOV && typeof loadMarlamov === 'function') loadMarlamov(() => { renderMarketKPI(); renderMarketSignals(); updateDataStatus(); });
  if (!BONDS && typeof loadBonds === 'function') loadBonds(() => { renderMarketKPI(); updateDataStatus(); });
}

function kpiCard(label, value, cls, note) {
  return `<div class="kpi-card ${cls || ''}"><span class="kpi-lbl">${label}</span><span class="kpi-val">${value}</span>${note ? `<span class="kpi-note">${note}</span>` : ''}</div>`;
}

function renderMarketPulse() {
  const el = document.getElementById('market-pulse');
  if (!el) return;
  if (!SAW_DATA) {
    el.innerHTML = '<div class="pulse-loading muted">Загрузка рыночного пульса...</div>';
    return;
  }
  el.innerHTML = marketPulseHTML(SAW_DATA);
}

function renderMarketSignals() {
  const el = document.getElementById('market-signals');
  if (!el) return;
  el.innerHTML = marketSignalsHTML();
}

function renderMarketKPI() {
  const el = document.getElementById('market-kpi');
  if (!el) return;
  const dash = '<span class="muted">—</span>';
  const ml = MARLAMOV ? MARLAMOV.meta : null;
  const bn = (BONDS && BONDS.meta) ? BONDS.meta : null;
  const rfr = ml && isNum(ml.rfr) ? (ml.rfr * 100).toFixed(1) + '%' : dash;
  const regime = ml && ml.regime ? esc(ml.regime) : dash;
  const nStocks = DATA && DATA.meta ? (DATA.meta.n_ok || DATA.meta.n_total) : null;
  const nBonds = bn && isNum(bn.n) ? bn.n : null;
  const stress = SAW_DATA ? marketStressFromSaw(SAW_DATA) : null;
  const volValue = stress ? fmtPct(stress.current_vol * 100, 1) : dash;
  const volNote = stress ? `${stress.score}/100 · ${esc(stress.label)}` : '';
  el.innerHTML = [
    kpiCard('Волатильность MCFTR 20d', volValue, stress ? `stress-card stress-${stress.tone}` : '', volNote),
    kpiCard('RFR (КБД 1Y)', rfr),
    kpiCard('Режим рынка', regime),
    kpiCard('Акций / облигаций', `${nStocks != null ? nStocks : '—'} / ${nBonds != null ? nBonds : '—'}`),
  ].join('');
}

function loadMarketHistory(cb) {
  if (MARKET_HISTORY) { if (cb) cb(); return; }
  fetch('market_history.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (!j || !Array.isArray(j.instruments) || !j.instruments.length) throw new Error('empty');
      MARKET_HISTORY = j;
      if (cb) cb();
    })
    .catch((e) => { console.error('[market-history]', e); if (cb) cb(e); });
}

function marketInstrument(id) {
  return MARKET_HISTORY && MARKET_HISTORY.instruments
    ? MARKET_HISTORY.instruments.find((row) => row.id === id)
    : null;
}

function marketNumber(value, decimals) {
  if (!isNum(value)) return '—';
  return Number(value).toLocaleString('ru-RU', {
    minimumFractionDigits: decimals >= 4 ? 2 : 0,
    maximumFractionDigits: decimals >= 4 ? 4 : 2,
  });
}

function marketChange(value) {
  if (!isNum(value)) return '—';
  return `${value >= 0 ? '+' : ''}${Number(value).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

function marketInstrumentCardHTML(item) {
  const s = item.summary || {};
  const tone = (s.change_pct || 0) > 0 ? 'up' : (s.change_pct || 0) < 0 ? 'down' : 'flat';
  return `<button type="button" class="market-instrument-card" data-market-id="${esc(item.id)}" aria-label="Открыть график ${esc(item.name)}">
    <span class="mic-name">${esc(item.name)}</span>
    <span class="mic-value">${marketNumber(s.last, item.decimals)}</span>
    <span class="mic-change ${tone}">${marketChange(s.change_pct)}</span>
    <span class="mic-meta">RSI ${isNum(s.rsi14) ? ru(s.rsi14, 1) : '—'} · vol ${isNum(s.volatility20_annualized_pct) ? ru(s.volatility20_annualized_pct, 1) + '%' : '—'}</span>
    <span class="mic-date">${esc(sawDate(item.data_last))}</span>
  </button>`;
}

function renderMarketInstruments() {
  const grid = document.getElementById('market-instrument-grid');
  if (!grid) return;
  if (!MARKET_HISTORY) {
    grid.innerHTML = '<div class="pulse-loading muted">Загрузка истории MOEX...</div>';
    return;
  }
  grid.innerHTML = MARKET_HISTORY.instruments.map(marketInstrumentCardHTML).join('');
  const asof = document.getElementById('market-instruments-asof');
  const latest = MARKET_HISTORY.instruments.map((row) => row.data_last).sort().slice(-1)[0];
  if (asof) asof.textContent = `MOEX ISS · ${sawDate(latest)}`;
  if (!grid.dataset.wired) {
    grid.dataset.wired = '1';
    grid.addEventListener('click', (event) => {
      const button = event.target.closest('[data-market-id]');
      if (button) openMarketChart(button.dataset.marketId);
    });
  }
  wireMarketChartDialog();
}

function marketLevel(label, value, note) {
  return `<div class="market-level"><span>${esc(label)}</span><b>${esc(value)}</b>${note ? `<em>${esc(note)}</em>` : ''}</div>`;
}

function marketLevelsHTML(item) {
  const s = item.summary || {};
  const fmt = (value) => marketNumber(value, item.decimals);
  const rsiNote = !isNum(s.rsi14) ? '' : s.rsi14 >= 70 ? 'выше 70' : s.rsi14 <= 30 ? 'ниже 30' : 'нейтральная зона';
  return [
    marketLevel('Структура средних', s.trend || '—', `SMA20 ${fmt(s.sma20)} · SMA50 ${fmt(s.sma50)} · SMA200 ${fmt(s.sma200)}`),
    marketLevel('RSI (14)', isNum(s.rsi14) ? ru(s.rsi14, 1) : '—', rsiNote),
    marketLevel('Волатильность 20d', isNum(s.volatility20_annualized_pct) ? ru(s.volatility20_annualized_pct, 1) + '%' : '—', 'годовая, close-to-close'),
    marketLevel('Диапазон 20d', `${fmt(s.low20)} — ${fmt(s.high20)}`, 'фактические low / high'),
    marketLevel('Диапазон 60d', `${fmt(s.low60)} — ${fmt(s.high60)}`, 'фактические low / high'),
    marketLevel('Диапазон 1Y', `${fmt(s.low252)} — ${fmt(s.high252)}`, '252 торговые сессии'),
  ].join('');
}

function marketChartRows(item) {
  const count = MARKET_CHART_STATE.period || 252;
  return (item.series || []).slice(-count);
}

function marketOhlcHTML(item, row) {
  if (!row) return '';
  const fmt = (value) => marketNumber(value, item.decimals);
  return `<b>${esc(sawDate(row[0]))}</b><span>O ${fmt(row[1])}</span><span>H ${fmt(row[2])}</span><span>L ${fmt(row[3])}</span><span>C ${fmt(row[4])}</span>`;
}

function drawMarketChart(item) {
  const element = document.getElementById('market-chart-canvas');
  if (!element || !window.LightweightCharts) return;
  if (MARKET_CHART) { MARKET_CHART.remove(); MARKET_CHART = null; }
  element.innerHTML = '';
  const LC = window.LightweightCharts;
  const rows = marketChartRows(item);
  MARKET_CHART = LC.createChart(element, {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#5A6472', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 11 },
    grid: { vertLines: { color: '#EEF1F6' }, horzLines: { color: '#EEF1F6' } },
    rightPriceScale: { borderColor: '#E6E9F0', scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: '#E6E9F0', timeVisible: false, rightOffset: 5 },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });
  const candles = MARKET_CHART.addCandlestickSeries({
    upColor: '#1E6F4C', downColor: '#A2452C', borderVisible: false,
    wickUpColor: '#1E6F4C', wickDownColor: '#A2452C', priceLineVisible: true,
  });
  candles.setData(rows.map((row) => ({ time: row[0], open: row[1], high: row[2], low: row[3], close: row[4] })));
  const enabled = new Set(Array.from(document.querySelectorAll('#market-chart-overlays input:checked')).map((input) => input.value));
  const overlays = [
    ['sma20', 5, '#176B87'], ['sma50', 6, '#C58A14'], ['sma200', 7, '#59616E'],
  ];
  overlays.forEach(([key, index, color]) => {
    if (!enabled.has(key)) return;
    const line = MARKET_CHART.addLineSeries({ color, lineWidth: key === 'sma200' ? 2 : 1, priceLineVisible: false, lastValueVisible: false });
    line.setData(rows.filter((row) => isNum(row[index])).map((row) => ({ time: row[0], value: row[index] })));
  });
  const summary = item.summary || {};
  if (isNum(summary.low20)) candles.createPriceLine({ price: summary.low20, color: '#1E6F4C', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: '20d min' });
  if (isNum(summary.high20)) candles.createPriceLine({ price: summary.high20, color: '#A2452C', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: '20d max' });
  MARKET_CHART.timeScale().fitContent();
  const ohlc = document.getElementById('market-chart-ohlc');
  if (ohlc) ohlc.innerHTML = marketOhlcHTML(item, rows[rows.length - 1]);
  MARKET_CHART.subscribeCrosshairMove((param) => {
    if (!ohlc || !param || !param.time || !param.seriesData) return;
    const bar = param.seriesData.get(candles);
    if (!bar) return;
    const time = typeof param.time === 'string' ? param.time
      : `${param.time.year}-${String(param.time.month).padStart(2, '0')}-${String(param.time.day).padStart(2, '0')}`;
    const row = [time, bar.open, bar.high, bar.low, bar.close];
    ohlc.innerHTML = marketOhlcHTML(item, row);
  });
}

function renderMarketChartDialog() {
  const item = marketInstrument(MARKET_CHART_STATE.id);
  if (!item) return;
  const s = item.summary || {};
  document.getElementById('market-chart-title').textContent = `${item.name} · ${marketNumber(s.last, item.decimals)}`;
  document.getElementById('market-chart-sub').innerHTML = `<span class="${(s.change_pct || 0) >= 0 ? 'up' : 'down'}">${marketChange(s.change_pct)}</span> · ${esc(item.description)} · ${esc(sawDate(item.data_last))}`;
  document.getElementById('market-chart-tabs').innerHTML = MARKET_HISTORY.instruments.map((row) =>
    `<button type="button" data-market-tab="${esc(row.id)}" class="${row.id === item.id ? 'active' : ''}">${esc(row.name)}</button>`
  ).join('');
  document.querySelectorAll('#market-chart-periods [data-period]').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.period) === MARKET_CHART_STATE.period);
  });
  document.getElementById('market-chart-levels').innerHTML = marketLevelsHTML(item);
  const source = document.getElementById('market-chart-source');
  source.href = /^https:\/\/iss\.moex\.com\//i.test(String(item.source_url || ''))
    ? item.source_url : 'https://iss.moex.com/';
  source.textContent = `${item.source} · ${sawDate(item.data_last)}`;
  loadLWC((error) => {
    const canvas = document.getElementById('market-chart-canvas');
    if (error || !window.LightweightCharts) {
      canvas.innerHTML = '<div class="news-fallback">График недоступен; числовые уровни рассчитаны и сохранены.</div>';
      return;
    }
    drawMarketChart(item);
  });
}

function wireMarketChartDialog() {
  const dialog = document.getElementById('market-chart-dialog');
  if (!dialog || dialog.dataset.wired) return;
  dialog.dataset.wired = '1';
  document.getElementById('market-chart-close').addEventListener('click', () => dialog.close());
  document.getElementById('market-chart-tabs').addEventListener('click', (event) => {
    const button = event.target.closest('[data-market-tab]');
    if (!button) return;
    MARKET_CHART_STATE.id = button.dataset.marketTab;
    renderMarketChartDialog();
  });
  document.getElementById('market-chart-periods').addEventListener('click', (event) => {
    const button = event.target.closest('[data-period]');
    if (!button) return;
    MARKET_CHART_STATE.period = Number(button.dataset.period);
    renderMarketChartDialog();
  });
  document.getElementById('market-chart-overlays').addEventListener('change', () => renderMarketChartDialog());
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { if (MARKET_CHART) { MARKET_CHART.remove(); MARKET_CHART = null; } });
}

function openMarketChart(id) {
  const dialog = document.getElementById('market-chart-dialog');
  if (!dialog || !marketInstrument(id)) return;
  MARKET_CHART_STATE.id = id;
  if (!dialog.open) dialog.showModal();
  renderMarketChartDialog();
}

// initRouter() вызывается в самом конце файла (после ВСЕХ модулей и их let-глобалов) — см. низ app.js

// ══════════════════════════════════════════════════════════════════════════
// Банки РФ / данные ЦБ РФ. Всё из site/cbr/*.json (формы 102/123/135, реальные значения ЦБ).
// Bar chart (Chart.js), таблица, Excel (SheetJS), metadata. Не ИИР.
// ══════════════════════════════════════════════════════════════════════════
let CBR_DATA = null;
const XLSX_SRC = 'https://cdn.jsdelivr.net/npm/xlsx@0.18.5/package/dist/xlsx.full.min.js';
const CBR_MONTHS = ['', 'янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const CBR_QUICK_METRICS = [
  { id: 'net_profit', label: 'Прибыль', mode: 'q' },
  { id: 'capital_total', label: 'Капитал' },
  { id: 'n1_0', label: 'Н1.0' },
  { id: 'n2', label: 'Ликвидность' },
];
const cbrMon = (iso) => { const [y, m] = String(iso).split('-'); return CBR_MONTHS[+m] + ' ' + y; };
const cbrBn = (v) => isNum(v) ? (v / 1e6).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' млрд ₽' : ND;
const cbrRub = (v) => {
  if (!isNum(v)) return ND;
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' трлн ₽';
  if (a >= 1e9) return (v / 1e9).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млрд ₽';
  return (v / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн ₽';
};

function loadCbr(cb) {
  if (CBR_DATA) { cb(); return; }
  const t = Date.now();
  const f = (n) => fetch('cbr/' + n + '?t=' + t, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error(n + ' ' + r.status); return r.json(); });
  Promise.all([f('banks.json'), f('bank_timeseries.json'), f('metadata.json'), f('data_quality.json'), f('metric_mapping.json')])
    .then(([banks, ts, meta, dq, mapping]) => { CBR_DATA = { banks, ts, meta, dq, mapping }; cb(); })
    .catch((e) => { console.error('[cbr] не загрузился:', e); cb(e); });
}

function loadXLSX(cb) {
  if (window.XLSX) { cb(); return; }
  if (window.__xlsx) { window.__xlsx.push(cb); return; }
  window.__xlsx = [cb];
  const s = document.createElement('script'); s.src = XLSX_SRC; s.async = true;
  s.onload = () => { const q = window.__xlsx; window.__xlsx = null; q.forEach((f) => f()); };
  s.onerror = () => { const q = window.__xlsx; window.__xlsx = null; q.forEach((f) => f(new Error('XLSX'))); };
  document.head.appendChild(s);
}

function renderCbr() {
  const body = document.getElementById('cbr-body');
  if (!body || body.dataset.shown === '1' && CBR_DATA) { cbrDraw(); return; }
  body.innerHTML = '<div class="cbr-loading muted">Загрузка данных ЦБ РФ…</div>';
  loadCbr((err) => {
    if (err || !CBR_DATA) { body.innerHTML = '<div class="cbr-fallback"><b>Данные ЦБ временно недоступны.</b> Раздел не обновлён. <div class="cbr-disc">Не ИИР.</div></div>'; return; }
    loadBanksValuation(() => {
      body.innerHTML = cbrUIHTML(CBR_DATA);
      body.dataset.shown = '1';
      ['cbr-bank', 'cbr-filter', 'cbr-metric', 'cbr-mode', 'cbr-from', 'cbr-to'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => {
          if (id === 'cbr-filter') cbrRefreshBanks();
          if (id === 'cbr-bank' || id === 'cbr-metric' || id === 'cbr-filter') cbrRefreshDates();
          cbrDraw();
        });
      });
      const xb = document.getElementById('cbr-xlsx'); if (xb) xb.addEventListener('click', cbrExcel);
      cbrRefreshBanks();
      cbrRefreshDates();                                  // даты — из реальных точек выбранного ряда
      cbrBindBankDeck();
      cbrDraw();                                          // таблица не должна зависеть от CDN Chart.js
      loadChartJS(() => cbrDraw());
    });
  });
}

function cbrBankRank(bank) {
  const listed = ((BVAL && BVAL.banks) || []).find((b) => String(b.regnum) === String(bank.reg_num));
  if (listed && isNum(listed.mcap_rub)) return [0, -listed.mcap_rub, bank.name || ''];
  return [bank.is_systemically_important ? 1 : 2, 0, bank.name || ''];
}

function cbrBankOptions(sibOnly) {
  return CBR_DATA.banks.filter((b) => b.is_active && (!sibOnly || b.is_systemically_important))
    .sort((a, b) => {
      const ra = cbrBankRank(a), rb = cbrBankRank(b);
      return ra[0] - rb[0] || ra[1] - rb[1] || String(ra[2]).localeCompare(String(rb[2]), 'ru');
    })
    .map((b) => `<option value="${b.reg_num}">${esc(b.name)}${b.is_systemically_important ? ' ★' : ''}</option>`).join('');
}

function cbrRefreshBanks() {
  const sel = document.getElementById('cbr-bank');
  const sibOnly = document.getElementById('cbr-filter').value === 'sib';
  const cur = sel.value;
  sel.innerHTML = cbrBankOptions(sibOnly);
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
}

function cbrValuationByReg(reg) {
  return ((BVAL && BVAL.banks) || []).find((b) => String(b.regnum) === String(reg));
}

function cbrPublicBanks() {
  const active = new Set((CBR_DATA.banks || []).filter((b) => b.is_active).map((b) => String(b.reg_num)));
  return ((BVAL && BVAL.banks) || []).filter((b) => active.has(String(b.regnum)))
    .sort((a, b) => (b.mcap_rub || 0) - (a.mcap_rub || 0));
}

function cbrLatestMetric(reg, metric, mode) {
  const rows = (((CBR_DATA.ts || {})[reg] || {})[metric]) || [];
  const p = rows[rows.length - 1];
  if (!p) return null;
  const v = mode === 'q' && isNum(p.value_q) ? p.value_q : p.value;
  return { ...p, v };
}

function cbrMetricMeta(metric) {
  return (CBR_DATA.mapping.metrics || []).find((x) => x.metric_id === metric) || {};
}

function cbrMetricValue(metric, point) {
  if (!point || !isNum(point.v)) return ND;
  const mm = cbrMetricMeta(metric);
  if (mm.unit === '%') return point.v.toFixed(1) + '%';
  return cbrBn(point.v);
}

function cbrBankDeckHTML() {
  const banks = cbrPublicBanks();
  if (!banks.length) return '';
  return banks.map((b, i) => {
    const pbv = isNum(b.p_bv) ? b.p_bv.toFixed(2) : ND;
    const roe = isNum(b.roe) ? b.roe.toFixed(1) + '%' : ND;
    const tone = isNum(b.p_bv) && b.p_bv < 1 ? 'cheap' : 'rich';
    return `<button class="cbr-bank-card ${i === 0 ? 'on' : ''}" type="button" data-reg="${esc(b.regnum)}" style="--bank-color:${esc(b.color || '#5B6B83')}">
      <span class="cbr-bank-card-top"><b>${esc(b.ticker)}</b><span>${cbrRub(b.mcap_rub)}</span></span>
      <span class="cbr-bank-card-name">${esc(b.name)}</span>
      <span class="cbr-bank-card-metrics"><span class="${tone}">P/BV ${pbv}</span><span>ROE ${roe}</span></span>
    </button>`;
  }).join('');
}

function cbrBindBankDeck() {
  const deck = document.getElementById('cbr-bank-deck');
  if (!deck) return;
  deck.innerHTML = cbrBankDeckHTML();
  deck.querySelectorAll('.cbr-bank-card').forEach((btn) => btn.addEventListener('click', () => cbrSelectBank(btn.dataset.reg)));
}

function cbrSelectBank(reg) {
  const sel = document.getElementById('cbr-bank');
  const filter = document.getElementById('cbr-filter');
  if (!sel || !reg) return;
  if (![...sel.options].some((o) => o.value === String(reg)) && filter) {
    filter.value = 'all';
    cbrRefreshBanks();
  }
  if ([...sel.options].some((o) => o.value === String(reg))) sel.value = String(reg);
  cbrRefreshDates();
  cbrDraw();
}

function cbrSelectMetric(metric, mode) {
  const sel = document.getElementById('cbr-metric');
  const ms = document.getElementById('cbr-mode');
  if (!sel) return;
  if ([...sel.options].some((o) => o.value === metric)) sel.value = metric;
  if (ms && mode) ms.value = mode;
  cbrRefreshDates();
  cbrDraw();
}

function cbrSyncBankDashboard(series) {
  const sel = document.getElementById('cbr-bank');
  if (!sel) return;
  const reg = sel.value;
  const cards = document.querySelectorAll('.cbr-bank-card');
  cards.forEach((c) => c.classList.toggle('on', c.dataset.reg === String(reg)));
  const sum = document.getElementById('cbr-bank-summary');
  const quick = document.getElementById('cbr-quick-metrics');
  const bank = (CBR_DATA.banks || []).find((b) => String(b.reg_num) === String(reg)) || {};
  const val = cbrValuationByReg(reg);
  if (sum) sum.innerHTML = cbrBankSummaryHTML(bank, val, series);
  if (quick) {
    const currentMetric = (document.getElementById('cbr-metric') || {}).value;
    const currentMode = (document.getElementById('cbr-mode') || {}).value;
    quick.innerHTML = CBR_QUICK_METRICS.map((q) => {
      const p = cbrLatestMetric(reg, q.id, q.mode);
      const active = currentMetric === q.id && (!q.mode || currentMode === q.mode);
      return `<button class="cbr-qchip${active ? ' on' : ''}" type="button" data-metric="${esc(q.id)}" data-mode="${esc(q.mode || '')}">
        <span>${esc(q.label)}</span><b>${esc(cbrMetricValue(q.id, p))}</b></button>`;
    }).join('');
    quick.querySelectorAll('.cbr-qchip').forEach((btn) => btn.addEventListener('click', () => cbrSelectMetric(btn.dataset.metric, btn.dataset.mode)));
  }
}

function cbrBankSummaryHTML(bank, val, series) {
  const name = val ? val.name : (bank.name || 'Банк');
  const ticker = val ? val.ticker : '';
  const last = series && series.rows && series.rows.length ? series.rows[series.rows.length - 1] : null;
  const metric = series ? series.metricName + (series.modeLabel || '') : 'Выбранная метрика';
  const n10Tone = val && isNum(val.n10_headroom) && val.n10_headroom < 0 ? 'bad' : 'ok';
  const pbvTone = val && isNum(val.p_bv) && val.p_bv < 1 ? 'cheap' : 'rich';
  const warns = val && (val.warnings || []).length ? `<div class="cbr-bank-warnings">${val.warnings.slice(0, 2).map((w) => `<span>⚠ ${esc(w)}</span>`).join('')}</div>` : '';
  return `<div class="cbr-bank-summary-main" style="--bank-color:${esc((val && val.color) || '#5B6B83')}">
    <div class="cbr-bank-title">
      <span class="cbr-bank-dot"></span>
      <div><b>${esc(name)}</b><span>${ticker ? esc(ticker) + ' · ' : ''}${bank.is_systemically_important ? 'системно значимый' : 'активный банк'}</span></div>
    </div>
    <div class="cbr-bank-kpis">
      <div><span>Капитализация</span><b>${val ? cbrRub(val.mcap_rub) : ND}</b></div>
      <div><span>P/BV</span><b class="${pbvTone}">${val && isNum(val.p_bv) ? val.p_bv.toFixed(2) : ND}</b></div>
      <div><span>ROE</span><b>${val && isNum(val.roe) ? val.roe.toFixed(1) + '%' : ND}</b></div>
      <div><span>Н1.0 запас</span><b class="${n10Tone}">${val && isNum(val.n10_headroom) ? (val.n10_headroom >= 0 ? '+' : '') + val.n10_headroom.toFixed(1) + ' п.п.' : ND}</b></div>
      <div><span>Дивдоходность</span><b>${val && isNum(val.div_yield) ? val.div_yield.toFixed(1) + '%' : ND}</b></div>
      <div><span>${esc(metric)}</span><b>${last ? cbrMetricValue((document.getElementById('cbr-metric') || {}).value, last) : ND}</b></div>
    </div>
  </div>${warns}`;
}

function cbrUIHTML(d) {
  const m = d.meta;
  // метрики сгруппированы по формам (optgroup)
  const groups = {};
  d.mapping.metrics.forEach((x) => { (groups[x.group || 'Метрики'] = groups[x.group || 'Метрики'] || []).push(x); });
  const metricOpts = Object.entries(groups).map(([g, ms]) =>
    `<optgroup label="${esc(g)}">${ms.map((x) => `<option value="${x.metric_id}">${esc(x.display_name_ru)}</option>`).join('')}</optgroup>`).join('');
  const upd = (m.generated_at || '').replace('T', ' ').slice(0, 16);
  const lrd = m.last_report_dates || {};
  return `
    <div class="cbr-bank-deck" id="cbr-bank-deck"></div>
    <div class="cbr-bank-summary" id="cbr-bank-summary"></div>
    <div class="cbr-quick-metrics" id="cbr-quick-metrics"></div>
    <details class="cbr-advanced">
      <summary>Расширенные настройки</summary>
      <div class="cbr-controls">
        <label>Банк<select id="cbr-bank"></select></label>
        <label>Фильтр<select id="cbr-filter"><option value="sib">Системно значимые ★</option><option value="all">Все активные</option></select></label>
        <label>Метрика<select id="cbr-metric">${metricOpts}</select></label>
        <label id="cbr-mode-wrap">Режим<select id="cbr-mode">
          <option value="q" selected>За период (расчёт)</option>
          <option value="cum">Накопленным итогом</option>
        </select></label>
        <label>С<select id="cbr-from"></select></label>
        <label>По<select id="cbr-to"></select></label>
        <button class="btn" id="cbr-xlsx">Скачать Excel</button>
      </div>
    </details>
    <div class="cbr-meta">
      <span class="cbr-chip"><span class="k">Источник:</span> <b>ЦБ РФ · формы 102/123/135</b></span>
      <span class="cbr-chip"><span class="k">Отчётные даты:</span> <b>Ф.102 ${esc(lrd['102'] || '—')} · Ф.123 ${esc(lrd['123'] || '—')} · Ф.135 ${esc(lrd['135'] || '—')}</b></span>
      <span class="cbr-chip"><span class="k">Проверка (Actions):</span> <b>${esc(upd)}</b></span>
      <span class="cbr-chip"><span class="k">Банков:</span> <b>${d.dq.banks_active}</b> · знач. <b>${d.dq.values_loaded}</b></span>
    </div>
    <div class="cbr-chart-wrap"><canvas id="cbr-chart"></canvas></div>
    <div id="cbr-table-wrap"></div>
    <div class="cbr-disc">Факт ЦБ РФ. «За период» — расчетная разность накопленных значений Ф.102. Не индивидуальная инвестиционная рекомендация.</div>
  `;
}

function cbrRefreshDates() {
  // варианты «С/По» — из реальных дат точек выбранного ряда; дефолт — весь диапазон
  const reg = document.getElementById('cbr-bank').value;
  const metric = document.getElementById('cbr-metric').value;
  const mm = CBR_DATA.mapping.metrics.find((x) => x.metric_id === metric) || {};
  const all = ((CBR_DATA.ts[reg] || {})[metric]) || [];
  const dates = all.map((p) => p.date);
  const opts = dates.map((dt) => `<option value="${dt}">${esc(cbrMon(dt))}</option>`).join('');
  const from = document.getElementById('cbr-from'), to = document.getElementById('cbr-to');
  const oldF = from.value, oldT = to.value;
  from.innerHTML = opts; to.innerHTML = opts;
  from.value = dates.includes(oldF) ? oldF : (dates[0] || '');
  to.value = dates.includes(oldT) ? oldT : (dates[dates.length - 1] || '');
  // режим «за квартал» — только для накопленных метрик (Ф.102)
  const mw = document.getElementById('cbr-mode-wrap');
  if (mw) mw.style.display = mm.cumulative ? '' : 'none';
}

function cbrSeries() {
  const reg = document.getElementById('cbr-bank').value;
  const metric = document.getElementById('cbr-metric').value;
  const from = document.getElementById('cbr-from').value;
  const to = document.getElementById('cbr-to').value;
  const all = ((CBR_DATA.ts[reg] || {})[metric]) || [];
  const lo = from && to && from <= to ? from : (all[0] && all[0].date);
  const hi = from && to && from <= to ? to : (all.length && all[all.length - 1].date);
  const rows = all.filter((p) => (!lo || p.date >= lo) && (!hi || p.date <= hi));
  const mm = CBR_DATA.mapping.metrics.find((x) => x.metric_id === metric) || {};
  const bank = CBR_DATA.banks.find((b) => String(b.reg_num) === String(reg)) || {};
  const modeSel = document.getElementById('cbr-mode');
  const mode = (mm.cumulative && modeSel && modeSel.value === 'q') ? 'q' : 'cum';
  const unit = mm.unit || 'тыс. руб.';
  // отображаемое значение: «за квартал» = расчётная разность накопленных (value_q)
  const disp = rows.map((p) => ({ ...p,
    v: mode === 'q' ? (isNum(p.value_q) ? p.value_q : p.value) : p.value,
    status: mode === 'q' ? (p.value_q_method || 'calculated_from_official') : p.quality_status }));
  return { rows: disp, metricName: mm.display_name_ru || metric, bank, symbol: mm.symbol, unit, mode,
           modeLabel: mode === 'q' ? ' · за период' : (mm.cumulative ? ' · накопл. итогом' : '') };
}

function cbrDraw() {
  const s = cbrSeries();
  const { rows, metricName, bank, unit } = s;
  const tw = document.getElementById('cbr-table-wrap');
  cbrSyncBankDashboard(s);
  if (!rows.length) {
    if (tw) tw.innerHTML = '<div class="cbr-nodata muted">Нет данных по выбранному банку/метрике/периоду.</div>';
    if (window.__cbrChart) { try { window.__cbrChart.destroy(); } catch (e) { /* noop */ } window.__cbrChart = null; }
    return;
  }
  const isPct = unit === '%';
  const fmt = (v) => isPct ? (isNum(v) ? v.toFixed(2) + '%' : ND) : cbrBn(v);
  cbrChartDraw(s);
  tw.innerHTML = `<div class="cbr-table-scroll"><table class="cbr-table">
    <thead><tr><th>Дата</th><th>Банк</th><th>Метрика</th><th>Значение</th><th>Ед.</th><th>Форма ЦБ</th><th>Символ</th><th>Статус</th></tr></thead>
    <tbody>${rows.slice().reverse().map((p) => `<tr>
      <td>${esc(cbrMon(p.date))}</td><td class="cbr-bname">${esc(bank.name || '')}</td><td>${esc(metricName)}${esc(s.modeLabel)}</td>
      <td class="tnum ${p.v >= 0 ? 'cbr-up' : 'cbr-down'}">${fmt(p.v)}</td>
      <td>${isPct ? '%' : 'тыс.₽ (сыро: ' + ru(p.v, 0) + ')'}</td><td>Ф.${esc(p.form)}</td><td>${esc(p.symbol)}</td>
      <td><span class="cbr-status s-${esc(p.status)}">${esc(p.status)}</span></td>
    </tr>`).join('')}</tbody></table></div>`;
}

// инлайн-плагин: цифровые значения над столбиками (без внешнего chartjs-plugin-datalabels)
const cbrBarLabels = {
  id: 'cbrBarLabels',
  afterDatasetsDraw(chart, _args, opts) {
    const { ctx } = chart;
    const meta = chart.getDatasetMeta(0);
    const data = chart.data.datasets[0].data;
    ctx.save();
    ctx.font = '600 10px system-ui, -apple-system, sans-serif';
    ctx.fillStyle = '#5A6472';
    ctx.textAlign = 'center';
    meta.data.forEach((bar, i) => {
      const v = data[i];
      if (v == null || !bar || bar.width < 14) return;   // тесные бары (мобайл) — без подписи
      const txt = opts.isPct ? v.toFixed(2) : ru(v, Math.abs(v) >= 100 ? 0 : 1);
      const above = v >= 0;
      ctx.textBaseline = above ? 'bottom' : 'top';
      ctx.fillText(txt, bar.x, above ? bar.y - 4 : bar.y + 4);
    });
    ctx.restore();
  },
};

function cbrChartDraw(s) {
  const ctx = document.getElementById('cbr-chart');
  if (!ctx || !window.Chart) return;
  if (window.__cbrChart) { try { window.__cbrChart.destroy(); } catch (e) { /* noop */ } }
  const isPct = s.unit === '%';
  const labels = s.rows.map((p) => cbrMon(p.date));
  const vals = s.rows.map((p) => isPct ? p.v : p.v / 1e6);
  const colors = vals.map((v) => v >= 0 ? '#1E6F4C' : '#A2452C');
  const yTitle = isPct ? '%' : 'млрд ₽';
  const form = s.rows[0] ? s.rows[0].form : '';
  window.__cbrChart = new window.Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label: s.metricName + s.modeLabel + ', ' + yTitle, data: vals, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      layout: { padding: { top: 16 } },                 // место под подписи над столбиками
      scales: {
        x: { grid: { display: false }, ticks: { color: '#5A6472' } },
        y: { grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' }, title: { display: true, text: yTitle } },
      },
      plugins: {
        legend: { labels: { color: '#5A6472' } },
        tooltip: { callbacks: { label: (i) => `${s.metricName}${s.modeLabel}: ${i.parsed.y.toFixed(isPct ? 2 : 1)} ${yTitle} (Ф.${form}, ЦБ РФ)` } },
        cbrBarLabels: { isPct },
      },
    },
    plugins: [cbrBarLabels],
  });
}

function cbrExcel() {
  loadXLSX((err) => {
    if (err || !window.XLSX) { alert('Не удалось загрузить библиотеку Excel.'); return; }
    const { rows, metricName, bank, unit } = cbrSeries();
    const X = window.XLSX, wb = X.utils.book_new();
    X.utils.book_append_sheet(wb, X.utils.json_to_sheet(rows.map((p) => ({ Дата: p.date, Банк: bank.name, Метрика: metricName, ['Значение_' + (unit === '%' ? 'проц' : 'тыс_руб')]: p.value, ...(isNum(p.value_q) ? { 'За_период_расчет': p.value_q } : {}), Форма: p.form, Символ: p.symbol, Статус: p.quality_status, Источник: p.source }))), 'timeseries');
    X.utils.book_append_sheet(wb, X.utils.json_to_sheet(CBR_DATA.banks), 'banks');
    X.utils.book_append_sheet(wb, X.utils.json_to_sheet(CBR_DATA.mapping.metrics), 'metrics');
    X.utils.book_append_sheet(wb, X.utils.json_to_sheet([CBR_DATA.meta]), 'metadata');
    X.utils.book_append_sheet(wb, X.utils.json_to_sheet(CBR_DATA.dq.banks), 'data_quality');
    const from = document.getElementById('cbr-from').value, to = document.getElementById('cbr-to').value;
    X.writeFile(wb, `cbr_banks_${bank.reg_num}_${document.getElementById('cbr-metric').value}_${from}_${to}.xlsx`);
  });
}

// ══════════════════════════════════════════════════════════════════════════
// MOEX Bond Finder — месячный шорт-лист кандидатов (НЕ сигналы). Всё из
// site/bonds/finder.json (bonds/bond_finder.py). Источники рейтингов видны в каждой строке.
// ══════════════════════════════════════════════════════════════════════════
let FINDER = null;
let FINDER_SORT = { key: 'score', dir: -1 };

function loadFinder(cb) {
  if (FINDER) { cb(); return; }
  fetch('bonds/finder.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.profiles) throw new Error('пустой finder.json'); FINDER = j; cb(); })
    .catch((e) => { console.error('[finder]', e); cb(e); });
}

function renderFinder() {
  const body = document.getElementById('finder-body');
  if (!body) return;
  if (body.dataset.shown === '1' && FINDER) { finderDraw(); return; }
  body.innerHTML = '<div class="finder-loading muted">Загрузка шорт-листа Bond Finder…</div>';
  loadFinder((err) => {
    if (err || !FINDER) {
      body.innerHTML = '<div class="finder-fallback"><b>Bond Finder недоступен</b> — файл ещё не сгенерирован или источник упал. Скринер ниже работает независимо.</div>';
      return;
    }
    body.innerHTML = finderShellHTML(FINDER);
    body.dataset.shown = '1';
    const ps = document.getElementById('fnd-profile');
    const bd = document.getElementById('fnd-budget');
    if (ps) ps.addEventListener('change', finderDraw);
    if (bd) bd.addEventListener('input', debounce(finderDraw, 250));
    finderDraw();
  });
}

function finderShellHTML(d) {
  const m = d.meta;
  const warns = (m.warnings || []).map((w) => `<li>${esc(w)}</li>`).join('');
  const profOpts = Object.entries(d.profiles).map(([id, p]) =>
    `<option value="${id}"${id === 'balanced' ? ' selected' : ''}>${esc(p.title)}</option>`).join('');
  const c = m.counts || {};
  const rc = m.rating_coverage || {};
  const checked = shortIsoDate(((m.ratings || {}).checked_at || '').slice(0, 10));
  const agencies = ratingSources(m);
  return `
    ${warns ? `<details class="fnd-warns"><summary>Предупреждения качества данных <span class="muted">(${(m.warnings || []).length})</span></summary><ul>${warns}</ul></details>` : ''}
    <div class="fnd-controls">
      <label>Профиль<select id="fnd-profile">${profOpts}</select></label>
      <label>Бюджет, ₽<input type="number" id="fnd-budget" value="1000000" min="0" step="100000"></label>
      <span class="fnd-chip"><span class="k">Срез</span> <b>${esc(m.snapshot_date || '—')}</b></span>
      <span class="fnd-chip"><span class="k">Вселенная</span> <b>${c.universe_clean ?? '—'}</b> · FX <b>${c.fx ?? 0}</b> · ПИР <b>${c.pir_board ?? 0}</b> · call <b>${c.call_only ?? 0}</b></span>
      <span class="fnd-chip"><span class="k">Рейтинги выпусков</span> <b>${rc.official_issue_ratings ?? 0}/${rc.candidates ?? 0}</b> · ${esc(agencies || 'источник недоступен')}${checked ? ' · ' + esc(checked) : ''}</span>
    </div>
    <div class="fnd-summary" id="fnd-summary"></div>
    <div id="fnd-table"></div>
    <div id="fnd-extra"></div>
    <details class="fnd-method"><summary>Методика и ограничения</summary><div>${esc(m.methodology || '')}</div></details>
    <div class="fnd-disc">${esc(m.disclaimer || '')}</div>`;
}

function finderRows() {
  const pid = (document.getElementById('fnd-profile') || {}).value || 'balanced';
  const prof = FINDER.profiles[pid] || { picks: [], aggregates: {} };
  const budget = Math.max(0, +(document.getElementById('fnd-budget') || {}).value || 0);
  const rows = prof.picks.map((p) => {
    const alloc = p.weight * budget;
    const pieces = p.dirty_price > 0 ? Math.floor(alloc / p.dirty_price) : 0;
    return { ...p, pieces, cost: pieces * p.dirty_price };
  });
  return { pid, prof, budget, rows };
}

function finderDraw() {
  const { prof, budget, rows } = finderRows();
  const sumEl = document.getElementById('fnd-summary');
  const tblEl = document.getElementById('fnd-table');
  if (!sumEl || !tblEl) return;
  const a = prof.aggregates || {};
  const spent = rows.reduce((s, r) => s + r.cost, 0);
  sumEl.innerHTML = [
    ['Net YTM', a.ytm_net_wavg != null ? a.ytm_net_wavg.toFixed(2) + '%' : '—'],
    ['Дюрация', a.duration_wavg != null ? a.duration_wavg.toFixed(2) + ' г' : '—'],
    ['Эмитентов', a.issuers ?? '—'],
    ['Бюджет', rub0(spent) + ' из ' + rub0(budget)],
  ].map(([k, v]) => `<div class="fnd-kpi"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')
  + (a.note ? `<div class="fnd-note">⚠️ ${esc(a.note)}</div>` : '');

  const key = FINDER_SORT.key, dir = FINDER_SORT.dir;
  rows.sort((x, y) => {
    const a = x[key] ?? -1e18, b = y[key] ?? -1e18;
    return (a > b ? 1 : a < b ? -1 : 0) * dir;
  });
  const cols = [['secid', 'SECID'], ['name', 'Бумага'], ['rating_rank', 'Рейтинг'], ['dirty_price', 'Цена+НКД'],
    ['ytm_net', 'Net YTM'], ['g_spread', 'G-спред'], ['spread_pctl', 'Пцл'],
    ['duration_years', 'Дюр.'], ['score', 'Скор'], ['pieces', 'Шт.'], ['cost', 'Сумма']];
  const th = cols.map(([k, t]) =>
    `<th data-key="${k}" class="${key === k ? 'fnd-sorted' : ''}">${t}${key === k ? (dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('');
  const trs = rows.map((r) => `<tr>
    <td class="fnd-links"><a href="https://www.moex.com/ru/issue.aspx?code=${esc(r.secid)}" target="_blank" rel="noopener">${esc(r.secid)}</a>
      <a class="muted" href="https://smart-lab.ru/q/bonds/${esc(r.secid)}/" target="_blank" rel="noopener">sl</a></td>
    <td class="fnd-name"><b>${esc(r.name || '')}</b><span>${esc(String(r.issuer || '').slice(0, 36))}</span>${r.new_placement ? ' <i class="fnd-new">новый</i>' : ''}${r.qual_only ? ' <i class="fnd-qual">квал</i>' : ''}</td>
    <td class="fnd-rating">${officialRatingHTML(r, 'fnd-rt')}</td>
    <td class="tnum">${isNum(r.dirty_price) ? ru(r.dirty_price, 0) : ND}</td>
    <td class="tnum">${isNum(r.ytm_net) ? r.ytm_net.toFixed(2) + '%' : ND}</td>
    <td class="tnum">${isNum(r.g_spread) ? (r.g_spread >= 0 ? '+' : '') + r.g_spread.toFixed(2) + 'пп' : ND}</td>
    <td class="tnum">${r.spread_pctl != null ? r.spread_pctl.toFixed(0) + '%' : '<span class="muted" title="история короче 60 сессий">—</span>'}</td>
    <td class="tnum">${isNum(r.duration_years) ? r.duration_years.toFixed(2) : ND}</td>
    <td class="tnum fnd-score">${isNum(r.score) ? r.score.toFixed(2) : ND}</td>
    <td class="tnum"><b>${r.pieces}</b></td>
    <td class="tnum">${rub0(r.cost)}</td>
  </tr>`).join('');
  tblEl.innerHTML = rows.length
    ? `<div class="fnd-table-scroll"><table class="fnd-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`
    : '<div class="fnd-empty muted">Ничего не найдено — ослабьте фильтры (профиль «Агрессивный»).</div>';
  tblEl.querySelectorAll('th[data-key]').forEach((el) => el.addEventListener('click', () => {
    const k = el.dataset.key;
    FINDER_SORT = { key: k, dir: FINDER_SORT.key === k ? -FINDER_SORT.dir : -1 };
    finderDraw();
  }));
  finderExtra();
}

function finderExtra() {
  const el = document.getElementById('fnd-extra');
  if (!el || el.dataset.done) return;
  el.dataset.done = '1';
  const d = FINDER;
  const sec = [];
  if ((d.new_placements || []).length) {
    sec.push(`<details class="fnd-sec"><summary>Новые выпуски <span class="muted">(${d.new_placements.length} · нет истории торгов — ликвидность не проверена)</span></summary>
      <div class="fnd-table-scroll"><table class="fnd-table"><thead><tr><th>SECID</th><th>Бумага</th><th>YTM</th><th>После налога</th><th>G-спред</th><th>Дюр., г</th></tr></thead><tbody>
      ${d.new_placements.map((r) => `<tr><td><a href="https://www.moex.com/ru/issue.aspx?code=${esc(r.secid)}" target="_blank" rel="noopener">${esc(r.secid)}</a></td>
        <td class="fnd-name">${esc(r.name || '')}</td><td class="tnum">${isNum(r.ytm) ? r.ytm.toFixed(2) + '%' : ND}</td>
        <td class="tnum">${isNum(r.ytm_net) ? r.ytm_net.toFixed(2) + '%' : ND}</td>
        <td class="tnum">${isNum(r.g_spread) ? r.g_spread.toFixed(2) + 'пп' : ND}</td>
        <td class="tnum">${isNum(r.duration_years) ? r.duration_years.toFixed(2) : ND}</td></tr>`).join('')}
      </tbody></table></div></details>`);
  }
  if ((d.with_offer || []).length) {
    sec.push(`<details class="fnd-sec"><summary>С офертой <span class="muted">(${d.with_offer.length} · доходность к оферте)</span></summary>
      <div class="fnd-table-scroll"><table class="fnd-table"><thead><tr><th>SECID</th><th>Бумага</th><th>Оферта</th><th>Дох. к оферте</th><th>YTM к погаш.</th><th>Дюр., г</th></tr></thead><tbody>
      ${d.with_offer.map((r) => `<tr><td><a href="https://www.moex.com/ru/issue.aspx?code=${esc(r.secid)}" target="_blank" rel="noopener">${esc(r.secid)}</a></td>
        <td class="fnd-name">${esc(r.name || '')}</td><td class="tnum">${esc(r.offer_date || '—')}</td>
        <td class="tnum">${isNum(r.ytm_to_offer) ? (+r.ytm_to_offer).toFixed(2) + '%' : '—'}</td>
        <td class="tnum">${isNum(r.ytm) ? r.ytm.toFixed(2) + '%' : ND}</td>
        <td class="tnum">${isNum(r.duration_years) ? r.duration_years.toFixed(2) : ND}</td></tr>`).join('')}
      </tbody></table></div></details>`);
  }
  if ((d.events || []).length) {
    sec.push(`<details class="fnd-sec"><summary>События <span class="muted">(новые размещения и спред-события с прошлого прогона)</span></summary>
      <ul class="fnd-events">${d.events.map((e) => `<li><span class="muted">${esc(e.date)}</span> ${esc(e.text)}</li>`).join('')}</ul></details>`);
  }
  if ((d.journal_review || []).length) {
    sec.push(`<details class="fnd-sec"><summary>Проверка себя <span class="muted">(журнал прошлых шорт-листов против RUCBTRNS)</span></summary>
      <div class="fnd-table-scroll"><table class="fnd-table"><thead><tr><th>Дата списка</th><th>Дней</th><th>Бумаг</th><th>Портфель</th><th>RUCBTRNS</th></tr></thead><tbody>
      ${d.journal_review.map((r) => `<tr><td>${esc(r.entry_date)}</td><td class="tnum">${r.held_days}</td><td class="tnum">${r.n_reviewed}</td>
        <td class="tnum ${r.portfolio_return_pct >= 0 ? 'cbr-up' : 'cbr-down'}">${r.portfolio_return_pct >= 0 ? '+' : ''}${r.portfolio_return_pct}%</td>
        <td class="tnum">${r.rucbtrns_return_pct != null ? (r.rucbtrns_return_pct >= 0 ? '+' : '') + r.rucbtrns_return_pct + '%' : '—'}</td></tr>`).join('')}
      </tbody></table></div>
      <div class="muted" style="font-size:.76rem;padding:6px 2px">${esc((d.journal_review[0] || {}).method || '')}</div></details>`);
  }
  el.innerHTML = sec.join('');
}

// ══════════════════════════════════════════════════════════════════════════
// Банки РФ — секторная оценка (P/BV, ROE, пэйаут, Н1.0) + ROE×P/BV scatter с
// справедливой линией P/BV=ROE/COE. Всё из site/cbr/valuation.json. Не ИИР.
// ══════════════════════════════════════════════════════════════════════════
let BVAL = null;
let BVAL_COE = 20;                 // percent, slider-driven
let BVAL_SORT = { key: 'mcap_rub', dir: -1 };
let BVAL_SEL = null;               // единый выбор банка: таблица ↔ карта сектора ↔ история P/BV
let BHIST = null;                  // site/cbr/history.json (price-vs-capital trajectory)
let BHIST_SEL = null;             // currently selected ticker in the history chart

// Аналитические зоны достаточности капитала по Н1.0 (НЕ регуляторное заключение — см. методологию)
const BVAL_CAPITAL_CONFIG = { h10Watch: 11.0, h10Comfort: 13.0, h10Strong: 15.0 };

// Отклонение рыночного P/BV от ROE-линии (P/BV = ROE/COE). Это НЕ fair value и НЕ целевая
// цена — грубая модельная линия связи прибыльности и оценки при заданной стоимости капитала.
function bvalRoeLineDiscount(b) {
  const roe = Number(b && b.roe), pbv = Number(b && b.p_bv), coe = Number(BVAL_COE);
  if (!(roe > 0) || !(pbv > 0) || !(coe > 0)) return null;
  return pbv * coe / roe - 1;      // roe и coe оба в %, единицы сокращаются
}
function bvalCapitalZone(b) {
  const h10 = Number(b && b.n10);
  if (!(h10 > 0)) return { zone: 'na', label: 'н/д' };
  const c = BVAL_CAPITAL_CONFIG;
  if (h10 >= c.h10Strong) return { zone: 'strong', label: 'сильный' };
  if (h10 >= c.h10Comfort) return { zone: 'comfort', label: 'комфортный' };
  if (h10 >= c.h10Watch) return { zone: 'watch', label: 'watch' };
  return { zone: 'risk', label: 'проверить' };
}
function bvalMedian(key) {
  const v = (BVAL && BVAL.banks || []).map((b) => b[key]).filter(isNum).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}
// тренд Н1.0 из помесячной истории Ф.135 (CBR_DATA, тот же таб) — критика «одна точка Н1.0 мало говорит»
function bvalN1Trend(b) {
  if (!CBR_DATA || !CBR_DATA.ts || !b || !b.regnum) return null;
  const rows = ((CBR_DATA.ts[String(b.regnum)] || {}).n1_0 || []).filter((p) => isNum(p.value));
  if (rows.length < 2) return null;
  const last = rows[rows.length - 1];
  const prev = rows[Math.max(0, rows.length - 4)];              // ~квартал назад (помесячные точки)
  const min = rows.reduce((a, p) => (p.value < a.value ? p : a));
  const d = last.value - prev.value;
  return { last, prev, min, delta: d, n: rows.length };
}

function loadBanksValuation(cb) {
  if (BVAL) { cb(); return; }
  fetch('cbr/valuation.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.banks) throw new Error('empty'); BVAL = j; cb(); })
    .catch((e) => { console.error('[bval]', e); cb(e); });
}

function loadBanksHistory(cb) {
  if (BHIST) { cb(); return; }
  fetch('cbr/history.json?t=' + Date.now(), { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.banks) throw new Error('empty'); BHIST = j; cb(); })
    .catch((e) => { console.error('[bhist]', e); cb(e); });
}

// ── банковский cockpit: связки капитал → прибыль → дивиденды → оценка ────────

// человеческий вывод по банку: ROE vs COE × отклонение от ROE-линии × капитал × дивспособность
function bvalBankNarrative(b) {
  const parts = [];
  const disc = bvalRoeLineDiscount(b);
  const spread = isNum(b.roe) ? b.roe - BVAL_COE : null;
  if (spread != null && disc != null) {
    if (spread > 0 && disc < 0) parts.push(`ROE выше заданной стоимости капитала (COE ${BVAL_COE}%), а P/BV ниже ROE-линии: рынок оценивает капитал банка осторожнее, чем следует из текущей прибыльности. Требуется проверить устойчивость ROE и качество данных.`);
    else if (spread > 0) parts.push(`ROE выше стоимости капитала, но банк торгуется выше ROE-линии — такая оценка требует устойчивой прибыли и сохранения капитального буфера.`);
    else parts.push(`ROE ниже заданной стоимости капитала (COE ${BVAL_COE}%): банк пока не показывает создание стоимости сверх требуемой доходности — оценка требует осторожной интерпретации.`);
  } else if (spread != null) {
    parts.push(spread > 0 ? `ROE выше заданной стоимости капитала (COE ${BVAL_COE}%).` : `ROE ниже заданной стоимости капитала (COE ${BVAL_COE}%).`);
  }
  const z = bvalCapitalZone(b);
  if (z.zone === 'strong') parts.push('Капитальный буфер выглядит сильным по аналитической шкале.');
  else if (z.zone === 'watch' || z.zone === 'risk') parts.push('Капитальный буфер тонкий по аналитической шкале — способность платить дивиденды чувствительна к прибыли.');
  else if (z.zone === 'na') parts.push('Данных по нормативам капитала недостаточно для вывода.');
  const cap = b.dividend_capacity_score;
  if (isNum(cap)) {
    if (cap >= 75) parts.push('Дивидендная способность высокая по текущим данным, но зависит от прибыли, капитала и политики payout.');
    else if (cap < 40) parts.push('Дивидендная способность ограничена: требуется проверить капитал, payout и устойчивость прибыли.');
  }
  if (isNum(b.data_quality_score) && b.data_quality_score < 70) parts.push('Качество данных неполное — выводы требуют сверки с первоисточником.');
  parts.push('Не является индивидуальной инвестиционной рекомендацией.');
  return parts.join(' ');
}

// Capital-to-Dividend Bridge: прибыль TTM → потенциальные дивиденды → удержанная прибыль → капитал/Н1
function bvalBridgeHTML(b) {
  if (!isNum(b.profit_ttm_rub) || !isNum(b.payout) || !isNum(b.capital_rub) || b.capital_rub <= 0) {
    return '<div class="muted">Мост «капитал → дивиденды» недоступен: нет прибыли TTM, payout или капитала в данных.</div>';
  }
  const bn = (x) => ru(x / 1e9, 0) + ' млрд ₽';
  const div = b.profit_ttm_rub * Math.min(Math.max(b.payout, 0), 200) / 100;
  const retained = b.profit_ttm_rub - div;
  const divShare = div / b.capital_rub * 100;
  const rows = [
    ['Прибыль TTM (Ф.102)', bn(b.profit_ttm_rub)],
    [`Потенциальные дивиденды (пэйаут ${Math.round(b.payout)}%)`, bn(div)],
    ['Удержанная прибыль → в капитал', bn(retained)],
    ['Капитал (Ф.123)', bn(b.capital_rub)],
    ['Дивиденды к капиталу', ru(divShare, 1) + '%'],
    ['Н1.0 / запас к минимуму', isNum(b.n10) ? `${ru(b.n10, 1)}%${isNum(b.n10_headroom) ? ` / ${b.n10_headroom >= 0 ? '+' : ''}${ru(b.n10_headroom, 1)} п.п.` : ''}` : 'н/д'],
  ].map(([k, v]) => `<div class="bval-br-row"><span>${k}</span><b>${v}</b></div>`).join('');
  return `${rows}<div class="muted bval-br-note">Банк может выглядеть дивидендным, только если прибыль и капитал одновременно поддерживают выплату. Точный эффект выплаты на Н1.0 нельзя оценить без RWA и структуры регуляторного капитала — здесь только грубая связка.</div>`;
}

// сравнение банка с медианой сектора (строка не показывается, если поля нет)
function bvalPeersHTML(b) {
  const defs = [
    ['roe', 'ROE', '%'], ['p_bv', 'P/BV', ''], ['n10', 'Н1.0', '%'], ['payout', 'Пэйаут', '%'],
    ['dividend_capacity_score', 'Дивспособность', '/100'], ['data_quality_score', 'Качество данных', '/100'],
  ];
  const rows = defs.map(([k, lbl, u]) => {
    const v = b[k], med = bvalMedian(k);
    if (!isNum(v) || !isNum(med)) return '';
    const d = u === '' ? 2 : (u === '%' ? 1 : 0);
    return `<div class="bval-peer-row"><span>${lbl}</span><b>${ru(v, d)}${u === '/100' ? '' : u}</b><em class="muted">медиана ${ru(med, d)}${u === '/100' ? '' : u}</em></div>`;
  }).filter(Boolean).join('');
  return rows || '<div class="muted">Недостаточно данных для сравнения с сектором.</div>';
}

const BVAL_FORECAST_LABELS = {
  profit_rub: 'Прибыль', net_income: 'Прибыль', net_income_ttm: 'Прибыль TTM', roe: 'ROE',
  payout: 'Пэйаут', dps: 'Дивиденд/акция', dividend_per_share: 'Дивиденд/акция', div_yield: 'Дивдоходность',
  year: 'Период', source: 'Источник', date: 'Дата', comment: 'Комментарий', note: 'Комментарий',
};
function bvalForecastHTML(fc) {
  if (!fc || typeof fc !== 'object') return '';
  return Object.entries(fc).map(([k, v]) => {
    const lbl = BVAL_FORECAST_LABELS[k] || k;
    const val = isNum(v) ? (Math.abs(v) >= 1e9 ? ru(v / 1e9, 0) + ' млрд ₽' : ru(v, 1)) : esc(String(v));
    return `${esc(lbl)}: ${val}`;
  }).join(' · ');
}

// единый выбор банка: подсветка строки + точка на карте + история P/BV (без scrollIntoView)
function bvalSelect(tk) {
  if (!tk) return;
  BVAL_SEL = tk;
  document.querySelectorAll('#bval-table-wrap tr.bval-row').forEach((tr) => {
    tr.classList.toggle('bval-selected', tr.dataset.tk === tk);
  });
  bvalScatterSelect();
  if (BHIST && (BHIST.banks || []).some((x) => x.ticker === tk && (x.points || []).length)) {
    BHIST_SEL = tk;
    document.querySelectorAll('#bval-hist-chips .bh-chip').forEach((c) => c.classList.toggle('on', c.dataset.tk === tk));
    bvalHistDraw();
  }
}
// выделение точки на карте сектора без пересоздания графика
function bvalScatterSelect() {
  const ch = window.__bvalChart;
  if (!ch || !ch.$pts) return;
  const ds = ch.data.datasets[0];
  ds.pointRadius = ch.$pts.map((b) => bvalPointR(b) + (b.ticker === BVAL_SEL ? 3 : 0));
  ds.pointBorderWidth = ch.$pts.map((b) => (b.ticker === BVAL_SEL ? 3 : 1));
  ds.pointBorderColor = ch.$pts.map((b) => (b.ticker === BVAL_SEL ? '#263140' : 'rgba(255,255,255,.85)'));
  ch.update('none');
}
function bvalPointR(b) {   // размер точки — по капитализации (sqrt-шкала 6..11)
  const caps = (BVAL.banks || []).map((x) => x.mcap_rub).filter((x) => isNum(x) && x > 0);
  if (!isNum(b.mcap_rub) || b.mcap_rub <= 0 || !caps.length) return 7;
  return 6 + 5 * Math.sqrt(b.mcap_rub / Math.max(...caps));
}
function bvalCapColor(b) { // цвет точки — по дивидендной способности
  const s = b.dividend_capacity_score;
  if (!isNum(s)) return '#8A93A3';
  return s >= 75 ? '#1E6F4C' : s >= 55 ? '#4C5C86' : s >= 40 ? '#8A6224' : '#A2452C';
}

// «Что видно по банковскому сектору» — 3–6 тезисов из уже загруженного BVAL
function bvalSectorTakeaways() {
  const el = document.getElementById('bval-takeaways');
  if (!el || !BVAL) return;
  const banks = BVAL.banks || [];
  const withPbv = banks.filter((b) => isNum(b.p_bv));
  if (withPbv.length < 2) { el.innerHTML = '<div class="muted">Недостаточно данных для секторного вывода.</div>'; return; }
  const bank = (b) => `<button class="bval-tw-bank" data-tk="${esc(b.ticker)}">${esc(b.name)}</button>`;
  const t = [];
  const cheap = withPbv.filter((b) => b.p_bv < 1);
  t.push(`<b>${cheap.length} из ${withPbv.length}</b> банков торгуются ниже капитала (P/BV &lt; 1).`);
  const above = banks.filter((b) => isNum(b.roe) && b.roe > BVAL_COE);
  t.push(`<b>${above.length}</b> ${above.length === 1 ? 'банк имеет' : 'банков имеют'} ROE выше заданной стоимости капитала (COE ${BVAL_COE}%).`);
  const discs = banks.map((b) => ({ b, d: bvalRoeLineDiscount(b) })).filter((x) => x.d != null);
  if (discs.length) {
    const lo = discs.reduce((a, x) => (x.d < a.d ? x : a));
    t.push(`Дальше всех ниже ROE-линии — ${bank(lo.b)} (${Math.round(lo.d * 100)}% к линии ROE/COE; это не «недооценка», а повод проверить устойчивость ROE).`);
  }
  const caps = banks.filter((b) => isNum(b.dividend_capacity_score));
  if (caps.length) {
    const hi = caps.filter((b) => b.dividend_capacity_score >= 75);
    const best = caps.reduce((a, b) => (b.dividend_capacity_score > a.dividend_capacity_score ? b : a));
    t.push(`Дивидендная способность выше 75/100 — у ${hi.length ? hi.length + ' (лучшая: ' + bank(best) + ', ' + best.dividend_capacity_score + '/100)' : '0 банков; максимум у ' + bank(best) + ' (' + best.dividend_capacity_score + '/100)'}.`);
  }
  const watch = banks.filter((b) => ['watch', 'risk'].includes(bvalCapitalZone(b).zone));
  if (watch.length) t.push(`У ${watch.length} ${watch.length === 1 ? 'банка' : 'банков'} капитал в аналитической зоне «watch/проверить»: ${watch.map(bank).join(', ')}.`);
  const badData = banks.filter((b) => (isNum(b.data_quality_score) && b.data_quality_score < 70) || (b.warnings || []).length >= 3);
  if (badData.length) t.push(`Данные по ${badData.length} ${badData.length === 1 ? 'банку' : 'банкам'} неполные или требуют проверки.`);
  el.innerHTML = `<div class="bval-tw-head">Что видно по банковскому сектору</div>
    <ol class="bval-tw-list">${t.slice(0, 6).map((x) => `<li>${x}</li>`).join('')}</ol>
    <div class="muted bval-tw-note">Тезисы — модельные, при COE ${BVAL_COE}% (ползунок на карте сектора). Не ИИР.</div>`;
}

function renderBanksValuation() {
  const body = document.getElementById('bval-body');
  if (!body) return;
  if (body.dataset.shown === '1' && BVAL) return;
  body.innerHTML = '<div class="bval-loading muted">Загрузка оценки банковского сектора…</div>';
  loadBanksValuation((err) => {
    if (err || !BVAL) { body.innerHTML = '<div class="bval-fallback">Оценка сектора недоступна — файл ещё не сгенерирован.</div>'; return; }
    BVAL_COE = Math.round((BVAL.meta.coe_default || 0.20) * 100);
    body.innerHTML = bvalShellHTML(BVAL);
    body.dataset.shown = '1';
    bvalTable();
    bvalSectorTakeaways();
    bvalCapacityDraw();
    // клики по именам банков в секторных тезисах → единый выбор (делегированно, вешается один раз)
    const tw = document.getElementById('bval-takeaways');
    if (tw) tw.addEventListener('click', (e) => {
      const btn = e.target.closest('.bval-tw-bank');
      if (btn) bvalSelect(btn.dataset.tk);
    });
    const sl = document.getElementById('bval-coe');
    if (sl) sl.addEventListener('input', () => {
      BVAL_COE = +sl.value;
      document.getElementById('bval-coe-val').textContent = BVAL_COE;
      bvalCoeUpdate();
    });
    loadChartJS(() => { bvalScatterDraw(); renderBvalHistory(); });
  });
}

// смена COE: точечные обновления БЕЗ полного re-render таблицы — открытые detail-rows,
// сортировка и выбранный банк сохраняются (порядок сортировок COE-инвариантен: p_bv/roe и roe монотонны)
function bvalCoeUpdate() {
  const find = (tk) => (BVAL.banks || []).find((b) => b.ticker === tk);
  document.querySelectorAll('#bval-table-wrap td.bval-fair-cell').forEach((td) => {
    const b = find(td.dataset.tk);
    const d = b ? bvalRoeLineDiscount(b) : null;
    td.innerHTML = bvalFairFmt(d);
  });
  document.querySelectorAll('#bval-table-wrap td.bval-spread-cell').forEach((td) => {
    const b = find(td.dataset.tk);
    const s = b && isNum(b.roe) ? b.roe - BVAL_COE : null;
    td.innerHTML = bvalSpreadFmt(s);
  });
  // человеческий вывод в ОТКРЫТЫХ detail-rows зависит от COE — обновить только их
  document.querySelectorAll('#bval-table-wrap tr.bval-detail:not([hidden]) .bval-nar-txt').forEach((div) => {
    const b = find(div.dataset.tk);
    if (b) div.textContent = bvalBankNarrative(b);
  });
  bvalSectorTakeaways();
  bvalScatterDraw();       // пересоздание карты selection-aware (см. bvalScatterDraw)
  bvalCapacityDraw();      // ROE−COE в cap-таблице; открытых состояний в ней нет
}
// форматтеры ячеек, зависящих от COE (используются и при рендере, и при обновлении слайдером)
function bvalFairFmt(d) {
  if (d == null) return '<span class="muted">—</span>';
  const cls = d < 0 ? 'bval-fair-neg' : 'bval-fair-pos';
  return `<span class="${cls}">${d >= 0 ? '+' : '−'}${Math.abs(Math.round(d * 100))}%</span>`;
}
function bvalSpreadFmt(s) {
  if (s == null) return '<span class="muted">—</span>';
  return `<span class="${s >= 0 ? 'saw-up' : 'saw-down'}">${s >= 0 ? '+' : ''}${ru(s, 1)}</span>`;
}

function bvalShellHTML(d) {
  const m = d.meta;
  const dt = (s) => (s ? String(s).slice(0, 10) : '—');
  const tips = m.tooltips || {};
  const howto = [
    ['P/BV', tips.p_bv], ['ROE', tips.roe], ['P/E', tips.p_e], ['Пэйаут', tips.payout],
    ['Дивдоходность', tips.div_yield], ['Н1.0', tips.n10], ['Прибыль', tips.profit],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v || '')}</dd>`).join('');
  const [lo, hi] = m.coe_range || [0.12, 0.30];
  return `
    ${bvalHealthStrip(m)}
    <div class="bval-takeaway" id="bval-takeaways"></div>
    <div class="bval-guide">
      <div><b>ROE − COE</b><span>создаёт ли банк доходность выше требуемой</span></div>
      <div><b>ROE-линия</b><span>грубая связь прибыльности и P/BV, не fair value</span></div>
      <div><b>Н1.0</b><span>достаточность капитала (зоны — аналитические)</span></div>
      <div><b>Дивспособность</b><span>выдержит ли капитал выплату — диагностика</span></div>
    </div>
    <div id="bval-table-wrap"></div>
    <details class="bval-howto"><summary>Как читать оценку банков</summary><dl class="bval-dl">${howto}</dl>
      <div class="bval-method-txt">
        P/BV показывает, сколько рынок платит за 1 рубль капитала банка. ROE — доходность капитала.
        ROE − COE показывает, создаёт ли банк доходность выше пользовательского требования (COE — ползунок).
        ROE-линия P/BV = ROE / COE — грубая модельная связь прибыльности и оценки; отклонение от неё
        <b>не является fair value или целевой ценой</b>. Н1.0 — достаточность капитала; зоны
        «сильный/комфортный/watch/проверить» (${BVAL_CAPITAL_CONFIG.h10Strong}/${BVAL_CAPITAL_CONFIG.h10Comfort}/${BVAL_CAPITAL_CONFIG.h10Watch}%) — аналитические допущения, не регуляторное заключение.
        Пэйаут — доля прибыли, направляемая акционерам. Дивидендная способность — модельная диагностика
        (ROE, буфер капитала, payout, качество данных), не рекомендация. Источники: формы ЦБ 102/123/135 + MOEX ISS.
        Пользователь самостоятельно принимает инвестиционные решения.
      </div>
      <div class="muted" style="font-size:.78rem;margin-top:6px">${esc(m.reg_min_note || '')}</div></details>

    <div class="bval-hist-box" id="bval-hist">
      <div class="bval-hist-head"><b>История P/BV: цена акции против капитала на акцию</b><span class="muted"> — когда линия цены ныряет под линию капитала, банк торгуется дешевле одного капитала (P/BV &lt; 1)</span></div>
      <div class="bval-hist-chips" id="bval-hist-chips"></div>
      <div class="bval-hist-stat" id="bval-hist-stat"></div>
      <div class="bval-hist-wrap"><canvas id="bval-hist-canvas"></canvas></div>
      <div class="bval-hist-cap muted" id="bval-hist-cap"></div>
    </div>

    <div class="bval-scatter-box">
      <div class="bval-scatter-head">
        <b>Карта сектора: прибыльность (ROE) × цена капитала (P/BV)</b>
        <label class="bval-coe">COE <input type="range" id="bval-coe" min="${Math.round(lo * 100)}" max="${Math.round(hi * 100)}" step="1" value="${BVAL_COE}"><span><b id="bval-coe-val">${BVAL_COE}</b>%</span></label>
      </div>
      <div class="bval-scatter-wrap"><canvas id="bval-scatter"></canvas></div>
      <div class="bval-caption muted">Линия P/BV = ROE / COE (сейчас <b id="bval-cap-coe">${BVAL_COE}</b>%) — <b>не fair value</b>, а ориентир связи прибыльности и оценки: точки <b>ниже</b> линии рынок оценивает дешевле, чем следует из ROE, <b>выше</b> — дороже. Размер точки — капитализация, цвет — дивидендная способность. Клик по точке выбирает банк.</div>
    </div>

    <div class="bval-cap-box">
      <div class="bval-cap-head"><b>Запас капитала: хватит ли на дивиденды</b><span class="muted"> — может ли банк устойчиво платить, не пробивая достаточность капитала (Н1.0/Н1.1/Н1.2)</span></div>
      <div id="bval-cap-body"></div>
      <div class="bval-caption muted">Дивидендная способность 0–100: запас Н1.0 (буфер) + ROE (генерация капитала) − текущий пэйаут + запас базового капитала Н1.1. Экономическая прибыль = ROE − COE (при текущем COE ползунка): выше 0 — банк создаёт стоимость на капитал. Не ИИР.</div>
    </div>
    <div class="bval-disc">${esc(m.disclaimer || '')}</div>`;
}

// per-source data health strip (MOEX / Ф.102 / Ф.123 / Ф.135) для вкладки «Банки РФ»
function bvalHealthStrip(m) {
  const src = m.sources || {};
  const labels = { moex: 'MOEX', cbr_102: 'Ф.102', cbr_123: 'Ф.123', cbr_135: 'Ф.135' };
  const stCls = { fresh: 'ds-fresh', stale: 'ds-stale', missing: 'ds-broken' };
  const items = Object.keys(labels).map((k) => {
    const s = src[k] || {}; const c = stCls[s.status] || '';
    return `<span class="ds-item ${c}" title="${esc(s.status || 'н/д')}${s.date ? ' · ' + s.date : ''}"><span class="ds-lbl">${labels[k]}:</span> <b>${s.date ? String(s.date).slice(0, 10) : '—'}</b></span>`;
  }).join('');
  const statuses = Object.values(src).map((s) => s && s.status);
  const anyBad = statuses.some((s) => s && s !== 'fresh');
  const upd = m.generated_at ? new Date(Date.parse(m.generated_at)).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }) : '—';
  const nb = m.banks_valued || m.banks_count || (BVAL && (BVAL.banks || []).length) || 0;
  const scope = nb ? `<span class="ds-item bval-hs-scope" title="Только публичные банки с котировками MOEX — НЕ весь банковский сектор РФ"><span class="ds-lbl">Периметр:</span> <b>${nb} публичных банков</b> (не весь сектор)</span>` : '';
  return `<div class="bval-health"><span class="bval-hs-lbl">Данные банков:</span>${items}${scope}<span class="ds-item bval-hs-upd">обновлено ${esc(upd)}</span></div>
    ${anyBad ? '<div class="bval-hs-warn">Часть данных устарела/недоступна — выводы по капиталу и дивидендам требуют проверки.</div>' : ''}`;
}

// McKinsey-lens: капитал (Н1.0/1.1/1.2) + буфер + экономическая прибыль (ROE−COE) + дивидендная способность
function bvalCapacityDraw() {
  const el = document.getElementById('bval-cap-body');
  if (!el || !BVAL) return;
  const coe = BVAL_COE;
  const banks = (BVAL.banks || []).filter((b) => isNum(b.dividend_capacity_score) || isNum(b.n10));
  const rows = banks.slice().sort((a, b) => (b.dividend_capacity_score ?? -1) - (a.dividend_capacity_score ?? -1)).map((b) => {
    const cap = isNum(b.dividend_capacity_score) ? b.dividend_capacity_score : null;
    const spread = isNum(b.roe) ? b.roe - coe : null;
    const verdict = cap == null ? '—' : cap >= 75 ? 'сильная' : cap >= 55 ? 'умеренная' : cap >= 40 ? 'ограниченная' : 'под давлением';
    const vt = cap == null ? 'neut' : cap >= 75 ? 'good' : cap >= 55 ? 'neut' : cap >= 40 ? 'warn' : 'risk';
    const buf = b.capital_buffer;
    return `<tr class="bval-caprow" data-tk="${esc(b.ticker)}">
      <td class="left"><span class="bval-dot" style="background:${esc(b.color || '#888')}"></span><b>${esc(b.name)}</b> <span class="bval-tk">${esc(b.ticker)}</span></td>
      <td class="tnum">${isNum(b.n10) ? ru(b.n10, 1) + '%' : mdash}</td>
      <td class="tnum col-sec">${isNum(b.n11) ? ru(b.n11, 1) + '%' : mdash}</td>
      <td class="tnum col-sec">${isNum(b.n12) ? ru(b.n12, 1) + '%' : mdash}</td>
      <td class="tnum ${isNum(buf) ? (buf >= 0 ? 'saw-up' : 'saw-down') : ''}">${isNum(buf) ? (buf >= 0 ? '+' : '') + ru(buf, 1) : mdash}</td>
      <td class="tnum">${isNum(b.roe) ? ru(b.roe, 1) + '%' : mdash}</td>
      <td class="tnum col-sec">${isNum(b.payout) ? Math.round(b.payout) + '%' : mdash}</td>
      <td class="tnum ${spread != null ? (spread >= 0 ? 'saw-up' : 'saw-down') : ''}">${spread != null ? (spread >= 0 ? '+' : '') + ru(spread, 1) : mdash}</td>
      <td><div class="bval-cap-bar"><i class="cap-${vt}" style="width:${cap ?? 0}%"></i></div></td>
      <td class="left"><span class="pfx-tag ${vt}">${verdict}${cap != null ? ' · ' + cap : ''}</span></td>
    </tr>`;
  }).join('');
  const withCap = banks.filter((b) => isNum(b.dividend_capacity_score));
  let take = '';
  if (withCap.length) {
    const best = withCap.reduce((a, b) => (b.dividend_capacity_score > a.dividend_capacity_score ? b : a));
    const worst = withCap.reduce((a, b) => (b.dividend_capacity_score < a.dividend_capacity_score ? b : a));
    take = `Наибольшая дивидендная способность — ${best.name} (${best.dividend_capacity_score}/100, буфер Н1.0 ${best.capital_buffer >= 0 ? '+' : ''}${ru(best.capital_buffer, 1)} п.п.); наименьшая — ${worst.name} (${worst.dividend_capacity_score}/100${isNum(worst.div_yield) && worst.div_yield > 10 ? `, при дивдоходности ${ru(worst.div_yield, 1)}% дивиденд ограничен тонким капиталом` : ''}).`;
  }
  el.innerHTML = `<div class="bval-cap-scroll"><table class="bval-table bval-cap-tbl"><thead><tr>
    <th class="left">Банк</th><th title="норматив достаточности собственных средств">Н1.0</th><th class="col-sec" title="базовый капитал">Н1.1</th><th class="col-sec" title="основной капитал">Н1.2</th>
    <th title="запас Н1.0 над регуляторным минимумом, п.п.">Буфер</th><th>ROE</th><th class="col-sec">Пэйаут</th><th title="экономическая прибыль: ROE − COE (ползунок)">ROE−COE</th>
    <th title="0–100: устойчивость дивиденда к достаточности капитала">Див. способность</th><th class="left">Вердикт</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    ${take ? `<div class="bval-cap-take"><b>Вывод:</b> ${esc(take)}</div>` : ''}`;
  el.querySelectorAll('tr.bval-caprow').forEach((tr) => tr.addEventListener('click', () => bvalSelect(tr.dataset.tk)));
}

// COE-инвариантный accessor для сортировки: fair_disc ~ p_bv/roe (монотонно), roe_spread ~ roe
function bvalSortVal(b, key) {
  if (key === 'fair_disc') return (isNum(b.p_bv) && isNum(b.roe) && b.roe > 0) ? b.p_bv / b.roe : null;
  if (key === 'roe_spread') return isNum(b.roe) ? b.roe : null;
  return b[key];
}
function bvalRows() {
  const rows = (BVAL.banks || []).slice();
  const { key, dir } = BVAL_SORT;
  rows.sort((a, b) => { const x = bvalSortVal(a, key) ?? -1e18, y = bvalSortVal(b, key) ?? -1e18; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
  return rows;
}

function bvalTable() {
  const wrap = document.getElementById('bval-table-wrap');
  if (!wrap) return;
  const num = (v, s = '') => (isNum(v) ? v.toFixed(v >= 100 ? 0 : (s === '%' ? 1 : 2)) + s : '<span class="muted">—</span>');
  const T = BVAL.meta.tooltips || {};
  // [key, title, align, secondary?, tooltip]
  const cols = [
    ['name', 'Банк', 'l', 0, ''],
    ['p_bv', 'P/BV', 'r', 0, T.p_bv],
    ['fair_disc', 'К ROE-линии', 'r', 0, 'P/BV ÷ (ROE/COE) − 1 при текущем COE ползунка. Это НЕ fair value и не целевая цена, а грубая модельная линия связи ROE и P/BV. Минус — рынок ценит банк дешевле, чем оправдывает его прибыльность'],
    ['roe', 'ROE', 'r', 0, T.roe],
    ['roe_spread', 'ROE−COE', 'r', 0, 'ROE минус заданная стоимость капитала (COE, ползунок). Выше 0 — банк создаёт доходность сверх требуемой'],
    ['price', 'Цена', 'r', 1, ''],
    ['p_e', 'P/E', 'r', 1, T.p_e],
    ['payout', 'Пэйаут', 'r', 1, T.payout],
    ['div_yield', 'Дивдоходность', 'r', 0, T.div_yield],
    ['n10', 'Н1.0', 'r', 0, T.n10],
    ['dividend_capacity_score', 'Дивспособн.', 'r', 0, '0–100: модельная способность выдержать дивиденды по капиталу и прибыли. Не рекомендация'],
    ['data_quality_score', 'Кач-во', 'r', 1, 'Оценка полноты и свежести данных банка, 0–100'],
  ];
  const th = cols.map(([k, t, al, sec, tp]) => `<th data-key="${k}" class="al-${al}${sec ? ' col-sec' : ''} ${BVAL_SORT.key === k ? 'bval-sorted' : ''}"${tp ? ` title="${esc(tp)}"` : ''}>${t}${tp ? ' ⓘ' : ''}${BVAL_SORT.key === k ? (BVAL_SORT.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('');
  const rows = bvalRows().map((b, i) => {
    const warn = (b.warnings || []).length;
    const tk = esc(b.ticker);
    const pctl = (k) => num(b[k], '%');
    const disc = bvalRoeLineDiscount(b);
    const spread = isNum(b.roe) ? b.roe - BVAL_COE : null;
    const cap = b.dividend_capacity_score;
    const capVt = !isNum(cap) ? 'neut' : cap >= 75 ? 'good' : cap >= 55 ? 'neut' : cap >= 40 ? 'warn' : 'risk';
    const dq = b.data_quality_score;
    const sel = b.ticker === BVAL_SEL ? ' bval-selected' : '';
    const main = `<tr class="bval-row${sel}" data-i="${i}" data-tk="${tk}">
      <td class="al-l bval-bname"><span class="bval-dot" style="background:${esc(b.color || '#888')}"></span>${esc(b.name)}<span class="bval-tk">${tk}</span>${warn ? ` <span class="bval-warn-badge" title="${esc((b.warnings || []).join(' · '))}">⚠${warn}</span>` : ''}</td>
      <td class="al-r tnum bval-strong">${num(b.p_bv)}</td>
      <td class="al-r tnum bval-fair-cell" data-tk="${tk}">${bvalFairFmt(disc)}</td>
      <td class="al-r tnum">${pctl('roe')}</td>
      <td class="al-r tnum bval-spread-cell" data-tk="${tk}">${bvalSpreadFmt(spread)}</td>
      <td class="al-r tnum col-sec">${num(b.price)}</td>
      <td class="al-r tnum col-sec">${num(b.p_e)}</td>
      <td class="al-r tnum col-sec">${pctl('payout')}</td>
      <td class="al-r tnum">${pctl('div_yield')}</td>
      <td class="al-r tnum">${num(b.n10, '%')}${isNum(b.n10_headroom) ? ` <span class="bval-head ${b.n10_headroom >= 0 ? 'ok' : 'bad'}">${b.n10_headroom >= 0 ? '+' : ''}${b.n10_headroom.toFixed(1)}</span>` : ''}</td>
      <td class="al-r tnum">${isNum(cap) ? `<span class="bval-capmini"><i class="cap-${capVt}" style="width:${cap}%"></i></span> ${cap}` : '<span class="muted">—</span>'}</td>
      <td class="al-r tnum col-sec">${isNum(dq) ? dq : '<span class="muted">—</span>'}</td>
    </tr>`;
    const vin = b.vintages || {};
    const dh = (b.div_history || []).map((x) => `${esc(x.date)}: ${x.value}₽`).join(' · ');
    const fcHTML = bvalForecastHTML(b.forecast);
    const trend = bvalN1Trend(b);
    const trendHTML = trend
      ? `Н1.0 ${ru(trend.last.value, 1)}% (${trend.delta >= 0 ? '+' : ''}${ru(trend.delta, 2)} п.п. за ~квартал; минимум окна ${ru(trend.min.value, 1)}% на ${esc(trend.min.date.slice(0, 7))})`
      : 'История нормативов недоступна';
    const detail = `<tr class="bval-detail" data-i="${i}" data-tk="${tk}" hidden><td colspan="${cols.length}">
      <div class="bval-detail-cols">
        <div class="bval-dcard"><h5>Что показывает связка ROE × P/BV × капитал</h5><div class="bval-nar-txt" data-tk="${tk}">${esc(bvalBankNarrative(b))}</div></div>
        <div class="bval-dcard"><h5>Капитал → дивиденды</h5>${bvalBridgeHTML(b)}</div>
        <div class="bval-dcard"><h5>Против медианы сектора</h5>${bvalPeersHTML(b)}</div>
        <div class="bval-dcard"><h5>Тренд достаточности капитала</h5><div>${trendHTML}</div>
          <div class="bval-detail-meta">Даты: MOEX ${esc(vin.moex || '—')} · Ф.102 ${esc(vin.cbr_102 || '—')} · Ф.123 ${esc(vin.cbr_123 || '—')} · Ф.135 ${esc(vin.cbr_135 || '—')}</div>
          ${dh ? `<div class="bval-detail-meta">Дивиденды: ${dh}</div>` : ''}
          ${fcHTML ? `<div class="bval-fc"><span class="k">Прогноз — ручной ввод:</span> ${fcHTML}</div>` : ''}</div>
      </div>
      ${(b.warnings || []).length ? `<div class="bval-wlines">${(b.warnings || []).map((w) => `<div class="bval-wline">⚠ ${esc(w)}</div>`).join('')}</div>` : ''}
    </td></tr>`;
    return main + detail;
  }).join('');
  wrap.innerHTML = `<div class="bval-table-scroll"><table class="bval-table"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table></div>`;
  wrap.querySelectorAll('th[data-key]').forEach((el) => el.addEventListener('click', () => {
    const k = el.dataset.key;
    BVAL_SORT = { key: k, dir: BVAL_SORT.key === k ? -BVAL_SORT.dir : -1 };
    bvalTable();
  }));
  wrap.querySelectorAll('tr.bval-row').forEach((tr) => tr.addEventListener('click', () => {
    const d = wrap.querySelector(`tr.bval-detail[data-i="${tr.dataset.i}"]`);
    if (d) d.hidden = !d.hidden;
    bvalSelect(tr.dataset.tk);        // единый выбор: подсветка + карта + история
  }));
}

function bvalScatterDraw() {
  const ctx = document.getElementById('bval-scatter');
  if (!ctx || !window.Chart) return;
  const cap = document.getElementById('bval-cap-coe'); if (cap) cap.textContent = BVAL_COE;
  if (window.__bvalChart) { try { window.__bvalChart.destroy(); } catch (e) { /* noop */ } }
  const pts = (BVAL.banks || []).filter((b) => isNum(b.p_bv) && isNum(b.roe));
  const xmax = Math.max(1.3, ...pts.map((b) => b.p_bv)) * 1.15;
  const coe = BVAL_COE;
  const labelPlugin = {
    id: 'bvalLabels',
    afterDatasetsDraw(chart) {
      const { ctx: c } = chart;
      const meta = chart.getDatasetMeta(0);
      c.save(); c.font = '600 11px system-ui,sans-serif'; c.fillStyle = '#3A424E'; c.textAlign = 'left';
      pts.forEach((b, i) => { const el = meta.data[i]; if (el) c.fillText(b.ticker, el.x + 7, el.y + 4); });
      c.restore();
    },
  };
  window.__bvalChart = new window.Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        { label: 'Банки', data: pts.map((b) => ({ x: b.p_bv, y: b.roe })),
          pointBackgroundColor: pts.map((b) => bvalCapColor(b)),
          pointBorderColor: pts.map((b) => (b.ticker === BVAL_SEL ? '#263140' : 'rgba(255,255,255,.85)')),
          pointBorderWidth: pts.map((b) => (b.ticker === BVAL_SEL ? 3 : 1)),
          pointRadius: pts.map((b) => bvalPointR(b) + (b.ticker === BVAL_SEL ? 3 : 0)),
          pointHoverRadius: pts.map((b) => bvalPointR(b) + 2), order: 2 },
        { type: 'line', label: `ROE-линия P/BV = ROE/COE (${coe}%)`, order: 1,
          data: [{ x: 0, y: 0 }, { x: xmax, y: coe * xmax }],
          borderColor: '#A2452C', borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      onClick: (evt, _els, chart) => {
        const hit = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, true).filter((e) => e.datasetIndex === 0);
        if (hit.length && pts[hit[0].index]) bvalSelect(pts[hit[0].index].ticker);
      },
      scales: {
        x: { min: 0, max: xmax, title: { display: true, text: 'P/BV (цена капитала)', color: '#5A6472' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
        y: { min: 0, title: { display: true, text: 'ROE, % (прибыльность)', color: '#5A6472' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (it) => { const b = pts[it.dataIndex]; return b ? `${b.name}: P/BV ${b.p_bv}, ROE ${b.roe}%${isNum(b.dividend_capacity_score) ? `, дивспособн. ${b.dividend_capacity_score}/100` : ''}` : ''; }, filter: (it) => it.datasetIndex === 0 } },
      },
    },
    plugins: [labelPlugin],
  });
  window.__bvalChart.$pts = pts;    // для точечного выделения без пересоздания (bvalScatterSelect)
}

// ── price-vs-capital trajectory (site/cbr/history.json) ──────────────────────
function renderBvalHistory() {
  const box = document.getElementById('bval-hist');
  if (!box) return;
  loadBanksHistory((err) => {
    const chips = document.getElementById('bval-hist-chips');
    if (err || !BHIST) { box.style.display = 'none'; return; }
    const banks = (BHIST.banks || []).filter((b) => (b.points || []).length);
    if (!banks.length) { box.style.display = 'none'; return; }
    // синхронизация с единым выбором: если банк уже выбран в таблице/карте и есть в истории — показываем его
    if (BVAL_SEL && banks.some((b) => b.ticker === BVAL_SEL)) BHIST_SEL = BVAL_SEL;
    else if (!BHIST_SEL || !banks.some((b) => b.ticker === BHIST_SEL)) BHIST_SEL = banks[0].ticker;
    chips.innerHTML = banks.map((b) => {
      const cheap = b.cheap ? ' <span class="bh-cheap">&lt;1</span>' : '';
      return `<button class="bh-chip${b.ticker === BHIST_SEL ? ' on' : ''}" data-tk="${esc(b.ticker)}" title="${esc(b.name)}">
        <span class="bval-dot" style="background:${esc(b.color || '#888')}"></span>${esc(b.ticker)}${cheap}</button>`;
    }).join('');
    chips.querySelectorAll('.bh-chip').forEach((el) => el.addEventListener('click', () => bvalSelect(el.dataset.tk)));
    bvalHistDraw();
  });
}

function bvalHistDraw() {
  const canvas = document.getElementById('bval-hist-canvas');
  if (!canvas || !window.Chart || !BHIST) return;
  const b = (BHIST.banks || []).find((x) => x.ticker === BHIST_SEL);
  if (!b || !(b.points || []).length) return;
  const pts = b.points;
  const labels = pts.map((p) => p.d);
  const price = pts.map((p) => p.p);
  const bvps = pts.map((p) => p.bv);
  const cheapN = pts.filter((p) => p.pbv < 1).length;
  const cheapPct = Math.round(100 * cheapN / pts.length);
  const cur = isNum(b.last_pbv) ? b.last_pbv : pts[pts.length - 1].pbv;

  // header stat: current verdict + how often it has been below one capital
  const stat = document.getElementById('bval-hist-stat');
  if (stat) {
    const verdict = cur < 1
      ? `<span class="bh-verdict cheap">дешевле капитала · P/BV ${cur.toFixed(2)}</span>`
      : `<span class="bh-verdict rich">дороже капитала · P/BV ${cur.toFixed(2)}</span>`;
    stat.innerHTML = `<span class="bh-name" style="border-color:${esc(b.color || '#888')}">${esc(b.name)}</span>${verdict}
      <span class="muted">ниже 1 капитала: ${cheapPct}% времени окна (${cheapN}/${pts.length} мес.)</span>`;
  }

  // caption: window / warn / method
  const cap = document.getElementById('bval-hist-cap');
  if (cap) {
    const warn = b.warn ? `<div class="bh-warn">⚠ ${esc(b.warn)}</div>` : '';
    const clamp = b.split_clamp ? ` · окно с ${esc(b.split_clamp)} (необъяснённый разрыв цены)` : '';
    const spl = (b.splits_applied || []).length
      ? ` · цены до сплита приведены к текущей базе акций (${b.splits_applied.map((s) => `${esc(s.ratio)} от ${esc(s.date)}`).join(', ')}, реестр MOEX)` : '';
    cap.innerHTML = `Зелёная зона — цена ниже капитала (P/BV&nbsp;&lt;&nbsp;1, потенциально дёшево), красная — выше.
      Капитал на акцию = регуляторный капитал Ф.123&nbsp;/&nbsp;число акций; окно ${esc(pts[0].d)}–${esc(pts[pts.length - 1].d)}${clamp}${spl}.${warn}`;
  }

  if (window.__bhChart) { try { window.__bhChart.destroy(); } catch (e) { /* noop */ } }
  window.__bhChart = new window.Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Цена, ₽', data: price, borderColor: b.color || '#2C6E9B', backgroundColor: b.color || '#2C6E9B',
          borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.15, order: 1,
          fill: { target: 1, above: 'rgba(200,60,50,0.10)', below: 'rgba(33,160,56,0.16)' } },
        { label: 'Капитал на акцию (P/BV = 1)', data: bvps, borderColor: '#7A8598', backgroundColor: '#7A8598',
          borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 0, tension: 0.15, order: 2, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#5A6472', maxTicksLimit: 9, autoSkip: true, maxRotation: 0 } },
        y: { grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' }, title: { display: true, text: '₽ на акцию', color: '#5A6472' } },
      },
      plugins: {
        legend: { display: true, position: 'bottom', labels: { boxWidth: 22, color: '#3A424E', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (it) => `${it.dataset.label}: ${Number(it.raw).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽`,
            afterBody: (items) => { const p = pts[items[0].dataIndex]; return p ? `P/BV: ${p.pbv.toFixed(2)}${p.pbv < 1 ? '  — дешевле капитала' : ''}` : ''; },
          },
        },
      },
    },
  });
}

// ══════════════════════════════════════════════════════════════════════════
// НОВОСТИ (#news) — утренний брифинг из site/news.json (CI + Gemini, статик). Не ИИР.
// ══════════════════════════════════════════════════════════════════════════
NEWS = null;
let NEWS_INVEST_ONLY = false;
let NEWS_FETCHED_AT = 0;
let NEWS_LOAD_PROMISE = null;
const NEWS_REFRESH_MS = 5 * 60 * 1000;
const NEWS_STALE_MS = 6 * 60 * 60 * 1000;

const NEWS_CAT = {
  cb_policy: 'ЦБ', banks: 'Банки', markets: 'Рынки', macro: 'Макро',
  corporate: 'Компании', tech: 'Технологии', geopolitics: 'Геополитика',
};
const NEWS_AGENDA_TYPE = {
  dividend_cutoff: 'Отсечка', earnings: 'Отчёт', ofz_auction: 'Аукцион ОФЗ',
  cb_minfin: 'ЦБ/Минфин', macro: 'Макро',
};

function loadNews(cb, force = false) {
  if (NEWS && !force && Date.now() - NEWS_FETCHED_AT < NEWS_REFRESH_MS) { cb(null, false); return; }
  if (!NEWS_LOAD_PROMISE) {
    const previousVersion = NEWS && NEWS.generated_at;
    NEWS_LOAD_PROMISE = fetch('news.json?t=' + Date.now(), { cache: 'no-store' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((j) => {
        if (!j || !Array.isArray(j.market_snapshot) || !j.market_snapshot.length) throw new Error('empty');
        NEWS = j;
        NEWS_FETCHED_AT = Date.now();
        return previousVersion !== j.generated_at;
      })
      .finally(() => { NEWS_LOAD_PROMISE = null; });
  }
  NEWS_LOAD_PROMISE
    .then((changed) => cb(null, changed))
    .catch((e) => { console.error('[news]', e); cb(e, false); });
}

function newsMskTime(iso, withDate) {
  const t = Date.parse(iso);
  if (isNaN(t)) return '';
  const opt = { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' };
  if (withDate) { opt.day = '2-digit'; opt.month = '2-digit'; }
  return new Date(t).toLocaleString('ru-RU', opt).replace(',', '');
}

function newsRelTime(iso) {
  const t = Date.parse(iso);
  if (isNaN(t)) return '';
  const diff = (Date.now() - t) / 1000;
  if (diff < 0) return newsMskTime(iso, true);
  if (diff < 3600) return Math.max(1, Math.round(diff / 60)) + ' мин назад';
  if (diff < 86400) return Math.round(diff / 3600) + ' ч назад';
  return newsMskTime(iso, true);
}

function newsFreshness(iso) {
  const ts = Date.parse(iso);
  if (!isFinite(ts)) return { stale: true, ageHours: null };
  const age = Math.max(0, Date.now() - ts);
  return { stale: age > NEWS_STALE_MS, ageHours: Math.floor(age / 3600000) };
}

// источники приходят из внешних лент/каналов (через Gemini) → доверять URL нельзя:
// пропускаем ТОЛЬКО http(s), иначе ссылка не кликабельна (защита от javascript:/data: XSS)
function newsSafeUrl(u) {
  const s = String(u == null ? '' : u).trim();
  return /^https?:\/\//i.test(s) ? s : null;
}

function newsChangeCls(pct) {
  const v = parseFloat(String(pct).replace('%', '').replace('+', ''));
  if (!isFinite(v) || v === 0) return 'flat';
  return v > 0 ? 'up' : 'down';
}

function newsChipHTML(s) {
  const cls = newsChangeCls(s.change_pct);
  return `<div class="news-chip">
    <span class="nc-name">${esc(s.name)}</span>
    <span class="nc-val">${esc(s.value)}</span>
    <span class="nc-chg ${cls}">${esc(s.change_pct || '—')}</span>
    <span class="nc-as">${esc(s.as_of || '')}</span>
  </div>`;
}

function newsCardHTML(it, i, kind) {
  const star = (it.importance >= 4) ? '<span class="news-star" title="важное">★</span>' : '';
  const cat = it.category ? `<span class="news-cat cat-${esc(it.category)}">${esc(NEWS_CAT[it.category] || it.category)}</span>` : '';
  const inv = it.investment_relevant ? '<span class="news-inv" title="инвестиционно значимо">₽</span>' : '';
  const rel = it.published_at ? `<span class="news-time">${esc(newsRelTime(it.published_at))}</span>` : '';
  const srcs = (it.sources || []).map((s) => {
    const u = newsSafeUrl(s.url);
    return u
      ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.name || 'источник')}</a>`
      : `<span>${esc(s.name || '')}</span>`;
  }).join(' · ');
  const ctx = it.context ? `<div class="news-ctx" id="nctx-${kind}-${i}" hidden>${esc(it.context)}</div>` : '';
  const expandable = it.context ? ' news-expandable' : '';
  return `<article class="news-card${expandable}" data-kind="${kind}" data-i="${i}">
    <div class="news-head">
      ${cat}${star}${inv}
      <span class="news-hl">${esc(it.headline || '')}</span>
    </div>
    <div class="news-meta">${rel}${srcs ? `<span class="news-src">${srcs}</span>` : ''}${it.context ? '<span class="news-more">контекст ▾</span>' : ''}</div>
    ${ctx}
  </article>`;
}

function newsListHTML(items, kind) {
  const list = (items || []).filter((it) => !NEWS_INVEST_ONLY || it.investment_relevant === true)
    .slice().sort((a, b) => (b.importance || 0) - (a.importance || 0));
  if (!list.length) return '<div class="news-empty muted">Пока без значимых новостей.</div>';
  return list.map((it) => newsCardHTML(it, (items).indexOf(it), kind)).join('');
}

function newsAgendaHTML(items) {
  const list = (items || []).slice().sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));
  if (!list.length) return '<div class="news-empty muted">На сегодня событий в календаре нет.</div>';
  return list.map((a) => {
    const tp = a.type ? `<span class="news-cat cat-${esc(a.type)}">${esc(NEWS_AGENDA_TYPE[a.type] || a.type)}</span>` : '';
    const star = (a.importance >= 4) ? '<span class="news-star">★</span>' : '';
    const tk = a.ticker ? `<span class="news-agenda-tk">${esc(a.ticker)}</span>` : '';
    return `<div class="news-agenda-row"><span class="news-agenda-time">${esc(a.time || '—')}</span>${tp}<span class="news-agenda-ev">${esc(a.event || '')}</span>${tk}${star}</div>`;
  }).join('');
}

function newsShellHTML(d) {
  const upd = newsMskTime(d.generated_at);
  const freshness = newsFreshness(d.generated_at);
  const stale = freshness.stale
    ? `<span class="news-stale">данные устарели${freshness.ageHours == null ? '' : ` · ${freshness.ageHours} ч`}</span>`
    : '';
  const back = d.external_backdrop ? `<div class="news-backdrop">${esc(d.external_backdrop)}</div>` : '';
  // RF-инструменты с интерактивными графиками показываем в блоке «Графики рынка» ниже —
  // из статичного снапшота их убираем, чтобы не дублировать (глобальные рынки остаются чипами)
  const RF_INTERACTIVE = ['mcftr', 'imoex', 'rts', 'usd/rub', 'cny/rub'];
  const chips = (d.market_snapshot || [])
    .filter((s) => !RF_INTERACTIVE.includes(String(s.name || '').trim().toLowerCase()))
    .map(newsChipHTML).join('');
  return `
    <div class="news-topbar">
      <div class="news-updated">Обновлено ${upd ? `в <b>${esc(upd)}</b> МСК` : '—'}${d.date ? ` · ${esc(d.date)}` : ''}${stale}</div>
      <label class="news-toggle"><input type="checkbox" id="news-invest"${NEWS_INVEST_ONLY ? ' checked' : ''}> только инвестиции</label>
    </div>
    ${back}
    <div class="news-snapshot" aria-label="Рыночный снапшот">${chips || '<span class="muted">снапшот недоступен</span>'}</div>

    <section class="news-block">
      <h3 class="news-h">За ночь</h3>
      <div class="news-list" id="news-overnight">${newsListHTML(d.overnight, 'ov')}</div>
    </section>
    <section class="news-block">
      <h3 class="news-h">Главное вчера</h3>
      <div class="news-list" id="news-yesterday">${newsListHTML(d.yesterday, 'ys')}</div>
    </section>
    <section class="news-block">
      <h3 class="news-h">Сегодня в календаре</h3>
      <div class="news-agenda" id="news-agenda">${newsAgendaHTML(d.today_agenda)}</div>
    </section>
    <div class="news-disc muted">Источники — открытые ленты и каналы; формулировки структурированы автоматически. Не индивидуальная инвестиционная рекомендация.</div>`;
}

function newsWire() {
  const body = document.getElementById('news-body');
  if (!body) return;
  const tgl = document.getElementById('news-invest');
  if (tgl) tgl.addEventListener('change', () => {
    NEWS_INVEST_ONLY = tgl.checked;
    const ov = document.getElementById('news-overnight');
    const ys = document.getElementById('news-yesterday');
    if (ov) ov.innerHTML = newsListHTML(NEWS.overnight, 'ov');
    if (ys) ys.innerHTML = newsListHTML(NEWS.yesterday, 'ys');
  });
  body.addEventListener('click', (e) => {
    const card = e.target.closest('.news-expandable');
    if (!card || e.target.closest('a')) return;
    const ctx = card.querySelector('.news-ctx');
    const more = card.querySelector('.news-more');
    if (ctx) {
      ctx.hidden = !ctx.hidden;
      card.classList.toggle('open', !ctx.hidden);
      if (more) more.textContent = ctx.hidden ? 'контекст ▾' : 'контекст ▴';
    }
  });
}

function renderNews(force = false) {
  const body = document.getElementById('news-body');
  if (!body) return;
  const alreadyShown = body.dataset.shown === '1' && NEWS;
  if (!alreadyShown) body.innerHTML = '<div class="news-loading muted">Загрузка новостного блока…</div>';
  loadNews((err, changed) => {
    if (err && !NEWS) { body.innerHTML = '<div class="news-fallback">Новостной блок недоступен — файл ещё не сгенерирован.</div>'; return; }
    if (err && alreadyShown) {
      const updated = body.querySelector('.news-updated');
      if (updated && !updated.querySelector('.news-refresh-error')) {
        updated.insertAdjacentHTML('beforeend', '<span class="news-refresh-error">не удалось проверить обновление</span>');
      }
      return;
    }
    if (alreadyShown && !changed && !force) return;
    body.innerHTML = newsShellHTML(NEWS);
    body.dataset.shown = '1';
    newsWire();
  }, force || alreadyShown);
}

function refreshVisibleNews() {
  if (document.visibilityState === 'visible' && getSectionFromHash() === 'news'
      && Date.now() - NEWS_FETCHED_AT >= NEWS_REFRESH_MS) renderNews(true);
}

document.addEventListener('visibilitychange', refreshVisibleNews);
setInterval(refreshVisibleNews, NEWS_REFRESH_MS);

// ══════════════════════════════════════════════════════════════════════════
// PORTFOLIO X-RAY & REBALANCE LAB (#my-portfolio) — проф. риск/перформанс-терминал
// Данные ТОЛЬКО реальные: returns.json (месячный ценовой ретёрн + див), MCFTR (marketsaw),
// data.json (дивпрогноз/cut_risk/сектор/качество), RFR-константа. Нет synthetic/mock/target price.
// Ограничение: данные МЕСЯЧНЫЕ → дневной VaR/rolling-дни/EWMA-daily честно недоступны.
// Не ИИР. Все сценарии — диагностические.
// ══════════════════════════════════════════════════════════════════════════
const PFX = { Z95: 1.6448536, Z99: 2.3263479, TAX: 0.87, LAMBDA: 0.94 };

// Алиасы ввода → канонический тикер MOEX. КРИТИЧНО: T≠TATN, SNGSP≠SNGS, преф отдельны.
const PFX_ALIASES = {
  'TCSG': 'T', 'TINKOFF': 'T', 'T-БАНК': 'T', 'Т-БАНК': 'T', 'ТИНЬКОФФ': 'T', 'ТКС': 'T',
  'SNGS_P': 'SNGSP', 'SNGS-P': 'SNGSP', 'SNGSPREF': 'SNGSP', 'СУРГУТ АП': 'SNGSP', 'СУРГУТ ПРЕФ': 'SNGSP',
  'СУРГУТНЕФТЕГАЗ АП': 'SNGSP', 'СУРГУТНЕФТЕГАЗ ПРЕФ': 'SNGSP',
  'TATNEFT': 'TATN', 'ТАТНЕФТЬ': 'TATN', 'ТАТНЕФТЬ АО': 'TATN',
  'ТАТНЕФТЬ АП': 'TATNP', 'ТАТНЕФТЬ ПРЕФ': 'TATNP', 'TATN PREF': 'TATNP',
  'TRANSNEFT PREF': 'TRNFP', 'ТРАНСНЕФТЬ АП': 'TRNFP', 'ТРАНСНЕФТЬ ПРЕФ': 'TRNFP',
};
// Бумаги, которых нет в data.json, но которые пользователь может держать (для autocomplete + честный warning)
const PFX_EXTRA_TICKERS = [
  { ticker: 'SNGS', name: 'Сургутнефтегаз', _extra: true },
  { ticker: 'SNGSP', name: 'Сургутнефтегаз ап', _extra: true },
];
function pfxCanonTicker(raw) {
  const up = String(raw || '').toUpperCase().replace(/\s+/g, ' ').trim();
  if (PFX_ALIASES[up]) return PFX_ALIASES[up];
  const clean = up.replace(/[^A-Z0-9._-]/g, '');
  return PFX_ALIASES[clean] || clean;
}
// объединённый универсум для autocomplete: data.json + недостающие обязательные
function pfxUniverse() {
  const base = (DATA && DATA.tickers) ? DATA.tickers.slice() : [];
  const have = new Set(base.map((t) => t.ticker));
  PFX_EXTRA_TICKERS.forEach((e) => { if (!have.has(e.ticker)) base.push(e); });
  return base;
}
// аномалия ряда (split-like): месячный total-return скачок > 250% → ряд не годится для риска
function pfxSeriesAnomaly(tr) {
  if (!tr || !tr.length) return false;
  return tr.some((x) => isNum(x) && Math.abs(x) > 2.5);
}

// ── чистая математика ────────────────────────────────────────────────────────
function pfxMean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0; }
function pfxStd(a, sample) {
  if (a.length < 2) return 0;
  const m = pfxMean(a), n = a.length - (sample === false ? 0 : 1);
  return Math.sqrt(a.reduce((s, x) => s + (x - m) * (x - m), 0) / n);
}
function pfxPercentile(a, p) {              // p в [0,1], линейная интерполяция
  if (!a.length) return null;
  const s = a.slice().sort((x, y) => x - y), idx = p * (s.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (idx - lo);
}
function pfxSkew(a) {
  const n = a.length; if (n < 3) return 0;
  const m = pfxMean(a), sd = pfxStd(a);
  if (sd === 0) return 0;
  return a.reduce((s, x) => s + Math.pow((x - m) / sd, 3), 0) / n;
}
function pfxKurt(a) {                       // excess kurtosis
  const n = a.length; if (n < 4) return 0;
  const m = pfxMean(a), sd = pfxStd(a);
  if (sd === 0) return 0;
  return a.reduce((s, x) => s + Math.pow((x - m) / sd, 4), 0) / n - 3;
}
function pfxMaxDrawdown(cum) {              // cum = кумулятивная equity (старт 1)
  let peak = cum[0] || 1, mdd = 0, peakIdx = 0, troughIdx = 0, curPeakIdx = 0;
  for (let i = 0; i < cum.length; i++) {
    if (cum[i] > peak) { peak = cum[i]; curPeakIdx = i; }
    const dd = cum[i] / peak - 1;
    if (dd < mdd) { mdd = dd; peakIdx = curPeakIdx; troughIdx = i; }
  }
  // время восстановления: месяцев от trough до возврата к пику
  let recovery = null;
  for (let i = troughIdx; i < cum.length; i++) { if (cum[i] >= cum[peakIdx]) { recovery = i - troughIdx; break; } }
  return { mdd, peakIdx, troughIdx, recovery };
}
function pfxEquity(rets) { const c = []; let v = 1; for (const r of rets) { v *= (1 + r); c.push(v); } return c; }

// ── слой данных: месячные ряды портфеля / бенчмарка / RFR ────────────────────
// total return бумаги за месяц = ценовой (data) + дивдоходность (div). Явно разделено.
function pfxTickerTotalReturns(ticker) {
  if (!PF_RETURNS || !PF_RETURNS.data) return null;
  const pr = PF_RETURNS.data[ticker];
  if (!pr) return null;
  const dv = (PF_RETURNS.div && PF_RETURNS.div[ticker]) || null;
  // total = ценовой + дивидендный. null (нет листинга/данных) — обрезаем ведущие, внутри → пропуск (0),
  // чтобы null не превращался в фейковый 0-ретёрн и не давал NaN в риск-математике.
  let start = 0;
  while (start < pr.length && !isNum(pr[start])) start += 1;   // T: первые ~10 мес null (тикер с ноя-2024)
  const out = [];
  for (let i = start; i < pr.length; i++) {
    const r = isNum(pr[i]) ? pr[i] : 0;
    out.push(r + (dv && isNum(dv[i]) ? dv[i] : 0));
  }
  return out.length ? out : null;
}

// парсинг + валидация + слияние дубликатов (взвеш. средняя цена по количеству)
function pfxParseValidate(text) {
  const map = myPortfolioTickerMap();
  const warns = [];
  const seen = {};                                     // ticker → {ticker, quantity, avg_price}
  const dupes = new Set();
  const badLines = [];
  String(text || '').split(/\r?\n/).forEach((raw) => {
    const line = raw.split('#')[0].trim();             // комментарий после #
    if (!line) return;
    const parts = line.split(/[;,\t ]+/).map((x) => x.trim()).filter(Boolean);
    const ticker = pfxCanonTicker(parts[0]);            // резолв алиасов (TCSG→T, СУРГУТ АП→SNGSP…)
    const qty = Number(String(parts[1] || '').replace(',', '.'));
    const avg = Number(String(parts[2] || '').replace(',', '.'));
    if (!ticker || !/[A-Z0-9]/.test(ticker) || parts.length < 2 || !isFinite(qty)) { badLines.push(raw.trim()); return; }
    if (qty <= 0) { warns.push({ tone: 'risk', msg: `${ticker}: неположительное количество (${parts[1]}) — строка пропущена` }); return; }
    if (!isFinite(avg) || avg < 0) { warns.push({ tone: 'risk', msg: `${ticker}: некорректная средняя цена — строка пропущена` }); return; }
    if (avg === 0) warns.push({ tone: 'warn', msg: `${ticker}: нулевая средняя цена — P&L по позиции не считается` });
    if (seen[ticker]) {                                // дубликат → слить
      const s = seen[ticker];
      const totQ = s.quantity + qty;
      s.avg_price = totQ > 0 ? (s.avg_price * s.quantity + avg * qty) / totQ : s.avg_price;
      s.quantity = totQ; dupes.add(ticker);
    } else seen[ticker] = { ticker, quantity: qty, avg_price: avg };
  });
  const rows = Object.values(seen);
  if (badLines.length) warns.push({ tone: 'warn', msg: `Не распознаны строки: ${badLines.slice(0, 4).map(esc).join(' · ')}${badLines.length > 4 ? '…' : ''}` });
  if (dupes.size) warns.push({ tone: 'neut', msg: `Дубликаты объединены (взвеш. средняя цена): ${[...dupes].join(', ')}` });
  const uni = new Set(pfxUniverse().map((t) => t.ticker));
  const noCover = rows.filter((r) => !map[r.ticker] && uni.has(r.ticker)).map((r) => r.ticker);   // известен (SNGSP), но нет чистой истории
  const unknown = rows.filter((r) => !map[r.ticker] && !uni.has(r.ticker)).map((r) => r.ticker);   // вообще неизвестный
  if (noCover.length) noCover.forEach((t) => warns.push({ tone: 'warn',
    msg: `${t} найден в портфеле, но нет чистой истории для риск-метрик. Цена/дивиденды считаются отдельно; бумага исключена из VaR/CVaR.` }));
  if (unknown.length) warns.push({ tone: 'risk', msg: `Неизвестный тикер (проверьте написание): ${unknown.join(', ')}` });
  return { rows, warnings: warns };
}

// backfilled portfolio по ТЕКУЩИМ весам (фикс.), НЕ история сделок. Считаем на общем «хвосте»
// истории, где у всех включённых бумаг есть данные.
function pfxPortfolioSeries(positions) {
  const months = (PF_RETURNS && PF_RETURNS.months) || [];
  const withHist = positions.filter((p) => p._tr && p._tr.length);
  if (!withHist.length || !months.length) return null;
  const minLen = Math.min(...withHist.map((p) => p._tr.length), months.length);
  if (minLen < 6) return null;
  const wsum = withHist.reduce((s, p) => s + p.value, 0);
  if (wsum <= 0) return null;
  const w = withHist.map((p) => p.value / wsum);        // веса нормированы на бумаги-с-историей
  const series = [];
  for (let m = 0; m < minLen; m++) {
    let r = 0;
    withHist.forEach((p, i) => { r += w[i] * p._tr[p._tr.length - minLen + m]; });
    series.push(r);
  }
  const covered = wsum / positions.reduce((s, p) => s + p.value, 0);
  return { series, months: months.slice(months.length - minLen), n: minLen, covered, weights: w, tickers: withHist.map((p) => p.ticker) };
}

// MCFTR (дневной уровень) → месячные total-return, выровнены по месяцам портфеля
function pfxBenchmarkMonthly(alignMonths) {
  if (!SAW_DATA || !SAW_DATA.series || !SAW_DATA.series.length) return null;
  const monthEnd = {};                                   // YYYY-MM → последний уровень месяца
  SAW_DATA.series.forEach(([d, v]) => { if (isNum(v)) monthEnd[String(d).slice(0, 7)] = v; });
  const keys = Object.keys(monthEnd).sort();
  const lvl = alignMonths.map((ym) => {
    if (monthEnd[ym] != null) return monthEnd[ym];
    const prior = keys.filter((k) => k <= ym); return prior.length ? monthEnd[prior[prior.length - 1]] : null;
  });
  const rets = [];
  for (let i = 1; i < lvl.length; i++) rets.push((lvl[i] != null && lvl[i - 1]) ? lvl[i] / lvl[i - 1] - 1 : 0);
  // первый месяц окна не имеет предыдущего уровня внутри окна → берём из полного ряда
  if (lvl[0] != null) {
    const i0 = keys.indexOf(alignMonths[0]);
    if (i0 > 0) rets.unshift(monthEnd[keys[i0]] / monthEnd[keys[i0 - 1]] - 1); else rets.unshift(0);
  } else rets.unshift(0);
  return rets.slice(0, alignMonths.length);
}

function pfxRfrMonthlyPct() {                             // текущая RFR как константа (истории нет)
  const a = myPortfolioRfrPct();
  return a != null ? { annual: a, monthly: a / 100 / 12, ok: true } : { annual: null, monthly: 0, ok: false };
}

// ── перформанс (месячная база, аннуализация ×12 / ×√12) ──────────────────────
function pfxPerf(rets, rfMonthly) {
  const n = rets.length;
  const cum = pfxEquity(rets), totalRet = cum[n - 1] - 1;
  const years = n / 12;
  const cagr = years > 0 ? Math.pow(1 + totalRet, 1 / years) - 1 : null;
  const volM = pfxStd(rets), volAnn = volM * Math.sqrt(12);
  const dd = pfxMaxDrawdown(cum);
  const meanM = pfxMean(rets);
  const excess = rets.map((r) => r - rfMonthly);
  const sharpe = volM > 0 ? (pfxMean(excess) / volM) * Math.sqrt(12) : null;
  const downside = rets.filter((r) => r < rfMonthly).map((r) => r - rfMonthly);
  const dStd = downside.length ? Math.sqrt(downside.reduce((s, x) => s + x * x, 0) / rets.length) : 0;
  const sortino = dStd > 0 ? (pfxMean(excess) / dStd) * Math.sqrt(12) : null;
  const calmar = (cagr != null && dd.mdd < 0) ? cagr / Math.abs(dd.mdd) : null;
  const wins = rets.filter((r) => r > 0).length;
  const period = (k) => { if (n < k) return null; const s = rets.slice(n - k); return pfxEquity(s)[k - 1] - 1; };
  return {
    n, totalRet, cagr, volAnn, meanAnn: meanM * 12, mdd: dd.mdd, recovery: dd.recovery,
    sharpe, sortino, calmar, winPct: wins / n, best: Math.max(...rets), worst: Math.min(...rets),
    ret1m: period(1), ret3m: period(3), ret6m: period(6), ret1y: period(12), ret3y: period(36), cum,
  };
}

// ── CAPM: Rp-Rf = alpha + beta*(Rm-Rf) ───────────────────────────────────────
function pfxCapm(port, bench, rfMonthly) {
  const n = Math.min(port.length, bench.length);
  if (n < 12) return { ok: false, reason: 'нужно ≥12 месяцев' };
  const p = port.slice(port.length - n), b = bench.slice(bench.length - n);
  const xs = b.map((r) => r - rfMonthly), ys = p.map((r) => r - rfMonthly);
  const mx = pfxMean(xs), my = pfxMean(ys);
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) ** 2; syy += (ys[i] - my) ** 2; }
  const beta = sxx > 0 ? sxy / sxx : null;
  const alphaM = my - (beta != null ? beta * mx : 0);
  const r2 = (sxx > 0 && syy > 0) ? (sxy * sxy) / (sxx * syy) : null;
  const corr = (sxx > 0 && syy > 0) ? sxy / Math.sqrt(sxx * syy) : null;
  // остатки, t-stat alpha
  const resid = ys.map((y, i) => y - (alphaM + (beta || 0) * xs[i]));
  const residStd = pfxStd(resid);
  const seAlpha = residStd * Math.sqrt(1 / n + (mx * mx) / (sxx || 1));
  const tAlpha = seAlpha > 0 ? alphaM / seAlpha : null;
  // tracking error / IR (актив к бенчу, без RFR)
  const active = p.map((r, i) => r - b[i]);
  const te = pfxStd(active) * Math.sqrt(12);
  const ir = te > 0 ? (pfxMean(active) * 12) / te : null;
  const treynor = (beta && beta !== 0) ? (pfxMean(ys) * 12) / beta : null;
  // capture ratios
  const up = [], upB = [], dn = [], dnB = [];
  for (let i = 0; i < n; i++) { if (b[i] > 0) { up.push(p[i]); upB.push(b[i]); } else if (b[i] < 0) { dn.push(p[i]); dnB.push(b[i]); } }
  const upCap = upB.length && pfxMean(upB) !== 0 ? pfxMean(up) / pfxMean(upB) : null;
  const dnCap = dnB.length && pfxMean(dnB) !== 0 ? pfxMean(dn) / pfxMean(dnB) : null;
  return {
    ok: true, n, beta, alphaAnn: alphaM * 12, r2, corr, residVolAnn: residStd * Math.sqrt(12),
    te, ir, treynor, tAlpha, upCapture: upCap, dnCapture: dnCap,
    bull: up.length ? pfxMean(up) * 12 : null, bear: dn.length ? pfxMean(dn) * 12 : null,
  };
}

// ── VaR-движок (МЕСЯЧНАЯ база; дневной — недоступен) ─────────────────────────
function pfxVaR(rets) {
  const n = rets.length;
  if (n < 6) return { ok: false };
  const conf = n >= 60 ? 'high' : n >= 36 ? 'medium' : n >= 18 ? 'low' : 'very_low';
  const mu = pfxMean(rets), sd = pfxStd(rets), S = pfxSkew(rets), K = pfxKurt(rets);
  const hist = (p) => pfxPercentile(rets, p);
  const cvar = (thr) => { const tail = rets.filter((r) => r <= thr); return tail.length ? pfxMean(tail) : thr; };
  const h95 = hist(0.05), h99 = hist(0.01);
  const gauss = (z) => mu - z * sd;
  // Cornish-Fisher скорректированный квантиль (клампим экстремальный хвост)
  const cf = (z) => {
    let zc = z + (1 / 6) * (z * z - 1) * S + (1 / 24) * (z * z * z - 3 * z) * K - (1 / 36) * (2 * z * z * z - 5 * z) * S * S;
    if (!isFinite(zc) || zc < z * 0.4 || zc > z * 3) zc = z;   // защита от «взрыва» на коротком ряде
    return mu - zc * sd;
  };
  return {
    ok: true, n, conf, mu, sd, skew: S, kurt: K,
    hist95: h95, hist99: h99, cvar95: cvar(h95), cvar99: cvar(h99),
    gauss95: gauss(PFX.Z95), gauss99: gauss(PFX.Z99),
    cf95: cf(PFX.Z95), cf99: cf(PFX.Z99),
  };
}

// VaR-backtest на месячных: сколько раз факт-убыток пробивал rolling-историч. VaR
function pfxVaRBacktest(rets, win) {
  win = win || 24;
  if (rets.length < win + 6) return { ok: false };
  let breaches = 0, obs = 0, worst = 0, lastIdx = null;
  for (let i = win; i < rets.length; i++) {
    const varT = pfxPercentile(rets.slice(i - win, i), 0.05);
    obs++;
    if (rets[i] < varT) { breaches++; lastIdx = i; if (rets[i] - varT < worst) worst = rets[i] - varT; }
  }
  return { ok: true, obs, breaches, freq: breaches / obs, expected: 0.05, worst, lastIdx };
}

// ── ковариация + shrinkage → component/marginal VaR, risk budget ─────────────
// Ledoit-Wolf-подобный shrink к диагонали: 232 бумаги на 90 мес → выборочная ковариация
// сингулярна, поэтому усадка обязательна. Возвращаем approx-флаг при короткой истории.
function pfxCovariance(seriesList) {
  const k = seriesList.length;
  const minLen = Math.min(...seriesList.map((s) => s.length));
  const approx = minLen < k + 12;                          // недостаточно наблюдений для устойчивой матрицы
  const X = seriesList.map((s) => s.slice(s.length - minLen));
  const means = X.map(pfxMean);
  const S = Array.from({ length: k }, () => new Array(k).fill(0));
  for (let i = 0; i < k; i++) for (let j = i; j < k; j++) {
    let c = 0; for (let t = 0; t < minLen; t++) c += (X[i][t] - means[i]) * (X[j][t] - means[j]);
    c /= Math.max(1, minLen - 1); S[i][j] = c; S[j][i] = c;
  }
  const avgVar = pfxMean(S.map((row, i) => S[i][i]));
  const lambda = approx ? 0.5 : Math.min(0.4, 12 / minLen);  // сильнее усадка при короткой истории
  for (let i = 0; i < k; i++) for (let j = 0; j < k; j++) {
    const target = i === j ? avgVar : 0;
    S[i][j] = (1 - lambda) * S[i][j] + lambda * target;
  }
  return { S, minLen, approx };
}
function pfxMatVec(S, w) { return S.map((row) => row.reduce((s, v, j) => s + v * w[j], 0)); }

function pfxRiskBudget(positions) {
  const withHist = positions.filter((p) => p._tr && p._tr.length >= 12);
  if (withHist.length < 2) return { ok: false };
  const wsum = withHist.reduce((s, p) => s + p.value, 0);
  const w = withHist.map((p) => p.value / wsum);
  const cov = pfxCovariance(withHist.map((p) => p._tr));
  const Sw = pfxMatVec(cov.S, w);
  const varP = w.reduce((s, wi, i) => s + wi * Sw[i], 0);
  const sigmaP = Math.sqrt(Math.max(varP, 1e-12));
  const rows = withHist.map((p, i) => {
    const marginal = Sw[i] / sigmaP;                       // marginal risk
    const component = w[i] * marginal;                     // component risk (₽-нейтрально, доля σ)
    return {
      ticker: p.ticker, weight: w[i], marginal, component,
      share: component / sigmaP, indivVol: pfxStd(p._tr) * Math.sqrt(12),
    };
  });
  rows.sort((a, b) => b.share - a.share);
  return { ok: true, sigmaAnn: sigmaP * Math.sqrt(12), rows, approx: cov.approx };
}

// ── дивидендный стресс-тест: base/conservative/stress/crisis ─────────────────
// income = Σ shares×DPS(dividend_forecast); payout_prob = 1 − cut_risk. cut-бакет по cut_risk.
function pfxCutBucket(cr) { return cr == null ? 'unknown' : cr >= 0.6 ? 'high' : cr >= 0.35 ? 'medium' : 'low'; }
function pfxDividendStress(positions) {
  const suspect = [];
  const items = positions.map((p) => {
    const t = p.t; let dps = t && isNum(t.dividend_forecast) ? t.dividend_forecast : null;
    const cr = t && isNum(t.cut_risk) ? t.cut_risk : null;
    // guard: dps/цена > 35% — почти наверняка до-сплитный/битый дивиденд (напр. T после сплита 1:10).
    // Не завышаем ожидаемый дивпоток без источника → исключаем DPS, помечаем «требует проверки».
    if (dps != null && p.current_price && dps / p.current_price > 0.35) { suspect.push(p.ticker); dps = null; }
    const base = dps != null ? dps * p.quantity : 0;   // дивпоток = число АКЦИЙ × DPS (не лоты)
    return { ticker: p.ticker, base, cr, bucket: pfxCutBucket(cr), prob: cr != null ? 1 - cr : null,
      yield: p.dividend_yield, hasData: dps != null && cr != null };
  });
  const F = { base: { low: 1, medium: 1, high: 1 }, conservative: { low: 1, medium: 0.75, high: 0.5 },
    stress: { low: 0.75, medium: 0.5, high: 0.15 }, crisis: { low: 0.75, medium: 0.25, high: 0 } };
  const scen = {};
  ['base', 'conservative', 'stress', 'crisis'].forEach((k) => {
    scen[k] = items.reduce((s, it) => s + it.base * (F[k][it.bucket] != null ? F[k][it.bucket] : (it.prob != null ? it.prob : 0)), 0);
  });
  const baseIncome = scen.base;
  const riskAdj = items.reduce((s, it) => s + it.base * (it.prob != null ? it.prob : 0), 0);
  const totalBase = items.reduce((s, it) => s + it.base, 0) || 1;
  items.forEach((it) => { it.share = it.base / totalBase; });
  const topIncome = items.filter((it) => it.base > 0).sort((a, b) => b.base - a.base).slice(0, 5);
  const topRisk = items.filter((it) => it.base > 0 && it.cr != null).sort((a, b) => (b.base * b.cr) - (a.base * a.cr)).slice(0, 5);
  // yield trap: высокая ожидаемая доходность + высокий cut risk
  const traps = items.filter((it) => isNum(it.yield) && it.yield >= 8 && it.cr != null && it.cr >= 0.5)
    .map((it) => it.ticker);
  const noData = items.filter((it) => !it.hasData).map((it) => it.ticker);
  return { items, scen, baseIncome, riskAdj, atRisk: baseIncome - riskAdj, topIncome, topRisk,
    traps, noData, suspect, topShare: topIncome.length ? topIncome[0].share : 0 };
}

// ── bootstrap устойчивости (месячный resample, горизонт 12 мес) ──────────────
function pfxBootstrap(port, bench, rfMonthly, sims) {
  const n = Math.min(port.length, bench.length);
  if (n < 18) return { ok: false, reason: 'нужно ≥18 месяцев' };
  sims = sims || 1000; const H = 12;
  const p = port.slice(port.length - n), b = bench.slice(bench.length - n);
  const cagrs = [], mdds = [], sharpes = [], excesses = []; let beatRet = 0, lowerDD = 0, negExc = 0;
  const benchCum = pfxEquity(b), benchCagr = Math.pow(benchCum[n - 1], 12 / n) - 1;
  for (let s = 0; s < sims; s++) {
    const rp = [], rb = [];
    for (let h = 0; h < H; h++) { const idx = Math.floor(Math.random() * n); rp.push(p[idx]); rb.push(b[idx]); }
    const cp = pfxEquity(rp), cb = pfxEquity(rb);
    const cagrP = cp[H - 1] - 1, cagrB = cb[H - 1] - 1;
    const volP = pfxStd(rp) || 1e-9;
    const shP = (pfxMean(rp) - rfMonthly) / volP * Math.sqrt(12);
    const ddP = pfxMaxDrawdown(cp).mdd, ddB = pfxMaxDrawdown(cb).mdd;
    cagrs.push(cagrP); mdds.push(ddP); sharpes.push(shP); excesses.push(cagrP - cagrB);
    if (cagrP > cagrB) beatRet++; if (ddP > ddB) lowerDD++; if (cagrP - cagrB < 0) negExc++;
  }
  const pct = (a, q) => pfxPercentile(a, q);
  const pLoss = cagrs.filter((x) => x < 0).length / sims;   // доля виртуальных лет с убытком
  return { ok: true, sims, benchCagr, histMonths: n, pLoss,
    pBeat: beatRet / sims, pLowerDD: lowerDD / sims, pNegExcess: negExc / sims,
    cagr: [pct(cagrs, 0.05), pct(cagrs, 0.5), pct(cagrs, 0.95)],
    mdd: [pct(mdds, 0.05), pct(mdds, 0.5), pct(mdds, 0.95)],
    sharpe: [pct(sharpes, 0.05), pct(sharpes, 0.5), pct(sharpes, 0.95)],
    cagrs, excesses };
}

// ── data-quality по позиции и портфелю ───────────────────────────────────────
function pfxPositionDQ(p) {
  const hist = p._tr ? p._tr.length : 0;
  let level, hlabel;
  if (!p.t || !p.current_price) { level = 'unavailable'; }
  else if (hist >= 60) level = 'high'; else if (hist >= 36) level = 'medium'; else if (hist >= 12) level = 'low'; else level = 'unavailable';
  const miss = [];
  if (!p.t) miss.push('нет в data.json');
  if (!p._tr) miss.push('нет истории цены');
  else if (hist < 36) miss.push(`история ${hist} мес`);
  if (p.t && !isNum(p.t.dividend_forecast)) miss.push('нет дивпрогноза');
  if (p.t && !isNum(p.t.cut_risk)) miss.push('нет cut risk');
  if (p.t && (!p.sector || p.sector === ND || p.sector === 'нет в покрытии')) miss.push('нет сектора');
  return { level, hist, miss };
}
function pfxDataQuality(positions) {
  positions.forEach((p) => { p._dq = pfxPositionDQ(p); });
  const total = positions.reduce((s, p) => s + p.value, 0) || 1;
  const lowW = positions.filter((p) => p._dq.level === 'low' || p._dq.level === 'unavailable').reduce((s, p) => s + p.value, 0) / total;
  const wmap = { high: 1, medium: 0.75, low: 0.45, unavailable: 0.1 };
  const score = Math.round(100 * positions.reduce((s, p) => s + (p.value / total) * wmap[p._dq.level], 0));
  return { score, lowWeight: lowW, hasBench: !!(SAW_DATA && SAW_DATA.series), hasRfr: pfxRfrMonthlyPct().ok };
}

// ── аллокация / exposure бакеты ──────────────────────────────────────────────
function pfxBuckets(positions, keyFn, order) {
  const total = positions.reduce((s, p) => s + p.value, 0) || 1;
  const map = {};
  positions.forEach((p) => { const k = keyFn(p); map[k] = (map[k] || 0) + p.value / total; });
  const entries = Object.entries(map);
  entries.sort((a, b) => (order ? order.indexOf(a[0]) - order.indexOf(b[0]) : b[1] - a[1]));
  return entries;
}
function pfxBetaBucket(b) { return b == null ? 'н/д' : b < 0.8 ? 'defensive <0.8' : b <= 1.1 ? 'market 0.8–1.1' : b <= 1.4 ? 'aggressive 1.1–1.4' : 'high >1.4'; }
function pfxYieldBucket(y) { return !isNum(y) ? 'н/д' : y < 3 ? '<3%' : y < 6 ? '3–6%' : y < 9 ? '6–9%' : '≥9%'; }
function pfxCutBucketLabel(cr) { const b = pfxCutBucket(cr); return { low: 'low cut risk', medium: 'medium cut risk', high: 'high cut risk', unknown: 'н/д' }[b]; }
function pfxAdvBucket(p) { const adv = p.t && isNum(p.t.adv) ? p.t.adv : null; return adv == null ? 'н/д' : adv >= 1e9 ? 'высокая (>1 млрд/д)' : adv >= 1e8 ? 'средняя' : 'низкая (<100 млн/д)'; }

// ── эвристический ребаланс (Suggested Diagnostic Weights, НЕ рекомендация) ────
function pfxScore(p, mode) {
  const t = p.t || {};
  const yield_ = isNum(p.dividend_yield) ? Math.min(p.dividend_yield / 12, 1) : 0.3;
  const q = isNum(t.quality_barra) ? t.quality_barra : 0.5;
  const stab = isNum(t.stability_score) ? t.stability_score : 0.5;
  const cut = isNum(t.cut_risk) ? t.cut_risk : 0.5;
  const vol = isNum(t.vol_ann) ? Math.min(t.vol_ann, 1) : 0.5;
  const beta = isNum(p._beta) ? p._beta : 1;
  const dq = p._dq ? { high: 1, medium: 0.7, low: 0.4, unavailable: 0.1 }[p._dq.level] : 0.5;
  const risk = isNum(p._riskShare) ? p._riskShare : 0.1;
  let s = q * 0.2 + stab * 0.15 + dq * 0.1 + yield_ * 0.15;
  s -= vol * 0.12 + cut * 0.13 + risk * 0.15;
  if (mode === 'lowrisk') { s -= vol * 0.25 + Math.max(0, beta - 1) * 0.2 + risk * 0.2; }
  if (mode === 'sharpe') { s += q * 0.15; s -= vol * 0.2 + risk * 0.15; }
  if (mode === 'dividend') { s += yield_ * 0.2 + stab * 0.15; s -= cut * 0.35; }
  if (mode === 'benchmark') { s += (isNum(p._beta) ? 0.2 * (1 - Math.min(1, Math.abs(p._beta - 1) / 0.5)) : 0); s -= risk * 0.15 + Math.abs(beta - 1) * 0.15; }
  if (mode === 'balanced') { s += q * 0.1 + yield_ * 0.1 + stab * 0.1; s -= vol * 0.12 + cut * 0.12 + risk * 0.15 + Math.max(0, beta - 1.1) * 0.1; }
  return Math.max(0.001, s);
}
function pfxRebalance(positions, mode) {
  const elig = positions.filter((p) => p.t && p.current_price);
  if (elig.length < 2) return null;
  const scores = elig.map((p) => pfxScore(p, mode));
  const ssum = scores.reduce((a, b) => a + b, 0);
  let w = scores.map((s) => s / ssum);
  // constraints: max 15% на бумагу, простая итеративная нормировка с капом
  for (let it = 0; it < 20; it++) {
    let over = 0, under = 0;
    w = w.map((x) => { if (x > 0.15) { over += x - 0.15; return 0.15; } under += (0.15 - x); return x; });
    if (over < 1e-6) break;
    w = w.map((x) => x < 0.15 ? x + over * ((0.15 - x) / (under || 1)) : x);
  }
  const cur = elig.map((p) => p.weight);
  const turnover = 0.5 * w.reduce((s, x, i) => s + Math.abs(x - cur[i]), 0);
  const changes = elig.map((p, i) => ({ ticker: p.ticker, cur: cur[i], sug: w[i], delta: w[i] - cur[i],
    reason: pfxRebalanceReason(p, w[i] - cur[i], mode) }));
  changes.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  return { mode, weights: w, tickers: elig.map((p) => p.ticker), positions: elig, turnover, changes };
}
function pfxRebalanceReason(p, delta, mode) {
  const t = p.t || {};
  if (delta < -0.01) {
    if (p.weight > 0.15) return 'снизить концентрацию';
    if (isNum(p._beta) && p._beta > 1.3) return 'снизить high-beta вклад';
    if (isNum(p._riskShare) && p._riskShare > 0.15) return 'снизить вклад в риск';
    if (isNum(t.cut_risk) && t.cut_risk > 0.5) return 'высокий cut risk';
    if (isNum(t.vol_ann) && t.vol_ann > 0.5) return 'высокая волатильность';
    return 'диверсификация';
  }
  if (delta > 0.01) {
    if (mode === 'dividend' && isNum(t.cut_risk) && t.cut_risk < 0.35) return 'стабильный дивиденд';
    if (isNum(t.quality_barra) && t.quality_barra > 0.6) return 'высокое качество';
    return 'улучшить диверсификацию';
  }
  return '≈ без изменений';
}

// ── классификация типа портфеля + rule-based диагноз ─────────────────────────
function pfxClassify(x) {
  const { dq, capm, perf, div, riskBudget } = x;
  if (dq.score < 45 || dq.lowWeight > 0.5) return { type: 'Low Data Quality', tone: 'warn' };
  if (!perf) return { type: 'Data Insufficient', tone: 'warn' };
  const beta = capm && capm.ok ? capm.beta : null;
  const top3 = x.top3;
  const highYield = div && isNum(x.grossYield) && x.grossYield > (x.rfr || 8);
  const highCutShare = div ? (div.topRisk.reduce((s, it) => s + it.base, 0) / (div.baseIncome || 1)) : 0;
  if (top3 > 0.6) return { type: 'Concentrated Bet', tone: 'risk' };
  if (highYield && highCutShare > 0.35) return { type: 'Yield Trap Risk', tone: 'risk' };
  if (beta != null && beta > 1.25 && highYield) return { type: 'High Beta Dividend Tilt', tone: 'warn' };
  if (beta != null && beta > 1.3) return { type: 'Aggressive Growth / High Beta', tone: 'warn' };
  if (beta != null && beta < 0.85 && highYield) return { type: 'Defensive Income', tone: 'good' };
  return { type: 'Market-like', tone: 'neut' };
}

// композитный risk score 0..100 (выше = рискованнее): концентрация+beta+VaR+cut-risk+DQ
function pfxRiskScore(c) {
  let s = 0; const drivers = [];
  const conc = Math.min(1, Math.max(0, (c.top3 - 0.35) / 0.4)); s += conc * 28; if (conc > 0.5) drivers.push('концентрация');
  const beta = c.capm && c.capm.ok ? c.capm.beta : (isNum(c.wBeta) ? c.wBeta : 1);
  const betaR = Math.min(1, Math.max(0, (beta - 0.9) / 0.6)); s += betaR * 22; if (betaR > 0.5) drivers.push('высокая beta');
  const varR = c.vaR && c.vaR.ok ? Math.min(1, Math.abs(c.vaR.hist95) / 0.15) : 0.4; s += varR * 22; if (varR > 0.6) drivers.push('высокий VaR');
  const highCut = c.positions.reduce((a, p) => a + (p.t && isNum(p.t.cut_risk) && p.t.cut_risk >= 0.6 ? p.weight : 0), 0);
  s += Math.min(1, highCut / 0.3) * 18; if (highCut > 0.2) drivers.push('cut risk');
  s += (1 - c.dq.score / 100) * 10; if (c.dq.score < 55) drivers.push('качество данных');
  return { score: Math.round(Math.min(100, s)), label: drivers.length ? 'драйверы: ' + drivers.slice(0, 3).join(', ') : 'сбалансирован' };
}

// ── P1: факторная диагностика (взвеш. экспозиции + перцентиль vs универсум) ───
const PFX_RATE_SENS = { 'Финансы': 0.9, 'Электроэнергетика': 0.8, 'Телеком': 0.7, 'Недвижимость': 0.85,
  'Потребительский': 0.5, 'Нефтегаз': 0.3, 'Металлы и добыча': 0.35, 'Химия': 0.4, 'Транспорт': 0.5 };
function pfxSectorRate(sec) {
  if (!sec) return 0.5;
  for (const k in PFX_RATE_SENS) if (sec.indexOf(k) >= 0) return PFX_RATE_SENS[k];
  return 0.5;
}
function pfxFactorVal(t, key) {
  if (!t) return null;
  switch (key) {
    case 'quality': return isNum(t.quality_barra) ? t.quality_barra : null;
    case 'momentum': return isNum(t.mom_score) ? t.mom_score : null;
    case 'divstab': return isNum(t.stability_score) ? t.stability_score : null;
    case 'value': return (t.valuation && isNum(t.valuation.fair_price) && isNum(t.price) && t.price > 0) ? (t.valuation.fair_price / t.price - 1) : null;
    case 'safety': return isNum(t.vol_ann) ? -t.vol_ann : null;         // выше = безопаснее (ниже vol)
    case 'payout': return isNum(t.payout) ? t.payout : null;
    case 'debt': return isNum(t.nd_ebitda) ? t.nd_ebitda : null;        // выше = больше долга
    case 'cutrisk': return isNum(t.cut_risk) ? t.cut_risk : null;       // выше = выше риск среза
    default: return null;
  }
}
function pfxFactors(c) {
  const uni = (DATA && DATA.tickers) ? DATA.tickers : [];
  const defs = [
    ['quality', 'Quality'], ['value', 'Value'], ['momentum', 'Momentum'], ['divstab', 'Dividend Stability'],
    ['safety', 'Safety'], ['payout', 'Payout'], ['debt', 'Debt Risk'], ['cutrisk', 'Cut Risk'],
  ];
  const factors = defs.map(([key, label]) => {
    let wsum = 0, acc = 0;
    c.positions.forEach((p) => { const v = pfxFactorVal(p.t, key); if (v != null) { acc += p.weight * v; wsum += p.weight; } });
    if (wsum < 0.3) return { key, label, pct: null, note: 'мало данных' };
    const wavg = acc / wsum;
    const dist = uni.map((t) => pfxFactorVal(t, key)).filter((x) => x != null).sort((a, b) => a - b);
    const below = dist.filter((x) => x <= wavg).length;
    const pct = dist.length ? Math.round(100 * below / dist.length) : null;   // перцентиль vs универсум
    return { key, label, pct, wavg, cover: wsum };
  });
  // rate sensitivity — по секторам (нет прямого фактора)
  const rate = c.positions.reduce((s, p) => s + p.weight * pfxSectorRate(p.sector), 0);
  factors.push({ key: 'rate', label: 'Rate Sensitivity', pct: Math.round(rate * 100), note: 'секторная оценка' });

  // человеко-язычный вывод
  const say = [];
  const f = (k) => factors.find((x) => x.key === k);
  const topSec = c.sorted && c.sorted.length ? null : null;
  const sectors = {}; c.positions.forEach((p) => { sectors[p.sector] = (sectors[p.sector] || 0) + p.weight; });
  const secTop = Object.entries(sectors).sort((a, b) => b[1] - a[1])[0];
  const cut = f('cutrisk'), q = f('quality'), db = f('debt'), mo = f('momentum'), sf = f('safety');
  if (cut && cut.pct != null && cut.pct >= 65 && isNum(c.grossYield) && c.grossYield > 6)
    say.push('Портфель перегружен дивидендными историями с повышенным риском среза выплат.');
  if (secTop && secTop[1] > 0.35) say.push(`Портфель сильно зависит от сектора «${secTop[0]}» (${Math.round(secTop[1] * 100)}%).`);
  if (q && q.pct != null && q.pct <= 30) say.push('Средневзвешенное качество бумаг ниже рынка.');
  else if (q && q.pct != null && q.pct >= 70) say.push('Портфель смещён в качественные бумаги.');
  if (db && db.pct != null && db.pct >= 70) say.push('Долговая нагрузка бумаг портфеля выше медианы рынка.');
  if (mo && mo.pct != null && mo.pct <= 25) say.push('Моментум портфеля слабый (бумаги отставали от рынка).');
  if (sf && sf.pct != null && sf.pct <= 25) say.push('Портфель смещён в волатильные бумаги (низкий Safety).');
  if (f('rate') && f('rate').pct >= 65) say.push('Портфель чувствителен к ставке ЦБ (много банков/энергетики/недвижимости).');
  if (c.dq && c.dq.lowWeight > 0.1) say.push(`Доля бумаг с неполными/устаревшими данными — ${Math.round(c.dq.lowWeight * 100)}%.`);
  if (!say.length) say.push('Явных факторных перекосов не выявлено по доступным данным.');
  return { factors, summary: say };
}

// ── P1: три главных риска человеческим языком ────────────────────────────────
function pfxTopRisks(c) {
  const risks = [];
  const top = c.sorted && c.sorted[0];
  if (top && top.weight > 0.2) risks.push({ sev: top.weight, tone: 'risk',
    text: `Концентрация: ${top.ticker} занимает ${Math.round(top.weight * 100)}% портфеля — сильная зависимость от одной бумаги.` });
  if (c.top3 > 0.5) risks.push({ sev: c.top3, tone: 'risk',
    text: `Топ-3 позиции — ${Math.round(c.top3 * 100)}% портфеля; риск и результат определяются несколькими бумагами.` });
  if (c.capm && c.capm.ok && c.capm.beta > 1.2) risks.push({ sev: (c.capm.beta - 1) * 0.6, tone: 'warn',
    text: `Высокая beta к MCFTR (${ru(c.capm.beta, 2)}): в падениях рынка портфель просаживается сильнее индекса.` });
  const highCut = c.positions.reduce((s, p) => s + (p.t && isNum(p.t.cut_risk) && p.t.cut_risk >= 0.6 ? p.weight : 0), 0);
  if (highCut > 0.25) risks.push({ sev: highCut, tone: 'risk',
    text: `${Math.round(highCut * 100)}% портфеля — бумаги с повышенным риском среза дивидендов; дивпоток нестабилен.` });
  if (c.div && c.div.traps && c.div.traps.length) risks.push({ sev: 0.5, tone: 'risk',
    text: `Возможные дивидендные ловушки (высокая доходность + высокий cut risk): ${c.div.traps.join(', ')}.` });
  if (c._rb && c._rb.ok && c._rb.substituted) risks.push({ sev: 0.62, tone: 'warn',
    text: `Ставка выше дивидендов: корп. AAA дают ${ru(c._rb.aaaNet, 1)}% «на руки» — не меньше ожидаемой чистой дивдоходности портфеля (${ru(c._rb.netY, 1)}%) при кредитном риске AAA и меньшей волатильности. См. «Ставка и облигации против портфеля».` });
  const sectors = {}; c.positions.forEach((p) => { sectors[p.sector] = (sectors[p.sector] || 0) + p.weight; });
  const secTop = Object.entries(sectors).sort((a, b) => b[1] - a[1])[0];
  if (secTop && secTop[1] > 0.4) risks.push({ sev: secTop[1] * 0.9, tone: 'warn',
    text: `Секторная концентрация: «${secTop[0]}» — ${Math.round(secTop[1] * 100)}% портфеля.` });
  if (c.vaR && c.vaR.ok && Math.abs(c.vaR.hist95) > 0.12) risks.push({ sev: Math.abs(c.vaR.hist95) * 3, tone: 'warn',
    text: `Высокий месячный VaR 95%: ${PN(c.vaR.hist95)} (${rub0(c.vaR.hist95 * c.total)}) — заметный downside-риск.` });
  if (c.dq && c.dq.lowWeight > 0.2) risks.push({ sev: c.dq.lowWeight, tone: 'warn',
    text: `${Math.round(c.dq.lowWeight * 100)}% веса — бумаги с неполной историей/данными; часть выводов low confidence.` });
  risks.sort((a, b) => b.sev - a.sev);
  return risks.slice(0, 3);
}

// ── P2: новости, связанные с тикерами портфеля (матч по названию компании) ───
function pfxNameToken(name) {
  if (!name) return null;
  let s = String(name).toLowerCase().replace(/пао|оао|ао|пуб\.|«|»|"|'|\(.*?\)/g, ' ').replace(/[-–]/g, ' ');
  const words = s.split(/\s+/).filter((w) => w.length >= 4 && !/^(банк|груп|холд|компан|россия|росс)$/.test(w));
  words.sort((a, b) => b.length - a.length);
  return words[0] || null;                             // самое длинное значимое слово названия
}
function pfxNewsForPortfolio(c) {
  if (!NEWS || NEWS.failed) return [];
  const items = [].concat(NEWS.overnight || [], NEWS.yesterday || []);
  if (!items.length) return [];
  const toks = [];
  c.positions.forEach((p) => { const tk = pfxNameToken(p.t && p.t.name); if (tk) toks.push({ ticker: p.ticker, tok: tk, w: p.weight }); });
  const hits = [], seen = new Set();
  items.forEach((it) => {
    const hay = ((it.headline || '') + ' ' + (it.context || '')).toLowerCase();
    for (const t of toks) {
      if (hay.indexOf(t.tok) >= 0 && !seen.has(it.id + t.ticker)) {
        seen.add(it.id + t.ticker);
        hits.push({ ticker: t.ticker, weight: t.w, headline: it.headline, importance: it.importance || 1, sources: it.sources || [] });
      }
    }
  });
  hits.sort((a, b) => (b.importance - a.importance) || (b.weight - a.weight));
  return hits.slice(0, 4);
}

// ── P2: Daily Portfolio Brief — «Сегодня важно для портфеля» ─────────────────
function pfxDailyBrief(c) {
  const items = [];
  // 1. рыночная фаза MCFTR
  if (SAW_DATA && SAW_DATA.current_phase) {
    const ph = SAW_DATA.current_phase;
    const reg = (MARLAMOV && MARLAMOV.meta && MARLAMOV.meta.regime) ? MARLAMOV.meta.regime : (ph.risk_level || '');
    const tone = ph.direction === 'down' ? (Math.abs(ph.move_pct || 0) > 0.15 ? 'risk' : 'warn') : 'good';
    items.push({ tone, text: `Рынок (MCFTR): ${ph.label}${isNum(ph.move_pct) ? `, ${PN(ph.move_pct, 0)} от максимума` : ''}${reg ? ` · режим ${reg}` : ''}.` });
  }
  // 2. главный структурный риск дня
  const tr = pfxTopRisks(c);
  if (tr[0]) items.push({ tone: tr[0].tone, text: tr[0].text });
  // 3. новости по бумагам портфеля
  const news = pfxNewsForPortfolio(c);
  news.slice(0, 2).forEach((h) => items.push({ tone: 'neut', text: `Новость по ${h.ticker}: ${h.headline}` }));
  // 4. сильнейшее движение за последний месяц
  let mover = null;
  c.positions.forEach((p) => { if (p._tr && p._tr.length) { const r = p._tr[p._tr.length - 1]; if (!mover || Math.abs(r) > Math.abs(mover.r)) mover = { ticker: p.ticker, r }; } });
  if (mover && Math.abs(mover.r) > 0.05) items.push({ tone: mover.r >= 0 ? 'good' : 'warn', text: `${mover.ticker}: ${PP(mover.r)} за последний месяц — сильнейшее движение в портфеле (месячные данные).` });
  // 5. дивидендная зависимость
  if (c.div && c.div.baseIncome > 0 && c.div.topShare > 0.3) items.push({ tone: 'warn', text: `${c.div.topIncome[0].ticker} даёт ${Math.round(c.div.topShare * 100)}% ожидаемого дивпотока — зависимость от одной выплаты.` });
  // 6. ставка ЦБ / RFR
  if (c.rf.ok) { const rateSens = c.positions.reduce((s, p) => s + p.weight * pfxSectorRate(p.sector), 0);
    items.push({ tone: rateSens > 0.6 ? 'warn' : 'neut', text: `Ставка (RFR) ${PU(c.rf.annual, 1)}%${MARLAMOV && MARLAMOV.meta && MARLAMOV.meta.regime ? ` · режим ${MARLAMOV.meta.regime}` : ''}${rateSens > 0.6 ? ' — портфель чувствителен к ставке (много банков/энергетики)' : ''}.` }); }
  // 7. устаревшие/неполные данные
  const stale = c.positions.filter((p) => p._dq && (p._dq.level === 'low' || p._dq.level === 'unavailable'));
  if (stale.length) items.push({ tone: 'warn', text: `Данные по ${stale.length} ${stale.length === 1 ? 'бумаге' : 'бумагам'} неполные/устаревшие (${stale.map((p) => p.ticker).slice(0, 4).join(', ')}) — доходность может быть искажена.` });
  // 8. позитив
  const pos = [];
  if (isNum(c.grossYield) && c.rf.ok && c.grossYield > c.rf.annual) pos.push(`ожидаемая дивдоходность ${PU(c.grossYield, 1)}% выше RFR`);
  if (c.capm && c.capm.ok && c.capm.alphaAnn > 0) pos.push(`историческая alpha положительна (+${PN(c.capm.alphaAnn, 1)})`);
  if (c.effN >= 6) pos.push(`неплохая диверсификация (${PU(c.effN, 1)} эфф. бумаг)`);
  if (pos.length) items.push({ tone: 'good', text: `Сильная сторона: ${pos[0]}.` });
  // 9. что проверить дальше
  const check = [];
  if (c.top3 > 0.5) check.push('снизить вклад топ-3 позиций');
  if (news.length) check.push(`новости по ${news[0].ticker}`);
  if (stale.length) check.push(`обновить данные по ${stale[0].ticker}`);
  if (c.div && c.div.traps && c.div.traps.length) check.push(`дивидендную устойчивость ${c.div.traps[0]}`);
  if (check.length) items.push({ tone: 'neut', text: `Проверить дальше: ${check.slice(0, 3).join('; ')}.` });
  return items.slice(0, 8);
}

// ── оркестратор: собрать полный набор метрик ─────────────────────────────────
function pfxEnrich(rows) {
  const positions = myPortfolioEnrich(rows);           // value/weight/sector/pnl/data_quality из data.json
  const seriesStatus = (PF_RETURNS && PF_RETURNS.series_status) || {};
  positions.forEach((p) => {
    const tr = pfxTickerTotalReturns(p.ticker);
    // приоритет — флаг из данных (clean_portfolio_data.py: needs_adjustment), эвристика как fallback
    p._anomaly = seriesStatus[p.ticker] === 'needs_adjustment' || pfxSeriesAnomaly(tr);
    p._tr = p._anomaly ? null : tr;                     // аномальный ряд НЕ идёт в риск (иначе фейковые 600% vol)
  });
  const total = positions.reduce((s, p) => s + (isNum(p.value) ? p.value : 0), 0);
  positions.forEach((p) => { p.weight = total > 0 ? p.value / total : 0; });
  return { positions, total };
}
function pfxCompute(rows) {
  const { positions, total } = pfxEnrich(rows);
  const rf = pfxRfrMonthlyPct();
  const pf = pfxPortfolioSeries(positions);
  let bench = null, capm = null, perf = null, vaR = null, backtest = null, boot = null;
  if (pf) {
    bench = pfxBenchmarkMonthly(pf.months);
    perf = pfxPerf(pf.series, rf.monthly);
    vaR = pfxVaR(pf.series);
    backtest = pfxVaRBacktest(pf.series, 24);
    if (bench) { capm = pfxCapm(pf.series, bench, rf.monthly); boot = pfxBootstrap(pf.series, bench, rf.monthly); }
  }
  // per-position beta (регрессия total returns бумаги на бенч)
  if (bench) positions.forEach((p) => {
    if (p._tr && p._tr.length >= 12) {
      const c = pfxCapm(p._tr, bench, rf.monthly);       // pfxCapm выравнивает по хвостам (общие последние месяцы)
      p._beta = c.ok ? c.beta : null;
    } else p._beta = null;
  });
  positions.forEach((p) => { p._ivol = p._tr && p._tr.length >= 6 ? pfxStd(p._tr) * Math.sqrt(12) : null;
    p._ivar = p._tr && p._tr.length >= 6 ? pfxPercentile(p._tr, 0.05) : null; });
  const riskBudget = pfxRiskBudget(positions);
  if (riskBudget.ok) riskBudget.rows.forEach((r) => { const p = positions.find((x) => x.ticker === r.ticker); if (p) p._riskShare = r.share; });
  const div = pfxDividendStress(positions);
  const dq = pfxDataQuality(positions);
  const _divSuspect = div.suspect || [];
  // агрегаты
  const sorted = positions.slice().sort((a, b) => b.weight - a.weight);
  const top3 = sorted.slice(0, 3).reduce((s, p) => s + p.weight, 0);
  const effN = positions.length ? 1 / positions.reduce((s, p) => s + p.weight * p.weight, 0) : 0;
  const grossYield = positions.reduce((s, p) => s + ((isNum(p.dividend_yield) && !_divSuspect.includes(p.ticker)) ? p.weight * p.dividend_yield : 0), 0);
  const wBeta = positions.reduce((s, p) => s + (isNum(p._beta) ? p.weight * p._beta : 0), 0);
  const cost = positions.reduce((s, p) => s + p.cost, 0);
  const cls = pfxClassify({ dq, capm, perf, div, riskBudget, top3, grossYield, rfr: rf.annual });
  return { positions, total, cost, rf, pf, bench, perf, capm, vaR, backtest, boot, riskBudget, div, dq,
    top3, effN, grossYield, wBeta, cls, sorted, _divSuspect };
}

// ── rule-based investment committee memo ─────────────────────────────────────
function pfxMemo(c) {
  const L = [];
  const pctv = (x, d) => isNum(x) ? (x >= 0 ? '+' : '') + ru(x * 100, d == null ? 1 : d) + '%' : 'н/д';
  L.push(['Executive Summary', `Портфель классифицирован как «${c.cls.type}». Стоимость ${rub0(c.total)}, ` +
    `${c.positions.length} позиций, эффективное число бумаг ${ru(c.effN, 1)}. ` +
    (c.capm && c.capm.ok ? `Историческая beta к MCFTR ${ru(c.capm.beta, 2)}, ` : 'Beta недоступна, ') +
    `top-3 концентрация ${ru(c.top3 * 100, 0)}%. Выводы — по доступным данным, не ИИР.`]);
  if (c.perf && c.bench) {
    const b = pfxPerf(c.bench, c.rf.monthly);
    L.push(['Performance vs MCFTR', `За окно ${c.pf.n} мес total return портфеля ${pctv(c.perf.totalRet)} против MCFTR ` +
      `${pctv(b.totalRet)} (активный ${pctv(c.perf.totalRet - b.totalRet)}). Ann.vol ${pctv(c.perf.volAnn)}, ` +
      `max drawdown ${pctv(c.perf.mdd)}. ${c.perf.n < 24 ? 'Короткая история — годовые метрики нестабильны.' : ''}`]);
  }
  if (c.capm && c.capm.ok) {
    const capt = isNum(c.capm.dnCapture) ? `downside capture ${ru(c.capm.dnCapture * 100, 0)}%` : '';
    L.push(['Alpha / Beta', `Beta ${ru(c.capm.beta, 2)} (${pfxBetaBucket(c.capm.beta)}), ист. alpha ${pctv(c.capm.alphaAnn)}/год, ` +
      `R² ${ru(c.capm.r2, 2)}, IR ${isNum(c.capm.ir) ? ru(c.capm.ir, 2) : 'н/д'}. ${capt}. Alpha историческая, не прогноз.`]);
  }
  if (c.vaR && c.vaR.ok) L.push(['Динамический риск / VaR', `Месячный historical VaR 95% ${pctv(c.vaR.hist95)} ` +
    `(${rub0(c.vaR.hist95 * c.total)}), CVaR 95% ${pctv(c.vaR.cvar95)}. Уверенность: ${c.vaR.conf}. ` +
    `VaR — не максимальный убыток; на месячной базе, дневной хвост не оценивается.`]);
  if (c.riskBudget && c.riskBudget.ok) { const t = c.riskBudget.rows[0];
    L.push(['Risk Drivers', `Главный вклад в риск: ${t.ticker} (${ru(t.share * 100, 0)}% риска при весе ${ru(t.weight * 100, 0)}%). ` +
      (c.riskBudget.approx ? 'Ковариация усажена из-за короткой истории (approx).' : '')]); }
  if (c.div) L.push(['Дивидендная устойчивость', `Ожидаемый дивпоток ${rub0(c.div.baseIncome)}/год, risk-adjusted ` +
    `${rub0(c.div.riskAdj)} (income at risk ${rub0(c.div.atRisk)}). ` +
    (c.div.traps.length ? `Yield-trap риск: ${c.div.traps.join(', ')}. ` : '') +
    (c.div.topShare > 0.25 ? `Один эмитент даёт ${ru(c.div.topShare * 100, 0)}% дивпотока.` : '')]);
  if (c.top3 > 0.5) L.push(['Концентрация', `Top-3 позиции — ${ru(c.top3 * 100, 0)}% портфеля. ` +
    `Основная задача — снизить их вклад в риск и VaR, а не добавлять ещё одну похожую бумагу.`]);
  if (c.boot && c.boot.ok) L.push(['Bootstrap-сценарии', `Вероятность обойти MCFTR по доходности на 1 год ` +
    `${ru(c.boot.pBeat * 100, 0)}%, получить меньшую просадку ${ru(c.boot.pLowerDD * 100, 0)}%. ` +
    `Bootstrap — resampling истории, не прогноз.`]);
  const lim = [];
  if (c.dq.lowWeight > 0.3) lim.push(`${ru(c.dq.lowWeight * 100, 0)}% веса — бумаги с неполной историей/данными`);
  if (!c.bench) lim.push('нет выравнивания с MCFTR — alpha/beta недоступны');
  if (!c.rf.ok) lim.push('RFR недоступна — excess-метрики без безрисковой ставки');
  lim.push('данные месячные — дневной VaR и rolling-дни не считаются');
  L.push(['Ограничения данных', lim.join('; ') + '.']);
  return L;
}

initRouter();   // ПОСЛЕ всех модулей (marketsaw/bonds/marlamov/methodology/cbr) — все let-глобалы инициализированы
