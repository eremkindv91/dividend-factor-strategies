# FILES-TO-CHANGE — карта правок (Итерация 1, §8.7)

Оценка объёма и итерация. Правки CI запрещены, **кроме** `build.json` в манифест кэша (§6.1.4, отдельный
коммит). Python-пайплайны, JSON-схемы, математика — вне скоупа.

| Файл | Итерация | Объём | Что |
|---|---|---|---|
| `site/index.html` (489) | 2 | средний | app-shell (sidebar/topbar/bottom-nav), `defer` на app.js (§6.2), CSP-мета (§6.4), a11y `.pro-card`→`<button>` (§21), `<meta>` viewport safe-area |
| `site/styles.css` (2851) | 2, 7 | большой | токены+типографика+`tabular-nums` (2); компоненты `.app-shell/.sidebar/.metric-card/.skeleton/.confidence-badge/.method-chip/.unavailable-state` (2); консолидация 42→4 брейкпоинтов (7, высокий риск) |
| `site/app.js` (8188) | 2–6 | точечный | НЕ переписывать расчёт. Обёртка `loadJSON` + версия кэша (3); `taxProfile` + маркеры §5 (3); shell-роутинг+алиас overview (2); подача Портфель/Акции/Стратегии/Облигации (4–6). Держать в рамках <40% строк/заход (§3.2) |
| `site/build.json` (нов.) | 3 | нов. | `{"version":"<sha>"}` для кэш-манифеста |
| нов. CSS/JS модули токенов/компонентов | 2 | нов. | по §17 (без React/сборки) |
| `.github/workflows/*` | 3 | 1 строка | **только** запись `build.json` при деплое (§6.1.4), отдельный коммит |
| `tools/*`, `tests/*` | 0–7 | по мере | харнесс/baseline (Итерация 0 сделана; стратегии/облигации/банки — добавить в 6) |
| `docs/redesign/*` | 1, 7 | докум. | аудит (1); REPORT (7) |

**Не трогать:** `src/`, `scripts/`, `divmodel/`, `bonds/`(py), `news/`, `market_saw/`, `divmodel/`,
Telegram-бот, содержимое/схемы `*.json`, `MY_PORTFOLIO_STORAGE_KEY` формат.
