# Morning News Pipeline

Статический блок новостей для сайта. Клиентский JS читает готовый `news.json`; вызов Gemini
разрешён только в CI/локальном backend-скрипте, ключи во фронт не попадают.

## Runtime layout

- Source config: `news/channels.yml`
- Manual calendar: `news/calendar.yml`
- Prompt: `news/prompt.md`
- Local artifacts: `news/artifacts/` (ignored)
- Site JSON output: `site/news.json`

Pages в этом репозитории публикуется из ветки `gh-pages`. Полный deploy копирует файлы
из `site/`, поэтому `site/news.json` нужно явно включать в deploy whitelist.

## Local phase 1

```bash
python -m news.collectors.fetch_news --rss-only
python -m news.collectors.fetch_markets
python -m news.collectors.load_calendar
python -m news.generate_news --skip-collectors --dry-run
```

Полная генерация:

```bash
GEMINI_API_KEY=... python -m news.generate_news --skip-collectors
```

По умолчанию используется `GEMINI_MODEL=gemini-3.5-flash`; модель можно переопределить
через env `GEMINI_MODEL`, если в Google AI Studio актуальное имя изменится.

## Secrets

В GitHub Actions нужны только secrets, значения в repo не писать:

- `GEMINI_API_KEY`
- `TG_API_ID`
- `TG_API_HASH`
- `TG_SESSION`

`TG_SESSION` генерируется один раз локально через Telethon `StringSession`. Использовать
запасной Telegram-аккаунт: автоматическое чтение каналов может конфликтовать с ToS Telegram.
