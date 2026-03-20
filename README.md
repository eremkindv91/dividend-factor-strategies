# Прогноз дивидендов и построение факторных стратегий: сопоставление рынков РФ и Японии

> **Магистерская диссертация** | НИУ ВШЭ, программа «Инвестиции на финансовых рынках», 2026

**Автор:** Еремкин Дмитрий Валерьевич
**Научный руководитель:** д.э.н., проф. Теплова Тамара Викторовна
**Кафедра:** Фондового рынка и рынка инвестиций

---

## Аннотация

Исследование сопоставляет дивидендные рынки России и Японии по трём направлениям: (1) бэктестинг факторных стратегий (Fama–French 5-factor), (2) декомпозиция фактора качества (Quality) на уровне дескрипторов, (3) ML-прогнозирование дивидендной политики с помощью ансамблевых моделей (CatBoost, XGBoost, LightGBM, Random Forest) и сравнение с линейным кросс-секционным подходом Фамы–Макбета. Ключевые результаты: на российском рынке 4 признака дивидендной истории объясняют 99,6% предсказательной силы полной модели (146 признаков), а преимущество ML над линейной моделью проявляется только на японском рынке (+6,6 п.п. ROC-AUC).

---

## Оглавление

- [Структура репозитория](#структура-репозитория)
- [Ноутбуки](#ноутбуки)
  - [01. Факторные стратегии — бэктест](#01-факторные-стратегии--бэктест)
  - [02. Фактор качества — декомпозиция](#02-фактор-качества--декомпозиция)
  - [03. ML-прогнозирование дивидендов](#03-ml-прогнозирование-дивидендов)
- [Данные](#данные)
- [Быстрый старт](#быстрый-старт)
- [Результаты](#результаты)
- [Цитирование](#цитирование)

---

## Структура репозитория

```
dividend-factor-strategies/
├── README.md
├── requirements.txt
├── .gitignore
├── data/                          # Исходные данные
│   ├── рф_ПАНЕЛЬ_enriched.xlsx    # Панель российских эмитентов (2011-2025)
│   ├── JP_PANEL_enriched.xlsx     # Панель японских эмитентов (2011-2025)
│   ├── russian_stocks_adjusted.xlsx
│   ├── nikkei225_monthly_returns.csv
│   ├── moex_total_return_monthly.xlsx
│   ├── руония_мес_2011_2025.xlsx  # Безрисковая ставка (RUONIA)
│   ├── индекс_мосбиржи_полн_дох.csv
│   └── ИНДЕКС nikkei225_TR.xlsx
├── notebooks/
│   ├── 01_factor_backtest.ipynb   # Бэктест факторных стратегий
│   ├── 02_quality_factor_research.ipynb  # Декомпозиция Quality
│   └── 03_ml_dividend_forecast.ipynb     # ML-прогнозирование
└── results/
    ├── factor_backtest/           # Таблицы и графики бэктеста
    ├── quality_research/          # Результаты Quality-декомпозиции
    ├── ml_forecast_v5/            # Прогнозы ML (OOF, SHAP, рейтинги)
    └── thesis_figures/            # Публикационные фигуры для диплома
```

---

## Ноутбуки

### 01. Факторные стратегии — бэктест

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/eremkindv91/dividend-factor-strategies/blob/main/notebooks/01_factor_backtest.ipynb)

| | |
|---|---|
| **Файл** | `notebooks/01_factor_backtest.ipynb` |
| **Задача** | Бэктест 5-факторной модели Fama–French на рынках РФ и Японии (2011–2025) |
| **Факторы** | SMB, HML, RMW, WML, CMA |
| **Стратегии** | Long-Only Top-20, Long-Short 2×3 |
| **Данные** | `рф_ПАНЕЛЬ_enriched.xlsx`, `JP_PANEL_enriched.xlsx`, MOEX TR, Nikkei 225 TR, RUONIA |
| **Выход** | Кумулятивные кривые доходности, альфа-тесты, сравнительные таблицы |

### 02. Фактор качества — декомпозиция

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/eremkindv91/dividend-factor-strategies/blob/main/notebooks/02_quality_factor_research.ipynb)

| | |
|---|---|
| **Файл** | `notebooks/02_quality_factor_research.ipynb` |
| **Задача** | Декомпозиция фактора Quality на дескрипторы по методологии Barra |
| **Метод** | IC-анализ, Fama–MacBeth регрессия, квинтильная сортировка |
| **Данные** | `рф_ПАНЕЛЬ_enriched.xlsx`, `JP_PANEL.xlsx`, бенчмарки |
| **Выход** | Таблицы Part I (Baseline) и Part II (Quality), графики IC и квинтилей |

### 03. ML-прогнозирование дивидендов

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/eremkindv91/dividend-factor-strategies/blob/main/notebooks/03_ml_dividend_forecast.ipynb)

| | |
|---|---|
| **Файл** | `notebooks/03_ml_dividend_forecast.ipynb` |
| **Задача** | Двухэтапная модель: (1) классификация P(выплата), (2) регрессия DPS |
| **Модели** | CatBoost, XGBoost, LightGBM, Random Forest, Logistic Regression |
| **Валидация** | Walk-forward expanding window CV (2016–2025) |
| **Данные** | `рф_ПАНЕЛЬ_enriched_BACKUP2.xlsx`, `JP_PANEL_enriched.xlsx` |
| **Выход** | OOF-прогнозы, аблационное исследование, рейтинг 2026, SHAP |

**Ключевые результаты:**

| Метрика | Россия | Япония |
|---------|--------|--------|
| Лучшая модель | XGBoost | CatBoost |
| ROC-AUC | 0,904 | 0,979 |
| Фама–Макбет (baseline) | 0,905 | 0,913 |
| ML vs FM | −0,001 | **+0,066** |

---

## Данные

| Файл | Описание | Строк | Период |
|------|----------|-------|--------|
| `рф_ПАНЕЛЬ_enriched.xlsx` | Панель 241 российского эмитента: МСФО + дивиденды + макро | ~3 000 | 2011–2025 |
| `JP_PANEL_enriched.xlsx` | Панель ~500 японских эмитентов (Nikkei 225 + расширение) | ~7 000 | 2011–2025 |
| `russian_stocks_adjusted.xlsx` | Скорректированные цены акций РФ (месячные) | — | 2010–2025 |
| `nikkei225_monthly_returns.csv` | Месячные доходности компонентов Nikkei 225 | — | 2010–2025 |
| `moex_total_return_monthly.xlsx` | Индекс MOEX полной доходности (месячный) | — | 2005–2025 |
| `руония_мес_2011_2025.xlsx` | Безрисковая ставка RUONIA (месячная) | — | 2011–2025 |
| `ИНДЕКС nikkei225_TR.xlsx` | Индекс Nikkei 225 Total Return | — | 2010–2025 |

---

## Быстрый старт

### Локальный запуск

```bash
# 1. Клонируйте репозиторий
git clone https://github.com/dremkin/dividend-factor-strategies.git
cd dividend-factor-strategies

# 2. Создайте виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# 3. Установите зависимости
pip install -r requirements.txt

# 4. Запустите Jupyter
jupyter notebook notebooks/
```

### Google Colab

Нажмите бейдж **Open In Colab** у нужного ноутбука — данные загрузятся автоматически из репозитория.

---

## Результаты

Все результаты сохранены в папке `results/` и видны на GitHub без запуска ноутбуков:

- **`results/factor_backtest/`** — таблицы факторных стратегий, кривые доходности
- **`results/quality_research/`** — декомпозиция Quality, IC-анализ
- **`results/ml_forecast_v5/`** — OOF-прогнозы, SHAP, рейтинг дивидендов 2026
- **`results/thesis_figures/`** — 6 публикационных фигур для магистерской

---

## Цитирование

```
Еремкин Д. И. Прогноз дивидендов и построение факторных стратегий:
сопоставление рынков РФ и Японии. Магистерская диссертация.
НИУ ВШЭ, программа «Финансовый аналитик», 2025.
```

---

## Лицензия

Данный репозиторий создан в образовательных целях как приложение к магистерской диссертации. Использование данных и кода допускается с указанием авторства.
