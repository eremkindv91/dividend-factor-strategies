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

// Текст тултипа «Рейтинг» — ПЛЕЙСХОЛДЕР, меняй формулировку здесь:
const RATING_TOOLTIP = 'Итоговый рейтинг. По умолчанию таблица отсортирована по убыванию ожидаемой дивидендной доходности E[DY], скорректированной на фактор устойчивости.';

let DATA = null;
let VIEW = [];
let sortKey = 'stability_score';
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

// ── загрузка ──
fetch('data.json', { cache: 'no-store' })
  .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(init)
  .catch((e) => {
    document.getElementById('app').innerHTML =
      `<div class="error">Не удалось загрузить данные: ${esc(e.message)}</div>`;
  });

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
  document.getElementById('search').addEventListener('input', debounce(render, 130));
  document.getElementById('sector').addEventListener('change', render);
  document.getElementById('statusFilter').addEventListener('change', render);
  document.getElementById('csv').addEventListener('click', exportCSV);

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

function render() {
  VIEW = computeView();
  document.getElementById('count').textContent = `${VIEW.length} из ${DATA.tickers.length}`;
  renderRanks();
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

// ── мини-график (SVG-спарклайн) ──
function sparkline(vals, color) {
  const pts = vals.map((v, i) => [i, v]).filter((p) => p[1] != null);
  if (pts.length < 2) return '<span class="muted">—</span>';
  const w = 200, h = 38, ys = pts.map((p) => p[1]);
  const x0 = pts[0][0], x1 = pts[pts.length - 1][0];
  const y0 = Math.min(...ys, 0), y1 = Math.max(...ys, 0);     // включаем 0 (видно знак)
  const sx = (i) => (x1 === x0 ? 0 : (i - x0) / (x1 - x0)) * (w - 6) + 3;
  const sy = (v) => h - 3 - (y1 === y0 ? 0.5 : (v - y0) / (y1 - y0)) * (h - 6);
  const d = pts.map((p, k) => (k ? 'L' : 'M') + sx(p[0]).toFixed(1) + ' ' + sy(p[1]).toFixed(1)).join(' ');
  const last = pts[pts.length - 1];
  const zero = (y0 < 0 && y1 > 0) ? `<line x1="3" y1="${sy(0).toFixed(1)}" x2="${w - 3}" y2="${sy(0).toFixed(1)}" stroke="var(--line)" stroke-width="1"/>` : '';
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${zero}`
    + `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.7"/>`
    + `<circle cx="${sx(last[0]).toFixed(1)}" cy="${sy(last[1]).toFixed(1)}" r="2.4" fill="${color}"/></svg>`;
}

// ── динамика показателей (графики один-под-другим) ──
function historyHTML(h) {
  if (!h || !h.years || !h.years.length) return `<p class="muted">${ND}</p>`;
  const fields = [
    ['revenue_mln', 'Выручка', 'var(--accent-deep)', ' млн ₽'],
    ['net_profit_mln', 'Чистая прибыль', 'var(--good-ink)', ' млн ₽'],
    ['ebitda_mln', 'EBITDA', 'var(--accent)', ' млн ₽'],
    ['total_debt_mln', 'Долг', 'var(--risk-ink)', ' млн ₽'],
    ['roe_pct', 'ROE', 'var(--warn-ink)', '%'],
  ];
  const rows = fields.filter((f) => h[f[0]] && h[f[0]].some((v) => v != null)).map(([k, label, col, unit]) => {
    const last = [...h[k]].reverse().find((v) => v != null);
    return `<div class="hist-row"><span class="hist-lbl">${label}</span>${sparkline(h[k], col)}`
      + `<span class="hist-val tnum">${last != null ? ru(last, unit === '%' ? 1 : 0) + unit : '—'}</span></div>`;
  }).join('');
  const axis = `<div class="hist-row hist-axis"><span></span>`
    + `<div class="hist-yr">${h.years.map((y) => `<span>'${String(y).slice(2)}</span>`).join('')}</div>`
    + `<span></span></div>`;
  return `<div class="hist">${rows}${axis}</div>`;
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
    <div><h4>Динамика показателей</h4>${historyHTML(t.history)}</div>
    <div><h4>Ключевые факторы (SHAP)</h4>${shapHTML(t)}</div>
    <div><h4>Детали</h4>${detailKV(t)}</div>
  </div></td>`;
  tr.after(dr);
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
    box.innerHTML = valuationHTML(t.valuation) + historyHTML(t.history) + shapHTML(t) + detailKV(t);
    box.dataset.filled = '1';
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
