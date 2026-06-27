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
  loadReturns(() => { const o = document.getElementById('pf-out'); if (o && o.dataset.shown) renderPortfolio(); });  // жадно грузим историю → мгновенный результат

  render();
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

function toggleDetail(tr, t) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains('detail-row')) { next.remove(); return; }
  document.querySelectorAll('tr.detail-row').forEach((r) => r.remove());
  const dr = document.createElement('tr');
  dr.className = 'detail-row';
  dr.innerHTML = `<td colspan="${COLS.length}"><div class="detail">
    <div><h4>Оценка стоимости</h4>${valuationHTML(t.valuation)}</div>
    <div><h4>Позиция в секторе</h4>${sectorPercentilesHTML(t)}</div>
    <div><h4>Динамика показателей</h4>${historyHTML(t.history)}</div>
    <div><h4>Ключевые факторы (SHAP)</h4>${shapHTML(t)}</div>
    <div><h4>Детали</h4>${detailKV(t)}</div>
  </div></td>`;
  tr.after(dr);
  wireCharts(dr);
}

function renderCards() {
  const el = document.getElementById('cards');
  if (!el) return;
  el.innerHTML = VIEW.length ? VIEW.map((t, i) => {
    const statusChip = t.status === 'ok'
      ? '<span class="status-chip s-ok">✓ полные</span>'
      : '<span class="status-chip s-insuf">неполные</span>';
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
    box.innerHTML = valuationHTML(t.valuation) + sectorPercentilesHTML(t) + historyHTML(t.history) + shapHTML(t) + detailKV(t);
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
let PF_RETURNS = null;
let PF_RET_LOADING = false;
function loadReturns(cb) {
  if (PF_RETURNS) { if (cb) cb(); return; }
  if (PF_RET_LOADING) return;
  PF_RET_LOADING = true;
  fetch('returns.json?t=' + Date.now(), { cache: 'no-store' })   // cache-bust: уникальный URL обходит любой кэш/404
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
    .then((j) => { PF_RETURNS = { months: (j && j.meta && j.meta.months) || [], data: (j && j.data) || {}, div: (j && j.div) || null }; PF_RET_LOADING = false; if (cb) cb(); })   // плоская: months из meta + блок div (реальные дивиденды)
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
let SAW_DATA = null;
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

    <div class="saw-card phase-${esc(cp.risk_level)}">
      <div class="saw-phase-label">${esc(cp.label)}</div>
      <div class="saw-phase-grid">
        <div class="saw-metric"><span class="k">Последний экстремум</span><span class="v tnum">${esc(sawDate(cp.anchor_date))}</span><span class="k tnum">${ru(cp.anchor_price, 0)}</span></div>
        <div class="saw-metric"><span class="k">Текущая цена MCFTR</span><span class="v tnum">${ru(cp.current_price, 0)}</span><span class="k tnum">${esc(sawDate(cp.current_date))}</span></div>
        <div class="saw-metric"><span class="k">Движение от экстремума</span><span class="v tnum ${moveCls}">${sawPct(cp.move_pct)}</span></div>
        <div class="saw-metric"><span class="k saw-help" data-tooltip="${esc(TT_FREQ)}">Историческая частота ⓘ</span><span class="v tnum">${probStr}</span></div>
      </div>
    </div>

    ${sawGaugeHTML(d)}

    <div class="saw-interp">${esc(cp.explanation)}</div>

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
const HORIZON_RU = { short: 'Короткий (0–1 год)', mid: 'Средний (1–3 года)', long: 'Длинный (>3 лет)' };
const rub0 = (x) => isNum(x) ? Math.round(x).toLocaleString('ru-RU') + ' ₽' : ND;

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
  return `
    <div class="bonds-fresh muted">Обновлено: ${esc(upd)} · бумаг в скринере: <b>${d.bonds.length}</b> · источник: MOEX ISS</div>
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
    const g = RATING_GROUP(x.rating);
    const dev = isNum(x.deviation) ? (x.deviation >= 0 ? '+' : '') + x.deviation.toFixed(1) + '%' : ND;
    return `<tr>
      <td class="b-name">${esc(x.name)}</td>
      <td><span class="b-rating r-${g}">${esc(x.rating)}</span></td>
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
    <td><span class="b-rating r-${RATING_GROUP(b.rating)}">${esc(b.rating)}</span></td>
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
let MARLAMOV = null;
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
