/* Bond Analytics Engine v4 UI. Financial calculations are read from backend artifacts. */
(function () {
  'use strict';

  const state = {
    profile: 'balanced', budget: 1000000, qualified: false, complex: true,
    query: '', structure: 'all', analysis: 'all', sort: 'opportunity_score', dir: -1,
  };
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
  const num = (value, digits = 1) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : '—';
  const rub = (value) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽' : '—';
  const pct = (value, digits = 1) => Number.isFinite(Number(value)) ? num(value, digits) + '%' : '—';
  const bp = (value) => Number.isFinite(Number(value)) ? num(value, 0) + ' б.п.' : '—';
  const metric = (detail, key) => detail && detail.analytics && detail.analytics[key]
    ? detail.analytics[key].value : null;
  const structureLabels = {
    FIXED_BULLET: 'Фиксированный', AMORTIZING_FIXED: 'Амортизация', PUTTABLE_FIXED: 'Оферта',
    CALLABLE_FIXED: 'Call', FLOATER: 'Флоатер', PERPETUAL_RESET: 'Вечный / reset',
    SUBORDINATED: 'Субординированный', INDEX_LINKED: 'Индексируемый',
  };
  const reasonLabels = {
    ANALYTICS_NOT_FULL: 'аналитика не полная', RELATIVE_VALUE_UNAVAILABLE: 'нет сопоставимых выпусков',
    LIQUIDITY_FLOOR: 'ниже порога ликвидности', RATING_FLOOR: 'ниже рейтингового порога',
    LOT_SIZE_CONCENTRATION: 'лот нарушает лимит концентрации', QUALIFIED_ONLY_DISABLED: 'только для квалифицированных',
    COMPLEX_DISABLED: 'сложные структуры выключены', DURATION_LIMIT: 'дюрация выше лимита',
    MIN_LIQUID_CORE_NOT_MET: 'не собран ликвидный базовый слой', NO_ELIGIBLE_LOTS: 'нет допустимых лотов',
  };

  function identity(row, size = 'sm') {
    if (typeof window.instrumentIdentityHTML === 'function') {
      return window.instrumentIdentityHTML(row.secid, row.name, 'bond', size, { showTypeBadge: false });
    }
    return `<span class="bav4-fallback">${esc(String(row.secid || 'BD').slice(0, 2))}</span><b>${esc(row.name || row.secid)}</b>`;
  }

  function badge(label, tone = '') {
    return `<span class="bav4-badge ${tone}">${esc(label)}</span>`;
  }

  function primary(row) {
    const value = row.primary_metric;
    const label = row.primary_metric_label || 'Метрика';
    if (!Number.isFinite(Number(value))) return { label, value: 'Требует данных' };
    const isSpread = /margin|spread/i.test(label);
    return { label, value: isSpread ? bp(value) : pct(value, 2) };
  }

  function opportunityKey() {
    return `${state.profile}:${state.budget}:${Number(state.qualified)}:${Number(state.complex)}`;
  }

  function opportunitiesHTML(payload, universe) {
    if (!payload || !payload.allocations) return unavailable('Расчёт возможностей пока не опубликован.');
    const allocation = payload.allocations[opportunityKey()] || payload.allocations[payload.default_key];
    if (!allocation) return unavailable('Для выбранных параметров нет серверного расчёта.');
    const byId = new Map(((universe && universe.bonds) || []).map((row) => [row.secid, row]));
    const positions = allocation.positions || [];
    const risk = allocation.risk || {};
    const controls = `<div class="bav4-controls">
      <label>Бюджет<select data-bav4-control="budget">${(payload.available_budgets_rub || []).map((v) => `<option value="${v}"${Number(v) === state.budget ? ' selected' : ''}>${rub(v)}</option>`).join('')}</select></label>
      <label>Профиль<select data-bav4-control="profile">${(payload.available_profiles || []).map((v) => `<option value="${esc(v)}"${v === state.profile ? ' selected' : ''}>${v === 'defensive' ? 'Защитный' : v === 'income' ? 'Доходный' : 'Сбалансированный'}</option>`).join('')}</select></label>
      <label class="bav4-check"><input type="checkbox" data-bav4-control="complex"${state.complex ? ' checked' : ''}> Сложные структуры</label>
      <label class="bav4-check"><input type="checkbox" data-bav4-control="qualified"${state.qualified ? ' checked' : ''}> Есть статус квалифицированного инвестора</label>
    </div>`;
    const summary = `<div class="bav4-summary">
      <div><span>Статус</span><b>${allocation.status === 'OK' ? 'Портфель сформирован' : 'Ограничения не выполнены'}</b></div>
      <div><span>Позиций</span><b>${positions.length}</b></div><div><span>Инвестировано</span><b>${rub(allocation.invested_rub)}</b></div>
      <div><span>Остаток</span><b>${rub(allocation.cash_rub)}</b></div><div><span>Сложные</span><b>${pct((risk.complex_share || 0) * 100)}</b></div>
      <div><span>Ликвидное ядро</span><b>${pct((risk.liquid_core_share || 0) * 100)}</b></div>
    </div>`;
    const warning = allocation.status === 'OK' ? '' : `<div class="bav4-warning"><b>Портфель не опубликован.</b><span>${(allocation.reason_codes || []).map((code) => reasonLabels[code] || code).join('; ') || 'Жёсткие ограничения не выполнены.'}</span></div>`;
    const rows = positions.map((position) => {
      const row = byId.get(position.secid) || position;
      return `<tr><td><button class="bond-open-button" data-bond-open="${esc(position.secid)}">${identity(row)}</button></td>
        <td>${badge(structureLabels[position.structure_class] || position.structure_class)}</td>
        <td class="tnum">${position.lots}</td><td class="tnum">${rub(position.amount_rub)}</td>
        <td class="tnum">${pct(position.weight * 100)}</td><td>${esc(position.reason_included)}</td></tr>`;
    }).join('');
    const exclusions = Object.entries(allocation.exclusions || {}).sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(([code, count]) => `<span>${esc(reasonLabels[code] || code)} <b>${count}</b></span>`).join('');
    return `${controls}${summary}${warning}<div class="bav4-mix" aria-label="Структура портфеля">${Object.entries(allocation.structure_mix || {}).map(([key, value]) => `<i style="--w:${Math.max(1, value * 100)}%"><span>${esc(structureLabels[key] || key)} ${pct(value * 100)}</span></i>`).join('')}</div>
      ${rows ? `<div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Выпуск</th><th>Структура</th><th>Лоты</th><th>Сумма</th><th>Вес</th><th>Почему включён</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="bav4-empty">При этих ограничениях исполнимый портфель не найден. Система не ослабляет лимиты автоматически.</div>'}
      <details class="bav4-details"><summary>Почему бумаги исключались</summary><div class="bav4-exclusions">${exclusions || 'Нет данных'}</div></details>
      <p class="bonds-disc">Режим возможностей сравнивает бумаги только внутри сопоставимых структур и применяет жёсткие лимиты. Высокая доходность сама по себе не является причиной включения. Не ИИР.</p>`;
  }

  function compare(a, b) {
    const key = state.sort;
    const av = a[key], bv = b[key];
    const am = av == null || !Number.isFinite(Number(av));
    const bm = bv == null || !Number.isFinite(Number(bv));
    if (am !== bm) return am ? 1 : -1;
    if (!am && Number(av) !== Number(bv)) return (Number(av) - Number(bv)) * state.dir;
    return String(a.secid).localeCompare(String(b.secid));
  }

  function explorerHTML(universe) {
    const all = (universe && universe.bonds) || [];
    const structures = [...new Set(all.map((row) => row.structure_class).filter(Boolean))].sort();
    const query = state.query.trim().toLowerCase();
    const rows = all.filter((row) => (!query || `${row.secid} ${row.name} ${row.issuer_name}`.toLowerCase().includes(query))
      && (state.structure === 'all' || row.structure_class === state.structure)
      && (state.analysis === 'all' || row.analysis_status === state.analysis)).sort(compare);
    const controls = `<div class="bav4-controls bav4-explorer-controls">
      <label class="bav4-search">Поиск<input type="search" data-bav4-control="query" value="${esc(state.query)}" placeholder="SECID, выпуск или эмитент"></label>
      <label>Структура<select data-bav4-control="structure"><option value="all">Все</option>${structures.map((v) => `<option value="${esc(v)}"${v === state.structure ? ' selected' : ''}>${esc(structureLabels[v] || v)}</option>`).join('')}</select></label>
      <label>Полнота<select data-bav4-control="analysis">${[['all','Все'],['FULL','Полная'],['PARTIAL','Частичная'],['UNSUPPORTED','Не поддерживается']].map(([v,l]) => `<option value="${v}"${v === state.analysis ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      <label>Сортировка<select data-bav4-control="sort">${[
        ['opportunity_score', 'Opportunity score'], ['liquidity_score', 'Ликвидность'],
        ['credit_quality_score', 'Кредитное качество'], ['carry_pct', 'Carry'],
        ['duration_years', 'Дюрация'],
      ].map(([value, label]) => `<option value="${value}"${value === state.sort ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
      <span class="bav4-found">${rows.length} из ${all.length}</span>
    </div>`;
    const visible = rows.slice(0, 150);
    const tableRows = visible.map((row) => {
      const p = primary(row);
      return `<tr><td><button class="bond-open-button" data-bond-open="${esc(row.secid)}">${identity(row)}</button><small>${esc(row.issuer_name || '')}</small></td>
        <td>${badge(structureLabels[row.structure_class] || row.structure_class)} ${row.qualified_only ? badge('Квал', 'warn') : ''}</td>
        <td>${badge(row.analysis_status, row.analysis_status === 'FULL' ? 'good' : 'warn')}</td><td class="tnum">${pct(row.clean_price_pct, 2)}</td>
        <td><b>${esc(p.value)}</b><small>${esc(p.label)}</small></td><td>${esc(row.rating || '—')}</td><td class="tnum">${num(row.liquidity_score, 0)}</td>
        <td>${esc(row.next_event_date || row.maturity_date || '—')}</td></tr>`;
    }).join('');
    const cards = visible.map((row) => {
      const p = primary(row);
      return `<article class="bav4-card"><header>${identity(row, 'md')}<button class="btn" data-bond-open="${esc(row.secid)}">Подробнее</button></header>
        <div class="bav4-badges">${badge(structureLabels[row.structure_class] || row.structure_class)}${badge(row.analysis_status, row.analysis_status === 'FULL' ? 'good' : 'warn')}</div>
        <dl><div><dt>Цена</dt><dd>${pct(row.clean_price_pct, 2)}</dd></div><div><dt>${esc(p.label)}</dt><dd>${esc(p.value)}</dd></div><div><dt>Рейтинг</dt><dd>${esc(row.rating || '—')}</dd></div><div><dt>Ликвидность</dt><dd>${num(row.liquidity_score, 0)}</dd></div></dl>
        <p>Следующее событие: <b>${esc(row.next_event_date || row.maturity_date || '—')}</b></p></article>`;
    }).join('');
    return `${controls}<div class="bav4-table bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Выпуск</th><th>Структура</th><th>Анализ</th><th>Цена</th><th>Ключевая метрика</th><th>Рейтинг</th><th>Ликвид.</th><th>Событие</th></tr></thead><tbody>${tableRows}</tbody></table></div><div class="bav4-cards">${cards}</div>${rows.length > 150 ? '<p class="muted">Показаны первые 150 результатов. Уточните фильтр.</p>' : ''}`;
  }

  function unavailable(text) {
    return `<div class="bav4-empty"><b>Данные v4 недоступны.</b><p>${esc(text)}</p></div>`;
  }

  function metricCard(label, value, note) {
    return `<div><span>${esc(label)}</span><b>${esc(value)}</b>${note ? `<small>${esc(note)}</small>` : ''}</div>`;
  }

  function detailKPIs(detail) {
    const cls = detail.structure_class || '';
    const fixed = [
      ['Чистая цена', pct(detail.market.clean_price_pct, 2)], ['Dirty', rub(detail.market.dirty_price_per_bond_rub)],
      ['YTM', pct(metric(detail, 'ytm_gross'), 2)], ['Modified duration', num(metric(detail, 'modified_duration'), 2)],
      ['DV01', rub(metric(detail, 'dv01'))], ['Z-spread', bp(metric(detail, 'z_spread'))],
    ];
    const floater = [
      ['Чистая цена', pct(detail.market.clean_price_pct, 2)], ['Текущий купон', pct(metric(detail, 'current_coupon_rate'), 2)],
      ['Discount Margin', bp(metric(detail, 'discount_margin'))], ['Effective duration', num(metric(detail, 'effective_duration'), 2)],
      ['Spread duration', num(metric(detail, 'spread_duration'), 2)], ['DV01', rub(metric(detail, 'dv01'))],
    ];
    const perpetual = [
      ['Чистая цена', pct(detail.market.clean_price_pct, 2)], ['Current Yield', pct(metric(detail, 'current_yield'), 2)],
      ['YTC if call', pct(metric(detail, 'ytc_if_call'), 2)], ['Call duration', num(metric(detail, 'call_case_duration'), 2)],
      ['Extension duration', num(metric(detail, 'extension_case_duration'), 2)], ['DV01', rub(metric(detail, 'dv01'))],
    ];
    return (cls === 'FLOATER' ? floater : cls === 'PERPETUAL_RESET' ? perpetual : fixed)
      .map(([label, value]) => metricCard(label, value)).join('');
  }

  function timelineHTML(detail) {
    const events = [{ date: detail.market.as_of || 'Сейчас', label: 'Сейчас' }];
    const option = detail.structure.optionality || {};
    (option.put_schedule || []).forEach((item) => events.push({ date: item.date, label: 'Put / оферта' }));
    (option.call_schedule || []).forEach((item) => events.push({ date: item.date, label: 'Call' }));
    const reset = detail.structure.coupon_model && detail.structure.coupon_model.next_reset_date;
    if (reset) events.push({ date: reset, label: 'Reset купона' });
    (detail.cashflows || []).filter((flow) => flow.principal > 0).forEach((flow) => events.push({ date: flow.date, label: 'Погашение номинала' }));
    const unique = events.filter((item, index) => events.findIndex((x) => x.date === item.date && x.label === item.label) === index).slice(0, 8);
    return `<div class="bav4-timeline">${unique.map((event, index) => `<div><i></i><b>${esc(event.label)}</b><span>${esc(event.date)}</span>${index < unique.length - 1 ? '<em></em>' : ''}</div>`).join('')}</div>`;
  }

  function cashflowHTML(detail) {
    const flows = (detail.cashflows || []).slice(0, 24);
    if (!flows.length) return '<p class="muted">Подтверждённого графика денежных потоков нет.</p>';
    const max = Math.max(...flows.map((f) => Number(f.amount) || 0), 1);
    return `<div class="bav4-cashflow-chart" aria-label="График денежных потоков">${flows.map((flow) => `<div title="${esc(flow.date)} · ${rub(flow.amount)}"><i style="--h:${Math.max(4, Number(flow.amount) / max * 100)}%" class="${flow.model_flag === 'projected' ? 'projected' : ''}"></i><span>${esc(flow.date.slice(0, 7))}</span></div>`).join('')}</div>`;
  }

  function scenarioHTML(detail) {
    const lab = detail.scenario_lab || {};
    if (lab.status !== 'CALCULATED') return '<p class="muted">Сценарная оценка недоступна: не хватает цены или дюрации.</p>';
    const axis = lab.curve_shocks_bp || [];
    const cells = (lab.cells || []).flat();
    const initial = cells.find((cell) => cell.curve_bp === 0 && cell.spread_bp === 0) || cells[0];
    return `<div class="bav4-scenario" data-bav4-scenario>
      <div class="bav4-scenario-inputs"><label>Сдвиг кривой, б.п.<input type="range" min="${axis[0]}" max="${axis[axis.length - 1]}" step="150" value="0" data-scenario="curve"><input type="number" min="${axis[0]}" max="${axis[axis.length - 1]}" step="150" value="0" data-scenario-number="curve"></label>
      <label>Сдвиг спреда, б.п.<input type="range" min="${axis[0]}" max="${axis[axis.length - 1]}" step="150" value="0" data-scenario="spread"><input type="number" min="${axis[0]}" max="${axis[axis.length - 1]}" step="150" value="0" data-scenario-number="spread"></label></div>
      <div class="bav4-scenario-result" data-scenario-result>${scenarioResult(initial)}</div>
      <div class="bav4-heatmap" style="--n:${axis.length}">${cells.map((cell) => `<button type="button" data-curve="${cell.curve_bp}" data-spread="${cell.spread_bp}" title="Кривая ${cell.curve_bp}; спред ${cell.spread_bp}"><span>${pct(cell.net_estimate_pct, 1)}</span><small>${rub(cell.future_dirty)}</small></button>`).join('')}</div>
      <p class="muted">${esc(lab.method_warning)} Breakeven combined shock: ${bp(lab.breakeven_combined_shock_bp)}. Издержки: ${bp(lab.assumed_costs_bp)}.</p>
      <script type="application/json" data-scenario-data>${JSON.stringify(cells).replace(/</g, '\\u003c')}</script></div>`;
  }

  function scenarioResult(cell) {
    if (!cell) return 'Нет сценария';
    return `${metricCard('Будущая dirty price', rub(cell.future_dirty))}${metricCard('Купонный доход', rub(cell.coupon_income))}${metricCard('Price P&L', rub(cell.price_pnl))}${metricCard('Gross TR', pct(cell.gross_total_return_pct, 2))}${metricCard('Net estimate', pct(cell.net_estimate_pct, 2))}`;
  }

  function detailHTML(detail, compact) {
    if (!detail || detail.schema_version !== '4.0') return unavailable('Карточка имеет несовместимую схему.');
    detail.structure_class = compact && compact.structure_class;
    const row = Object.assign({}, compact || {}, detail.identity || {});
    const warnings = (detail.warnings || []).map((value) => badge(value, 'warn')).join('');
    const rv = detail.relative_value || {};
    const flows = (detail.cashflows || []).slice(0, 20).map((flow) => `<tr><td>${esc(flow.date)}</td><td>${esc(flow.cashflow_type)}</td><td class="tnum">${rub(flow.coupon)}</td><td class="tnum">${rub(flow.principal)}</td><td>${esc(flow.model_flag || '')}</td></tr>`).join('');
    return `<div class="bond-detail bav4-detail"><header class="bond-detail-head"><div>${identity(row, 'lg')}<p class="bond-detail-ids">${esc(detail.secid)} · ${esc(detail.identity.isin || '')}</p><div class="bav4-badges">${badge(structureLabels[detail.structure_class] || detail.structure_class || 'Структура')}${badge(detail.analysis_status, detail.analysis_status === 'FULL' ? 'good' : 'warn')}${detail.structure.qualified_only ? badge('Квал', 'warn') : ''}${warnings}</div></div><button type="button" class="bond-detail-close" id="bond-detail-close" aria-label="Закрыть">✕</button></header>
      <div class="bond-detail-kpis">${detailKPIs(detail)}</div>
      <section><h3>Структура и события</h3>${timelineHTML(detail)}<p>${esc(detail.structure.coupon_model.type)} · ${esc(detail.structure.principal_model.type)} · ${esc(detail.structure.seniority)}</p></section>
      <section><h3>Relative Value</h3><div class="bav4-summary">${metricCard('Метрика', rv.metric || '—')}${metricCard('Значение', bp(rv.value))}${metricCard('Медиана peers', bp(rv.peer_median))}${metricCard('Excess', bp(rv.excess))}${metricCard('Процентиль', pct(rv.percentile))}${metricCard('Peer N', String(rv.peer_n == null ? '—' : rv.peer_n))}</div></section>
      <section><h3>Сценарный анализ</h3>${scenarioHTML(detail)}</section>
      <section><h3>Денежные потоки</h3>${cashflowHTML(detail)}<div class="bonds-table-wrap"><table class="bonds-table"><thead><tr><th>Дата</th><th>Тип</th><th>Купон</th><th>Номинал</th><th>Статус</th></tr></thead><tbody>${flows}</tbody></table></div></section>
      <section><h3>Ликвидность</h3><div class="bav4-summary">${metricCard('ADV 20', rub(detail.liquidity.adv_20_rub))}${metricCard('Торговых сессий', String(detail.liquidity.sessions_traded || '—'))}${metricCard('Bid / Ask', detail.liquidity.bid == null ? 'Нет глубины' : `${detail.liquidity.bid} / ${detail.liquidity.ask}`)}${metricCard('Depth', detail.liquidity.depth_status || '—')}</div><p>Slippage не рассчитывается без фактической глубины стакана.</p></section>
      <section><h3>Источники и метод</h3><dl class="bond-detail-dl"><div><dt>Рынок</dt><dd>${esc(detail.provenance.market || '—')}</dd></div><div><dt>Условия</dt><dd>${esc(detail.provenance.terms || 'Не подтверждены отдельно')}</dd></div><div><dt>Кривая</dt><dd>${esc((detail.provenance.curve || {}).source || '—')}</dd></div><div><dt>Сформировано</dt><dd>${esc(detail.provenance.generated_at || '—')}</dd></div></dl><p>OAS не публикуется без калиброванной стохастической модели опционов. Не ИИР.</p></section></div>`;
  }

  function bind(panel, redraw) {
    panel.querySelectorAll('[data-bav4-control]').forEach((control) => {
      const apply = () => {
        const key = control.dataset.bav4Control;
        state[key] = control.type === 'checkbox' ? control.checked : key === 'budget' ? Number(control.value) : control.value;
        redraw();
      };
      control.addEventListener(control.type === 'search' ? 'input' : 'change', apply);
    });
  }

  function bindScenario(container) {
    const root = container.querySelector('[data-bav4-scenario]');
    if (!root) return;
    const payload = JSON.parse(root.querySelector('[data-scenario-data]').textContent);
    const update = (curve, spread) => {
      const cell = payload.find((item) => item.curve_bp === curve && item.spread_bp === spread);
      if (cell) root.querySelector('[data-scenario-result]').innerHTML = scenarioResult(cell);
      ['curve', 'spread'].forEach((key) => {
        const value = key === 'curve' ? curve : spread;
        root.querySelector(`[data-scenario="${key}"]`).value = value;
        root.querySelector(`[data-scenario-number="${key}"]`).value = value;
      });
    };
    root.querySelectorAll('[data-scenario]').forEach((input) => input.addEventListener('input', () => update(
      Number(root.querySelector('[data-scenario="curve"]').value), Number(root.querySelector('[data-scenario="spread"]').value))));
    root.querySelectorAll('[data-scenario-number]').forEach((input) => input.addEventListener('change', () => {
      const values = [-300, -150, 0, 150, 300];
      const nearest = (value) => values.reduce((a, b) => Math.abs(b - value) < Math.abs(a - value) ? b : a);
      update(nearest(Number(root.querySelector('[data-scenario-number="curve"]').value)), nearest(Number(root.querySelector('[data-scenario-number="spread"]').value)));
    }));
    root.querySelectorAll('.bav4-heatmap button').forEach((button) => button.addEventListener('click', () => update(Number(button.dataset.curve), Number(button.dataset.spread))));
  }

  window.BondAnalyticsV4 = { state, opportunitiesHTML, explorerHTML, detailHTML, bind, bindScenario };
}());
