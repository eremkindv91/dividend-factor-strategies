import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.preserve_freshest_news import preserve_freshest


def payload(generated_at: str, headline: str) -> dict:
    return {
        "generated_at": generated_at,
        "market_snapshot": [{"name": "IMOEX"}],
        "overnight": [{"headline": headline}],
        "yesterday": [],
        "today_agenda": [],
    }


def write(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_preserves_newer_already_published_news(tmp_path):
    old = datetime(2026, 7, 10, 3, tzinfo=timezone.utc)
    candidate = tmp_path / "site" / "news.json"
    published = tmp_path / "gh-pages" / "news.json"
    candidate.parent.mkdir()
    published.parent.mkdir()
    write(candidate, payload(old.isoformat(), "old"))
    write(published, payload((old + timedelta(hours=2)).isoformat(), "new"))

    selected = preserve_freshest(candidate, published)

    assert selected == "published"
    assert json.loads(candidate.read_text())["overnight"][0]["headline"] == "new"


def test_keeps_newer_local_generation(tmp_path):
    now = datetime(2026, 7, 10, 6, tzinfo=timezone.utc)
    candidate = tmp_path / "news.json"
    published = tmp_path / "published.json"
    write(candidate, payload(now.isoformat(), "local"))
    write(published, payload((now - timedelta(hours=1)).isoformat(), "remote"))

    assert preserve_freshest(candidate, published) == "candidate"
    assert json.loads(candidate.read_text())["overnight"][0]["headline"] == "local"


def test_compares_timezone_less_generation_without_crashing(tmp_path):
    candidate = tmp_path / "news.json"
    published = tmp_path / "published.json"
    write(candidate, payload("2026-07-10T06:00:00", "local"))
    write(published, payload("2026-07-10T05:00:00+00:00", "remote"))

    assert preserve_freshest(candidate, published) == "candidate"


def test_rejects_two_invalid_news_files(tmp_path):
    candidate = tmp_path / "news.json"
    published = tmp_path / "published.json"
    candidate.write_text("{}", encoding="utf-8")
    published.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="no valid"):
        preserve_freshest(candidate, published)
