# COMPONENTS — дизайн-система редизайна

Статический дашборд (vanilla JS/CSS, без фреймворков и icon-фонтов). Все компоненты — в
`site/styles.css`; разметка оболочки — в `site/index.html`, динамическая — в `site/app.js`.
Идентичность собственная, не воспроизводит фирменный стиль Т-Инвестиций (§25).

## Токены (§16)

`:root` в начале `styles.css` (базовый слой) + добавочный слой редизайна:

- **Бренд:** `--c-brand-900..-050` (тёмно-синий → светлый), акцент `--accent` / `--accent-deep`.
- **Поверхности:** `--surface`, `--bg`, `--line` (границы), `--ink` / `--ink-soft` / `--ink-faint` (текст).
- **Семантика:** `--good-*` / `--warn-*` / `--risk-*` / `--neut-*` (fill/ink/bar) — для вердиктов и статусов.
- **Ритм:** `--s-1..-8` (spacing 4–32px), радиусы `--r-control` (10) / `--r-card` (16) / `--r-pill` (999).
- **Числа:** `font-variant-numeric: tabular-nums` на `.tnum` и ключевых числовых контекстах.

## Оболочка приложения (Итерация 2)

- **`.app-shell`** — grid `sidebar | content`. На ≤980px → одна колонка, sidebar скрыт, показана нижняя навигация.
- **`.app-sidebar`** — бренд + сгруппированная навигация (`.app-nav .section-tab` с inline-SVG `.nav-ico`).
  Свёртка в icon-rail: `.app-shell.sidebar-collapsed` (состояние в `dfs.ui.v1.sidebar`).
- **`.app-topbar`** — sticky: заголовок раздела (`#topbar-title` из `SECTION_META`), подзаголовок,
  свежесть данных (`#data-status`), бейдж «Не ИИР» (`.app-nir`).
- **`.app-bottomnav`** (моб.) + **`.app-more-sheet`** («Ещё» → Новости/Облигации/Банки/Методология/О проекте).
- Навигационные кнопки сохраняют класс `.section-tab` + `data-section` → JS-роутинг (`setActiveSection`/`initRouter`) не изменён.

## Данные и состояния

- **`dataURL(path)`** — статичные JSON грузятся как `path?v=<build.json.version>` → versioned-кэш (§6.1).
- **`.skeleton`** (`.skeleton-row` / `.skeleton-block`) — shimmer-плейсхолдеры; гаснут при `prefers-reduced-motion`.
- **`uiStateLoad/Save`** — версионируемый `dfs.ui.v1` (sidebar, taxRate, pfxTab, stockView); портфель `MY_PORTFOLIO_STORAGE_KEY` не тронут.

## Компоненты разделов

- **Налоговый профиль** (`.tax-profile` + `.tax-opt`) — сегмент 13/15/0%, фактор из таблицы точных литералов.
- **Portfolio X-Ray** (Итерация 4): `.pfx-committee` (итог), `.pfx-kpistrip` (headline KPI, моб. h-scroll),
  `.pfx-alerts` (что требует внимания), `.pfx-tabs` + `.pfx-tabpanel` (7 вкладок, ленивый рендер + графики),
  `.pfx-kpi` / `.pfx-mod` (модули, таблицы скроллятся внутри `.pfx-mod-body`), `.mp-empty-rich` (пустое состояние).
- **Акции** (Итерация 5): `.stock-chips` (быстрые фильтры → `SHOWN`, VIEW-контракт цел), `.stock-view-toggle`
  (Таблица/Карточки/Карта, `data-stock-view` на секции), `.table-card` / `.cards` / `#map`.
- **Облигации** (Итерация 6): `.b-mark` (маркер «чистая»), `.bonds-limits` («Ограничения данных §5.6»).

## Доступность (§21)

Semantic HTML · `aria-current` на активном разделе · `aria-selected` на вкладках стратегий/X-Ray ·
`role="tablist"/"tab"/"tabpanel"` · видимый `:focus-visible` (клавиатурный, не мышиный) ·
`<dialog>` через `showModal()` (Escape + focus-trap) + возврат фокуса на триггер · `label` у input ·
touch targets ≥44–48px в нижней навигации · `prefers-reduced-motion` гасит все анимации/переходы.

## Ограничения дизайн-системы (честно)

- **CSP** (`index.html`, §6.4): `script-src` допускает `cdn.jsdelivr.net` + `unpkg.com` (оттуда on-demand
  грузятся Chart.js и LightweightCharts), `style-src` допускает `'unsafe-inline'` (~44 динамических
  inline-стиля в шаблонах: ширины баров, цвета). Идеал `'self'`-only не достигнут без вендоринга
  библиотек и рефактора inline-стилей — осознанный компромисс.
- **Inline-обработчики (`onclick=` и т.п.) запрещены** — CSP их молча блокирует (так сломался
  переключатель MCFTR/IMOEX). Вешать делегированные слушатели на `document` по `data-`атрибуту
  (см. `initRouter`: `data-saw-index`, `data-divcal-tab`, `data-goto`). Гард: `node tools/xss-guard.js`
  падает с exit 1, если inline-обработчик появился снова.
- **Стили дописаны блоками** в конец `styles.css` по итерациям — не единый модульный слой.
- **XSS-guard** (`tools/xss-guard.js`) — эвристика, не полный анализ потоков (см. шапку скрипта).
