# DOM-CONTRACT — инвентарь неприкасаемого (Итерация 1, §8.9)

Список селекторов, id, `data-*` и функций, завязанных на inline-обработчики и роутинг. Что можно
оборачивать/переименовывать при редизайне, а что **сломает поведение**. Проверено на ветке `redesign/audit`.

## 1. Роутинг (ломать нельзя)

- `location.hash` — 9 значений: `news | market | my-portfolio | stocks | strategies | bonds | cbr |
  methodology | pro`. Точки: `initRouter`, `openDetails`, `dividendDeepLink`, `applyDividendDeepLink`.
- Deep links дивкалендаря: `#market?calendar=dividends&ticker=SBER` → `dividendDeepLink()` /
  `applyDividendDeepLink()`. **Сохранить двусторонний маппинг.** Если вводится `overview` как алиас
  `market` — добавить маппинг в обе стороны и smoke-тест.
- Секции: 9 `<section class="app-section" data-section="…">`, переключение атрибутом `hidden`.
  **`data-section` значения не переименовывать** (на них завязан роутер и `openDetails`).

## 2. Inline-обработчики в строках innerHTML (КРИТИЧНО — только 2 функции)

Единственные функции, вызываемые из `onclick="…"` внутри `innerHTML` (grep подтвердил — их ровно 2):

| Функция | Где генерится | Правило |
|---|---|---|
| `setMarketSawIndex(id)` | `sawSwitcherHTML` (переключатель MCFTR/IMOEX) | **должна оставаться глобальной**; не переименовывать без правки строки |
| `openDividendCalendarTab(tab)` | вердикт-строка «Что впереди» | **должна оставаться глобальной** |

**Следствие для §6.2 (ES-модули):** вынести можно почти всё, КРОМЕ этих двух (или заменить inline
`onclick` на делегированные слушатели — тогда и их). Остальные 79 `addEventListener` — делегированные/
прямые, не завязаны на глобальные имена в разметке. Риск модуляризации ниже, чем в спеке опасались.

## 3. Мосты глобального состояния (не ломать имена)

- **Портфель:** `PFX_STATE` (результат `pfxCompute`, читается регрессией и рендером); `MY_PORTFOLIO_STORAGE_KEY
  = 'dividendFactorStrategies.myPortfolio.v1'` — **формат и ключ сохранить, иначе у пользователей пропадут
  данные** (отдельный обязательный тест §12).
- **Скринер:** `VIEW` (глобал, топ-N после фильтров; читается регрессией).
- **Данные:** глобалы `DATA, PF_RETURNS/RETURNS, SAW_DATA, MARLAMOV, QUALITY, SITE_FINANCIALS,
  SITE_STATUS, NEWS, EVENTS_DATA, DIVIDEND_CALENDAR, ALFA_INDEX, BONDS, CBR_DATA, MARKET_PE, MARKET_HISTORY`
  — хойстятся в топ (TDZ-защита, см. app.js:21). Ленивые загрузчики `load*(cb)`.
- **Графики/либы (window.\_\_):** `__pfxCharts, __bondsChart, __cbrChart, __bhChart, __bvalChart, __lwc,
  __cjs, __xlsx` — инстансы Chart.js/LWC и очереди ленивой загрузки. Не переиспользовать имена.
- Второй ключ localStorage: `DIVIDEND_CALENDAR_FILTER_KEY = 'dividendFactorStrategies.dividendCalendarFilters.v2'`.

## 4. Ключевые id (выборка; полный список — 221 `getElementById`, 55 `querySelector`)

`mp-input, mp-save, mp-out, mp-ticker-search, mp-add-*` (портфель-редактор); `market-pulse,
market-pe-card, market-kpi, market-signals, events-today, dividend-calendar(-body/-summary), marketsaw,
saw-body, alfa-index-card, market-chart-dialog/-canvas/-ohlc` (рынок); `cards, tbl` (скринер);
`pf, pfx-daily-risk, quality-drawer` (портфель/стратегии/акции); `bonds*, cbr*`.

**Правило:** id можно оборачивать в новые контейнеры, но **не переименовывать** без обновления всех
`getElementById`. Перед переименованием любого id/`data-section`/класса-hook — **остановись и спроси** (§3.2).

## 5. Санитайзеры (белый список для XSS-guard, §6.4)

`esc()` (экранирование `&<>"`), `newsSafeUrl()` / `alfaSafeUrl()` / `dividendSafeUrl()` (фильтр схем
против `javascript:`/`data:`), `ru()`/`PU()`/`PN()`/`PP()` (числа). Любая интерполяция `${…}` в
`innerHTML` обязана проходить через один из них. 132 `innerHTML` — поверхность для guard-эвристики.

## 6. `<dialog>` (менеджер фокуса)

`#market-chart-dialog` (`showModal()/close()`) — единственный текущий модал. При вводе единого modal-слоя
(§10) сохранить его id и поведение периодов/overlay, либо переносить с обновлением `openMarketChart`.
