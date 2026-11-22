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
    if (!company || !(company.fact_count ?? company.facts.length)) return '<span class="foundry-coverage-badge is-missing" title="В этом слое отобраны проверенные консолидированные факты МСФО в RUB. Отсутствие сопоставимого среза не означает отсутствия отчётности. Другие источники показаны в подробностях.">Data Foundry: нет сопоставимого среза</span>';
    const end = company.latest_report_end || company.facts.map(f => f.period.end).sort().pop();
    return `<span class="foundry-coverage-badge is-covered" title="Есть принятые факты, но полнота последней отчётности и качество модельной оценки проверяются отдельно.">Data Foundry: есть данные · период до ${escape(end)}</span>`;
  }
  function renderCoverageSummary() {
    if (typeof document === 'undefined') return;
    const el = document.getElementById('foundry-coverage-summary');
    if (!el || !snapshot) return;
    const {covered_companies: count, requested_companies: total, fetched_at: fetched} = snapshot.meta;
    el.hidden = false;
    el.innerHTML = `<b>МСФО в рублях · Data Foundry: ${escape(count)} из ${escape(total)} бумаг</b> · загрузка ${escape(day(fetched))}. `
      + 'Покрытие источника не означает полноту последней отчётности. <button type="button">Посмотреть источники</button>';
    el.querySelector('button').addEventListener('click', () => {
      const panel = document.getElementById('foundry-panel');
      if (panel) { panel.open = true; panel.scrollIntoView({block:'start', behavior:'auto'}); }
    });
  }
  function reportHTML(ticker) {
    const company = snapshot && snapshot.companies.find(c => c.ticker === ticker);
    if (!company || !company.facts.length) return '';
    const codes = ['fin.revenue', 'fin.operating_profit', 'fin.net_income', 'fin.total_assets', 'fin.total_equity'];
    const rows = codes.map(code => {
      const facts = company.facts.filter(f => f.metric_code === code && f.period.group === 'annual');
      const end = facts.map(f => f.period.end).sort().pop();
      const latest = facts.filter(f => f.period.end === end);
      if (!latest.length) return '';
      // No automatic choice between differing assertions of the same period.
      if (new Set(latest.map(f => f.value)).size !== 1) return `<tr><td>${escape(snapshot.metrics[code])}</td><td colspan="2">Несколько вариантов · см. панель Data Foundry</td></tr>`;
      const f = latest[0], evidence = snapshot.evidence[f.source_id] || {}, url = safeUrl(evidence.official_url);
      return `<tr><td>${escape(snapshot.metrics[code])}</td><td>${escape(f.period.key)}</td><td class="tnum">${(Number(f.value)/1e6).toLocaleString('ru-RU',{maximumFractionDigits:1})}</td><td>${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">Отчёт</a>` : 'н/д'}</td></tr>`;
    }).join('');
    if (!rows) return '';
    return `<div class="foundry-card"><b>Отчётность Data Foundry · МСФО группы</b><p>Последние доступные годовые значения, млн ₽. Периоды у строк могут различаться. База опубликована ${escape(day(snapshot.dataset.published_at))}; наличие последнего отчёта не гарантировано.</p><div class="foundry-table"><table><thead><tr><th>Показатель</th><th>Период</th><th>Млн ₽</th><th>Источник</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
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
          + `<p class="muted">Групповая МСФО, RUB. Доступные финансовые показатели группы. Витрина поставщика обновляется вручную; наличие последнего отчёта не гарантировано. Этот слой пока не заменяет входы ML и оценки стоимости.</p>`
          + `<label for="foundry-company">Компания</label> <select id="foundry-company">${data.companies.map(c => `<option value="${escape(c.ticker)}">${escape(c.ticker)} · ${escape(c.name || 'нет покрытия')}</option>`).join('')}</select>`
          + `<label for="foundry-metric">Показатель</label> <select id="foundry-metric">${Object.entries(data.metrics).map(([code,name]) => `<option value="${escape(code)}">${escape(name)}</option>`).join('')}</select>`
          + '<div id="foundry-facts" aria-live="polite"></div>';
        const select = document.getElementById('foundry-company');
        const metric = document.getElementById('foundry-metric');
        if (data.metrics['fin.revenue']) metric.value = 'fin.revenue';
        const render = () => {
          const company = data.companies.find(c => c.ticker === select.value);
          const out = document.getElementById('foundry-facts');
          if (!company || !company.facts.length) { out.innerHTML = '<p>Нет принятых сопоставимых значений в запрошенных периодах. Это не нулевая выручка или прибыль.</p>'; return; }
          const facts = company.facts.filter(f => f.metric_code === metric.value).sort((a,b) => b.period.end.localeCompare(a.period.end) || a.metric_code.localeCompare(b.metric_code));
          if (!facts.length) { out.innerHTML = '<p>Этот показатель не покрыт принятым набором.</p>'; return; }
          out.innerHTML = '<div class="foundry-table" tabindex="0" role="region" aria-label="Отчётность с источниками"><table><thead><tr><th>Период</th><th>Показатель</th><th>Млн ₽</th><th>Источник / вариант</th></tr></thead><tbody>'
            + facts.map(f => {
              const e = data.evidence[f.source_id] || {}, url = safeUrl(e.official_url);
              const value = Number(f.value) / 1e6;
              const dims = Object.entries(f.dimensions || {}).map(([k,v]) => ({business_segment:'Сегмент',geography:'География'}[k] || k) + ': ' + ({all:'все',group:'вся группа'}[v] || v)).join(', ');
              return `<tr><td>${escape(f.period.key)}</td><td>${escape(data.metrics[f.metric_code])}</td>`
                + `<td class="tnum" title="${escape(f.value)} RUB">${Number.isFinite(value) ? value.toLocaleString('ru-RU', {maximumFractionDigits: 3}) : 'н/д'}</td>`
                + `<td>${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">Отчёт${e.locator && e.locator.page ? ', стр. ' + escape(e.locator.page) : ''}</a>` : 'Ссылка отсутствует'}`
                + `<small>${escape(dims || 'Без дополнительных измерений')} · сохранённые сведения об источнике</small></td></tr>`;
            }).join('') + '</tbody></table></div>';
        };
        if (data.companies.some(c => c.ticker === 'WUSH')) select.value = 'WUSH';
        select.addEventListener('change', render); metric.addEventListener('change', render); render();
      } catch (e) {
        body.textContent = 'Отчётность Data Foundry недоступна. Данные других разделов сохранены. Повторите открытие панели для загрузки.';
        loaded = false;
      }
    });
  }
  root.DataFoundryUI = { age, freshness, renderFreshness, load, reportHTML, coverageHTML };
  if (typeof module !== 'undefined') module.exports = { age, freshness, safeUrl, load, reportHTML, coverageHTML };
  if (typeof document !== 'undefined') mount();
})(typeof window !== 'undefined' ? window : globalThis);
