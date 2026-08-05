# -*- coding: utf-8 -*-
"""Два списка публикации в gh-pages обязаны совпадать.

Контекст (30.07.2026): файлы сайта копируются в gh-pages ДВАЖДЫ и НЕЗАВИСИМО —
инлайн-списком в `.github/workflows/update.yml` (шаг «Публикация site/ → gh-pages»)
и скриптом `scripts/deploy_ghpages.sh` для ручного выката. Списки живут отдельно,
поэтому расходятся молча.

Как это проявилось: новый `site/macro_cbr.json` был добавлен только в скрипт, CI его
не копировал → на проде HTTP 404 при том, что пайплайн честно отработал и написал в лог
«записано: site/macro_cbr.json». Проверка списков заодно вскрыла, что ручной скрипт УЖЕ
отставал на три файла (`market_pe_current.json`, `_fallback.json`, `cbr/`) — то есть
ручной деплой выкидывал карточку P/E рынка и данные вкладки «Банки РФ».

Тест сравнивает множества файлов из обоих механизмов. Добавляешь новый JSON — добавляй
в оба места, иначе здесь красный.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "update.yml"
SCRIPT = ROOT / "scripts" / "deploy_ghpages.sh"

# `cp site/a.json site/b.json "$TMP/"` и `cp -r site/cbr "$TMP/"`
CP = re.compile(r"cp\s+(?:-r\s+)?(site/[\w./-]+(?:\s+site/[\w./-]+)*)")


def published_files(text: str) -> set[str]:
    out: set[str] = set()
    for match in CP.finditer(text):
        for token in match.group(1).split():
            if token.startswith("site/"):
                out.add(token.rstrip("/"))
    return out


def test_both_publish_mechanisms_copy_the_same_files():
    from_workflow = published_files(WORKFLOW.read_text(encoding="utf-8"))
    from_script = published_files(SCRIPT.read_text(encoding="utf-8"))
    assert from_workflow, "не удалось разобрать список файлов из update.yml"
    assert from_script, "не удалось разобрать список файлов из deploy_ghpages.sh"

    only_ci = sorted(from_workflow - from_script)
    only_manual = sorted(from_script - from_workflow)
    assert not only_ci and not only_manual, (
        "списки публикации разошлись — файл окажется 404 на проде или пропадёт при ручном выкате.\n"
        f"  только в update.yml:          {only_ci or '—'}\n"
        f"  только в deploy_ghpages.sh:   {only_manual or '—'}"
    )


def test_data_files_used_by_frontend_are_published_somewhere():
    """Каждый JSON, который фронт грузит через dataURL(), должен кем-то публиковаться.

    Ловит обратный случай: файл добавили в загрузку на фронте, но нигде не выкладывают —
    блок молча уходит в «недоступно» вместо того, чтобы работать.

    Публикацию ведут НЕСКОЛЬКО воркфлоу, и это нормальная архитектура: свои данные
    additive-пушат market-saw-update.yml (marketsaw_*), bonds_update.yml (bonds/*),
    dividend_calendar.yml, news.yml, update-cbr-banks.yml. Поэтому ищем по всем, а не
    только в update.yml — иначе тест краснел бы на здоровом коде.
    """
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    requested = set(re.findall(r"dataURL\(['\"]([\w./-]+\.json)['\"]\)", app))

    published: set[str] = set()
    dirs: set[str] = set()
    sources = list((ROOT / ".github" / "workflows").glob("*.yml")) + [SCRIPT]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for item in published_files(text):
            rel = item.split("/", 1)[1]
            (dirs if "." not in rel.split("/")[-1] else published).add(rel)
        # additive-пуш перечисляет файлы просто по имени в `for f in a.json b.json`
        for chunk in re.findall(r"for f in ([\w. /-]+\.json[\w. /-]*);", text):
            published.update(x for x in chunk.split() if x.endswith(".json"))

    def covered(name: str) -> bool:
        if name in published:
            return True
        return any(name.startswith(d.rstrip("/") + "/") for d in dirs)   # копия каталога

    # site_status.json/build.json пишутся отдельными шагами уже ВНУТРИ каталога
    # публикации, поэтому в списках cp их нет и быть не должно.
    side_channel = {"site_status.json", "build.json"}
    missing = sorted(f for f in requested if f not in side_channel and not covered(f))
    assert not missing, (
        "фронт грузит, но НИ ОДИН воркфлоу не публикует: " + ", ".join(missing))


def test_every_local_asset_referenced_by_index_is_published():
    """Файл, подключённый в index.html, обязан попадать в публикацию.

    Сравнение двух списков публикации между собой этого не ловит: если файл забыт в
    ОБОИХ, тест проходит, а на проде получается 404. Так уже случилось дважды —
    macro_cbr.json и bond_retail.js (весь retail-слой облигаций молча не работал).
    """
    root = Path(__file__).resolve().parents[1]
    html = (root / "site" / "index.html").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    manual = (root / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")

    # локальные скрипты и стили (внешние CDN-ссылки не публикуем)
    assets = set(re.findall(r'(?:src|href)="([\w./-]+\.(?:js|css))"', html))
    local = {a for a in assets if not a.startswith(("http", "//"))}
    assert local, "в index.html должны быть локальные ассеты — иначе тест бессмысленен"

    for asset in sorted(local):
        name = asset.split("/")[-1]
        assert name in workflow, f"{name} подключён в index.html, но не публикуется в update.yml"
        assert name in manual, f"{name} подключён в index.html, но не публикуется в deploy_ghpages.sh"
