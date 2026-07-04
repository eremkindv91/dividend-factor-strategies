import json
from pathlib import Path

import yaml

from news.generate_news import DEFAULT_MODEL, validate_news_json


ROOT = Path(__file__).resolve().parents[1]


def test_news_prompt_and_handoff_files_exist():
    assert (ROOT / "claude_code_news_tab_brief.md").exists()
    prompt = (ROOT / "news" / "prompt.md").read_text(encoding="utf-8")
    assert "{OVERNIGHT_MARKETS}" in prompt
    assert "{NEWS_BLOB}" in prompt
    assert "{TODAY_CALENDAR}" in prompt
    assert "Только факты из входа" in prompt


def test_news_channels_have_tiers_and_sources():
    data = yaml.safe_load((ROOT / "news" / "channels.yml").read_text(encoding="utf-8"))
    assert data["telegram"]
    assert data["rss"]
    for row in data["telegram"] + data["rss"]:
        assert row.get("tier") in {"A", "B"}
    assert any(row.get("name") == "Ведомости" for row in data["rss"])


def test_news_json_contract_minimal_valid():
    payload = {
        "date": "2026-07-04",
        "generated_at": "2026-07-04T08:30:00+03:00",
        "session_open": "10:00 MSK",
        "external_backdrop": "",
        "market_snapshot": [
            {"name": "S&P 500", "value": "100.00", "change_pct": "+0.10%", "as_of": "2026-07-03", "group": "global_equity"}
        ],
        "overnight": [
            {
                "id": "test-news",
                "headline": "Тестовая новость подтверждена источником",
                "context": "",
                "category": "markets",
                "investment_relevant": False,
                "importance": 1,
                "sources": [{"name": "Источник", "url": ""}],
                "published_at": "2026-07-04T07:00:00+03:00",
            }
        ],
        "yesterday": [],
        "today_agenda": [
            {"time": "10:00", "event": "Открытие торгов", "ticker": "", "type": "macro", "importance": 1}
        ],
    }
    assert validate_news_json(json.loads(json.dumps(payload))) == payload


def test_news_default_model_is_overridable_flash_model():
    assert "flash" in DEFAULT_MODEL


def test_news_workflow_uses_secrets_and_publishes_additively():
    workflow = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")

    assert 'cron: "30 5 * * 1-5"' in workflow
    assert "python -m news.generate_news" in workflow
    assert "python scripts/validate_site_data.py news" in workflow
    assert "site/news.json" in workflow
    assert "gh-pages" in workflow
    assert "GITHUB_TOKEN" in workflow
    for secret_name in ("GEMINI_API_KEY", "TG_API_ID", "TG_API_HASH", "TG_SESSION"):
        assert f"secrets.{secret_name}" in workflow

    forbidden_literals = ("fake", "dummy", "changeme", "test-key")
    assert not any(token in workflow.lower() for token in forbidden_literals)


def test_full_deploy_whitelists_news_json():
    update = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    manual = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")

    assert "site/news.json" in update
    assert "site/news.json" in manual


def test_validate_site_data_has_news_contract():
    validator = (ROOT / "scripts" / "validate_site_data.py").read_text(encoding="utf-8")

    assert "def check_news" in validator
    assert '"news.json"' in validator
    assert '"market_snapshot"' in validator
    assert '"today_agenda"' in validator
