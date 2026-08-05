'use strict';

// ── форматирование (RU-локаль, десятичная запятая, ₽) ──
const ND = 'нет данных';
const isNum = (x) => typeof x === 'number' && isFinite(x);
const ru = (x, d = 2) => x.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (x, d = 1) => isNum(x) ? ru(x, d) + '%' : ND;
const fmtRub = (x) => isNum(x) ? ru(x, 2) + ' ₽' : ND;
const fmtScore = (x) => isNum(x) ? ru(x * 100, 1) + '%' : ND;   // 0..1 → %
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const instrumentTypeHint = (row) => {
  if (!row) return '';
  if (row.share_class === 'preferred' || row.instrument_type === 'equity_preferred') return 'preferred_equity';
  return row.instrument_type || row.type || '';
};
const instrumentAvatarHTML = (secid, name, type, size = 'sm', options = {}) => {
  if (!window.InstrumentIdentity) return `<span class="instrument-avatar-plain">${esc(String(secid || '?').slice(0, 2))}</span>`;
  return window.InstrumentIdentity.avatarHTML({ secid, name, type, size, ...options });
};
const instrumentIdentityHTML = (secid, name, type, size = 'sm', options = {}) => {
  if (!window.InstrumentIdentity) return `<span class="instrument-identity-plain"><b>${esc(name || secid || ND)}</b><span>${esc(secid || '')}</span></span>`;
  return window.InstrumentIdentity.identityHTML({ secid, name, type, size, ...options });
};
const mdash = '<span class="muted" title="нет данных">—</span>';
// склонение существительного при числе: 1 позиция / 2 позиции / 5 позиций
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
};
const cellNum = (x, fmt) => isNum(x) ? fmt(x) : mdash;   // «—» с тултипом вместо «нет данных»
const debounce = (fn, ms = 130) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const isoDayLag = (older, newer) => {
  const a = Date.parse(String(older || '').slice(0, 10) + 'T00:00:00Z');
  const b = Date.parse(String(newer || '').slice(0, 10) + 'T00:00:00Z');
  return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.round((b - a) / 86400000)) : null;
};
// хойст глобалов данных в топ: renderMyPortfolio/pfx* читают их, а wireMyPortfolio() вызывается
// top-level до их прежних объявлений ниже по файлу → без хойста был бы TDZ ('use strict')
let PF_RETURNS = null, SAW_DATA = null, MARKET_HISTORY = null, MARLAMOV = null, QUALITY = null, SITE_FINANCIALS = null, SITE_STATUS = null, NEWS = null, EVENTS_DATA = null, DIVIDEND_CALENDAR = null, ALFA_INDEX = null, ALFA_INDEX_HISTORY = null;
let ML_STRATEGY = null, ML_STRATEGY_LOADING = null, ACTIVE_STRATEGY_MODE = 'quality';
let IMOEX_SAW = null, MARKET_SAW_ACTIVE = 'MCFTR', MARKET_SAW_MANIFEST = null, IMOEX_LIVE_AT = 0;
let MARKET_PE = null;
let MARKET_PE_HIST = null, MPE_RANGE = '5y', MPE_METRIC = 'reported';   // история оценки рынка (site/market_pe_history.json)
let MACRO_CBR = null;      // макро ЦБ: ключевая ставка + инфляция (site/macro_cbr.json)
const STOCK_OHLC_CACHE = {};   // ticker|from → [[date,open,high,low,close,volume]...] (MOEX ISS, дневные)
let ALFA_INDEX_LOAD = null, ALFA_INDEX_CHART = null, ALFA_INDEX_RESIZE = null;
const PFX_DAILY = { index: null, bench: null, cache: {} };   // веб-мост дневного риска (ленивый, per-secid)

// ── авто-обновление lazy-loaded данных ──────────────────────────────────────
// Баг: каждый loadX(cb) вида `if (X) { cb(); return; }` кэширует ПЕРВЫЙ загруженный JSON
// на весь сеанс браузера — CI публикует новые данные дважды в будний день (marketsaw/bonds/
// cbr/marlamov/quality/financials/coverage/market_history/events/site_status), но открытая
// вкладка их никогда не увидит без полного refresh страницы. Раз в TTL, а также при возврате
// вкладки в видимость — обнуляем кэш-глобалы; существующие guard'ы (внутри loadX и на call
// site вида `if (!X && ...)`) сами честно перезапросят файл на следующий рендер секции; здесь
// же сразу принудительно перерисовываем ТЕКУЩУЮ открытую секцию, чтобы обновление было видно
// без дополнительного клика. `?t=Date.now()` в каждом fetch уже обходит HTTP-кэш браузера —
// это чинит именно JS-уровень «навсегда закешированного» объекта, отдельная проблема.
const DATA_CACHE_TTL_MS = 20 * 60 * 1000;   // 20 минут — не долбим CDN, не залипаем на весь день
let _dataCacheAt = Date.now();
function invalidateStaleDataCaches() {
  if (Date.now() - _dataCacheAt < DATA_CACHE_TTL_MS) return false;
  SAW_DATA = null; IMOEX_SAW = null; MARKET_SAW_MANIFEST = null; MARLAMOV = null; QUALITY = null; SITE_FINANCIALS = null; SITE_STATUS = null;
  ML_STRATEGY = null; ML_STRATEGY_LOADING = null;
  EVENTS_DATA = null; DIVIDEND_CALENDAR = null; MARKET_HISTORY = null; ALFA_INDEX = null; ALFA_INDEX_HISTORY = null; ALFA_INDEX_LOAD = null;
  if (typeof BONDS !== 'undefined') BONDS = null;
  if (typeof CBR_DATA !== 'undefined') CBR_DATA = null;
  if (typeof FINDER !== 'undefined') FINDER = null;
  if (typeof BVAL !== 'undefined') BVAL = null;
  if (typeof BHIST !== 'undefined') BHIST = null;
  if (typeof DATA_COVERAGE !== 'undefined') DATA_COVERAGE = null;
  _dataCacheAt = Date.now();
  return true;
}
function refreshVisibleSectionIfStale() {
  if (document.hidden) return;
  if (invalidateStaleDataCaches()) {
    if (typeof refreshCoreData === 'function') refreshCoreData();   // главный data.json (цены) — не lazy-глобал
    if (typeof onSectionShown === 'function' && typeof getSectionFromHash === 'function') {
      onSectionShown(getSectionFromHash());   // секция уже видима — перерисовать сразу, не ждать навигации
    }
  }
}
setInterval(refreshVisibleSectionIfStale, 60 * 1000);
document.addEventListener('visibilitychange', refreshVisibleSectionIfStale);

// Текст тултипа «Рейтинг» — меняй формулировку здесь:
const RATING_TOOLTIP = 'Основной рейтинг: надёжность дивиденда × оценка, со штрафом за долг и governance. Экстремальная доходность, payout выше 100%, старая цена и неполные данные автоматически исключаются до ручной проверки.';

let DATA = null;
let VIEW = [];        // результат computeView() — БАЗОВЫЙ срез (его читает регрессия, не менять контракт)
let SHOWN = [];       // VIEW после быстрых чип-фильтров — то, что отображается (редизайн, Итерация 5)
let sortKey = 'verdict_score';
let sortDir = -1; // -1 desc, 1 asc

// Быстрые фильтр-чипы (§13). Предикаты — только по существующим полям тикера, без новой математики.
// По умолчанию ни один не активен → SHOWN === VIEW → baseline топ-20 совпадает.
const STOCK_CHIPS = [
  { id: 'top', label: 'Высокий рейтинг', test: (t) => t.verdict && t.verdict.color === 'good' },
  { id: 'reliable', label: 'Надёжный дивиденд', test: (t) => isNum(t.stability_score) && t.stability_score >= 0.6 },
  { id: 'undervalued', label: 'Недооценённые', test: (t) => t.verdict && isNum(t.verdict.v) && t.verdict.v >= 5 },
  { id: 'yield', label: 'Высокая дивдоходность', test: (t) => isNum(t.dividend_yield_expected) && t.dividend_yield_expected >= 10 },
  { id: 'lowrisk', label: 'Низкий риск невыплаты', test: (t) => isNum(t.cut_risk) && t.cut_risk <= 0.25 },
  { id: 'fulldata', label: 'Полные данные', test: (t) => stockRankingEligible(t) },
];
const activeStockChips = new Set();
function applyStockChips(rows) {
  if (!activeStockChips.size) return rows;
  const tests = STOCK_CHIPS.filter((c) => activeStockChips.has(c.id)).map((c) => c.test);
  return rows.filter((t) => tests.every((fn) => fn(t)));
}
let STOCK_VIEW_MODE = '';   // '' = адаптив (desktop таблица / mobile карточки); 'table'|'cards'|'map' — явный выбор

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

const STOCK_REVIEW_FLAGS = new Set([
  'y_paid_invalid', 'y_exp_invalid', 'y_paid_high', 'y_exp_high',
  'payout_negative', 'payout_high', 'price_stale',
]);
const STOCK_REVIEW_LABELS = {
  y_paid_invalid: 'условная доходность вне диапазона',
  y_exp_invalid: 'ожидаемая доходность вне диапазона',
  y_paid_high: 'условная доходность выше 30%',
  y_exp_high: 'ожидаемая доходность выше 30%',
  payout_negative: 'выплата при убытке',
  payout_high: 'payout выше 100%',
  price_stale: 'цена из кэша',
};
function stockReviewReasons(t) {
  const reasons = new Set(Array.isArray(t.ranking_review_reasons) ? t.ranking_review_reasons : []);
  (t.flags || []).forEach((flag) => { if (STOCK_REVIEW_FLAGS.has(flag)) reasons.add(flag); });
  if (isNum(t.payout) && t.payout > 100) reasons.add('payout_high'); // совместимость со старым data.json
  return [...reasons];
}
function stockRankingEligible(t) {
  if (typeof t.ranking_eligible === 'boolean') return t.ranking_eligible;
  return t.status === 'ok' && stockReviewReasons(t).length === 0;
}
function stockRankingStatus(t) {
  if (t.status !== 'ok') return 'insufficient';
  return stockRankingEligible(t) ? 'eligible' : 'review';
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

// ── кэш-манифест данных (редизайн, Итерация 3, §6.1) ──
// build.json пишет CI при каждой публикации: { "version": "<sha|ts>", ... }. Статичные JSON
// грузятся как dataURL(path)=path?v=<version> → стабильный URL в пределах сборки кэшируется
// браузером (быстрый повторный визит), новая публикация меняет version → кэш инвалидируется.
// Живые котировки MOEX ISS (индекс/свечи) остаются no-store — им нужна максимальная свежесть.
const BUILD = { version: '' };
function dataURL(path) {
  if (!BUILD.version) return path;   // до загрузки манифеста / локально — ETag-ревалидация
  return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'v=' + encodeURIComponent(BUILD.version);
}
function loadBuildManifest() {
  return fetch('build.json', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => { if (j && j.version != null) BUILD.version = String(j.version); })
    .catch(() => { /* build.json может отсутствовать (локально / до первого деплоя) */ });
}
loadBuildManifest();

// ── версионируемое хранилище UI-настроек (редизайн, Итерация 3, §6.6) ──
// Единый ключ dfs.ui.v1 с полем версии → безопасная миграция при смене схемы.
// Портфель (dividendFactorStrategies.myPortfolio.v1) и фильтры календаря НЕ трогаем —
// у них своя схема и свой контракт; читаются как прежде.
const UI_STATE_KEY = 'dfs.ui.v1';
const UI_STATE_DEFAULT = { v: 1, sidebar: '', taxRate: 0.13 };
function uiStateLoad() {
  try {
    const raw = localStorage.getItem(UI_STATE_KEY);
    if (raw) { const o = JSON.parse(raw); if (o && o.v === 1) return { ...UI_STATE_DEFAULT, ...o }; }
    // миграция плоского ключа dfs.ui.sidebar из Итерации 2
    const legacy = localStorage.getItem('dfs.ui.sidebar');
    return { ...UI_STATE_DEFAULT, sidebar: legacy || '' };
  } catch (_e) { return { ...UI_STATE_DEFAULT }; }
}
function uiStateSave(patch) {
  try {
    const next = { ...uiStateLoad(), ...patch, v: 1 };
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(next));
    localStorage.removeItem('dfs.ui.sidebar');   // подчистить legacy после первой записи
    return next;
  } catch (_e) { return null; }
}

// ── загрузка ──
fetch(dataURL('data.json'))
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

function renderDateChips(m) {
  const el = document.getElementById('dates');
  if (!el || !m) return;
  const forecastLag = isoDayLag(m.forecast_asof, m.price_asof);
  const lagChip = forecastLag > 0
    ? `<span class="date-chip forecast-lag" data-tooltip="Прогноз модели пересчитывается реже цен MOEX"><span class="lbl">Модельный срез:</span> <b>${forecastLag} дн. до цены</b></span>`
    : '';
  el.innerHTML =
    `<span class="date-chip"><span class="lbl">Прогноз модели:</span> <b>${esc(m.forecast_asof || '—')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Цены:</span> <b>${esc(m.price_asof || '—')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Горизонт:</span> <b>дивиденды ${esc(m.forecast_year || '')}</b></span>`
    + `<span class="date-chip"><span class="lbl">Эмитентов:</span> <b>${m.n_total}</b></span>`
    + lagChip;
}

// Переподгрузка главного data.json (цены/прогнозы) в ОТКРЫТОЙ вкладке: init() грузит его
// один раз на старте и делает разовую проводку слушателей (повторный init() — двойные
// listener'ы), поэтому здесь ТОЛЬКО обновляем DATA и перерисовываем DATA-зависимые вью,
// без ре-wiring. Дополняет invalidateStaleDataCaches() (тот чинил lazy-глобалы, но не DATA).
function refreshCoreData(cb) {
  fetch(dataURL('data.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (j && Array.isArray(j.tickers) && j.meta) {
        DATA = j;
        renderDateChips(j.meta);
        if (typeof render === 'function') render();
        if (typeof updateDataStatus === 'function') updateDataStatus();
        if (typeof renderMarketKPI === 'function') renderMarketKPI();
        if (typeof renderMarketSignals === 'function') renderMarketSignals();
      }
      cb && cb();
    })
    .catch((e) => { console.warn('[core-data] refresh failed:', e); cb && cb(e); });
}

function init(data) {
  DATA = data;
  const m = data.meta;

  applyTaxRate(uiStateLoad().taxRate);   // §5.1 — применить сохранённую ставку НДФЛ до рендеров (дефолт 13% → NET_OF_TAX=0.87)
  renderDateChips(m);   // даты (Прогноз/Цены/Горизонт/Эмитентов/лаг модельного среза)

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
  document.getElementById('search').addEventListener('input', debounce(render, 250));   // §13: debounce 250мс
  document.getElementById('sector').addEventListener('change', render);
  document.getElementById('statusFilter').addEventListener('change', render);
  document.getElementById('csv').addEventListener('click', exportCSV);
  // §13: быстрые фильтр-чипы (тоггл активности → пересчёт SHOWN)
  const chipsEl = document.getElementById('stock-chips');
  if (chipsEl) chipsEl.addEventListener('click', (e) => {
    const b = e.target.closest('[data-chip]'); if (!b) return;
    if (b.dataset.chip === '__clear') activeStockChips.clear();
    else if (activeStockChips.has(b.dataset.chip)) activeStockChips.delete(b.dataset.chip);
    else activeStockChips.add(b.dataset.chip);
    render();
  });
  // §13: переключатель вида Таблица/Карточки/Карта
  const vt = document.getElementById('stock-view-toggle');
  if (vt) vt.addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]'); if (!b) return;
    STOCK_VIEW_MODE = b.dataset.view;
    uiStateSave({ stockView: STOCK_VIEW_MODE });
    applyStockViewMode();
    if (STOCK_VIEW_MODE === 'map') renderMap();
  });
  STOCK_VIEW_MODE = uiStateLoad().stockView || '';
  applyStockViewMode();
  wirePortfolio();
  wireQuality();
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
    if (st === 'rankable' && !stockRankingEligible(t)) return false;
    if (st === 'review' && stockRankingStatus(t) !== 'review') return false;
    if (st === 'insufficient_data' && t.status !== 'insufficient_data') return false;
    if (q && !(t.ticker.toLowerCase().includes(q) || String(t.name).toLowerCase().includes(q))) return false;
    return true;
  });
  rows.sort((a, b) => {
    const ae = stockRankingEligible(a), be = stockRankingEligible(b);
    if (ae !== be) return ae ? -1 : 1; // review-строки не поднимаются наверх даже в режиме «все»
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

// ── быстрые фильтр-чипы (§13) — тоггл активности, пересчёт SHOWN ──
function renderStockChips() {
  const el = document.getElementById('stock-chips');
  if (!el) return;
  el.innerHTML = STOCK_CHIPS.map((c) =>
    `<button type="button" class="stock-chip${activeStockChips.has(c.id) ? ' active' : ''}" data-chip="${c.id}" aria-pressed="${activeStockChips.has(c.id) ? 'true' : 'false'}">${esc(c.label)}</button>`
  ).join('') + (activeStockChips.size ? `<button type="button" class="stock-chip stock-chip-clear" data-chip="__clear">Сбросить</button>` : '');
}

// ── переключатель вида Таблица / Карточки / Карта (§13) ──
function applyStockViewMode() {
  const sec = document.querySelector('.app-section[data-section="stocks"]');
  if (sec) { if (STOCK_VIEW_MODE) sec.setAttribute('data-stock-view', STOCK_VIEW_MODE); else sec.removeAttribute('data-stock-view'); }
  document.querySelectorAll('#stock-view-toggle .stock-view-btn').forEach((b) => {
    const on = b.dataset.view === (STOCK_VIEW_MODE || 'table');
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const mw = document.getElementById('mapwrap');
  if (mw && STOCK_VIEW_MODE === 'map' && !mw.open) mw.open = true;
}

// ── Карта рынка: scatter Надёжность(Y) × Оценка(X), цвет = вердикт ──
function renderMap() {
  const el = document.getElementById('map');
  if (!el) return;
  const pts = SHOWN.filter((t) => t.verdict && t.verdict.v != null && isNum(t.stability_score));
  const na = SHOWN.filter((t) => t.verdict && t.verdict.v == null).length;
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
  VIEW = computeView();            // базовый срез (контракт регрессии) — не фильтруем чипами
  SHOWN = applyStockChips(VIEW);   // отображаемый срез (быстрые чипы)
  document.getElementById('count').textContent = `${SHOWN.length} из ${DATA.tickers.length}`;
  renderStockChips();
  const gate = document.getElementById('stock-quality-gate');
  if (gate) {
    const eligible = DATA.tickers.filter(stockRankingEligible).length;
    const review = DATA.tickers.filter((t) => stockRankingStatus(t) === 'review').length;
    gate.innerHTML = `<b>Основной рейтинг: ${eligible}</b><span>На проверке: ${review}. Экстремальная доходность, payout &gt;100% и старая цена не ранжируются до проверки источника.</span>`;
  }
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

  const body = SHOWN.length ? SHOWN.map((t, i) => {
    const payoutTxt = isNum(t.payout)
      ? `${ru(t.payout, 1)}%${t.payout_year ? ` <span class="muted">(${t.payout_year})</span>` : ''}`
      : mdash;
    const statusChip = statusChipHTML(t);
    return `<tr class="data-row" data-i="${i}">
      <td class="left"><div class="instrument-ranked"><span class="rank">${i + 1}</span>${instrumentIdentityHTML(t.ticker, t.name, instrumentTypeHint(t), 'sm')}</div></td>
      <td class="left">${verdictChip(t.verdict, false)}</td>
      <td>${stabilityCell(t.stability_score)}</td>
      <td>${riskBadge(t.cut_risk)}</td>
      <td class="tnum">${cellNum(t.dividend_forecast, fmtRub)}${announcedBadgeHTML(t) ? '<br>' + announcedBadgeHTML(t) : ''}</td>
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
    tr.addEventListener('click', () => toggleDetail(tr, SHOWN[+tr.dataset.i])));
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

// ── объявленный дивиденд против прогноза модели ──────────────────────────────
// Прогноз пересчитывается раз в квартал (по годовой отчётности), а решения советов
// директоров выходят непрерывно. Без этой связки скринер показывал цифру, уже
// опровергнутую официальным решением: AKRN — модель 430,23 ₽ против объявленных
// 235,00 ₽. Объявленное значение ВЕДЁТ, прогноз остаётся рядом как модельная оценка.
// Уровень доверия подписываем честно: анонс брокера ≠ подтверждение Мосбиржи.
function announcedNote(a) {
  if (!a || !isNum(a.value)) return null;
  const confirmed = a.confirmed_by_moex === true;
  return {
    value: a.value,
    label: confirmed ? 'Объявлен' : 'Объявлен (анонс)',
    title: confirmed
      ? `Подтверждено раскрытием Мосбиржи. Отсечка ${a.record_date || '—'}.`
      : `Данные брокера, Мосбиржей пока НЕ подтверждено. Отсечка ${a.record_date || '—'}.`,
    tone: confirmed ? 'good' : 'neut',
    recordDate: a.record_date || null,
    yieldPct: isNum(a.yield_pct) ? a.yield_pct : null,
    divergence: isNum(a.divergence_pct) ? a.divergence_pct : null,
    outsideBand: a.outside_model_band === true,
  };
}

function announcedBadgeHTML(t) {
  const a = announcedNote(t && t.announced_dividend);
  if (!a) return '';
  const warn = a.outsideBand
    ? ` <span class="ann-warn" title="Объявленная сумма вне интервала прогноза — модельная оценка по этой бумаге устарела">прогноз разошёлся</span>`
    : '';
  return `<span class="ann-chip ann-${a.tone}" title="${esc(a.title)}">${esc(a.label)} ${fmtRub(a.value)}</span>${warn}`;
}

function detailKV(t) {
  const lohi = (isNum(t.dividend_forecast_lo) && isNum(t.dividend_forecast_hi))
    ? `${ru(t.dividend_forecast_lo, 1)}–${ru(t.dividend_forecast_hi, 1)} ₽` : ND;
  const ann = announcedNote(t.announced_dividend);
  const annRows = ann ? `
    <dt>${esc(ann.label)} дивиденд</dt><dd class="tnum"><b>${fmtRub(ann.value)}</b>${
      ann.yieldPct != null ? ` <span class="muted">· ${ru(ann.yieldPct, 2)}% к цене</span>` : ''}</dd>
    <dt>Отсечка</dt><dd class="tnum">${ann.recordDate ? esc(ann.recordDate) : ND}<span class="muted"> · ${esc(ann.title)}</span></dd>` : '';
  const flagMap = {
    y_paid_invalid: 'доходность при выплате вне диапазона — скрыта',
    y_exp_invalid: 'ожидаемая доходность вне диапазона — скрыта',
    y_paid_high: 'высокая доходность при выплате (>30%)',
    y_exp_high: 'высокая ожидаемая доходность (>30%)',
    payout_negative: 'payout отрицательный (убыток при выплате)',
    payout_high: 'payout выше 100% — требуется проверка источника',
    price_stale: 'цена не обновлена (кэш)',
    no_price: 'нет рыночной цены',
    no_forecast: 'нет прогноза модели',
    dps_unreliable: 'прогноз дивиденда скрыт как ненадёжный',
  };
  const flags = (t.flags || []).map((f) => flagMap[f] || f);
  return `<dl class="kv">
    <dt>Текущая цена</dt><dd class="tnum">${fmtRub(t.price)}${t.price_field ? ` <span class="muted">(${esc(t.price_field)})</span>` : ''}</dd>${annRows}
    <dt>Прогноз дивиденда${ann ? ' <span class="muted">(модель)</span>' : ''}</dt><dd class="tnum">${fmtRub(t.dividend_forecast)}${
      ann && ann.divergence != null ? ` <span class="muted">· ${ann.divergence > 0 ? '+' : ''}${ru(ann.divergence, 1)}% к объявленному</span>` : ''}</dd>
    <dt>Интервал прогноза</dt><dd class="tnum">${lohi}</dd>
    <dt>Дивиденд за посл. год</dt><dd class="tnum">${fmtRub(t.current_dps)}</dd>
    <dt>Серия лет выплат</dt><dd class="tnum">${t.div_streak ?? ND}</dd>
    <dt>Payout (факт)</dt><dd class="tnum">${isNum(t.payout) ? ru(t.payout,1)+'%'+(t.payout_year?` (${t.payout_year})`:'')+(t.payout_source?` <span class="muted">· эмитент</span>`:'') : ND}</dd>
  </dl>`
    + (t.forecast_note ? `<div class="flagline">ℹ ${esc(t.forecast_note)}</div>` : '')
    + (flags.length ? `<div class="flagline">⚠ ${flags.map(esc).join('; ')}</div>` : '');
}

function statusChipHTML(t) {
  const status = stockRankingStatus(t);
  if (status === 'eligible') return '<span class="status-chip s-ok">✓ основной рейтинг</span>';
  if (status === 'review') {
    const tip = stockReviewReasons(t).map((reason) => STOCK_REVIEW_LABELS[reason] || reason).join('; ');
    return `<span class="status-chip s-review" data-tooltip="${esc(tip)}">⚠ проверка</span>`;
  }
  return '<span class="status-chip s-insuf">неполные</span>';
}

function stockDetailSummaryHTML(t) {
  return `<div class="issuer-summary">
    <div class="issuer-title">
      ${instrumentAvatarHTML(t.ticker, t.name, instrumentTypeHint(t), 'lg')}
      <div>
        <b>${esc(t.name)}</b>
        <span>${esc(t.ticker)} · ${esc(t.sector || ND)}</span>
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
      <div><span>Прогноз дивиденда</span><b class="tnum">${cellNum(t.dividend_forecast, fmtRub)}</b>${announcedBadgeHTML(t)}</div>
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
    ${stockPriceChartHTML(t)}
    <div class="detail-card"><h4>Оценка стоимости</h4>${valuationHTML(t.valuation)}</div>
    <div class="detail-card"><h4>Дивидендные метрики</h4>${dividendMetricsHTML(t)}</div>
    <div class="detail-card"><h4>Позиция в секторе</h4>${sectorPercentilesHTML(t)}</div>
    <div class="detail-card"><h4>Фундаментальные показатели</h4>${fundamentalsOrHistoryHTML(t)}</div>
    <div class="detail-card"><h4>Ключевые факторы (SHAP)</h4>${shapHTML(t)}</div>
    <div class="detail-card"><h4>Детали и флаги</h4>${detailKV(t)}</div>
  </div></td>`;
  tr.after(dr);
  wireCharts(dr);
  wireStockChart(dr, t.ticker);
}

function renderCards() {
  const el = document.getElementById('cards');
  if (!el) return;
  el.innerHTML = SHOWN.length ? SHOWN.map((t, i) => {
    const statusChip = statusChipHTML(t);
    return `<div class="card">
      <div class="top"><div class="instrument-ranked"><span class="rank">${i + 1}</span>${instrumentIdentityHTML(t.ticker, t.name, instrumentTypeHint(t), 'md')}</div>${riskBadge(t.cut_risk)}</div>
      <div class="nm card-sector">${esc(t.sector)}</div>
      <div class="card-verdict">${verdictChip(t.verdict, true)}</div>
      <div class="grid">
        <div><span class="lbl">Устойчивость</span>${stabilityCell(t.stability_score)}</div>
        <div><span class="lbl">Прогноз дивиденда</span><span class="tnum">${cellNum(t.dividend_forecast, fmtRub)}</span>${announcedBadgeHTML(t)}</div>
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
    const t = SHOWN[+this.dataset.i];
    box.innerHTML = stockDetailSummaryHTML(t) + stockPriceChartHTML(t) + dividendMetricsHTML(t) + valuationHTML(t.valuation) + sectorPercentilesHTML(t) + fundamentalsOrHistoryHTML(t) + shapHTML(t) + detailKV(t);
    box.dataset.filled = '1';
    wireCharts(box);
    wireStockChart(box, t.ticker);
  }));
}

// ── экспорт CSV (RU Excel: ; разделитель, запятая-десятичная, BOM) ──
function exportCSV() {
  const cols = [
    ['ticker', 'Тикер'], ['name', 'Название'], ['sector', 'Отрасль'],
    ['stability_score', 'Устойчивость'], ['cut_risk', 'Риск невыплаты'],
    ['dividend_forecast', 'Прогноз дивиденда (модель), ₽'],
    ['announced_dividend_value', 'Объявленный дивиденд, ₽'],
    ['announced_dividend_record_date', 'Отсечка'],
    ['announced_dividend_confirmed', 'Подтверждён Мосбиржей'],
    ['payout', 'Payout, %'],
    ['dividend_yield_expected', 'Доходность ожидаемая, %'],
    ['dividend_yield_if_paid', 'Доходность при выплате, %'],
    ['price', 'Цена, ₽'], ['status', 'Полнота данных'], ['ranking_status', 'Статус рейтинга'],
    ['ranking_review_reasons', 'Причины проверки'],
  ];
  const cell = (v) => {
    if (typeof v === 'number') return ru(v, 4).replace(/ /g, '');
    return '"' + String(v).replace(/"/g, '""') + '"';
  };
  // announced_dividend приходит вложенным объектом — раскладываем в плоские колонки,
  // иначе в CSV попало бы "[object Object]"
  const pick = (t, key) => {
    if (!key.startsWith('announced_dividend_')) return t[key];
    const a = t.announced_dividend;
    if (!a) return ND;
    if (key === 'announced_dividend_value') return isNum(a.value) ? a.value : ND;
    if (key === 'announced_dividend_record_date') return a.record_date || ND;
    if (key === 'announced_dividend_confirmed') return a.confirmed_by_moex ? 'да' : 'нет (анонс брокера)';
    return ND;
  };
  const lines = [cols.map((c) => c[1]).join(';')];
  SHOWN.forEach((t) => lines.push(cols.map((c) => cell(pick(t, c[0]))).join(';')));
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dividend_forecast_rf.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ══════════ Конструктор портфеля ══════════
// ── налоговый профиль (редизайн, Итерация 3, §5.1) ──
// Ставка НДФЛ на дивиденды/доход конфигурируема. Фактор «на руки» берётся из таблицы
// ТОЧНЫХ литералов (не 1−rate — это дало бы float-дрейф и расхождение регрессии).
// Дефолт 13% → NET_OF_TAX ровно 0.87 → расчётный слой идентичен baseline.
// Влияет ТОЛЬКО на клиентский дивидендный net (акции/портфель/X-Ray). Купонный YTM-net
// облигаций и форвардная доходность считаются сервером при 13% и помечены как фикс.
const TAX_OPTIONS = [
  { rate: 0.13, factor: 0.87, label: '13%', hint: 'Стандартная ставка НДФЛ для резидента' },
  { rate: 0.15, factor: 0.85, label: '15%', hint: 'Повышенная ставка (доход свыше порога прогрессии)' },
  { rate: 0, factor: 1, label: '0%', hint: 'ЛДВ (>3 лет) или ИИС — налог не удерживается' },
];
function taxOption() { const r = uiStateLoad().taxRate; return TAX_OPTIONS.find((o) => o.rate === r) || TAX_OPTIONS[0]; }
function taxPct() { return Math.round(taxOption().rate * 100); }
let NET_OF_TAX = 0.87;                // ×(1−НДФЛ) — доходность «на руки»; переопределяется applyTaxRate()
function applyTaxRate(rate) {
  const opt = TAX_OPTIONS.find((o) => o.rate === rate) || TAX_OPTIONS[0];
  NET_OF_TAX = opt.factor;
  try { if (typeof PFX === 'object' && PFX) PFX.TAX = opt.factor; } catch (_e) { /* PFX ещё в TDZ на самом раннем вызове */ }
  uiStateSave({ taxRate: opt.rate });
}
function renderTaxControl() {
  const box = document.getElementById('tax-profile-opts');
  if (!box) return;
  const cur = taxOption().rate;
  box.innerHTML = TAX_OPTIONS.map((o) =>
    `<button type="button" class="tax-opt${o.rate === cur ? ' active' : ''}" data-rate="${o.rate}" aria-pressed="${o.rate === cur}" data-tooltip="${esc(o.hint)}">${esc(o.label)}</button>`
  ).join('');
}
function onTaxChange(rate) {
  applyTaxRate(rate);
  renderTaxControl();
  if (typeof render === 'function') render();                       // таблица акций (net-доходность)
  if (typeof renderPortfolio === 'function') renderPortfolio();     // конструктор портфеля
  if (typeof renderMyPortfolio === 'function') renderMyPortfolio(); // Portfolio X-Ray (NET_OF_TAX + PFX.TAX)
}
const PF_MIN_MCAP = 5000;            // млн ₽ (5 млрд): лёгкий liquidity-floor
const PF_MIN_ADV = 10e6;             // ₽/день: ADV-фильтр — отсечь нетендерные (стоячие цены → ложный low-vol в оптимизаторе)
const FACTOR_BACKTEST = {            // статы из ВКР-бэктеста (results/), как пруф доверия
  quality: { label: 'RU Quality · полный point-in-time backtest пока не рассчитан; ниже показаны характеристики текущего набора бумаг, а не историческая доходность стратегии' },
  momentum: { label: 'Momentum (WML 12-1, ТОЛЬКО ЛОНГ top-N) · бэктест ВКР 2012–2025: +2,2%/год избыточной доходности над рынком (t≈0,3 — статистически незначима); единственный фактор, исторически работавший на РФ, но слабо. Ребаланс месячный.' },
  marlamov: { label: 'Дивидендная переоценка · модельный состав по ожидаемой чистой дивдоходности к сопоставимой RFR; Div2 показан только как независимый сценарий' },
  optmv: { label: 'Робастная оптимизация: минимум дисперсии портфеля по ковариации ВСЕХ бумаг (усадка ковариации к диагонали + box-ограничения) — портфельная теория, не факторный бэктест' },
  optrp: { label: 'Risk-parity: равный риск-вклад каждой бумаги (ковариация всех бумаг с усадкой) — не факторный бэктест' },
  optiv: { label: 'Inverse-volatility: вес ∝ 1/волатильность — простая робастная диверсификация' },
  optms: { label: 'Макс-Шарп (tangency, w∝Σ⁻¹μ): связывает независимый RU Quality score и риск из ковариации. Это оптимизатор текущего universe, не исторический факторный backtest' },
};
const REBALANCE = {            // рекомендуемая частота ребаланса по стратегии
  quality: 'годовой (после годовых отчётов, как в ВКР — май)',
  momentum: 'месячный (фактор быстро затухает)',
  marlamov: 'ежемесячная проверка сигнала; research backtest ребалансируется ежегодно в мае',
  optmv: 'квартальный (ковариация медленная)', optms: 'квартальный (ковариация медленная)',
  optrp: 'квартальный (ковариация медленная)', optiv: 'квартальный (ковариация медленная)',
};

// RU Quality Core приходит готовой cross-section из quality.json/build_quality.py.
function qualityScore(t) {
  return isNum(t.quality_rank_pct) ? t.quality_rank_pct / 100 : null;
}

let QUALITY_LOADING = false;
let QUALITY_LAST_ANALYSIS = null;
function loadQuality(cb) {
  if (QUALITY) { if (cb) cb(null, QUALITY); return; }
  if (QUALITY_LOADING) return;
  QUALITY_LOADING = true;
  fetch(dataURL('quality.json'))
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
    .then((payload) => { QUALITY = payload; QUALITY_LOADING = false; renderQualityPanel(); if (cb) cb(null, payload); })
    .catch((error) => {
      QUALITY_LOADING = false;
      const notice = document.getElementById('quality-notice');
      if (notice) { notice.hidden = false; notice.textContent = 'RU Quality временно недоступен: ' + error.message; }
      if (cb) cb(error);
    });
}

function qualityMethodSelected() {
  const select = document.getElementById('pf-method');
  return select && select.value === 'quality';
}

function syncStrategyPanels() {
  const method = (document.getElementById('pf-method') || {}).value || 'quality';
  ACTIVE_STRATEGY_MODE = method;
  const pf = document.getElementById('pf');
  const marlamov = document.getElementById('marlamov');
  const momentum = document.getElementById('momentum-panel');
  const mlPanel = document.getElementById('ml-strategy-panel');
  if (pf) pf.hidden = method === 'momentum';
  if (marlamov) marlamov.hidden = method !== 'marlamov';
  if (momentum) momentum.hidden = method !== 'momentum';
  if (mlPanel) mlPanel.hidden = true;
  const qualityPanel = document.getElementById('quality-panel');
  if (qualityPanel) qualityPanel.hidden = method !== 'quality';
  document.querySelectorAll('.strategy-mode').forEach((button) => {
    const value = button.dataset.strategy || '';
    const active = value === method || (value === 'optmv' && method.startsWith('opt'));
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  if (method === 'quality' && !QUALITY) loadQuality();
  if (method === 'momentum') renderMomentumStrategy();
}

function qualityStatusLabel(status) {
  return ({ eligible: 'Eligible', low_confidence_review: 'Нужна проверка', excluded: 'Исключена',
    sector_specific_model_required: 'Нужна секторная модель' })[status] || status || '—';
}

function qualityFactorDefinitions(row) {
  if (Array.isArray(row.factor_definitions) && row.factor_definitions.length) return row.factor_definitions;
  return [
    { key: 'roe', label: 'ROE', format: 'percent' },
    { key: 'debt_to_equity', label: 'Debt/Equity ↓', format: 'multiple' },
    { key: 'earnings_variability', label: 'Изменчивость EPS ↓', format: 'percent' },
  ];
}

function qualityFmtRaw(key, value, format) {
  if (!isNum(value)) return '—';
  if (format === 'percent' || ['roe', 'earnings_variability', 'bank_roe', 'ebitda_margin', 'fcf_margin'].includes(key)) return ru(value * 100, 1) + '%';
  if (format === 'percentage_points' || key === 'capital_headroom') return `${value >= 0 ? '+' : ''}${ru(value * 100, 1)} п.п.`;
  return ru(value, 2) + '×';
}

function qualityFactorChips(row) {
  const raw = row.raw || {};
  const periods = ((row.diagnostics || {}).factor_periods) || {};
  return qualityFactorDefinitions(row).map((factor) => {
    const period = periods[factor.key];
    const vintage = period ? ` · ${String(period).slice(0, 4)}` : '';
    return `<span class="quality-factor-chip"${period ? ` title="Период фактора: ${esc(period)}"` : ''}><small>${esc(factor.label || factor.key)}${esc(vintage)}</small><b>${qualityFmtRaw(factor.key, raw[factor.key], factor.format)}</b></span>`;
  }).join('');
}

function renderQualityPanel() {
  if (!QUALITY || !Array.isArray(QUALITY.rows)) return;
  const meta = QUALITY.meta || {}, dq = QUALITY.data_quality || {}, models = meta.models || dq.models || {};
  const kpis = document.getElementById('quality-kpis');
  if (kpis) {
    const cell = (label, value) => `<div class="quality-kpi"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
    kpis.innerHTML = cell('Эмитентов', String(meta.n_issuers || meta.n_universe || 0))
      + cell('Получили score', String(meta.n_scored_issuers || meta.n_scored || 0))
      + cell('Доступны для live', String(meta.n_investable_scored_issuers || meta.n_investable_scored || 0))
      + cell('Verified / PIT', String(meta.n_eligible_issuers || meta.n_eligible || 0))
      + cell('Среднее покрытие', isNum(meta.issuer_data_coverage) ? ru(meta.issuer_data_coverage * 100, 0) + '%' : isNum(meta.data_coverage) ? ru(meta.data_coverage * 100, 0) + '%' : '—')
      + cell('Банки · ЦБ', String(((models.bank_quality || {}).n_investable_scored_issuers) || 0))
      + cell('IT-модель', String(((models.it_quality || {}).n_investable_scored_issuers) || 0))
      + cell('Fundamentals', meta.as_of_date || '—');
  }
  const notice = document.getElementById('quality-notice');
  if (notice) {
    const hasUnknownDates = (meta.warnings || []).some((warning) => String(warning).includes('publication_date'));
    notice.hidden = !hasUnknownDates;
    notice.textContent = hasUnknownDates
      ? 'RU Quality использует три отраслевые модели: корпоративную, банковскую по формам ЦБ и IT-модель. Полной истории дат публикации пока нет, поэтому автоматическое включение в PIT-корзину отключено.'
      : '';
  }
  const confidence = dq.confidence || meta.confidence || {};
  const strip = document.getElementById('quality-data-strip');
  if (strip) strip.innerHTML = `<span>Confidence: <b>${confidence.high || 0} high</b> · <b>${confidence.medium || 0} medium</b> · <b>${confidence.low || 0} low</b></span>
    <span>Модели: <b>корпоративная ${(models.industrial_core || {}).n_scored_issuers || 0}</b> · <b>банки ${(models.bank_quality || {}).n_scored_issuers || 0}</b> · <b>IT ${(models.it_quality || {}).n_scored_issuers || 0}</b></span>
    <span>Самый старый период: <b>${esc(dq.oldest_report_period_end || '—')}</b></span>`;
  const sectorSelect = document.getElementById('quality-sector');
  if (sectorSelect && sectorSelect.options.length === 1) {
    [...new Set(QUALITY.rows.map((row) => row.sector).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ru'))
      .forEach((sector) => { const option = document.createElement('option'); option.value = sector; option.textContent = sector; sectorSelect.appendChild(option); });
  }
  renderQualityFilterStatus();
  renderQualityTable();
}

function renderQualityTable() {
  const target = document.getElementById('quality-table');
  if (!target || !QUALITY || !Array.isArray(QUALITY.rows)) return;
  const model = (document.getElementById('quality-model') || {}).value || '';
  const sector = (document.getElementById('quality-sector') || {}).value || '';
  const status = (document.getElementById('quality-status') || {}).value || '';
  const sortKey = (document.getElementById('quality-sort') || {}).value || 'sector_rank_pct';
  const rows = QUALITY.rows.filter((row) => (!model || row.quality_model === model) && (!sector || row.sector === sector) && (!status || row.status === status))
    .sort((a, b) => (isNum(b[sortKey]) ? b[sortKey] : -Infinity) - (isNum(a[sortKey]) ? a[sortKey] : -Infinity)
      || String(a.ticker).localeCompare(String(b.ticker)));
  if (!rows.length) { target.innerHTML = '<div class="quality-drawer-note">По выбранным фильтрам компаний нет.</div>'; return; }
  const body = rows.map((row, index) => {
    return `<tr data-quality-ticker="${esc(row.ticker)}" tabindex="0">
      <td>${index + 1}</td><td class="left"><b>${esc(row.ticker)}</b></td><td class="left">${esc(row.name || row.ticker)}</td>
      <td class="left">${esc(row.sector || '—')}</td>
      <td class="left"><span class="quality-model-chip ${esc(row.quality_model || 'industrial_core')}">${esc(row.quality_model_label || 'Корпоративная Quality')}</span></td>
      <td class="quality-score">${isNum(row.sector_rank_pct) ? ru(row.sector_rank_pct, 0) : '—'}</td>
      <td>${isNum(row.quality_rank_pct) ? ru(row.quality_rank_pct, 0) : '—'}</td>
      <td class="left"><div class="quality-factor-chips">${qualityFactorChips(row)}</div></td>
      <td>${isNum(row.coverage_ratio) ? ru(row.coverage_ratio * 100, 0) + '%' : '—'}</td>
      <td>${esc(row.report_period_end || '—')}</td>
      <td><span class="quality-confidence ${esc(row.confidence || 'low')}">${esc(row.confidence || 'low')}</span></td>
      <td>${esc(qualityStatusLabel(row.status))}</td></tr>`;
  }).join('');
  target.innerHTML = `<table class="quality-table"><thead><tr><th>#</th><th class="left">Тикер</th><th class="left">Компания</th><th class="left">Сектор</th>
    <th class="left">Модель</th><th>Quality сектор</th><th>Quality абсолют.</th><th class="left">Факторы</th><th>Покрытие</th><th>Период</th><th>Confidence</th><th>Статус</th>
    </tr></thead><tbody>${body}</tbody></table>`;
  target.querySelectorAll('[data-quality-ticker]').forEach((row) => {
    const open = () => openQualityDrawer(row.dataset.qualityTicker);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
  });
}

function qualityFactorBar(label, zValue) {
  const present = isNum(zValue);
  const width = present ? Math.max(0, Math.min(100, (zValue + 3) / 6 * 100)) : 0;
  return `<div class="quality-factor-row ${present ? '' : 'missing'}"><span>${esc(label)}</span><span class="quality-factor-track"><i style="width:${width.toFixed(1)}%"></i></span><b>${present ? ru(zValue, 2) : '—'}</b></div>`;
}

function openQualityDrawer(ticker) {
  if (!QUALITY) return;
  const row = QUALITY.rows.find((item) => item.ticker === ticker);
  const dialog = document.getElementById('quality-drawer');
  if (!row || !dialog) return;
  const title = document.getElementById('quality-drawer-title');
  const body = document.getElementById('quality-drawer-body');
  if (title) title.innerHTML = `<b>${esc(row.ticker)} · ${esc(row.name || '')}</b><div class="muted">${esc(row.sector || '')} · ${esc(row.quality_model_label || '')}</div>`;
  const raw = row.raw || {}, win = row.winsorized || {}, z = row.z || {};
  const factors = qualityFactorDefinitions(row);
  const factorPeriods = ((row.diagnostics || {}).factor_periods) || {};
  const decomp = factors.map((factor) => `<tr><td>${esc(factor.label || factor.key)}</td><td>${qualityFmtRaw(factor.key, raw[factor.key], factor.format)}</td><td>${qualityFmtRaw(factor.key, win[factor.key], factor.format)}</td><td>${isNum(z[factor.key]) ? ru(z[factor.key], 2) : '—'}</td></tr>`).join('');
  const bars = factors.map((factor) => qualityFactorBar(factor.label || factor.key, z[factor.key])).join('');
  const periodDetails = factors.filter((factor) => factorPeriods[factor.key]).map((factor) =>
    `${factor.label || factor.key}: ${factorPeriods[factor.key]}`
  ).join(' · ');
  const explanation = row.explanation || {};
  if (body) body.innerHTML = `<div class="quality-factor-bars">
      ${bars}${qualityFactorBar('Итоговый Quality', row.quality_z_sector)}
    </div>
    <table class="quality-decomp"><thead><tr><th>Фактор</th><th>Raw</th><th>Winsorized</th><th>Z-score</th></tr></thead><tbody>${decomp}</tbody></table>
    <div class="quality-drawer-note"><b>${esc(explanation.summary || '')}</b><br>${esc((explanation.weaknesses || []).length ? 'Слабые стороны: ' + explanation.weaknesses.join(', ') + '.' : 'Выраженных слабых сторон по доступным факторам нет.')}<br>${esc(explanation.confidence_note || '')}</div>
    <div class="quality-drawer-note">Период: <b>${esc(row.report_period_end || '—')}</b> · публикация: <b>${esc(row.publication_date || 'не подтверждена')}</b> · стандарт: <b>${esc(row.report_standard || '—')}</b><br>
      Источник: ${esc((row.provenance || {}).source_name || (row.provenance || {}).source_type || '—')} · normalization: ${esc(row.normalization_scope || '—')} · coverage: ${isNum(row.coverage_ratio) ? ru(row.coverage_ratio * 100, 0) + '%' : '—'}<br>
      ${periodDetails ? `Винтажи факторов: ${esc(periodDetails)}<br>` : ''}
      Предупреждения: ${esc((row.warnings || []).join(', ') || 'нет')}<br>Исключения: ${esc((row.exclusion_reasons || []).join(', ') || 'нет')}</div>`;
  // §13 a11y: запомнить элемент-триггер, чтобы вернуть фокус при закрытии (showModal сам даёт Escape+фокус-трап)
  dialog._returnFocus = document.activeElement;
  if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
}

function qualityCandidateAnalysis(config) {
  const stageSets = Object.fromEntries(
    ['universe', 'priced', 'scoredInvestable', 'coverage', 'confidence', 'adv', 'usable']
      .map((key) => [key, new Set()])
  );
  const coverageSets = { twoFactor: new Set(), full: new Set() };
  const modelSets = {};
  if (!QUALITY || !DATA || !Array.isArray(QUALITY.rows)) {
    return { config, candidates: [], stages: {}, coverageAvailable: {}, modelCoverage: {}, failure: 'data_unavailable' };
  }
  const universe = new Map(DATA.tickers.map((ticker) => [ticker.ticker, ticker]));
  const scoreKey = config.sectorNeutral ? 'quality_score_sector' : 'quality_score_absolute';
  const rankKey = config.sectorNeutral ? 'sector_rank_pct' : 'quality_rank_pct';
  const candidates = [];
  QUALITY.rows.forEach((q) => {
    const issuer = q.issuer_id || q.ticker;
    const model = q.quality_model || 'industrial_core';
    if (!modelSets[model]) modelSets[model] = {
      label: q.quality_model_label || model,
      twoFactor: new Set(), full: new Set(), coverage: new Set(), usable: new Set(),
    };
    stageSets.universe.add(issuer);
    const t = universe.get(q.ticker);
    if (!t || !isNum(t.price) || t.price <= 0) return;
    stageSets.priced.add(issuer);
    if (!q.score_eligible || !q.investable || q.financial_model_required) return;
    stageSets.scoredInvestable.add(issuer);
    if (isNum(q.coverage_ratio) && q.coverage_ratio >= 0.67) {
      coverageSets.twoFactor.add(issuer);
      modelSets[model].twoFactor.add(issuer);
    }
    if (isNum(q.coverage_ratio) && q.coverage_ratio >= 1) {
      coverageSets.full.add(issuer);
      modelSets[model].full.add(issuer);
    }
    if (!isNum(q.coverage_ratio) || q.coverage_ratio < config.minCoverage) return;
    stageSets.coverage.add(issuer);
    modelSets[model].coverage.add(issuer);
    if (!config.allowLow && !['high', 'medium'].includes(q.confidence)) return;
    stageSets.confidence.add(issuer);
    if (!isNum(q.adv_rub) || q.adv_rub < config.minAdv) return;
    stageSets.adv.add(issuer);
    if (!isNum(q[scoreKey]) || !isNum(q[rankKey])) return;
    stageSets.usable.add(issuer);
    modelSets[model].usable.add(issuer);
    candidates.push({ q, t });
  });
  const issuerBest = new Map();
  candidates.forEach((item) => {
    const key = item.q.issuer_id || item.q.ticker;
    const current = issuerBest.get(key);
    if (!current || (item.q.adv_rub || 0) > (current.q.adv_rub || 0)) issuerBest.set(key, item);
  });
  const uniqueCandidates = [...issuerBest.values()].sort((a, b) => b.q[rankKey] - a.q[rankKey] || String(a.q.ticker).localeCompare(String(b.q.ticker)));
  const stages = Object.fromEntries(Object.entries(stageSets).map(([key, values]) => [key, values.size]));
  stages.issuers = uniqueCandidates.length;
  const coverageAvailable = { twoFactor: coverageSets.twoFactor.size, full: coverageSets.full.size };
  const modelCoverage = Object.fromEntries(Object.entries(modelSets).map(([key, value]) => [key, {
    label: value.label,
    twoFactor: value.twoFactor.size,
    full: value.full.size,
    coverage: value.coverage.size,
    usable: value.usable.size,
  }]));
  return {
    config, candidates: uniqueCandidates, stages, coverageAvailable, modelCoverage,
    scoreKey, rankKey, failure: null,
  };
}

function buildQualityPortfolio(config) {
  const analysis = qualityCandidateAnalysis(config);
  QUALITY_LAST_ANALYSIS = analysis;
  const candidates = analysis.candidates;
  if (!QUALITY || !DATA) return null;
  if (!analysis.stages.scoredInvestable) analysis.failure = 'no_investable_scores';
  else if (!analysis.stages.coverage) analysis.failure = 'coverage';
  else if (!analysis.stages.confidence) analysis.failure = 'confidence';
  else if (!analysis.stages.adv) analysis.failure = 'adv';
  else if (!analysis.stages.usable) analysis.failure = 'score';
  const scoreKey = analysis.scoreKey;
  const rankKey = analysis.rankKey;
  const selected = candidates.slice(0, config.n);
  analysis.selected = selected.length;
  if (selected.length < 3) { analysis.failure ||= 'too_few'; return null; }
  if (selected.length * config.maxSecurity < 1 - 1e-9) { analysis.failure = 'security_cap'; return null; }
  if (selected.length * config.maxIssuer < 1 - 1e-9) { analysis.failure = 'issuer_cap'; return null; }
  const selectedSectors = new Set(selected.map((item) => item.q.sector || item.t.sector || ND));
  if (selectedSectors.size * config.sectorCap < 1 - 1e-9) { analysis.failure = 'sector_cap'; return null; }
  const vols = selected.map((item) => item.q.volatility).filter(isNum).sort((a, b) => a - b);
  const medianVol = vols.length ? vols[Math.floor(vols.length / 2)] : 0.3;
  const items = selected.map(({ q, t }) => {
    let rawWeight = 1;
    if (config.weight === 'score') rawWeight = Math.max(q[scoreKey], 1e-6);
    else if (config.weight === 'mcap') rawWeight = q.market_cap_rub || 0;
    else if (config.weight === 'invvol') rawWeight = 1 / (isNum(q.volatility) && q.volatility > 0 ? q.volatility : medianVol);
    else if (config.weight === 'factor_tilt') rawWeight = (q.free_float_market_cap_rub || q.market_cap_rub || 0) * q[scoreKey];
    return { ticker: q.ticker, name: q.name || t.name, sector: q.sector || t.sector || ND, issuer: q.issuer_id, t, q,
      score: q[rankKey] / 100, w: rawWeight };
  });
  const total = items.reduce((sum, item) => sum + item.w, 0);
  if (!(total > 0)) { analysis.failure = 'zero_weights'; return null; }
  items.forEach((item) => { item.w /= total; });
  capWeights(items, config.maxSecurity, config.sectorCap);
  if (items.some((item) => item.w > Math.min(config.maxSecurity, config.maxIssuer) + 1e-8)) { analysis.failure = 'issuer_cap'; return null; }
  const sectorWeights = {};
  items.forEach((item) => { sectorWeights[item.sector] = (sectorWeights[item.sector] || 0) + item.w; });
  if (Object.values(sectorWeights).some((weight) => weight > config.sectorCap + 1e-8)) { analysis.failure = 'sector_cap'; return null; }
  analysis.failure = null;
  return items.sort((a, b) => b.w - a.w);
}

function qualityFilterStatusConfig() {
  return {
    n: +(document.getElementById('pf-n') || {}).value || 10,
    weight: (document.getElementById('pf-weight') || {}).value || 'factor_tilt',
    sectorNeutral: !!(document.getElementById('quality-sector-neutral') || {}).checked,
    minCoverage: +(document.getElementById('quality-min-coverage') || {}).value || 0.67,
    minAdv: (+((document.getElementById('quality-min-adv') || {}).value || 10)) * 1e6,
    maxSecurity: (+((document.getElementById('pf-cap') || {}).value || 20)) / 100,
    maxIssuer: (+((document.getElementById('quality-max-issuer') || {}).value || 20)) / 100,
    sectorCap: (+((document.getElementById('pf-seccap') || {}).value || 40)) / 100,
    allowLow: !!(document.getElementById('quality-allow-low') || {}).checked,
  };
}

function renderQualityFilterStatus() {
  const target = document.getElementById('quality-filter-status');
  if (!target || !QUALITY || !DATA) return;
  const config = qualityFilterStatusConfig();
  const analysis = qualityCandidateAnalysis(config);
  const coverageSelect = document.getElementById('quality-min-coverage');
  if (coverageSelect) {
    const two = coverageSelect.querySelector('option[value="0.67"]');
    const full = coverageSelect.querySelector('option[value="1"]');
    if (two) two.textContent = `2 из 3 своей модели · ${analysis.coverageAvailable.twoFactor}`;
    if (full) full.textContent = `Все 3 своей модели · ${analysis.coverageAvailable.full}`;
  }
  const strict = !config.allowLow;
  const noFull = config.minCoverage >= 1 && analysis.coverageAvailable.full === 0;
  const modelSummary = Object.values(analysis.modelCoverage || {}).map((model) =>
    `${esc(model.label)}: ${model.coverage}`
  ).join(' · ');
  target.classList.toggle('warn', noFull || strict);
  if (noFull) {
    target.innerHTML = `<b>Нет эмитентов с полным набором своей модели.</b><span>Выбери «2 из 3 факторов»; отсутствующий третий фактор зависит от модели сектора.</span>`;
  } else if (strict) {
    target.innerHTML = `<b>Verified / PIT: ${analysis.stages.confidence}.</b><span>${analysis.coverageAvailable.twoFactor} эмитентов доступны только как live preview без подтверждённых publication dates.</span><button type="button" data-quality-filter-action="live-preview">Показать live preview</button>`;
  } else {
    target.innerHTML = `<b>Live preview: ${analysis.stages.issuers} эмитентов.</b><span>Score ${analysis.stages.scoredInvestable} → покрытие ${analysis.stages.coverage} → ADV ${analysis.stages.adv}.${modelSummary ? ' ' + modelSummary + '.' : ''}</span>`;
  }
  const previewButton = target.querySelector('[data-quality-filter-action="live-preview"]');
  if (previewButton) previewButton.addEventListener('click', () => {
    const preview = document.getElementById('quality-allow-low');
    if (preview) preview.checked = true;
    renderQualityFilterStatus();
  });
}

function qualityEmptyStateHTML(config, analysis) {
  const a = analysis || { stages: {}, coverageAvailable: {}, modelCoverage: {}, failure: 'data_unavailable' };
  const s = a.stages || {};
  let title = 'Не удалось сформировать RU Quality корзину.';
  let reason = 'Проверь доступность quality.json и рыночных данных.';
  let action = '';
  if (a.failure === 'coverage') {
    title = 'Выбранное покрытие не оставило кандидатов.';
    reason = `У каждой секторной модели свой третий фактор: стабильность EPS у компаний, стабильность прибыли у банков и Net debt/EBITDA у IT. В режиме «2 из 3» доступны ${a.coverageAvailable.twoFactor || 0} investable эмитентов.`;
    action = '<button type="button" class="btn" data-quality-action="two-factor-preview">Перейти к 2-факторному live preview</button>';
  } else if (a.failure === 'confidence') {
    title = 'Verified / PIT корзина пока пустая.';
    reason = `${s.coverage || 0} компаний проходят score и покрытие, но даты публикации отчётности не подтверждены. Их можно посмотреть только как явно отмеченный live preview.`;
    action = '<button type="button" class="btn" data-quality-action="live-preview">Открыть live preview</button>';
  } else if (a.failure === 'adv') {
    title = 'Кандидаты не проходят заданный ADV.';
    reason = `После score, investability и покрытия осталось ${s.confidence || 0}, после ADV — ${s.adv || 0}. Снизь минимальный ADV осознанно.`;
  } else if (a.failure === 'security_cap') {
    title = 'Лимит на бумагу несовместим с числом бумаг.';
    reason = `При ${a.selected || 0} бумагах и лимите ${ru((config.maxSecurity || 0) * 100, 0)}% невозможно распределить 100% капитала.`;
  } else if (a.failure === 'sector_cap') {
    title = 'Секторный лимит несовместим с составом корзины.';
    reason = `В выбранных кандидатах недостаточно разных секторов, чтобы распределить 100% капитала при лимите ${ru((config.sectorCap || 0) * 100, 0)}% на сектор.`;
  } else if (['too_few', 'issuer_cap'].includes(a.failure)) {
    title = 'После ограничения по эмитентам осталось слишком мало бумаг.';
    reason = `Уникальных кандидатов: ${s.issuers || 0}. Проверь число бумаг и лимиты, не ослабляя качество данных автоматически.`;
  }
  const funnel = `<div class="quality-empty-funnel"><span>Score + investability: ${s.scoredInvestable || 0}</span><span>Покрытие: ${s.coverage || 0}</span><span>Confidence: ${s.confidence || 0}</span><span>ADV: ${s.adv || 0}</span><span>Эмитенты: ${s.issuers || 0}</span></div>`;
  return `<div class="quality-empty"><strong>${esc(title)}</strong><p>${esc(reason)}</p>${funnel}${action ? `<div class="quality-empty-actions">${action}</div>` : ''}</div>`;
}

function wireQualityEmptyActions(target) {
  target.querySelectorAll('[data-quality-action]').forEach((button) => button.addEventListener('click', () => {
    const coverage = document.getElementById('quality-min-coverage');
    const preview = document.getElementById('quality-allow-low');
    if (button.dataset.qualityAction === 'two-factor-preview' && coverage) coverage.value = '0.67';
    if (preview) preview.checked = true;
    renderQualityFilterStatus();
    renderPortfolio();
  }));
}

function qualityPortfolioConfig(opts) {
  return {
    n: opts.n,
    weight: opts.weight,
    sectorNeutral: !!document.getElementById('quality-sector-neutral').checked,
    minCoverage: +(document.getElementById('quality-min-coverage').value || 0.67),
    minAdv: +(document.getElementById('quality-min-adv').value || 10) * 1e6,
    maxSecurity: opts.cap / 100,
    maxIssuer: +(document.getElementById('quality-max-issuer').value || 20) / 100,
    sectorCap: opts.seccap / 100,
    allowLow: !!document.getElementById('quality-allow-low').checked,
  };
}

function wireQuality() {
  const panel = document.getElementById('quality-panel');
  if (!panel) return;
  document.querySelectorAll('.strategy-mode').forEach((button) => button.addEventListener('click', () => {
    const method = document.getElementById('pf-method');
    if (!method) return;
    const requested = button.dataset.strategy;
    if (requested === 'ml') {
      activateMlStrategy();
      return;
    }
    const option = method.querySelector(`option[value="${requested}"]`);
    if (option && option.disabled) return;
    method.value = requested;
    method.dispatchEvent(new Event('change'));
  }));
  const strategyTabs = [...document.querySelectorAll('.strategy-mode')];
  strategyTabs.forEach((button, index) => button.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? strategyTabs.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + strategyTabs.length) % strategyTabs.length;
    strategyTabs[next].focus();
    strategyTabs[next].click();
  }));
  ['quality-model', 'quality-sector', 'quality-status', 'quality-sort'].forEach((id) => {
    const control = document.getElementById(id); if (control) control.addEventListener('change', renderQualityTable);
  });
  ['quality-sector-neutral', 'quality-min-coverage', 'quality-min-adv', 'quality-max-issuer', 'quality-allow-low'].forEach((id) => {
    const control = document.getElementById(id);
    if (control) control.addEventListener('change', renderQualityFilterStatus);
  });
  document.getElementById('quality-build').addEventListener('click', () => {
    const pf = document.getElementById('pf'); if (pf) pf.open = true;
    const out = document.getElementById('pf-out'); if (out) out.dataset.shown = '1';
    renderPortfolio();
  });
  const close = document.getElementById('quality-drawer-close');
  const qDrawer = document.getElementById('quality-drawer');
  if (close && qDrawer) close.addEventListener('click', () => qDrawer.close());
  if (qDrawer) {
    // §13 a11y: клик по бэкдропу закрывает; при закрытии (в т.ч. по Escape) — возврат фокуса на триггер
    qDrawer.addEventListener('click', (e) => { if (e.target === qDrawer) qDrawer.close(); });
    qDrawer.addEventListener('close', () => { try { if (qDrawer._returnFocus && qDrawer._returnFocus.focus) qDrawer._returnFocus.focus(); } catch (_e) { /* noop */ } });
  }
  syncStrategyPanels();
  loadQuality();
}

function activateMlStrategy() {
  ACTIVE_STRATEGY_MODE = 'ml';
  const pf = document.getElementById('pf');
  const marlamov = document.getElementById('marlamov');
  const quality = document.getElementById('quality-panel');
  const momentum = document.getElementById('momentum-panel');
  const panel = document.getElementById('ml-strategy-panel');
  if (pf) pf.hidden = true;
  if (marlamov) marlamov.hidden = true;
  if (quality) quality.hidden = true;
  if (momentum) momentum.hidden = true;
  if (panel) panel.hidden = false;
  document.querySelectorAll('.strategy-mode').forEach((button) => {
    const active = button.dataset.strategy === 'ml';
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  renderMlStrategy();
}

function renderMomentumStrategy() {
  const target = document.getElementById('momentum-content');
  if (!target || !DATA || ACTIVE_STRATEGY_MODE !== 'momentum') return;
  const meta = DATA.meta || {};
  const schedule = meta.momentum_schedule || {};
  const candidates = DATA.tickers.filter(eligibleForPortfolio)
    .filter((ticker) => isNum(ticker.mom_score) && isNum(ticker.adv) && ticker.adv >= PF_MIN_ADV)
    .sort((a, b) => b.mom_score - a.mom_score)
    .slice(0, 12);
  const status = schedule.status === 'current' ? 'Актуально' : schedule.status === 'pending_execution' ? 'К исполнению' : schedule.status === 'overdue' ? 'Просрочено' : 'Календарь недоступен';
  const rows = candidates.map((ticker, index) => `<tr>
    <td>${index + 1}</td><td>${instrumentIdentityHTML(ticker.ticker, ticker.name, instrumentTypeHint(ticker), 'sm')}</td>
    <td><b>${isNum(ticker.mom_score) ? `${ticker.mom_score >= 0 ? '+' : ''}${ru(ticker.mom_score * 100, 1)}%` : '—'}</b></td>
    <td>${isNum(ticker.adv) ? fmtRub(Math.round(ticker.adv)) : '—'}</td>
    <td>${isNum(ticker.vol_ann) ? ru(ticker.vol_ann * 100, 1) + '%' : '—'}</td></tr>`).join('');
  target.innerHTML = `<div class="strategy-execution-head">
      <div><span>Статус сигнала</span><b>${esc(status)}</b><small>WML 12–1; статистическая премия в текущем исследовании не подтверждена.</small></div>
      <div class="strategy-execution-meta"><span>Данные по <b>${esc(schedule.data_through || meta.momentum_asof || '—')}</b></span><span>Последний сигнал <b>${esc(schedule.last_signal_at || '—')}</b></span><span>Последнее исполнение <b>${esc(schedule.last_execution_at || '—')}</b></span></div>
    </div>
    <div class="strategy-schedule-grid">
      <div><span>Следующий расчёт</span><b>${esc(schedule.next_calculation_at || '—')}</b><small>после официального close</small></div>
      <div><span>Плановое исполнение</span><b>${esc(schedule.planned_execution_at || schedule.next_execution_at || '—')}</b><small>следующая сессия MOEX после signal close</small></div>
      <div><span>До пересмотра</span><b>${isNum(schedule.trading_days_remaining) ? schedule.trading_days_remaining + ' торг. дн.' : '—'}</b><small>${isNum(schedule.calendar_days_remaining) ? schedule.calendar_days_remaining + ' календарных дней' : ''}</small></div>
      <div><span>Действие</span><b>Наблюдать</b><small>исследовательский рейтинг, не исполнимая рекомендация</small></div>
    </div>
    <div class="strategy-workspace-head"><div><h3>Текущий рейтинг Momentum</h3><p>Доходность от close 12 месяцев назад до последнего полного месяца; самый свежий месяц исключён. Это ranking, не опубликованный портфель.</p></div><b>${candidates.length} бумаг</b></div>
    ${candidates.length ? `<div class="mls-table-wrap"><table class="mls-table momentum-table"><thead><tr><th>#</th><th>Бумага</th><th>WML 12–1</th><th>ADV</th><th>Волатильность</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="strategy-empty"><b>Сигнал не сформирован</b><span>Недостаточно ликвидных бумаг с полной 12-месячной историей.</span></div>'}
    <details class="mls-method"><summary>Методология и ограничения</summary><div>Сигнал формируется после закрытия последней торговой сессии месяца и моделирует исполнение на следующей сессии MOEX. Текущий список не выдаётся за point-in-time backtest; история делистингов пока неполна. Turnover и издержки будут показаны только после появления предыдущего подтверждённого snapshot.</div></details>`;
}

function loadMlStrategy() {
  if (ML_STRATEGY) return Promise.resolve(ML_STRATEGY);
  if (ML_STRATEGY_LOADING) return ML_STRATEGY_LOADING;
  const get = (name) => {
    const base = dataURL('ml_strategy/' + name);
    const refresh = Math.floor(Date.now() / DATA_CACHE_TTL_MS);
    return fetch(base + (base.includes('?') ? '&' : '?') + 'ml_refresh=' + refresh).then((response) => {
    if (!response.ok) throw new Error(name + ': HTTP ' + response.status);
    return response.json();
    });
  };
  ML_STRATEGY_LOADING = Promise.all([
    get('latest.json'), get('backtest.json'), get('model_card.json'), get('data_quality.json'),
    get('sector_features/latest_quality.json'), get('sector_features/latest_registry.json'),
  ]).then(([latest, backtest, modelCard, dataQuality, sectorQuality, sectorRegistry]) => {
    ML_STRATEGY = { latest, backtest, modelCard, dataQuality, sectorQuality, sectorRegistry };
    ML_STRATEGY_LOADING = null;
    return ML_STRATEGY;
  }).catch((error) => {
    ML_STRATEGY_LOADING = null;
    throw error;
  });
  return ML_STRATEGY_LOADING;
}

function mlsPct(value, digits = 1) {
  return isNum(value) ? ru(value * 100, digits) + '%' : '—';
}

function mlsActionLabel(action) {
  return ({
    hold: 'Изменений не требуется', rebalance: 'Сформирован новый состав',
    no_trade: 'Новых операций нет', frozen: 'Расчёт заморожен',
    NO_ACTION: 'Изменений не требуется', WATCH: 'Наблюдать', REBALANCE: 'Сформирован новый состав',
    DATA_STALE: 'Расчёт заморожен', MODEL_UNCERTAIN: 'Новый сигнал отклонён', DEGRADED: 'Расчёт заморожен',
  })[action] || action || '—';
}

function mlsModelStatusLabel(status) {
  return ({ production: 'Production', research_only: 'Только исследование', rejected: 'Отклонена', failed: 'Ошибка расчёта', APPROVED: 'Production', RESEARCH_ONLY: 'Только исследование' })[status] || status || '—';
}

function mlsCheckLabel(name) {
  return ({
    price_series: 'Ценовые ряды',
    history_depth: 'Глубина истории',
    benchmark_history: 'История MCFTR',
    latest_investable_cross_section: 'Ликвидный universe',
    staleness: 'Свежесть',
    official_dividend_coverage: 'Дивиденды MOEX',
    macro_market_features: 'Рыночные и макро-факторы',
    unresolved_extreme_moves: 'Экстремальные движения',
    historical_membership: 'Исторический состав',
  })[name] || String(name || '').replaceAll('_', ' ');
}

function mlsCurveSvg(curve) {
  if (!Array.isArray(curve) || curve.length < 2) return '<div class="mls-empty">Истории пока недостаточно.</div>';
  const width = 800, height = 210, pad = 16;
  const values = curve.flatMap((row) => [row.portfolio_nav, row.benchmark_nav]).filter(isNum);
  const low = Math.min(...values), high = Math.max(...values);
  const range = Math.max(0.0001, high - low);
  const path = (key) => curve.map((row, index) => {
    const x = pad + index * (width - pad * 2) / (curve.length - 1);
    const y = height - pad - (row[key] - low) / range * (height - pad * 2);
    return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<div class="mls-chart" role="img" aria-label="Динамика модельного портфеля и MCFTR">
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <path class="mls-grid" d="M${pad},${height / 2}H${width - pad}"/>
      <path class="mls-line benchmark" d="${path('benchmark_nav')}"/>
      <path class="mls-line portfolio" d="${path('portfolio_nav')}"/>
    </svg>
    <div class="mls-legend"><span><i class="portfolio"></i>Модель после издержек</span><span><i class="benchmark"></i>MCFTR</span></div>
  </div>`;
}

function renderMlStrategy() {
  const target = document.getElementById('mls-content');
  const state = document.getElementById('mls-state');
  if (!target || ACTIVE_STRATEGY_MODE !== 'ml') return;
  if (!ML_STRATEGY) {
    target.innerHTML = '<div class="mls-loading">Загрузка проверенного snapshot…</div>';
    loadMlStrategy().then(renderMlStrategy).catch((error) => {
      if (state) { state.textContent = 'Недоступно'; state.className = 'mls-state blocked'; }
      target.innerHTML = `<div class="mls-error"><b>ML snapshot не опубликован</b><span>${esc(error.message)}</span><small>Предыдущие стратегии продолжают работать; расчёт на клиенте не подменяется заглушкой.</small></div>`;
    });
    return;
  }
  const { latest, backtest, modelCard, dataQuality, sectorQuality, sectorRegistry } = ML_STRATEGY;
  const action = latest.action_status || (latest.signal || {}).action;
  const model = latest.model || {};
  const published = latest.published_portfolio || null;
  const candidate = latest.candidate_portfolio || null;
  const execution = latest.execution || {};
  const portfolio = published || {};
  const metrics = backtest.portfolio_metrics || {};
  const positions = portfolio.positions || [];
  if (state) {
    state.textContent = mlsActionLabel(action);
    state.className = 'mls-state ' + (action === 'rebalance' || action === 'REBALANCE' ? 'good' : (action === 'frozen' || action === 'DATA_STALE' ? 'blocked' : 'watch'));
  }
  const publishedRows = positions.map((row) => {
    const drivers = (row.sector_drivers || []).slice(0, 3);
    const driverHtml = drivers.length
      ? `<div class="mls-drivers">${drivers.map((driver) =>
        `<span class="${esc(driver.direction)}">${esc(driver.factor)} <b>${isNum(driver.value) ? ru(driver.value * 100, 2) + '%' : '—'}</b></span>`
      ).join('')}</div>`
      : '';
    return `<tr>
    <td>${instrumentIdentityHTML(row.ticker, row.name, row.instrument_type, 'sm')}</td>
    <td>${esc(row.sector || '—')}${driverHtml}</td>
    <td><b>${mlsPct(row.target_weight)}</b></td>
    <td>${mlsPct(row.expected_excess_return_20d, 2)}</td>
  </tr>`;
  }).join('');
  const candidateRows = ((candidate || {}).positions || []).map((row) => `<tr>
    <td>${instrumentIdentityHTML(row.ticker, row.name, row.instrument_type, 'sm')}</td><td>${esc(row.sector || '—')}</td>
    <td><b>${mlsPct(row.calculated_weight)}</b></td><td>${mlsPct(row.expected_excess_return_20d, 2)}</td></tr>`).join('');
  const dqChecks = (dataQuality.checks || []).map((check) =>
    `<li><span>${esc(mlsCheckLabel(check.name))}${check.value != null ? `<small>${esc(String(check.value))}${check.minimum != null ? ` / min ${esc(String(check.minimum))}` : ''}</small>` : ''}</span><b class="${String(check.status).toLowerCase()}">${esc(check.status)}</b></li>`
  ).join('');
  const limitations = (modelCard.limitations || []).map((text) => `<li>${esc(text)}</li>`).join('');
  const sectorPacks = (sectorQuality.packs || []).map((pack) => {
    const ablation = (pack.status || '').toLowerCase();
    const evaluation = pack.evaluation || {};
    const gateLabels = {
      identical_test_rows: 'общая OOS-выборка', sector_oos_evidence: 'объём отраслевой выборки',
      rank_ic_improvement: 'прирост Rank IC', hit_rate_change: 'hit rate',
      positive_top_bottom_spread: 'top-bottom spread', relative_after_cost_portfolio: 'результат после издержек',
    };
    const blocked = (pack.blocked_sources || []).length
      ? ` · недоступны: ${(pack.blocked_sources || []).join(', ')}`
      : '';
    const timing = evaluation.feature_role === 'sector_timing';
    const baseIc = timing ? evaluation.timing_base_rank_ic : evaluation.base_rank_ic;
    const candidateIc = timing ? evaluation.timing_candidate_rank_ic : evaluation.candidate_rank_ic;
    const deltaIc = timing ? evaluation.timing_rank_ic_improvement : evaluation.rank_ic_improvement;
    const rankIc = isNum(deltaIc)
      ? `${timing ? 'timing' : 'issuer'} Rank IC ${ru(baseIc, 3)} → ${ru(candidateIc, 3)}; Δ ${deltaIc >= 0 ? '+' : ''}${ru(deltaIc, 4)} / gate +${ru(evaluation.rank_ic_minimum || 0, 4)}`
      : 'OOS-метрика недоступна';
    const evidence = isNum(evaluation.sector_oos_rows)
      ? `${evaluation.sector_oos_rows} OOS-строк · ${evaluation.sector_oos_tickers || 0} бумаг · ${timing ? evaluation.timing_dates : evaluation.sector_oos_dates || 0} дат`
      : 'отраслевая выборка недоступна';
    const failed = (evaluation.failed_gates || []).map((name) => gateLabels[name] || name).join(', ');
    const sourceState = (pack.blocked_sources || []).length ? 'источники неполные' : 'официальные источники готовы';
    return `<li><span><b>${esc(pack.label || pack.pack_id)}</b><small>${timing ? 'Timing сектора' : 'Отбор внутри сектора'} · ${esc(sourceState)} · ${esc(evidence)} · ${esc(rankIc)} · after-cost ${esc(String(evaluation.after_costs_gate || '—'))}${failed ? ` · не пройдено: ${esc(failed)}` : ''}${esc(blocked)}</small></span><strong class="${esc(ablation)}">${pack.used_in_production ? 'В production' : 'Не прошёл gate'}</strong></li>`;
  }).join('');
  const approvedPackCount = (sectorQuality.packs || []).filter((pack) => pack.status === 'APPROVED').length;
  const evaluatedPackCount = sectorQuality.evaluated_pack_count || (sectorQuality.packs || []).filter((pack) => pack.ablation_status).length;
  const approvedSources = (sectorRegistry.sources || []).filter((source) => source.status === 'APPROVED').length;
  const diagnostics = latest.diagnostics || {};
  const predictive = diagnostics.predictive_gate || {};
  const portfolioGate = diagnostics.portfolio_gate || {};
  const predictiveActual = predictive.actual || {};
  const portfolioActual = portfolioGate.actual || {};
  const publishedTitle = published ? 'Последний подтверждённый модельный состав' : 'Целевой состав не сформирован';
  const publishedBody = published ? `<div class="mls-layout"><div class="mls-main">
      <div class="mls-section-head"><div><h3>${publishedTitle}</h3><p>Опубликован ${esc(published.as_of || latest.data_as_of || '—')} · run ${esc(published.published_from_run_id || published.run_id || '—')}.</p></div><b>${mlsPct(published.cash_weight)} cash</b></div>
      <div class="mls-table-wrap"><table class="mls-table"><thead><tr><th>Бумага</th><th>Сектор</th><th>Подтверждённый вес</th><th>Excess 20д</th></tr></thead><tbody>${publishedRows}</tbody></table></div>
    </div><aside class="mls-side"><h3>Контроль качества</h3><ul class="mls-checks">${dqChecks}</ul></aside></div>`
    : `<div class="mls-layout"><div class="strategy-empty"><b>${publishedTitle}</b><span>${esc((latest.signal || {}).reason || 'Новый кандидат не прошёл обязательные gates.')}</span><small>Расчётные веса не являются операциями и скрыты ниже.</small></div><aside class="mls-side"><h3>Контроль качества</h3><ul class="mls-checks">${dqChecks}</ul></aside></div>`;
  target.innerHTML = `
    <div class="mls-action">
      <div><span>Статус стратегии</span><b>${esc((latest.signal || {}).title || mlsActionLabel(action))}</b><small>${esc((latest.signal || {}).reason || '')}</small></div>
      <div class="mls-meta"><span>Данные <b>${esc(latest.data_as_of || '—')}</b></span><span>Run <b>${esc(((latest.run || {}).run_id) || '—')}</b></span><span>Benchmark <b>${esc(latest.benchmark || 'MCFTR')}</b></span></div>
    </div>
    <div class="mls-kpis">
      <div><span>Модель</span><b>${esc(model.champion || '—')}</b><small>${esc(mlsModelStatusLabel(latest.model_status || model.status))}</small></div>
      <div><span>Подтверждённый состав</span><b>${positions.length ? positions.length + ' бумаг' : 'Нет'}</b><small>${published ? esc(published.method || '—') : 'кандидат не опубликован'}</small></div>
      <div><span>Действие</span><b>${esc(mlsActionLabel(action))}</b><small>${action === 'rebalance' ? 'принятый rebalance' : 'исполняемых изменений нет'}</small></div>
      <div><span>Оборот</span><b>${isNum(execution.turnover) ? mlsPct(execution.turnover) : '—'}</b><small>лимит ${isNum(execution.turnover_cap) ? mlsPct(execution.turnover_cap) : '—'}</small></div>
      <div><span>Издержки</span><b>${isNum(execution.estimated_cost_rub) ? ru(execution.estimated_cost_rub, 0) + ' ₽' : '—'}</b><small>${isNum(execution.one_way_cost_bps) ? ru(execution.one_way_cost_bps, 0) + ' б.п.' : '—'}</small></div>
      <div><span>Входы модели</span><b>${esc(latest.data_status || String(dataQuality.status || '—').toLowerCase())}</b><small>${latest.data_quality ? latest.data_quality.investable_companies : '—'} investable</small></div>
    </div>
    ${publishedBody}
    ${candidateRows ? `<details class="mls-candidate"><summary>Исследовательский кандидат — не используется для операций</summary><div class="candidate-warning">Расчётные веса нового run. Нет количества акций, рублёвых сумм и команд увеличить/снизить.</div><div class="mls-table-wrap"><table class="mls-table"><thead><tr><th>Бумага</th><th>Сектор</th><th>Расчётный вес</th><th>Excess 20д</th></tr></thead><tbody>${candidateRows}</tbody></table></div></details>` : ''}
    <div class="mls-backtest">
      <div class="mls-section-head"><div><h3>Out-of-sample против MCFTR</h3><p>Purged walk-forward, следующий торговый день, после заданных издержек.</p></div></div>
      ${mlsCurveSvg(backtest.curve)}
      <div class="mls-metrics">
        <span>CAGR <b>${mlsPct(metrics.cagr_after_costs)}</b></span>
        <span>Sharpe <b>${isNum(metrics.sharpe_after_costs) ? ru(metrics.sharpe_after_costs, 2) : '—'}</b></span>
        <span>Max drawdown <b>${mlsPct(metrics.max_drawdown)}</b></span>
        <span>Excess <b>${mlsPct(metrics.excess_cumulative_return)}</b></span>
        <span>Периодов <b>${metrics.periods || '—'}</b></span>
      </div>
    </div>
    <details class="mls-method">
      <summary>Почему такой статус?</summary>
      <div class="mls-diagnostics"><p><b>Прогнозный gate:</b> ${esc(predictive.status || '—')} · Rank IC ${isNum(predictiveActual.spearman_ic) ? ru(predictiveActual.spearman_ic, 3) : '—'} · hit rate ${mlsPct(predictiveActual.hit_rate)}.</p><p><b>After-cost gate:</b> ${esc(portfolioGate.status || '—')} · excess ${mlsPct(portfolioActual.excess_cumulative_return)} · Sharpe ${isNum(portfolioActual.sharpe_after_costs) ? ru(portfolioActual.sharpe_after_costs, 2) : '—'}.</p><p><b>Target:</b> ${esc((modelCard.target || {}).name || '—')}. Scaler и imputer обучаются внутри каждого fold; незакрытые targets исключаются.</p><div class="mls-sector-summary"><div><span>Отраслевые признаки</span><b>${evaluatedPackCount} из 4 оценены · ${approvedPackCount} в production</b><small>${approvedSources} официальных ряда. Веса production-модели используют только packs, прошедшие все фиксированные OOS gates.</small></div><ul>${sectorPacks}</ul></div><ul>${limitations}</ul></div>
    </details>`;
}

function eligibleForPortfolio(t) {
  if (t.status !== 'ok' || !isNum(t.price) || t.price <= 0) return false;
  if (t.verdict && t.verdict.unreliable) return false;
  if (isNum(t.mcap) && t.mcap < PF_MIN_MCAP) return false;   // ND по mcap не отсекаем
  if (isNum(t.adv) && t.adv < PF_MIN_ADV) return false;      // нетендерные — вон (ND по adv не отсекаем)
  return true;
}

function marlamovPortfolioCandidates() {
  if (!MARLAMOV || !Array.isArray(MARLAMOV.rows) || !DATA) return [];
  const universe = new Map(DATA.tickers.map((ticker) => [ticker.ticker, ticker]));
  const rfr = MARLAMOV.meta && isNum(MARLAMOV.meta.rfr) ? MARLAMOV.meta.rfr : null;
  return MARLAMOV.rows.map((row) => {
    const ticker = universe.get(row.ticker);
    if (!ticker || !eligibleForPortfolio(ticker)) return null;
    if (!row.eligible || !isNum(row.expected_net_spread)) return null;
    return {
      t: ticker,
      score: row.expected_net_spread,
      strategy: { ...row },
    };
  }).filter(Boolean);
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
  if (method === 'quality') return buildQualityPortfolio(qualityPortfolioConfig(opts));
  const uni = DATA.tickers.filter(eligibleForPortfolio);
  const scoreFn = method === 'momentum' ? ((t) => (isNum(t.mom_score) ? t.mom_score : null)) : qualityScore;
  const scored = method === 'marlamov'
    ? marlamovPortfolioCandidates()
    : uni.map((t) => ({ t, score: scoreFn(t) })).filter((x) => x.score != null);
  if (method !== 'marlamov' && scored.length < 3) return null;
  if (method === 'marlamov' && scored.length === 0) return [];
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, opts.n);
  if (method === 'marlamov') {
    return top.map((x) => ({ ticker: x.t.ticker, name: x.t.name, sector: x.t.sector || ND, t: x.t,
      score: x.score, strategy: x.strategy || null, w: 1 / opts.n }));
  }
  const vols = top.map((x) => x.t.vol_ann).filter(isNum).sort((a, b) => a - b);   // для дозаполнения inverse-vol
  const medVol = vols.length ? vols[Math.floor(vols.length / 2)] : 0.3;
  const ss = top.map((x) => x.score), smin = Math.min(...ss), srange = (Math.max(...ss) - smin) || 1;  // диапазон для score-weight
  const items = top.map((x) => {
    let w = 1;
    if (opts.weight === 'score') w = (x.score - smin) + 0.15 * srange;   // ∝ фактору (сдвиг в плюс; низший ~15% шага, не ноль)
    else if (opts.weight === 'mcap') w = isNum(x.t.mcap) ? x.t.mcap : 1;
    else if (opts.weight === 'invvol') w = 1 / (isNum(x.t.vol_ann) && x.t.vol_ann > 0 ? x.t.vol_ann : medVol);
    return { ticker: x.t.ticker, name: x.t.name, sector: x.t.sector || ND, t: x.t, score: x.score, strategy: x.strategy || null, w };
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

/* ══════════════════════════════════════════════════════════════════════════════
   ГРАНИЦА ЭФФЕКТИВНОСТИ (модуль «Эффективность портфеля» в X-Ray → Сценарии)

   Чистая математика: ни одна функция ниже не трогает DOM и не знает о портфеле
   пользователя — на вход только матрицы. Состав портфеля не покидает браузер.

   Почему НЕ переиспользуется соседний maxSharpe(): он решает w ∝ Σ⁻¹μ с клипом
   отрицательных весов. Это численно хрупко и не гарантирует касательный портфель
   при ограничениях. Здесь Max Sharpe выбирается среди точек ОГРАНИЧЕННОЙ границы —
   то есть среди заведомо допустимых портфелей.
   ══════════════════════════════════════════════════════════════════════════════ */

const EF = {
  MIN_OBS: 36,          // меньше — бумага не входит в оптимизатор
  PREF_OBS: 60,         // ниже — пониженная уверенность
  COV_MIN: 70,          // покрытие по стоимости, % — ниже не показываем границу
  COV_FULL: 85,         // выше — полный расчёт без предупреждения
  FRONTIER_PTS: 40,     // точек на границе
  MAX_ITER: 1200,       // потолок итераций; выход раньше по сходимости
  TOL: 1e-10,
  PENALTY: [1e2, 1e3, 1e4, 1e5],   // рост штрафа за отклонение от целевой доходности
  RET_TOL: 2e-4,        // допуск попадания в целевую доходность (годовых, доли)
  MONTHS_Y: 12,
};

/** Ledoit–Wolf shrinkage ковариации к масштабированной единичной матрице.
 *
 *  Реализация повторяет sklearn.covariance.LedoitWolf (identity-scaled target).
 *  Формула выведена из четвёртых моментов, поэтому правдоподобная опечатка даёт
 *  правдоподобную матрицу — ошибку не увидеть глазами. Отсюда parity-тест против
 *  sklearn на фиксированных данных: tests/fixtures/ledoit_wolf.json.
 *
 *  @param {number[][]} X — T×N матрица доходностей (строка = наблюдение)
 *  @returns {{cov:number[][], shrinkage:number, mu:number}}
 */
function efLedoitWolf(X) {
  const T = X.length, N = X[0].length;
  const mean = new Array(N).fill(0);
  for (let t = 0; t < T; t++) for (let i = 0; i < N; i++) mean[i] += X[t][i] / T;
  const Z = X.map((row) => row.map((v, i) => v - mean[i]));   // центрируем

  // выборочная ковариация (смещённая, делитель T — как в sklearn)
  const S = Array.from({ length: N }, () => new Array(N).fill(0));
  for (let t = 0; t < T; t++) {
    const z = Z[t];
    for (let i = 0; i < N; i++) { const zi = z[i]; for (let j = i; j < N; j++) S[i][j] += zi * z[j]; }
  }
  for (let i = 0; i < N; i++) for (let j = i; j < N; j++) { S[i][j] /= T; S[j][i] = S[i][j]; }

  let mu = 0;
  for (let i = 0; i < N; i++) mu += S[i][i];
  mu /= N;                                   // средняя дисперсия = масштаб цели

  // delta² = ||S − mu·I||²_F / N — насколько выборочная матрица далека от цели
  let delta = 0;
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const d = S[i][j] - (i === j ? mu : 0);
    delta += d * d;
  }
  delta /= N;

  // beta² — дисперсия оценки самой ковариации (по четвёртым моментам)
  let beta = 0;
  for (let t = 0; t < T; t++) {
    const z = Z[t];
    for (let i = 0; i < N; i++) { const zi = z[i]; for (let j = 0; j < N; j++) { const d = zi * z[j] - S[i][j]; beta += d * d; } }
  }
  beta /= (T * T * N);
  beta = Math.min(beta, delta);              // shrinkage не может превысить 1

  const shrinkage = delta > 0 ? beta / delta : 0;
  const cov = S.map((row, i) => row.map((v, j) => (1 - shrinkage) * v + (i === j ? shrinkage * mu : 0)));
  return { cov, shrinkage, mu };
}

/** James–Stein сжатие средних доходностей к общему cross-sectional якорю.
 *
 *  Простое историческое среднее — худший вход для квадратичного оптимизатора:
 *  ошибка оценки μ бьёт по результату сильнее, чем ошибка Σ, и solver охотно
 *  ставит максимум веса в бумагу, которой «повезло» на выборке. Сжатие к общему
 *  среднему гасит именно эти выбросы.
 *
 *  λ = min(1, (N−2)·σ̄² / (T·Σ(μᵢ−μ̄)²)) — классическая форма, воспроизводимая и
 *  не требующая произвольных коэффициентов.
 */
function efJamesStein(X, cov) {
  const T = X.length, N = X[0].length;
  const sample = new Array(N).fill(0);
  for (let t = 0; t < T; t++) for (let i = 0; i < N; i++) sample[i] += X[t][i] / T;

  // Якорь — доходность портфеля минимальной дисперсии (Jorion 1986), а не простое
  // среднее по бумагам: в портфельной задаче осмысленная «точка притяжения» —
  // это доходность наименее рискованной комбинации, а не арифметика по тикерам.
  // Если ковариация не передана, откатываемся на кросс-секционное среднее.
  let anchor;
  let lambda;
  if (cov && cov.length === N) {
    const ones = new Array(N).fill(1);
    let invOnes, invDiff;
    try { invOnes = _solve(cov, ones); } catch (e) { invOnes = null; }
    const denom = invOnes ? invOnes.reduce((a, b) => a + b, 0) : 0;
    if (invOnes && Math.abs(denom) > 1e-12) {
      const wMin = invOnes.map((v) => v / denom);
      anchor = wMin.reduce((s, w, i) => s + w * sample[i], 0);
      const diff = sample.map((m) => m - anchor);
      try { invDiff = _solve(cov, diff); } catch (e) { invDiff = null; }
      const quad = invDiff ? diff.reduce((s, d, i) => s + d * invDiff[i], 0) : 0;
      // Bayes-Stein: λ = (N+2) / ((N+2) + T·(μ̂−μ_g)ᵀΣ⁻¹(μ̂−μ_g)).
      // Строго в (0,1): не схлопывает все доходности в одну точку даже когда
      // выборочные средние почти неразличимы — просто сильно их сближает.
      lambda = (N + 2) / ((N + 2) + T * Math.max(0, quad));
    }
  }
  if (!isNum(anchor) || !isNum(lambda)) {
    anchor = sample.reduce((a, b) => a + b, 0) / N;
    const disp = sample.reduce((a, m) => a + (m - anchor) ** 2, 0);
    let varSum = 0;
    for (let i = 0; i < N; i++) {
      let v = 0;
      for (let t = 0; t < T; t++) { const d = X[t][i] - sample[i]; v += d * d; }
      varSum += v / Math.max(1, T - 1);
    }
    lambda = (disp > 0 && N > 2) ? ((N - 2) * (varSum / N)) / (T * disp) : 1;
  }
  lambda = Math.max(0, Math.min(1, lambda));
  return { mu: sample.map((m) => (1 - lambda) * m + lambda * anchor), lambda, anchor, sample };
}

/** Проекция на {Σw=1, lo ≤ w ≤ hi} — точная, через бинарный поиск по сдвигу.
 *  Нужна вместо обычной проекции на симплекс, потому что у нас есть потолок веса. */
function efProjectCapped(v, lo, hi) {
  const N = v.length;
  if (lo * N > 1 + 1e-12 || hi * N < 1 - 1e-12) return null;   // множество пусто

  // Решение имеет вид wᵢ = clip(vᵢ − τ, lo, hi), где τ подобрано так, что Σw = 1.
  // g(τ) = Σ clip(vᵢ − τ) — кусочно-линейная убывающая функция с изломами в
  // точках vᵢ−lo и vᵢ−hi, поэтому τ находится ТОЧНО: ищем отрезок, где g
  // пересекает 1, и решаем на нём линейное уравнение.
  //
  // Раньше здесь стояла 100-шаговая бисекция. Она давала верный ответ, но
  // проекция вызывается на КАЖДОЙ итерации градиента (164 000 раз на построение
  // границы) — замер показал 3,3 с на границу и 6,5 с на оценку устойчивости.
  const bp = new Array(2 * N);
  for (let i = 0; i < N; i++) { bp[i] = v[i] - hi; bp[N + i] = v[i] - lo; }
  bp.sort((a, b) => a - b);

  const g = (tau) => {
    let s = 0;
    for (let i = 0; i < N; i++) { const x = v[i] - tau; s += x < lo ? lo : (x > hi ? hi : x); }
    return s;
  };
  // g убывает по τ → бинарный поиск по ОТСОРТИРОВАННЫМ изломам (O(log N) шагов)
  let a = 0, b = bp.length - 1;
  if (g(bp[0]) <= 1) { a = -1; } else if (g(bp[b]) >= 1) { a = b; } else {
    while (b - a > 1) { const m = (a + b) >> 1; if (g(bp[m]) >= 1) a = m; else b = m; }
  }
  // на отрезке [bp[a], bp[a+1]] активное множество не меняется → g линейна
  const tauLo = a < 0 ? bp[0] - (hi - lo) - 1 : bp[a];
  const tauHi = a >= bp.length - 1 ? bp[bp.length - 1] + (hi - lo) + 1 : bp[a + 1];
  const gLo = g(tauLo), gHi = g(tauHi);
  const tau = Math.abs(gLo - gHi) < 1e-15 ? tauLo : tauLo + ((gLo - 1) * (tauHi - tauLo)) / (gLo - gHi);

  const out = new Array(N);
  for (let i = 0; i < N; i++) { const x = v[i] - tau; out[i] = x < lo ? lo : (x > hi ? hi : x); }
  return out;
}

/** min wᵀΣw при Σw=1, lo≤w≤hi и (мягко) μᵀw = target.
 *
 *  Целевая доходность вводится растущим квадратичным штрафом, а не жёстким
 *  равенством: проекция на пересечение симплекса, box и гиперплоскости не имеет
 *  дешёвой замкнутой формы, а штраф с проверкой невязки даёт тот же результат и
 *  ЧЕСТНО сообщает, если точка недостижима (см. ok в ответе).
 */
function efMinVarAtTarget(cov, mu, target, lo, hi, warm) {
  const N = cov.length;
  // target === null → чистый минимум дисперсии без ограничения на доходность.
  // Раньше это эмулировалось «недостижимой» целью −1e9, из-за чего штрафной
  // градиент 2ρ(μᵀw − target) разносил веса в бесконечность и расчёт вис.
  const pure = target === null || !isNum(target);
  let L = 1e-9;
  for (let i = 0; i < N; i++) { let s = 0; for (let j = 0; j < N; j++) s += Math.abs(cov[i][j]); L = Math.max(L, s); }
  // тёплый старт: соседняя точка границы — почти тот же портфель, поэтому
  // стартовать от предыдущего решения кратно дешевле, чем от равных весов
  let w = efProjectCapped(warm && warm.length === N ? warm.slice() : new Array(N).fill(1 / N), lo, hi);
  if (!w) return { ok: false, reason: 'bounds_infeasible' };

  const muNorm = mu.reduce((a, m) => a + m * m, 0);
  const stages = pure ? [0] : EF.PENALTY;
  const iters = Math.round(EF.MAX_ITER / stages.length);
  for (const rho of stages) {
    const step = 1 / (2 * L + 2 * rho * muNorm + 1e-12);
    for (let k = 0; k < iters; k++) {
      const g = _sigma(cov, w).map((x) => 2 * x);
      if (rho) {
        let dot = 0; for (let i = 0; i < N; i++) dot += mu[i] * w[i];
        const pen = 2 * rho * (dot - target);
        for (let i = 0; i < N; i++) g[i] += pen * mu[i];
      }
      const prev = w;
      const next = new Array(N);
      for (let i = 0; i < N; i++) next[i] = w[i] - step * g[i];
      const proj = efProjectCapped(next, lo, hi);
      if (!proj || proj.some((v) => !Number.isFinite(v))) return { ok: false, reason: 'numeric_failure' };
      w = proj;
      if (k % 25 === 24) {                       // проверка сходимости — не каждый шаг
        let d = 0; for (let i = 0; i < N; i++) d += Math.abs(w[i] - prev[i]);
        if (d < EF.TOL * N) break;               // веса перестали двигаться
      }
    }
  }
  let achieved = 0; for (let i = 0; i < N; i++) achieved += mu[i] * w[i];
  return { ok: pure || Math.abs(achieved - target) <= EF.RET_TOL, w, achieved, target };
}

/** Статистики портфеля. Годовые: доходность ×12, волатильность ×√12. */
function efStats(w, mu, cov, rf) {
  let ret = 0, varm = 0;
  const N = w.length;
  for (let i = 0; i < N; i++) ret += mu[i] * w[i];
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) varm += w[i] * cov[i][j] * w[j];
  const retA = ret * EF.MONTHS_Y;
  const volA = Math.sqrt(Math.max(0, varm) * EF.MONTHS_Y);
  return { ret: retA, vol: volA, sharpe: volA > 1e-9 ? (retA - (rf || 0)) / volA : null };
}

/** Ограниченная граница эффективности: серия min-var при целевой доходности.
 *  Monte Carlo сознательно НЕ используется — случайное облако не гарантирует
 *  нахождения касательного портфеля и выдаёт случайную точку за оптимум (§1.8). */
function efFrontier(mu, cov, lo, hi, rf) {
  const N = mu.length;
  const gmv = efMinVarAtTarget(cov, mu, null, lo, hi);       // null → чистый min-var, без штрафа за доходность
  if (!gmv.w) return { ok: false, reason: gmv.reason || 'solver_failed' };
  const gmvStats = efStats(gmv.w, mu, cov, rf);

  // верхний предел доходности при заданных границах весов: жадно по μ
  const order = mu.map((m, i) => [m, i]).sort((a, b) => b[0] - a[0]);
  const wMax = new Array(N).fill(lo);
  let left = 1 - lo * N;
  for (const [, i] of order) { const add = Math.min(hi - lo, left); wMax[i] += add; left -= add; if (left <= 1e-12) break; }
  const retMax = efStats(wMax, mu, cov, rf).ret;

  const points = [{ ...gmvStats, w: gmv.w, target: null }];
  let warm = gmv.w;
  const lowM = gmvStats.ret / EF.MONTHS_Y, highM = retMax / EF.MONTHS_Y;
  for (let k = 1; k <= EF.FRONTIER_PTS; k++) {
    const target = lowM + (highM - lowM) * (k / EF.FRONTIER_PTS);
    const sol = efMinVarAtTarget(cov, mu, target, lo, hi, warm);
    if (!sol.w) continue;
    warm = sol.w;                                            // следующая цель стартует отсюда
    if (!sol.ok) continue;                                   // цель не достигнута — точку не рисуем
    points.push({ ...efStats(sol.w, mu, cov, rf), w: sol.w, target: target * EF.MONTHS_Y });
  }
  points.sort((a, b) => a.vol - b.vol);
  // оставляем только недоминируемые: при большей волатильности доходность обязана расти
  const eff = [];
  let best = -Infinity;
  for (const p of points) { if (p.ret > best + 1e-12) { eff.push(p); best = p.ret; } }
  const tangency = eff.reduce((a, p) => (a === null || (p.sharpe != null && p.sharpe > a.sharpe) ? p : a), null);
  return { ok: eff.length >= 2, points: eff, gmv: points[0], tangency, retMax };
}

/** Сбор входов оптимизатора из позиций X-Ray.
 *
 *  Ряды у бумаг разной длины (недавние размещения), поэтому берём общий ХВОСТ —
 *  так одна свежая бумага не обрезает всю матрицу до нескольких месяцев. Если её
 *  включение роняет глубину истории ниже минимума, честнее исключить её и сказать
 *  об этом, чем считать ковариацию по 8 наблюдениям.
 *
 *  Покрытие считается ПО СТОИМОСТИ, а не по числу бумаг: исключение одной позиции
 *  на 40% портфеля — совсем не то же самое, что исключение трёх по 1%.
 */
/* ── Классификация инструмента: почему бумага в расчёте или вне него ──────────
   Раньше все исключения получали одну строку «нет истории доходностей», и три
   совершенно разные ситуации выглядели одинаково: недавнее размещение, снятая с
   торгов бумага и опечатка в коде. Пользователь не мог понять, что делать. */

// Подсказки по частым ошибкам в кодах. Это ПОДСКАЗКА, а не подмена: тикер никогда не
// заменяется молча (иначе можно незаметно создать дубль уже имеющейся позиции).
const PFX_SUGGEST = {
  MMK: 'MAGN',     // ММК торгуется под кодом MAGN
  YNDX: 'YDEX',    // прежний код Яндекса; continuity официально НЕ подтверждена — только подсказка
  FIVE: 'X5',      // прежняя расписка; ряды НЕ склеиваются
  RSTI: 'FEES',    // Россети реорганизованы; conversion ratio не подтверждён
};

const EF_STATUS = {
  supported_equity:    { label: 'обычная акция',     tone: 'good' },
  supported_preferred: { label: 'преф. акция',       tone: 'good' },
  supported_fund:      { label: 'биржевой фонд',     tone: 'good' },
  short_history:       { label: 'короткая история',  tone: 'warn' },
  insufficient_common_window: { label: 'мало общих месяцев', tone: 'warn' },
  history_empty:       { label: 'истории нет',       tone: 'warn' },
  corporate_action_unresolved: { label: 'корп. действие', tone: 'risk' },
  unknown_ticker:      { label: 'код не найден',     tone: 'risk' },
  invalid_data:        { label: 'некорректные данные', tone: 'risk' },
};

function efClassify(p) {
  const months = p._tr ? p._tr.length : 0;
  const t = p.t || null;
  const itype = t && t.instrument_type ? t.instrument_type : null;
  // instrument_type проставляется только бумагам из дополнительного универсума
  // (их тип спрашивается у ISS). Бумаги ML-универсума — по определению акции, поэтому
  // при наличии строки в data.json подписываем «акция», а не пустой прочерк.
  const typeLabel = itype === 'fund' ? 'фонд'
    : (itype === 'equity_preferred' ? 'преф'
      : (itype === 'equity_ordinary' ? 'акция' : (t ? 'акция' : '—')));

  if (!t) {
    const hint = PFX_SUGGEST[String(p.ticker || '').toUpperCase()];
    return {
      eligible: false, status: 'unknown_ticker', months, type: typeLabel,
      reason: 'такого кода нет в данных MOEX по нашему покрытию',
      action: hint ? `вероятно, имелся в виду ${hint} — проверьте код в брокерском отчёте и введите его сами` : 'проверьте актуальный торговый код',
      suggest: hint || null,
    };
  }
  if (!isNum(p.value) || p.value <= 0) {
    return { eligible: false, status: 'invalid_data', months, type: typeLabel,
      reason: 'нулевая или некорректная стоимость позиции', action: 'проверьте количество и цену' };
  }
  if (p._anomaly) {
    return { eligible: false, status: 'corporate_action_unresolved', months, type: typeLabel,
      reason: 'в ряду цен split-like разрыв (нераспознанное корпоративное действие)',
      action: 'позиция учтена в стоимости и P&L, но исключена из риск-метрик до корректировки ряда' };
  }
  if (!months) {
    return { eligible: false, status: 'history_empty', months, type: typeLabel,
      reason: 'ряда месячных доходностей нет (недавнее размещение либо нет данных)',
      action: 'позиция учтена в стоимости и P&L; в оптимизацию войдёт, когда наберётся история' };
  }
  if (months < EF.MIN_OBS) {
    return { eligible: false, status: 'short_history', months, type: typeLabel,
      reason: `доступно ${months} мес. из необходимых ${EF.MIN_OBS}`,
      action: 'позиция учтена в стоимости и P&L, но не участвует в оптимизации' };
  }
  const ok = itype === 'fund' ? 'supported_fund'
    : (itype === 'equity_preferred' ? 'supported_preferred' : 'supported_equity');
  return { eligible: true, status: ok, months, type: typeLabel, reason: '', action: 'включена автоматически' };
}

function efBuildInputs(positions) {
  const total = positions.reduce((s, p) => s + (isNum(p.value) ? p.value : 0), 0);
  if (!(total > 0)) return { ok: false, reason: 'no_value' };

  const excluded = [];
  const usable = [];
  positions.forEach((p) => {
    const cls = efClassify(p);
    if (cls.eligible) { usable.push(p); return; }
    excluded.push({ ticker: p.ticker, value: isNum(p.value) ? p.value : 0, ...cls });
  });
  if (usable.length < 2) return { ok: false, reason: 'too_few_assets', excluded, total };

  // общий хвост: максимизируем покрытие по стоимости при глубине ≥ MIN_OBS
  const byLen = [...usable].sort((a, b) => b._tr.length - a._tr.length);
  let best = null;
  for (let k = 2; k <= byLen.length; k++) {
    const subset = byLen.slice(0, k);
    const depth = Math.min(...subset.map((p) => p._tr.length));
    if (depth < EF.MIN_OBS) break;
    const cov = subset.reduce((s, p) => s + p.value, 0) / total;
    if (!best || cov > best.cov) best = { subset, depth, cov };
  }
  if (!best) return { ok: false, reason: 'insufficient_history', excluded, total };

  best.subset.forEach(() => {});
  usable.filter((p) => !best.subset.includes(p)).forEach((p) => {
    const months = p._tr ? p._tr.length : 0;
    excluded.push({
      ticker: p.ticker, value: p.value, status: 'insufficient_common_window', months,
      type: (p.t && p.t.instrument_type === 'fund') ? 'фонд' : 'акция',
      reason: `после выравнивания общего окна остаётся ${months} мес. — меньше ${EF.MIN_OBS}`,
      action: 'позиция учтена в стоимости и P&L; включение сузило бы окно всем бумагам',
    });
  });

  const T = best.depth;
  const tickers = best.subset.map((p) => p.ticker);
  // X[t][i]: берём ПОСЛЕДНИЕ T наблюдений каждой бумаги — общий календарный хвост
  const X = [];
  for (let t = 0; t < T; t++) {
    X.push(best.subset.map((p) => p._tr[p._tr.length - T + t]));
  }
  const subTotal = best.subset.reduce((s, p) => s + p.value, 0);
  return {
    ok: true, tickers, X, obs: T,
    weights: best.subset.map((p) => p.value / subTotal),   // веса ВНУТРИ eligible-подмножества
    values: best.subset.map((p) => p.value),
    positions: best.subset,
    coverage: best.cov * 100,
    excluded, total, subTotal,
    confidence: T >= EF.PREF_OBS ? 'high' : 'reduced',
  };
}

/** Оборот: половина суммы модулей изменений весов — доля капитала, которую надо
 *  перекласть (продажи и покупки не считаются дважды). */
function efTurnover(wCur, wNew) {
  let s = 0;
  for (let i = 0; i < wCur.length; i++) s += Math.abs(wNew[i] - wCur[i]);
  return s / 2;
}

/** Разовые издержки. Годовой доходностью НЕ являются: чтобы сравнить с выигрышем,
 *  комиссию раскладывают на горизонт удержания (§ методологии). */
function efCosts(turnover, capital, feeBps) {
  const rub = turnover * capital * (feeBps / 10000) * 2;   // ×2: продажа + покупка
  return { rub, pctOfCapital: capital > 0 ? (rub / capital) * 100 : null, feeBps };
}

/** Перевод теоретических весов в исполнимый портфель: округление вниз по лотам.
 *  Теоретические веса нельзя купить — биржа торгует лотами, и остаток уходит в кэш. */
function efLotRound(tickers, targetW, positions, capital) {
  const rows = [];
  let spent = 0;
  tickers.forEach((tk, i) => {
    const p = positions[i];
    const price = (p.t && isNum(p.t.price) && p.t.price > 0) ? p.t.price : p.current_price;
    const lot = Math.max(1, Math.round((p.t && p.t.lot_size) || 1));
    if (!isNum(price) || price <= 0) { rows.push({ ticker: tk, ok: false }); return; }
    const lotValue = price * lot;
    const targetRub = targetW[i] * capital;
    const lots = Math.floor(targetRub / lotValue);
    const actual = lots * lotValue;
    spent += actual;
    rows.push({ ticker: tk, ok: true, price, lot, lots, quantity: lots * lot, targetRub, actualRub: actual });
  });
  const cash = capital - spent;
  const wExec = rows.map((r) => (r.ok && spent > 0 ? r.actualRub / capital : 0));
  return { rows, cash, cashPct: capital > 0 ? (cash / capital) * 100 : 0, wExec };
}

/** Устойчивость сценария: пересчёт на двух половинах истории.
 *  Если веса разъезжаются между подпериодами, «оптимум» держится на конкретной
 *  выборке, а не на структуре рынка — это надо показать, а не прятать. */
function efStability(X, lo, hi, rf) {
  const T = X.length;
  if (T < EF.MIN_OBS * 2) return { ok: false, reason: 'short_history' };
  const half = Math.floor(T / 2);
  const run = (rows) => {
    const { cov } = efLedoitWolf(rows);
    const { mu } = efJamesStein(rows, cov);
    const f = efFrontier(mu, cov, lo, hi, rf);
    return f.ok && f.tangency ? f.tangency.w : null;
  };
  const a = run(X.slice(0, half)), b = run(X.slice(half));
  if (!a || !b) return { ok: false, reason: 'solver_failed' };
  const l1 = a.reduce((s, v, i) => s + Math.abs(v - b[i]), 0) / 2;   // 0 = идентичны, 1 = не пересекаются
  return {
    ok: true, divergence: l1,
    grade: l1 < 0.15 ? 'high' : (l1 < 0.35 ? 'moderate' : 'low'),
    label: l1 < 0.15 ? 'высокая' : (l1 < 0.35 ? 'умеренная' : 'низкая'),
  };
}

/** Полный анализ: граница + сценарии + Efficiency Gap + устойчивость.
 *
 *  Возвращает СТАТУС, а не молча пустой результат: недостаточное покрытие, короткая
 *  история и отказ солвера — разные ситуации, и пользователю показывается разное.
 *  Убедительно выглядящую границу на плохих данных не рисуем (§ политика покрытия).
 */
function efAnalyze(positions, opts) {
  const o = Object.assign({ cap: 0.35, feeBps: 5, rf: 0 }, opts || {});
  const inp = efBuildInputs(positions);
  if (!inp.ok) return { status: 'insufficient_data', reason: inp.reason, excluded: inp.excluded || [] };
  if (inp.coverage < EF.COV_MIN) {
    return { status: 'insufficient_data', reason: 'low_coverage', coverage: inp.coverage, excluded: inp.excluded };
  }
  const N = inp.tickers.length;
  const hi = Math.max(o.cap, 1 / N + 1e-9);       // потолок не может быть ниже равных весов — иначе задача пуста
  const { cov, shrinkage } = efLedoitWolf(inp.X);
  const { mu, lambda, anchor } = efJamesStein(inp.X, cov);

  const frontier = efFrontier(mu, cov, 0, hi, o.rf);
  if (!frontier.ok) return { status: 'solver_failed', reason: frontier.reason || 'frontier_empty', excluded: inp.excluded };

  const current = Object.assign({ w: inp.weights }, efStats(inp.weights, mu, cov, o.rf));
  const capital = inp.subTotal;

  const mk = (name, label, point, note) => {
    if (!point) return null;
    const turnover = efTurnover(inp.weights, point.w);
    return {
      name, label, note,
      ret: point.ret, vol: point.vol, sharpe: point.sharpe, w: point.w,
      turnover, costs: efCosts(turnover, capital, o.feeBps),
      exec: efLotRound(inp.tickers, point.w, inp.positions, capital),
    };
  };

  // «Сохранить доходность, снизить риск»: ближайшая точка границы с доходностью
  // не ниже текущей и минимальной волатильностью
  const sameRet = frontier.points.filter((p) => p.ret >= current.ret - 1e-9)
    .reduce((a, p) => (a === null || p.vol < a.vol ? p : a), null);
  // «Сохранить риск, поднять доходность»
  const sameRisk = frontier.points.filter((p) => p.vol <= current.vol + 1e-9)
    .reduce((a, p) => (a === null || p.ret > a.ret ? p : a), null);

  // Если НИ ОДИН допустимый портфель не обгоняет безрисковую ставку, максимизация
  // Sharpe вырождается: при отрицательной премии (r−rf)/σ РАСТЁТ с ростом σ, и
  // «оптимум» уезжает в самый рискованный угол границы. Показывать такую точку как
  // цель — вводить в заблуждение, поэтому сценарий снимается, а причина называется.
  const noPremium = !frontier.tangency || !isNum(frontier.tangency.sharpe) || frontier.tangency.sharpe <= 0;

  const scenarios = [
    mk('gmv', 'Минимальный риск', frontier.gmv, 'Глобальный минимум волатильности при заданных ограничениях.'),
    noPremium ? null : mk('sharpe', 'Максимальный Sharpe', frontier.tangency, 'Лучшее отношение премии к риску среди допустимых портфелей.'),
    mk('same_return', 'Сохранить доходность', sameRet, 'Та же оценочная доходность при меньшем модельном риске.'),
    noPremium ? null : mk('same_risk', 'Сохранить риск', sameRisk, 'Тот же модельный риск при большей оценочной доходности.'),
  ].filter(Boolean);

  // Efficiency Gap — насколько ниже могла быть волатильность при сопоставимой доходности
  const gap = sameRet ? {
    volNow: current.vol, volBest: sameRet.vol,
    deltaPp: (current.vol - sameRet.vol) * 100,
    relPct: current.vol > 0 ? ((current.vol - sameRet.vol) / current.vol) * 100 : null,
    dominated: sameRet.vol < current.vol - 1e-6,
  } : { dominated: false, deltaPp: 0, relPct: 0, volNow: current.vol, volBest: current.vol };

  return {
    status: 'ok',
    tickers: inp.tickers, obs: inp.obs, coverage: inp.coverage, confidence: inp.confidence,
    excluded: inp.excluded, capital, cap: hi, rf: o.rf,
    current, frontier, scenarios, gap, noPremium,
    stability: efStability(inp.X, 0, hi, o.rf),
    model: { shrinkage, lambda, anchor: anchor * EF.MONTHS_Y, returnBasis: 'total_return_monthly' },
    warnings: [].concat(
      inp.coverage < EF.COV_FULL ? [`покрытие ${ru(inp.coverage, 0)}% стоимости портфеля — часть позиций вне расчёта`] : [],
      inp.confidence === 'reduced' ? [`история ${inp.obs} мес. (меньше ${EF.PREF_OBS}) — пониженная уверенность`] : [],
      noPremium ? [`ни один допустимый портфель не даёт положительной премии к безрисковой ставке (${ru((o.rf || 0) * 100, 1)}% годовых) — сценарии по Sharpe сняты как экономически бессмысленные`] : [],
    ),
  };
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
  else if (method === 'optms') w = maxSharpe(cov, cand.map((t) => (isNum(t.quality_rank_pct) ? t.quality_rank_pct / 100 : 0)));  // RU Quality tilt
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

function portfolioMetrics(items, capital, preserveCash = false) {
  let gy = 0, stab = 0, wsum = 0;
  const sec = {};
  items.forEach((it) => {
    const y = isNum(it.t.dividend_yield_expected) ? it.t.dividend_yield_expected
      : (isNum(it.t.dividend_yield_if_paid) ? it.t.dividend_yield_if_paid : null);
    if (y != null) { gy += it.w * y; wsum += it.w; }
    if (isNum(it.t.stability_score)) stab += it.w * it.t.stability_score;
    sec[it.sector] = (sec[it.sector] || 0) + it.w;
  });
  const grossY = wsum ? (preserveCash ? gy : gy / wsum) : null;
  const netY = grossY != null ? grossY * NET_OF_TAX : null;
  return {
    grossY, netY, stability: stab,
    incomeNet: (netY != null && capital) ? capital * netY / 100 : null,
    cashWeight: preserveCash ? Math.max(0, 1 - items.reduce((sum, item) => sum + item.w, 0)) : 0,
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
  fetch(dataURL('returns.json'))   // cache-bust: уникальный URL обходит любой кэш/404
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

function marlamovBacktestHTML(backtest) {
  if (!backtest || !backtest.metrics) {
    const reason = backtest && backtest.reason ? backtest.reason : 'Исторический baseline пока недоступен.';
    return `<div class="pf-backtest pf-backtest-unavailable"><b>Бэктест стратегии не показан</b><span>${esc(reason)}</span></div>`;
  }
  const metrics = backtest.metrics;
  const benchmark = backtest.benchmark || {};
  const period = backtest.period || {};
  const pct = (value, digits = 1, signed = false) => isNum(value)
    ? `${signed && value > 0 ? '+' : ''}${ru(value * 100, digits)}%`
    : '—';
  const num = (value, digits = 2) => isNum(value) ? ru(value, digits) : '—';
  const metric = (label, value, tone = '') => `<div class="pf-backtest-cell"><span>${esc(label)}</span><b class="${tone}">${esc(value)}</b></div>`;
  const curve = (backtest.series || []).map((row) => row.strategy).filter(isNum);
  const firstLimitation = (backtest.limitations || [])[0] || '';
  const methodology = backtest.methodology || {};
  return `<div class="pf-backtest">
    <div class="pf-backtest-head">
      <div><b>Исторический research backtest</b><span>${esc(period.start || '—')} — ${esc(period.end || '—')} · ${metrics.months || 0} мес.</span></div>
      <span class="pf-backtest-state">лагированные данные · не point-in-time</span>
    </div>
    <div class="pf-backtest-grid">
      ${metric('CAGR', pct(metrics.cagr), metrics.cagr >= 0 ? 'g' : 'r')}
      ${metric('MCFTR CAGR', pct(benchmark.cagr))}
      ${metric('К RFR', pct(metrics.excess_return_vs_rfr, 1, true), metrics.excess_return_vs_rfr >= 0 ? 'g' : 'r')}
      ${metric('К MCFTR', pct(metrics.excess_return_vs_mcftr, 1, true), metrics.excess_return_vs_mcftr >= 0 ? 'g' : 'r')}
      ${metric('Волатильность', pct(metrics.volatility))}
      ${metric('Sharpe', num(metrics.sharpe))}
      ${metric('Sortino', num(metrics.sortino))}
      ${metric('Max drawdown', pct(metrics.max_drawdown), 'r')}
      ${metric('Calmar', num(metrics.calmar))}
      ${metric('Alpha к MCFTR', pct(metrics.alpha_vs_mcftr, 1, true))}
      ${metric('Beta к MCFTR', num(metrics.beta_vs_mcftr))}
      ${metric('Downside capture', pct(metrics.downside_capture))}
      ${metric('Hit rate', pct(metrics.hit_rate))}
      ${metric('Profit factor', num(metrics.profit_factor))}
      ${metric('Средний turnover', pct(metrics.average_turnover))}
      ${metric('Ребалансов', String(metrics.rebalances || 0))}
    </div>
    ${curve.length ? `<div class="pf-backtest-curve">${areaSpark(curve, CH.teal, 'marlamov-backtest')}</div>` : ''}
    <div class="pf-backtest-method">${esc(methodology.selection || '')} · ${esc(methodology.weighting || '')} · ${esc(methodology.rebalance || '')}.</div>
    <div class="pf-backtest-note">${esc(firstLimitation)}</div>
  </div>`;
}

function marlamovEntryGateHTML(items, backtest) {
  const thresholdPct = MARLAMOV && MARLAMOV.meta && isNum(MARLAMOV.meta.entry_threshold) ? MARLAMOV.meta.entry_threshold * 100 : 3;
  const threshold = thresholdPct / 100;
  const passed = items.filter((item) => item.strategy && isNum(item.strategy.expected_net_spread) && item.strategy.expected_net_spread >= threshold).length;
  const tone = passed > 0 ? 'good' : 'risk';
  const conclusion = passed > 0
    ? `${passed} бумаг проходят порог; каждый занял один из ${+document.getElementById('pf-n').value || 10} слотов, остальные остаются cash/RFR.`
    : 'Подходящих бумаг нет; модельный состав не сформирован.';
  return `<div class="pf-entry-gate ${tone}"><b>Фильтр модельного состава</b><span>Expected net yield − RFR net ≥ +${ru(thresholdPct, 1)} п.п. · ${esc(conclusion)}</span></div>`;
}

function renderPortfolio() {
  const out = document.getElementById('pf-out');
  if (!out) return;
  syncWeightControl();                               // синхронизируем доступность «Взвешивания»
  if (!PF_RETURNS) loadReturns(renderPortfolio);     // подгрузим историю и перерисуем с риск-метриками
  const method = document.getElementById('pf-method').value;
  if (method === 'quality' && !QUALITY) {
    out.innerHTML = '<p class="muted" style="padding:8px">Загрузка RU Quality cross-section…</p>';
    loadQuality((err) => {
      if (err) out.innerHTML = '<p class="muted" style="padding:8px">RU Quality временно недоступен.</p>';
      else renderPortfolio();
    });
    return;
  }
  if (method === 'marlamov' && !MARLAMOV) {
    out.innerHTML = '<p class="muted" style="padding:8px">Загрузка форвардного сигнала и бэктеста…</p>';
    loadMarlamov((err) => {
      if (err) out.innerHTML = '<p class="muted" style="padding:8px">Форвардный слой временно недоступен.</p>';
      else renderPortfolio();
    });
    return;
  }
  const opts = {
    n: +document.getElementById('pf-n').value,
    weight: document.getElementById('pf-weight').value,
    cap: +document.getElementById('pf-cap').value,
    seccap: +document.getElementById('pf-seccap').value,
  };
  const capital = +document.getElementById('pf-capital').value || 0;
  const items = buildPortfolio(method, opts);
  if (!items || !items.length) {
    if (method === 'quality') {
      const analysis = QUALITY_LAST_ANALYSIS || qualityCandidateAnalysis(qualityPortfolioConfig(opts));
      out.innerHTML = qualityEmptyStateHTML(analysis.config || qualityPortfolioConfig(opts), analysis);
      wireQualityEmptyActions(out);
      return;
    }
    let msg = method === 'marlamov'
      ? 'По ожидаемой чистой дивдоходности ни одна бумага не прошла порог. Модельный состав пуст: 100% капитала остаётся в cash/RFR; кандидаты остаются в таблице наблюдения ниже.'
      : 'Недостаточно подходящих бумаг для корзины.';
    if (method.startsWith('opt')) {
      if (!PF_RETURNS) msg = 'Загрузка истории…';
      else if (PF_RETURNS.failed) msg = 'Не удалось загрузить историю (returns.json) — обнови страницу (Cmd+Shift+R).';
      else if (!PF_RETURNS.months.length) msg = 'История недоступна.';
    }
    out.innerHTML = `<p class="muted" style="padding:8px">${msg}</p>`;
    return;
  }
  PF_LAST = { items, capital };
  const m = portfolioMetrics(items, capital, method === 'marlamov');
  const risk = portfolioRisk(items, m.grossY);
  const bt = FACTOR_BACKTEST[method];
  const strategyBacktest = method === 'marlamov' && MARLAMOV ? MARLAMOV.backtest : null;
  let qualityCash = capital;
  if (method === 'quality' && capital) {
    items.forEach((item) => {
      const lotSize = Math.max(1, Math.round(item.t.lot_size || item.q.lot_size || 1));
      const lotValue = item.t.price * lotSize;
      const targetRub = capital * item.w;
      const lots = lotValue > 0 ? Math.floor(targetRub / lotValue) : 0;
      item._lot = { lotSize, lots, quantity: lots * lotSize, actualRub: lots * lotValue, targetRub };
      qualityCash -= item._lot.actualRub;
    });
  }
  const rows = items.map((it, i) => {
    const alloc = method === 'quality' && it._lot ? it._lot.actualRub : (capital ? capital * it.w : null);
    const y = isNum(it.t.dividend_yield_expected) ? it.t.dividend_yield_expected : it.t.dividend_yield_if_paid;
    const inc = (alloc && isNum(y)) ? alloc * y / 100 * NET_OF_TAX : null;
    const strategyCells = method === 'marlamov'
      ? `<td class="tnum">${it.strategy && isNum(it.strategy.expected_net_yield) ? ru(it.strategy.expected_net_yield * 100, 1) + '%' : mdash}</td>
        <td class="tnum ${it.strategy && isNum(it.strategy.expected_net_spread) ? (it.strategy.expected_net_spread >= 0 ? 'pf-spread-up' : 'pf-spread-down') : ''}">${it.strategy && isNum(it.strategy.expected_net_spread) ? `${it.strategy.expected_net_spread >= 0 ? '+' : ''}${ru(it.strategy.expected_net_spread * 100, 1)} п.п.` : mdash}</td>`
      : method === 'quality'
        ? `<td class="tnum quality-score">${it.q && isNum(it.q.sector_rank_pct) ? ru(it.q.sector_rank_pct, 0) : mdash}</td>`
        : `<td class="left">${verdictChip(it.t.verdict, false)}</td>`;
    return `<tr><td class="left">${i + 1}</td><td class="left">${instrumentIdentityHTML(it.ticker, it.t && it.t.name, instrumentTypeHint(it.t), 'sm')}<span class="instrument-sector">${esc(it.sector)}</span></td>
      <td class="tnum">${ru(it.w * 100, 1)}%</td>
      <td class="tnum">${alloc != null ? fmtRub(Math.round(alloc)) : mdash}</td>
      <td class="tnum">${inc != null ? fmtRub(Math.round(inc)) : mdash}</td>
      ${method === 'quality' ? `<td class="tnum">${it._lot ? ru(it._lot.lots, 0) : mdash}</td><td class="tnum">${it._lot ? ru(it._lot.quantity, 0) : mdash}</td>` : ''}
      ${strategyCells}</tr>`;
  }).join('');
  const secBars = m.sectors.map(([s, w]) =>
    `<div class="pf-secrow"><span>${esc(s)}</span><span class="pf-secbar"><i style="width:${(w * 100).toFixed(0)}%"></i></span><span class="tnum">${ru(w * 100, 0)}%</span></div>`).join('');
  out.innerHTML = `<div class="pf-summary">
      <div class="pf-card"><span class="lbl">${method === 'marlamov' ? 'Ожидаемая доходность состава' : 'Доходность (на руки)'}</span><b class="tnum">${m.netY != null ? ru(m.netY, 1) + '%' : mdash}</b><span class="muted">${method === 'marlamov' ? `после НДФЛ · cash ${ru(m.cashWeight * 100, 0)}%` : `до НДФЛ ${m.grossY != null ? ru(m.grossY, 1) + '%' : '—'}`}</span></div>
      <div class="pf-card"><span class="lbl">Устойчивость портфеля</span><b class="tnum">${ru(m.stability * 100, 0)}%</b></div>
      <div class="pf-card"><span class="lbl">Доход в год (на руки)</span><b class="tnum">${m.incomeNet != null ? fmtRub(Math.round(m.incomeNet)) : mdash}</b><span class="muted">на ${capital ? fmtRub(capital) : '—'}</span></div>
      <div class="pf-card"><span class="lbl">Бумаг</span><b class="tnum">${items.length}</b></div>
    </div>
    ${bt ? `<div class="pf-bt muted">📈 ${esc(bt.label)}</div>` : ''}
    ${method === 'quality' && document.getElementById('quality-allow-low').checked ? '<div class="quality-notice">Research preview включает low-confidence строки. Это не строгая point-in-time корзина.</div>' : ''}
    ${method === 'quality' && capital ? `<div class="pf-reb muted">Покупка округлена вниз до лотов MOEX · остаток cash: <b>${fmtRub(Math.max(0, qualityCash))}</b></div>` : ''}
    ${method === 'marlamov' ? marlamovEntryGateHTML(items, strategyBacktest) : ''}
    ${method === 'marlamov' ? marlamovBacktestHTML(strategyBacktest) : ''}
    ${REBALANCE[method] ? `<div class="pf-reb muted">🔁 Рекомендуемый ребаланс: <b>${esc(REBALANCE[method])}</b></div>` : ''}
    ${riskPanelHTML(risk)}
      <div class="pf-grid"><div class="pf-holdings"><table class="pf-tbl"><thead><tr><th class="left">#</th><th class="left">Бумага</th><th>Вес</th><th>${method === 'quality' ? 'Факт ₽' : 'Сумма'}</th><th>Доход/год</th>${method === 'quality' ? '<th>Лотов</th><th>Штук</th><th>Quality сектор</th>' : (method === 'marlamov' ? '<th>Ожид. net yield</th><th>Спред к RFR net</th>' : '<th class="left">Вердикт</th>')}</tr></thead><tbody>${rows}</tbody></table>
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
      <div class="mp-empty-ico" aria-hidden="true">
        <svg viewBox="0 0 48 48"><path d="M24 24V6a18 18 0 1 0 18 18z"/><path d="M28 20h16A18 18 0 0 0 28 4z"/></svg>
      </div>
      <b>Добавьте портфель для анализа</b>
      <span>Введите позиции сверху (поиск бумаги · количество · цена покупки) или нажмите «Пример».
      Получите: стоимость и P&L, риск (VaR/CVaR, beta), дивидендный поток, концентрацию, сценарии и memo.</span>
      <div class="mp-empty-cta">
        <button class="btn" type="button" id="mp-empty-sample">Заполнить пример</button>
      </div>
      <em>Расчёт локальный, в браузере: состав портфеля никуда не отправляется. Не индивидуальная инвестиционная рекомендация. Импорт CSV и ручной ввод — в редакторе выше.</em>
    </div>`;
    const es = document.getElementById('mp-empty-sample');
    if (es) es.addEventListener('click', () => { input.value = MY_PORTFOLIO_SAMPLE; renderMyPortfolio(); });
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
  // «Нет модельного покрытия» и «нет истории» — РАЗНЫЕ факты, и раньше они были склеены
  // в одно предупреждение. После расширения универсума истории (supplementary_universe.json)
  // SNGS/SNGSP получили 90 месячных наблюдений и участвуют в риск-метриках, хотя модельных
  // полей у них по-прежнему нет. Утверждать «исключена из VaR/CVaR» стало неправдой.
  const noModel = c.positions.filter((p) => p.t && p.t.status === 'no_model_coverage');
  const noModelWithHist = noModel.filter((p) => p._tr && p._tr.length).map((p) => p.ticker);
  const noModelNoHist = noModel.filter((p) => !p._tr || !p._tr.length).map((p) => p.ticker);
  if (noModelWithHist.length) c._warnings.push({ tone: 'warn', msg: `${noModelWithHist.join(', ')}: цена и история доходностей есть (рынок MOEX) — риск-метрики считаются. Нет модельных полей: прогноз дивиденда, риск невыплаты и вердикт недоступны.` });
  if (noModelNoHist.length) c._warnings.push({ tone: 'warn', msg: `${noModelNoHist.join(', ')}: цена есть (рынок MOEX), истории доходностей нет — бумага исключена из VaR/CVaR и дивидендной модели.` });
  if (c._divSuspect && c._divSuspect.length) c._warnings.push({ tone: 'warn', msg: `${c._divSuspect.join(', ')}: дивиденд в данных выглядит завышенным (возможно, до сплита) — исключён из ожидаемого дивпотока, требует проверки.` });
  const shortHist = c.positions.filter((p) => p._tr && p._tr.length < 24).map((p) => p.ticker);
  if (shortHist.length) c._warnings.push({ tone: 'warn', msg: `Короткая история (<24 мес): ${shortHist.join(', ')} — риск-метрики low confidence` });
  if (c.pf && c.pf.covered < 0.9) c._warnings.push({ tone: 'warn', msg: `Risk metrics рассчитаны по ${Math.round(c.pf.covered * 100)}% веса портфеля. Бумаги без чистой истории исключены из VaR/CVaR.` });
  if (!c.bench) c._warnings.push({ tone: 'risk', msg: 'MCFTR не выровнен — alpha/beta/tracking error/active VaR недоступны' });
  if (!c.rf.ok) c._warnings.push({ tone: 'warn', msg: 'RFR недоступна — Sharpe/Sortino/alpha считаются без безрисковой ставки' });
  PFX_STATE = c;
  myPortfolioSave(rows);
  out.innerHTML = pfxRenderHTML(c);
  pfxWireDashboard(c);   // copy/export — стабильные кнопки, вяжем один раз
  document.querySelectorAll('.pfx-tab').forEach((t) => t.addEventListener('click', () => pfxSelectTab(t.dataset.pfxTab)));
  const savedTab = uiStateLoad().pfxTab;
  pfxSelectTab(savedTab || 'summary');   // рендер панели активной вкладки + её графики + daily-risk (для «Риск»)
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
// ── «Итог инвесткомитета» — детерминированный Level-1 вывод (Bible I/IV): здоровье +
// сильные стороны + риски + что проверить. Всё из уже посчитанных метрик, без AI/бэкенда.
function pfxCommitteeSummary(c) {
  const risk = pfxRiskScore(c).score;
  const health = Math.max(0, Math.min(100, 100 - risk));
  const hTone = health >= 66 ? 'good' : health >= 40 ? 'warn' : 'risk';
  const hWord = health >= 66 ? 'Здоровый' : health >= 40 ? 'Сбалансированный, есть риски' : 'Повышенный риск';
  // сильные стороны
  const str = [];
  if (c.effN >= 5 && c.top3 < 0.5) str.push(`Диверсификация: эффективно ${ru(c.effN, 1)} бумаг, top-3 ${PN(c.top3, 0)}`);
  if (c._corr && c._corr.ok && c._corr.avg != null && c._corr.avg < 0.4) str.push(`Слабая связанность бумаг (средняя корреляция ${(c._corr.avg >= 0 ? '+' : '−') + Math.abs(Math.round(c._corr.avg * 100))}%)`);
  if (c.capm && c.capm.ok && isNum(c.capm.alphaAnn) && c.capm.alphaAnn > 0.02) str.push(`Опережает MCFTR: alpha +${ru(c.capm.alphaAnn * 100, 1)}% годовых (историч.)`);
  if (c.perf && isNum(c.perf.sharpe) && c.perf.sharpe > 0.8) str.push(`Хорошее risk-adjusted: Sharpe ${ru(c.perf.sharpe, 2)}`);
  if (isNum(c.grossYield) && c.rf.ok && c.grossYield > c.rf.annual) str.push(`Дивдоходность ${PU(c.grossYield, 1)}% выше безриска`);
  if (c.capm && c.capm.ok && isNum(c.capm.beta) && c.capm.beta < 0.9) str.push(`Защитный профиль: beta ${ru(c.capm.beta, 2)} < 1`);
  if (c.dq && c.dq.score >= 78) str.push(`Высокое качество данных (${c.dq.score}/100)`);
  if (!str.length) str.push('Явных сильных сторон по текущим правилам не выделено');
  // риски (переиспользуем топ-3)
  const risks = pfxTopRisks(c).map((r) => r.text);
  // что проверить (действия)
  const watch = [];
  if (c.top3 > 0.5) watch.push(`Снизить концентрацию top-3 (${PN(c.top3, 0)} портфеля)`);
  if (c.div && c.div.traps && c.div.traps.length) watch.push(`Проверить дивидендную устойчивость: ${c.div.traps.slice(0, 3).join(', ')}`);
  if (c.dq && c.dq.lowWeight > 0.2) watch.push(`${PN(c.dq.lowWeight, 0)} веса — бумаги с неполными данными/историей`);
  if (c.capm && c.capm.ok && isNum(c.capm.beta) && c.capm.beta > 1.25) watch.push('Высокая beta — в падениях просадка сильнее рынка; проверить долю high-beta');
  if (c._rb && c._rb.ok && c._rb.substituted) watch.push('Ставка выше дивидендов — сравнить с облигациями (раздел «Ставка и облигации»)');
  if (!watch.length) watch.push('Критичных проверок по текущим правилам нет — периодически сверять веса и cut risk');
  const col = (title, items, tone) => `<div class="pfx-cs-col pfx-cs-${tone}"><h4>${title}</h4><ul>${items.slice(0, 3).map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`;
  return `<div class="pfx-committee">
    <div class="pfx-cs-head">
      <div class="pfx-cs-score pfx-${hTone}"><b>${health}</b><span>/100</span></div>
      <div class="pfx-cs-verdict"><span class="pfx-cs-eyebrow">Итог инвесткомитета</span><h3>${hWord}</h3>
        <p>${esc(pfxDiagnosis(c))}</p></div>
    </div>
    <div class="pfx-cs-cols">
      ${col('Сильные стороны', str, 'good')}
      ${col('Главные риски', risks.length ? risks : ['Критичных рисков не выделено'], 'risk')}
      ${col('Что проверить', watch, 'warn')}
    </div>
    <div class="pfx-note muted">Здоровье = 100 − композитный risk score (концентрация, beta, VaR, cut risk, качество данных). Rule-based синтез уже посчитанных метрик, не ИИ и не ИИР.</div>
  </div>`;
}

// Вкладки X-Ray (редизайн, Итерация 4, §12). Математика в модулях не меняется — только группировка.
const PFX_TAB_LIST = [
  { id: 'summary', label: 'Резюме' },
  { id: 'holdings', label: 'Состав' },
  { id: 'returns', label: 'Доходность' },
  { id: 'risk', label: 'Риск' },
  { id: 'dividends', label: 'Дивиденды' },
  { id: 'scenarios', label: 'Сценарии' },
  { id: 'memo', label: 'Memo' },
];

// полный KPI-грид (21 метрика) — вкладка «Резюме»
function pfxKpiGrid(c) {
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
  return g;
}

// компактная headline-лента (≤6) — всегда на виду над вкладками; на мобиле горизонтальный скролл
function pfxHeadlineKpis(c) {
  const pnlAbs = c.total - c.cost, pnlPct = c.cost > 0 ? c.total / c.cost - 1 : null;
  const rscore = pfxRiskScore(c);
  const g = [];
  g.push(pfxKpi('Стоимость', rub0(c.total)));
  g.push(pfxKpi('Нереализ. P&L', rub0(pnlAbs), PP(pnlPct), pnlAbs >= 0 ? 'good' : 'risk'));
  g.push(pfxKpi('Ожид. дивдоход/год', c.div ? rub0(c.div.baseIncome) : NA, `≈ ${PU(c.grossYield, 1)}% gross`));
  g.push(pfxKpi('Beta к MCFTR', c.capm && c.capm.ok ? PU(c.capm.beta, 2) : NA, c.capm && c.capm.ok ? pfxBetaBucket(c.capm.beta) : ''));
  g.push(pfxKpi('VaR 95% (мес)', c.vaR && c.vaR.ok ? PN(c.vaR.hist95) : NA, c.vaR && c.vaR.ok ? rub0(c.vaR.hist95 * c.total) : '', 'risk'));
  g.push(pfxKpi('Risk Score', rscore.score + '/100', rscore.label, rscore.score >= 66 ? 'risk' : rscore.score >= 40 ? 'warn' : 'good'));
  return `<div class="pfx-kpistrip">${g.join('')}</div>`;
}

function pfxTabNav() {
  return `<div class="pfx-tabs" role="tablist" aria-label="Разделы анализа портфеля">${
    PFX_TAB_LIST.map((t) => `<button class="pfx-tab" type="button" role="tab" data-pfx-tab="${t.id}" aria-selected="false">${esc(t.label)}</button>`).join('')
  }</div>`;
}

// HTML активной вкладки — переиспользует существующие модули без изменения математики
function pfxTabHTML(c, tab) {
  const bp = (c.bench && c.perf) ? pfxPerf(c.bench, c.rf.monthly) : null;
  switch (tab) {
    case 'summary':
      return `<div class="pfx-grid">${pfxKpiGrid(c).join('')}</div>`;
    case 'holdings':
      return pfxDetails('Position Diagnostics', '(по каждой бумаге)', pfxPosHTML(c), true)
        + pfxDetails('Allocation / Exposure', '(разрезы книги + лимиты)', pfxAllocHTML(c))
        + pfxDetails('Атрибуция доходности', '(вклад бумаг и секторов в фактический P&L)', pfxAttrHTML(c))
        + pfxDetails('Возможности и внимание', '(потенциал к справедливой цене · флаги внимания)', pfxOppHTML(c));
    case 'returns':
      return pfxDetails('Performance vs MCFTR', '(total return, риск-adjusted)', pfxPerfHTML(c, bp), true)
        + pfxDetails('Alpha / Beta / Risk-adjusted', '(CAPM-регрессия к MCFTR)', pfxCapmHTML(c));
    case 'risk':
      return pfxDetails('Дневной риск (VaR / CVaR / волатильность)', '(по дневным данным MOEX · краткосрочный горизонт)',
          '<div id="pfx-daily-risk"><div class="pulse-loading muted">Загрузка дневных данных портфеля…</div></div>', true)
        + pfxDetails('Долгосрочный риск: VaR / CVaR (месячная база)', '(многолетняя месячная история — иной горизонт)', pfxVaRHTML(c))
        + pfxDetails('Risk Budget', '(вклад бумаг в риск, component VaR)', pfxRiskBudgetHTML(c))
        + pfxDetails('Корреляционная матрица', '(как связаны бумаги — диверсификация)', pfxCorrHTML(c))
        + pfxDetails('Факторная диагностика', '(экспозиции vs рынок + вывод)', pfxFactorsHTML(c))
        + pfxDetails('Ставка и облигации против портфеля', '(премия к безриску · порог замещения · дюрация к ставке)', pfxRateBondHTML(c), (c._rb && c._rb.ok && c._rb.substituted));
    case 'dividends':
      return pfxDetails('Дивидендный стресс-тест', '(base / conservative / stress / crisis + yield trap)', pfxDivHTML(c), true);
    case 'scenarios':
      return pfxDetails('Эффективность портфеля', '(положение относительно границы эффективности · сценарии перераспределения)', pfxFrontierHTML(c), true)
        + pfxDetails('Веер сценариев года', '(1000 виртуальных лет из вашей истории · не прогноз)', pfxBootHTML(c))
        + pfxDetails('Стресс-сценарии рынка', '(рынок · ставка · рецессия — по исторической beta)', pfxScenarioHTML(c))
        + pfxDetails('Smart Rebalancer', '(Suggested Diagnostic Weights — не рекомендация)', pfxRebalHTML(c));
    case 'memo': {
      const memo = pfxMemo(c).map(([h, b]) => `<div class="pfx-memo-block"><h4>${esc(h)}</h4><p>${esc(b)}</p></div>`).join('');
      return pfxDetails('Investment Committee Memo', '(rule-based, тон аналитика)', `<div class="pfx-memo">${memo}</div>`, true)
        + pfxDetails('Data Quality Layer', '(confidence по бумагам)', pfxDQHTML(c))
        + pfxDetails('Методология и предупреждения', '', pfxMethodHTML());
    }
    default:
      return '';
  }
}

// «Данные и ограничения» — исключённые позиции, длина/частота истории, дата снапшота (§12)
function pfxLimitationsHTML(c) {
  const warns = (c._warnings && c._warnings.length)
    ? `<div class="pfx-warns-panel">${c._warnings.map((w) => `<div class="pfx-wline pfx-w-${w.tone}">${esc(w.msg)}</div>`).join('')}</div>` : '';
  const meta = `<div class="pfx-limit-meta muted">`
    + `История: <b>${c.pf ? c.pf.n + ' мес' : 'н/д'}</b> · частота: <b>месячная</b> (дневной риск — отдельным модулем во вкладке «Риск»)`
    + (c.pf && isNum(c.pf.covered) ? ` · риск-покрытие: <b>${Math.round(c.pf.covered * 100)}%</b> веса` : '')
    + (DATA && DATA.meta && DATA.meta.price_asof ? ` · снапшот цен: <b>${esc(DATA.meta.price_asof)}</b>` : '')
    + `. Backfilled по текущему составу — НЕ история ваших сделок. Не ИИР.</div>`;
  return pfxDetails('Данные и ограничения', '(исключённые позиции, длина/частота истории, снапшот)', warns + meta, false);
}

function pfxRenderHTML(c) {
  const diag = pfxDiagnosis(c);
  c._rb = pfxRateBond(c);        // P4: связка со ставкой/облигациями (для модуля и графика)
  c._corr = pfxCorrelation(c);   // корреляции — для «Итога» и модуля матрицы
  let html = '';

  // Дашборд: итог инвесткомитета (Level-1 вывод «за 20 секунд») + заголовок + кнопки отчёта
  html += pfxCommitteeSummary(c);
  html += `<div class="pfx-top">
    <div class="pfx-type pfx-${c.cls.tone}">${esc(c.cls.type)}</div>
    <div class="pfx-diag">${esc(diag)}</div>
    <div class="pfx-btns">
      <button class="btn btn-secondary" id="pfx-copy">Скопировать отчёт</button>
      <button class="btn btn-secondary" id="pfx-export">Экспорт диагностики CSV</button>
    </div>
  </div>`;

  // Headline KPI-лента (всегда на виду)
  html += pfxHeadlineKpis(c);

  // «Что требует внимания» — приоритетные алерты (переиспользуем pfxTopRisks)
  const risks = pfxTopRisks(c);
  if (risks.length) {
    html += `<div class="pfx-alerts"><div class="pfx-alerts-h">Что требует внимания</div>${
      risks.map((r) => `<div class="pfx-alert pfx-a-${r.tone}"><span class="pfx-alert-dot"></span><span class="pfx-alert-tx">${esc(r.text)}</span></div>`).join('')
    }</div>`;
  }

  // Daily Portfolio Brief — «сегодня важно для портфеля» (герой-блок)
  const brief = pfxDailyBrief(c);
  if (brief.length) {
    html += `<div class="pfx-brief"><div class="pfx-brief-head">📌 Сегодня важно для портфеля</div>${
      brief.map((b) => `<div class="pfx-brief-item pfx-bi-${b.tone}"><span class="pfx-bi-dot"></span><span>${esc(b.text)}</span></div>`).join('')}
      <div class="pfx-brief-foot muted">Синтез по доступным данным (месячные ретёрны, новости, фаза MCFTR, RFR). Дат дивотсечек в наборе нет — см. вкладку «Дивиденды». Не ИИР.</div></div>`;
  }

  // Вкладки + панель (наполняется активной вкладкой в pfxSelectTab — графики в скрытых панелях = 0 ширины)
  html += pfxTabNav();
  html += `<div class="pfx-tabpanel" id="pfx-tabpanel" role="tabpanel"></div>`;

  // Данные и ограничения
  html += pfxLimitationsHTML(c);
  return html;
}

// Переключение вкладки: рендерим только её модули + рисуем только её графики (ленивый рендер).
function pfxSelectTab(tab) {
  const c = PFX_STATE;
  if (!c) return;
  const valid = PFX_TAB_LIST.some((t) => t.id === tab) ? tab : 'summary';
  uiStateSave({ pfxTab: valid });
  document.querySelectorAll('.pfx-tab').forEach((t) => {
    const on = t.dataset.pfxTab === valid;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const panel = document.getElementById('pfx-tabpanel');
  if (!panel) return;
  panel.innerHTML = pfxTabHTML(c, valid);
  pfxDrawCharts(c);     // рисует только графики, чьи контейнеры сейчас в DOM (активная вкладка)
  pfxWirePanel();       // кнопки внутри панели (ребалансер) — свежий DOM, без двойных listener'ов
  if (valid === 'risk') { try { pfxDailyRiskLoad(c); } catch (e) { console.error('[daily-risk] load:', e); } }
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

// ── Daily Risk Engine v1 (клиент; ЗЕРКАЛИТ scripts/daily_risk.py; портфель НЕ покидает браузер) ──
// Дневные данные грузятся ленивы (site/daily/web/{SECID}.json) — только бумаги портфеля.
const PFX_DR = { N: 252, DDOF: 1, MIN_OBS: 60, H_INSUF: 125, H_USABLE: 252, CVAR_TAIL: 5, VC_PARTIAL: 0.85, VC_MIN: 0.50, TOL: 1e-9,
  EWMA_LAMBDA: 0.94, MC_SEED: 42, MC_SIMS_POINT: 20000, MC_SIMS_BACKTEST: 300,
  MC_AC_BLOCK_THR: 0.10, MC_BLOCK_LEN: 5, CF_MIN_OBS: 252, CF_MAX_KURT: 15.0,
  BT_WINDOW: 252, BT_HORIZON: 1, MIN_BT_FORECASTS: 100, KUPIEC_REJECT_LR: 3.841 };
const DR_RANK = { total_return: 3, adjusted_price_return: 2, raw_price_return: 1 };
const DR_EXCL = { invalid_value: 'нулевая или некорректная стоимость', no_data: 'нет дневных данных',
  stale: 'ряд устарел', insufficient_history: 'недостаточная история',
  corporate_action_unresolved: 'нераспознанное корпоративное действие', not_mapped: 'тикер не сопоставлен',
  insufficient_common_dates: 'мало общих торговых дат с портфелем' };

function drMean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0; }
function drStd(a) { const n = a.length; if (n - PFX_DR.DDOF <= 0) return 0; const m = drMean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (n - PFX_DR.DDOF)); }
function drQuantile(sorted, p) { const n = sorted.length; if (!n) return NaN; if (n === 1) return sorted[0]; const h = (n - 1) * p, lo = Math.floor(h), hi = Math.min(lo + 1, n - 1); return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]); }
function drVarCvar(rets, level) { if (!rets.length) return { var: null, cvar: null, tail: 0 }; const s = rets.slice().sort((a, b) => a - b); const q = drQuantile(s, 1 - level); const tail = rets.filter((r) => r <= q); return { var: -q, cvar: tail.length ? -drMean(tail) : -q, tail: tail.length }; }
function drMaxDD(rets) {
  const w = [1]; for (const r of rets) w.push(w[w.length - 1] * (1 + r));
  let peak = w[0], peakI = 0, mdd = 0, mpI = 0, mtI = 0;
  for (let i = 0; i < w.length; i++) { if (w[i] > peak) { peak = w[i]; peakI = i; } const dd = w[i] / peak - 1; if (dd < mdd) { mdd = dd; mpI = peakI; mtI = i; } }
  let rec = null; const pv = w[mpI]; for (let i = mtI + 1; i < w.length; i++) { if (w[i] >= pv) { rec = i; break; } }
  const curPeak = Math.max(...w); const curDD = w[w.length - 1] / curPeak - 1;
  return { mdd, peakI: mpI, troughI: mtI, recI: rec, curDD };
}
function drDownside(rets) { if (!rets.length) return 0; return Math.sqrt(rets.reduce((s, r) => s + Math.min(r, 0) ** 2, 0) / rets.length); }
function drBeta(port, bench) {
  const n = Math.min(port.length, bench.length); if (n < 2) return { beta: null, corr: null, n };
  const p = port.slice(0, n), b = bench.slice(0, n), mp = drMean(p), mb = drMean(b);
  let cov = 0, vb = 0, vp = 0; for (let i = 0; i < n; i++) { cov += (p[i] - mp) * (b[i] - mb); vb += (b[i] - mb) ** 2; vp += (p[i] - mp) ** 2; }
  cov /= (n - PFX_DR.DDOF); vb /= (n - PFX_DR.DDOF); vp /= (n - PFX_DR.DDOF);
  if (vb <= PFX_DR.TOL) return { beta: null, corr: null, n };
  const sdp = Math.sqrt(vp), sdb = Math.sqrt(vb);
  return { beta: cov / vb, corr: (sdp > PFX_DR.TOL && sdb > PFX_DR.TOL) ? cov / (sdp * sdb) : null, n };
}
// v1.1 + v2 (зеркало daily_risk.py)
const DR_Z = { 0.95: 1.6448536269514722, 0.99: 2.3263478740408408 };
function drCov(matrix, secids) {
  const k = secids.length, n = matrix[secids[0]].length, means = {};
  secids.forEach((s) => { means[s] = drMean(matrix[s]); });
  const S = Array.from({ length: k }, () => new Array(k).fill(0));
  for (let i = 0; i < k; i++) for (let j = i; j < k; j++) {
    let c = 0; for (let t = 0; t < n; t++) c += (matrix[secids[i]][t] - means[secids[i]]) * (matrix[secids[j]][t] - means[secids[j]]);
    c = (n - PFX_DR.DDOF > 0) ? c / (n - PFX_DR.DDOF) : 0; S[i][j] = c; S[j][i] = c;
  }
  return S;
}
function drRiskContribution(matrix, weights, secids) {
  const k = secids.length; if (!k) return { ok: false };
  const S = drCov(matrix, secids), w = secids.map((s) => weights[s]);
  const Sw = S.map((row) => row.reduce((s, v, j) => s + v * w[j], 0));
  const varP = w.reduce((s, wi, i) => s + wi * Sw[i], 0), sigma = Math.sqrt(Math.max(varP, 0));
  const scale = Math.sqrt(PFX_DR.N); if (sigma <= PFX_DR.TOL) return { ok: false };
  const rows = secids.map((s, i) => { const mrc = Sw[i] / sigma, crc = w[i] * mrc; return { secid: s, weight: w[i], mrc: mrc * scale, crc: crc * scale, pcr: crc / sigma }; });
  return { ok: true, sigmaAnnual: sigma * scale, rows };
}
// v1.1: агрегация PCR/веса по секторам из строк risk_contribution (не теряет и не удваивает риск)
function drSectorRiskContribution(rcRows, sectors) {
  const agg = {};
  rcRows.forEach((r) => { const sec = sectors[r.secid] || 'н/д'; const a = agg[sec] || (agg[sec] = { sector: sec, weight: 0, pcr: 0 }); a.weight += r.weight; a.pcr += r.pcr; });
  return Object.values(agg).sort((a, b) => b.pcr - a.pcr);
}
function drConcentration(weights, sectors) {
  const vals = Object.values(weights).sort((a, b) => b - a);
  const hhi = Object.values(weights).reduce((s, w) => s + w * w, 0);
  const out = { largest: vals[0] || 0, top3: vals.slice(0, 3).reduce((s, x) => s + x, 0), top5: vals.slice(0, 5).reduce((s, x) => s + x, 0), hhi, effN: hhi > 0 ? 1 / hhi : 0, sectorHhi: null, sectors: null };
  if (sectors) { const sw = {}; Object.keys(weights).forEach((s) => { const sec = sectors[s] || 'н/д'; sw[sec] = (sw[sec] || 0) + weights[s]; }); out.sectorHhi = Object.values(sw).reduce((s, w) => s + w * w, 0); out.sectors = sw; }
  return out;
}
function drVarNormal(r, lvl) { return -(drMean(r) - DR_Z[lvl] * drStd(r)); }
function drVarEwma(r, lvl, lam) { lam = lam == null ? PFX_DR.EWMA_LAMBDA : lam; if (r.length < 2) return null; const m = drMean(r); let v = (r[0] - m) ** 2; for (let i = 1; i < r.length; i++) v = lam * v + (1 - lam) * (r[i] - m) ** 2; return DR_Z[lvl] * Math.sqrt(v); }
// v2: Cornish-Fisher — EXPLICIT GATE (никогда не клампится молча): либо {ok:true,var,skew,kurt},
// либо {ok:false,reason,detail} — «метод недоступен из-за недостаточной устойчивости выборки».
function drVarCF(r, lvl) {
  const n = r.length;
  if (n < PFX_DR.CF_MIN_OBS) return { ok: false, var: null, reason: 'insufficient_history', detail: `нужно ≥${PFX_DR.CF_MIN_OBS} набл., есть ${n}` };
  const m = drMean(r), sd = drStd(r);
  if (sd <= PFX_DR.TOL) return { ok: false, var: null, reason: 'numerical_instability', detail: 'нулевая дисперсия' };
  const s = r.reduce((a, x) => a + ((x - m) / sd) ** 3, 0) / n, kx = r.reduce((a, x) => a + ((x - m) / sd) ** 4, 0) / n - 3;
  if (!isFinite(s) || !isFinite(kx)) return { ok: false, var: null, reason: 'numerical_instability', detail: 'skew/kurtosis не определены' };
  if (Math.abs(kx) > PFX_DR.CF_MAX_KURT) return { ok: false, var: null, reason: 'unstable_kurtosis', detail: `|excess kurtosis|=${Math.abs(kx).toFixed(1)} > ${PFX_DR.CF_MAX_KURT}` };
  const z = -DR_Z[lvl];
  const zc = z + (s / 6) * (z * z - 1) + (kx / 24) * (z ** 3 - 3 * z) - (s * s / 36) * (2 * z ** 3 - 5 * z);
  if (!isFinite(zc) || zc > 0 || zc < 4 * z) return { ok: false, var: null, reason: 'numerical_instability', detail: 'скорректированный квантиль вне допустимого диапазона' };
  const v = -(m + zc * sd);
  if (!isFinite(v) || v <= 0) return { ok: false, var: null, reason: 'invariant_violation', detail: 'CF VaR ≤ 0' };
  return { ok: true, var: v, skewness: s, excessKurtosis: kx, zAdjusted: zc };
}
function drRng(seed) { let a = seed >>> 0; return () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function drAutocorrLag1(xs) { const n = xs.length; if (n < 3) return 0; const m = drMean(xs); let den = 0; xs.forEach((x) => { den += (x - m) ** 2; }); if (den <= PFX_DR.TOL) return 0; let num = 0; for (let i = 1; i < n; i++) num += (xs[i] - m) * (xs[i - 1] - m); return num / den; }
// v2: Historical Monte Carlo — SEEDED NON-PARAMETRIC bootstrap (ресэмплинг РЕАЛЬНЫХ значений,
// не синтетика из распределения). IID vs block — по автокорреляции КВАДРАТОВ доходности (ARCH/
// кластеризация волатильности), не по автокорреляции самих доходностей.
function drVarBootstrap(r, lvl, nSims, seed) {
  const n = r.length; if (n < 20) return { ok: false, var: null, reason: 'insufficient_history' };
  nSims = nSims || PFX_DR.MC_SIMS_POINT; seed = seed == null ? PFX_DR.MC_SEED : seed;
  const m = drMean(r), sq = r.map((x) => (x - m) ** 2), acSq = drAutocorrLag1(sq);
  const useBlock = Math.abs(acSq) >= PFX_DR.MC_AC_BLOCK_THR, blockLen = useBlock ? PFX_DR.MC_BLOCK_LEN : 1;
  const rng = drRng(seed), sims = [];
  if (useBlock) {
    while (sims.length < nSims) { const start = Math.floor(rng() * n); for (let k = 0; k < blockLen; k++) { sims.push(r[(start + k) % n]); if (sims.length >= nSims) break; } }
  } else {
    for (let i = 0; i < nSims; i++) sims.push(r[Math.floor(rng() * n)]);
  }
  sims.sort((a, b) => a - b);
  return { ok: true, var: -drQuantile(sims, 1 - lvl), bootstrapMethod: useBlock ? 'block' : 'iid', blockLength: useBlock ? blockLen : null, autocorrSqLag1: acSq, nSims, seed };
}
function drChi2sf1(x) { return x >= 0 ? erfcApprox(Math.sqrt(x / 2)) : 1; }
function erfcApprox(x) { const t = 1 / (1 + 0.3275911 * x); const y = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))); return y * Math.exp(-x * x); }
function drKupiec(breaches, obs, lvl) { if (!obs) return { ok: false }; const p = 1 - lvl, x = breaches, n = obs, ph = x / n; const ln = (v) => v > 0 ? Math.log(v) : 0; const ll0 = (n - x) * ln(1 - p) + x * ln(p), ll1 = (n - x) * ln(1 - ph) + x * ln(ph); const lr = -2 * (ll0 - ll1); return { ok: true, lr, pvalue: drChi2sf1(lr), reject: lr > PFX_DR.KUPIEC_REJECT_LR }; }
function drChristoffersen(seq) { let n00 = 0, n01 = 0, n10 = 0, n11 = 0; for (let i = 1; i < seq.length; i++) { const a = seq[i - 1], b = seq[i]; if (!a && !b) n00++; else if (!a && b) n01++; else if (a && !b) n10++; else n11++; } if (!(n00 + n01) || !(n10 + n11)) return { ok: false }; const pi01 = n01 / (n00 + n01), pi11 = n11 / (n10 + n11), pi = (n01 + n11) / (n00 + n01 + n10 + n11); const ln = (v) => v > 0 ? Math.log(v) : 0; const ll0 = (n00 + n10) * ln(1 - pi) + (n01 + n11) * ln(pi), ll1 = n00 * ln(1 - pi01) + n01 * ln(pi01) + n10 * ln(1 - pi11) + n11 * ln(pi11); const lr = -2 * (ll0 - ll1); return { ok: true, lr, pvalue: drChi2sf1(lr), reject: lr > PFX_DR.KUPIEC_REJECT_LR }; }
// v2: обобщённый rolling out-of-sample backtest — ЛЮБОЙ метод. Прогноз на день i использует
// ТОЛЬКО r[i-win:i] (нет look-ahead). forecastFn может вернуть null (напр. CF gate) → окно
// пропускается (skippedGate), не считается ни пробоем, ни не-пробоем.
function drRollingBacktest(r, lvl, win, forecastFn) {
  const n = r.length; const seq = []; let breaches = 0, skipped = 0;
  for (let i = win; i < n; i++) {
    const v = forecastFn(r.slice(i - win, i));
    if (v == null) { skipped++; continue; }
    const b = r[i] < -v ? 1 : 0; seq.push(b); breaches += b;
  }
  const obs = seq.length;
  if (obs < PFX_DR.MIN_BT_FORECASTS) return { ok: false, status: 'insufficient_backtest_history', obs, skippedGate: skipped, window: win, level: lvl, minRequired: PFX_DR.MIN_BT_FORECASTS };
  const kp = drKupiec(breaches, obs, lvl), ch = drChristoffersen(seq), calibrated = kp.ok && !kp.reject;
  return { ok: true, status: 'complete', window: win, level: lvl, horizonDays: PFX_DR.BT_HORIZON, policy: 'rolling',
    obs, skippedGate: skipped, breaches, expectedBreaches: (1 - lvl) * obs, freq: breaches / obs,
    kupiec: kp, christoffersen: ch, calibrated, verdict: calibrated ? 'калиброван' : 'калибровка под вопросом' };
}
function drBacktestMethod(r, lvl, method, win) {
  win = win || PFX_DR.BT_WINDOW;
  let fn;
  if (method === 'historical') fn = (w) => -drQuantile(w.slice().sort((a, b) => a - b), 1 - lvl);
  else if (method === 'normal') fn = (w) => drVarNormal(w, lvl);
  else if (method === 'ewma') fn = (w) => drVarEwma(w, lvl, PFX_DR.EWMA_LAMBDA);
  else if (method === 'cornish_fisher') fn = (w) => { const c = drVarCF(w, lvl); return c.ok ? c.var : null; };
  else if (method === 'bootstrap') fn = (w) => { const b = drVarBootstrap(w, lvl, PFX_DR.MC_SIMS_BACKTEST, PFX_DR.MC_SEED); return b.ok ? b.var : null; };
  else throw new Error('unknown method ' + method);
  return drRollingBacktest(r, lvl, win, fn);
}
const DR_METHOD_META = {
  historical: { label: 'Исторический', assumptions: 'эмпирическое распределение прошлых доходностей, без параметрических допущений' },
  normal: { label: 'Нормальное распределение', assumptions: 'доходности распределены нормально (Gauss); недооценивает жирные хвосты' },
  ewma: { label: 'EWMA', assumptions: `нормальное распределение с экспоненциально взвешенной волатильностью (λ=${PFX_DR.EWMA_LAMBDA})` },
  cornish_fisher: { label: 'Cornish-Fisher', assumptions: 'поправка нормального квантиля на скошенность и эксцесс выборки' },
  bootstrap: { label: 'Monte Carlo (bootstrap)', assumptions: 'непараметрический ресэмплинг реальных исторических доходностей (не синтетика)' },
};
function drNeutralVerdict(bt) {
  if (!bt.ok) return 'Истории недостаточно для надёжной проверки';
  const kpOk = bt.kupiec.ok && !bt.kupiec.reject, ch = bt.christoffersen || {}, chOk = ch.ok && !ch.reject;
  if (kpOk && chOk) return 'VaR откалиброван приемлемо';
  if (!kpOk) return bt.freq > (1 - bt.level) ? 'VaR занижает риск' : 'VaR завышает риск (слишком консервативен)';
  return 'Наблюдается кластеризация плохих дней';
}
function drMethodComparison(port, pointEstimates, win) {
  win = win || PFX_DR.BT_WINDOW;
  return Object.keys(DR_METHOD_META).map((method) => {
    const meta = DR_METHOD_META[method], est = pointEstimates[method], bt = drBacktestMethod(port, 0.95, method, win);
    return { method, label: meta.label, assumptions: meta.assumptions, currentEstimate: est, available: est != null, backtest: bt, verdict: drNeutralVerdict(bt) };
  });
}
// сохранена как алиас для обратной совместимости вызовов вне этого модуля
function drVarBacktest(r, lvl, win) { return drBacktestMethod(r, lvl, 'historical', win || PFX_DR.BT_WINDOW); }

function drConfidence(commonN, valueCov, mixed, fallback, benchOk, partial) {
  const order = ['unavailable', 'low', 'medium', 'high']; let cap = 'high';
  const down = (l) => { if (order.indexOf(l) < order.indexOf(cap)) cap = l; };
  if (commonN < PFX_DR.MIN_OBS) return 'unavailable';
  if (commonN < PFX_DR.H_INSUF) down('low'); else if (commonN < PFX_DR.H_USABLE) down('medium');
  if (valueCov < PFX_DR.VC_MIN) down('low'); else if (valueCov < PFX_DR.VC_PARTIAL) down('medium');
  if (mixed || fallback) down('medium');
  if (!benchOk) down('medium');
  if (partial) down('medium');
  return cap;
}
// mirror daily_risk.compute: positions=[{ticker,secid,quantity,price,returns:{d:r},quality_status,corporate_action_status,return_type,fallback_status}]
function drCompute(positions, benchmark) {
  const mv = (p) => { const q = +p.quantity, pr = +p.price; return (isFinite(q) && isFinite(pr) && q > 0 && pr > 0) ? q * pr : (q <= 0 ? 0 : null); };
  const totalValue = positions.reduce((s, p) => { const v = mv(p); return s + (v || 0); }, 0);
  const included = [], excluded = [];
  positions.forEach((p) => {
    const v = mv(p); const rets = p.returns || {};
    let code = null;
    if (v == null || v <= 0) code = 'invalid_value';
    else if (!Object.keys(rets).length) code = 'no_data';
    else if (p.quality_status === 'unavailable') code = 'no_data';
    else if (p.quality_status === 'stale') code = 'stale';
    else if (p.quality_status === 'insufficient_history') code = 'insufficient_history';
    else if (p.corporate_action_status === 'unresolved' || p.quality_status === 'corporate_action_unresolved') code = 'corporate_action_unresolved';
    else if (!p.secid) code = 'not_mapped';
    if (code) excluded.push({ ticker: p.ticker, reason: DR_EXCL[code] || 'недоступно' }); else included.push(p);
  });
  const base = { included, excluded, totalValue, weights_method: 'current_market_value_fixed_weights',
    annualization: PFX_DR.N, ddof: PFX_DR.DDOF, quantile: 'linear_interpolation' };
  if (!included.length) return Object.assign(base, { confidence: 'unavailable', metrics: null });
  const incVal = included.reduce((s, p) => s + mv(p), 0);
  const weights = {}; included.forEach((p) => { weights[p.secid] = mv(p) / incVal; });
  // выравнивание по общим датам
  const dateSets = included.map((p) => new Set(Object.keys(p.returns)));
  let common = [...dateSets[0]].filter((d) => dateSets.every((s) => s.has(d))).sort();
  const commonN = common.length;
  const ranks = included.map((p) => DR_RANK[p.return_type] || 1);
  const mixed = new Set(ranks).size > 1;
  const returnType = { 3: 'total_return', 2: 'adjusted_price_return', 1: 'raw_price_return' }[Math.min(...ranks)];
  const fallback = included.some((p) => p.fallback_status === 'fallback' || p.return_type === 'raw_price_return');
  const valueCov = totalValue > 0 ? incVal / totalValue : 0;
  const numberCov = positions.length ? included.length / positions.length : 0;
  const partial = valueCov < PFX_DR.VC_PARTIAL;
  const cov = { valueCov, numberCov, included: included.length, excluded: positions.length - included.length,
    common_start: common[0] || null, common_end: common[common.length - 1] || null, partial };
  if (commonN < PFX_DR.MIN_OBS) return Object.assign(base, { confidence: 'unavailable', metrics: null, observations: commonN, coverage: cov, returnType });
  const port = common.map((d) => included.reduce((s, p) => s + weights[p.secid] * p.returns[d], 0));
  const dailyVol = drStd(port), annVol = dailyVol * Math.sqrt(PFX_DR.N);
  const v95 = drVarCvar(port, 0.95), v99 = drVarCvar(port, 0.99);
  const dd = drMaxDD(port), dsd = drDownside(port);
  // бенчмарк по общим датам
  let benchOk = false, bres = { beta: null, corr: null }, benchOut = null;
  if (benchmark && benchmark.returns) {
    const bmap = benchmark.returns; const bdates = common.filter((d) => d in bmap);
    if (bdates.length >= PFX_DR.MIN_OBS) {
      const pmap = {}; common.forEach((d, i) => { pmap[d] = port[i]; });
      bres = drBeta(bdates.map((d) => pmap[d]), bdates.map((d) => bmap[d])); benchOk = bres.beta != null;
    }
    benchOut = { ticker: benchmark.ticker || 'IMOEX', type: benchmark.type || 'price_index', beta: bres.beta, corr: bres.corr, n: bdates.length };
  }
  const confidence = drConfidence(commonN, valueCov, mixed, fallback, benchOk, partial);
  const cvar99ok = v99.tail >= PFX_DR.CVAR_TAIL;
  const metrics = { dailyVol, annVol, covered: incVal,
    var95: { pct: v95.var, rub: v95.var * incVal }, var99: { pct: v99.var, rub: v99.var * incVal },
    cvar95: { pct: v95.cvar, rub: v95.cvar * incVal, tail: v95.tail, low: v95.tail < PFX_DR.CVAR_TAIL },
    cvar99: cvar99ok ? { pct: v99.cvar, rub: v99.cvar * incVal, tail: v99.tail } : { pct: null, tail: v99.tail, low: true },
    dd, dsd };
  const warnings = [];
  if (mixed) warnings.push('смешение типов рядов — надёжность не выше средней');
  if (fallback) warnings.push('часть позиций на сырых (нескорректированных) ценах');
  if (partial) warnings.push('покрыта не вся стоимость портфеля — метрики частичные');
  if (!benchOk) warnings.push('бенчмарк не выровнен — beta/корреляция недоступны');
  // v1.1: component risk contribution + concentration + sector risk contribution
  const secids = included.map((p) => p.secid);
  const matrix = {}; secids.forEach((s) => { const p = included.find((x) => x.secid === s); matrix[s] = common.map((d) => p.returns[d]); });
  const sectors = {}; included.forEach((p) => { sectors[p.secid] = p.sector || null; });
  const rc = drRiskContribution(matrix, weights, secids);
  if (rc.ok) {
    const byT = {}; included.forEach((p) => { byT[p.secid] = p.ticker; });
    rc.rows.forEach((r) => { r.ticker = byT[r.secid] || r.secid; });
    rc.rows.sort((a, b) => b.pcr - a.pcr);
    rc.bySector = drSectorRiskContribution(rc.rows, sectors);
  }
  const conc = drConcentration(weights, sectors);
  // v2: методы VaR (95/99, точечные) — CF и bootstrap возвращают explicit-gate объекты
  const varMethods = {}, methodDiag = {};
  [[0.95, '95'], [0.99, '99']].forEach(([lvl, key]) => {
    const cf = drVarCF(port, lvl), bs = drVarBootstrap(port, lvl, PFX_DR.MC_SIMS_POINT, PFX_DR.MC_SEED);
    varMethods[key] = { historical: (lvl === 0.95 ? v95.var : v99.var), normal: drVarNormal(port, lvl), ewma: drVarEwma(port, lvl),
      cornish_fisher: cf.ok ? cf.var : null, bootstrap: bs.ok ? bs.var : null };
    methodDiag[key] = { cornish_fisher: cf, bootstrap: bs };
  });
  // v2: строгий out-of-sample backtest (rolling, окно из единого config, БЕЗ look-ahead) +
  // нейтральное сравнение методов (порядок фиксирован DR_METHOD_META, не по p-value)
  const backtest = drBacktestMethod(port, 0.95, 'historical', PFX_DR.BT_WINDOW);
  const comparison = drMethodComparison(port, varMethods['95'], PFX_DR.BT_WINDOW);
  const ewmaMeta = { lambda: PFX_DR.EWMA_LAMBDA, effectiveMemoryDays: Math.round((1 / (1 - PFX_DR.EWMA_LAMBDA)) * 10) / 10, confidenceLevel: 0.95, currentForecastDate: common[common.length - 1] };
  return Object.assign(base, { confidence, metrics, benchmark: benchOut, observations: commonN,
    coverage: cov, returnType, mixed, weights, warnings,
    riskContribution: rc, concentration: conc, varMethods, methodDiag, backtest,
    methodComparison: comparison, ewmaMeta });
}

// ленивая загрузка веб-моста + расчёт + рендер в #pfx-daily-risk
function pfxDailyRiskLoad(c) {
  const box = document.getElementById('pfx-daily-risk'); if (!box) return;
  const idxUrl = 'daily/web/_index.json';
  const getJSON = (u) => fetch(dataURL(u)).then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  const ensureIdx = PFX_DAILY.index ? Promise.resolve(PFX_DAILY.index) : getJSON(idxUrl).then((j) => { PFX_DAILY.index = j; return j; });
  ensureIdx.then((idx) => {
    const aliases = idx.aliases || {}; const secInfo = idx.securities || {};
    const want = [];
    c.positions.forEach((p) => { const secid = aliases[p.ticker] || p.ticker; want.push({ p, secid, info: secInfo[secid] }); });
    const toFetch = want.filter((w) => w.info && w.info.published && !PFX_DAILY.cache[w.secid]).map((w) => w.secid);
    const benchP = PFX_DAILY.bench ? Promise.resolve(PFX_DAILY.bench) : getJSON('daily/web/_benchmark.json').then((b) => { PFX_DAILY.bench = b; return b; }).catch(() => null);
    const filesP = Promise.all(toFetch.map((s) => getJSON('daily/web/' + s + '.json').then((rec) => { PFX_DAILY.cache[s] = rec; }).catch(() => { PFX_DAILY.cache[s] = null; })));
    return Promise.all([benchP, filesP]).then(([bench]) => {
      const positions = want.map((w) => {
        const rec = w.info && w.info.published ? PFX_DAILY.cache[w.secid] : null;
        const price = (w.p.t && isNum(w.p.t.price)) ? w.p.t.price : (w.p.quantity > 0 && isNum(w.p.value) ? w.p.value / w.p.quantity : null);
        const returns = {}; if (rec && rec.dates) rec.dates.forEach((d, i) => { returns[d] = rec.returns[i]; });
        return { ticker: w.p.ticker, secid: rec ? w.secid : null, quantity: w.p.quantity, price, sector: w.p.sector,
          returns, quality_status: rec ? rec.quality_status : 'unavailable',
          corporate_action_status: rec ? rec.corporate_action_status : 'resolved',
          return_type: rec ? rec.return_type : 'raw_price_return', fallback_status: rec ? rec.fallback_status : 'none' };
      });
      const benchmark = bench ? { ticker: bench.ticker, type: bench.type, returns: (() => { const m = {}; if (bench.dates) bench.dates.forEach((d, i) => { m[d] = bench.returns[i]; }); return m; })() } : null;
      const res = drCompute(positions, benchmark);
      box.innerHTML = pfxDailyRiskHTML(res);
    });
  }).catch((e) => { console.error('[daily-risk]', e); box.innerHTML = '<div class="pfx-note muted">Дневные данные временно недоступны. Долгосрочная (месячная) оценка риска — в блоках выше.</div>'; });
}

const DR_CONF_RU = { high: 'высокая', medium: 'средняя', low: 'низкая', unavailable: 'недостаточно данных' };
const DR_CONF_TONE = { high: 'good', medium: 'warn', low: 'risk', unavailable: 'risk' };
function drPct(x, d) { return isNum(x) ? (x * 100).toFixed(d == null ? 2 : d) + '%' : NA; }

function pfxDailyRiskHTML(r) {
  const disc = '<div class="pfx-disc">Историческая модель <b>текущего состава</b> на фиксированных текущих весах по <b>дневным</b> данным MOEX. Это НЕ фактическая история ваших сделок. Не прогноз, не рекомендация.</div>';
  if (!r.metrics) {
    const why = r.confidence === 'unavailable' && r.included && r.included.length
      ? `Общая история короче ${PFX_DR.MIN_OBS} торговых дней.` : 'Нет включаемых позиций с дневными данными.';
    return `<div class="pfx-note">Дневной риск не рассчитан: ${esc(why)} ${r.excluded && r.excluded.length ? 'Исключены: ' + r.excluded.map((e) => esc(e.ticker) + ' — ' + esc(e.reason)).join('; ') + '.' : ''}</div>${disc}`;
  }
  const m = r.metrics, conf = r.confidence;
  const volTone = m.annVol > 0.30 ? 'risk' : m.annVol > 0.15 ? 'warn' : 'good';
  const volWord = m.annVol > 0.30 ? 'высокая' : m.annVol > 0.15 ? 'умеренная' : 'низкая';
  const beta = r.benchmark && isNum(r.benchmark.beta) ? r.benchmark.beta : null;
  const g = [
    pfxKpi('Годовая волатильность', drPct(m.annVol, 1), `дневная ${drPct(m.dailyVol, 2)} · ${volWord}`, volTone),
    pfxKpi('VaR 95% (1 день)', drPct(m.var95.pct, 2), rub0(m.var95.rub) + ' от покрытой части', 'risk'),
    pfxKpi('CVaR 95% (1 день)', drPct(m.cvar95.pct, 2), (m.cvar95.low ? 'мало наблюдений в хвосте' : rub0(m.cvar95.rub)), 'risk'),
    pfxKpi('Макс. модельная просадка', drPct(m.dd.mdd, 1), m.dd.recI == null ? 'без восстановления' : 'восстановление было', 'risk'),
    pfxKpi('Beta к IMOEX', beta != null ? PU(beta, 2) : NA, beta != null ? pfxBetaBucket(beta) : 'бенчмарк не выровнен', 'neut'),
    pfxKpi('Надёжность оценки', DR_CONF_RU[conf], `покрытие ${drPct(r.coverage.valueCov, 0)} стоимости`, DR_CONF_TONE[conf]),
  ];
  // главный вывод простым языком
  const top = r.included.map((p) => r.weights[p.secid]).sort((a, b) => b - a);
  const topW = top[0] || 0;
  let takeaway;
  if (r.coverage.partial) takeaway = `Расчёт покрывает ${drPct(r.coverage.valueCov, 0)} стоимости портфеля (${r.coverage.included} из ${r.coverage.included + r.coverage.excluded} позиций) и ${r.observations} общих торговых дней — метрики частичные.`;
  else if (topW > 0.4) takeaway = `Основной краткосрочный риск — концентрация: крупнейшая позиция ${drPct(topW, 0)} стоимости. Расчёт на ${r.observations} общих торговых днях.`;
  else if (beta != null && beta > 1.2) takeaway = `Портфель заметно чувствительнее рынка (beta ${PU(beta, 2)} к IMOEX). Расчёт на ${r.observations} общих торговых днях.`;
  else takeaway = `Краткосрочный риск ${volWord}, концентрация умеренная. Расчёт на ${r.observations} общих торговых днях, покрытие ${drPct(r.coverage.valueCov, 0)} стоимости.`;

  const exHTML = r.excluded && r.excluded.length
    ? `<div class="pfx-dr-excl"><b>Исключены из дневного расчёта:</b> ${r.excluded.map((e) => `${esc(e.ticker)} — ${esc(e.reason)}`).join('; ')}.</div>` : '';
  const warnHTML = r.warnings && r.warnings.length
    ? `<div class="pfx-dr-warns">${r.warnings.map((w) => `<div class="pfx-wline pfx-w-warn">${esc(w)}</div>`).join('')}</div>` : '';
  const cvar99 = m.cvar99.pct != null ? drPct(m.cvar99.pct, 2) : 'н/д (мало наблюдений в хвосте)';
  const method = `<div class="pfx-dr-method muted">
    Метод: Historical Simulation. Квантиль — линейная интерполяция (тип-7); ddof=1; annualization √${PFX_DR.N}.
    Тип ряда: ${esc(r.returnType)}. Веса: текущая рыночная стоимость, фиксированы на всей выборке (без ежедневной ребалансировки).
    Общих торговых дней: ${r.observations} (${esc(r.coverage.common_start || '')}…${esc(r.coverage.common_end || '')}).
    VaR 99%: ${drPct(m.var99.pct, 2)}; CVaR 99%: ${cvar99}. Downside deviation (дн.): ${drPct(m.dsd, 2)}.
    Бенчмарк: ${r.benchmark ? esc(r.benchmark.ticker) + ' (' + esc(r.benchmark.type) + '), корреляция ' + (isNum(r.benchmark.corr) ? PU(r.benchmark.corr, 2) : 'н/д') : 'н/д'}.
    Комиссии и налоги не учитываются; дивиденды — только при total return.</div>`;
  const help = `<div class="pfx-dr-help muted">
    <div><b>VaR 95%</b> — порог дневных потерь, который исторически превышался примерно в 5% дней. Это не максимальная возможная потеря.</div>
    <div><b>CVaR</b> — средняя потеря в самые неблагоприятные дни выбранного периода.</div>
    <div><b>Beta</b> — насколько чувствительно портфель исторически реагировал на движение IMOEX (не прогноз).</div>
    <div><b>Просадка</b> — наибольшее модельное снижение стоимости от пика до минимума.</div></div>`;
  // ── Level 2 (простой язык, открыто по умолчанию): «Откуда идёт риск» / «Концентрация» / «Насколько VaR калиброван» ──
  let riskSrcHTML = '', concHTML = '';
  if (r.riskContribution && r.riskContribution.ok) {
    const rcRows = r.riskContribution.rows;
    const rows = rcRows.slice(0, 5).map((x) => {
      const diff = x.pcr - x.weight;
      return `<tr><td>${esc(x.ticker)}</td><td class="num">${drPct(x.weight, 0)}</td><td class="num">${drPct(x.pcr, 0)}</td><td class="num ${diff > 0.02 ? 'pfx-neg' : ''}">${diff >= 0 ? '+' : ''}${drPct(diff, 0)}</td></tr>`;
    }).join('');
    const bySecLine = (r.riskContribution.bySector || []).slice(0, 3)
      .map((s) => `«${esc(s.sector)}» ${drPct(s.pcr, 0)} риска`).join(' · ');
    riskSrcHTML = `<details class="pfx-dr-more" open><summary>Откуда идёт риск</summary>
      <div class="pfx-dr-sub muted">Вклад в риск ≠ вес: позиция может давать больше риска, чем её доля стоимости.</div>
      <div class="cbr-table-scroll"><table class="pfx-dr-tbl"><thead><tr><th>Бумага</th><th class="num">Вес</th><th class="num">Вклад в риск</th><th class="num">Δ</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${bySecLine ? `<div class="pfx-dr-conc muted">По секторам: ${bySecLine}.</div>` : ''}</details>`;

    const c = r.concentration;
    const top3RiskShare = rcRows.slice(0, 3).reduce((s, x) => s + x.pcr, 0);
    concHTML = `<details class="pfx-dr-more" open><summary>Концентрация</summary>
      <div class="pfx-dr-conc muted">Три крупнейшие позиции формируют ${drPct(c.top3, 0)} стоимости и ${drPct(top3RiskShare, 0)} общего риска портфеля. Крупнейшая — ${drPct(c.largest, 0)}; по HHI портфель эквивалентен ${PU(c.effN, 1)} равновзвешенным бумагам.</div></details>`;
  }
  let calibHTML = '';
  if (r.backtest) {
    const bt = r.backtest, verdict = drNeutralVerdict(bt);
    const tone = !bt.ok ? 'neut' : (verdict === 'VaR откалиброван приемлемо' ? 'good' : 'warn');
    const support = bt.ok
      ? `Пробоев ${bt.breaches} из ${bt.obs} проверенных дней вне обучающей выборки (ожидалось около ${Math.round(bt.expectedBreaches)}).`
      : `Недостаточно данных вне обучающей выборки для проверки (нужно ≥${bt.minRequired || PFX_DR.MIN_BT_FORECASTS} прогнозов, доступно ${bt.obs || 0}).`;
    calibHTML = `<details class="pfx-dr-more" open><summary>Насколько VaR калиброван</summary>
      <div class="pfx-dr-calib pfx-w-${tone}">${esc(verdict)}</div>
      <div class="pfx-dr-sub muted">${esc(support)} Проверка — на днях, которые НЕ участвовали в текущей оценке.</div></details>`;
  }
  // ── Level 3 (профессиональный уровень, свёрнуто): сравнение методов, EWMA/bootstrap/CF параметры ──
  let cmpHTML = '';
  if (r.methodComparison) {
    const rows = r.methodComparison.map((m) => {
      const est = m.available ? drPct(m.currentEstimate, 2) : '—';
      const bt = m.backtest;
      const btCell = !bt.ok
        ? (bt.status === 'insufficient_backtest_history' ? `мало истории (${bt.obs}/${bt.minRequired})` : 'н/д')
        : `${bt.breaches}/${bt.obs} (ожид. ${bt.expectedBreaches.toFixed(0)}) · Kupiec p=${PU(bt.kupiec.pvalue, 2)}${bt.christoffersen && bt.christoffersen.ok ? ' · Christoffersen p=' + PU(bt.christoffersen.pvalue, 2) : ''}`;
      return `<tr><td title="${esc(m.assumptions)}">${esc(m.label)}</td><td class="num">${est}</td><td>${esc(btCell)}</td><td>${esc(m.verdict)}</td></tr>`;
    }).join('');
    const cfd = r.methodDiag && r.methodDiag['95'].cornish_fisher, bsd = r.methodDiag && r.methodDiag['95'].bootstrap, em = r.ewmaMeta;
    cmpHTML = `<details class="pfx-dr-more"><summary>Сравнение методов VaR (профессиональный уровень)</summary>
      <div class="pfx-dr-sub muted">Допущения — в подсказке названия метода. Проверка — rolling out-of-sample backtest (окно ${PFX_DR.BT_WINDOW} торг. дн., горизонт 1 день, без заглядывания вперёд). Методы НЕ ранжированы по p-value — сравниваются допущения и калибровка, не «лучший» метод.</div>
      <div class="cbr-table-scroll"><table class="pfx-dr-tbl"><thead><tr><th>Метод</th><th class="num">VaR 95%</th><th>Backtest</th><th>Вывод</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="pfx-dr-sub muted">
        EWMA: λ=${PU(em.lambda, 2)}, эффективная память ≈${Math.round(em.effectiveMemoryDays)} дн., прогноз на ${esc(em.currentForecastDate)}.<br/>
        Monte Carlo (bootstrap): ${bsd && bsd.ok ? esc(bsd.bootstrapMethod === 'block' ? `block-бутстрэп (блок ${bsd.blockLength} дн. — обнаружена кластеризация волатильности)` : 'IID-бутстрэп (кластеризация волатильности не обнаружена)') + `, ${bsd.nSims} симуляций, seed=${bsd.seed}` : 'недоступен'}.<br/>
        Cornish-Fisher: ${cfd && cfd.ok ? `skew=${PU(cfd.skewness, 2)}, excess kurtosis=${PU(cfd.excessKurtosis, 2)}` : `недоступен (${esc((cfd && cfd.detail) || 'н/д')})`}.
      </div></details>`;
  }
  return `<div class="pfx-dr-take">${esc(takeaway)}</div>
    <div class="pfx-grid pfx-dr-grid">${g.join('')}</div>
    ${warnHTML}${exHTML}
    ${riskSrcHTML}${concHTML}${calibHTML}${cmpHTML}
    <details class="pfx-dr-more"><summary>Методология и качество данных</summary>${method}${help}</details>
    ${disc}`;
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
    <div class="pfx-note muted">Дивдоходность — ожидаемая (прогноз проекта), не гарантирована и плавает; YTM облигации — контрактная к погашению при удержании (кредитный риск эмитента остаётся). Дивдоходность «на руки» — после НДФЛ ${taxPct()}%; облигационная YTM — при 13% (серверный расчёт). Дюрация дивпотока — стилизованная оценка по модели Гордона (1/дивдоходность), не прогноз цены. КБД — G-кривая ОФЗ MOEX. Не ИИР.</div>`;
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

// ── Возможности и внимание: потенциал к справедливой цене + флаги фундаментала (Bible XI) ──
function pfxOpportunity(c) {
  const opp = [], att = [];
  c.positions.forEach((p) => {
    const t = p.t; if (!t) return;
    const fair = t.valuation && isNum(t.valuation.fair_price) ? t.valuation.fair_price : null;
    const price = isNum(t.price) ? t.price : (isNum(p.current_price) ? p.current_price : null);
    const gap = (fair != null && price != null && price > 0) ? fair / price - 1 : null;
    const cut = isNum(t.cut_risk) ? t.cut_risk : null;
    const vcol = t.verdict && t.verdict.color;
    if (gap != null && gap >= 0.12) opp.push({ ticker: p.ticker, gap, method: (t.valuation.method || 'модель'), weight: p.weight });
    const reasons = [];
    if (vcol === 'risk') reasons.push(t.verdict.label || 'risk-вердикт');
    if (cut != null && cut >= 0.6) reasons.push(`высокий риск среза дивиденда (${Math.round(cut * 100)}%)`);
    if (gap != null && gap <= -0.15) reasons.push(`цена выше модельной справедливой на ${Math.round(-gap * 100)}%`);
    if (reasons.length) att.push({ ticker: p.ticker, reasons, weight: p.weight });
  });
  opp.sort((a, b) => b.gap - a.gap); att.sort((a, b) => b.weight - a.weight);
  return { ok: opp.length + att.length > 0, opp, att };
}
function pfxOppHTML(c) {
  const o = pfxOpportunity(c);
  if (!o.ok) return `<div class="pfx-note">По текущим бумагам портфеля нет ни заметного потенциала к модельной справедливой цене, ни флагов внимания. ${NA} для бумаг без покрытия.</div>`;
  const oppRows = o.opp.slice(0, 8).map((x) => `<li><b>${esc(x.ticker)}</b> <span class="saw-up">+${Math.round(x.gap * 100)}%</span> к модельной справедливой <span class="muted">(${esc(x.method)}, вес ${PN(x.weight, 0)})</span></li>`).join('');
  const attRows = o.att.slice(0, 8).map((x) => `<li><b>${esc(x.ticker)}</b> <span class="muted">вес ${PN(x.weight, 0)}</span> — ${esc(x.reasons.join('; '))}</li>`).join('');
  return `<div class="pfx-opp">
    <div class="pfx-opp-col pfx-opp-good"><h4>Потенциал к справедливой цене</h4>${oppRows ? `<ul>${oppRows}</ul>` : '<div class="muted">Заметного апсайда к модельной оценке нет.</div>'}</div>
    <div class="pfx-opp-col pfx-opp-att"><h4>Требуют внимания</h4>${attRows ? `<ul>${attRows}</ul>` : '<div class="muted">Флагов внимания нет.</div>'}</div>
    <div class="pfx-note muted" style="grid-column:1/-1">«Справедливая цена» — модельная оценка (DCF/DDM/сравнит., из data.json), НЕ целевая цена и не рекомендация купить/продать. «Внимание» — диагностические флаги (вердикт, риск среза дивиденда, цена выше модели). Проверяйте по источнику. Не ИИР.</div>
  </div>`;
}

// ── Scenario Lab: реакция портфеля на макро-шоки (честно: историческая beta + дюрация) ──
function pfxScenarioLab(c) {
  const beta = (c.capm && c.capm.ok && isNum(c.capm.beta)) ? c.capm.beta : (isNum(c.wBeta) ? c.wBeta : null);
  const total = c.total, rb = c._rb;
  const rateSens = c.positions.reduce((s, p) => s + p.weight * pfxSectorRate(p.sector), 0);
  const scen = [];
  const push = (name, movePct, basis, tone, divHit) => scen.push({ name, movePct, rub: total * movePct, basis, tone, divHit });
  if (beta != null) {
    push('Рынок −30%', beta * -0.30, `beta ${ru(beta, 2)} к MCFTR`, 'risk');
    push('Рынок −20%', beta * -0.20, `beta ${ru(beta, 2)}`, 'warn');
    push('Рынок +20%', beta * 0.20, `beta ${ru(beta, 2)}`, 'good');
  }
  if (rateSens > 0) {                                        // +2пп: секторная эластичность ~6%/пп (умеренно)
    const mv = -rateSens * 2 * 0.06;
    push('Ставка +2 п.п.', mv, `секторная чувствительность к ставке ${Math.round(rateSens * 100)}% × ~6% на п.п. (стилизов.)`, 'warn');
  }
  if (beta != null) {                                        // рецессия: рынок −25%×beta + дивиденды по crisis
    const mv = beta * -0.25;
    const divHit = (c.div && c.div.baseIncome > 0) ? (c.div.scen.crisis - c.div.baseIncome) : 0;
    push('Рецессия РФ', mv, `рынок −25%×beta${divHit ? '; дивиденды по crisis-сценарию' : ''}`, 'risk', divHit);
  }
  const macro = [
    { k: 'Рынок (MCFTR)', v: beta == null ? '—' : ru(beta, 2), cls: beta == null ? 'neut' : beta > 1.15 ? 'risk' : beta > 0.85 ? 'warn' : 'good', lbl: beta == null ? 'н/д' : beta > 1.15 ? 'высокая' : beta > 0.85 ? 'средняя' : 'низкая' },
    { k: 'Ставка (КБД)', v: Math.round(rateSens * 100) + '%', cls: rateSens > 0.6 ? 'risk' : rateSens > 0.4 ? 'warn' : 'good', lbl: rateSens > 0.6 ? 'высокая' : rateSens > 0.4 ? 'средняя' : 'низкая' },
  ];
  return { ok: scen.length > 0, scen, macro, beta };
}
function pfxScenarioHTML(c) {
  const s = pfxScenarioLab(c);
  if (!s.ok) return `<div class="pfx-note">${NA}: нужна beta к MCFTR (история ≥12 мес + выравнивание бенчмарка).</div>`;
  const cards = s.scen.map((x) => `<div class="pfx-scn-card pfx-${x.tone}">
    <span class="pfx-scn-name">${esc(x.name)}</span>
    <b class="pfx-scn-rub ${x.rub >= 0 ? 'saw-up' : 'saw-down'}">${rub0(x.rub)}</b>
    <span class="pfx-scn-pct ${x.movePct >= 0 ? 'saw-up' : 'saw-down'}">${PP(x.movePct, 1)}${x.divHit ? ` · дивиденды ${rub0(x.divHit)}/год` : ''}</span>
    <em class="pfx-scn-basis">${esc(x.basis)}</em></div>`).join('');
  const macro = s.macro.map((m) => `<div class="pfx-scn-macro pfx-${m.cls}"><span>${esc(m.k)}</span><b>${m.v}</b><em>${m.lbl} чувствительность</em></div>`).join('');
  return `<div class="pfx-scn">
    <div class="pfx-scn-macrorow">${macro}</div>
    <div class="pfx-scn-cards">${cards}</div>
    <div class="pfx-note muted">Оценки стилизованные, от текущей стоимости портфеля ${rub0(c.total)}: рыночные сценарии — историческая beta к MCFTR; ставка — стилизованная дюрация дивпотока (Гордон); рецессия — рынок×beta + дивиденды по crisis-сценарию. Не прогноз и не ИИР. Экспозиция к нефти/рублю требует факторной модели — не выдумываем.</div>
  </div>`;
}

// ── модуль 8: Smart Rebalancer ───────────────────────────────────────────────
/* ── UI модуля «Эффективность портфеля» ─────────────────────────────────────
   График рисуется inline-SVG: внешние библиотеки запрещены CSP, а зависимость
   ради одного scatter-графика не оправдана. */

function efChartSVG(a, activeName) {
  const W = 620, H = 320, P = { l: 54, r: 16, t: 14, b: 40 };
  const pts = a.frontier.points;
  const xs = pts.map((p) => p.vol).concat([a.current.vol]);
  const ys = pts.map((p) => p.ret).concat([a.current.ret]);
  const pad = (v) => (v > 0 ? v * 0.12 : 0.02);
  const x0 = Math.max(0, Math.min(...xs) - pad(Math.min(...xs))), x1 = Math.max(...xs) + pad(Math.max(...xs));
  const y0 = Math.min(...ys) - pad(Math.abs(Math.min(...ys))), y1 = Math.max(...ys) + pad(Math.abs(Math.max(...ys)));
  const X = (v) => P.l + ((v - x0) / ((x1 - x0) || 1)) * (W - P.l - P.r);
  const Y = (v) => H - P.b - ((v - y0) / ((y1 - y0) || 1)) * (H - P.t - P.b);

  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.vol).toFixed(1)},${Y(p.ret).toFixed(1)}`).join('');
  const ticks = (lo, hi, n) => Array.from({ length: n + 1 }, (_, i) => lo + ((hi - lo) * i) / n);
  const gridX = ticks(x0, x1, 4).map((v) =>
    `<line class="ef-grid" x1="${X(v).toFixed(1)}" y1="${P.t}" x2="${X(v).toFixed(1)}" y2="${H - P.b}"/>
     <text class="ef-ax" x="${X(v).toFixed(1)}" y="${H - P.b + 16}" text-anchor="middle">${ru(v * 100, 0)}%</text>`).join('');
  const gridY = ticks(y0, y1, 4).map((v) =>
    `<line class="ef-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>
     <text class="ef-ax" x="${P.l - 8}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v * 100, 0)}%</text>`).join('');

  const marks = a.scenarios.map((s) => {
    const on = s.name === activeName;
    return `<circle class="ef-pt ef-pt-${esc(s.name)}${on ? ' on' : ''}" cx="${X(s.vol).toFixed(1)}" cy="${Y(s.ret).toFixed(1)}" r="${on ? 7 : 5}"><title>${esc(s.label)}: доходность ${ru(s.ret * 100, 1)}%, риск ${ru(s.vol * 100, 1)}%</title></circle>`;
  }).join('');

  return `<svg class="ef-svg" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Граница эффективности: текущий портфель — доходность ${ru(a.current.ret * 100, 1)}%, риск ${ru(a.current.vol * 100, 1)}%">
    ${gridX}${gridY}
    <path class="ef-frontier" d="${path}"/>
    ${marks}
    <circle class="ef-cur" cx="${X(a.current.vol).toFixed(1)}" cy="${Y(a.current.ret).toFixed(1)}" r="7">
      <title>Текущий портфель: доходность ${ru(a.current.ret * 100, 1)}%, риск ${ru(a.current.vol * 100, 1)}%</title></circle>
    <text class="ef-axlbl" x="${(W / 2).toFixed(0)}" y="${H - 6}" text-anchor="middle">риск — годовая волатильность</text>
    <text class="ef-axlbl" x="14" y="${(H / 2).toFixed(0)}" text-anchor="middle" transform="rotate(-90 14 ${(H / 2).toFixed(0)})">оценочная доходность, годовых</text>
  </svg>`;
}

function efScenarioHTML(a, name) {
  const s = a.scenarios.find((x) => x.name === name) || a.scenarios[0];
  if (!s) return '';
  const dRet = (s.ret - a.current.ret) * 100, dVol = (s.vol - a.current.vol) * 100;
  const sign = (v, d) => `${v > 0 ? '+' : ''}${ru(v, d)}`;
  const rows = a.tickers.map((tk, i) => ({ tk, cur: a.current.w[i] * 100, tgt: s.w[i] * 100, lots: s.exec.rows[i] }))
    .filter((r) => Math.abs(r.tgt - r.cur) > 0.5)
    .sort((x, y) => Math.abs(y.tgt - y.cur) - Math.abs(x.tgt - x.cur)).slice(0, 12);

  const changes = rows.length ? `<table class="pfx-tbl ef-tbl"><thead><tr>
      <th class="left">Бумага</th><th>Сейчас</th><th>Сценарий</th><th>Δ</th><th>Лотов</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="left"><b>${esc(r.tk)}</b></td>
        <td class="tnum">${ru(r.cur, 1)}%</td><td class="tnum">${ru(r.tgt, 1)}%</td>
        <td class="tnum ${r.tgt > r.cur ? 'ef-up' : 'ef-dn'}">${sign(r.tgt - r.cur, 1)} п.п.</td>
        <td class="tnum">${r.lots && r.lots.ok ? ru(r.lots.lots, 0) : mdash}</td></tr>`).join('')}
    </tbody></table>` : `<div class="pfx-note">Веса сценария практически совпадают с текущими.</div>`;

  return `<div class="ef-cmp">
      <div><span>Оценочная доходность</span><b class="tnum">${ru(s.ret * 100, 1)}%</b><small>${sign(dRet, 1)} п.п. к текущей</small></div>
      <div><span>Риск (волатильность)</span><b class="tnum">${ru(s.vol * 100, 1)}%</b><small>${sign(dVol, 1)} п.п. к текущему</small></div>
      <div><span>Sharpe</span><b class="tnum">${s.sharpe == null ? mdash : ru(s.sharpe, 2)}</b><small>текущий ${a.current.sharpe == null ? mdash : ru(a.current.sharpe, 2)}</small></div>
      <div><span>Оборот</span><b class="tnum">${ru(s.turnover * 100, 0)}%</b><small>переложить ${fmtRub(s.turnover * a.capital)}</small></div>
      <div><span>Разовые издержки</span><b class="tnum">${fmtRub(s.costs.rub)}</b><small>${ru(s.costs.pctOfCapital, 2)}% капитала · ${s.costs.feeBps} б.п.</small></div>
      <div><span>Остаток в деньгах</span><b class="tnum">${fmtRub(s.exec.cash)}</b><small>после округления по лотам</small></div>
    </div>
    <div class="pfx-note muted">${esc(s.note)}</div>
    ${changes}`;
}

/** Таблица исключений: тикер · тип · статус · история · причина · что делать.
 *  Заменяет прежний плоский список одинаковых причин — теперь по каждой строке видно,
 *  это опечатка в коде, недавнее размещение или снятая с торгов бумага. */
function efExclusionsHTML(a) {
  const rows = [...a.excluded].sort((x, y) => (y.value || 0) - (x.value || 0));
  const sumExcl = rows.reduce((s, e) => s + (e.value || 0), 0);
  const share = a.capital + sumExcl > 0 ? (sumExcl / (a.capital + sumExcl)) * 100 : 0;
  const dupWarn = (e) => (e.suggest && (a.tickers || []).includes(e.suggest))
    ? ` <span class="ef-dup" title="Объединять позиции нельзя молча: количество и средняя цена изменятся">${esc(e.suggest)} уже есть в портфеле</span>` : '';
  const body = rows.map((e) => {
    const st = EF_STATUS[e.status] || { label: e.status || '—', tone: 'neut' };
    return `<tr>
      <td class="left"><b>${esc(e.ticker)}</b></td>
      <td class="left">${esc(e.type || '—')}</td>
      <td class="left"><span class="ef-st ef-st-${st.tone}">${esc(st.label)}</span></td>
      <td class="tnum">${e.months ? e.months + ' мес.' : mdash}</td>
      <td class="left">${esc(e.reason || '')}${dupWarn(e)}</td>
      <td class="left muted">${esc(e.action || '')}</td>
    </tr>`;
  }).join('');
  return `<details class="ef-excl" open>
    <summary>Вне оптимизации: ${rows.length} ${plural(rows.length, 'позиция', 'позиции', 'позиций')}
      на ${fmtRub(sumExcl)} (${ru(share, 0)}% портфеля)</summary>
    <div class="ef-excl-wrap"><table class="pfx-tbl ef-tbl"><thead><tr>
      <th class="left">Тикер</th><th class="left">Тип</th><th class="left">Статус</th>
      <th>История</th><th class="left">Причина</th><th class="left">Что делать</th>
    </tr></thead><tbody>${body}</tbody></table></div>
    <p class="muted">Эти позиции <b>остаются в портфеле</b>: стоимость, вложенная сумма, P&L, веса и
      концентрация считаются по ним полностью. Они не участвуют только в оптимизации и метриках,
      которым нужна история. Тикеры никогда не подменяются автоматически — подсказка требует
      вашего подтверждения, потому что иначе можно незаметно создать дубль позиции.</p></details>`;
}

function pfxFrontierHTML(c) {
  const a = efAnalyze(c.positions, { rf: (c.rf && isNum(c.rf.annual)) ? c.rf.annual / 100 : 0 });
  c._ef = a;
  if (a.status !== 'ok') {
    const why = {
      low_coverage: `бумаги с историей покрывают лишь ${ru(a.coverage || 0, 0)}% стоимости портфеля (нужно ≥${EF.COV_MIN}%)`,
      too_few_assets: 'нужно минимум 2 бумаги с историей доходностей',
      insufficient_history: `нужно минимум ${EF.MIN_OBS} общих месяцев истории`,
      no_value: 'не удалось определить стоимость позиций',
      solver_failed: 'оптимизатор не сошёлся на этих данных',
      frontier_empty: 'при заданных ограничениях допустимых портфелей не нашлось',
    }[a.reason] || 'недостаточно данных';
    const exc = (a.excluded || []).length
      ? `<div class="pfx-note muted">Вне расчёта: ${a.excluded.slice(0, 8).map((e) => `${esc(e.ticker)} (${esc(e.reason)})`).join('; ')}.</div>` : '';
    return `<div class="pfx-note">${NA}: ${esc(why)}. Граница эффективности не показывается — рисовать убедительный график на недостаточных данных нельзя.${exc ? '' : ''}</div>${exc}`;
  }

  const first = (a.scenarios.find((s) => s.name === 'same_return') || a.scenarios[0]).name;
  const verdict = a.gap.dominated
    ? `Портфель <b>доминируется</b>: при сопоставимой оценочной доходности модельная волатильность могла быть ниже на <b>${ru(a.gap.deltaPp, 1)} п.п.</b> (${ru(a.gap.relPct, 0)}% относительно текущей).`
    : `Портфель лежит <b>близко к границе</b>: заметного запаса по снижению риска при той же оценочной доходности модель не видит.`;

  const btns = a.scenarios.map((s, i) =>
    `<button class="pfx-rbtn ef-btn${s.name === first ? ' on' : ''}" data-ef="${esc(s.name)}" aria-pressed="${s.name === first}">${esc(s.label)}</button>`).join('');

  const excl = a.excluded.length ? efExclusionsHTML(a) : '';

  const warn = a.warnings.length
    ? `<div class="ef-warn">⚠ ${a.warnings.map(esc).join(' · ')}</div>` : '';

  const stab = a.stability.ok
    ? `Устойчивость сценария: <b>${esc(a.stability.label)}</b> (расхождение весов между половинами истории ${ru(a.stability.divergence * 100, 0)}%).`
    : 'Устойчивость не оценивалась: истории хватает лишь на один период.';

  return `${warn}
    <div class="ef-verdict">${verdict}</div>
    <div class="ef-chart">${efChartSVG(a, first)}</div>
    <div class="ef-legend">
      <span class="ef-lg ef-lg-cur">Текущий портфель</span>
      <span class="ef-lg ef-lg-fr">Граница эффективности</span>
      <span class="ef-lg ef-lg-sc">Сценарии</span>
    </div>
    <div class="pfx-rbtns">${btns}</div>
    <div id="pfx-ef-body">${efScenarioHTML(a, first)}</div>
    <div class="pfx-note muted">${stab}</div>
    ${excl}
    <div class="pfx-note muted">Расчёт на <b>${a.obs} мес.</b> общей истории по ${a.tickers.length} ${plural(a.tickers.length, 'бумаге', 'бумагам', 'бумагам')},
      покрытие <b>${ru(a.coverage, 0)}%</b> стоимости портфеля. База — месячный <b>total return</b> (цена MOEX + фактические дивиденды),
      номинальный рубль, до налогов. Ковариация — Ledoit–Wolf (сжатие ${ru(a.model.shrinkage * 100, 1)}%),
      ожидаемая доходность — robust estimate: James–Stein со сжатием ${ru(a.model.lambda * 100, 1)}% к общему среднему ${ru(a.model.anchor * 100, 1)}% годовых.
      Перераспределение только между уже имеющимися бумагами, без плеча и коротких позиций, потолок ${ru(a.cap * 100, 0)}% на бумагу.
      Ожидаемая доходность — <b>модельная оценка по прошлому</b>, а не прогноз. Не ИИР.</div>`;
}

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
    `<tr><td class="left">${instrumentIdentityHTML(x.ticker, (c.positions.find((p) => p.ticker === x.ticker) || {}).name, '', 'sm', { variant: 'compact', showTypeText: false })}</td><td class="tnum">${PN(x.cur, 1)}</td><td class="tnum">${PN(x.sug, 1)}</td>
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
      <td class="left">${instrumentIdentityHTML(p.ticker, p.name || (p.t && p.t.name), instrumentTypeHint(p.t), 'sm')}<span class="instrument-sector">${esc(p.sector)}</span></td>
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
// Кнопки дашборда (copy/export) — стабильны, вяжутся ОДИН раз в renderMyPortfolio.
function pfxWireDashboard(c) {
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
}

// Кнопки внутри панели вкладки (ребалансер) — панель перерисовывается на каждой смене вкладки,
// DOM свежий, поэтому addEventListener без риска двойных listener'ов.
function pfxWirePanel() {
  // Две группы кнопок с одним классом .pfx-rbtn: ребалансер (data-mode) и
  // «Эффективность портфеля» (data-ef). Различаем по атрибуту и подсвечиваем
  // только внутри своей группы — иначе клик в одном блоке гасил активную
  // кнопку в другом и рендерил чужое тело.
  document.querySelectorAll('.pfx-rbtn[data-mode]').forEach((btn) => btn.addEventListener('click', () => {
    document.querySelectorAll('.pfx-rbtn[data-mode]').forEach((b) => b.classList.toggle('on', b === btn));
    const body = document.getElementById('pfx-rebal-body');
    if (body && PFX_STATE) body.innerHTML = pfxRebalScenarioHTML(PFX_STATE, btn.dataset.mode);
  }));
  document.querySelectorAll('.pfx-rbtn[data-ef]').forEach((btn) => btn.addEventListener('click', () => {
    document.querySelectorAll('.pfx-rbtn[data-ef]').forEach((b) => {
      const on = b === btn;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
    const body = document.getElementById('pfx-ef-body');
    const chart = document.querySelector('.ef-chart');
    if (PFX_STATE && PFX_STATE._ef && PFX_STATE._ef.status === 'ok') {
      if (body) body.innerHTML = efScenarioHTML(PFX_STATE._ef, btn.dataset.ef);
      if (chart) chart.innerHTML = efChartSVG(PFX_STATE._ef, btn.dataset.ef);   // подсветка активной точки
    }
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
      ${instrumentAvatarHTML(t.ticker, t.name, instrumentTypeHint(t), 'md')}
      <span class="mp-sug-copy"><b>${esc(t.ticker)}</b><span>${esc(t.name || '')}${isNum(t.price) ? ' · ' + ru(t.price, 2) + '₽' : (t._extra ? ' · нет истории' : '')}</span></span></div>`).join('');
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
  const method = document.getElementById('pf-method').value;
  const isOpt = method.startsWith('opt');
  const factorTilt = wsel.querySelector('option[value="factor_tilt"]');
  if (factorTilt) factorTilt.disabled = method !== 'quality';
  if (method !== 'quality' && wsel.value === 'factor_tilt') wsel.value = method === 'momentum' ? 'score' : 'equal';
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
    if (el) el.addEventListener('change', () => {
      if (id === 'pf-method') {
        if (el.value === 'marlamov') {
          document.getElementById('pf-n').value = '10';
          document.getElementById('pf-weight').value = 'equal';
        }
        if (el.value === 'momentum') document.getElementById('pf-weight').value = 'score';
        if (el.value === 'quality') document.getElementById('pf-weight').value = 'factor_tilt';
        syncStrategyPanels();
      }
      syncWeightControl();
      if (document.getElementById('pf-out').dataset.shown) renderPortfolio();
    });
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
  fetch(dataURL('marketsaw.json'))   // cache-bust, как returns.json
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
  // Динамика напряжения. Сама 20-дневная реализованная волатильность физически НЕ может
  // быстро упасть после отскока — окно ещё содержит дни распродажи, поэтому карточка
  // выглядела «замершей» и противоречила тому, что видит пользователь на рынке.
  // История vols уже посчитана выше, так что направление доступно без доп. вычислений.
  const prevIdx = vols.length - 1 - 20;
  const volPrev = prevIdx >= 0 ? vols[prevIdx] : null;
  const scorePrev = volPrev != null
    ? Math.round((vols.filter((v) => v <= volPrev).length / vols.length) * 100) : null;
  return {
    current_vol: current,
    vol_prev_20d: volPrev,
    vol_change: volPrev != null ? current - volPrev : null,
    score_change: scorePrev != null ? score - scorePrev : null,
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

function alfaSafeUrl(value) {
  try {
    const parsed = new URL(String(value || ''), location.href);
    const host = parsed.hostname.toLowerCase();
    return parsed.protocol === 'https:' && (host === 'alfabank.ru' || host.endsWith('.alfabank.ru')) ? parsed.href : '';
  } catch (_) { return ''; }
}

function alfaDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return 'дата не указана';
  const [year, month, day] = String(value).split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
    .format(new Date(year, month - 1, day));
}

function loadAlfaIndex(cb) {
  if (ALFA_INDEX) { cb && cb(); return; }
  if (ALFA_INDEX_LOAD) { ALFA_INDEX_LOAD.then(() => cb && cb()).catch((e) => cb && cb(e)); return; }
  const getJSON = (path) => fetch(dataURL(path))
    .then((response) => { if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`); return response.json(); });
  ALFA_INDEX_LOAD = Promise.allSettled([getJSON('alfa-index.json'), getJSON('alfa-index-history.json')])
    .then((results) => {
      if (results[0].status !== 'fulfilled') throw results[0].reason;
      const current = results[0].value;
      if (!current || !Number.isInteger(current.value) || current.value < 0 || current.value > 100) {
        throw new Error('alfa-index.json: некорректное значение');
      }
      ALFA_INDEX = current;
      ALFA_INDEX_HISTORY = results[1].status === 'fulfilled' && Array.isArray(results[1].value) ? results[1].value : [];
    })
    .finally(() => { ALFA_INDEX_LOAD = null; });
  ALFA_INDEX_LOAD.then(() => cb && cb()).catch((e) => { console.warn('[alfa-index]', e); cb && cb(e); });
}

function alfaStatusText(data) {
  if (data.status === 'source_unavailable') return 'Источник временно недоступен';
  if (data.status === 'no_fresh_publication') return 'Новая публикация пока не найдена';
  if (data.stale) return 'Последнее доступное значение';
  return 'Данные актуальны';
}

function alfaChangeHTML(data) {
  if (!isNum(data.change)) return '<span class="alfa-change alfa-flat">Нет предыдущего отличающегося значения</span>';
  const direction = data.change > 0 ? 'up' : data.change < 0 ? 'down' : 'flat';
  const sign = data.change > 0 ? '+' : data.change < 0 ? '−' : '';
  const points = Math.abs(data.change);
  const ending = points % 10 === 1 && points % 100 !== 11 ? 'пункт' : (points % 10 >= 2 && points % 10 <= 4 && !(points % 100 >= 12 && points % 100 <= 14) ? 'пункта' : 'пунктов');
  return `<span class="alfa-change alfa-${direction}">${sign}${ru(points, 0)} ${ending} к предыдущему отличающемуся значению</span>`;
}

function alfaIndexHTML(data, history) {
  const source = data.source || {};
  const sourceUrl = alfaSafeUrl(source.url);
  const label = (data.site_interpretation || {}).label || 'Без интерпретации';
  const zone = (data.site_interpretation || {}).zone || 'neutral';
  const value = Math.max(0, Math.min(100, data.value));
  const statusClass = data.stale || data.status !== 'ok' ? 'stale' : 'fresh';
  const validHistory = (history || []).filter((row) => row && /^\d{4}-\d{2}-\d{2}$/.test(row.article_date) && Number.isInteger(row.value) && row.value >= 0 && row.value <= 100).slice(-30);
  const chart = validHistory.length >= 2 ? `
      <div class="alfa-history">
        <div class="alfa-history-head"><span>Динамика</span><em>${validHistory.length} последних наблюдений</em></div>
        <div class="alfa-chart" id="alfa-index-chart" role="img" aria-label="История Альфа-Индекса: от ${validHistory[0].value} до ${validHistory[validHistory.length - 1].value} пунктов"></div>
      </div>` : '';
  return `<section class="alfa-index-card alfa-zone-${esc(zone)}${chart ? ' has-chart' : ''}">
    <div class="alfa-summary">
      <div class="alfa-title-row">
        <div><span class="alfa-eyebrow">Внешний индикатор настроения</span><h2>Настроение российского рынка</h2></div>
        <span class="alfa-info" tabindex="0" data-tooltip="Альфа-Индекс публикуется Альфа-Инвестициями. Методика расчёта и официальные границы зон сайту не раскрыты.">ⓘ</span>
      </div>
      <div class="alfa-reading"><strong>${value}</strong><span>/ 100</span><b>${esc(label)}</b></div>
      ${alfaChangeHTML(data)}
      <div class="alfa-freshness alfa-${statusClass}"><i aria-hidden="true"></i>${esc(alfaStatusText(data))} · публикация от ${esc(alfaDate(source.article_date))}</div>
    </div>
    <div class="alfa-scale-panel">
      <div class="alfa-scale-labels" aria-hidden="true"><span>0</span><span>20</span><span>40</span><span>60</span><span>80</span><span>100</span></div>
      <div class="alfa-scale" role="meter" aria-label="Альфа-Индекс" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}" aria-valuetext="${value} из 100, ${esc(label)}">
        <div class="alfa-scale-zones" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>
        <span class="alfa-marker" style="left:${value}%" aria-hidden="true"><i></i><b>${value}</b></span>
      </div>
      <div class="alfa-zone-labels"><span>Осторожно</span><span>Нейтрально</span><span>Оптимистично</span></div>
      <p>Диапазоны интерпретированы сайтом и не являются официальной классификацией Альфа-Инвестиций.</p>
    </div>
    ${chart}
    <footer class="alfa-footer">
      <span>Альфа-Индекс отражает оценку текущего настроения рынка; не используйте его как самостоятельный торговый сигнал.</span>
      ${sourceUrl ? `<a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">Данные Альфа-Инвестиций ↗</a>` : '<span>Источник: Альфа-Инвестиции</span>'}
    </footer>
  </section>`;
}

function destroyAlfaChart() {
  if (ALFA_INDEX_RESIZE) { ALFA_INDEX_RESIZE.disconnect(); ALFA_INDEX_RESIZE = null; }
  if (ALFA_INDEX_CHART) { ALFA_INDEX_CHART.remove(); ALFA_INDEX_CHART = null; }
}

function renderAlfaChart(history) {
  const container = document.getElementById('alfa-index-chart');
  if (!container) return;
  const rows = (history || []).filter((row) => row && /^\d{4}-\d{2}-\d{2}$/.test(row.article_date) && Number.isInteger(row.value) && row.value >= 0 && row.value <= 100).slice(-30);
  if (rows.length < 2) return;
  loadLWC((error) => {
    if (error || !window.LightweightCharts || !document.body.contains(container)) {
      container.innerHTML = '<span class="muted">Мини-график временно недоступен.</span>';
      return;
    }
    destroyAlfaChart();
    const chart = LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: 112,
      layout: { background: { color: 'transparent' }, textColor: '#697386', fontFamily: 'system-ui, sans-serif', fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: '#E7EAF0' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.18, bottom: 0.18 } },
      timeScale: { borderVisible: false, timeVisible: false, rightOffset: 0, barSpacing: 18, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { vertLine: { labelVisible: false }, horzLine: { labelVisible: true } },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addLineSeries({ color: '#315F78', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    series.setData(rows.map((row) => ({ time: row.article_date, value: row.value })));
    series.createPriceLine({ price: 50, color: '#9AA3B3', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '50' });
    chart.timeScale().fitContent();
    ALFA_INDEX_CHART = chart;
    if (window.ResizeObserver) {
      ALFA_INDEX_RESIZE = new ResizeObserver(() => {
        if (ALFA_INDEX_CHART && container.clientWidth > 0) ALFA_INDEX_CHART.applyOptions({ width: container.clientWidth });
      });
      ALFA_INDEX_RESIZE.observe(container);
    }
  });
}

function renderAlfaIndex() {
  const element = document.getElementById('alfa-index-card');
  if (!element) return;
  if (ALFA_INDEX) {
    destroyAlfaChart();
    element.innerHTML = alfaIndexHTML(ALFA_INDEX, ALFA_INDEX_HISTORY || []);
    renderAlfaChart(ALFA_INDEX_HISTORY || []);
    return;
  }
  element.innerHTML = '<div class="pulse-loading muted">Загрузка индикатора настроения...</div>';
  loadAlfaIndex((error) => {
    if (error || !ALFA_INDEX) {
      element.innerHTML = '<div class="alfa-index-fallback"><b>Показатель настроения временно недоступен.</b><span>Остальные рыночные индикаторы продолжают работать.</span></div>';
      return;
    }
    destroyAlfaChart();
    element.innerHTML = alfaIndexHTML(ALFA_INDEX, ALFA_INDEX_HISTORY || []);
    renderAlfaChart(ALFA_INDEX_HISTORY || []);
  });
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
    // Убраны три карточки, которые были служебной телеметрией, а не сигналом инвестору:
    //   «Свежесть цен N/M»    — состояние пайплайна; свежесть уже показывает чип в шапке,
    //                           причём с датами по каждому блоку. В вечернем прогоне при
    //                           недоступности ISS она честно показывала 0/238 и пугала,
    //                           хотя цены за нужную дату лежали в кэше;
    //   «Сильные карточки»    — доля вердиктов good, метрика покрытия нашей же модели;
    //   «Акций / облигаций»   — инвентарный счётчик (убран из renderMarketKPI).
    // Инвестору здесь нужны только те числа, по которым он меняет поведение.
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

function sawSwitcherHTML() {
  // Без инлайнового onclick: CSP (§6.4) запрещает inline-обработчики — клик ловит делегированный
  // слушатель в initRouter() по data-saw-index.
  const b = (id, lbl) => `<button type="button" class="saw-tab${MARKET_SAW_ACTIVE === id ? ' saw-tab-active' : ''}" data-saw-index="${id}" aria-pressed="${MARKET_SAW_ACTIVE === id}">${lbl}</button>`;
  return `<div class="saw-switch" role="tablist">${b('MCFTR', 'Полная доходность · MCFTR')}${b('IMOEX', 'Ценовой индекс · IMOEX')}</div>`;
}

function setMarketSawIndex(id) {
  if (id !== 'MCFTR' && id !== 'IMOEX') return;
  MARKET_SAW_ACTIVE = id;
  const sw = document.querySelector('.saw-switch');
  if (sw) sw.querySelectorAll('.saw-tab').forEach((t) => {
    const on = t.dataset.sawIndex === id;
    t.classList.toggle('saw-tab-active', on);
    t.setAttribute('aria-pressed', String(on));
  });
  renderSawActive();
}

function renderMarketSaw() {
  const body = document.getElementById('saw-body');
  body.innerHTML = sawSwitcherHTML() + '<div id="saw-content"><div class="saw-loading muted">Загрузка…</div></div>';
  renderSawActive();
}

function renderSawActive() {
  const content = document.getElementById('saw-content');
  if (!content) return;
  content.innerHTML = '<div class="saw-loading muted">Загрузка…</div>';
  if (MARKET_SAW_ACTIVE === 'IMOEX') {
    loadImoexSaw((err) => {
      if (err || !IMOEX_SAW) { content.innerHTML = sawErrorHTML('IMOEX'); return; }
      content.innerHTML = sawImoexHTML(IMOEX_SAW);
      loadLWC((lerr) => {
        const c = document.getElementById('saw-chart');
        if (lerr || !window.LightweightCharts) { if (c) c.innerHTML = '<div class="muted saw-chart-fallback">График недоступен. Расчёт зон выше — корректен.</div>'; return; }
        try { sawImoexChart(IMOEX_SAW); } catch (e) { console.error('[saw-imoex] chart:', e); if (c) c.innerHTML = '<div class="muted saw-chart-fallback">Не удалось построить график.</div>'; }
      });
      fetchImoexLive();   // best-effort внутридневной снимок (не подтверждает экстремумы)
    });
    return;
  }
  loadMarketSaw((err) => {
    if (err || !SAW_DATA) { content.innerHTML = sawErrorHTML('MCFTR'); return; }
    content.innerHTML = sawUIHTML(SAW_DATA);
    loadLWC((lerr) => {
      const c = document.getElementById('saw-chart');
      if (lerr || !window.LightweightCharts) { if (c) c.innerHTML = '<div class="muted saw-chart-fallback">График недоступен (не загрузилась графическая библиотека). Расчёт фазы выше — корректен.</div>'; return; }
      try { sawChart(SAW_DATA); } catch (e) { console.error('[saw] chart:', e); if (c) c.innerHTML = '<div class="muted saw-chart-fallback">Не удалось построить график.</div>'; }
    });
  });
}

function loadImoexSaw(cb) {
  if (IMOEX_SAW) { cb(); return; }
  fetch(dataURL('marketsaw_imoex.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || j.index !== 'IMOEX' || !j.current_state || !j.series) throw new Error('пустой/битый marketsaw_imoex.json'); IMOEX_SAW = j; cb(); })
    .catch((e) => { console.error('[saw-imoex] не загрузился:', e); cb(e); });
}

function sawErrorHTML(idx) {
  return `<div class="saw-fallback">
    <b>Данные ${esc(idx || 'MCFTR')} временно недоступны.</b> Индикатор не обновлён.
    <div class="saw-disc">Индикатор не является индивидуальной инвестиционной рекомендацией и не прогнозирует будущую доходность.</div>
  </div>`;
}

// ── Ценовой контур IMOEX (swing/zigzag по ценовому индексу) ──
const IMOEX_ZONE = { buy: ['Зона покупки', 'good'], neutral: ['Нейтральная зона', 'neut'], fix: ['Зона фиксации', 'warn'] };

function sawImoexHTML(d) {
  const cs = d.current_state, hi = d.last_confirmed_high, lo = d.last_confirmed_low, lv = d.levels;
  const [zlbl, ztone] = IMOEX_ZONE[cs.zone] || ['—', 'neut'];
  const liveNote = cs.price_type === 'live'
    ? `<span class="saw-live-note muted" data-tooltip="Внутридневная котировка MOEX ISS; официальный CLOSE и подтверждённые экстремумы не меняет">Предварительное значение внутри торговой сессии</span>` : '';
  const oc = d.official_close || {};
  const kpi = (lbl, val, sub, tone) => `<div class="saw-imoex-kpi${tone ? ' saw-k-' + tone : ''}"><span>${lbl}</span><b>${val}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
  return `
    <p class="saw-sub">Публичная воспроизводимая реализация swing/zigzag-подхода по ЦЕНОВОМУ индексу IMOEX (порог разворота 7%, зона покупки −13%, зона фиксации +17%). Ценовой индекс не учитывает дивиденды — для полной картины см. MCFTR.</p>
    <div class="saw-fresh muted">Официальный CLOSE: <b>${esc(sawDate(oc.date || d.data_last))}</b>${cs.price_type === 'live' ? ` · снимок сессии обновлён ${new Date(IMOEX_LIVE_AT || Date.now()).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : ''} ${liveNote}</div>

    <div class="saw-imoex-grid">
      ${kpi('Текущее значение', ru(cs.price, 2), cs.price_type === 'live' ? 'внутри сессии' : 'закрытие', ztone)}
      ${kpi('Зона', zlbl, `от максимума ${sawPct(cs.move_from_high_pct)}`, ztone)}
      ${kpi('Уровень покупки (−13%)', lv.buy != null ? ru(lv.buy, 0) : '—', hi ? `от макс ${ru(hi.price, 0)} (${esc(sawDate(hi.date))})` : '')}
      ${kpi('Уровень фиксации (+17%)', lv.fix != null ? ru(lv.fix, 0) : '—', lo ? `от мин ${ru(lo.price, 0)} (${esc(sawDate(lo.date))})` : '')}
    </div>

    <div class="saw-col-title">Динамика IMOEX, зигзаг и ценовые уровни</div>
    <div id="saw-chart"></div>
    <div class="saw-chart-legend muted"><span class="lg-line"></span> IMOEX &nbsp; <span class="lg-zz"></span> зигзаг &nbsp; <span class="lg-low">▲</span> мин &nbsp; <span class="lg-high">▼</span> макс &nbsp; <span class="lg-buy">—</span> покупка &nbsp; <span class="lg-fix">—</span> фиксация</div>

    <p class="saw-imoex-expl">${esc(cs.explanation)}</p>
    <details class="pfx-dr-more"><summary>Как читать</summary>
      <div class="pfx-dr-sub muted">Максимум подтверждается лишь после падения на 7%, минимум — после отскока на 7% (никаких будущих данных). Уровень покупки = последний подтверждённый максимум −13%; уровень фиксации = последний подтверждённый минимум +17%. Зоны buy/fix могут перекрываться при широком диапазоне — приоритет у зоны покупки. Не сигнал, а ценовой ориентир. Движения: от максимума ${sawPct(cs.move_from_high_pct)}, от минимума ${sawPct(cs.move_from_low_pct)}.</div></details>
    <div class="saw-disc">Информационно, не индивидуальная инвестиционная рекомендация. Ценовой индекс IMOEX не учитывает дивиденды.</div>
  `;
}

function sawImoexChart(d) {
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
  const markers = d.extremes.map((e) => ({
    time: e.date, position: e.type === 'high' ? 'aboveBar' : 'belowBar',
    color: e.type === 'high' ? '#A2452C' : '#1E6F4C', shape: e.type === 'high' ? 'arrowDown' : 'arrowUp',
  })).sort((a, b) => (a.time < b.time ? -1 : 1));
  zz.setMarkers(markers);
  const cs = d.current_state;
  line.setMarkers([{ time: (d.official_close || {}).date || d.data_last, position: 'belowBar', color: '#2E3440', shape: 'circle', text: 'сейчас' }]);
  // горизонтальные ценовые уровни покупки/фиксации
  if (d.levels && d.levels.buy != null) line.createPriceLine({ price: d.levels.buy, color: '#1E6F4C', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: 'покупка ' + ru(d.levels.buy, 0) });
  if (d.levels && d.levels.fix != null) line.createPriceLine({ price: d.levels.fix, color: '#A2452C', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: 'фиксация ' + ru(d.levels.fix, 0) });
  try {
    const n = d.series.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 1 - 760), to: n - 1 + 72 });
  } catch (e) { chart.timeScale().fitContent(); }
}

// best-effort внутридневной снимок IMOEX. НЕ подтверждает экстремумы/волны/официальный CLOSE —
// только текущую позицию (price/zone/move). Троттлинг 60с, AbortController+timeout, БЕЗ постоянных
// таймеров (fetch только при открытии/переключении). Ошибка ISS не показывается — остаётся официальный snapshot.
function fetchImoexLive() {
  if (!IMOEX_SAW || MARKET_SAW_ACTIVE !== 'IMOEX') return;
  if (Date.now() - IMOEX_LIVE_AT < 60000) return;
  let ac; try { ac = new AbortController(); } catch (e) { return; }
  const to = setTimeout(() => { try { ac.abort(); } catch (e) {} }, 8000);
  fetch('https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/IMOEX.json?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,CURRENTVALUE,LASTVALUE,SYSTIME', { signal: ac.signal, cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((j) => {
      clearTimeout(to);
      const md = j && j.marketdata; if (!md || !md.data || !md.data.length) return;
      const ci = {}; md.columns.forEach((c, i) => { ci[c] = i; });
      const row = md.data[0];
      const cur = row[ci.CURRENTVALUE], lastv = row[ci.LASTVALUE];
      const price = (cur != null && cur > 0) ? cur : lastv;
      if (price == null || !(price > 0)) return;
      const d = IMOEX_SAW, hi = d.last_confirmed_high, lo = d.last_confirmed_low, lv = d.levels || {};
      const zone = (lv.buy != null && price <= lv.buy) ? 'buy' : (lv.fix != null && price >= lv.fix) ? 'fix' : 'neutral';
      d.current_state = Object.assign({}, d.current_state, {
        price: Math.round(price * 100) / 100, price_type: (cur != null && cur > 0) ? 'live' : 'official', zone,
        move_from_high_pct: hi ? price / hi.price - 1 : 0, move_from_low_pct: lo ? price / lo.price - 1 : 0,
      });
      IMOEX_LIVE_AT = Date.now();                       // ставим ДО re-render → вложенный fetchImoexLive троттлится
      if (MARKET_SAW_ACTIVE === 'IMOEX') renderSawActive();
    })
    .catch(() => { clearTimeout(to); });                // тихо: официальный snapshot остаётся
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
  // Шкала адаптивная: если движение вышло за штатный предел (напр. просадка −30% > 28%),
  // расширяем max, чтобы маркер «вы здесь» всегда оставался ВИДИМЫМ внутри трека (иначе он
  // прижимался к правому краю и обрезался overflow:hidden). +0.02 гарантирует pos < 100%.
  const moveAbsRaw = Math.abs(cp.move_pct);
  const max = Math.max(lastThr + 0.10, moveAbsRaw + 0.02);
  const pos = (moveAbsRaw / max) * 100;
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
let BONDS_SORT = { key: 'ytm_net', dir: -1 };
let BOND_SCREEN_MODE = 'safe';
let BOND_ANALYTICS_VIEW = 'relative';
let BOND_SCREEN_FILTERS = {
  minRating: 'A-', minYtm: '', maxYtm: '', maxDuration: '',
  minLiquidity: 45, sector: 'all', couponType: 'all', retailOnly: true, simpleOnly: true,
};
let BOND_USER_PORTFOLIO = [];
let BOND_USER_IMPORT_ERRORS = [];
let BOND_REBALANCE_MODE = 'full';
let BOND_COMPARE_SECIDS = [];
const CHARTJS_SRC = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';
const RATING_GROUP = (r) => { r = String(r || ''); return r.startsWith('AAA') ? 'aaa' : r.startsWith('AA') ? 'aa' : r.startsWith('A') ? 'a' : 'bbb'; };
const RATING_COLOR = { aaa: '#1E6F4C', aa: '#7FB069', a: '#D9A521', bbb: '#D77A33' };
const RATING_SOURCE_RU = { acra: 'АКРА', expert_ra: 'Эксперт РА', nkr: 'НКР' };
const HORIZON_RU = { short: 'Короткий (0–1 год)', mid: 'Средний (1–3 года)', long: 'Длинный (>3 лет)' };
const rub0 = (x) => isNum(x) ? Math.round(x).toLocaleString('ru-RU') + ' ₽' : ND;
// Копейки нужны там, где суммы мелкие и округление до рубля искажает: НКД, купон на бумагу,
// итог покупки одного лота. Для портфельных сумм по-прежнему rub0.
const rub2 = (x) => isNum(x)
  ? Number(x).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽' : ND;

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

const BOND_RATING_SCALE = new Map([
  ['BBB-', 1], ['BBB', 2], ['BBB+', 3],
  ['A-', 4], ['A', 5], ['A+', 6],
  ['AA-', 7], ['AA', 8], ['AA+', 9], ['AAA', 10],
]);

const BOND_SORT_FIELDS = {
  price_market: ['price_market', 'clean_price_pct'],
  ytm_market: ['ytm_market', 'ytm_gross_pct'],
  ytm_fair: ['ytm_fair', 'g_curve_yield_pct'],
  deviation: ['deviation', 'excess_spread_pp'],
  ytm_net: ['ytm_net', 'ytm_net_est_pct'],
  duration_years: ['duration_years', 'duration_value'],
  coupon_pct: ['coupon_pct'],
  liquidity: ['liquidity_score', 'valtoday', 'median_volume_20d_rub', 'value_today_rub'],
  g_spread: ['g_spread_pp'],
  dirty_price: ['dirty_price_per_lot_rub'],
};

function bondSortSecid(row) {
  return String((row || {}).secid || (row || {}).isin || '').toUpperCase();
}

function bondSortValue(row, key) {
  if (key === 'name') {
    const value = String((row || {}).name || bondSortSecid(row)).trim();
    return value || null;
  }
  if (key === 'rating') {
    const value = String((row || {}).rating || '').replace(/\s+/g, '').toUpperCase();
    return BOND_RATING_SCALE.has(value) ? BOND_RATING_SCALE.get(value) : null;
  }
  if (key === 'maturity') {
    const value = Date.parse((row || {}).maturity || (row || {}).maturity_date);
    return Number.isFinite(value) ? value : null;
  }
  const fields = BOND_SORT_FIELDS[key] || [];
  const field = fields.find((candidate) => (row || {})[candidate] != null);
  const raw = field ? (row || {})[field] : null;
  if (raw == null || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function compareBonds(a, b, sort = BONDS_SORT) {
  const av = bondSortValue(a, sort.key);
  const bv = bondSortValue(b, sort.key);
  const aMissing = av == null;
  const bMissing = bv == null;
  if (aMissing || bMissing) {
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    return bondSortSecid(a).localeCompare(bondSortSecid(b), 'ru');
  }
  const compared = typeof av === 'string' ? av.localeCompare(bv, 'ru') : av - bv;
  return compared === 0
    ? bondSortSecid(a).localeCompare(bondSortSecid(b), 'ru')
    : compared * sort.dir;
}

function sortedBonds(bonds, sort = BONDS_SORT) {
  if (!sort || !sort.key || !sort.dir) return bonds.slice().sort((a, b) => bondSortSecid(a).localeCompare(bondSortSecid(b), 'ru'));
  return bonds.slice().sort((a, b) => compareBonds(a, b, sort));
}

function bondSortHeaderHTML(key, label, tooltip = '') {
  const active = BONDS_SORT.key === key;
  const ariaSort = active ? (BONDS_SORT.dir === 1 ? 'ascending' : 'descending') : 'none';
  const arrow = active
    ? '<span class="bonds-sort-arrow" aria-hidden="true">' + (BONDS_SORT.dir === 1 ? '↑' : '↓') + '</span>'
    : '';
  return '<th data-bonds-sort="' + key + '" aria-sort="' + ariaSort + '"'
    + (active ? ' class="bonds-sort-active"' : '')
    + (tooltip ? ' data-tooltip="' + esc(tooltip) + '"' : '')
    + '><button type="button" class="bonds-sort-button">' + label + arrow + '</button></th>';
}

function wireBonds() {
  const el = document.getElementById('bondlab-workspace');
  if (!el) return;
  el.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-bondlab-tab]');
    if (!tab) return;
    BOND_LAB_TAB = tab.dataset.bondlabTab;
    drawBondLab();
  });
}

function loadBonds(cb) {
  if (BONDS) { cb(); return; }
  Promise.all([
    fetch(dataURL('bonds/screener.json')).then((r) => { if (!r.ok) throw new Error('screener ' + r.status); return r.json(); }),
    fetch(dataURL('bonds/chart_data.json')).then((r) => { if (!r.ok) throw new Error('chart ' + r.status); return r.json(); }),
    fetch(dataURL('bonds/portfolios.json')).then((r) => { if (!r.ok) throw new Error('portfolios ' + r.status); return r.json(); }),
    ...['universe.json', 'portfolio_presets.json', 'portfolio_validation.json', 'portfolio_last_valid.json'].map((name) =>
      fetch(dataURL('bonds/' + name)).then((r) => r.ok ? r.json() : null).catch(() => null)),
  ]).then(([screener, chart, portfolios, universe, presets, validation, lastValid]) => {
    if (!screener || !screener.bonds || !chart) throw new Error('пустые/битые JSON облигаций');
    if (universe && Array.isArray(universe.bonds) && window.BondRetail) {
      universe = { ...universe, bonds: window.BondRetail.classifyUniverse(universe.bonds) };
      BOND_USER_PORTFOLIO = window.BondRetail.loadPortfolio(window.localStorage).positions || [];
      const savedSettings = window.BondRetail.loadSettings(window.localStorage);
      BOND_SCREEN_FILTERS.minRating = savedSettings.minRating || BOND_SCREEN_FILTERS.minRating;
    }
    BONDS = {
      meta: screener.meta || {}, bonds: screener.bonds, chart,
      portfolios: (portfolios && portfolios.portfolios) || {},
      lab: { universe, presets, validation, lastValid },
    };
    cb();
  }).catch((e) => { console.error('[bonds] не загрузились:', e); cb(e); });
}

let BOND_LAB_TAB = 'portfolios';
let BOND_LAB_PROFILE = 'balanced';
let BOND_LAB_HORIZON = '3y';
let BOND_LAB_BUDGET = 1000000;
let BOND_LAB_BUDGET_ERROR = '';
let BOND_LAB_CURRENT_ALLOCATION = null;

function renderBondLab() {
  const panel = document.getElementById('bondlab-panel');
  if (!panel) return;
  if (!BONDS) panel.innerHTML = '<div class="bonds-loading muted">Загрузка Bond Portfolio Lab…</div>';
  loadBonds((err) => {
    if (err || !BONDS) { panel.innerHTML = bondsErrorHTML(); return; }
    drawBondLab();
  });
}

function drawBondLab() {
  const panel = document.getElementById('bondlab-panel');
  if (!panel || !BONDS) return;
  document.querySelectorAll('[data-bondlab-tab]').forEach((button) => {
    const active = button.dataset.bondlabTab === BOND_LAB_TAB;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  if (BOND_LAB_TAB === 'portfolios') panel.innerHTML = bondPortfolioLabHTML(BONDS.lab);
  else if (BOND_LAB_TAB === 'screener') panel.innerHTML = bondUniverseScreenerHTML(BONDS.lab && BONDS.lab.universe);
  else if (BOND_LAB_TAB === 'analytics') {
    panel.innerHTML = bondAnalyticsHTML(BONDS);
    if (BOND_ANALYTICS_VIEW === 'curve') loadChartJS((err) => { if (!err && window.Chart) bondsChart(BONDS); });
    if (BOND_ANALYTICS_VIEW === 'finder') renderFinder();
  }
  wireBondLabControls();
}

let BOND_SCREEN_QUERY = '';
let BOND_SCREEN_RATING = 'all';

function bondPct(value, digits = 1) {
  return isNum(value) ? Number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '%' : ND;
}

function bondOpenIdentityHTML(row, size = 'sm') {
  return `<button type="button" class="bond-open-button" data-bond-open="${esc(row.secid)}" aria-label="Открыть карточку ${esc(row.name || row.secid)}">${instrumentIdentityHTML(row.secid, row.name, 'bond', size, { showTypeBadge: false })}</button>`;
}

function bondSafetyBadgeHTML(row) {
  const safety = row.bond_safety || (window.BondRetail && window.BondRetail.classifyBond(row));
  if (!safety) return '<span class="bond-safe-badge unknown">Недостаточно данных</span>';
  if (safety.investable) return '<span class="bond-safe-badge checked">Проверено</span>';
  const high = safety.riskLevel === 'high';
  return `<span class="bond-safe-badge ${high ? 'high' : 'attention'}">${high ? 'Высокий риск' : 'Требует внимания'}</span>`;
}

function bondReasonListHTML(row, compact = false) {
  const safety = row.bond_safety || (window.BondRetail && window.BondRetail.classifyBond(row));
  if (!safety || !safety.reasonLabels || !safety.reasonLabels.length) return '';
  const labels = compact ? safety.reasonLabels.slice(0, 2) : safety.reasonLabels;
  return `<div class="bond-reasons">${labels.map((label) => `<span>${esc(label)}</span>`).join('')}${compact && safety.reasonLabels.length > 2 ? `<span>+${safety.reasonLabels.length - 2}</span>` : ''}</div>`;
}

function bondConfirmedYtmHTML(row) {
  const safety = row.bond_safety;
  if (safety && !safety.ytmConfirmed) return '<span class="bond-unconfirmed-yield">Расчёт требует проверки</span>';
  return bondPct(row.ytm_net_est_pct, 2);
}

function bondLabUnavailableHTML(target) {
  const d = (target && target.candidate_diagnostics) || {};
  return `<div class="bondlab-unavailable">
    <span class="bondlab-status">Портфель не сформирован</span>
    <h3>Текущая ликвидная выборка не позволяет соблюсти все ограничения</h3>
    <p>Для защитного горизонта 1 год требуется modified duration 0,35–1,10. В этом диапазоне сейчас ${d.issues_inside_duration_corridor ?? 0} выпуск одного эмитента, а профиль требует минимум 9 эмитентов и не более 10% на выпуск.</p>
    <p class="muted">Модель не расширяет corridor, не повышает концентрацию и не подставляет неликвидные бумаги. Можно выбрать другой горизонт или профиль.</p>
  </div>`;
}

function bondDistribution(rows, key, labelFn) {
  const sums = {};
  rows.forEach((row) => {
    const keyValue = labelFn ? labelFn(row) : (row[key] || 'Не определено');
    sums[keyValue] = (sums[keyValue] || 0) + Number(row.actual_weight || 0);
  });
  return Object.entries(sums).sort((a, b) => b[1] - a[1]).map(([label, weight]) =>
    `<div class="bondlab-bar"><span>${esc(label)}</span><b>${bondPct(weight * 100)}</b><i style="--w:${Math.min(100, weight * 100).toFixed(2)}%"></i></div>`).join('');
}

function bondCouponCalendar(rows, universe) {
  if (!window.BondRetail) return '<p class="muted">Модуль денежных потоков не загрузился.</p>';
  const schedule = window.BondRetail.cashflowSchedule(rows, (universe || {}).bonds || [], { taxRate: 0.13 });
  const entries = Object.entries(schedule.months || {}).sort();
  if (!entries.length) return '<p class="muted">В ближайшие 12 месяцев подтверждённые выплаты не найдены.</p>';
  const max = Math.max(...entries.map(([, item]) => Number(item.net_rub || 0)), 1);
  const bars = entries.map(([month, item]) => `<button type="button" data-bond-cash-month="${esc(month)}" title="Купоны gross ${rub0(item.coupon_gross_rub)}; возврат номинала ${rub0(item.principal_rub)}">
    <span>${esc(month)}</span><i style="--h:${Math.max(6, Number(item.net_rub || 0) / max * 100).toFixed(1)}%"></i><b>${rub0(item.net_rub)}</b>
  </button>`).join('');
  const flows = schedule.flows.map((flow) => `<tr data-cashflow-month="${esc(String(flow.date || '').slice(0, 7))}"><td>${esc(shortIsoDate(flow.date))}</td><td>${esc(flow.secid)}</td><td>${flow.type === 'coupon' ? 'Купон' : 'Возврат номинала'}</td><td class="tnum">${rub0(flow.gross_rub)}</td><td class="tnum">${rub0(flow.tax_rub)}</td><td class="tnum b-strong">${rub0(flow.net_rub)}</td></tr>`).join('');
  const monthlyAverage = entries.length ? schedule.coupon_net_rub / 12 : 0;
  return `<div class="bond-cash-kpis"><div><span>Купоны gross</span><b>${rub0(schedule.coupon_gross_rub)}</b></div><div><span>Купоны net</span><b>${rub0(schedule.coupon_net_rub)}</b></div><div><span>Возврат номинала</span><b>${rub0(schedule.principal_rub)}</b></div><div><span>Средний купон / месяц</span><b>${rub0(monthlyAverage)}</b></div></div>
    <div class="bondlab-coupons">${bars}</div>
    <details class="bond-cash-details"><summary>Выплаты по точным датам</summary><div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Дата</th><th>Выпуск</th><th>Тип</th><th>Gross</th><th>Налог, оценка</th><th>Net</th></tr></thead><tbody>${flows}</tbody></table></div><p>Возврат номинала показан отдельно и не считается инвестиционным доходом. Налог рассчитан оценочно по ставке 13% только для купонов.</p></details>`;
}

function bondPortfolioLabHTML(lab) {
  BOND_LAB_CURRENT_ALLOCATION = null;
  const presets = lab && lab.presets;
  const universe = lab && lab.universe;
  const validation = lab && lab.validation;
  const lastValid = lab && lab.lastValid;
  if (!presets || !universe || !validation) {
    return `<div class="bondlab-unavailable"><h3>Новый портфельный слой пока не опубликован</h3><p>Finder и G-кривая доступны в соседних вкладках. Последний неполный расчёт не подменяет валидный портфель.</p></div>`;
  }
  const currentGatePassed = validation.status === 'PASS' && validation.quality_gate && validation.quality_gate.status === 'PASS';
  const hasLastValid = lastValid && lastValid.allocations && Object.keys(lastValid.allocations).length > 0;
  if (!currentGatePassed && !hasLastValid) {
    return `<div class="bondlab-unavailable"><h3>Свежий расчёт не прошёл контроль качества</h3><p>Портфель не опубликован: нет предыдущего валидного snapshot. Finder и G-кривая остаются доступны отдельно.</p></div>`;
  }
  const key = `${BOND_LAB_PROFILE}:${BOND_LAB_HORIZON}`;
  const activePresets = currentGatePassed ? presets.presets : lastValid.presets;
  const activeAllocations = currentGatePassed ? presets.allocations : lastValid.allocations;
  const target = activePresets[key];
  const serverAllocation = activeAllocations[key];
  const profileConfig = presets.profiles[BOND_LAB_PROFILE] || {};
  const horizonConfig = presets.horizons[BOND_LAB_HORIZON] || {};
  let allocation = serverAllocation;
  BOND_LAB_BUDGET_ERROR = '';
  if (target && serverAllocation && Number(BOND_LAB_BUDGET) !== Number(serverAllocation.budget_rub)) {
    if (!window.BondLotAllocator) {
      allocation = null; BOND_LAB_BUDGET_ERROR = 'Модуль пересчёта лотов не загрузился.';
    } else {
      allocation = window.BondLotAllocator.allocate(target, universe, BOND_LAB_BUDGET, profileConfig, horizonConfig, presets.costs || {});
      if (!allocation || allocation.status !== 'CLIENT_VALIDATED') {
        BOND_LAB_BUDGET_ERROR = 'Для этой суммы нельзя сохранить текущий состав и все жёсткие ограничения. Увеличь бюджет или сбрось сумму.';
        allocation = null;
      }
    }
  }
  const profiles = Object.entries(presets.profiles || {}).map(([id, item]) =>
    `<button type="button" class="bondlab-choice${id === BOND_LAB_PROFILE ? ' active' : ''}" data-bond-profile="${esc(id)}">${esc(item.label)}</button>`).join('');
  const horizons = Object.entries(presets.horizons || {}).map(([id, item]) =>
    `<button type="button" class="bondlab-choice${id === BOND_LAB_HORIZON ? ' active' : ''}" data-bond-horizon="${esc(id)}">${esc(item.label)}</button>`).join('');
  const snapshotGeneratedAt = currentGatePassed ? universe.generated_at : lastValid.generated_at;
  const generated = shortIsoDate(String(snapshotGeneratedAt || '').slice(0, 10));
  const gateFailures = ((validation.quality_gate || {}).failures || []).join(', ');
  const staleNotice = currentGatePassed ? '' : `<div class="bondlab-stale"><b>Показан последний валидный расчёт от ${esc(generated)}</b><span>Свежий запуск не опубликован${gateFailures ? `: ${esc(gateFailures)}` : '.'}</span></div>`;
  const limits = presets.budget_limits || { minimum_rub: 250000, maximum_rub: 100000000, step_rub: 50000 };
  const head = `${staleNotice}<div class="bondlab-meta"><span class="${currentGatePassed ? 'bondlab-pass' : 'bondlab-last-valid'}">${currentGatePassed ? 'Quality gate: PASS' : 'Последний валидный snapshot'}</span><span>Данные ${esc(generated)}</span><span>${universe.bonds.length} выпусков</span><span>${Object.keys(activeAllocations).length}/15 портфелей доступны</span></div>
    <div class="bondlab-builder"><div><label>Профиль риска</label><div class="bondlab-choices">${profiles}</div><small>Минимальный рейтинг: ${esc(profileConfig.minimum_corporate_rating || ND)}</small></div><div><label>Горизонт <span class="bond-help" title="Горизонт задаёт целевую дюрацию и структуру портфеля, а не одну дату погашения.">?</span></label><div class="bondlab-choices">${horizons}</div><small>Это цель по процентному риску; выпуски могут погашаться позже.</small></div><div class="bondlab-budget"><label for="bondlab-budget">Сумма, ₽</label><div><input id="bondlab-budget" type="number" inputmode="numeric" min="${limits.minimum_rub}" max="${limits.maximum_rub}" step="${limits.step_rub}" value="${Number(BOND_LAB_BUDGET)}"><button class="btn" type="button" id="bondlab-recalculate">Рассчитать</button></div><small>${allocation && allocation.status === 'CLIENT_VALIDATED' ? 'лоты пересчитаны локально' : 'эталонный серверный расчёт'}</small></div></div>
    ${BOND_LAB_BUDGET_ERROR ? `<div class="bondlab-inline-error">${esc(BOND_LAB_BUDGET_ERROR)}</div>` : ''}`;
  if (!target || target.status === 'INFEASIBLE') return head + bondLabUnavailableHTML(target || {});
  if (!allocation) return head + `<div class="bondlab-unavailable"><h3>Пересчёт лотов для этой суммы недоступен</h3><p>${esc(BOND_LAB_BUDGET_ERROR)}</p><button class="btn" type="button" id="bondlab-reset-budget">Вернуть 1 000 000 ₽</button></div>`;
  BOND_LAB_CURRENT_ALLOCATION = allocation;

  const rows = allocation.positions || [];
  const investedWeight = rows.reduce((sum, row) => sum + Number(row.actual_weight || 0), 0);
  const weighted = (field) => investedWeight ? rows.reduce((sum, row) => sum + Number(row.actual_weight || 0) * Number(row[field] || 0), 0) / investedWeight : null;
  const ytmGross = weighted('ytm_gross_pct');
  const ytmNet = weighted('ytm_net_est_pct');
  const duration = weighted('duration_value');
  const weightedGSpread = weighted('g_spread_pp');
  const weightedPeerExcess = weighted('excess_spread_pp');
  const ofzWeight = rows.filter((row) => row.instrument_type === 'ofz').reduce((sum, row) => sum + Number(row.actual_weight || 0), 0);
  const bbbWeight = rows.filter((row) => row.rating_group === 'BBB').reduce((sum, row) => sum + Number(row.actual_weight || 0), 0);
  const issuerCount = new Set(rows.map((row) => row.issuer_id)).size;
  const dv01 = rows.reduce((sum, row) => sum + Number(row.dirty_amount_rub || 0) * Number(row.duration_value || 0) * 0.0001, 0);
  const rateShock100 = window.BondRetail ? window.BondRetail.stress(rows, { rateShockBp: 100, spreadShockBp: 0 }) : { combined_impact_rub: 0 };
  const rateShock200 = window.BondRetail ? window.BondRetail.stress(rows, { rateShockBp: 200, spreadShockBp: 0 }) : { combined_impact_rub: 0 };
  const spreadShock100 = window.BondRetail ? window.BondRetail.stress(rows, { rateShockBp: 0, spreadShockBp: 100 }) : { combined_impact_rub: 0 };
  const groupCoverage = window.BondRetail ? window.BondRetail.concentration(rows, universe.bonds, allocation.budget_rub) : { unknown_group_weight_pct: 100 };
  const table = rows.map((row) => `<tr>
    <td class="b-name">${bondOpenIdentityHTML(row)}<small>${esc(row.issuer_name || '')}</small></td>
    <td>${row.instrument_type === 'ofz' ? '<span class="bondlab-ofz">ОФЗ</span>' : esc(row.rating || ND)}</td>
    <td class="tnum">${bondPct(row.target_weight * 100)}</td><td class="tnum b-strong">${bondPct(row.actual_weight * 100)}</td>
    <td class="tnum">${Number(row.lots || 0).toLocaleString('ru-RU')}</td><td class="tnum">${rub0(row.dirty_price_per_lot_rub)}</td>
    <td class="tnum">${rub0(row.total_amount_rub)}</td><td class="tnum">${bondPct(row.ytm_net_est_pct, 2)}</td>
    <td class="tnum">${Number(row.duration_value).toFixed(2)}</td><td>${esc(shortIsoDate(row.maturity_date))}</td>
  </tr>`).join('');
  const cards = rows.map((row) => `<article class="bondlab-mobile-card">
    <header>${bondOpenIdentityHTML(row)}<span>${row.instrument_type === 'ofz' ? 'ОФЗ' : esc(row.rating || ND)}</span></header>
    <div><span>Доля<b>${bondPct(row.actual_weight * 100)}</b></span><span>Сумма<b>${rub0(row.total_amount_rub)}</b></span><span>Лоты<b>${Number(row.lots).toLocaleString('ru-RU')}</b></span><span>YTM net<b>${bondPct(row.ytm_net_est_pct, 2)}</b></span></div>
    <details><summary>Параметры выпуска</summary><p>${esc(row.issuer_name || '')} · ${esc(row.sector || 'Сектор не определён')}</p><p>Dirty/лот ${rub0(row.dirty_price_per_lot_rub)} · дюрация ${Number(row.duration_value).toFixed(2)} · погашение ${esc(shortIsoDate(row.maturity_date))}</p><p>${esc(row.reason_included || '')}</p></details>
  </article>`).join('');
  return `${head}
    <div class="bondlab-cockpit">
      <div><span>Бюджет</span><b>${rub0(allocation.budget_rub)}</b><small>${allocation.status === 'CLIENT_VALIDATED' ? 'локальный пересчёт' : 'server validated'}</small></div>
      <div><span>Инвестировано</span><b>${rub0(allocation.invested_with_costs_rub)}</b><small>включая издержки</small></div>
      <div><span>Издержки</span><b>${rub0(allocation.estimated_costs_rub)}</b><small>${allocation.commission_bps + allocation.slippage_bps} б.п.</small></div>
      <div><span>YTM gross</span><b>${bondPct(ytmGross, 2)}</b><small>оценка до НДФЛ</small></div>
      <div><span>YTM net</span><b>${bondPct(ytmNet, 2)}</b><small>упрощённая tax model</small></div>
      <div><span>Modified duration</span><b>${duration ? duration.toFixed(2) : ND}</b><small>цель ${target.target_duration.toFixed(2)}</small></div>
      <div><span>ОФЗ</span><b>${bondPct(ofzWeight * 100)}</b><small>минимум профиля соблюдён</small></div>
      <div><span>BBB</span><b>${bondPct(bbbWeight * 100)}</b><small>лимит ${bondPct(profileConfig.max_bbb * 100)}</small></div>
      <div><span>Остаток</span><b>${rub0(allocation.cash_rub)}</b><small>после лотов и издержек</small></div>
      <div><span>DV01</span><b>${rub0(dv01)}</b><small>оценка на +1 б.п.</small></div>
      <div><span>G-spread</span><b>${bondPct(weightedGSpread, 2)}</b><small>взвешенный</small></div>
      <div><span>Peer excess</span><b>${bondPct(weightedPeerExcess, 2)}</b><small>не сигнал покупки</small></div>
      <div><span>Диверсификация</span><b>${rows.length} / ${issuerCount}</b><small>выпусков / эмитентов</small></div>
    </div>
    <div class="bondlab-grid">
      <section class="bondlab-main"><div class="bondlab-block-head"><div><h3>Модельный список</h3><p>Целевые веса преобразованы в целые лоты по dirty price с комиссией ${allocation.commission_bps} б.п. и slippage ${allocation.slippage_bps} б.п.</p></div><div class="bondlab-actions"><button class="btn" type="button" id="bondlab-copy">Копировать</button><button class="btn" type="button" data-bond-download="csv">CSV</button><button class="btn" type="button" data-bond-download="json">JSON</button></div></div>
        <div class="bonds-table-wrap bondlab-desktop-table"><table class="bonds-table bondlab-portfolio-table"><thead><tr><th>Выпуск</th><th>Класс</th><th>Цель</th><th>Факт</th><th>Лоты</th><th>Dirty/лот</th><th>Сумма</th><th>YTM net</th><th>Дюр.</th><th>Погашение</th></tr></thead><tbody>${table}</tbody></table></div>
        <div class="bondlab-mobile-cards">${cards}</div>
      </section>
      <aside class="bondlab-side"><section><h3>Рейтинг</h3>${bondDistribution(rows, 'rating', (r) => r.instrument_type === 'ofz' ? 'ОФЗ' : (r.rating_group || 'Без рейтинга'))}</section><section><h3>Секторы</h3>${bondDistribution(rows, 'sector')}</section><section class="bondlab-shock"><h3>Стресс портфеля</h3><dl><div><dt>Ставка +100 б.п.</dt><dd>${rub0(rateShock100.combined_impact_rub)}</dd></div><div><dt>Ставка +200 б.п.</dt><dd>${rub0(rateShock200.combined_impact_rub)}</dd></div><div><dt>Кредитный spread +100 б.п.</dt><dd>${rub0(spreadShock100.combined_impact_rub)}</dd></div></dl><p>Линейная duration-оценка без convexity. Процентный и кредитный шоки рассчитаны отдельно.</p></section><section><h3>Группы компаний</h3><b>${bondPct(groupCoverage.unknown_group_weight_pct, 1)} не подтверждено</b><p>Неизвестная группа не считается диверсификацией. Лимит группы станет расчётным после появления проверенного ultimate parent.</p></section></aside>
    </div>
    <section class="bondlab-coupon-block"><div class="bondlab-block-head"><div><h3>Денежный календарь · 12 месяцев</h3><p>Купоны, оценка налога и возврат номинала для фактического количества облигаций.</p></div></div>${bondCouponCalendar(rows, universe)}</section>
    ${bondUserPortfolioHTML(allocation, universe, activeAllocations)}
    <details class="bonds-limits"><summary>Расширенные настройки · только просмотр</summary><div class="bonds-limits-body bondlab-advanced"><p>Custom optimization в браузере не имитируется: изменение ограничений требует полного MILP на сервере.</p><dl><div><dt>Мин. рейтинг</dt><dd>${esc(profileConfig.minimum_corporate_rating)}</dd></div><div><dt>ОФЗ минимум</dt><dd>${bondPct(profileConfig.minimum_ofz * 100)}</dd></div><div><dt>Выпуск / эмитент</dt><dd>${bondPct(profileConfig.max_issue * 100)} / ${bondPct(profileConfig.max_issuer * 100)}</dd></div><div><dt>Сектор</dt><dd>${bondPct(profileConfig.max_sector * 100)}</dd></div><div><dt>Дюрация</dt><dd>${horizonConfig.min}–${horizonConfig.max}</dd></div><div><dt>Мин. эмитентов</dt><dd>${profileConfig.minimum_issuers}</dd></div><div><dt>Ликвидность 20d</dt><dd>${rub0(profileConfig.minimum_median_volume_20d_rub)}</dd></div><div><dt>Участие в обороте</dt><dd>${bondPct(profileConfig.maximum_participation_rate * 100)}</dd></div></dl></div></details>
    <details class="bonds-limits"><summary>Как сформирован портфель</summary><div class="bonds-limits-body"><p>MILP максимизирует скорректированный carry при жёстких лимитах выпуска, эмитента, сектора, рейтинга, ОФЗ, дюрации, ликвидности и лестницы погашений. Затем отдельная integer-модель подбирает лоты и повторно проверяет ограничения.</p><p>Estimated net YTM — упрощённая модель налога, не персональный налоговый расчёт. Состав является исследовательским модельным списком для самостоятельной проверки, не индивидуальной рекомендацией.</p>${['7y','10y'].includes(BOND_LAB_HORIZON) ? '<p>Модель использует лестницу погашений и предполагает реинвестирование. Это не означает удержание одного неизменного набора облигаций весь горизонт.</p>' : ''}</div></details>`;
}

/** Импорт портфеля пользователя.
 *
 *  Разбор делегирован BondRetail.parsePortfolioText: он определяет разделитель, узнаёт
 *  заголовок по псевдонимам колонок и сопоставляет столбцы по имени, а не по позиции —
 *  своя копия этой логики во фронте только разошлась бы с расчётным слоем.
 */
function bondApplyImport(text) {
  if (!window.BondRetail) return;
  const parsed = window.BondRetail.parsePortfolioText(text);
  if (!parsed.positions.length && !parsed.errors.length) return;
  BOND_USER_PORTFOLIO = parsed.positions;
  BOND_USER_IMPORT_ERRORS = parsed.errors;
  if (window.BondRetail) window.BondRetail.savePortfolio(window.localStorage, BOND_USER_PORTFOLIO);
  drawBondLab();
}

function bondUserPortfolioHTML(allocation, universe, allocations) {
  if (!window.BondRetail) return '';
  const resolved = window.BondRetail.resolvePortfolio(BOND_USER_PORTFOLIO, universe.bonds || []);
  let target = allocation;
  let targetLabel = 'Текущий модельный портфель';
  if (BOND_REBALANCE_MODE === 'reduce_risk' && allocations[`defensive:${BOND_LAB_HORIZON}`]) {
    target = allocations[`defensive:${BOND_LAB_HORIZON}`]; targetLabel = 'Защитный профиль';
  } else if (BOND_REBALANCE_MODE === 'income' && allocations[`income:${BOND_LAB_HORIZON}`]) {
    target = allocations[`income:${BOND_LAB_HORIZON}`]; targetLabel = 'Доходный профиль';
  }
  const reconcileMode = BOND_REBALANCE_MODE === 'new_money' ? 'new_money' : 'full';
  const minTradeRub = BOND_REBALANCE_MODE === 'min_trades' ? 10000 : 3000;
  const noTradeBandPct = BOND_REBALANCE_MODE === 'min_trades' ? 1.5 : 0.5;
  const reconciliation = window.BondRetail.reconcile(resolved.recognized, target, universe.bonds, {
    mode: reconcileMode, minTradeRub, noTradeBandPct, commissionBps: target.commission_bps, slippageBps: target.slippage_bps,
  });
  const currentRows = resolved.recognized.map((position) => {
    const row = position.bond;
    const value = Number(row.dirty_price_per_bond_rub || 0) * Number(position.quantity_bonds || 0);
    return `<tr><td class="b-name">${bondOpenIdentityHTML(row)}<small>${esc(row.issuer_name || '')}</small></td><td class="tnum">${Number(position.quantity_bonds).toLocaleString('ru-RU')}</td><td class="tnum">${position.average_price == null ? ND : bondPct(position.average_price, 2)}</td><td class="tnum">${rub0(value)}</td><td>${bondSafetyBadgeHTML(row)}</td></tr>`;
  }).join('');
  const tradeRows = reconciliation.trades.filter((trade) => trade.action !== 'SKIP').map((trade) => {
    const row = (universe.bonds || []).find((bond) => bond.secid === trade.secid) || { secid: trade.secid, name: trade.secid };
    const labels = { BUY: 'Купить', SELL: 'Сократить', HOLD: 'Оставить', SKIP: 'Без сделки' };
    return `<tr><td class="b-name">${bondOpenIdentityHTML(row)}</td><td class="tnum">${trade.current_quantity}</td><td class="tnum">${trade.target_quantity}</td><td class="tnum ${trade.trade_lots > 0 ? 'b-up' : trade.trade_lots < 0 ? 'b-down' : ''}">${trade.trade_lots > 0 ? '+' : ''}${trade.trade_lots}</td><td class="tnum">${rub0(Math.abs(trade.trade_amount_rub))}</td><td><span class="bond-action ${trade.action.toLowerCase()}">${labels[trade.action]}</span></td><td>${trade.reason === 'NO_TRADE_BAND' ? 'Изменение меньше издержек / no-trade band' : trade.reason === 'BELOW_TARGET' ? 'Доля ниже цели' : trade.reason === 'ABOVE_TARGET' ? 'Доля выше цели' : 'В пределах цели'}</td></tr>`;
  }).join('');
  const alertRows = window.BondRetail.alerts(resolved.recognized, universe.bonds).slice(0, 8);
  const currentCalendar = resolved.recognized.length ? bondCouponCalendar(resolved.recognized, universe) : '';
  const importErrors = BOND_USER_IMPORT_ERRORS.length
    ? `<div class="bond-import-errors">Не удалось распознать строк: ${BOND_USER_IMPORT_ERRORS.length}. Проверь тикер/ISIN и количество.</div>` : '';
  const unknown = resolved.unrecognized.length
    ? `<div class="bond-unrecognized"><b>Не распознаны:</b> ${resolved.unrecognized.map((row) => esc(row.identifier)).join(', ')}. Они сохранены и не удалены.</div>` : '';
  return `<section class="bond-user-portfolio">
    <div class="bondlab-block-head"><div><h3>Мой текущий портфель облигаций</h3><p>Данные хранятся только в этом браузере и не отправляются на сервер.</p></div><div class="bondlab-actions"><button type="button" class="btn" id="bond-portfolio-backup" ${BOND_USER_PORTFOLIO.length ? '' : 'disabled'}>Резервная копия</button><button type="button" class="btn" id="bond-portfolio-clear" ${BOND_USER_PORTFOLIO.length ? '' : 'disabled'}>Удалить</button></div></div>
    <div class="bond-import-grid"><div><label for="bond-portfolio-paste">Вставь таблицу: тикер/ISIN, количество бумаг, средняя цена</label><textarea id="bond-portfolio-paste" rows="4" placeholder="SECID;Количество;Средняя цена\nRU000A107RZ0;100;101,20"></textarea><div class="bond-import-actions"><button type="button" class="btn" id="bond-portfolio-import">Импортировать</button><label class="btn bond-file-button">CSV<input type="file" id="bond-portfolio-file" accept=".csv,text/csv,text/plain"></label></div>${importErrors}${unknown}</div>
      <div class="bond-import-help"><b>Что произойдёт</b><p>Позиции сопоставятся со свежим universe. Нераспознанные строки останутся в резервной копии. Для расчёта используются текущие dirty price и целые лоты.</p><p>XLSX пока не читается в браузере: экспортируй файл брокера в CSV.</p></div></div>
    ${resolved.recognized.length ? `<div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Бумага</th><th>Количество</th><th>Средняя цена</th><th>Текущая dirty-стоимость</th><th>Проверка</th></tr></thead><tbody>${currentRows}</tbody></table></div>
      <div class="bond-rebalance-head"><div><h3>Сделки до модельной цели</h3><p>${esc(targetLabel)} · сделки меньше ${rub0(minTradeRub)} или ${bondPct(noTradeBandPct, 1)} бюджета пропускаются.</p></div><label>Режим<select id="bond-rebalance-mode"><option value="full"${BOND_REBALANCE_MODE === 'full' ? ' selected' : ''}>Полная ребалансировка</option><option value="new_money"${BOND_REBALANCE_MODE === 'new_money' ? ' selected' : ''}>Только новые деньги</option><option value="min_trades"${BOND_REBALANCE_MODE === 'min_trades' ? ' selected' : ''}>Минимум сделок</option><option value="reduce_risk"${BOND_REBALANCE_MODE === 'reduce_risk' ? ' selected' : ''}>Снизить риск</option><option value="income"${BOND_REBALANCE_MODE === 'income' ? ' selected' : ''}>Повысить денежный поток</option></select></label></div>
      <div class="bond-trade-summary"><span>Сделок <b>${reconciliation.trade_count}</b></span><span>Оборот <b>${rub0(reconciliation.turnover_rub)}</b></span><span>Цель <b>${esc(targetLabel)}</b></span></div>
      <div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Бумага</th><th>Сейчас, шт.</th><th>Цель, шт.</th><th>Сделка, лот.</th><th>Сумма</th><th>Действие</th><th>Причина</th></tr></thead><tbody>${tradeRows || '<tr><td colspan="7">Экономически значимых сделок нет.</td></tr>'}</tbody></table></div>
      ${alertRows.length ? `<div class="bond-alert-center"><h3>Предупреждения</h3>${alertRows.map((item) => `<button type="button" data-bond-open="${esc(item.secid)}"><span>${item.severity === 'high' ? 'Высокий риск' : 'Проверить'}</span><b>${esc(item.secid)}</b><small>${esc(item.message)}</small></button>`).join('')}</div>` : ''}
      <div class="bond-current-calendar"><div class="bondlab-block-head"><div><h3>Выплаты моего портфеля · 12 месяцев</h3><p>Купоны и возврат номинала показаны раздельно.</p></div></div>${currentCalendar}</div>`
      : `<div class="bond-portfolio-empty"><b>Портфель пока не добавлен</b><p>Импортируй CSV или вставь три колонки из таблицы брокера, чтобы увидеть реальные сделки, выплаты и предупреждения.</p></div>`}
  </section>`;
}

/** Скринер. По умолчанию показывает ТОЛЬКО прошедшие safe-фильтр выпуски.
 *
 *  Причина жёсткая: при сортировке по доходности наверх всплывали бумаги без рейтинга с
 *  YTM 325–373 % — расчёт по ним недостоверен (нераспознанная оферта, амортизация, битая
 *  цена), и показывать их лидерами скринера значит выдавать дефект данных за возможность.
 *  Такие выпуски не исчезают: они доступны в отдельном режиме с перечнем конкретных причин.
 */
function bondScreenerRows(universe) {
  const query = BOND_SCREEN_QUERY.toLowerCase();
  const f = BOND_SCREEN_FILTERS;
  const minRank = window.BondRetail ? window.BondRetail.ratingRank(f.minRating) : null;
  return (universe.bonds || []).filter((row) => {
    const safety = row.bond_safety;
    if (BOND_SCREEN_MODE === 'safe' && !(safety && safety.investable)) return false;
    if (BOND_SCREEN_MODE === 'risk' && safety && safety.investable) return false;
    if (query && !`${row.secid} ${row.name} ${row.issuer_name}`.toLowerCase().includes(query)) return false;
    if (BOND_SCREEN_RATING !== 'all') {
      if (BOND_SCREEN_RATING === 'ofz' ? row.instrument_type !== 'ofz' : row.rating_group !== BOND_SCREEN_RATING) return false;
    }
    // Фильтр минимального рейтинга не применяется:
    //  • к ОФЗ — у госбумаг рейтинг выпуска не выставляется;
    //  • в режиме высокого риска — иначе он прятал бы именно безрейтинговые выпуски,
    //    ради показа которых этот режим и существует.
    if (minRank && row.instrument_type !== 'ofz' && BOND_SCREEN_MODE !== 'risk') {
      const rank = window.BondRetail.ratingRank(row.rating);
      if (!rank || rank < minRank) return false;
    }
    if (f.maxDuration !== '' && isNum(Number(f.maxDuration)) && Number(row.duration_value) > Number(f.maxDuration)) return false;
    if (f.minYtm !== '' && Number(row.ytm_net_est_pct) < Number(f.minYtm)) return false;
    if (f.maxYtm !== '' && Number(row.ytm_net_est_pct) > Number(f.maxYtm)) return false;
    if (f.sector !== 'all' && row.sector !== f.sector) return false;
    if (f.couponType !== 'all' && row.coupon_type !== f.couponType) return false;
    if (f.retailOnly && row.qualified_only) return false;
    if (f.simpleOnly && (row.amortizing || row.has_put_offer || row.has_call)) return false;
    if (Number(f.minLiquidity) > 0 && window.BondRetail) {
      const score = window.BondRetail.liquidity(row).score;
      if (score == null || score < Number(f.minLiquidity)) return false;
    }
    return true;
  });
}

function bondUniverseScreenerHTML(universe) {
  if (!universe || !Array.isArray(universe.bonds)) return bondsErrorHTML();
  const all = universe.bonds;
  const safeCount = all.filter((row) => row.bond_safety && row.bond_safety.investable).length;
  const rows = sortedBonds(bondScreenerRows(universe));
  const f = BOND_SCREEN_FILTERS;
  const риск = BOND_SCREEN_MODE === 'risk';

  const modes = `<div class="bond-mode-switch" role="group" aria-label="Режим отбора">
    <button type="button" class="bond-mode${!риск ? ' on' : ''}" data-bond-screen-mode="safe" aria-pressed="${!риск}">Безопасные и доступные <b>${safeCount}</b></button>
    <button type="button" class="bond-mode${риск ? ' on risky' : ''}" data-bond-screen-mode="risk" aria-pressed="${риск}">Высокий риск · требуется проверка <b>${all.length - safeCount}</b></button>
  </div>`;
  const warning = риск ? `<div class="bond-mode-warning"><b>Это режим непроверенных выпусков.</b>
    <span>Здесь могут быть бумаги без подтверждённого рейтинга, с низкой ликвидностью, сложными условиями
    или аномальной расчётной доходностью. Перед покупкой проверьте условия выпуска и официальные документы.</span></div>` : '';

  const sectors = [...new Set(all.map((row) => row.sector).filter(Boolean))].sort();
  const ratingOptions = ['BBB-', 'BBB', 'BBB+', 'A-', 'A', 'A+', 'AA-', 'AA', 'AA+', 'AAA'];
  const filters = `<div class="bond-filters">
    <label>Мин. рейтинг<select data-bond-filter="minRating"${риск ? ' disabled' : ''}>
      <option value=""${f.minRating === '' ? ' selected' : ''}>любой</option>
      ${ratingOptions.map((r) => `<option value="${r}"${f.minRating === r ? ' selected' : ''}>${r}</option>`).join('')}
    </select>${риск ? '<small class="bond-filter-off">не применяется в этом режиме</small>' : ''}</label>
    <label>YTM net от<input type="number" step="0.5" data-bond-filter="minYtm" value="${esc(f.minYtm)}" placeholder="%"></label>
    <label>до<input type="number" step="0.5" data-bond-filter="maxYtm" value="${esc(f.maxYtm)}" placeholder="%"></label>
    <label>Дюрация до<input type="number" step="0.5" data-bond-filter="maxDuration" value="${esc(f.maxDuration)}" placeholder="лет"></label>
    <label>Ликвидность от<input type="number" step="5" min="0" max="100" data-bond-filter="minLiquidity" value="${esc(f.minLiquidity)}"></label>
    <label>Сектор<select data-bond-filter="sector">
      <option value="all"${f.sector === 'all' ? ' selected' : ''}>все</option>
      ${sectors.map((s) => `<option value="${esc(s)}"${f.sector === s ? ' selected' : ''}>${esc(s)}</option>`).join('')}
    </select></label>
    <label>Купон<select data-bond-filter="couponType">
      ${[['all', 'любой'], ['fixed', 'фиксированный'], ['floating', 'плавающий'], ['zero', 'бескупонный']]
        .map(([id, label]) => `<option value="${id}"${f.couponType === id ? ' selected' : ''}>${label}</option>`).join('')}
    </select></label>
    <label class="bond-filter-check"><input type="checkbox" data-bond-filter="retailOnly"${f.retailOnly ? ' checked' : ''}>Без статуса квалифицированного</label>
    <label class="bond-filter-check"><input type="checkbox" data-bond-filter="simpleOnly"${f.simpleOnly ? ' checked' : ''}>Без оферты и амортизации</label>
    <button type="button" class="btn" id="bond-filters-reset">Сбросить фильтры</button>
  </div>`;

  const headers = [
    bondSortHeaderHTML('name', 'Выпуск'),
    '<th>Статус</th>',
    bondSortHeaderHTML('rating', 'Рейтинг'),
    '<th>Сектор</th>',
    bondSortHeaderHTML('ytm_net', 'YTM net'),
    bondSortHeaderHTML('liquidity', 'Ликвидн.'),
    bondSortHeaderHTML('duration_years', 'Дюрация'),
    bondSortHeaderHTML('dirty_price', 'Dirty/лот'),
    bondSortHeaderHTML('maturity', 'Погашение'),
  ].join('');
  const body = rows.slice(0, 100).map((row) => {
    const liq = window.BondRetail ? window.BondRetail.liquidity(row) : {};
    return `<tr>
      <td class="b-name">${bondOpenIdentityHTML(row)}<small>${esc(row.issuer_name || '')}</small>${риск ? bondReasonListHTML(row, true) : ''}</td>
      <td>${bondSafetyBadgeHTML(row)}</td>
      <td>${row.instrument_type === 'ofz' ? 'ОФЗ' : (row.rating ? esc(row.rating) : '<span class="muted">не найден</span>')}</td>
      <td>${esc(row.sector || ND)}</td>
      <td class="tnum">${bondConfirmedYtmHTML(row)}</td>
      <td class="tnum">${liq.score == null ? ND : liq.score}</td>
      <td class="tnum">${isNum(row.duration_value) ? Number(row.duration_value).toFixed(2) : ND}</td>
      <td class="tnum">${rub0(row.dirty_price_per_lot_rub)}</td>
      <td>${esc(shortIsoDate(row.maturity_date))}</td>
    </tr>`;
  }).join('');
  const empty = `<tr><td colspan="9" class="bond-empty-row">Под фильтры не попал ни один выпуск. Ослабьте условия или нажмите «Сбросить фильтры».</td></tr>`;

  return `${modes}${warning}
    <div class="bondlab-tools"><label>Поиск<input id="bondlab-search" type="search" value="${esc(BOND_SCREEN_QUERY)}" placeholder="SECID, выпуск или эмитент"></label>
      <div class="bondlab-choices">${[['all','Все'],['AAA','AAA'],['AA','AA'],['A','A'],['BBB','BBB'],['ofz','ОФЗ']].map(([id,label]) => `<button type="button" class="bondlab-choice${id === BOND_SCREEN_RATING ? ' active' : ''}" data-bond-rating="${id}">${label}</button>`).join('')}</div>
      <span class="muted">Найдено: ${rows.length} из ${all.length}</span></div>
    ${filters}
    <div class="bonds-table-wrap"><table class="bonds-table"><thead><tr>${headers}</tr></thead><tbody>${body || empty}</tbody></table></div>
    <p class="bonds-disc">Показаны до 100 строк. Нажмите на название, чтобы открыть карточку выпуска. Доходность, не прошедшая проверку, не показывается числом — вместо неё стоит «Расчёт требует проверки».</p>`;
}

function bondRelativeValueHTML(universe) {
  if (!universe || !Array.isArray(universe.bonds)) return bondsErrorHTML();
  const rows = universe.bonds.filter((row) => row.instrument_type === 'corp' && isNum(row.excess_spread_pp)).sort((a, b) => b.excess_spread_pp - a.excess_spread_pp).slice(0, 40);
  const body = rows.map((row) => `<tr><td class="b-name">${instrumentIdentityHTML(row.secid, row.name, 'bond', 'sm', { showTypeBadge: false })}<small>${esc(row.issuer_name || '')}</small></td><td>${esc(row.rating || ND)}</td><td class="tnum">${bondPct(row.g_spread_pp, 2)}</td><td class="tnum">${bondPct(row.peer_spread_pp, 2)}</td><td class="tnum ${row.excess_spread_pp >= 0 ? 'b-up' : 'b-down'}">${bondPct(row.excess_spread_pp, 2)}</td><td class="tnum">${row.peer_n || 0}</td><td>${esc(row.peer_fallback_level || ND)}</td></tr>`).join('');
  return `<div class="bondlab-explain"><h3>Премия к сопоставимым выпускам</h3><p>Сравнение идёт внутри рейтинга и bucket дюрации. Положительный excess spread не является сигналом покупки: он может отражать кредитный, офертный или ликвидностный риск.</p></div><div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Выпуск</th><th>Рейтинг</th><th>G-spread</th><th>Peer</th><th>Excess</th><th>Peer N</th><th>Уровень fallback</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function bondCurveLabHTML(d) {
  return `<div class="bondlab-explain"><h3>G-кривая MOEX и корпоративные выпуски</h3><p>Линия — опубликованная рублёвая zero-coupon curve. Точки — YTM корпоратов; сравнение корректно только при сопоставимой дюрации и структуре денежных потоков.</p></div><div id="bonds-chart-wrap" class="bonds-chart-wrap"><canvas id="bonds-chart"></canvas></div><div class="bonds-chart-legend muted">Цвет точки обозначает рейтинговую группу. Наведи на выпуск для деталей.</div>`;
}

// Три прежние вкладки (Relative Value, G-кривая, Finder) сведены сюда под-переключателем.
// Сами представления НЕ переписаны: вызываются те же функции, что и раньше, — объединён
// только пользовательский путь, чтобы верхних вкладок стало три вместо пяти.
const BOND_ANALYTICS_VIEWS = [
  { id: 'relative', label: 'Relative Value' },
  { id: 'curve', label: 'G-кривая' },
  { id: 'finder', label: 'Finder' },
];

function bondAnalyticsHTML(d) {
  const universe = (d && d.lab && d.lab.universe) || null;
  const tabs = BOND_ANALYTICS_VIEWS.map((view) => {
    const on = view.id === BOND_ANALYTICS_VIEW;
    return `<button type="button" class="bondlab-subtab${on ? ' on' : ''}" data-bond-analytics="${view.id}"
      role="tab" aria-selected="${on}">${esc(view.label)}</button>`;
  }).join('');
  let body;
  if (BOND_ANALYTICS_VIEW === 'curve') body = bondCurveLabHTML(d);
  else if (BOND_ANALYTICS_VIEW === 'finder') {
    // Finder монтируется в свой контейнер и наполняется renderFinder() после вставки в DOM
    body = '<div class="finder-body" id="finder-body"><div class="finder-loading muted">Загрузка Bond Finder…</div></div>';
  } else body = bondRelativeValueHTML(universe);
  return `<div class="bondlab-subtabs" role="tablist" aria-label="Разделы аналитики">${tabs}</div>
    <div class="bondlab-analytics-body">${body}</div>`;
}

// ── Карточка выпуска ───────────────────────────────────────────────────────
function bondFindRow(secid) {
  const pools = [
    (BONDS && BONDS.lab && BONDS.lab.universe && BONDS.lab.universe.bonds) || [],
    (BONDS && BONDS.bonds) || [],
  ];
  for (const pool of pools) {
    const hit = pool.find((row) => row && row.secid === secid);
    if (hit) return hit;
  }
  return null;
}

/** Строка провенанса. Источник и дата обязательны: без них показываем «не подтверждён»,
 *  а не пустое место — пользователь должен видеть разницу между «нет данных» и «свежо». */
function bondSourceRow(label, date, url, note) {
  const when = date ? shortIsoDate(String(date).slice(0, 10)) : null;
  const value = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(note || 'официальный источник')}</a>`
    : esc(note || (when ? 'MOEX ISS' : 'источник не подтверждён'));
  return `<tr><td>${esc(label)}</td><td>${value}</td><td class="tnum">${when ? esc(when) : ND}</td></tr>`;
}

function bondRisksHTML(row, safety) {
  const risks = [];
  const rating = row.rating;
  risks.push(rating
    ? `<li><b>Кредитный риск.</b> Рейтинг ${esc(rating)}${row.rating_agency ? ` (${esc(row.rating_agency)})` : ''}. Чем ниже рейтинг, тем выше вероятность проблем с выплатами.</li>`
    : '<li><b>Кредитный риск.</b> Официальный рейтинг выпуска не найден — оценить надёжность эмитента по данным сайта нельзя.</li>');
  if (isNum(row.duration_value)) {
    risks.push(`<li><b>Процентный риск.</b> Дюрация ${Number(row.duration_value).toFixed(2)}: при росте ставок на 1 п.п. цена упадёт примерно на ${Number(row.duration_value).toFixed(1)}%.</li>`);
  }
  const liq = (window.BondRetail && window.BondRetail.liquidity(row)) || null;
  if (liq) {
    risks.push(liq.score == null
      ? '<li><b>Риск ликвидности.</b> Данных о торгах недостаточно — продать выпуск быстро может не получиться.</li>'
      : `<li><b>Риск ликвидности.</b> Оценка ${liq.score} из 100 (${esc(liq.label)}). Низкая ликвидность означает широкий спред и потери при срочной продаже.</li>`);
  }
  if (row.has_put_offer) risks.push('<li><b>Риск оферты.</b> У выпуска есть оферта: эмитент может изменить купон, и доходность до погашения перестанет быть актуальной.</li>');
  if (row.has_call) risks.push('<li><b>Риск досрочного выкупа.</b> Эмитент вправе погасить выпуск раньше срока — обычно когда это выгодно ему, а не держателю.</li>');
  if (row.amortizing) risks.push('<li><b>Амортизация.</b> Номинал возвращается частями: полученные деньги придётся вкладывать заново по неизвестной сегодня ставке.</li>');
  if (row.coupon_type === 'floating') risks.push('<li><b>Плавающий купон.</b> Размер будущих выплат зависит от базовой ставки и заранее неизвестен.</li>');
  if (row.coupon_type === 'zero') risks.push('<li><b>Бескупонный выпуск.</b> Промежуточных выплат нет, весь доход — в разнице цены и номинала.</li>');
  if (row.qualified_only) risks.push('<li><b>Только для квалифицированных инвесторов.</b> Купить выпуск без этого статуса нельзя.</li>');
  if (!row.ultimate_parent_id) risks.push('<li><b>Концентрация по группе.</b> Материнская структура эмитента не подтверждена — учесть связанные выпуски в лимите группы нельзя.</li>');
  if (safety && !safety.ytmConfirmed) risks.push('<li><b>Доходность не подтверждена.</b> Расчёт YTM не прошёл проверку — не опирайтесь на это число без сверки с условиями выпуска.</li>');
  return `<ul class="bond-risks">${risks.join('')}</ul>`;
}

function bondDetailHTML(row) {
  const safety = row.bond_safety || (window.BondRetail && window.BondRetail.classifyBond(row)) || null;
  const liq = (window.BondRetail && window.BondRetail.liquidity(row)) || {};
  const src = row.source_dates || {};
  const face = Number(row.face_value_per_bond_rub || 0);
  // Текущая доходность = годовой купон в рублях ÷ грязная цена. Для бескупонных не определена.
  const annualCoupon = isNum(row.coupon_pct) ? face * Number(row.coupon_pct) / 100 : null;
  const dirty = Number(row.dirty_price_per_bond_rub || 0);
  const currentYield = (annualCoupon && dirty > 0 && row.coupon_type !== 'zero') ? annualCoupon / dirty * 100 : null;
  const buy = (window.BondRetail && window.BondRetail.purchaseBreakdown(row, 1)) || null;

  const kpi = (label, value, note) =>
    `<div><span>${esc(label)}</span><b>${value}</b>${note ? `<small>${esc(note)}</small>` : ''}</div>`;

  const flows = (row.cashflows_12m || []).map((flow) => {
    const type = flow.flow_type === 'principal' ? 'Возврат номинала' : 'Купон';
    const gross = Number(flow.amount_per_bond_rub || 0);
    const tax = flow.flow_type === 'principal' ? 0 : gross * 0.13;
    return `<tr><td>${esc(shortIsoDate(flow.date))}</td><td>${type}</td><td class="tnum">${rub2(gross)}</td><td class="tnum">${rub2(tax)}</td><td class="tnum b-strong">${rub2(gross - tax)}</td></tr>`;
  }).join('');
  const maturityNote = row.amortizing
    ? '<p class="muted">У выпуска амортизация: график возврата номинала в данных MOEX за 12 месяцев не детализирован, поэтому показаны только купоны.</p>'
    : `<p class="muted">Погашение номинала ${rub2(face)} на бумагу — ${esc(shortIsoDate(row.maturity_date))}. Возврат номинала не является доходом.</p>`;

  const features = [
    row.amortizing ? 'амортизация' : null,
    row.has_put_offer ? 'оферта' : null,
    row.has_call ? 'call-опцион' : null,
    row.qualified_only ? 'только для квалифицированных' : null,
    row.new_placement ? 'новое размещение' : null,
  ].filter(Boolean);

  const couponTypeLabel = { fixed: 'фиксированный', floating: 'плавающий', zero: 'бескупонный' }[row.coupon_type] || row.coupon_type || ND;

  return `<div class="bond-detail">
    <header class="bond-detail-head">
      <div>
        <span class="bond-detail-eyebrow">${esc(row.issuer_name || 'Эмитент не указан')}</span>
        <h2 id="bond-detail-title">${esc(row.name || row.secid)}</h2>
        <p class="bond-detail-ids">${esc(row.secid)}${row.isin && row.isin !== row.secid ? ` · ISIN ${esc(row.isin)}` : ''} · ${esc(row.sector || 'сектор не определён')} · ${esc(row.board || '')}</p>
      </div>
      <div class="bond-detail-head-right">
        ${bondSafetyBadgeHTML(row)}
        <button type="button" class="bond-detail-close" id="bond-detail-close" aria-label="Закрыть карточку">✕</button>
      </div>
    </header>
    ${safety && !safety.investable ? `<div class="bond-detail-warn"><b>Не проходит безопасный фильтр.</b>${bondReasonListHTML(row)}</div>` : ''}
    <div class="bond-detail-kpis">
      ${kpi('Цена', bondPct(row.clean_price_pct, 2), src.price ? `на ${shortIsoDate(src.price)}` : 'дата цены не указана')}
      ${kpi('YTM gross', bondPct(row.ytm_gross_pct, 2))}
      ${kpi('YTM net', safety && !safety.ytmConfirmed ? '<span class="bond-unconfirmed-yield">не подтверждена</span>' : bondPct(row.ytm_net_est_pct, 2), 'оценка налога, не персональный расчёт')}
      ${kpi('Текущая доходность', currentYield == null ? ND : bondPct(currentYield, 2))}
      ${kpi('Дюрация', isNum(row.duration_value) ? Number(row.duration_value).toFixed(2) : ND, row.duration_type === 'modified_duration_effective_annual' ? 'модифицированная' : '')}
      ${kpi('До погашения', isNum(row.years_to_maturity) ? Number(row.years_to_maturity).toFixed(2) + ' г.' : ND, shortIsoDate(row.maturity_date))}
      ${kpi('Рейтинг', row.rating ? esc(row.rating) : '<span class="muted">не найден</span>', row.rating_agency || '')}
      ${kpi('Ликвидность', liq.score == null ? ND : `${liq.score}/100`, liq.label || 'данных недостаточно')}
    </div>

    <section><h3>Купон и условия выпуска</h3>
      <dl class="bond-detail-dl">
        <div><dt>Тип купона</dt><dd>${esc(couponTypeLabel)}</dd></div>
        <div><dt>Ставка</dt><dd>${bondPct(row.coupon_pct, 2)}</dd></div>
        <div><dt>Выплат в год</dt><dd>${row.coupon_frequency ?? ND}</dd></div>
        <div><dt>Номинал</dt><dd>${rub2(face)}</dd></div>
        <div><dt>НКД на бумагу</dt><dd>${rub2(row.aci_per_bond_rub)}</dd></div>
        <div><dt>Лот</dt><dd>${row.lot_size ?? ND} бум.</dd></div>
        <div><dt>Объём выпуска</dt><dd>${isNum(row.issue_size_rub) ? rub0(row.issue_size_rub) : ND}</dd></div>
        <div><dt>Особенности</dt><dd>${features.length ? esc(features.join(', ')) : 'нет'}</dd></div>
      </dl>
      ${features.length ? '<p class="bond-detail-note">Сложные условия меняют фактическую доходность: расчёт YTM их полностью не учитывает.</p>' : ''}
    </section>

    ${buy ? `<section><h3>Сколько стоит купить один лот</h3>
      <table class="bonds-table bond-buy-table"><tbody>
        <tr><td>Чистая стоимость</td><td class="tnum">${rub2(buy.clean_cost_rub)}</td></tr>
        <tr><td>+ НКД</td><td class="tnum">${rub2(buy.accrued_interest_rub)}</td></tr>
        <tr><td>+ комиссия и проскальзывание</td><td class="tnum">${rub2(buy.commission_and_slippage_rub)}</td></tr>
        <tr class="bond-buy-total"><td><b>Итого за лот (${buy.bonds} бум.)</b></td><td class="tnum"><b>${rub2(buy.total_rub)}</b></td></tr>
      </tbody></table>
      <p class="muted">Комиссия и проскальзывание — оценка по умолчанию, а не тариф вашего брокера.</p></section>` : ''}

    <section><h3>Денежные потоки · 12 месяцев</h3>
      ${flows ? `<div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Дата</th><th>Тип</th><th>На бумагу</th><th>Налог, оценка</th><th>После налога</th></tr></thead><tbody>${flows}</tbody></table></div>`
        : '<p class="muted">Выплат в ближайшие 12 месяцев в данных MOEX не найдено.</p>'}
      ${maturityNote}
    </section>

    <section><h3>Рейтинг</h3>
      ${row.rating ? `<dl class="bond-detail-dl">
        <div><dt>Рейтинг</dt><dd>${esc(row.rating)}</dd></div>
        <div><dt>Агентство</dt><dd>${esc(row.rating_agency || ND)}</dd></div>
        <div><dt>Объект</dt><dd>${row.rating_scope === 'issue' ? 'выпуск' : row.rating_scope === 'issuer' ? 'эмитент' : esc(row.rating_scope || ND)}</dd></div>
        <div><dt>Дата присвоения</dt><dd>${row.rating_date ? esc(shortIsoDate(row.rating_date)) : ND}</dd></div>
      </dl>${row.rating_source_url ? `<p><a href="${esc(row.rating_source_url)}" target="_blank" rel="noopener noreferrer">Карточка рейтинга у агентства</a></p>` : ''}
      <p class="muted">Показан рейтинг агентства, а не внутренняя оценка сайта. Шкалы разных агентств не смешиваются.</p>`
        : '<p><b>Рейтинг не найден или не подтверждён.</b> Выпуск не проходит безопасный фильтр по этой причине.</p>'}
    </section>

    <section><h3>Основные риски</h3>${bondRisksHTML(row, safety)}</section>

    <section><h3>Источники и актуальность</h3>
      <div class="bonds-table-wrap"><table class="bonds-table bond-sources-table"><thead><tr><th>Поле</th><th>Источник</th><th>Обновлено</th></tr></thead><tbody>
        ${bondSourceRow('Цена и НКД', src.price, null, 'MOEX ISS')}
        ${bondSourceRow('Условия выпуска и купоны', src.price, null, 'MOEX ISS bondization')}
        ${bondSourceRow('История торгов', src.history, null, 'MOEX ISS')}
        ${bondSourceRow('Рейтинг', row.rating_date || src.rating, row.rating_source_url, row.rating_agency ? `${row.rating_agency}, карточка выпуска` : null)}
        ${bondSourceRow('Группа компаний', null, null, row.ultimate_parent_id ? 'подтверждена' : 'источник не подтверждён')}
      </tbody></table></div>
      <p class="muted">Не индивидуальная инвестиционная рекомендация. Перед покупкой проверьте условия выпуска в официальных документах эмитента.</p>
    </section>
  </div>`;
}

function openBondDetail(secid) {
  const dlg = document.getElementById('bond-detail-dialog');
  const body = document.getElementById('bond-detail-body');
  if (!dlg || !body) return;
  const row = bondFindRow(secid);
  body.innerHTML = row
    ? bondDetailHTML(row)
    : `<div class="bond-detail"><header class="bond-detail-head"><div><h2 id="bond-detail-title">Выпуск не найден</h2><p class="bond-detail-ids">${esc(secid)}</p></div><div class="bond-detail-head-right"><button type="button" class="bond-detail-close" id="bond-detail-close" aria-label="Закрыть">✕</button></div></header><p class="muted">Бумага отсутствует в текущем универсуме — возможно, данные обновились.</p></div>`;
  if (typeof dlg.showModal === 'function') { if (!dlg.open) dlg.showModal(); } else dlg.setAttribute('open', '');
}

function closeBondDetail() {
  const dlg = document.getElementById('bond-detail-dialog');
  if (!dlg) return;
  if (typeof dlg.close === 'function' && dlg.open) dlg.close(); else dlg.removeAttribute('open');
}

const BOND_FILTERS_DEFAULT = Object.freeze({
  minRating: 'A-', minYtm: '', maxYtm: '', maxDuration: '',
  minLiquidity: 45, sector: 'all', couponType: 'all', retailOnly: true, simpleOnly: true,
});

function wireBondLabControls() {
  document.querySelectorAll('[data-bond-analytics]').forEach((button) => button.addEventListener('click', () => {
    BOND_ANALYTICS_VIEW = button.dataset.bondAnalytics;
    drawBondLab();
  }));
  document.querySelectorAll('[data-bond-screen-mode]').forEach((button) => button.addEventListener('click', () => {
    BOND_SCREEN_MODE = button.dataset.bondScreenMode;
    drawBondLab();
  }));
  document.querySelectorAll('[data-bond-filter]').forEach((control) => {
    const key = control.dataset.bondFilter;
    const apply = () => {
      BOND_SCREEN_FILTERS[key] = control.type === 'checkbox' ? control.checked : control.value;
      drawBondLab();
    };
    // Ввод числа дебаунсим, чтобы таблица не перерисовывалась на каждой цифре
    control.addEventListener(control.tagName === 'SELECT' || control.type === 'checkbox' ? 'change' : 'input',
      control.tagName === 'SELECT' || control.type === 'checkbox' ? apply : debounce(apply, 300));
  });
  // ── Мой портфель: импорт, файл, режим ребаланса, копия, удаление ──
  const importButton = document.getElementById('bond-portfolio-import');
  if (importButton) importButton.addEventListener('click', () => {
    const area = document.getElementById('bond-portfolio-paste');
    if (area) bondApplyImport(area.value);
  });
  const fileInput = document.getElementById('bond-portfolio-file');
  if (fileInput) fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => bondApplyImport(String(reader.result || ''));
    reader.onerror = () => {
      BOND_USER_IMPORT_ERRORS = [{ line: 0, raw: file.name, code: 'FILE_READ_FAILED' }];
      drawBondLab();
    };
    reader.readAsText(file, 'utf-8');
  });
  const rebalanceMode = document.getElementById('bond-rebalance-mode');
  if (rebalanceMode) rebalanceMode.addEventListener('change', () => {
    BOND_REBALANCE_MODE = rebalanceMode.value;
    drawBondLab();
  });
  const backup = document.getElementById('bond-portfolio-backup');
  if (backup) backup.addEventListener('click', () => {
    // Резервная копия включает и нераспознанные строки: пользователь не должен терять
    // позиции только потому, что тикер не нашёлся в текущем универсуме.
    const payload = JSON.stringify({ schema_version: 1, saved_at: new Date().toISOString(), positions: BOND_USER_PORTFOLIO }, null, 2);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([payload], { type: 'application/json' }));
    link.download = `bond-portfolio-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
  const clear = document.getElementById('bond-portfolio-clear');
  if (clear) clear.addEventListener('click', () => {
    if (!window.confirm('Удалить сохранённый портфель из этого браузера? Действие необратимо.')) return;
    BOND_USER_PORTFOLIO = [];
    BOND_USER_IMPORT_ERRORS = [];
    if (window.BondRetail) window.BondRetail.clearPortfolio(window.localStorage);
    drawBondLab();
  });

  const resetFilters = document.getElementById('bond-filters-reset');
  if (resetFilters) resetFilters.addEventListener('click', () => {
    BOND_SCREEN_FILTERS = { ...BOND_FILTERS_DEFAULT };
    BOND_SCREEN_MODE = 'safe';           // сброс всегда возвращает в безопасный режим
    BOND_SCREEN_RATING = 'all';
    BOND_SCREEN_QUERY = '';
    drawBondLab();
  });
  document.querySelectorAll('[data-bond-profile]').forEach((button) => button.addEventListener('click', () => { BOND_LAB_PROFILE = button.dataset.bondProfile; drawBondLab(); }));
  document.querySelectorAll('[data-bond-horizon]').forEach((button) => button.addEventListener('click', () => { BOND_LAB_HORIZON = button.dataset.bondHorizon; drawBondLab(); }));
  document.querySelectorAll('[data-bond-rating]').forEach((button) => button.addEventListener('click', () => { BOND_SCREEN_RATING = button.dataset.bondRating; drawBondLab(); }));
  const search = document.getElementById('bondlab-search');
  if (search) search.addEventListener('input', debounce(() => { BOND_SCREEN_QUERY = search.value; drawBondLab(); }, 180));
  document.querySelectorAll('[data-bonds-sort]').forEach((header) => header.addEventListener('click', () => {
    const key = header.dataset.bondsSort;
    BONDS_SORT = BONDS_SORT.key !== key ? { key, dir: 1 }
      : BONDS_SORT.dir === 1 ? { key, dir: -1 }
        : { key: null, dir: 0 };
    drawBondLab();
  }));
  const recalculate = document.getElementById('bondlab-recalculate');
  const budget = document.getElementById('bondlab-budget');
  if (recalculate && budget) recalculate.addEventListener('click', () => {
    const value = Number(budget.value);
    if (!budget.checkValidity() || !Number.isFinite(value)) { budget.reportValidity(); return; }
    BOND_LAB_BUDGET = Math.round(value * 100) / 100;
    drawBondLab();
  });
  const resetBudget = document.getElementById('bondlab-reset-budget');
  if (resetBudget) resetBudget.addEventListener('click', () => { BOND_LAB_BUDGET = 1000000; drawBondLab(); });
  const copy = document.getElementById('bondlab-copy');
  if (copy) copy.addEventListener('click', () => copyBondPreset(copy));
  document.querySelectorAll('[data-bond-download]').forEach((button) => button.addEventListener('click', () => downloadBondPreset(button.dataset.bondDownload)));
}

function downloadBondPreset(format) {
  const allocation = BOND_LAB_CURRENT_ALLOCATION;
  if (!allocation) return;
  let body, type, ext;
  if (format === 'json') {
    body = JSON.stringify(allocation, null, 2); type = 'application/json'; ext = 'json';
  } else {
    const columns = ['secid','name','issuer_name','rating','sector','lots','dirty_price_per_lot_rub','total_amount_rub','actual_weight','ytm_net_est_pct','duration_value','maturity_date'];
    body = [columns.join(';')].concat(allocation.positions.map((row) => columns.map((key) => `"${String(row[key] ?? '').replaceAll('"','""')}"`).join(';'))).join('\n');
    type = 'text/csv;charset=utf-8'; ext = 'csv';
  }
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([body], { type }));
  link.download = `bond_portfolio_${BOND_LAB_PROFILE}_${BOND_LAB_HORIZON}.${ext}`;
  link.click(); URL.revokeObjectURL(link.href);
}

function copyBondPreset(button) {
  const allocation = BOND_LAB_CURRENT_ALLOCATION;
  if (!allocation || !navigator.clipboard) return;
  const text = allocation.positions.map((row) => `${row.secid} · ${row.lots} лот. · ${rub0(row.total_amount_rub)}`).join('\n');
  navigator.clipboard.writeText(text).then(() => {
    const previous = button.textContent; button.textContent = 'Скопировано';
    setTimeout(() => { button.textContent = previous; }, 1200);
  }).catch(() => {});
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

    <div class="bonds-section-title">Скринер (${d.bonds.length} бумаг, сортировка по заголовкам)</div>
    ${bondsTableHTML(d.bonds)}

    <details class="bonds-limits">
      <summary>Ограничения данных (§5.6) — что не размечено в скринере</summary>
      <div class="bonds-limits-body">
        <p>Справедливая цена строится от G-кривой ОФЗ (КБД MOEX) и модельного спреда рейтинга. Ряд критичных для розницы полей в данных <b>отсутствует</b> — по ним ничего не выводится и не додумывается:</p>
        <ul>
          <li><b>Оферта (put/call).</b> Дата/цена оферты не размечены → YTM и дюрация считаются к погашению; для бумаг с офертой это может быть неверно. <b>Оферта не проверена.</b></li>
          <li><b>Тип купона (фикс/флоатер).</b> Купон принят фиксированным; флоатеры (плавающая база + спред) не классифицированы → их YTM к погашению условна.</li>
          <li><b>Амортизация.</b> Амортизация номинала не размечена → дюрация/YTM амортизируемых выпусков — с оговоркой.</li>
          <li><b>НКД / чистая vs грязная цена.</b> Показана <b>чистая</b> цена MOEX (% номинала); НКД не выделен. Цена лота в калькуляторе — тоже чистая.</li>
          <li><b>Ликвидность.</b> Есть дневной оборот выпуска; bid/ask-спред не размечен.</li>
          <li><b>G-спред.</b> Отдельного G-спреда (к базе КБД на дату) в данных нет; «Апсайд» — модельное отклонение справедливой цены, а не G-спред.</li>
        </ul>
        <p class="muted">Ввод этих полей — задача пайплайна данных облигаций, не фронта. «YTM−налог» уже посчитан сервером после НДФЛ 13%. Не ИИР.</p>
      </div>
    </details>

    <div class="bonds-disc">Индикатор не является индивидуальной инвестиционной рекомендацией. Справедливая цена опирается на плоский спред рейтинга (модельное допущение) — крупный «апсайд» у имён A-/BBB отражает некомпенсированную в модели кредит-премию, а не гарантированную недооценку. Данные MOEX ISS.</div>
  `;
}

function bondsTableHTML(bonds) {
  const rows = sortedBonds(bonds).map((x) => {
    const dev = isNum(x.deviation) ? (x.deviation >= 0 ? '+' : '') + x.deviation.toFixed(1) + '%' : ND;
    return `<tr>
      <td class="b-name">${instrumentIdentityHTML(x.secid || x.isin, x.name, 'bond', 'sm', { showTypeBadge: false })}</td>
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
  return `<div class="bonds-table-wrap" data-bonds-screen-table><table class="bonds-table">
    <thead><tr>
      ${bondSortHeaderHTML('name', 'Бумага')}${bondSortHeaderHTML('rating', 'Рейтинг')}
      ${bondSortHeaderHTML('price_market', 'Цена<span class="b-mark">чистая</span>', 'Чистая цена MOEX (% номинала), без НКД. НКД/грязная цена в данных не размечены — см. «Ограничения данных».')}
      ${bondSortHeaderHTML('ytm_market', 'YTM', 'Доходность к погашению по рыночной цене (WAPRICE), считается из реальных потоков. К погашению: оферты и амортизация в данных не размечены — см. «Ограничения данных».')}
      ${bondSortHeaderHTML('ytm_fair', 'Fair YTM', 'Справедливая YTM по G-кривой MOEX + плоский спред рейтинга')}
      ${bondSortHeaderHTML('deviation', 'Апсайд', 'Апсайд справедливой цены к рыночной. Плоский спред занижает кредит-премию A-/BBB → большой «+» = модельное допущение')}
      ${bondSortHeaderHTML('ytm_net', 'YTM−налог', 'Чистая YTM после НДФЛ 13% (купоны и ценовой доход)')}
      ${bondSortHeaderHTML('duration_years', 'Дюрация')}
      ${bondSortHeaderHTML('coupon_pct', 'Купон', 'Купон принят фиксированным. Флоатеры (плавающая база + спред) в данных не классифицированы — для них YTM к погашению условна. См. «Ограничения данных».')}
      ${bondSortHeaderHTML('maturity', 'Погашение')}
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
  const body = document.getElementById('bonds-body');
  if (body && !body.dataset.sortWired) {
    body.dataset.sortWired = '1';
    body.addEventListener('click', (event) => {
      const header = event.target.closest('[data-bonds-sort]');
      if (!header || !body.contains(header) || !BONDS) return;
      const key = header.dataset.bondsSort;
      BONDS_SORT = BONDS_SORT.key !== key ? { key, dir: 1 }
        : BONDS_SORT.dir === 1 ? { key, dir: -1 }
          : { key: null, dir: 0 };
      const table = body.querySelector('[data-bonds-screen-table]');
      if (table) table.outerHTML = bondsTableHTML(BONDS.bonds);
    });
  }
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
    <td class="b-name">${instrumentIdentityHTML(b.secid || b.isin, b.name, 'bond', 'sm', { showTypeBadge: false })}</td>
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
const ML_SIG = { 'Выше модельного порога': 'good', 'Наблюдать': 'neut', 'Ниже модельного порога': 'risk', 'Недостаточно данных': 'neut' };

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
  fetch(dataURL('marlamov.json'))
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
  const placeholders = d.rows.filter((r) => r.forecast_status === 'insufficient_forecast').length;
  const TT = 'Доходность 2-го года считается от ОЧИЩЕННОЙ базы: P_adj = Цена − Div1·0.87 (после получения первого чистого дивиденда ваша база затрат снижается).';
  return `
    <div class="ml-macro">
      <span class="ml-chip ${regimeCls}">Режим рынка: <b>${esc(m.regime || '—')}</b></span>
      <span class="ml-chip neut">IMOEX <b>${m.imoex != null ? ru(m.imoex, 0) : '—'}</b> / SMA200 <b>${m.sma200 != null ? ru(m.sma200, 0) : '—'}</b></span>
      <span class="ml-chip neut">RFR (КБД 1Y) <b>${(m.rfr * 100).toFixed(1)}%</b></span>
      <span class="ml-chip neut">Налог <b>${Math.round((1 - m.net_tax) * 100)}%</b></span>
    </div>
    <p class="ml-sub">Кандидаты ранжируются по ожидаемой чистой дивдоходности: вероятность выплаты × условный дивиденд после НДФЛ / цена. Для модельного состава спред к сопоставимой RFR net должен быть не ниже <b>${ru((m.entry_threshold || 0.03) * 100, 1)} п.п.</b></p>
    ${placeholders ? `<div class="ml-banner">Для ${placeholders} бумаг нет независимого прогноза Div2. Двухлетний сценарий по ним отключён и не создаёт статус или модельный вес.</div>` : ''}
    ${mlTableHTML(d.rows)}
    <div class="ml-fresh muted">Обновлено: ${esc(upd)} · бумаг: ${m.n} (с прогнозом Div2: ${m.n_with_div2}) · источник: MOEX ISS + модель</div>
    <div class="ml-disc">Не индивидуальная инвестиционная рекомендация. Таблица — watchlist. Div2 используется только как сценарий при наличии независимого прогноза; live eligibility основана на ожидаемой чистой доходности первого года.</div>
  `;
}

function mlTableHTML(rows) {
  const pct = (x, d = 1) => isNum(x) ? (x * 100).toFixed(d) + '%' : ND;
  const pp = (x) => isNum(x) ? (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + 'пп' : ND;
  const body = rows.map((r) => `<tr>
    <td class="ml-name">${instrumentIdentityHTML(r.ticker, r.name, r.instrument_type, 'sm')}</td>
    <td class="tnum">${isNum(r.price) ? ru(r.price, 2) : ND}</td>
    <td class="tnum">${isNum(r.div1) ? ru(r.div1, 2) : ND}</td>
    <td class="tnum">${isNum(r.div2) ? ru(r.div2, 2) : ND}</td>
    <td class="tnum">${pct(r.payout_probability)}</td>
    <td class="tnum">${pct(r.expected_net_yield)}</td>
    <td class="tnum ${isNum(r.expected_net_spread) ? (r.expected_net_spread >= 0 ? 'ml-up' : 'ml-down') : ''}">${pp(r.expected_net_spread)}</td>
    <td class="tnum ml-y2">${pct(r.yield2)}</td>
    <td><span class="ml-sig s-${ML_SIG[r.signal] || 'neut'}">${esc(r.signal)}</span></td>
    <td class="ml-note muted">${esc(r.note || '')}</td>
  </tr>`).join('');
  return `<div class="ml-table-wrap"><table class="ml-table">
    <thead><tr>
      <th>Бумага</th><th>Цена</th><th>Div 1</th><th>Div 2</th><th>P(выплаты)</th>
      <th data-tooltip="P(выплаты) × Div1 × (1−НДФЛ) / Цена">Ожид. net yield</th>
      <th data-tooltip="Expected net yield − RFR после налога">Спред net</th>
      <th class="ml-y2" data-tooltip="Только сценарий при независимом Div2">Сценарий 2 год</th>
      <th>Статус</th><th>Примечание</th>
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
  fetch(dataURL('site_financials.json'))
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
  fetch(dataURL('methodology.json'))
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
  fetch(dataURL('site_coverage.json'))
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

// Заголовок и подзаголовок раздела для topbar (редизайн, Итерация 2).
const SECTION_META = {
  market: ['Обзор', 'Состояние рынка РФ, события и портфель'],
  'my-portfolio': ['Портфель', 'Portfolio X-Ray — риск и доходность, расчёт локально в браузере'],
  stocks: ['Акции', 'Скринер: прогноз дивидендов, оценка, риск невыплаты'],
  strategies: ['Стратегии', 'Факторные и сценарные портфели поверх данных по акциям'],
  news: ['Новости', 'Брифинг рынка РФ'],
  bonds: ['Облигации', 'Скринер рублёвых корпоративных облигаций MOEX'],
  cbr: ['Банки РФ', 'Отчётность банков по формам ЦБ РФ'],
  methodology: ['Методология', 'Источники, расчёты и ограничения'],
  pro: ['О проекте', 'Честно о проекте и тарифах-гипотезе'],
};

function getSectionFromHash() {
  const h = (location.hash || '').replace('#', '');
  let section = h.split('?', 1)[0];
  if (section === 'overview') section = 'market';   // двусторонний алиас overview↔market (§9)
  return SECTIONS.includes(section) ? section : 'market';
}

function openDetails(id) {
  const d = document.getElementById(id);
  if (d && d.tagName === 'DETAILS') { d.hidden = false; if (!d.open) d.open = true; }
}

function onSectionShown(sec) {
  if (sec === 'market') { if (dividendDeepLink().open) openDetails('dividend-calendar'); ensureKpiData(); renderMarketPulse(); renderMarketPE(); renderMarketKPI(); renderMarketSignals(); loadMacroCbr(() => renderMacroCbr()); renderEventsToday(); renderDividendCalendar(); }
  else if (sec === 'my-portfolio') {
    ensureKpiData();
    if (!SITE_FINANCIALS && typeof loadSiteFinancials === 'function') loadSiteFinancials(() => renderMyPortfolio());
    renderMyPortfolio();
  }
  else if (sec === 'strategies') {
    if (ACTIVE_STRATEGY_MODE === 'ml') renderMlStrategy();
    else { openDetails('pf'); openDetails('marlamov'); }
  }
  else if (sec === 'news') { renderNews(true); if (MARKET_HISTORY) renderMarketInstruments(); else loadMarketHistory(() => renderMarketInstruments()); }
  else if (sec === 'bonds') { renderBondLab(); }
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
  // topbar: заголовок/подзаголовок раздела (редизайн, Итерация 2)
  const meta = SECTION_META[sec] || [sec, ''];
  const tt = document.getElementById('topbar-title'); if (tt) tt.textContent = meta[0];
  const ts = document.getElementById('topbar-sub'); if (ts) ts.textContent = meta[1];
  // закрыть мобильный «Ещё»-sheet при переходе в раздел
  const sheet = document.getElementById('app-more-sheet');
  if (sheet) sheet.hidden = true;
  const moreBtn = document.getElementById('app-more-btn');
  if (moreBtn) moreBtn.setAttribute('aria-expanded', 'false');
  window.scrollTo({ top: 0, behavior: 'auto' });
  onSectionShown(sec);
}

function initRouter() {
  // Escape: нативный <dialog> закрывается сам и сам возвращает фокус на триггер,
  // но aria-expanded надо снять руками. Слушаем 'cancel' (Escape), а не 'close':
  // событие close в связке showModal()+close() здесь не доставляется, проверено.
  const inflDlg = document.getElementById('infl-dialog');
  if (inflDlg) inflDlg.addEventListener('cancel', () => {
    const card = document.getElementById('infl-card');
    if (card) card.setAttribute('aria-expanded', 'false');
  });

  document.querySelectorAll('.section-tab').forEach((t) => {
    t.addEventListener('click', () => { location.hash = t.dataset.section; });
  });
  // P5-лендинг: карточки/кнопки с data-goto ведут на соответствующий раздел;
  // кнопки «ранний доступ» показывают честную заглушку (контакт-канал добавим позже)
  // Делегированные слушатели вместо inline-onclick (CSP §6.4 запрещает inline-обработчики).
  // Элементы рендерятся динамически, поэтому слушаем на document по data-атрибуту.
  document.addEventListener('click', (e) => {
    const saw = e.target.closest('[data-saw-index]');
    if (saw && typeof setMarketSawIndex === 'function') { setMarketSawIndex(saw.dataset.sawIndex); return; }
    // Карточка выпуска облигации. Делегированно: таблицы скринера и портфеля
    // перерисовываются целиком, прямые слушатели на кнопках терялись бы.
    if (e.target.closest('#bond-detail-close')) { closeBondDetail(); return; }
    const bondOpen = e.target.closest('[data-bond-open]');
    if (bondOpen) { openBondDetail(bondOpen.dataset.bondOpen); return; }
    // «Динамика инфляции»: карточка, закрытие, вкладки, период. Делегированно —
    // inline-onclick запрещён CSP (tools/xss-guard.js падает при появлении on*=).
    if (e.target.closest('#infl-card')) { openInflDialog(); return; }
    if (e.target.closest('#infl-close')) { closeInflDialog(); return; }
    const itab = e.target.closest('[data-infl-tab]');
    if (itab) { INFL_TAB = itab.dataset.inflTab; renderInflDialog(); return; }
    const imetric = e.target.closest('[data-infl-metric]');
    if (imetric) {
      INFL_METRIC = imetric.dataset.inflMetric;
      const b = document.getElementById('infl-tabbody');
      if (b && MACRO_CBR) b.innerHTML = inflMonthlyHTML(MACRO_CBR);
      return;
    }
    const irange = e.target.closest('[data-infl-range]');
    if (irange) {
      INFL_RANGE = irange.dataset.inflRange;
      // innerHTML пересоздаёт кнопки уже с правильным состоянием по INFL_RANGE.
      // Ручной toggle тут был багом: он бежал по НОВЫМ кнопкам, сравнивая их со
      // СТАРЫМ (уже отсоединённым) элементом — совпадений не было, и подсветка
      // снималась со всех.
      const b = document.getElementById('infl-tabbody');
      if (b && MACRO_CBR) b.innerHTML = inflMonthlyHTML(MACRO_CBR);
      return;
    }
    const mmetric = e.target.closest('[data-mpe-metric]');
    if (mmetric) {
      MPE_METRIC = mmetric.dataset.mpeMetric;
      renderMarketPE();        // перерисовываем весь ряд, а не только последнюю точку
      return;
    }
    const mrange = e.target.closest('[data-mpe-range]');
    if (mrange) {
      MPE_RANGE = mrange.dataset.mpeRange;
      renderMarketPE();          // перерисовка целиком: кнопки пересоздаются уже с нужным состоянием
      return;
    }
    const dc = e.target.closest('[data-divcal-tab]');
    if (dc && typeof openDividendCalendarTab === 'function') { openDividendCalendarTab(dc.dataset.divcalTab); return; }
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
  // App-shell (Итерация 2): свёртка сайдбара в icon-rail (desktop) + мобильный «Ещё»-sheet
  const navToggle = document.getElementById('app-nav-toggle');
  const shell = document.querySelector('.app-shell');
  if (navToggle && shell) {
    if (uiStateLoad().sidebar === 'collapsed') { shell.classList.add('sidebar-collapsed'); navToggle.setAttribute('aria-expanded', 'false'); }
    navToggle.addEventListener('click', () => {
      const collapsed = shell.classList.toggle('sidebar-collapsed');
      navToggle.setAttribute('aria-expanded', String(!collapsed));
      uiStateSave({ sidebar: collapsed ? 'collapsed' : 'expanded' });
    });
  }
  const moreBtn = document.getElementById('app-more-btn');
  const moreSheet = document.getElementById('app-more-sheet');
  if (moreBtn && moreSheet) {
    moreBtn.addEventListener('click', () => {
      const open = moreSheet.hidden;
      moreSheet.hidden = !open;
      moreBtn.setAttribute('aria-expanded', String(open));
    });
    moreSheet.addEventListener('click', (e) => {
      if (e.target === moreSheet) { moreSheet.hidden = true; moreBtn.setAttribute('aria-expanded', 'false'); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !moreSheet.hidden) { moreSheet.hidden = true; moreBtn.setAttribute('aria-expanded', 'false'); } });
  }
  // §5.1 — переключатель налогового профиля
  const taxOpts = document.getElementById('tax-profile-opts');
  if (taxOpts) {
    taxOpts.addEventListener('click', (e) => {
      const b = e.target.closest('[data-rate]');
      if (b) onTaxChange(Number(b.dataset.rate));
    });
    renderTaxControl();
  }
  window.addEventListener('hashchange', () => setActiveSection(getSectionFromHash()));
  setActiveSection(getSectionFromHash());
}

// ── global data status bar (даты ТОЛЬКО из реальных JSON, не Date.now()) ──
function loadSiteStatus(cb) {
  if (SITE_STATUS) { if (cb) cb(); return; }
  fetch(dataURL('site_status.json'))
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
    // подсказка: дата ДАННЫХ (user_message) + отдельно время пересборки сервиса (generated_at) —
    // два разных понятия, не смешиваем в одной дате
    const gen = b && b.generated_at ? d10(b.generated_at) : null;
    const tip = b ? `${b.title}: ${b.user_message || b.title}${gen ? ' · сервис обновлён ' + gen : ''}` : '';
    return `<span class="ds-item ${c}"${tip ? ` title="${esc(tip)}"` : ''}><span class="ds-lbl">${lbl}:</span> <b>${v || '—'}</b></span>`;
  };
  const price = DATA && DATA.meta ? d10(DATA.meta.price_asof) : null;
  const saw = SAW_DATA ? d10(SAW_DATA.data_last) : null;
  const bonds = (BONDS && BONDS.meta) ? d10(BONDS.meta.data_date || BONDS.meta.updated) : null;
  const fin = (DATA_COVERAGE && DATA_COVERAGE.meta) ? d10(DATA_COVERAGE.meta.generated_at) : null;
  const news = (SITE_STATUS && st.news && st.news.asof) ? d10(st.news.asof) : null;
  // overall health chip — человеческий текст (учитывает торговый календарь: выходной ≠ «устаревает»)
  const overall = SITE_STATUS && !SITE_STATUS.failed ? SITE_STATUS.overall : null;
  const oLabel = { fresh: 'рыночные данные актуальны', stale: 'часть данных устарела', fallback: 'резервные данные', broken: 'часть данных недоступна' };
  const oText = overall === 'fresh' ? oLabel.fresh
    : ((SITE_STATUS && SITE_STATUS.overall_message) ? SITE_STATUS.overall_message : (oLabel[overall] || overall));
  const chip = overall
    ? `<span class="ds-health ${cls[overall] || ''}" title="Свежесть данных по торговому календарю MOEX">● ${esc(oText)}</span>`
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
const EV_SRC_LABEL = { moex_iss: 'MOEX ISS', cbr_schedule: 'график ЦБ РФ', company_disclosure: 'раскрытие эмитента', smartlab: 'календарь SmartLab' };

function loadEvents(cb) {
  if (EVENTS_DATA) { if (cb) cb(); return; }
  fetch(dataURL('events_calendar.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !Array.isArray(j.events)) throw new Error('пустой/битый events_calendar'); EVENTS_DATA = j; if (cb) cb(); })
    .catch((e) => { console.error('[events] не загрузились:', e); EVENTS_DATA = { failed: true, events: [], meta: {} }; if (cb) cb(); });
}

function loadDividendCalendar(cb) {
  if (DIVIDEND_CALENDAR) { if (cb) cb(); return; }
  fetch(dataURL('dividend_calendar.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (!j || j.schema_version !== '2.0' || !Array.isArray(j.events)) throw new Error('неподдерживаемый контракт');
      DIVIDEND_CALENDAR = j; if (cb) cb();
    })
    .catch((e) => {
      console.error('[dividend-calendar] не загрузился:', e);
      DIVIDEND_CALENDAR = { failed: true, events: [], meta: {}, error: e.message }; if (cb) cb();
    });
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

// Компактная строка ленты: категория + кто + что; подробности (описание, время, источник,
// важность) — по клику внутри details. Толстые карточки заменены строками намеренно:
// один взгляд — одна строка, экран вмещает всю картину дня.
function evWeekdayRu(iso) {
  try {
    return new Intl.DateTimeFormat('ru-RU', { weekday: 'short', timeZone: 'Europe/Moscow' })
      .format(new Date(iso + 'T12:00:00+03:00'));
  } catch (_e) { return ''; }
}
function evRowHTML(e, pf, todayIso) {
  const [catLbl, catCls] = EV_CAT[e.event_type] || ['Событие', 'gen'];
  const imp = isNum(e.importance) ? e.importance : 0;
  const impLbl = imp >= 85 ? 'высокая' : imp >= 60 ? 'средняя' : 'низкая';
  const inPf = e.ticker && pf.set.has(e.ticker);
  const wpct = inPf && isNum(pf.weights[e.ticker]) ? ` · ${ru(pf.weights[e.ticker] * 100, 0)}%` : '';
  const who = e.ticker
    ? instrumentIdentityHTML(e.ticker, e.company, e.instrument_type, 'sm', { variant: 'compact', showTypeText: false })
    : `<b>${esc(e.company)}</b>`;
  const isAnnounced = e.data_status === 'announced';
  const srcLbl = EV_SRC_LABEL[e.source] || esc(e.source || 'источник н/д');
  const src = e.source_url ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener">${srcLbl}</a>` : srcLbl;
  const staleNote = e.data_status && !['fresh', 'scheduled', 'announced'].includes(e.data_status)
    ? ` · <span class="ev-stale">данные ${esc(e.data_status)}</span>` : '';
  const bodyBits = [
    e.description ? `<p>${esc(e.description)}</p>` : '',
    `<p class="ev-row-meta">${e.time_msk ? `${esc(e.time_msk)} МСК · ` : ''}${e.pair_note ? `${esc(e.pair_note)} · ` : ''}важность: ${impLbl} · Источник: ${src}${staleNote}${isAnnounced ? ' · <span class="ev-announced">анонс, сверьте у эмитента</span>' : ''}</p>`,
  ].filter(Boolean).join('');
  return `<details class="ev-row${inPf ? ' ev-row-pf' : ''}${imp < 60 ? ' ev-row-low' : ''}">
    <summary>
      <span class="ev-chip ev-cat-${catCls}">${catLbl}</span>
      <span class="ev-row-main">${who} — ${esc(e.title)}</span>
      ${inPf ? `<span class="ev-chip ev-chip-pf" title="Бумага есть в вашем портфеле${wpct ? `, вес${wpct}` : ''}">моё${wpct}</span>` : ''}
      ${isAnnounced ? `<span class="ev-chip ev-chip-warn" title="Дата не подтверждена MOEX или документом эмитента">анонс</span>` : ''}
    </summary>
    <div class="ev-row-body">${bodyBits}</div>
  </details>`;
}

// Строка-вердикт по портфелю: «что моё и сколько» — раньше эта информация была размазана
// по бейджам карточек, KPI-плашкам календаря и summary. Сумма — та же логика, что в календаре.
function eventsPortfolioVerdictHTML(pf, todayIso) {
  // Без инлайнового onclick (CSP §6.4) — делегированный слушатель в initRouter() по data-divcal-tab.
  const btn = (tab, label) => `<button type="button" class="events-verdict-btn" data-divcal-tab="${esc(tab)}">${label}</button>`;
  if (!pf.hasPortfolio) {
    return `<div class="events-verdict events-verdict-empty">
      <span class="events-verdict-text">Добавьте портфель во вкладке «Портфель» — события ваших бумаг будут подсвечены, а здесь появится сумма ожидаемых дивидендов.</span>
      ${btn('upcoming', 'Весь календарь ↓')}</div>`;
  }
  if (!DIVIDEND_CALENDAR || DIVIDEND_CALENDAR.failed) {
    return `<div class="events-verdict">
      <span class="ev-chip ev-chip-pf">Мой портфель</span>
      <span class="events-verdict-text">Сумма ожидаемых дивидендов недоступна: календарь выплат не загрузился. События ваших бумаг в ленте отмечены «моё».</span></div>`;
  }
  const portfolio = dividendPortfolioMap();
  const combined = dividendCombinedEvents(DIVIDEND_CALENDAR.events || [], dividendDiscoveryEvents(todayIso));
  const inWindow = (value, days = 90) => { const d = evDayDiff(value, todayIso); return d >= 0 && d <= days; };
  const mine = combined.filter((ev) => portfolio[ev.secid]
    && inWindow(ev.payment_date || ev.payment_deadline_nominee || ev.payment_deadline_registered || ev.record_date));
  if (!mine.length) {
    return `<div class="events-verdict">
      <span class="ev-chip ev-chip-pf">Мой портфель</span>
      <span class="events-verdict-text">По вашим бумагам опубликованных будущих выплат в ближайшие 90 дней нет.</span>
      ${btn('upcoming', 'Весь календарь ↓')}</div>`;
  }
  const gross = mine.reduce((sum, ev) => sum + (dividendPortfolioGross(ev, portfolio) || 0), 0);
  const relevant = (ev) => (inWindow(ev.record_date) ? ev.record_date : (ev.payment_date || ev.payment_deadline_nominee || ev.payment_deadline_registered || ev.record_date));
  const nearest = mine.slice().sort((a, b) => String(relevant(a)).localeCompare(String(relevant(b))))[0];
  const nearLbl = inWindow(nearest.record_date)
    ? `отсечка ${dividendDateLabel(nearest.record_date)}`
    : (nearest.payment_date ? `выплата ${dividendDateLabel(nearest.payment_date)}` : `выплата до ${dividendDateLabel(nearest.payment_deadline_nominee || nearest.payment_deadline_registered)}`);
  const grossTxt = gross > 0 ? ` · ожидается ≈ <b>${ru(gross, 0)} ₽</b> валовыми` : '';
  return `<div class="events-verdict" title="Валовая сумма до налога, если позиция удержана до отсечки; фактическое зачисление может быть позже">
    <span class="ev-chip ev-chip-pf">Мой портфель</span>
    <span class="events-verdict-text">Ближайшее — ${instrumentIdentityHTML(nearest.secid, nearest.name, nearest.instrument_type, 'xs', { variant: 'compact', showTypeText: false })}: ${nearLbl}${grossTxt} · ${mine.length} ${dividendEventWord(mine.length)} / 90 дней</span>
    ${btn('portfolio', 'Календарь портфеля ↓')}</div>`;
}

// «Одна выплата — одна карточка»: SmartLab присылает на каждый дивиденд два события
// (последний день покупки + закрытие реестра). В ленте это выглядит дублем — та же бумага
// попадает и в «Сегодня», и в «За выходные»/«Ближайшие». Оставляем одно событие на выплату
// (будущее приоритетнее прошедшего, ближе к сегодня — лучше), вторую дату показываем строкой.
function evCollapseDividendPairs(events, todayIso) {
  const PAIR = new Set(['last_buy_day', 'dividend_registry_close']);
  const groups = new Map();
  events.forEach((e) => {
    if (!PAIR.has(e.event_type) || !e.ticker) return;
    const key = `${e.ticker}|${e.record_date || e.date}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  });
  const drop = new Set();
  const dmy = (iso) => iso.slice(8, 10) + '.' + iso.slice(5, 7);
  groups.forEach((pair) => {
    if (pair.length < 2) return;
    const score = (e) => { const d = evDayDiff(e.date, todayIso); return d >= 0 ? d : 1000 - d; };
    const keep = pair.slice().sort((a, b) => score(a) - score(b))[0];
    const other = pair.find((e) => e !== keep);
    pair.forEach((e) => { if (e !== keep) drop.add(e); });
    keep.pair_note = keep.event_type === 'dividend_registry_close'
      ? (evDayDiff(other.date, todayIso) < 0 ? `покупка под дивиденд была до ${dmy(other.date)}` : `последний день покупки — ${dmy(other.date)}`)
      : `закрытие реестра — ${dmy(other.date)}`;
  });
  return events.filter((e) => !drop.has(e));
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
  const { iso: todayIso, weekday } = mskNow();
  const events = evCollapseDividendPairs(Array.isArray(EVENTS_DATA.events) ? EVENTS_DATA.events : [], todayIso);
  const pf = eventsPortfolioContext();

  // свежесть: приоритет — site_status.events (учитывает торговый календарь MOEX);
  // фолбэк — возраст generated_at с поблажкой на выходные (пт→пн ≠ «устарело»).
  const gen = meta.generated_at || null;
  const ageDays = gen ? (Date.now() - Date.parse(gen)) / 86400000 : null;
  const evStatus = (SITE_STATUS && SITE_STATUS.blocks && SITE_STATUS.blocks.events) ? SITE_STATUS.blocks.events : null;
  const lenient = (weekday === 'Sat' || weekday === 'Sun' || weekday === 'Mon') ? 3.4 : 1.6;  // разрыв выходных
  const staleData = EVENTS_DATA.failed || meta.status === 'fallback' || meta.status === 'broken'
    || (evStatus ? ['stale', 'broken'].includes(evStatus.status) : (ageDays != null && ageDays > lenient));
  const updTxt = gen ? `${gen.slice(8, 10)}.${gen.slice(5, 7)}.${gen.slice(0, 4)} ${gen.slice(11, 16)} МСК` : 'н/д';

  const todays = events.filter((e) => e.date === todayIso).sort((a, b) => evSort(a, b, pf));
  const weekend = weekday === 'Mon'
    ? events.filter((e) => { const d = evDayDiff(e.date, todayIso); return d <= -1 && d >= -3; }).sort((a, b) => evSort(a, b, pf))
    : [];
  // Единая хронологическая лента до 14 дней ВКЛЮЧАЯ ЦБ (раньше ЦБ дублировался отдельным
  // якорем — теперь якорь показываем только если заседание дальше горизонта ленты).
  const upcoming = events.filter((e) => { const d = evDayDiff(e.date, todayIso); return d >= 1 && d <= 14; })
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : evSort(a, b, pf)));
  const nextCbr = events.filter((e) => e.event_type === 'cbr_rate_decision' && evDayDiff(e.date, todayIso) >= 1)
    .sort((a, b) => (a.date < b.date ? -1 : 1))[0] || null;
  const cbrInFeed = nextCbr && evDayDiff(nextCbr.date, todayIso) <= 14;

  let html = `<div class="events-head">
    <h2>Что впереди: события и дивиденды</h2>
    <span class="events-upd ${staleData ? 'events-stale' : ''}">Обновлено: ${updTxt}</span></div>`;

  if (EVENTS_DATA.failed) {
    html += `<div class="events-banner events-banner-risk">Календарь событий сейчас недоступен. Показать нечего — данные не загрузились.</div>`;
    el.innerHTML = html; return;
  }
  if (staleData) {
    html += `<div class="events-banner events-banner-warn">Календарь событий устарел или резервный. Последнее успешное обновление: ${updTxt}. Актуальность сверяйте с источником.</div>`;
  }

  // Level 1: что моё (вердикт по портфелю)
  html += eventsPortfolioVerdictHTML(pf, todayIso);

  // Лента: Сегодня → будущие даты (сгруппировано по дням)
  const dayHead = (iso, label) => `<div class="ev-day-head"><b>${label}</b><span>${evWeekdayRu(iso)}, ${iso.slice(8, 10)}.${iso.slice(5, 7)}</span></div>`;
  html += `<div class="ev-feed">`;
  html += dayHead(todayIso, 'Сегодня');
  if (!todays.length) {
    html += `<div class="ev-day-empty">Подтверждённых событий нет. Дивидендные <b>прогнозы</b> по всем бумагам — во вкладке «Акции».</div>`;
  } else {
    const top = todays.slice(0, 8), rest = todays.slice(8);
    html += top.map((e) => evRowHTML(e, pf, todayIso)).join('');
    if (rest.length) {
      html += `<details class="events-more"><summary>Ещё ${rest.length} ${dividendEventWord(rest.length)} сегодня</summary>${rest.map((e) => evRowHTML(e, pf, todayIso)).join('')}</details>`;
    }
  }
  // Будущие: первые ~8 строк видимы, остальные дни — под details
  const byDate = new Map();
  upcoming.forEach((e) => { if (!byDate.has(e.date)) byDate.set(e.date, []); byDate.get(e.date).push(e); });
  const dayGroups = Array.from(byDate.entries());
  let shown = 0; const visible = []; const folded = [];
  dayGroups.forEach(([iso, list]) => { (shown < 8 ? visible : folded).push([iso, list]); shown += list.length; });
  const dayBlock = ([iso, list]) => {
    const d = evDayDiff(iso, todayIso);
    return dayHead(iso, d === 1 ? 'Завтра' : `Через ${d} ${d % 10 === 1 && d % 100 !== 11 ? 'день' : (d % 10 >= 2 && d % 10 <= 4 && !(d % 100 >= 12 && d % 100 <= 14) ? 'дня' : 'дней')}`)
      + list.map((e) => evRowHTML(e, pf, todayIso)).join('');
  };
  html += visible.map(dayBlock).join('');
  if (folded.length) {
    const foldedCount = folded.reduce((s, [, list]) => s + list.length, 0);
    html += `<details class="events-more"><summary>Дальше в 14 днях — ещё ${foldedCount} ${dividendEventWord(foldedCount)}</summary>${folded.map(dayBlock).join('')}</details>`;
  }
  html += `</div>`;

  // Якорь ЦБ — только когда заседание дальше горизонта ленты (иначе оно уже в ленте)
  if (nextCbr && !cbrInFeed) {
    const nd = evDayDiff(nextCbr.date, todayIso);
    const dmy = nextCbr.date.slice(8, 10) + '.' + nextCbr.date.slice(5, 7) + '.' + nextCbr.date.slice(0, 4);
    const when = `через ${nd} ${nd % 10 === 1 && nd % 100 !== 11 ? 'день' : (nd % 10 >= 2 && nd % 10 <= 4 && !(nd % 100 >= 12 && nd % 100 <= 14) ? 'дня' : 'дней')}`;
    html += `<div class="events-anchor"><span class="events-anchor-cat">ЦБ</span>
      <span>Следующее заседание ЦБ по ключевой ставке — <b>${dmy}</b> (${when}). Влияет на весь рынок: акции, облигации, ОФЗ, ставку по вкладам.</span></div>`;
  }

  // Понедельник: что накопилось за выходные (свёрнуто — прошлое не должно спорить с будущим)
  if (weekend.length) {
    html += `<details class="events-more events-weekend"><summary>Что было за выходные (${weekend.length})</summary>${weekend.slice(0, 6).map((e) => evRowHTML(e, pf, todayIso)).join('')}</details>`;
  }

  html += `<div class="events-foot muted">События — из открытых источников (${(meta.sources || []).map((s) => EV_SRC_LABEL[s] || s).join(', ') || 'MOEX ISS, ЦБ'}). Фильтр «сегодня» — по московскому времени. Информационно, не индивидуальная инвестиционная рекомендация.</div>`;
  el.innerHTML = html;
}

// ── Дивидендный календарь РФ: official-by-default + локальный cash-flow портфеля ──
const DIVIDEND_CALENDAR_FILTER_KEY = 'dividendFactorStrategies.dividendCalendarFilters.v2';
const DIVIDEND_DECISION_LABELS = {
  shareholders_approved: ['утверждено', 'approved'], market_confirmed: ['подтверждено MOEX', 'confirmed'],
  board_recommended: ['рекомендовано СД', 'recommended'], discovery_announced: ['анонс SmartLab', 'announced'],
  cancelled: ['отменено', 'cancelled'], unknown: ['статус не подтверждён', 'unknown'],
};
const DIVIDEND_EVENT_LABELS = {
  buyable: 'можно купить', last_buy_today: 'последний день покупки', ex_dividend: 'покупка под дивиденд закрыта',
  record_closed: 'реестр закрыт', payment_scheduled: 'выплата назначена',
  payment_deadline_open: 'ожидается выплата', payment_deadline_passed_unknown: 'срок прошёл, статус неизвестен', cancelled: 'отменено',
};
const DIVIDEND_ALLOWED_HOSTS = new Set(['iss.moex.com', 'moex.com', 'www.moex.com', 'e-disclosure.ru', 'www.e-disclosure.ru', 'tbank.ru', 'www.tbank.ru', 'smart-lab.ru', 'www.smart-lab.ru']);
let DIVIDEND_CURRENT_EVENTS = [];
let DIVIDEND_FILTERS = (() => {
  const base = { tab: 'portfolio', range: 90, search: '', minYield: 0, modeChosen: false };
  try { return { ...base, ...JSON.parse(localStorage.getItem(DIVIDEND_CALENDAR_FILTER_KEY) || '{}') }; } catch (_e) { return base; }
})();

function dividendSaveFilters() {
  try { localStorage.setItem(DIVIDEND_CALENDAR_FILTER_KEY, JSON.stringify(DIVIDEND_FILTERS)); } catch (_e) { /* optional */ }
}
function dividendDateLabel(value, withYear = false) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return '—';
  return `${value.slice(8, 10)}.${value.slice(5, 7)}${withYear ? '.' + value.slice(0, 4) : ''}`;
}
function dividendSafeUrl(raw) {
  try { const u = new URL(String(raw || '')); return u.protocol === 'https:' && DIVIDEND_ALLOWED_HOSTS.has(u.hostname) ? u.href : ''; }
  catch (_e) { return ''; }
}
function dividendDeepLink() {
  const raw = (location.hash || '').replace(/^#/, '');
  const [section, query] = raw.split('?', 2);
  if (section !== 'market') return { open: false, ticker: '' };
  const params = new URLSearchParams(query || '');
  if (params.get('calendar') !== 'dividends') return { open: false, ticker: '' };
  return { open: true, ticker: String(params.get('ticker') || '').trim().toUpperCase() };
}
function applyDividendDeepLink() {
  const link = dividendDeepLink();
  if (link.open && link.ticker && DIVIDEND_FILTERS.search !== link.ticker) {
    DIVIDEND_FILTERS.search = link.ticker;
    dividendSaveFilters();
  }
}
function dividendCsvCell(value) {
  return `"${String(value == null ? '' : value).replaceAll('"', '""')}"`;
}
function dividendIcsText(value) {
  return String(value == null ? '' : value).replace(/[\\,;]/g, '\\$&').replace(/\r?\n/g, '\\n');
}
function dividendDownload(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.hidden = true;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
function downloadDividendExport(format) {
  const rows = DIVIDEND_CURRENT_EVENTS || [];
  const today = mskNow().iso;
  if (format === 'csv') {
    const header = ['ticker', 'company', 'decision_status', 'dividend_per_share', 'currency', 'yield_pct', 'last_buy_date', 'record_date', 'payment_date', 'payment_deadline_nominee', 'source_url'];
    const lines = [header.map(dividendCsvCell).join(',')].concat(rows.map((event) => [
      event.secid, event.name, event.decision_status, event.dividend_value, event.currency, event.yield_pct,
      event.last_buy_date, event.record_date, event.payment_date, event.payment_deadline_nominee,
      dividendSafeUrl((event.source_evidence || [])[0]?.source_url),
    ].map(dividendCsvCell).join(',')));
    dividendDownload(`dividend-calendar-${today}.csv`, '\uFEFF' + lines.join('\n'), 'text/csv;charset=utf-8');
    return;
  }
  const events = rows.flatMap((event) => {
    const output = [];
    const source = dividendSafeUrl((event.source_evidence || [])[0]?.source_url);
    for (const [kind, when, title] of [
      ['last-buy', event.last_buy_date, 'Последний день покупки'], ['record', event.record_date, 'Закрытие реестра'],
      ['payment', event.payment_date || event.payment_deadline_nominee, event.payment_date ? 'Дата выплаты источника' : 'Крайний срок номинальному держателю'],
    ]) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(String(when || ''))) continue;
      output.push(['BEGIN:VEVENT', `UID:${dividendIcsText(event.id)}-${kind}@dividend-factor-strategies`, `DTSTART;VALUE=DATE:${when.replaceAll('-', '')}`,
        `SUMMARY:${dividendIcsText(`${event.secid}: ${title}`)}`,
        `DESCRIPTION:${dividendIcsText(`${event.dividend_value ?? '—'} ${event.currency || ''}; ${event.decision_status}${source ? `; ${source}` : ''}`)}`,
        'END:VEVENT'].join('\r\n'));
    }
    return output;
  });
  dividendDownload(`dividend-calendar-${today}.ics`, ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Dividend Factor Strategies//RU Dividend Calendar//RU', 'CALSCALE:GREGORIAN', ...events, 'END:VCALENDAR'].join('\r\n') + '\r\n', 'text/calendar;charset=utf-8');
}
function dividendPortfolioMap() {
  const map = {};
  let rows = [];
  try { rows = (typeof myPortfolioLoad === 'function' ? myPortfolioLoad() : []) || []; } catch (_e) { rows = []; }
  rows.forEach((row) => {
    const ticker = typeof pfxCanonTicker === 'function' ? pfxCanonTicker(row.ticker) : String(row.ticker || '').toUpperCase();
    if (ticker && isNum(row.quantity) && row.quantity > 0) map[ticker] = (map[ticker] || 0) + row.quantity;
  });
  return map;
}
function dividendPortfolioGross(event, portfolio) {
  const quantity = portfolio[event.secid] || 0;
  return quantity && isNum(event.dividend_value) ? quantity * event.dividend_value : null;
}
function dividendEventWord(count) {
  const n = Math.abs(Number(count)) % 100, n10 = n % 10;
  if (n > 10 && n < 20) return 'событий';
  if (n10 === 1) return 'событие';
  if (n10 >= 2 && n10 <= 4) return 'события';
  return 'событий';
}
function dividendRelevantDate(event, tab) {
  if (tab === 'portfolio') return event.payment_date || event.payment_deadline_nominee || event.payment_deadline_registered || event.record_date;
  if (tab === 'buyable') return event.last_buy_date;
  if (tab === 'changes') return String(event.last_changed_at || '').slice(0, 10);
  return event.record_date;
}
function dividendFilteredEvents(events, todayIso, portfolio) {
  const startMs = Date.parse(todayIso + 'T00:00:00+03:00');
  const endMs = startMs + Number(DIVIDEND_FILTERS.range || 90) * 86400000;
  const query = String(DIVIDEND_FILTERS.search || '').trim().toLowerCase();
  return events.filter((event) => {
    if (DIVIDEND_FILTERS.tab === 'portfolio' && !portfolio[event.secid]) return false;
    if (DIVIDEND_FILTERS.tab === 'buyable' && !['buyable', 'last_buy_today'].includes(event.event_status)) return false;
    if (DIVIDEND_FILTERS.tab === 'upcoming' && evDayDiff(event.record_date, todayIso) < 0) return false;
    if (DIVIDEND_FILTERS.tab === 'confirmed' && !['shareholders_approved', 'market_confirmed'].includes(event.decision_status)) return false;
    if (DIVIDEND_FILTERS.tab === 'confirmed' && evDayDiff(event.record_date, todayIso) < 0) return false;
    if (DIVIDEND_FILTERS.tab === 'changes' && !['new', 'updated', 'cancelled'].includes(event.change_type)) return false;
    if (isNum(event.yield_pct) ? event.yield_pct < Number(DIVIDEND_FILTERS.minYield || 0) : Number(DIVIDEND_FILTERS.minYield || 0) > 0) return false;
    if (query && !String(event.secid || '').toLowerCase().includes(query) && !String(event.name || '').toLowerCase().includes(query)) return false;
    const relevant = dividendRelevantDate(event, DIVIDEND_FILTERS.tab);
    const valueMs = Date.parse(String(relevant || '') + 'T00:00:00+03:00');
    if (!Number.isFinite(valueMs)) return false;
    if (DIVIDEND_FILTERS.tab === 'changes') return valueMs >= startMs - 30 * 86400000 && valueMs <= startMs;
    return valueMs >= startMs && valueMs <= endMs;
  }).sort((a, b) => String(dividendRelevantDate(a, DIVIDEND_FILTERS.tab)).localeCompare(String(dividendRelevantDate(b, DIVIDEND_FILTERS.tab))) || String(a.secid).localeCompare(String(b.secid)));
}
function dividendDiscoveryEvents(todayIso) {
  const rows = EVENTS_DATA && Array.isArray(EVENTS_DATA.events) ? EVENTS_DATA.events : [];
  const lastBuyByKey = new Map(rows.filter((event) => event.source === 'smartlab' && event.event_type === 'last_buy_day')
    .map((event) => [`${event.ticker}|${event.record_date || String(event.id || '').match(/\d{4}-\d{2}-\d{2}/)?.[0] || ''}`, event.date]));
  return rows.filter((event) => event.source === 'smartlab' && event.data_status === 'announced' && event.event_type === 'dividend_registry_close')
    .map((event) => {
      const amountMatch = String(event.description || '').match(/₽([\d.,]+)/);
      const yieldMatch = String(event.description || '').match(/≈([\d.,]+)%/);
      const recordDate = event.record_date || event.date;
      const lastBuyDate = event.last_buy_date || lastBuyByKey.get(`${event.ticker}|${recordDate}`) || null;
      const lastBuyDiff = lastBuyDate ? evDayDiff(lastBuyDate, todayIso) : null;
      const recordDiff = evDayDiff(recordDate, todayIso);
      const eventStatus = lastBuyDiff === 0 ? 'last_buy_today' : lastBuyDiff > 0 ? 'buyable' : recordDiff >= 0 ? 'ex_dividend' : 'record_closed';
      return {
        id: event.id, secid: event.ticker, name: event.company, decision_status: 'discovery_announced',
        verification_status: 'discovery_only', event_status: eventStatus, record_date: recordDate,
        last_buy_date: lastBuyDate, last_buy_date_source: event.last_buy_date_source || 'calculated_settlement',
        payment_date: event.payment_date || null,
        dividend_value: isNum(event.dividend_value) ? event.dividend_value : amountMatch ? Number(amountMatch[1].replace(',', '.')) : null,
        yield_pct: isNum(event.yield_pct) ? event.yield_pct : yieldMatch ? Number(yieldMatch[1].replace(',', '.')) : null,
        currency: event.currency || 'RUB', source_evidence: [{ source: 'smartlab', source_url: event.source_url, fields: ['discovery announcement'] }],
        quality_flags: ['discovery_only', 'not_confirmed_by_moex'], last_verified_at: EVENTS_DATA?.meta?.generated_at || null,
        discovery_description: event.description || '', change_type: 'new', has_conflict: false,
      };
    });
}
function dividendCombinedEvents(officialEvents, smartlabEvents) {
  const byKey = new Map();
  [...officialEvents, ...smartlabEvents].forEach((event) => {
    // ключ — по secid: у discovery-событий (SmartLab/T-Инвест) нет ISIN, и ключ по isin
    // давал бы одной выплате две строки, как только официальный слой заполнит isin
    const key = `${event.secid || event.isin}|${event.record_date}|${event.event_kind || 'cash_dividend'}`;
    if (!byKey.has(key)) byKey.set(key, event);
  });
  return Array.from(byKey.values());
}
function dividendDecisionBadge(event) {
  if (event.verification_status === 'broker_structured_discovery') {
    return '<span class="dc-status dc-status-broker">календарь T-Инвестиций</span>';
  }
  const value = DIVIDEND_DECISION_LABELS[event.decision_status] || DIVIDEND_DECISION_LABELS.unknown;
  return `<span class="dc-status dc-status-${value[1]}">${esc(value[0])}</span>`;
}
function dividendChangeBadges(event, inPortfolio) {
  const out = [];
  if (inPortfolio) out.push('<span class="dc-tag dc-tag-portfolio">в портфеле</span>');
  if (event.change_type === 'new') out.push('<span class="dc-tag">новое</span>');
  if (event.change_type === 'updated') out.push('<span class="dc-tag">изменено</span>');
  if (event.has_conflict) out.push('<span class="dc-tag dc-tag-risk">конфликт</span>');
  return out.join('');
}
function dividendSourceDetails(event) {
  if (event.verification_status === 'broker_structured_discovery') {
    const source = dividendSafeUrl((event.source_evidence || [])[0]?.source_url);
    return `<details class="dc-source-details"><summary>Источник и ограничения</summary><div class="dc-source-panel">
      <p><b>Структурированный календарь брокера:</b> ${source ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">T-Инвестиции</a>` : 'T-Инвестиции'}.</p>
      <p class="dc-discovery-warning">Будущая дата найдена в API брокера, но ещё не подтверждена MOEX или документом эмитента. В официальный счётчик не входит.</p>
    </div></details>`;
  }
  if (event.decision_status === 'discovery_announced') {
    const source = dividendSafeUrl((event.source_evidence || [])[0]?.source_url);
    return `<details class="dc-source-details"><summary>Источник и ограничения</summary><div class="dc-source-panel">
      <p><b>Discovery-источник:</b> ${source ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">SmartLab</a>` : 'SmartLab'}.</p>
      <p>${esc(event.discovery_description || '')}</p>
      <p class="dc-discovery-warning">Дата, сумма и доходность не подтверждены MOEX или документом эмитента. Расчётный последний день покупки требует ручной сверки.</p>
    </div></details>`;
  }
  const evidence = (event.source_evidence || []).map((item) => {
    const url = dividendSafeUrl(item.source_url);
    const label = item.source === 'moex_iss' ? 'MOEX ISS' : item.source === 'e_disclosure' ? 'e-disclosure' : item.source === 'official_issuer' ? 'сайт эмитента' : item.source === 'tinvest' ? 'Т-Инвестиции' : item.source;
    return url ? `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a> · ${esc((item.fields || []).join(', '))}</li>` : '';
  }).filter(Boolean).join('');
  const flags = (event.quality_flags || []).map((flag) => `<span class="dc-quality-flag">${esc(flag.replaceAll('_', ' '))}</span>`).join('');
  const conflicts = (event.conflicts || []).map((item) => `<li>${esc(item.field)}: ${esc((item.values || []).join(' / '))}</li>`).join('');
  const provenance = Object.entries(event.field_provenance || {}).map(([field, source]) => `<li>${esc(field)}: ${esc(source)}</li>`).join('');
  return `<details class="dc-source-details"><summary>Источник и качество</summary>
    <div class="dc-source-panel">
      <dl><dt>Проверено</dt><dd>${esc(String(event.last_verified_at || '—').replace('T', ' ').slice(0, 16))}</dd>
        <dt>Цена</dt><dd>${event.price_asof ? `${esc(event.price_field || '')} на ${esc(event.price_asof)}` : 'нет цены'}</dd>
        <dt>Статус события</dt><dd>${esc(DIVIDEND_EVENT_LABELS[event.event_status] || event.event_status || '—')}</dd></dl>
      ${flags ? `<div class="dc-quality-flags">${flags}</div>` : ''}
      ${provenance ? `<div class="dc-provenance"><b>Происхождение полей</b><ul>${provenance}</ul></div>` : ''}
      ${conflicts ? `<div class="dc-conflicts"><b>Расхождения источников</b><ul>${conflicts}</ul></div>` : ''}
      ${evidence ? `<ul class="dc-evidence">${evidence}</ul>` : '<div class="muted">Ссылка на evidence недоступна.</div>'}
      <p class="muted">Крайний срок выплаты — правовая граница, а не прогноз даты зачисления брокером.</p>
    </div></details>`;
}
function dividendPaymentLabel(event) {
  if (event.payment_date) return `${dividendDateLabel(event.payment_date)} · дата источника`;
  if (event.payment_deadline_nominee) return `до ${dividendDateLabel(event.payment_deadline_nominee)} · номинальному держателю`;
  return '—';
}
function dividendRowsHTML(rows, portfolio, tab) {
  if (!rows.length) {
    const labels = {
      portfolio: 'По бумагам из вашего портфеля будущих выплат с опубликованными датами не найдено.',
      buyable: 'Сейчас нет отсечек, под которые ещё можно купить. Вне дивидендного сезона это нормальная ситуация.',
      upcoming: 'Будущих отсечек с опубликованной датой в выбранном периоде нет.',
      confirmed: 'Подтверждённых событий в выбранном периоде нет.',
      changes: 'Новых, изменённых или отменённых событий за последние 30 дней нет.',
    };
    return `<div class="dc-empty">${labels[tab] || labels.confirmed} Измените фильтры.</div>`;
  }
  // Строки с закрытой покупкой (ex-dividend/реестр закрыт) приглушаются: они остаются для
  // контекста, но не должны выглядеть как действующие возможности («ничего не работает»-эффект).
  const closedCls = (event) => (['ex_dividend', 'record_closed'].includes(event.event_status) ? ' dc-row-closed' : '');
  const desktop = rows.map((event) => {
    const gross = dividendPortfolioGross(event, portfolio), inPortfolio = Boolean(portfolio[event.secid]);
    return `<tr class="${closedCls(event).trim()}">
      <td class="left"><div class="dc-company">${instrumentIdentityHTML(event.secid, event.name, event.instrument_type, 'sm')}</div><div class="dc-tags">${dividendChangeBadges(event, inPortfolio)}</div></td>
      <td class="left">${dividendDecisionBadge(event)}</td>
      <td class="tnum"><b>${ru(event.dividend_value, 2)} ${esc(event.currency || '')}</b></td>
      <td class="tnum">${isNum(event.yield_pct) ? `<b>${ru(event.yield_pct, 1)}%</b><small>цена ${dividendDateLabel(event.price_asof)}</small>` : '<span class="muted">нет сопоставимой цены</span>'}</td>
      <td class="tnum"><b>${dividendDateLabel(event.last_buy_date)}</b><small>${esc(DIVIDEND_EVENT_LABELS[event.event_status] || '')}</small></td>
      <td class="tnum">${dividendDateLabel(event.record_date)}</td>
      <td class="tnum">${esc(dividendPaymentLabel(event))}</td>
      <td class="tnum">${gross != null ? `<b>${ru(gross, 0)} ₽</b><small>валовыми · ${ru(portfolio[event.secid], 0)} акц.</small>` : '<span class="muted">—</span>'}</td>
      <td class="left">${dividendSourceDetails(event)}</td>
    </tr>`;
  }).join('');
  const mobile = rows.map((event) => {
    const gross = dividendPortfolioGross(event, portfolio), inPortfolio = Boolean(portfolio[event.secid]);
    return `<article class="dc-mobile-card${closedCls(event)}">
      <header><div>${instrumentIdentityHTML(event.secid, event.name, event.instrument_type, 'sm')}</div>${dividendDecisionBadge(event)}</header>
      <div class="dc-tags">${dividendChangeBadges(event, inPortfolio)}</div>
      <div class="dc-mobile-primary"><div><span>Дивиденд</span><b>${ru(event.dividend_value, 2)} ${esc(event.currency || '')}</b></div><div><span>Доходность к цене</span><b>${isNum(event.yield_pct) ? ru(event.yield_pct, 1) + '%' : '—'}</b></div></div>
      <dl><dt>Купить до</dt><dd>${dividendDateLabel(event.last_buy_date, true)}</dd><dt>Реестр</dt><dd>${dividendDateLabel(event.record_date, true)}</dd><dt>Выплата / до</dt><dd>${esc(dividendPaymentLabel(event))}</dd>${gross != null ? `<dt>Мой портфель</dt><dd><b>${ru(gross, 0)} ₽ валовыми</b></dd>` : ''}</dl>
      ${dividendSourceDetails(event)}
    </article>`;
  }).join('');
  return `<div class="dc-table-wrap"><table class="dc-table"><thead><tr><th scope="col" class="left">Компания</th><th scope="col" class="left">Статус</th><th scope="col">На акцию</th><th scope="col">Доходность</th><th scope="col">Купить до</th><th scope="col">Реестр</th><th scope="col">Выплата / срок</th><th scope="col">Мой портфель</th><th scope="col" class="left">Качество</th></tr></thead><tbody>${desktop}</tbody></table></div><div class="dc-mobile-list">${mobile}</div>`;
}
function dividendCashflowStrip(events, portfolio, todayIso, rangeDays) {
  const endMs = Date.parse(todayIso + 'T00:00:00+03:00') + rangeDays * 86400000;
  const monthTotals = {};
  events.forEach((event) => {
    const when = event.payment_date || event.record_date;
    const ms = Date.parse(String(when || '') + 'T00:00:00+03:00');
    const gross = dividendPortfolioGross(event, portfolio);
    if (gross != null && ms >= Date.parse(todayIso + 'T00:00:00+03:00') && ms <= endMs) monthTotals[String(when).slice(0, 7)] = (monthTotals[String(when).slice(0, 7)] || 0) + gross;
  });
  const entries = Object.entries(monthTotals).sort();
  // Полоса-график имеет смысл от 2 месяцев; один бар — просто число, оно уже есть в вердикте выше
  if (entries.length < 2) return '';
  const max = Math.max(...entries.map(([, value]) => value));
  return `<div class="dc-cashflow"><div class="dc-cashflow-head"><b>Ожидаемый валовой cash-flow портфеля</b><span>если позиция была удержана до отсечки; фактическое зачисление может быть позже</span></div><div class="dc-cashflow-bars">${entries.map(([month, value]) => `<div><span>${month.slice(5)}.${month.slice(2, 4)}</span><i style="--dc-bar:${Math.max(8, Math.round(value / max * 100))}%"></i><b>${ru(value, 0)} ₽</b></div>`).join('')}</div></div>`;
}
// Открыть календарь на нужном табе из других блоков (вердикт «Что впереди» и т.п.)
function openDividendCalendarTab(tab) {
  if (tab && ['portfolio', 'buyable', 'upcoming', 'confirmed', 'changes'].includes(tab)) {
    DIVIDEND_FILTERS.tab = tab; DIVIDEND_FILTERS.modeChosen = true; dividendSaveFilters();
  }
  const details = document.getElementById('dividend-calendar');
  if (!details) return;
  details.open = true;
  renderDividendCalendar();
  requestAnimationFrame(() => details.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}

function wireDividendCalendar() {
  const body = document.getElementById('dividend-calendar-body');
  if (!body || body.dataset.wired) return;
  body.dataset.wired = '1';
  body.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-dc-tab]');
    const range = event.target.closest('[data-dc-range]');
    const exportButton = event.target.closest('[data-dc-export]');
    if (tab) {
      DIVIDEND_FILTERS.tab = tab.dataset.dcTab; DIVIDEND_FILTERS.modeChosen = true;
      dividendSaveFilters(); renderDividendCalendar();
    }
    if (range) { DIVIDEND_FILTERS.range = Number(range.dataset.dcRange); dividendSaveFilters(); renderDividendCalendar(); }
    if (exportButton) downloadDividendExport(exportButton.dataset.dcExport);
  });
  body.addEventListener('change', (event) => {
    if (event.target.id === 'dc-min-yield') DIVIDEND_FILTERS.minYield = Number(event.target.value);
    else return;
    dividendSaveFilters(); renderDividendCalendar();
  });
  body.addEventListener('input', debounce((event) => {
    if (event.target.id !== 'dc-search') return;
    DIVIDEND_FILTERS.search = event.target.value; dividendSaveFilters(); renderDividendCalendar('dc-search');
  }, 160));
}
function renderDividendCalendar(refocus) {
  const body = document.getElementById('dividend-calendar-body');
  const summary = document.getElementById('dividend-calendar-summary');
  if (!body || !summary) return;
  wireDividendCalendar();
  if (!DIVIDEND_CALENDAR) { loadDividendCalendar(() => renderDividendCalendar()); body.innerHTML = '<div class="pulse-loading muted">Загрузка официальных событий…</div>'; return; }
  if (!EVENTS_DATA) { loadEvents(() => { renderEventsToday(); renderDividendCalendar(); }); body.innerHTML = '<div class="pulse-loading muted">Сверяем официальный слой и анонсы…</div>'; return; }
  if (DIVIDEND_CALENDAR.failed) {
    summary.textContent = 'данные временно недоступны';
    body.innerHTML = '<div class="dc-alert dc-alert-risk"><b>Календарь временно недоступен.</b><span>Остальные рыночные блоки продолжают работать; попробуйте обновить страницу позже.</span></div>';
    return;
  }
  const meta = DIVIDEND_CALENDAR.meta || {}, events = DIVIDEND_CALENDAR.events || [];
  applyDividendDeepLink();
  const { iso: todayIso } = mskNow(), portfolio = dividendPortfolioMap();
  const discoveryEvents = dividendDiscoveryEvents(todayIso);
  const combined = dividendCombinedEvents(events, discoveryEvents);
  const inWindow = (value, days = 90) => { const diff = evDayDiff(value, todayIso); return diff >= 0 && diff <= days; };
  const officialUpcoming = combined.filter((event) => ['shareholders_approved', 'market_confirmed'].includes(event.decision_status) && inWindow(event.record_date));
  const marketUpcoming = combined.filter((event) => inWindow(event.record_date));
  const buyable = combined.filter((event) => ['buyable', 'last_buy_today'].includes(event.event_status) && inWindow(event.last_buy_date));
  const portfolioUpcoming = combined.filter((event) => portfolio[event.secid]
    && inWindow(event.payment_date || event.payment_deadline_nominee || event.payment_deadline_registered || event.record_date));
  const portfolioGross = portfolioUpcoming.reduce((sum, event) => sum + (dividendPortfolioGross(event, portfolio) || 0), 0);
  const nonOfficialUpcoming = marketUpcoming.filter((event) => !['shareholders_approved', 'market_confirmed'].includes(event.decision_status));
  if (!DIVIDEND_FILTERS.modeChosen) {
    DIVIDEND_FILTERS.tab = portfolioUpcoming.length ? 'portfolio' : buyable.length ? 'buyable' : 'upcoming';
    dividendSaveFilters();
  }
  const filtered = dividendFilteredEvents(combined, todayIso, portfolio);
  DIVIDEND_CURRENT_EVENTS = filtered;
  const healthLabel = meta.status === 'fresh' ? 'данные свежие' : meta.status === 'partial' ? 'частичное покрытие' : meta.status === 'fallback' ? 'резервные данные' : 'статус неизвестен';
  summary.textContent = `можно купить: ${buyable.length} · в портфеле: ${portfolioUpcoming.length} ${dividendEventWord(portfolioUpcoming.length)} / ${ru(portfolioGross, 0)} ₽ · ${healthLabel}`;
  const alert = ['partial', 'fallback'].includes(meta.status) ? `<div class="dc-alert"><b>${meta.status === 'fallback' ? 'Показаны резервные данные.' : 'Часть источников недоступна.'}</b><span>Проверено ${esc(String(meta.generated_at || '').replace('T', ' ').slice(0, 16))}; coverage ${ru(meta.universe_coverage_pct || 0, 0)}%.</span></div>` : '';
  const tabs = [['portfolio', `Мой портфель · ${portfolioUpcoming.length}`], ['buyable', `Можно купить · ${buyable.length}`],
    ['upcoming', `Все будущие · ${marketUpcoming.length}`], ['confirmed', `Подтверждены · ${officialUpcoming.length}`], ['changes', 'Изменения']];
  const nearestBuyDate = buyable.map((event) => event.last_buy_date).sort()[0];
  // Компактная строка-статус вместо 4 KPI-плашек: смысл тот же, экрана — в 4 раза меньше.
  // «Ожидается по портфелю» здесь не дублируем — оно в вердикте блока «Что впереди» и в summary.
  const discoveryNote = nonOfficialUpcoming.length
    ? `<span class="dc-stat dc-stat-warn" title="Даты из SmartLab или календаря T-Инвестиций не подтверждены MOEX и не входят в официальный счётчик"><b>${nonOfficialUpcoming.length}</b> ${nonOfficialUpcoming.length === 1 ? 'анонс ждёт' : 'анонсов ждут'} подтверждения <button type="button" class="dc-link" data-dc-tab="upcoming">показать</button></span>`
    : '';
  body.innerHTML = `${alert}
    <div class="dc-statusline">
      <span class="dc-stat"><b>${buyable.length}</b> можно купить${nearestBuyDate ? ` · дедлайн ${dividendDateLabel(nearestBuyDate)}` : ''}</span>
      <span class="dc-stat"><b>${officialUpcoming.length}</b> подтверждено MOEX / эмитентом</span>
      ${discoveryNote}
    </div>
    ${dividendCashflowStrip(combined, portfolio, todayIso, Number(DIVIDEND_FILTERS.range || 90))}
    <div class="dc-toolbar">
      <div class="dc-tabs" role="tablist" aria-label="Статус дивидендов">${tabs.map(([key, label]) => `<button type="button" role="tab" aria-selected="${DIVIDEND_FILTERS.tab === key}" class="${DIVIDEND_FILTERS.tab === key ? 'active' : ''}" data-dc-tab="${key}">${label}</button>`).join('')}</div>
      ${DIVIDEND_FILTERS.tab === 'changes'
    ? '<div class="dc-range-note" aria-label="Период изменений">последние 30 дней</div>'
    : `<div class="dc-ranges" aria-label="Период">${[30, 90, 365].map((days) => `<button type="button" class="${Number(DIVIDEND_FILTERS.range) === days ? 'active' : ''}" data-dc-range="${days}">${days} дней</button>`).join('')}</div>`}
      <div class="dc-actions" aria-label="Экспорт календаря"><button type="button" data-dc-export="csv" title="Скачать текущую выборку CSV">CSV</button><button type="button" data-dc-export="ics" title="Скачать календарь ICS">ICS</button></div>
    </div>
    <div class="dc-filters">
      <label class="dc-search"><span>Поиск</span><input id="dc-search" type="search" value="${esc(DIVIDEND_FILTERS.search)}" placeholder="Тикер или компания" aria-label="Поиск по тикеру или компании"></label>
      <label class="dc-select"><span>Мин. доходность</span><select id="dc-min-yield" aria-label="Минимальная дивидендная доходность">${[0, 5, 10, 15].map((value) => `<option value="${value}" ${Number(DIVIDEND_FILTERS.minYield) === value ? 'selected' : ''}>${value}%</option>`).join('')}</select></label>
      <span class="dc-count" aria-live="polite">Найдено: <b>${filtered.length}</b></span>
    </div>
    ${dividendRowsHTML(filtered, portfolio, DIVIDEND_FILTERS.tab)}
    <div class="dc-foot">Основной вид не показывает прошедшие отсечки. Cash-flow портфеля — условная валовая сумма: бумага должна была находиться на счёте к последнему дню покупки. Market-confirmed означает запись MOEX; SmartLab и T-Инвестиции остаются отдельным discovery-слоем. Портфель считается локально и не отправляется по сети. Не ИИР.</div>`;
  wireDividendCalendar();
  if (refocus) requestAnimationFrame(() => { const input = document.getElementById(refocus); if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); } });
}

function ensureKpiData() {
  if (!EVENTS_DATA && typeof loadEvents === 'function') loadEvents(() => renderEventsToday());
  // после загрузки календаря перерисовать и ленту: вердикт-строка портфеля берёт из него сумму
  if (!DIVIDEND_CALENDAR && typeof loadDividendCalendar === 'function') loadDividendCalendar(() => { renderDividendCalendar(); renderEventsToday(); });
  if (!MARKET_PE && typeof loadMarketPE === 'function') loadMarketPE(() => renderMarketPE());
  if (!MARKET_PE_HIST && typeof loadMarketPeHistory === 'function') loadMarketPeHistory(() => renderMarketPE());
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

/* ── МАКРО ЦБ: ключевая ставка + инфляция (site/macro_cbr.json, генерит CI) ─────────
   Почему именно эти числа, а не «побольше макро»: ключевая ставка — одновременно
   ставка дисконтирования и альтернатива в виде вклада (при 14% дивдоходность 8%
   проигрывает депозиту), инфляция переводит номинальную доходность в реальную, а их
   разница показывает жёсткость политики. Всё остальное, что отдаёт ЦБ по SOAP
   (RUONIA, MKR, ликвидность, свопы), частному дивидендному инвестору решений не меняет. */

function loadMacroCbr(cb) {
  if (MACRO_CBR) { if (cb) cb(); return; }
  fetch(dataURL('macro_cbr.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (!j || !j.key_rate || !isNum(j.key_rate.current)) throw new Error('неподдерживаемый контракт macro_cbr');
      MACRO_CBR = j; if (cb) cb();
    })
    .catch((e) => { console.warn('[macro] не загрузился:', e.message); MACRO_CBR = { failed: true }; if (cb) cb(); });
}

/** Мини-график «ставка vs инфляция» по годам — inline SVG (CSP: внешних библиотек нет). */
function macroYearsSVG(byYear) {
  const rows = (byYear || []).filter((r) => isNum(r.inflation_yoy));
  if (rows.length < 3) return '';
  const W = 640, H = 150, P = { l: 34, r: 10, t: 10, b: 26 };
  const vals = rows.flatMap((r) => [r.inflation_yoy, isNum(r.key_rate) ? r.key_rate : r.inflation_yoy]);
  const hi = Math.ceil(Math.max(...vals) / 5) * 5, lo = 0;
  const bw = (W - P.l - P.r) / rows.length;
  const Y = (v) => H - P.b - ((v - lo) / ((hi - lo) || 1)) * (H - P.t - P.b);
  const bars = rows.map((r, i) => {
    const x = P.l + i * bw;
    const infl = `<rect class="mc-bar-infl" x="${(x + bw * 0.18).toFixed(1)}" y="${Y(r.inflation_yoy).toFixed(1)}"
      width="${(bw * 0.34).toFixed(1)}" height="${(H - P.b - Y(r.inflation_yoy)).toFixed(1)}"><title>${r.year}: инфляция ${ru(r.inflation_yoy, 2)}%${r.partial ? ' (год не закрыт)' : ''}</title></rect>`;
    const rate = isNum(r.key_rate)
      ? `<rect class="mc-bar-rate" x="${(x + bw * 0.52).toFixed(1)}" y="${Y(r.key_rate).toFixed(1)}"
          width="${(bw * 0.34).toFixed(1)}" height="${(H - P.b - Y(r.key_rate)).toFixed(1)}"><title>${r.year}: ключевая ставка ${ru(r.key_rate, 2)}%</title></rect>` : '';
    return infl + rate
      + `<text class="mc-ax" x="${(x + bw / 2).toFixed(1)}" y="${H - 8}" text-anchor="middle">${String(r.year).slice(2)}</text>`;
  }).join('');
  const grid = [0, hi / 2, hi].map((v) =>
    `<line class="mc-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>
     <text class="mc-ax" x="${P.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v, 0)}</text>`).join('');
  const target = rows[rows.length - 1] && isNum(rows[rows.length - 1].target) ? rows[rows.length - 1].target : 4;
  return `<svg class="mc-svg" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Инфляция и ключевая ставка по годам, ${rows[0].year}–${rows[rows.length - 1].year}">
    ${grid}
    <line class="mc-target" x1="${P.l}" y1="${Y(target).toFixed(1)}" x2="${W - P.r}" y2="${Y(target).toFixed(1)}"/>
    ${bars}
  </svg>`;
}

function renderMacroCbr() {
  const el = document.getElementById('macro-cbr');
  if (!el) return;
  if (!MACRO_CBR) { el.innerHTML = '<div class="pulse-loading muted">Загрузка макроданных ЦБ...</div>'; return; }
  if (MACRO_CBR.failed) {
    el.innerHTML = `<div class="pfx-note muted">${NA}: макроданные Банка России не загрузились. Остальные блоки не затронуты.</div>`;
    return;
  }
  const m = MACRO_CBR, kr = m.key_rate, inf = m.inflation;
  const dir = isNum(kr.change) ? (kr.change < 0 ? '↓' : (kr.change > 0 ? '↑' : '→')) : '';
  const rateNote = isNum(kr.change)
    ? `${dir} ${kr.change > 0 ? '+' : ''}${ru(kr.change, 2)} п.п. решением ${esc(kr.changed_on || '')}`
    : `на ${esc(kr.asof || '')}`;
  const inflNote = isNum(inf.above_target)
    ? `цель ЦБ ${ru(inf.target, 1)}% · выше на ${ru(inf.above_target, 2)} п.п.`
    : `за ${esc(inf.latest_month || '')}`;
  const realTone = isNum(m.real_key_rate) ? (m.real_key_rate >= 5 ? 'risk' : (m.real_key_rate >= 2 ? 'warn' : 'good')) : 'neut';
  const card = (label, value, note, tone) => `
    <div class="signal-card signal-${tone || 'neut'}"><span>${esc(label)}</span><b>${value}</b><em>${note}</em></div>`;
  // Карточка инфляции кликабельна ЦЕЛИКОМ (не только число) и работает с клавиатуры.
  // Смысл самой карточки не меняется: значение, цель, отклонение, месяц данных остаются.
  const cardBtn = (label, value, note, tone) => `
    <button type="button" class="signal-card signal-${tone || 'neut'} signal-open" id="infl-card"
      aria-haspopup="dialog" aria-expanded="false"
      aria-label="Инфляция год к году ${value}, ${String(note).replace(/<[^>]+>/g, '')}. Открыть динамику инфляции">
      <span>${esc(label)}<span class="signal-more" aria-hidden="true">подробнее ›</span></span>
      <b>${value}</b><em>${note}</em></button>`;

  el.innerHTML = `
    <div class="market-signals">
      ${card('Ключевая ставка ЦБ', `${ru(kr.current, 2)}%`, rateNote, kr.change < 0 ? 'good' : (kr.change > 0 ? 'risk' : 'neut'))}
      ${cardBtn('Инфляция, г/г', isNum(inf.latest_yoy) ? `${ru(inf.latest_yoy, 2)}%` : '—', inflNote,
        isNum(inf.above_target) && inf.above_target > 2 ? 'warn' : 'neut')}
      ${card('Реальная ставка', isNum(m.real_key_rate) ? `${ru(m.real_key_rate, 2)} п.п.` : '—',
        'ключевая ставка − инфляция', realTone)}
    </div>
    ${macroYearsSVG(inf.by_year)}
    <div class="mc-legend">
      <span class="mc-lg mc-lg-infl">инфляция г/г</span>
      <span class="mc-lg mc-lg-rate">ключевая ставка</span>
      <span class="mc-lg mc-lg-tg">цель ЦБ ${isNum(inf.target) ? ru(inf.target, 0) + '%' : '4%'}</span>
    </div>
    <div class="pfx-note muted">Источник — <b>Банк России</b>: дневная история ключевой ставки и официальная таблица
      «Инфляция и ключевая ставка». Инфляция публикуется с лагом, поэтому последний месяц —
      <b>${esc(inf.latest_month || '—')}</b>. Столбики по годам: значение на декабрь, для незакрытого года — последний
      доступный месяц. Ничего не интерполируется. Обновляется автоматически вместе с остальными данными сайта.</div>`;
}

/* ── МОДУЛЬ «Динамика инфляции» ────────────────────────────────────────────────
   Открывается кликом по карточке «Инфляция, г/г». Данные — те же, что в блоке
   макро (site/macro_cbr.json), повторной загрузки нет.

   ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ — сознательно:
   • «Год к году» — официальный ряд ЦБ, 138 месяцев. Основной режим.
   • «За месяц» НЕ показывается: из ряда г/г месячное изменение невыводимо без
     уровня индекса, а достраивать его — фабрикация. Пишем это прямо, а не
     прячем переключатель.
   • Недельной вкладки НЕТ сознательно. Файл недельных индексов Росстата
     (Nedel_ipc.xlsx) получен и разобран: в нём только индексы по ~110 ОТДЕЛЬНЫМ
     товарам, сводного недельного ИПЦ там нет. Чтобы показать «недельную инфляцию»,
     пришлось бы взвесить товары весами потребления, которых в файле нет, и выдать
     собственный расчёт за официальную оценку. Пустая вкладка с объяснением тоже
     не нужна — она читается как поломка. */

const INFL_RANGES = [
  { id: '1y', label: '1 год', months: 12 },
  { id: '3y', label: '3 года', months: 36 },
  { id: '5y', label: '5 лет', months: 60 },
  { id: 'all', label: 'весь период', months: null },
];
let INFL_RANGE = '5y';
let INFL_TAB = 'monthly';
let INFL_METRIC = 'yoy';   // yoy | mom

// Три падежа: «10 июня» (род.), «в июне» (предл.), «за май» (вин. = им.).
// Одной формой не обойтись — иначе получается «В июня 2026» и «за мая 2026».
const MONTHS_RU_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_RU_PREP = ['январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре'];
const MONTHS_RU_NOM = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];

function inflMonthLabel(period, form) {
  const [y, m] = String(period || '').split('-');
  const idx = Number(m) - 1;
  const table = form === 'prep' ? MONTHS_RU_PREP : (form === 'nom' ? MONTHS_RU_NOM : MONTHS_RU_GEN);
  return table[idx] ? `${table[idx]} ${y}` : String(period || '');
}

/** Линия инфляции г/г + пунктир цели. Цель показывается ТОЛЬКО здесь: она
 *  относится к годовой инфляции и в месячном приросте смысла не имеет. */
function inflChartSVG(rows, target) {
  if (!rows || rows.length < 2) return '';
  const W = 720, H = 340, P = { l: 44, r: 14, t: 16, b: 34 };
  const vals = rows.map((r) => r.inflation_yoy).filter(isNum);
  const lo = Math.min(0, Math.floor(Math.min(...vals, target || 4) - 1));
  const hi = Math.ceil(Math.max(...vals, target || 4) + 1);
  const X = (i) => P.l + (i / (rows.length - 1)) * (W - P.l - P.r);
  const Y = (v) => H - P.b - ((v - lo) / ((hi - lo) || 1)) * (H - P.t - P.b);

  const step = Math.max(1, Math.ceil((hi - lo) / 6));
  const grid = [];
  for (let v = lo; v <= hi; v += step) {
    grid.push(`<line class="ic-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>
      <text class="ic-ax" x="${P.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v, 0)}</text>`);
  }
  const tickEvery = Math.max(1, Math.round(rows.length / 6));
  const xlab = rows.map((r, i) => (i % tickEvery === 0 || i === rows.length - 1)
    ? `<text class="ic-ax" x="${X(i).toFixed(1)}" y="${H - 12}" text-anchor="middle">${esc(r.month.slice(0, 7))}</text>` : '').join('');

  const path = rows.map((r, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(r.inflation_yoy).toFixed(1)}`).join('');
  const last = rows[rows.length - 1];
  const pts = rows.map((r, i) =>
    `<circle class="ic-hit" cx="${X(i).toFixed(1)}" cy="${Y(r.inflation_yoy).toFixed(1)}" r="9" data-i="${i}"
      ><title>${esc(inflMonthLabel(r.month))}: инфляция ${ru(r.inflation_yoy, 2)}% г/г${
        isNum(target) ? ` · цель ${ru(target, 1)}% · отклонение ${r.inflation_yoy > target ? '+' : ''}${ru(r.inflation_yoy - target, 2)} п.п.` : ''
      } · официальные данные, Банк России</title></circle>`).join('');

  return `<svg class="ic-svg" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="График инфляции год к году за ${rows.length} месяцев, с ${inflMonthLabel(rows[0].month)} по ${inflMonthLabel(last.month)}">
    ${grid.join('')}
    ${isNum(target) ? `<line class="ic-target" x1="${P.l}" y1="${Y(target).toFixed(1)}" x2="${W - P.r}" y2="${Y(target).toFixed(1)}"/>
      <text class="ic-tglab" x="${W - P.r - 2}" y="${(Y(target) - 5).toFixed(1)}" text-anchor="end">цель ${ru(target, 1)}%</text>` : ''}
    <path class="ic-line" d="${path}"/>
    <circle class="ic-last" cx="${X(rows.length - 1).toFixed(1)}" cy="${Y(last.inflation_yoy).toFixed(1)}" r="5"/>
    ${pts}${xlab}
  </svg>`;
}

/** Прирост за месяц — столбцами, с линией нуля: отрицательные месяцы должны быть видны
 *  ниже неё. Цель ЦБ здесь НЕ показывается: она относится к годовой инфляции. */
function inflMomSVG(rows) {
  if (!rows || rows.length < 2) return '';
  const W = 720, H = 340, P = { l: 44, r: 14, t: 16, b: 34 };
  const vals = rows.map((r) => r.mom_pct);
  const hi = Math.max(...vals, 0) * 1.15 + 0.1, lo = Math.min(...vals, 0) * 1.15 - 0.05;
  const bw = (W - P.l - P.r) / rows.length;
  const Y = (v) => H - P.b - ((v - lo) / ((hi - lo) || 1)) * (H - P.t - P.b);
  const zero = Y(0);
  const step = (hi - lo) / 5;
  let grid = '';
  for (let k = 0; k <= 5; k++) {
    const v = lo + step * k;
    grid += `<line class="ic-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>` +
      `<text class="ic-ax" x="${P.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v, 1)}</text>`;
  }
  const bars = rows.map((r, i) => {
    const x = P.l + i * bw + bw * 0.15, w = bw * 0.7;
    const y = r.mom_pct >= 0 ? Y(r.mom_pct) : zero;
    const h = Math.max(1, Math.abs(Y(r.mom_pct) - zero));
    return `<rect class="im-bar ${r.mom_pct < 0 ? 'im-neg' : 'im-pos'}" x="${x.toFixed(1)}" y="${y.toFixed(1)}"
      width="${w.toFixed(1)}" height="${h.toFixed(1)}"><title>${esc(inflMonthLabel(r.month))}: ${r.mom_pct > 0 ? '+' : ''}${ru(r.mom_pct, 2)}% за месяц · официальные данные, Росстат</title></rect>`;
  }).join('');
  const every = Math.max(1, Math.round(rows.length / 6));
  const xlab = rows.map((r, i) => (i % every === 0 || i === rows.length - 1)
    ? `<text class="ic-ax" x="${(P.l + i * bw + bw / 2).toFixed(1)}" y="${H - 12}" text-anchor="middle">${esc(r.month)}</text>` : '').join('');
  return `<svg class="ic-svg" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Прирост потребительских цен за месяц, ${rows[0].month} — ${rows[rows.length - 1].month}">
    ${grid}${bars}
    <line class="im-zero" x1="${P.l}" y1="${zero.toFixed(1)}" x2="${W - P.r}" y2="${zero.toFixed(1)}"/>
    ${xlab}</svg>`;
}

/** Текстовая альтернатива графику — не дублирует tooltip, а заменяет его для
 *  скринридера и для тех, кто не наводит мышь. Обновляется вместе с режимом. */
function inflSummaryText(rows, target) {
  if (!rows.length) return 'Данных за выбранный период нет.';
  const last = rows[rows.length - 1];
  const prev = rows.length > 1 ? rows[rows.length - 2] : null;
  const dev = isNum(target) ? last.inflation_yoy - target : null;
  const dir = prev ? (last.inflation_yoy > prev.inflation_yoy ? 'выше' : (last.inflation_yoy < prev.inflation_yoy ? 'ниже' : 'на уровне')) : null;
  const parts = [`В ${inflMonthLabel(last.month, 'prep')} инфляция составила ${ru(last.inflation_yoy, 1)}% год к году`];
  if (isNum(target)) parts.push(`при цели Банка России ${ru(target, 1)}%`);
  let s = parts.join(' ') + '.';
  if (dev != null) s += ` Отклонение от цели: ${dev > 0 ? '+' : ''}${ru(dev, 1)} п.п.`;
  if (prev && dir) s += ` Это ${dir} значения за ${inflMonthLabel(prev.month, 'nom')} (${ru(prev.inflation_yoy, 1)}%).`;
  const vals = rows.map((r) => r.inflation_yoy).filter(isNum);
  s += ` За показанный период (${rows.length} мес.) инфляция изменялась от ${ru(Math.min(...vals), 1)}% до ${ru(Math.max(...vals), 1)}%.`;
  return s;
}

function inflMomSummary(rows) {
  if (!rows.length) return 'Официальный месячный ряд недоступен.';
  const last = rows[rows.length - 1];
  const prev = rows.length > 1 ? rows[rows.length - 2] : null;
  const neg = rows.filter((r) => r.mom_pct < 0).length;
  // после «В» нужен предложный падеж: «в июне», а не «в июня»
  let s = `В ${inflMonthLabel(last.month, 'prep')} цены изменились на ${last.mom_pct > 0 ? '+' : ''}${ru(last.mom_pct, 2)}% за месяц.`;
  if (prev) s += ` Месяцем ранее — ${prev.mom_pct > 0 ? '+' : ''}${ru(prev.mom_pct, 2)}%.`;
  const vals = rows.map((r) => r.mom_pct);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  s += ` За показанный период средний прирост ${avg > 0 ? '+' : ''}${ru(avg, 2)}% в месяц`;
  s += neg ? `, снижение цен было в ${neg} ${plural(neg, 'месяце', 'месяцах', 'месяцах')}.` : ', месяцев со снижением цен не было.';
  return s;
}

function inflMonthlyHTML(m) {
  const inf = m.inflation;
  const all = (inf.monthly || []).filter((r) => isNum(r.inflation_yoy));
  const range = INFL_RANGES.find((r) => r.id === INFL_RANGE) || INFL_RANGES[2];
  const rows = range.months ? all.slice(-range.months) : all;
  const target = isNum(inf.target) ? inf.target : null;
  const momAll = (inf.mom && inf.mom.rows) ? inf.mom.rows.filter((r) => isNum(r.mom_pct)) : [];
  const momRows = range.months ? momAll.slice(-range.months) : momAll.slice(-120);

  const btns = INFL_RANGES.map((r) => {
    const enough = !r.months || all.length >= Math.min(r.months, 12);
    return `<button type="button" class="pfx-rbtn infl-range${r.id === INFL_RANGE ? ' on' : ''}"
      data-infl-range="${r.id}" aria-pressed="${r.id === INFL_RANGE}"${enough ? '' : ' disabled'}>${esc(r.label)}</button>`;
  }).join('');

  return `
    <div class="infl-controls">
      <div class="pfx-rbtns" role="group" aria-label="Период графика">${btns}</div>
      <div class="pfx-rbtns" role="group" aria-label="Показатель">
        <button type="button" class="pfx-rbtn infl-metric-btn${INFL_METRIC === 'yoy' ? ' on' : ''}"
          data-infl-metric="yoy" aria-pressed="${INFL_METRIC === 'yoy'}">Год к году</button>
        <button type="button" class="pfx-rbtn infl-metric-btn${INFL_METRIC === 'mom' ? ' on' : ''}"
          data-infl-metric="mom" aria-pressed="${INFL_METRIC === 'mom'}"${momRows.length ? '' : ' disabled'}>За месяц</button>
      </div>
    </div>
    <div class="infl-chart">${INFL_METRIC === 'mom' ? inflMomSVG(momRows) : inflChartSVG(rows, target)}</div>
    <p class="infl-summary" role="status">${esc(INFL_METRIC === 'mom' ? inflMomSummary(momRows) : inflSummaryText(rows, target))}</p>
    <details class="infl-table"><summary>Таблица значений (${rows.length} мес.)</summary>
      <div class="ef-excl-wrap"><table class="pfx-tbl"><thead><tr>
        <th class="left">Месяц</th><th>Инфляция, % г/г</th><th>Цель, %</th><th>Отклонение, п.п.</th></tr></thead><tbody>
        ${[...rows].reverse().slice(0, 60).map((r) => `<tr><td class="left">${esc(inflMonthLabel(r.month))}</td>
          <td class="tnum">${ru(r.inflation_yoy, 2)}</td>
          <td class="tnum">${isNum(r.target) ? ru(r.target, 1) : mdash}</td>
          <td class="tnum">${isNum(r.target) ? (r.inflation_yoy > r.target ? '+' : '') + ru(r.inflation_yoy - r.target, 2) : mdash}</td></tr>`).join('')}
      </tbody></table></div></details>
    <div class="pfx-note muted">Год к году — таблица Банка России «Инфляция и ключевая ставка».
      За месяц — официальный ряд <b>Росстата</b> (индекс к концу предыдущего месяца, переведён в прирост:
      106,2 → +6,2%), ${momAll.length} наблюдений с ${esc(momAll.length ? momAll[0].month : '—')}.
      Цель ЦБ показана только в режиме «год к году»: она относится к годовой инфляции, а не к месячному приросту.
      ${inf.mom_error ? `<b>Месячный ряд не обновился:</b> ${esc(String(inf.mom_error))} — показаны последние валидные данные.` : ''}</div>`;
}

/** Ожидания населения против официального ИПЦ.
 *  Три ряда сознательно на одном графике: разрыв между тем, что люди ОЩУЩАЮТ, и тем,
 *  что показывает индекс, — самостоятельный факт. Именно на ожидания ссылается ЦБ,
 *  объясняя жёсткость ставки, поэтому 6% ИПЦ и 15% ощущаемой инфляции надо видеть рядом. */
function inflExpectSVG(exp, perc, cpi) {
  if (!exp || exp.length < 3) return '';
  const W = 720, H = 340, P = { l: 44, r: 14, t: 16, b: 34 };
  const months = exp.map((p) => p.month);
  const byMonth = (arr) => Object.fromEntries((arr || []).map((p) => [p.month, p.value]));
  const pm = byMonth(perc), cm = byMonth(cpi);
  const all = exp.map((p) => p.value)
    .concat(months.map((mo) => pm[mo]).filter(isNum))
    .concat(months.map((mo) => cm[mo]).filter(isNum));
  const lo = 0, hi = Math.ceil(Math.max(...all) + 2);
  const X = (i) => P.l + (i / (months.length - 1)) * (W - P.l - P.r);
  const Y = (v) => H - P.b - ((v - lo) / ((hi - lo) || 1)) * (H - P.t - P.b);
  // пропуск в ряду разрывает линию, а не соединяется прямой через дыру
  const line = (vals, cls) => {
    let d = '', open = false;
    vals.forEach((v, i) => {
      if (!isNum(v)) { open = false; return; }
      d += `${open ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`; open = true;
    });
    return d ? `<path class="${cls}" d="${d}"/>` : '';
  };
  const step = Math.max(2, Math.ceil((hi - lo) / 6));
  let grid = '';
  for (let v = lo; v <= hi; v += step) {
    grid += `<line class="ic-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>` +
      `<text class="ic-ax" x="${P.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v, 0)}</text>`;
  }
  const every = Math.max(1, Math.round(months.length / 6));
  const xlab = months.map((mo, i) => (i % every === 0 || i === months.length - 1)
    ? `<text class="ic-ax" x="${X(i).toFixed(1)}" y="${H - 12}" text-anchor="middle">${esc(mo)}</text>` : '').join('');
  const hits = months.map((mo, i) => `<circle class="ic-hit" cx="${X(i).toFixed(1)}" cy="${Y(exp[i].value).toFixed(1)}" r="8"><title>${esc(inflMonthLabel(mo))}: ожидают ${ru(exp[i].value, 1)}%${isNum(pm[mo]) ? `, наблюдают ${ru(pm[mo], 1)}%` : ''}${isNum(cm[mo]) ? `, официальный ИПЦ ${ru(cm[mo], 1)}%` : ''} · опрос ФОМ по заказу Банка России</title></circle>`).join('');
  return `<svg class="ic-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Ожидаемая и наблюдаемая населением инфляция против официального ИПЦ, ${months[0]} — ${months[months.length - 1]}">
    ${grid}
    ${line(months.map((mo) => cm[mo]), 'ie-cpi')}
    ${line(months.map((mo) => pm[mo]), 'ie-perc')}
    ${line(exp.map((p) => p.value), 'ie-exp')}
    ${hits}${xlab}
  </svg>`;
}

function inflExpectHTML(m) {
  const e = m.inflation && m.inflation.expectations;
  if (!e || !e.expected || !e.expected.length) {
    const why = (m.inflation && m.inflation.expectations_error) || 'источник не ответил';
    return `<div class="infl-empty"><p><b>Ожидания населения временно недоступны.</b></p>
      <p class="muted">Причина: ${esc(String(why))}. Прошлые данные не затирались; ряд вернётся автоматически.</p></div>`;
  }
  const cpi = (m.inflation.monthly || []).map((r) => ({ month: r.month, value: r.inflation_yoy }));
  const exp = e.expected.slice(Math.max(0, e.expected.length - 72));   // последние 6 лет
  const gap = isNum(m.inflation.latest_yoy) ? e.latest_expected.value - m.inflation.latest_yoy : null;
  const kpi = (l, v, n) => `<div class="infl-kpi"><span>${esc(l)}</span><b>${v}</b><em>${esc(n)}</em></div>`;
  return `
    <div class="infl-kpis">
      ${kpi('Ожидают через год', `${ru(e.latest_expected.value, 1)}%`, `медиана опроса, ${inflMonthLabel(e.latest_expected.month, 'nom')}`)}
      ${kpi('Наблюдают за год', `${ru(e.latest_perceived.value, 1)}%`, 'как люди ощущают рост цен')}
      ${kpi('Разрыв с ИПЦ', gap == null ? '—' : `${gap > 0 ? '+' : ''}${ru(gap, 1)} п.п.`, 'ожидания минус официальный ИПЦ')}
    </div>
    <div class="infl-chart">${inflExpectSVG(exp, e.perceived, cpi)}</div>
    <div class="mc-legend">
      <span class="ie-lg ie-lg-exp">ожидаемая</span>
      <span class="ie-lg ie-lg-perc">наблюдаемая</span>
      <span class="ie-lg ie-lg-cpi">официальный ИПЦ</span>
    </div>
    <p class="infl-summary" role="status">В ${esc(inflMonthLabel(e.latest_expected.month, 'prep'))} население ожидало инфляцию ${ru(e.latest_expected.value, 1)}% на год вперёд и оценивало прошедший год в ${ru(e.latest_perceived.value, 1)}%${gap == null ? '.' : `, тогда как официальный ИПЦ составил ${ru(m.inflation.latest_yoy, 1)}% — разрыв ${ru(Math.abs(gap), 1)} п.п.`}</p>
    <div class="pfx-note muted">${esc(e.note || '')} Источник — <b>Банк России</b>, ежемесячный опрос (файл ${esc((e.source_file || '').split('/').pop())}). Это не альтернативный замер инфляции: на ожидания Банк России прямо ссылается в решениях по ключевой ставке, поэтому разрыв с ИПЦ объясняет жёсткость политики лучше, чем сам индекс.</div>`;
}

function renderInflDialog() {
  const body = document.getElementById('infl-body');
  if (!body) return;
  const m = MACRO_CBR;
  if (!m || m.failed || !m.inflation) {
    body.innerHTML = `<div class="pfx-note">${NA}: макроданные Банка России не загружены.</div>`;
    return;
  }
  const tabs = [['monthly', 'Месячная'], ['expect', 'Ожидания населения']].map(([id, label]) =>
    `<button type="button" class="pfx-rbtn infl-tab${id === INFL_TAB ? ' on' : ''}" data-infl-tab="${id}"
      role="tab" aria-selected="${id === INFL_TAB}">${label}</button>`).join('');
  const inf = m.inflation;
  const kpi = (label, value, note) =>
    `<div class="infl-kpi"><span>${esc(label)}</span><b>${value}</b><em>${esc(note)}</em></div>`;

  body.innerHTML = `
    <div class="infl-kpis">
      ${kpi('Инфляция, г/г', isNum(inf.latest_yoy) ? `${ru(inf.latest_yoy, 1)}%` : '—', `за ${inflMonthLabel(inf.latest_month, 'nom')}`)}
      ${kpi('Цель Банка России', isNum(inf.target) ? `${ru(inf.target, 1)}%` : '—', 'годовая инфляция')}
      ${kpi('Отклонение от цели', isNum(inf.above_target) ? `${inf.above_target > 0 ? '+' : ''}${ru(inf.above_target, 1)} п.п.` : '—', 'факт минус цель')}
      ${kpi('Ключевая ставка', isNum(m.key_rate && m.key_rate.current) ? `${ru(m.key_rate.current, 2)}%` : '—', `на ${esc((m.key_rate || {}).asof || '')}`)}
    </div>
    <div class="pfx-rbtns infl-tabs" role="tablist" aria-label="Периодичность данных">${tabs}</div>
    <div id="infl-tabbody">${INFL_TAB === 'expect' ? inflExpectHTML(m) : inflMonthlyHTML(m)}</div>
    <div class="pfx-note muted">Источник — <b>Банк России</b> (инфляция г/г, цель, ключевая ставка) и <b>Росстат</b> (месячный ИПЦ). Ничего не интерполируется, обновляется автоматически. Не ИИР.</div>`;
}

function closeInflDialog() {
  const dlg = document.getElementById('infl-dialog');
  const card = document.getElementById('infl-card');
  if (card) card.setAttribute('aria-expanded', 'false');
  if (!dlg) return;
  if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open');
  // фокус возвращает сам нативный dialog; подстраховываемся, если showModal недоступен
  if (card && document.activeElement === document.body) card.focus();
}

function openInflDialog() {
  const dlg = document.getElementById('infl-dialog');
  const card = document.getElementById('infl-card');
  if (!dlg) return;
  renderInflDialog();
  dlg._returnFocus = document.activeElement;            // фокус вернём на карточку
  if (card) card.setAttribute('aria-expanded', 'true');
  if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
}

// ── P/E рынка по последней годовой прибыли (site/market_pe_current.json, генерит CI) ──
function loadMarketPE(cb) {
  if (MARKET_PE) { if (cb) cb(); return; }
  fetch(dataURL('market_pe_current.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (!j || j.metric !== 'aggregate_pe_imoex_basket') throw new Error('неподдерживаемый контракт market_pe');
      MARKET_PE = j; if (cb) cb();
    })
    .catch((e) => { console.error('[market-pe] не загрузился:', e); MARKET_PE = { failed: true }; if (cb) cb(); });
}

function marketPeCovPct(x) { return isNum(x) ? Math.round(x * 100) + '%' : '—'; }

function marketPeReconTable(rows) {
  if (!Array.isArray(rows) || !rows.length) return '';
  const rub = (v, div, suf) => (isNum(v) ? ru(v / div, suf === ' трлн' ? 2 : 0) + suf : '<span class="muted">н/д</span>');
  const body = rows.map((r) => {
    const inc = r.included ? '<span class="mpe-in">включён</span>' : '<span class="mpe-out">исключён</span>';
    return `<tr class="${r.included ? '' : 'mpe-r-out'}">
      <td class="left"><b>${esc(r.ticker)}</b></td>
      <td class="tnum">${isNum(r.weight_pct) ? ru(r.weight_pct, 2) + '%' : '—'}</td>
      <td class="tnum">${r.fy ?? '—'}</td>
      <td class="tnum">${rub(r.net_income_rub, 1e9, ' млрд')}</td>
      <td class="left">${r.accounting_standard ? esc(r.accounting_standard) : '<span class="muted">н/д</span>'}</td>
      <td class="left">${r.source ? esc(r.source) : '<span class="muted">н/д</span>'}</td>
      <td class="tnum">${rub(r.market_cap_rub, 1e12, ' трлн')}</td>
      <td class="left">${inc}</td>
      <td class="left mpe-reason">${esc(r.reason || '')}</td>
    </tr>`;
  }).join('');
  return `<div class="mpe-recon-wrap"><table class="mpe-recon"><thead><tr>
    <th class="left">Тикер</th><th>Вес</th><th>Год</th><th>Прибыль</th><th class="left">Стандарт</th>
    <th class="left">Источник</th><th>Капит.</th><th class="left">Статус</th><th class="left">Причина</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function marketPeHTML(d) {
  const dt = (s) => (/^\d{4}-\d{2}-\d{2}/.test(String(s || '')) ? sawDate(String(s).slice(0, 10)) : '—');
  const cov = d.coverage || {};
  const ok = d.status === 'ok' && isNum(d.value) && d.value > 0;
  const blocking = Array.isArray(d.blocking_reasons) ? d.blocking_reasons : [];

  // Level 1: значение — ТОЛЬКО когда контракт качества пройден; иначе честное «недоступно»
  const valueBlock = ok
    ? `<div class="mpe-values">
        <div class="mpe-main"><b class="mpe-num tnum">${ru(d.value, 1)}<span>×</span></b><span class="mpe-lbl">цена/прибыль</span></div>
        <div class="mpe-yield"><b class="tnum">${ru(100 / d.value, 1)}%</b><span>доходность по прибыли (1/PE)</span></div>
      </div>`
    : `<div class="mpe-unavailable">
        <b>${esc(d.unavailable_message || 'Расчёт временно недоступен: проводится проверка качества финансовых данных')}</b>
        <span>Значение не публикуется, пока крупнейшие эмитенты не пройдут контракт прибыли (IFRS, прибыль акционерам материнской компании, полный год).</span>
      </div>`;

  const blockList = blocking.length
    ? `<div class="mpe-blocklist"><span class="mpe-sub-h">Не прошли проверку (вес &gt; 2%)</span>
        <ul>${blocking.slice(0, 8).map((b) => `<li><b>${esc(b.ticker)}</b> <span class="mpe-w">${isNum(b.weight_pct) ? ru(b.weight_pct, 1) + '%' : ''}</span> — ${esc(b.reason || '')}</li>`).join('')}</ul></div>`
    : '';

  // Мягкий режим (earnings_verified=false): значение опубликовано, но это ОЦЕНОЧНЫЙ ориентир —
  // максимально явные пометки прямо под числом (покрытие, исключённые крупные имена, несверенность).
  const excludedTk = (d.excluded_material || []).map((r) => esc(r.ticker)).join(', ');
  const verifiedCov = (d.coverage || {}).earnings_coverage;
  const caveat = (ok && d.earnings_verified === false)
    ? `<div class="mpe-caveat">
         <b>⚠ Оценочный ориентир.</b>
         Покрытие <b>${marketPeCovPct(d.included_coverage)}</b> капитализации корзины (${d.included_n || '—'} эмитентов);
         из них <b>${marketPeCovPct(verifiedCov)}</b> — прибыль <b>сверена вручную с офиц. МСФО-отчётностью</b> (attributable to parent, FY2025),
         остаток — SmartLab, не сверено.
         ${excludedTk ? `Исключены (аномалия / убыток без сверки / «скорр.» прибыль): <b>${excludedTk}</b>.` : ''}
       </div>`
    : '';

  const noteText = (ok && d.earnings_verified === false)
    ? (d.note || 'Мягкий режим: значение по подмножеству корзины на несверённой прибыли SmartLab; оценочный ориентир, не точный P/E.')
    : 'Не официальный P/E Индекса МосБиржи: расчёт по полной капитализации эмитентов (данные реестра MOEX по всем классам акций), тогда как IMOEX учитывает free-float. Прибыль — последняя годовая по МСФО, относящаяся к акционерам материнской компании, включая убытки.';

  return `
    <div class="mpe-head">
      <div class="mpe-copy">
        <span class="mpe-eyebrow">Оценка рынка по прибыли</span>
        <div class="mpe-title">Агрегированный P/E компаний текущей корзины IMOEX</div>
        <p class="mpe-note">${esc(noteText)}</p>
      </div>
      ${valueBlock}
    </div>
    ${caveat}
    <details class="mpe-details">
      <summary>Проверка данных, покрытие и сверка по эмитентам</summary>
      <div class="mpe-grid">
        <div><span>Цены рынка</span><b>${dt(d.market_date)}</b></div>
        <div><span>Отчётность</span><b>${dt(d.fundamentals_as_of)}</b></div>
        <div><span>Покрытие ценами</span><b>${marketPeCovPct(cov.price_coverage)}</b><small>${esc(cov.price_coverage_n || '')}</small></div>
        <div><span>Строго сверено (контракт)</span><b>${marketPeCovPct(cov.earnings_coverage)}</b><small>${esc(cov.earnings_coverage_n || '')}</small></div>
        <div><span>Включено в расчёт (мягко)</span><b>${marketPeCovPct(d.included_coverage)}</b><small>${d.included_n || '—'} эмит.${d.earnings_verified === false ? ' · не сверено' : ''}</small></div>
        <div><span>Universe</span><b>${esc(d.universe || '—')}</b></div>
      </div>
      ${blockList}
      <div class="mpe-recon-h"><span class="mpe-sub-h">Сверка по эмитентам (reconciliation)</span></div>
      ${marketPeReconTable(d.reconciliation)}
      <p class="mpe-foot muted">${esc(d.note || '')} Обновляется каждый торговый день. Источники: MOEX ISS, SmartLab. Не индивидуальная инвестиционная рекомендация.</p>
    </details>`;
}

// ── История P/E: дорого ли сейчас ОТНОСИТЕЛЬНО СВОЕЙ нормы ──────────────────
// Одно число «P/E 4,4» не отвечает на вопрос, ради которого его смотрят. Ответ даёт
// положение внутри собственного исторического распределения рынка.
const MPE_RANGES = [
  { id: '3y', label: '3 года', months: 36 },
  { id: '5y', label: '5 лет', months: 60 },
  { id: '10y', label: '10 лет', months: 120 },
  { id: 'all', label: 'Всё', months: null },
];

function loadMarketPeHistory(cb) {
  if (MARKET_PE_HIST) { if (cb) cb(); return; }
  fetch(dataURL('market_pe_history.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => {
      if (!j || !Array.isArray(j.history)) throw new Error('неподдерживаемый контракт market_pe_history');
      MARKET_PE_HIST = j; if (cb) cb();
    })
    .catch((e) => { console.error('[market-pe-hist] не загрузился:', e); MARKET_PE_HIST = { failed: true }; if (cb) cb(); });
}

// Три знаменателя оценки. Пользователь не выбирает «правильный» — каждый отвечает на свой
// вопрос: что заработали, сколько зарабатывают в среднем по циклу, сколько пришло деньгами.
const MPE_METRICS = [
  {
    id: 'reported', label: 'Прибыль', unit: '×', ratio: 'P/E',
    what: 'Опубликованная годовая прибыль без корректировок — вместе с разовыми событиями: '
      + 'переоценкой валюты, списаниями, продажей активов.',
  },
  {
    id: 'normalized', label: 'Прибыль за цикл', unit: '×', ratio: 'P/E нормализованный',
    what: 'Средняя за цикл рентабельность, применённая к текущей выручке. Один удачный или '
      + 'провальный год перестаёт перекашивать оценку: в апреле 2021 обычный P/E доходил до 16 '
      + 'только потому, что в знаменателе стояла ковидная прибыль 2020 года.',
  },
  {
    id: 'ocf', label: 'Денежный поток', unit: '×', ratio: 'P/OCF',
    what: 'Операционный денежный поток вместо прибыли: в нём нет бумажных переоценок и списаний, '
      + 'только реально пришедшие деньги.',
  },
];

function mpeMetric() {
  return MPE_METRICS.find((m) => m.id === MPE_METRIC) || MPE_METRICS[0];
}

/** Точка выбранной метрики в едином виде. Старый контракт (верхнеуровневый pe) поддержан:
 *  если на сайте лежит файл предыдущей версии, режим «Прибыль» продолжает работать. */
function mpePoint(p, id) {
  const m = (p.metrics || {})[id];
  if (m) return { month: p.month, value: m.value, status: m.quality_status,
                  coverage_pct: m.coverage_pct, verified_coverage_pct: m.verified_coverage_pct,
                  constituents_used: m.constituents_used, last_fiscal_year: p.last_fiscal_year };
  if (id !== 'reported') return { month: p.month, value: null, status: 'unavailable' };
  return { month: p.month, value: p.pe, status: p.quality_status, coverage_pct: p.coverage_pct,
           verified_coverage_pct: p.verified_coverage_pct, constituents_used: p.constituents_used,
           last_fiscal_year: p.last_fiscal_year };
}

/** Точки, пригодные для сравнения: без дефектных знаменателей и без месяцев,
 *  где корзина покрыта слишком слабо, чтобы число что-то значило. */
function mpeUsable(d, id) {
  return (d.history || []).map((p) => mpePoint(p, id || MPE_METRIC))
    .filter((p) => isNum(p.value) && p.status !== 'insufficient_coverage' && p.status !== 'unavailable');
}

/** Период доступен, если история покрывает хотя бы 60% его длины: показывать «10 лет»
 *  на двух годах данных — врать подписью. «Всё» доступно всегда. */
function mpeRangeEnabled(d, conf) {
  return !conf.months || mpeUsable(d).length >= conf.months * 0.6;
}

/** Выбранный период может оказаться недоступным на короткой истории — тогда откатываемся
 *  к самому длинному доступному. Иначе активная кнопка выходила бы disabled. */
function mpeActiveRange(d) {
  const chosen = MPE_RANGES.find((r) => r.id === MPE_RANGE);
  if (chosen && mpeRangeEnabled(d, chosen)) return chosen;
  const ok = MPE_RANGES.filter((r) => r.months && mpeRangeEnabled(d, r));
  return ok.length ? ok[ok.length - 1] : MPE_RANGES[MPE_RANGES.length - 1];
}

function mpeWindowRows(d) {
  const rows = mpeUsable(d);
  const conf = mpeActiveRange(d);
  if (!conf.months || rows.length <= conf.months) return rows;
  return rows.slice(-conf.months);
}

/** Вердикт по положению в собственном распределении. Формулировки намеренно
 *  сравнительные («дороже, чем в N% месяцев»), а не оценочные («переоценён»): у нас
 *  нет оснований утверждать справедливую стоимость рынка, есть только его история. */
function mpeVerdict(pct) {
  if (!isNum(pct)) return null;
  if (pct >= 80) return { cls: 'hi', text: 'дороже обычного' };
  if (pct >= 60) return { cls: 'mid-hi', text: 'выше нормы' };
  if (pct > 40) return { cls: 'mid', text: 'около нормы' };
  if (pct > 20) return { cls: 'mid-lo', text: 'ниже нормы' };
  return { cls: 'lo', text: 'дешевле обычного' };
}

function mpeStats(rows) {
  if (rows.length < 3) return null;
  const v = rows.map((r) => r.value).sort((a, b) => a - b);
  const q = (p) => {
    const pos = (v.length - 1) * p, lo = Math.floor(pos), hi = Math.min(lo + 1, v.length - 1);
    return v[lo] + (v[hi] - v[lo]) * (pos - lo);
  };
  const cur = rows[rows.length - 1].value;
  return {
    n: v.length, median: q(0.5), p25: q(0.25), p75: q(0.75), min: v[0], max: v[v.length - 1],
    percentile: (100 * v.filter((x) => x <= cur).length) / v.length,
    enough: rows.length >= 36,
  };
}

function mpeMonthLabel(m) {
  const MM = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const i = parseInt(String(m).slice(5, 7), 10) - 1;
  return `${MM[i] || '?'} ${String(m).slice(0, 4)}`;
}

function mpeHistSVG(rows, st) {
  if (rows.length < 2 || !st) return '';
  const W = 720, H = 300, P = { l: 42, r: 14, t: 14, b: 30 };
  const pad = (st.max - st.min) * 0.12 || 0.5;
  const lo = Math.max(0, st.min - pad), hi = st.max + pad;
  const X = (i) => P.l + (i / (rows.length - 1)) * (W - P.l - P.r);
  const Y = (v) => H - P.b - ((v - lo) / ((hi - lo) || 1)) * (H - P.t - P.b);

  let grid = '';
  for (let k = 0; k <= 4; k++) {
    const v = lo + ((hi - lo) / 4) * k;
    grid += `<line class="ic-grid" x1="${P.l}" y1="${Y(v).toFixed(1)}" x2="${W - P.r}" y2="${Y(v).toFixed(1)}"/>` +
      `<text class="ic-ax" x="${P.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${ru(v, 1)}</text>`;
  }
  // Полоса «обычных» значений (25–75 перцентиль) — это и есть визуальная норма рынка
  const band = `<rect class="mpe-band" x="${P.l}" y="${Y(st.p75).toFixed(1)}" width="${(W - P.l - P.r).toFixed(1)}"
    height="${Math.max(1, Y(st.p25) - Y(st.p75)).toFixed(1)}"/>`;
  // Подпись медианы — СЛЕВА: справа всегда стоит маркер текущего значения, и они наложились бы.
  const med = `<line class="mpe-med" x1="${P.l}" y1="${Y(st.median).toFixed(1)}" x2="${W - P.r}" y2="${Y(st.median).toFixed(1)}"/>
    <text class="mpe-medlab" x="${P.l + 4}" y="${(Y(st.median) - 5).toFixed(1)}">медиана ${ru(st.median, 1)}×</text>`;

  const path = rows.map((r, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(r.value).toFixed(1)}`).join('');
  const last = rows[rows.length - 1];
  // Подписи оси: регулярная сетка + обязательно последний месяц. Ближайший к нему регулярный
  // тик убираем, иначе две подписи налезают друг на друга. Крайние якорим внутрь — по центру
  // они вылезают за viewBox и обрезаются.
  const every = Math.max(1, Math.round(rows.length / 6));
  const lastIdx = rows.length - 1;
  const ticks = new Set([lastIdx]);
  for (let i = 0; i <= lastIdx; i += every) if (lastIdx - i >= every * 0.6) ticks.add(i);
  const xlab = [...ticks].sort((a, b) => a - b).map((i) => {
    const anchor = i === lastIdx ? 'end' : (i === 0 ? 'start' : 'middle');
    return `<text class="ic-ax" x="${X(i).toFixed(1)}" y="${H - 10}" text-anchor="${anchor}">${esc(mpeMonthLabel(rows[i].month))}</text>`;
  }).join('');
  const hits = rows.map((r, i) =>
    `<circle class="ic-hit" cx="${X(i).toFixed(1)}" cy="${Y(r.value).toFixed(1)}" r="8"><title>${esc(mpeMonthLabel(r.month))}: ${esc(mpeMetric().ratio)} ${ru(r.value, 2)}× · ${r.constituents_used || '—'} эмитентов · покрытие ${ru(r.coverage_pct, 0)}%</title></circle>`).join('');

  return `<svg class="ic-svg" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="P/E рынка помесячно с ${mpeMonthLabel(rows[0].month)} по ${mpeMonthLabel(last.month)}, медиана ${ru(st.median, 1)}">
    ${band}${grid}${med}
    <path class="mpe-line" d="${path}"/>
    <circle class="mpe-last" cx="${X(rows.length - 1).toFixed(1)}" cy="${Y(last.value).toFixed(1)}" r="5"/>
    ${hits}${xlab}</svg>`;
}

function mpeDecompHTML(dec) {
  if (!dec || !isNum(dec.pe_change_pct)) return '';
  const sg = (v) => (v > 0 ? '+' : '') + ru(v, 1) + '%';
  const dir = dec.pe_change_pct > 0 ? 'вырос' : 'снизился';
  // P/E = Капитализация ÷ Прибыль, поэтому его движение раскладывается ровно на две причины.
  const why = Math.abs(dec.cap_change_pct) >= Math.abs(dec.earnings_change_pct)
    ? 'в основном из-за движения цен'
    : 'в основном из-за изменения прибыли';
  return `<div class="mpe-decomp">
    <span class="mpe-sub-h">За год P/E ${dir} на ${sg(dec.pe_change_pct)} — ${why}</span>
    <div class="mpe-decomp-row">
      <div><span>Капитализация</span><b class="${dec.cap_change_pct >= 0 ? 'pos' : 'neg'}">${sg(dec.cap_change_pct)}</b></div>
      <div><span>Прибыль</span><b class="${dec.earnings_change_pct >= 0 ? 'pos' : 'neg'}">${sg(dec.earnings_change_pct)}</b></div>
      <div><span>Период</span><b>${esc(mpeMonthLabel(dec.from))} → ${esc(mpeMonthLabel(dec.to))}</b></div>
    </div>
    ${dec.comparable_basket ? '' : '<p class="mpe-foot muted">Состав корзины за год менялся — сравнение приблизительное.</p>'}
  </div>`;
}

/** Кнопки выбора знаменателя. Вынесены отдельно, потому что рисуются даже когда у
 *  выбранного режима не хватает данных — иначе блок исчезает целиком вместе с
 *  переключателем, и вернуться к рабочему режиму пользователю нечем. */
function mpeModeButtons(d, activeId) {
  return MPE_METRICS.map((m) => {
    const on = m.id === activeId;
    const has = mpeUsable(d, m.id).length >= 12;
    return `<button type="button" class="pfx-rbtn mpe-mode${on ? ' on' : ''}"
      data-mpe-metric="${m.id}" aria-pressed="${on}"${has ? '' : ' disabled'}>${esc(m.label)}</button>`;
  }).join('');
}

function mpeHistoryHTML(d) {
  const rows = mpeWindowRows(d);
  const st = mpeStats(rows);
  if (!st) {
    if (!mpeUsable(d, 'reported').length) return '';      // истории нет вовсе — блока не будет
    return `<div class="mpe-hist">
      <div class="mpe-hist-head"><span class="mpe-sub-h">Оценка рынка в динамике</span></div>
      <div class="mpe-modes" role="group" aria-label="Знаменатель оценки">${mpeModeButtons(d, MPE_METRIC)}</div>
      <div class="mpe-verdict mid"><b>нет ряда для этого знаменателя</b>
        <span>данных в фундамент-слое не хватает — выберите другой</span></div>
    </div>`;
  }
  const cur = d.current || {};
  const v = mpeVerdict(st.percentile);
  const active = mpeActiveRange(d);
  const btns = MPE_RANGES.map((r) => {
    const on = r.id === active.id;
    return `<button type="button" class="pfx-rbtn mpe-range${on ? ' on' : ''}"
      data-mpe-range="${r.id}" aria-pressed="${on}"${mpeRangeEnabled(d, r) ? '' : ' disabled'}>${esc(r.label)}</button>`;
  }).join('');

  const vsMed = 100 * (rows[rows.length - 1].value / st.median - 1);
  // Перцентиль на коротком ряду — шум: показываем его только когда точек хватает.
  // Формулировка разворачивается по направлению: «дешевле, чем в 12% месяцев» звучало бы
  // как редкая дороговизна, хотя означает ровно противоположное.
  const rel = st.percentile >= 50
    ? `дороже, чем в ${ru(st.percentile, 0)}% месяцев выборки`
    : `дешевле, чем в ${ru(100 - st.percentile, 0)}% месяцев выборки`;
  const verdictLine = st.enough && v
    ? `<div class="mpe-verdict ${v.cls}"><b>${esc(v.text)}</b>
         <span>${rel}; ${vsMed >= 0 ? 'выше' : 'ниже'} медианы на ${ru(Math.abs(vsMed), 0)}%</span></div>`
    : `<div class="mpe-verdict mid"><b>сравнивать рано</b>
         <span>в выборке ${ru(st.n, 0)} мес. — для вывода о «дорого/дёшево» нужно хотя бы 36</span></div>`;

  // Премию считаем от ВЫБРАННОЙ метрики, а не от прибыли всегда: иначе в режиме денежного
  // потока под графиком стояла бы доходность из другого расчёта.
  const now = rows[rows.length - 1];
  const nowValue = now.value;
  const yieldPct = 100 / nowValue;
  const sp = isNum(cur.risk_free_pct) ? yieldPct - cur.risk_free_pct : null;
  const spread = isNum(sp)
    ? `<div><span>Премия к безриск. ставке</span><b class="${sp >= 0 ? 'pos' : 'neg'}">${sp > 0 ? '+' : ''}${ru(sp, 1)} п.п.</b><small>доходность ${ru(yieldPct, 1)}% против ${ru(cur.risk_free_pct, 1)}%</small></div>`
    : '';

  const mt = mpeMetric();
  const modes = mpeModeButtons(d, mt.id);

  return `<div class="mpe-hist">
    <div class="mpe-hist-head">
      <span class="mpe-sub-h">Оценка рынка в динамике</span>
      <div class="pfx-ranges" role="group" aria-label="Период истории">${btns}</div>
    </div>
    <div class="mpe-modes" role="group" aria-label="Знаменатель оценки">${modes}</div>
    <p class="mpe-mode-note"><b>${esc(mt.ratio)}.</b> ${esc(mt.what)}</p>
    ${verdictLine}
    <div class="mpe-chart-wrap">${mpeHistSVG(rows, st)}</div>
    <div class="mpe-grid mpe-hist-grid">
      <div><span>Сейчас</span><b>${ru(now.value, 1)}×</b><small>${esc(mpeMonthLabel(now.month))}</small></div>
      <div><span>Медиана периода</span><b>${ru(st.median, 1)}×</b><small>${ru(st.n, 0)} мес.</small></div>
      <div><span>Обычный коридор</span><b>${ru(st.p25, 1)}–${ru(st.p75, 1)}×</b><small>25–75 перцентиль</small></div>
      <div><span>Диапазон</span><b>${ru(st.min, 1)}–${ru(st.max, 1)}×</b><small>мин.–макс.</small></div>
      ${spread}
    </div>
    ${mt.id === 'reported' ? mpeDecompHTML(d.decomposition) : ''}
    <details class="mpe-details mpe-hist-details">
      <summary>Как это посчитано и чего этому ряду не хватает</summary>
      <div class="mpe-grid">
        <div><span>Состав корзины</span><b>исторический</b><small>на каждую дату свой</small></div>
        <div><span>Капитализация</span><b>MOEX, point-in-time</b><small>с учётом допэмиссий и сплитов</small></div>
        <div><span>Покрытие знаменателем</span><b>${ru(now.coverage_pct, 0)}%</b><small>${now.constituents_used || '—'} эмитентов · ${
          now.verified_coverage_pct > 0
            ? `сверено ${ru(now.verified_coverage_pct, 0)}%`
            : 'сверки нет: вручную сверялась только прибыль'}</small></div>
        ${isNum(cur.priced_pct) ? `<div><span>Есть капитализация</span><b>${ru(cur.priced_pct, 0)}%</b><small>${cur.constituents_priced || '—'} из ${cur.constituents_total || '—'} бумаг</small></div>` : ''}
        <div><span>Отчётность</span><b>за ${now.last_fiscal_year || cur.last_fiscal_year || '—'} г.</b><small>годовая, не TTM</small></div>
      </div>
      <p class="mpe-foot muted"><b>Допущение о дате раскрытия.</b> ${esc((d.disclosure_assumption || {}).rule || '')} —
        ${esc((d.disclosure_assumption || {}).why || '')}. Это допущение, а не фактическая дата публикации отчётов.</p>
      <ul class="mpe-limits">${(d.limitations || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
    </details>
  </div>`;
}

function renderMarketPE() {
  const el = document.getElementById('market-pe-card');
  if (!el) return;
  if (!MARKET_PE) { el.innerHTML = '<div class="pulse-loading muted">Загрузка P/E рынка…</div>'; return; }
  if (MARKET_PE.failed) {
    el.innerHTML = '<div class="mpe-fallback"><b>P/E рынка временно недоступен.</b> Остальные индикаторы работают.</div>';
    return;
  }
  // История — отдельный файл: её отсутствие не должно ломать основную карточку
  const hist = (MARKET_PE_HIST && !MARKET_PE_HIST.failed) ? mpeHistoryHTML(MARKET_PE_HIST) : '';
  el.innerHTML = marketPeHTML(MARKET_PE) + hist;
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
  const rfr = ml && isNum(ml.rfr) ? (ml.rfr * 100).toFixed(1) + '%' : dash;
  // Режим считается как IMOEX vs SMA200 в build_forward_yield.py. Когда ISS недоступен
  // (в проде это случается: вечерний прогон 29.07 дал 91 таймаут подряд), поле приходит
  // null — и карточка показывала голое тире, будто индикатор просто пустой. Говорим прямо,
  // что источник не ответил, иначе выглядит как поломка сайта.
  const regime = ml && ml.regime
    ? esc(ml.regime)
    : `${dash}<em class="kpi-why">источник (MOEX ISS) не ответил в последнем прогоне</em>`;
  const stress = SAW_DATA ? marketStressFromSaw(SAW_DATA) : null;
  const volValue = stress ? fmtPct(stress.current_vol * 100, 1) : dash;
  // Показываем ИЗМЕНЕНИЕ за 20 торговых дней: без него 20-дневная волатильность выглядит
  // замершей (она и не должна дёргаться от одного отскока), и пользователь считал карточку
  // сломанной. Направление — то, что реально меняет поведение.
  let volNote = '';
  if (stress) {
    const parts = [`${stress.score}/100 · ${esc(stress.label)}`];
    if (isNum(stress.vol_change)) {
      const d = stress.vol_change * 100;
      const arrow = d > 0.2 ? '↑' : (d < -0.2 ? '↓' : '→');
      parts.push(`${arrow} ${d > 0 ? '+' : ''}${ru(d, 1)} п.п. за 20 дней`);
    }
    volNote = parts.join(' · ');
  }
  el.innerHTML = [
    kpiCard('Волатильность MCFTR 20d', volValue, stress ? `stress-card stress-${stress.tone}` : '', volNote),
    kpiCard('RFR (КБД 1Y)', rfr),
    kpiCard('Режим рынка', regime),
    // «Акций / облигаций» убрана: это инвентарный счётчик покрытия наших данных,
    // по нему инвестор не принимает ни одного решения.
  ].join('');
}

function loadMarketHistory(cb) {
  if (MARKET_HISTORY) { if (cb) cb(); return; }
  fetch(dataURL('market_history.json'))
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
    <span class="mic-identity">${instrumentAvatarHTML(item.id, item.description || item.name, item.type, 'md')}<span class="mic-name">${esc(item.name)}</span></span>
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

function marketUsesCloseLine(item, rows) {
  const sample = (rows && rows.length ? rows : (item.series || [])).slice(-260);
  if (!sample.length) return false;
  const flat = sample.filter((row) => {
    if (!isNum(row[1]) || !isNum(row[2]) || !isNum(row[3]) || !isNum(row[4])) return false;
    const tolerance = Math.max(1e-8, Math.abs(row[4]) * 1e-8);
    return Math.abs(row[1] - row[4]) <= tolerance
      && Math.abs(row[2] - row[4]) <= tolerance
      && Math.abs(row[3] - row[4]) <= tolerance;
  }).length;
  return flat / sample.length >= 0.8;
}

function marketOhlcHTML(item, row, closeOnly = false) {
  if (!row) return '';
  const fmt = (value) => marketNumber(value, item.decimals);
  if (closeOnly) {
    return `<b>${esc(sawDate(row[0]))}</b><span>Закрытие <strong>${fmt(row[4])}</strong></span><span class="market-chart-row-note">индексный ряд без внутридневного OHLC</span>`;
  }
  return `<b>${esc(sawDate(row[0]))}</b><span>O ${fmt(row[1])}</span><span>H ${fmt(row[2])}</span><span>L ${fmt(row[3])}</span><span>C ${fmt(row[4])}</span>`;
}

function drawMarketChart(item) {
  const element = document.getElementById('market-chart-canvas');
  if (!element || !window.LightweightCharts) return;
  if (MARKET_CHART) { MARKET_CHART.remove(); MARKET_CHART = null; }
  element.innerHTML = '';
  const LC = window.LightweightCharts;
  const rows = marketChartRows(item);
  const closeOnly = marketUsesCloseLine(item, rows);
  MARKET_CHART = LC.createChart(element, {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#5A6472', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 11 },
    localization: { locale: 'ru-RU', priceFormatter: (value) => marketNumber(value, item.decimals) },
    grid: { vertLines: { color: '#E9EDF3' }, horzLines: { color: '#E9EDF3' } },
    rightPriceScale: { borderColor: '#D8DEE8', scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: '#D8DEE8', timeVisible: false, rightOffset: 3, minBarSpacing: 3 },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
      vertLine: { color: '#98A2B3', style: LC.LineStyle.Dashed, labelBackgroundColor: '#344054' },
      horzLine: { color: '#98A2B3', style: LC.LineStyle.Dashed, labelBackgroundColor: '#344054' },
    },
  });
  let primary;
  if (closeOnly) {
    primary = MARKET_CHART.addAreaSeries({
      lineColor: '#147A5A', lineWidth: 3,
      topColor: 'rgba(20, 122, 90, 0.22)', bottomColor: 'rgba(20, 122, 90, 0.02)',
      priceLineVisible: true, priceLineColor: '#147A5A',
      crosshairMarkerVisible: true, crosshairMarkerRadius: 4,
    });
    primary.setData(rows.map((row) => ({ time: row[0], value: row[4] })));
  } else {
    primary = MARKET_CHART.addCandlestickSeries({
      upColor: '#16805E', downColor: '#B34A32',
      borderVisible: true, borderUpColor: '#116B4F', borderDownColor: '#963923',
      wickUpColor: '#116B4F', wickDownColor: '#963923', priceLineVisible: true,
    });
    primary.setData(rows.map((row) => ({ time: row[0], open: row[1], high: row[2], low: row[3], close: row[4] })));
  }
  const enabled = new Set(Array.from(document.querySelectorAll('#market-chart-overlays input:checked')).map((input) => input.value));
  const overlays = [
    ['sma20', 5, '#176B87'], ['sma50', 6, '#C58A14'], ['sma200', 7, '#59616E'],
  ];
  overlays.forEach(([key, index, color]) => {
    if (!enabled.has(key)) return;
    const line = MARKET_CHART.addLineSeries({ color, lineWidth: key === 'sma200' ? 3 : 2, priceLineVisible: false, lastValueVisible: false });
    line.setData(rows.filter((row) => isNum(row[index])).map((row) => ({ time: row[0], value: row[index] })));
  });
  const summary = item.summary || {};
  if (isNum(summary.low20)) primary.createPriceLine({ price: summary.low20, color: '#77A994', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '20d min' });
  if (isNum(summary.high20)) primary.createPriceLine({ price: summary.high20, color: '#C78B79', lineStyle: LC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '20d max' });
  MARKET_CHART.timeScale().fitContent();
  const ohlc = document.getElementById('market-chart-ohlc');
  if (ohlc) ohlc.innerHTML = marketOhlcHTML(item, rows[rows.length - 1], closeOnly);
  MARKET_CHART.subscribeCrosshairMove((param) => {
    if (!ohlc || !param || !param.time || !param.seriesData) return;
    const bar = param.seriesData.get(primary);
    if (!bar) return;
    const time = typeof param.time === 'string' ? param.time
      : `${param.time.year}-${String(param.time.month).padStart(2, '0')}-${String(param.time.day).padStart(2, '0')}`;
    const row = closeOnly
      ? [time, bar.value, bar.value, bar.value, bar.value]
      : [time, bar.open, bar.high, bar.low, bar.close];
    ohlc.innerHTML = marketOhlcHTML(item, row, closeOnly);
  });
}

function renderMarketChartDialog() {
  const item = marketInstrument(MARKET_CHART_STATE.id);
  if (!item) return;
  const s = item.summary || {};
  document.getElementById('market-chart-title').innerHTML = `${instrumentAvatarHTML(item.id, item.description || item.name, item.type, 'md')}<span>${esc(item.name)} · ${marketNumber(s.last, item.decimals)}</span>`;
  document.getElementById('market-chart-sub').innerHTML = `<span class="${(s.change_pct || 0) >= 0 ? 'up' : 'down'}">${marketChange(s.change_pct)}</span> · ${esc(item.description)} · ${esc(sawDate(item.data_last))}`;
  document.getElementById('market-chart-tabs').innerHTML = MARKET_HISTORY.instruments.map((row) =>
    `<button type="button" data-market-tab="${esc(row.id)}" class="${row.id === item.id ? 'active' : ''}">${instrumentAvatarHTML(row.id, row.description || row.name, row.type, 'xs')}${esc(row.name)}</button>`
  ).join('');
  const seriesMode = document.getElementById('market-chart-mode');
  if (seriesMode) seriesMode.textContent = marketUsesCloseLine(item) ? 'Линия закрытия' : 'Свечи OHLC';
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

// ── График цены+объёма в карточке акции (дневные OHLC MOEX ISS, загрузка по клику) ──
const STOCK_CHART_PERIODS = [['127', '6М'], ['252', '1Г'], ['756', '3Г'], ['0', 'Макс']];

function stockChartFromDate(days) {
  if (!days) return '2014-01-01';   // «Макс» — практический старт TQBR-истории
  return new Date(Date.now() - (days + 25) * 86400000).toISOString().slice(0, 10);
}

function stockPriceChartHTML(t) {
  return `<div class="detail-card stock-chart" data-sc-ticker="${esc(t.ticker)}">
    <div class="sc-top">
      <h4>${instrumentAvatarHTML(t.ticker, t.name, instrumentTypeHint(t), 'sm')}<span>Цена и объём торгов · ${esc(t.ticker)}</span></h4>
      <div class="sc-periods" role="tablist" aria-label="Период графика">${STOCK_CHART_PERIODS.map(([d, l], i) => `<button type="button" data-sc-days="${d}" class="${i === 1 ? 'active' : ''}" aria-pressed="${i === 1}">${l}</button>`).join('')}</div>
    </div>
    <div class="sc-ohlc tnum" aria-live="polite"></div>
    <div class="sc-canvas"><div class="sc-loading muted">Загрузка дневных котировок MOEX ISS…</div></div>
    <div class="sc-foot muted">Дневные OHLC и объём — MOEX ISS, доска TQBR. Не индивидуальная инвестиционная рекомендация.</div>
  </div>`;
}

// Пагинированная выборка дневной истории ISS (start=0,100,…). Отдаёт [date,o,h,l,c,vol].
function fetchStockOHLC(ticker, fromDate, cb) {
  const rows = [];
  const cols = 'TRADEDATE,OPEN,HIGH,LOW,CLOSE,VOLUME';
  const base = `https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/${encodeURIComponent(ticker)}.json`
    + `?iss.only=history&iss.meta=off&history.columns=${cols}&from=${fromDate}`;
  const step = (start) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12000);
    fetch(`${base}&start=${start}`, { signal: ctrl.signal, cache: 'no-store' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((j) => {
        clearTimeout(timer);
        const data = (j.history && j.history.data) || [];
        data.forEach((d) => { if (isNum(d[4])) rows.push(d); });
        if (data.length >= 100 && rows.length < 4000) step(start + data.length);
        else cb(null, rows);
      })
      .catch((e) => { clearTimeout(timer); cb(e, rows); });
  };
  step(0);
}

function stockOhlcReadout(ticker, r) {
  if (!r) return '';
  const n = (v) => (isNum(v) ? ru(v, 2) : '—');
  const up = isNum(r[4]) && isNum(r[1]) && r[4] >= r[1];
  const volM = isNum(r[5]) ? (r[5] >= 1e6 ? ru(r[5] / 1e6, 1) + ' млн' : ru(r[5] / 1e3, 0) + ' тыс') : '—';
  return `<span class="sc-date">${esc(sawDate(r[0]))}</span>
    <span>O ${n(r[1])}</span><span>H ${n(r[2])}</span><span>L ${n(r[3])}</span>
    <span class="${up ? 'sc-up' : 'sc-down'}">C ${n(r[4])}</span>
    <span class="sc-vol">V ${volM} шт</span>`;
}

function renderStockChartData(container, ticker, rows) {
  const canvas = container.querySelector('.sc-canvas');
  const ohlc = container.querySelector('.sc-ohlc');
  if (!canvas) return;
  if (!window.LightweightCharts) { canvas.innerHTML = '<div class="sc-loading muted">График недоступен.</div>'; return; }
  if (container._scChart) { try { container._scChart.remove(); } catch (_e) { /* noop */ } container._scChart = null; }
  canvas.innerHTML = '';
  const LC = window.LightweightCharts;
  const chart = LC.createChart(canvas, {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#5A6472', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 11 },
    localization: { locale: 'ru-RU', priceFormatter: (v) => ru(v, 2) },
    grid: { vertLines: { color: '#EEF1F6' }, horzLines: { color: '#EEF1F6' } },
    rightPriceScale: { borderColor: '#D8DEE8', scaleMargins: { top: 0.08, bottom: 0.28 } },
    timeScale: { borderColor: '#D8DEE8', timeVisible: false, rightOffset: 4, minBarSpacing: 2 },
    crosshair: { mode: LC.CrosshairMode.Normal,
      vertLine: { color: '#98A2B3', style: LC.LineStyle.Dashed, labelBackgroundColor: '#344054' },
      horzLine: { color: '#98A2B3', style: LC.LineStyle.Dashed, labelBackgroundColor: '#344054' } },
  });
  container._scChart = chart;
  const candles = chart.addCandlestickSeries({
    upColor: '#16805E', downColor: '#B34A32', borderVisible: true,
    borderUpColor: '#116B4F', borderDownColor: '#963923', wickUpColor: '#116B4F', wickDownColor: '#963923',
  });
  candles.setData(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })));
  const vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
  vol.setData(rows.map((r) => ({ time: r[0], value: isNum(r[5]) ? r[5] : 0, color: (isNum(r[4]) && isNum(r[1]) && r[4] >= r[1]) ? 'rgba(22,128,94,.45)' : 'rgba(179,74,50,.45)' })));
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  chart.timeScale().fitContent();
  if (ohlc) ohlc.innerHTML = stockOhlcReadout(ticker, rows[rows.length - 1]);
  chart.subscribeCrosshairMove((param) => {
    if (!ohlc || !param || !param.time || !param.seriesData) return;
    const bar = param.seriesData.get(candles);
    const v = param.seriesData.get(vol);
    if (!bar) return;
    const time = typeof param.time === 'string' ? param.time
      : `${param.time.year}-${String(param.time.month).padStart(2, '0')}-${String(param.time.day).padStart(2, '0')}`;
    ohlc.innerHTML = stockOhlcReadout(ticker, [time, bar.open, bar.high, bar.low, bar.close, v ? v.value : null]);
  });
}

function wireStockChart(root, ticker) {
  const container = root.querySelector('.stock-chart');
  if (!container || container.dataset.wired) return;
  container.dataset.wired = '1';
  const load = (days) => {
    const from = stockChartFromDate(Number(days));
    const key = `${ticker}|${from}`;
    const canvas = container.querySelector('.sc-canvas');
    if (STOCK_OHLC_CACHE[key]) { renderStockChartData(container, ticker, STOCK_OHLC_CACHE[key]); return; }
    if (canvas) canvas.innerHTML = '<div class="sc-loading muted">Загрузка дневных котировок MOEX ISS…</div>';
    loadLWC((lerr) => {
      if (lerr) { if (canvas) canvas.innerHTML = '<div class="sc-loading muted">Библиотека графиков не загрузилась.</div>'; return; }
      fetchStockOHLC(ticker, from, (err, rows) => {
        if (err || !rows.length) {
          if (canvas) canvas.innerHTML = `<div class="sc-loading muted">Дневные котировки ${esc(ticker)} на MOEX ISS сейчас недоступны.</div>`;
          return;
        }
        STOCK_OHLC_CACHE[key] = rows;
        renderStockChartData(container, ticker, rows);
      });
    });
  };
  container.querySelector('.sc-periods').addEventListener('click', (event) => {
    const button = event.target.closest('[data-sc-days]');
    if (!button) return;
    container.querySelectorAll('.sc-periods button').forEach((b) => { b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', b === button); });
    load(button.dataset.scDays);
  });
  load('252');
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
// Метка точки: «за период» (value_q, Ф.102) относится к месяцу ПЕРЕД отчётной датой (отчёт на 01.06 =
// итог на конец мая → дельта = май). Берём явный period_month из данных; fallback для старых данных
// без поля — отчётная дата минус 1 месяц. Накопительный режим подписывается отчётной датой как есть.
function cbrPointLabel(p, mode) {
  if (mode === 'q') {
    if (p.period_month) return cbrMon(p.period_month);
    const [y, m] = String(p.date).split('-').map(Number);
    return CBR_MONTHS[m === 1 ? 12 : m - 1] + ' ' + (m === 1 ? y - 1 : y);
  }
  return cbrMon(p.date);
}
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
  const f = (n) => fetch(dataURL('cbr/' + n)).then((r) => { if (!r.ok) throw new Error(n + ' ' + r.status); return r.json(); });
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
      <span class="cbr-bank-card-top"><span class="cbr-bank-identity">${instrumentAvatarHTML(b.ticker, b.name, 'equity', 'sm')}<b>${esc(b.ticker)}</b></span><span>${cbrRub(b.mcap_rub)}</span></span>
      <span class="cbr-bank-card-name">${esc(b.name)}</span>
      <span class="cbr-bank-card-metrics"><span class="${tone}">P/капитал ЦБ ${pbv}</span><span>ROE ${roe}</span></span>
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
  const bankWarns = bvalWarnings(val);
  const warns = bankWarns.length ? `<div class="cbr-bank-warnings">${bankWarns.slice(0, 2).map((w) => `<span>⚠ ${esc(w)}</span>`).join('')}</div>` : '';
  return `<div class="cbr-bank-summary-main" style="--bank-color:${esc((val && val.color) || '#5B6B83')}">
    <div class="cbr-bank-title">
      ${instrumentAvatarHTML(ticker || name, name, 'equity', 'lg')}
      <div><b>${esc(name)}</b><span>${ticker ? esc(ticker) + ' · ' : ''}${bank.is_systemically_important ? 'системно значимый' : 'активный банк'}</span></div>
    </div>
    <div class="cbr-bank-kpis">
      <div><span>Капитализация</span><b>${val ? cbrRub(val.mcap_rub) : ND}</b></div>
      <div><span>P/капитал ЦБ</span><b class="${pbvTone}">${val && isNum(val.p_bv) ? val.p_bv.toFixed(2) : ND}</b></div>
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
      <td>${esc(cbrPointLabel(p, s.mode))}</td><td class="cbr-bname">${esc(bank.name || '')}</td><td>${esc(metricName)}${esc(s.modeLabel)}</td>
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
  const labels = s.rows.map((p) => cbrPointLabel(p, s.mode));
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
  fetch(dataURL('bonds/finder.json'))
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

function finderPortfolioState(prof) {
  const a = prof.aggregates || {};
  const issuers = Number.isFinite(+a.issuers) ? +a.issuers : 0;
  const required = Number.isFinite(+a.min_issuers_required) ? +a.min_issuers_required : 6;
  const constructible = typeof a.constructible === 'boolean'
    ? a.constructible
    : issuers >= required;
  return { constructible, issuers, required };
}

function finderRows() {
  const pid = (document.getElementById('fnd-profile') || {}).value || 'balanced';
  const prof = FINDER.profiles[pid] || { picks: [], aggregates: {} };
  const portfolio = finderPortfolioState(prof);
  const budget = Math.max(0, +(document.getElementById('fnd-budget') || {}).value || 0);
  const rows = prof.picks.map((p) => {
    const alloc = portfolio.constructible && isNum(p.weight) ? p.weight * budget : null;
    const pieces = alloc != null && p.dirty_price > 0 ? Math.floor(alloc / p.dirty_price) : null;
    return { ...p, pieces, cost: pieces != null ? pieces * p.dirty_price : null };
  });
  return { pid, prof, budget, rows, portfolio };
}

function finderDraw() {
  const { prof, budget, rows, portfolio } = finderRows();
  const sumEl = document.getElementById('fnd-summary');
  const tblEl = document.getElementById('fnd-table');
  if (!sumEl || !tblEl) return;
  const a = prof.aggregates || {};
  const spent = rows.reduce((s, r) => s + (isNum(r.cost) ? r.cost : 0), 0);
  if (!portfolio.constructible) {
    sumEl.innerHTML = `<div class="fnd-blocked"><b>Портфель не сформирован</b>`
      + `<span>Найдено эмитентов: ${portfolio.issuers}, минимум: ${portfolio.required}. Веса, сумма и портфельная доходность не рассчитаны. Ниже только кандидаты для ручной проверки.</span></div>`
      + [['Кандидатов', rows.length], ['Эмитентов', portfolio.issuers], ['Требуется', portfolio.required]]
        .map(([k, v]) => `<div class="fnd-kpi"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('');
  } else {
    sumEl.innerHTML = [
      ['Net YTM', a.ytm_net_wavg != null ? a.ytm_net_wavg.toFixed(2) + '%' : '—'],
      ['Дюрация', a.duration_wavg != null ? a.duration_wavg.toFixed(2) + ' г' : '—'],
      ['Эмитентов', portfolio.issuers],
      ['Бюджет', rub0(spent) + ' из ' + rub0(budget)],
    ].map(([k, v]) => `<div class="fnd-kpi"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')
    + (a.note ? `<div class="fnd-note">⚠ ${esc(a.note)}</div>` : '');
  }

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
    <td class="tnum"><b>${isNum(r.pieces) ? r.pieces : '—'}</b></td>
    <td class="tnum">${isNum(r.cost) ? rub0(r.cost) : '—'}</td>
  </tr>`).join('');
  tblEl.innerHTML = rows.length
    ? `${portfolio.constructible ? '' : '<div class="fnd-candidate-note">Кандидаты не являются готовой аллокацией: сначала расширьте диверсификацию по эмитентам.</div>'}<div class="fnd-table-scroll"><table class="fnd-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`
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
// Банки РФ — секторная оценка (цена/регуляторный капитал ЦБ, ROE, пэйаут, Н1.0)
// и диагностическая ROE-линия. Всё из site/cbr/valuation.json. Не ИИР.
// ══════════════════════════════════════════════════════════════════════════
let BVAL = null;
let BVAL_COE = 20;                 // percent, slider-driven
let BVAL_SORT = { key: 'mcap_rub', dir: -1 };
let BVAL_SEL = null;               // единый выбор банка: таблица ↔ карта сектора ↔ история цены/капитала
let BHIST = null;                  // site/cbr/history.json (price-vs-capital trajectory)
let BHIST_SEL = null;             // currently selected ticker in the history chart

// Аналитические зоны достаточности капитала по Н1.0 (НЕ регуляторное заключение — см. методологию)
const BVAL_CAPITAL_CONFIG = { h10Watch: 11.0, h10Comfort: 13.0, h10Strong: 15.0 };
const BVAL_TOOLTIPS = {
  p_bv: 'Рыночная капитализация / регуляторный капитал формы 123 ЦБ РФ. Это не бухгалтерский капитал по МСФО и не IFRS P/BV. Коэффициент ниже 1 сам по себе не доказывает недооценку.',
  roe: 'Прибыль на средний регуляторный капитал. Линия P/капитал ЦБ ≈ ROE / COE — диагностический ориентир внутри сектора, а не fair value или целевая цена.',
  p_e: 'Для банков вторичен: прибыль шумит от резервов и переоценок. Используйте вместе с P/капитал ЦБ и только внутри сектора.',
};

function bvalWarnings(bank) {
  return (bank && Array.isArray(bank.warnings) ? bank.warnings : []).map((warning) => {
    const text = String(warning);
    if (text.includes('Капитал — регуляторный Ф.123') && text.includes('P/BV')) {
      return 'Знаменатель — регуляторный капитал Ф.123 (Базель III, с субордами), не бухгалтерский equity; P/капитал ЦБ не равен IFRS P/BV';
    }
    return text;
  });
}

// Отклонение цены/регуляторного капитала от ROE-линии. Это НЕ fair value и НЕ целевая
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
  fetch(dataURL('cbr/valuation.json'))
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((j) => { if (!j || !j.banks) throw new Error('empty'); BVAL = j; cb(); })
    .catch((e) => { console.error('[bval]', e); cb(e); });
}

function loadBanksHistory(cb) {
  if (BHIST) { cb(); return; }
  fetch(dataURL('cbr/history.json'))
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
    if (spread > 0 && disc < 0) parts.push(`ROE выше заданной стоимости капитала (COE ${BVAL_COE}%), а P/капитал ЦБ ниже ROE-линии: рынок оценивает регуляторный капитал банка осторожнее, чем следует из текущей прибыльности. Требуется проверить устойчивость ROE и качество данных.`);
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
    ['roe', 'ROE', '%'], ['p_bv', 'P/капитал ЦБ', ''], ['n10', 'Н1.0', '%'], ['payout', 'Пэйаут', '%'],
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

// единый выбор банка: подсветка строки + точка на карте + история цены/капитала (без scrollIntoView)
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
  t.push(`<b>${cheap.length} из ${withPbv.length}</b> банков торгуются ниже регуляторного капитала ЦБ (коэффициент &lt; 1).`);
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
  const tips = { ...(m.tooltips || {}), ...BVAL_TOOLTIPS };
  const howto = [
    ['P/капитал ЦБ', tips.p_bv], ['ROE', tips.roe], ['P/E', tips.p_e], ['Пэйаут', tips.payout],
    ['Дивдоходность', tips.div_yield], ['Н1.0', tips.n10], ['Прибыль', tips.profit],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v || '')}</dd>`).join('');
  const [lo, hi] = m.coe_range || [0.12, 0.30];
  return `
    ${bvalHealthStrip(m)}
    <div class="bval-takeaway" id="bval-takeaways"></div>
    <div class="bval-guide">
      <div><b>ROE − COE</b><span>создаёт ли банк доходность выше требуемой</span></div>
      <div><b>ROE-линия</b><span>грубая связь прибыльности и цены регуляторного капитала, не fair value</span></div>
      <div><b>Н1.0</b><span>достаточность капитала (зоны — аналитические)</span></div>
      <div><b>Дивспособность</b><span>выдержит ли капитал выплату — диагностика</span></div>
    </div>
    <div id="bval-table-wrap"></div>
    <details class="bval-howto"><summary>Как читать оценку банков</summary><dl class="bval-dl">${howto}</dl>
      <div class="bval-method-txt">
        P/капитал ЦБ показывает, сколько рынок платит за 1 рубль регуляторного капитала из формы 123. Это не IFRS P/BV. ROE — доходность капитала.
        ROE − COE показывает, создаёт ли банк доходность выше пользовательского требования (COE — ползунок).
        ROE-линия P/капитал ЦБ = ROE / COE — грубая модельная связь прибыльности и оценки; отклонение от неё
        <b>не является fair value или целевой ценой</b>. Н1.0 — достаточность капитала; зоны
        «сильный/комфортный/watch/проверить» (${BVAL_CAPITAL_CONFIG.h10Strong}/${BVAL_CAPITAL_CONFIG.h10Comfort}/${BVAL_CAPITAL_CONFIG.h10Watch}%) — аналитические допущения, не регуляторное заключение.
        Пэйаут — доля прибыли, направляемая акционерам. Дивидендная способность — модельная диагностика
        (ROE, буфер капитала, payout, качество данных), не рекомендация. Источники: формы ЦБ 102/123/135 + MOEX ISS.
        Пользователь самостоятельно принимает инвестиционные решения.
      </div>
      <div class="muted" style="font-size:.78rem;margin-top:6px">${esc(m.reg_min_note || '')}</div></details>

    <div class="bval-hist-box" id="bval-hist">
      <div class="bval-hist-head"><b>История P/капитал ЦБ: цена акции против капитала на акцию</b><span class="muted"> — когда цена ниже линии регуляторного капитала, коэффициент меньше 1</span></div>
      <div class="bval-hist-chips" id="bval-hist-chips"></div>
      <div class="bval-hist-stat" id="bval-hist-stat"></div>
      <div class="bval-hist-wrap"><canvas id="bval-hist-canvas"></canvas></div>
      <div class="bval-hist-cap muted" id="bval-hist-cap"></div>
    </div>

    <div class="bval-scatter-box">
      <div class="bval-scatter-head">
        <b>Карта сектора: прибыльность (ROE) × цена регуляторного капитала</b>
        <label class="bval-coe">COE <input type="range" id="bval-coe" min="${Math.round(lo * 100)}" max="${Math.round(hi * 100)}" step="1" value="${BVAL_COE}"><span><b id="bval-coe-val">${BVAL_COE}</b>%</span></label>
      </div>
      <div class="bval-scatter-wrap"><canvas id="bval-scatter"></canvas></div>
      <div class="bval-caption muted">Линия P/капитал ЦБ = ROE / COE (сейчас <b id="bval-cap-coe">${BVAL_COE}</b>%) — <b>не fair value</b>, а диагностический ориентир: точки <b>ниже</b> линии рынок оценивает дешевле относительно текущего ROE, <b>выше</b> — дороже. Размер точки — капитализация, цвет — дивидендная способность. Клик по точке выбирает банк.</div>
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
      <td class="left">${instrumentIdentityHTML(b.ticker, b.name, 'equity', 'sm')}</td>
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
    ['p_bv', 'P/капитал ЦБ', 'r', 0, T.p_bv],
    ['fair_disc', 'К ROE-линии', 'r', 0, 'P/капитал ЦБ ÷ (ROE/COE) − 1 при текущем COE ползунка. Это НЕ fair value и не целевая цена, а грубая диагностическая линия. Минус — рынок ценит регуляторный капитал дешевле относительно текущего ROE'],
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
    const bankWarns = bvalWarnings(b);
    const warn = bankWarns.length;
    const tk = esc(b.ticker);
    const pctl = (k) => num(b[k], '%');
    const disc = bvalRoeLineDiscount(b);
    const spread = isNum(b.roe) ? b.roe - BVAL_COE : null;
    const cap = b.dividend_capacity_score;
    const capVt = !isNum(cap) ? 'neut' : cap >= 75 ? 'good' : cap >= 55 ? 'neut' : cap >= 40 ? 'warn' : 'risk';
    const dq = b.data_quality_score;
    const sel = b.ticker === BVAL_SEL ? ' bval-selected' : '';
    const main = `<tr class="bval-row${sel}" data-i="${i}" data-tk="${tk}">
      <td class="al-l bval-bname">${instrumentIdentityHTML(b.ticker, b.name, 'equity', 'sm')}${warn ? ` <span class="bval-warn-badge" title="${esc(bankWarns.join(' · '))}">⚠${warn}</span>` : ''}</td>
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
        <div class="bval-dcard"><h5>Что показывает связка ROE × P/капитал ЦБ</h5><div class="bval-nar-txt" data-tk="${tk}">${esc(bvalBankNarrative(b))}</div></div>
        <div class="bval-dcard"><h5>Капитал → дивиденды</h5>${bvalBridgeHTML(b)}</div>
        <div class="bval-dcard"><h5>Против медианы сектора</h5>${bvalPeersHTML(b)}</div>
        <div class="bval-dcard"><h5>Тренд достаточности капитала</h5><div>${trendHTML}</div>
          <div class="bval-detail-meta">Даты: MOEX ${esc(vin.moex || '—')} · Ф.102 ${esc(vin.cbr_102 || '—')} · Ф.123 ${esc(vin.cbr_123 || '—')} · Ф.135 ${esc(vin.cbr_135 || '—')}</div>
          ${dh ? `<div class="bval-detail-meta">Дивиденды: ${dh}</div>` : ''}
          ${fcHTML ? `<div class="bval-fc"><span class="k">Прогноз — ручной ввод:</span> ${fcHTML}</div>` : ''}</div>
      </div>
      ${bankWarns.length ? `<div class="bval-wlines">${bankWarns.map((w) => `<div class="bval-wline">⚠ ${esc(w)}</div>`).join('')}</div>` : ''}
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
        { type: 'line', label: `ROE-линия P/капитал ЦБ = ROE/COE (${coe}%)`, order: 1,
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
        x: { min: 0, max: xmax, title: { display: true, text: 'P/регуляторный капитал ЦБ', color: '#5A6472' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
        y: { min: 0, title: { display: true, text: 'ROE, % (прибыльность)', color: '#5A6472' }, grid: { color: '#EEF1F6' }, ticks: { color: '#5A6472' } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (it) => { const b = pts[it.dataIndex]; return b ? `${b.name}: P/капитал ЦБ ${b.p_bv}, ROE ${b.roe}%${isNum(b.dividend_capacity_score) ? `, дивспособн. ${b.dividend_capacity_score}/100` : ''}` : ''; }, filter: (it) => it.datasetIndex === 0 } },
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
      ? `<span class="bh-verdict cheap">ниже регуляторного капитала · ${cur.toFixed(2)}</span>`
      : `<span class="bh-verdict rich">выше регуляторного капитала · ${cur.toFixed(2)}</span>`;
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
    cap.innerHTML = `Зелёная зона — цена ниже регуляторного капитала ЦБ (коэффициент&nbsp;&lt;&nbsp;1), красная — выше.
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
        { label: 'Регуляторный капитал на акцию (коэффициент = 1)', data: bvps, borderColor: '#7A8598', backgroundColor: '#7A8598',
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
            afterBody: (items) => { const p = pts[items[0].dataIndex]; return p ? `P/капитал ЦБ: ${p.pbv.toFixed(2)}${p.pbv < 1 ? '  — ниже регуляторного капитала' : ''}` : ''; },
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
    NEWS_LOAD_PROMISE = fetch(dataURL('news.json'))
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
  const ticker = it.ticker || (Array.isArray(it.tickers) ? it.tickers[0] : '');
  const issuer = ticker ? instrumentAvatarHTML(ticker, it.company || '', it.instrument_type, 'xs') : '';
  return `<article class="news-card${expandable}" data-kind="${kind}" data-i="${i}">
    <div class="news-head">
      ${issuer}${cat}${star}${inv}
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
    const tk = a.ticker ? `<span class="news-agenda-tk">${instrumentIdentityHTML(a.ticker, a.company, a.instrument_type, 'xs', { variant: 'compact', showTypeText: false })}</span>` : '';
    return `<div class="news-agenda-row"><span class="news-agenda-time">${esc(a.time || '—')}</span>${tp}<span class="news-agenda-ev">${esc(a.event || '')}</span>${tk}${star}</div>`;
  }).join('');
}

// Один пайплайн отдаёт разные по смыслу выпуски: до открытия, во время сессии,
// по итогам дня, выходной обзор, повестка новой недели. Раньше всё называлось
// «утренним брифингом» — вечером и в выходные это была неправда. Тип проставляет
// генератор (поле briefing), а не модель; старые news.json без поля — просто без метки.
const NEWS_KIND_LABEL = {
  premarket:  'До открытия',
  intraday:   'К текущей сессии',
  evening:    'Итоги дня',
  weekend:    'Обзор недели',
  week_ahead: 'К новой неделе',
};

function newsShellHTML(d) {
  const upd = newsMskTime(d.generated_at);
  const freshness = newsFreshness(d.generated_at);
  const kind = d.briefing && NEWS_KIND_LABEL[d.briefing.kind] ? d.briefing.kind : null;
  const kindTag = kind
    ? `<span class="news-kind" title="${esc(d.briefing.context || '')}">${esc(NEWS_KIND_LABEL[kind])}</span>`
    : '';
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
      <div class="news-updated">${kindTag}Обновлено ${upd ? `в <b>${esc(upd)}</b> МСК` : '—'}${d.date ? ` · ${esc(d.date)}` : ''}${stale}</div>
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
    <!-- «Сегодня в календаре» убрано: те же события полнее показывает блок «Сегодня важные
         события» во вкладке «Обзор» (дивиденды MOEX + заседания ЦБ + отчётности, с подсветкой
         бумаг портфеля). Дублировать в брифинге незачем; newsAgendaHTML оставлен — данные
         today_agenda остаются в news.json и используются моделью для раздела «Главное». -->
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
  { ticker: 'EQMX', name: 'БПИФ на индекс МосБиржи', instrument_type: 'fund', _extra: true },
  { ticker: 'DIVD', name: 'БПИФ дивидендных акций РФ', instrument_type: 'fund', _extra: true },
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

// ── корреляционная матрица холдингов (из ковариации месячных total-returns) ───
function pfxCorrelation(c) {
  const ps = c.positions.filter((p) => p._tr && p._tr.length >= 12).sort((a, b) => b.weight - a.weight).slice(0, 16);
  if (ps.length < 2) return { ok: false };
  const cov = pfxCovariance(ps.map((p) => p._tr));
  const S = cov.S, n = ps.length;
  const M = Array.from({ length: n }, () => new Array(n).fill(null));
  const off = []; let maxPair = null, minPair = null;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    if (i === j) { M[i][j] = 1; continue; }
    const d = Math.sqrt(S[i][i] * S[j][j]);
    const r = d > 0 ? Math.max(-1, Math.min(1, S[i][j] / d)) : null;
    M[i][j] = r;
    if (j > i && r != null) { off.push(r);
      if (!maxPair || r > maxPair.r) maxPair = { i, j, r };
      if (!minPair || r < minPair.r) minPair = { i, j, r }; }
  }
  const avg = off.length ? off.reduce((a, b) => a + b, 0) / off.length : null;
  return { ok: true, labels: ps.map((p) => p.ticker), M, avg, maxPair, minPair, n, approx: cov.approx };
}
function pfxCorrColor(r) {                                  // диверг. шкала: высокая связь = тревога
  if (r == null) return '#EEF1F6';
  if (r >= 0.75) return '#C05B45'; if (r >= 0.55) return '#D98E63';
  if (r >= 0.35) return '#E9C79A'; if (r >= 0.15) return '#CFE0CB';
  if (r >= -0.05) return '#A8D5C2'; return '#7FB0C4';       // отрицательная — лучший диверсификатор
}
function pfxCorrHTML(c) {
  const cr = c._corr || pfxCorrelation(c);
  if (!cr.ok) return `<div class="pfx-note">${NA}: нужно ≥2 бумаги с историей ≥12 мес.</div>`;
  const { labels, M, avg, maxPair, minPair, n } = cr;
  const s = n > 12 ? 26 : n > 8 ? 32 : 38, pad = 58, W = pad + n * s + 6, H = pad + n * s + 6;
  const short = (t) => (t.length > 6 ? t.slice(0, 6) : t);
  let cells = '';
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const r = M[i][j], x = pad + j * s, y = pad + i * s;
    const fill = i === j ? '#E7EAF2' : pfxCorrColor(r);
    const txt = i === j ? '' : (r == null ? '—' : (r >= 0 ? '' : '−') + Math.abs(Math.round(r * 100)));
    const tc = (r != null && r >= 0.55) ? '#fff' : '#3A424E';
    cells += `<rect x="${x}" y="${y}" width="${s - 1}" height="${s - 1}" rx="3" fill="${fill}"></rect>` +
      (txt ? `<text x="${x + s / 2}" y="${y + s / 2 + 3}" text-anchor="middle" font-size="${n > 12 ? 8 : 9}" fill="${tc}">${txt}</text>` : '');
  }
  let labX = '', labY = '';
  for (let i = 0; i < n; i++) {
    const c0 = pad + i * s + s / 2;
    labX += `<text x="${c0}" y="${pad - 6}" text-anchor="start" font-size="9" fill="#5A6472" transform="rotate(-45 ${c0} ${pad - 6})">${esc(short(labels[i]))}</text>`;
    labY += `<text x="${pad - 6}" y="${c0 + 3}" text-anchor="end" font-size="9" fill="#5A6472">${esc(short(labels[i]))}</text>`;
  }
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="pfx-corr-svg" role="img" aria-label="Корреляционная матрица">${labX}${labY}${cells}</svg>`;
  const avgWord = avg == null ? '—' : avg > 0.6 ? 'высокая' : avg > 0.4 ? 'умеренная' : avg > 0.2 ? 'умеренно-низкая' : 'низкая';
  const avgNote = avg == null ? '' : avg > 0.6 ? 'портфель ведёт себя почти как одна ставка — в стрессе просядет синхронно'
    : avg > 0.4 ? 'диверсификация частичная' : 'бумаги слабо связаны — хорошая диверсификация';
  const pair = (p) => p ? `${esc(labels[p.i])}↔${esc(labels[p.j])} (${p.r >= 0 ? '+' : '−'}${Math.abs(Math.round(p.r * 100))}%)` : '—';
  return `<div class="pfx-corr">
    <div class="pfx-corr-wrap">${svg}</div>
    <div class="pfx-corr-side">
      <div class="pfx-corr-kpi"><span>Средняя парная корреляция</span><b>${avg == null ? mdash : (avg >= 0 ? '+' : '−') + Math.abs(Math.round(avg * 100)) + '%'}</b><em>${avgWord} · ${avgNote}</em></div>
      <div class="pfx-corr-pair"><span>Сильнее всех вместе:</span> <b>${pair(maxPair)}</b></div>
      <div class="pfx-corr-pair"><span>Лучший диверсификатор:</span> <b>${pair(minPair)}</b></div>
      <div class="pfx-corr-legend">
        <span><i style="background:#C05B45"></i>≥75</span><span><i style="background:#D98E63"></i>55–75</span>
        <span><i style="background:#E9C79A"></i>35–55</span><span><i style="background:#A8D5C2"></i>0–35</span>
        <span><i style="background:#7FB0C4"></i>&lt;0</span>
      </div>
    </div></div>
    <div class="pfx-note muted">Корреляция месячных total-returns (${n} крупнейших позиций с историей). Высокие значения — бумаги движутся вместе (диверсификация слабее); отрицательные — гасят друг друга.${cr.approx ? ' Ковариация регуляризована (короткая история).' : ''} Не ИИР.</div>`;
}

// ── атрибуция доходности: фактический нереализ. P&L по бумагам/секторам (Bible VIII) ──
function pfxAttribution(c) {
  const ps = c.positions.filter((p) => isNum(p.value) && isNum(p.cost) && p.cost > 0);
  if (!ps.length) return { ok: false };
  const rows = ps.map((p) => ({ ticker: p.ticker, name: (p.t && p.t.name) || p.ticker, sector: p.sector || ND,
    pnl: p.value - p.cost, pnlPct: p.value / p.cost - 1, weight: p.weight, value: p.value }));
  const totalPnl = rows.reduce((s, r) => s + r.pnl, 0);
  rows.sort((a, b) => b.pnl - a.pnl);
  const secMap = {};
  rows.forEach((r) => { secMap[r.sector] = (secMap[r.sector] || 0) + r.pnl; });
  const sectors = Object.entries(secMap).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return { ok: true, rows, totalPnl, sectors, best: rows[0], worst: rows[rows.length - 1] };
}
function pfxAttrHTML(c) {
  const a = pfxAttribution(c);
  if (!a.ok) return `<div class="pfx-note">${NA}: нет позиций с ценой и средней.</div>`;
  const maxAbs = Math.max(...a.rows.map((r) => Math.abs(r.pnl)), 1);
  const bar = (r) => {
    const w = Math.max(2, Math.round(Math.abs(r.pnl) / maxAbs * 100));
    const pos = r.pnl >= 0;
    return `<div class="pfx-attr-row">
      <span class="pfx-attr-tk">${esc(r.ticker)}</span>
      <div class="pfx-attr-track"><i class="${pos ? 'pos' : 'neg'}" style="width:${w}%"></i></div>
      <span class="pfx-attr-pnl ${pos ? 'saw-up' : 'saw-down'}">${rub0(r.pnl)}</span>
      <span class="pfx-attr-pct ${pos ? 'saw-up' : 'saw-down'}">${PP(r.pnlPct, 1)}</span></div>`;
  };
  const drivers = [
    `Всего нереализованный P&L: <b class="${a.totalPnl >= 0 ? 'saw-up' : 'saw-down'}">${rub0(a.totalPnl)}</b>.`,
    a.best && a.best.pnl > 0 ? `Главный вклад — <b>${esc(a.best.ticker)}</b> (${rub0(a.best.pnl)}, ${PP(a.best.pnlPct, 0)}).` : '',
    a.worst && a.worst.pnl < 0 ? `Тянет вниз — <b>${esc(a.worst.ticker)}</b> (${rub0(a.worst.pnl)}, ${PP(a.worst.pnlPct, 0)}).` : '',
    a.sectors.length ? `Сектор-лидер по вкладу: <b>${esc(a.sectors[0][0])}</b> (${rub0(a.sectors[0][1])}).` : '',
  ].filter(Boolean);
  const sec = a.sectors.slice(0, 6).map(([s, v]) => `<div class="pfx-attr-secrow"><span>${esc(s)}</span><b class="${v >= 0 ? 'saw-up' : 'saw-down'}">${rub0(v)}</b></div>`).join('');
  return `<div class="pfx-attr">
    <div class="pfx-attr-drivers">${drivers.map((d) => `<div>${d}</div>`).join('')}</div>
    <div class="pfx-attr-2col">
      <div class="pfx-attr-bars">${a.rows.map(bar).join('')}</div>
      <div class="pfx-attr-sectors"><h4>Вклад секторов</h4>${sec}</div>
    </div>
    <div class="pfx-note muted">Атрибуция по ФАКТИЧЕСКОМУ нереализованному P&L = (тек. цена − средняя) × количество. Это реальные бумажные прибыли/убытки по вашим позициям, не бэктест. Не ИИР.</div>
  </div>`;
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
