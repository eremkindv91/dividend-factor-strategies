---
name: ship
description: "Процедура выката dividend-factor-strategies — проверка, коммит, деплой на gh-pages, live-верификация, rollback. Использовать после КАЖДОГО изменения кода/данных сайта, когда работа готова к публикации. Изменение не считается «готовым», пока не подтверждено на живом сайте этой процедурой."
---

# Ship — проверить, выкатить, подтвердить на live

## Purpose / Scope

Единственное место, где описано «как довести изменение до прода и доказать, что оно
там работает». Scope: верификация, коммит, деплой, live-проверка, откат. НЕ решает,
что строить (council/pm) и как писать код (principal-eng).

## When to invoke / When not

- Invoke: после каждого изменения site/*, scripts/*, workflows, данных.
- Not: правки только .claude/skills или memory/доков → просто commit+push без деплоя.

## Процедура (по порядку; шаг упал → стоп и чинить, дальше не идти)

**1. Синтаксис/тесты:** `node --check site/app.js`; `python3 -m py_compile` изменённых
скриптов; при затронутых пайплайнах — целевые `python3 -m pytest tests/test_<область>* -q`.

**2. Preview** (только если изменение видно в браузере):
- временный cache-bust `?v=dev*` в index.html (гоча: preview кеширует bare app.js);
- desktop: блок рендерится, интерактив работает, скриншот ключевого экрана;
- mobile 375: `scrollWidth <= clientWidth+2`, гриды в 1 колонку, таблицы скроллятся;
- `preview_console_logs level=error` — пусто;
- текст DOM без `NaN|Infinity|undefined`;
- **вернуть `?v=dev*` обратно на bare `app.js` ДО коммита** (проверить grep'ом).

**3. Коммит:** stage только свои файлы (не хвататься за чужие unstaged); сообщение =
что/почему/как проверено (+числа охвата для дата-правок); footer Co-Authored-By.

**4. Push:** `git -c rebase.autostash=true pull --rebase` → push в `dividend-site`.

**5. Деплой:** `gh workflow run update.yml --ref dividend-site` — ВСЕГДА вручную после
фичи (cron только будни; иначе изменение висит незадеплоенным). `gh run watch <id>
--exit-status` в background. Знать: concurrency-группа `gh-pages-publish` сериализует
публикации; отмена промежуточных ранов при серии коммитов — норма, финальный содержит всё.

**6. Live-верификация (обязательна, без неё «готово» не заявлять):**
- `index.html` на проде отдаёт `app.js?v=<sha8 HEAD>`;
- grep ключевой функции/строки фичи в опубликованном app.js;
- изменение данных → `python3 scripts/smoke_public_site.py --retries 4` (все OK) и/или
  curl конкретного JSON с проверкой значения;
- новый блок данных → он появился в `site_status.json` (иначе observability-дыра).

**7. Отчёт:** commit hash, live-версия, что подтверждено, что осталось.

## Rollback / Incident

- CI сам откатывает gh-pages на last-good при провале smoke (P0-слой) — не дублировать.
- Ручной откат: клон `gh-pages` → `git push -f <prev-good> HEAD:gh-pages`; битый блок
  данных → `scripts/restore_last_good_site_data.py --last-good <клон>`.
- Прод «stale/broken» в Data Health: сначала смотреть логи последнего рана Actions и
  `site_status.json`, только потом код. Выходные ≠ инцидент (см. quant-honesty, календарь).

## Blocking conditions

`?v=dev*` остался в index.html · упавший шаг 1 · заявление «готово» без шага 6 ·
push при неубранных чужих файлах в стейдже.

## Non-blocking warnings

Pages CDN лаг до пары минут — ретраи, не паника · smoke `--core-only` прошёл, полный
нет — можно публиковать, зафиксировать причину.

## Anti-patterns

«Локально работает — значит готово» · деплой без watch · правка данных без счётчиков
охвата в сообщении коммита · «исправлю в следующем коммите» для сломанного шага.

## Output contract / DoD / Interaction

DoD: все шаги пройдены, отчёт с hash+live-версией выдан. Вызывается после
principal-eng-реализации; council/роли не вызывает. Пример: фикс формулы → шаг 1
(+pytest) → preview не нужен (нет UI) → коммит с числами до/после → деплой → grep
новой формулы в live app.js + smoke → отчёт.
