(function (root) {
  'use strict';
  const escape = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = (value) => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? String(value).slice(0, 10) : null;
  function age(value, now = new Date()) {
    const date = day(value);
    if (!date) return null;
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
    const result = Math.round((Date.parse(today + 'T00:00:00Z') - Date.parse(date + 'T00:00:00Z')) / 86400000);
    return Number.isFinite(result) && result >= 0 ? result : null;
  }
  function freshness(meta, now) {
    const prices = age(meta.price_asof, now), model = age(meta.forecast_asof, now);
    return { prices, model, warning: prices == null || prices > 4 || meta.prices_stale || model == null || model > 45 };
  }
  function renderFreshness(meta) {
    const el = document.getElementById('data-freshness-notice');
    if (!el) return;
    const state = freshness(meta);
    el.hidden = false;
    el.classList.toggle('is-warning', Boolean(state.warning));
    el.innerHTML = `<b>${state.warning ? 'Проверьте даты данных' : 'Даты скринера'}</b>`
      + `<span>Цены: ${escape(day(meta.price_asof) || 'дата неизвестна')} · ${state.prices == null ? 'возраст не подтверждён' : state.prices + ' календ. дн. назад'}. `
      + `Модель: ${escape(day(meta.forecast_asof) || 'дата неизвестна')} · ${state.model == null ? 'возраст не подтверждён' : state.model + ' дн. назад'}.</span>`
      + `<details><summary>Как оценена свежесть</summary><small>Цена и прогноз обновляются отдельно. Возраст рассчитан на сегодня по Москве; календарный контроль не заменяет торговый календарь.</small></details>`;
  }
  function safeUrl(url) {
    try { const u = new URL(url); return u.protocol === 'https:' ? u.href : null; } catch (_) { return null; }
  }
  let snapshot = null, loading = null;
  function load() {
    if (snapshot) return Promise.resolve(snapshot);
    if (!loading) loading = fetch('data_foundry_summary.json', {cache: 'no-store'}).then(response => {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    }).then(data => {
      if (data.schema_version !== 1 || !Array.isArray(data.companies)) throw new Error('Неподдерживаемый формат');
      snapshot = data;
      renderCoverageSummary();
      return data;
    }).catch(error => { loading = null; throw error; });
    return loading;
  }
  function coverageHTML(ticker) {
    if (!snapshot) return '<span class="foundry-coverage-badge">Покрытие отчётности проверяется</span>';
    const company = snapshot.companies.find(c => c.ticker === ticker);
    if (!company || !(company.fact_count ?? company.facts.length)) return '<span class="foundry-coverage-badge is-missing" title="Отсутствие в этом наборе не означает отсутствия отчётности компании. Другие источники показаны в подробностях.">Data Foundry: нет принятых значений</span>';
    const end = company.latest_report_end || company.facts.map(f => f.period.end).sort().pop();
    return `<span class="foundry-coverage-badge is-covered" title="Есть принятые факты, но полнота последней отчётности и качество модельной оценки проверяются отдельно.">Data Foundry: есть данные · период до ${escape(end)}</span>`;
  }
  function renderCoverageSummary() {
    if (typeof document === 'undefined') return;
    const el = document.getElementById('foundry-coverage-summary');
    if (!el || !snapshot) return;
    const {covered_companies: count, requested_companies: total, fetched_at: fetched} = snapshot.meta;
    el.hidden = false;
    el.innerHTML = `<b>Данные Data Foundry: ${escape(count)} из ${escape(total)} бумаг</b> · загрузка ${escape(day(fetched))}. `
      + 'Покрытие источника не означает полноту последней отчётности. <button type="button">Посмотреть источники</button>';
    el.querySelector('button').addEventListener('click', () => {
      const panel = document.getElementById('foundry-panel');
      if (panel) { panel.open = true; panel.scrollIntoView({block:'start', behavior:'auto'}); }
    });
  }
  function scopeLabel(f) {
    const basis = {IFRS:'МСФО', RAS:'РСБУ', US_GAAP:'US GAAP', APM:'Управленческий / расчётный'}[f.accounting_basis] || f.accounting_basis || 'стандарт н/д';
    const scope = {consolidated:'группа', standalone:'отдельная компания', separate:'отдельная компания'}[f.consolidation_scope] || f.consolidation_scope || 'периметр н/д';
    return basis + ' · ' + scope;
  }
  function formatFact(f) {
    const n = Number(f.value), unit = f.unit || 'единицы н/д';
    if (!Number.isFinite(n)) return {value:'н/д', unit};
    const fmt = v => v.toLocaleString('ru-RU', {maximumFractionDigits: 3});
    if (/^[A-Z]{3}$/.test(unit) || unit === 'currency') return {value:fmt(n / 1e6), unit:'млн ' + (f.currency || unit)};
    if (unit === 'percent') return {value:fmt(n), unit:'%'};
    if (['multiple','times'].includes(unit)) return {value:fmt(n), unit:'×'};
    if (unit.endsWith('_per_share')) return {value:fmt(n), unit:unit.replace('_per_share',' / акция')};
    return {value:fmt(n), unit:({shares:'акций', count:'шт.', ratio:'доля'}[unit] || unit)};
  }
  function latestFacts(facts) {
    const groups = new Map();
    facts.forEach(f => {
      const key = JSON.stringify([f.metric_code, f.accounting_basis, f.consolidation_scope, f.unit, f.currency,
        Object.entries(f.dimensions || {}).sort(), (f.methodology || {}).code]);
      const group = groups.get(key) || [];
      if (!group.length || f.period.end > group[0].period.end) groups.set(key,[f]);
      else if (f.period.end === group[0].period.end) group.push(f);
    });
    return [...groups.values()].flat();
  }
  function sourceHTML(e, full = false) {
    const url = safeUrl(e.official_url);
    if (url) return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">Отчёт${e.locator && e.locator.page ? ', стр. ' + escape(e.locator.page) : ''}</a>`;
    if (e.kind !== 'calculation') return 'Ссылка отсутствует';
    if (!full || !e.calculation) return 'Расчёт Data Foundry · формула в панели источников';
    const c = e.calculation;
    return `<details><summary>Расчёт Data Foundry</summary><p>${escape(c.expression || c.formula_code)}</p>${(c.inputs || []).map(i => `<small>${escape(i.name || i.metric)}: ${escape(i.value)} ${escape(i.unit)}</small>`).join('')}</details>`;
  }
  function factRow(f, data, full = false) {
    const shown = formatFact(f), e = data.evidence[f.source_id] || {};
    const dims = Object.entries(f.dimensions || {}).map(([k,v]) => ({business_segment:'Сегмент',geography:'География'}[k] || k) + ': ' + ({all:'все',group:'вся группа'}[v] || v)).join(', ');
    return `<tr><td>${escape(data.metrics[f.metric_code] || f.metric_code)}</td><td>${escape(f.period.key)}<small>${escape(scopeLabel(f))}</small></td><td class="tnum" title="${escape(f.value)} ${escape(f.unit)}">${escape(shown.value)}</td><td>${escape(shown.unit)}</td><td>${sourceHTML(e,full)}<small>${escape(dims)}${f.methodology && f.methodology.name ? ' · ' + escape(f.methodology.name) : ''}</small></td></tr>`;
  }
  function factTable(facts, data, full = false) {
    return '<div class="foundry-table" tabindex="0" role="region" aria-label="Все финансовые показатели с источниками"><table><thead><tr><th>Показатель</th><th>Период / стандарт</th><th>Значение</th><th>Единицы</th><th>Источник / методика</th></tr></thead><tbody>'
      + facts.map(f => factRow(f,data,full)).join('') + '</tbody></table></div>';
  }
  function reportHTML(ticker) {
    const company = snapshot && snapshot.companies.find(c => c.ticker === ticker);
    if (!company || !company.facts.length) return '';
    const facts = [...company.facts].sort((a,b) => (snapshot.metrics[a.metric_code] || a.metric_code).localeCompare(snapshot.metrics[b.metric_code] || b.metric_code,'ru') || b.period.end.localeCompare(a.period.end));
    return `<div class="foundry-card"><b>Все доступные показатели Data Foundry · ${facts.length} значений</b><p>Последние доступные наблюдения каждого показателя и варианта отчётности. Валюты, периоды и периметры различаются — они подписаны у значений. База опубликована ${escape(day(snapshot.dataset.published_at))}.</p>${factTable(facts,snapshot)}</div>`;
  }
  function mount() {
    const panel = document.getElementById('foundry-panel');
    if (!panel) return;
    let loaded = false;
    panel.addEventListener('toggle', async () => {
      if (!panel.open || loaded) return;
      loaded = true;
      const body = document.getElementById('foundry-body');
      try {
        const response = await fetch('data_foundry.json', {cache:'no-store'});
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data = await response.json();
        if (data.schema_version !== 1 || !Array.isArray(data.companies)) throw new Error('Неподдерживаемый формат');
        const meta = data.meta, dataset = data.dataset;
        body.innerHTML = `<p>Покрытие: <b>${escape(meta.covered_companies)} из ${escape(meta.requested_companies)}</b> бумаг. `
          + `Публикация базы: ${escape(day(dataset.published_at) || 'н/д')}. Загрузка: ${escape(day(meta.fetched_at))}.</p>`
          + `<p class="muted">Все доступные проверенные показатели: отчётность, денежные потоки, долг, рентабельность, мультипликаторы и отраслевые метрики. Единицы, валюта и периметр указаны отдельно. Витрина поставщика обновляется вручную; наличие последнего отчёта не гарантировано. Этот слой пока не заменяет входы ML и оценки стоимости.</p>`
          + `<label for="foundry-company">Компания</label> <select id="foundry-company">${data.companies.map(c => `<option value="${escape(c.ticker)}">${escape(c.ticker)} · ${escape(c.name || 'нет покрытия')}</option>`).join('')}</select>`
          + `<label for="foundry-metric">Показатель</label> <select id="foundry-metric"><option value="">Все показатели</option>${Object.entries(data.metrics).map(([code,name]) => `<option value="${escape(code)}">${escape(name)}</option>`).join('')}</select>`
          + '<label class="foundry-latest-label"><input type="checkbox" id="foundry-latest" checked> Последние доступные значения (снять для истории)</label><div id="foundry-facts" aria-live="polite"></div>';
        const select = document.getElementById('foundry-company');
        const metric = document.getElementById('foundry-metric');
        const latest = document.getElementById('foundry-latest');
        let renderVersion = 0;
        const companyCache = new Map();
        const render = async () => {
          const version = ++renderVersion;
          let company = data.companies.find(c => c.ticker === select.value);
          const out = document.getElementById('foundry-facts');
          if (!company || !(company.fact_count ?? company.facts.length)) { out.innerHTML = '<p>Нет принятых сопоставимых значений в запрошенных периодах. Это не нулевая выручка или прибыль.</p>'; return; }
          let detailData = data;
          if (data.is_index) {
            const ticker = company.ticker;
            out.textContent = 'Загрузка показателей компании…';
            try {
              if (!companyCache.has(ticker)) {
                const response = await fetch('data_foundry/companies/' + encodeURIComponent(ticker) + '.json', {cache:'no-store'});
                if (!response.ok) throw new Error('HTTP ' + response.status);
                const payload = await response.json();
                if (payload.dataset.version !== data.dataset.version) throw new Error('Разные версии данных');
                companyCache.set(ticker,payload);
              }
              if (version !== renderVersion) return;
              const payload = companyCache.get(ticker);
              company = payload.company;
              detailData = {...data, evidence: payload.evidence};
            } catch (_) {
              if (version === renderVersion) out.textContent = 'Данные компании недоступны. Повторите выбор компании.';
              return;
            }
          }
          let facts = company.facts.filter(f => !metric.value || f.metric_code === metric.value);
          if (latest.checked) facts = latestFacts(facts);
          facts.sort((a,b) => (data.metrics[a.metric_code] || a.metric_code).localeCompare(data.metrics[b.metric_code] || b.metric_code,'ru') || b.period.end.localeCompare(a.period.end));
          if (!facts.length) { out.innerHTML = '<p>Этот показатель не покрыт принятым набором.</p>'; return; }
          out.innerHTML = `<p>Показано ${facts.length} значений. Периоды, стандарты и валюты могут различаться.</p>` + factTable(facts,detailData,true);
        };
        if (data.companies.some(c => c.ticker === 'WUSH')) select.value = 'WUSH';
        select.addEventListener('change', render); metric.addEventListener('change', render); latest.addEventListener('change', render); render();
      } catch (e) {
        body.textContent = 'Отчётность Data Foundry недоступна. Данные других разделов сохранены. Повторите открытие панели для загрузки.';
        loaded = false;
      }
    });
  }
  root.DataFoundryUI = { age, freshness, renderFreshness, load, reportHTML, coverageHTML, formatFact, latestFacts };
  if (typeof module !== 'undefined') module.exports = { age, freshness, safeUrl, load, reportHTML, coverageHTML, formatFact, latestFacts };
  if (typeof document !== 'undefined') mount();
})(typeof window !== 'undefined' ? window : globalThis);
