# -*- coding: utf-8 -*-
"""Контракт автообновления прогноза модели.

Контекст (29.07.2026): вкладка «Акции» показывала forecast_asof=2026-06-19 (40 дней)
и valuation_asof=2026-06-26 (33 дня), при том что цены и фундамент обновлялись
ежедневно. Причина — refresh.yml стоял на КВАРТАЛЬНОМ кроне и вдобавок ни разу не
отработал автоматически: единственный ручной прогон 19.06 был отменён.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "refresh.yml"


def _crons(text: str) -> list[str]:
    return [line.split('"')[1] for line in text.splitlines() if "- cron:" in line]


def test_refresh_runs_at_least_monthly():
    """Квартальный шаг давал прогнозу протухать до 3 месяцев — теперь месячный."""
    crons = _crons(WORKFLOW.read_text(encoding="utf-8"))
    assert crons, "у refresh.yml должно быть расписание, иначе обновление только руками"
    months = {c.split()[3] for c in crons}
    assert months == {"*"}, f"расписание не ежемесячное: {months}"


def test_refresh_avoids_round_minutes():
    """Круглая минута = длинная очередь GitHub (замер по news.yml: до +2,6 ч)."""
    for cron in _crons(WORKFLOW.read_text(encoding="utf-8")):
        assert int(cron.split()[0]) % 10 != 0, f"крон {cron} стоит на «круглой» минуте"


def test_refresh_push_has_retry():
    """Гонка с update/news/bonds роняла push и теряла переобучение."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git pull --rebase --autostash" in text
    assert "push не удался после 5 попыток" in text


def test_refresh_triggers_publication():
    """Свежий артефакт не должен ждать следующего планового прогона update.yml."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh workflow run update.yml" in text


def test_refresh_keeps_sanity_gates():
    """Переобучение обязано падать на мусорных данных, а не публиковать их."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "build_artifact.py --live" in text
    assert "build_valuations.py" in text
