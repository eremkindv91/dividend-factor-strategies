from __future__ import annotations

import argparse
import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import collect_alfa_index
from scripts.collect_alfa_index import (
    ArticleResult,
    IndexCandidate,
    apply_failure_state,
    build_current_payload,
    extract_article_content,
    extract_index_candidates,
    interpretation,
    is_stale,
    parse_article,
    previous_distinct_value,
    select_best_candidate,
    should_write_current,
    update_history,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "alfa_index"
URL = "https://alfabank.ru/alfa-investor/t/hod-torgov-15-07-2026/"
MSK = ZoneInfo("Europe/Moscow")


def config() -> dict:
    return json.loads((ROOT / "config" / "alfa_index.json").read_text(encoding="utf-8"))


def best(text: str, method: str = "articleBody") -> IndexCandidate:
    return select_best_candidate(extract_index_candidates(text, method), 7)


def article(value: int = 42, article_date: str = "2026-07-15", period: str = "morning") -> ArticleResult:
    return ArticleResult(
        url=f"https://alfabank.ru/alfa-investor/t/hod-torgov-{article_date[8:10]}-{article_date[5:7]}-{article_date[:4]}/",
        article_date=datetime.fromisoformat(article_date).date(),
        title="Проверенная статья",
        published_at=None,
        updated_at=None,
        body_method="articleBody",
        candidate=IndexCandidate(value, 13, "value_out_of_100", f"Альфа-Индекс {value}/100", 0, period),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Значение Альфа-Индекса на утро — 42/100.", 42),
        ("Альфа-Индекс — 42 / 100", 42),
        ("Альфа-Индекс — 42 балла из 100", 42),
        ("Альфа-Индекс составляет 42 пункта", 42),
        ("Альфа-Индекс вырос с 40 до 42 пунктов", 42),
        ("Альфа-Индекс снизился до 38 пунктов", 38),
        ("Значение Альфа Индекса — 42 из 100", 42),
    ],
)
def test_supported_publication_formats(text, expected):
    assert best(text).value == expected


@pytest.mark.parametrize("value", [0, 100])
def test_scale_boundaries_are_valid(value):
    assert best(f"Значение Альфа-Индекса на утро — {value}/100.").value == value


@pytest.mark.parametrize("value", [101, 145])
def test_values_outside_official_scale_are_rejected(value):
    candidates = extract_index_candidates(f"Значение Альфа-Индекса — {value}/100.", "articleBody")
    with pytest.raises(ValueError, match="no high-confidence"):
        select_best_candidate(candidates, 7)


def test_absent_index_is_rejected():
    with pytest.raises(ValueError, match="no high-confidence"):
        select_best_candidate(extract_index_candidates("IMOEX равен 2200", "article"), 7)


@pytest.mark.parametrize("dash", ["-", "‑", "–", "—", " "])
def test_dash_and_no_dash_variants(dash):
    assert best(f"Значение Альфа{dash}Индекса на утро — 42/100").value == 42


def test_jsonld_article_body_has_priority_and_metadata():
    html = (FIXTURES / "jsonld_42.html").read_text(encoding="utf-8")
    result = parse_article(html, URL, datetime(2026, 7, 15, 12, tzinfo=MSK), config())
    assert result.candidate.value == 42
    assert result.body_method == "articleBody"
    assert result.title == "Индекс МосБиржи опять снижается"
    assert result.published_at == "2026-07-15T09:00:00+03:00"


def test_article_body_ignores_footer_candidate():
    html = (FIXTURES / "article_38.html").read_text(encoding="utf-8")
    result = parse_article(html, URL, datetime(2026, 7, 15, 12, tzinfo=MSK), config())
    assert result.body_method == "article"
    assert result.candidate.value == 38


def test_multiple_numbers_and_mentions_select_morning_value():
    text = (
        "IMOEX 2115. Альфа-Индекс поднялся с 35 до 38. "
        "Значение Альфа-Индекса на утро — 42/100. Доходность ОФЗ 16,2%."
    )
    candidate = best(text)
    assert candidate.value == 42
    assert candidate.value_period == "morning"


def test_waf_page_is_not_article_content():
    html = (FIXTURES / "waf.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="WAF/error page"):
        extract_article_content(html)


def test_missing_article_date_is_rejected():
    html = (FIXTURES / "article_38.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="date missing"):
        parse_article(html, "https://alfabank.ru/alfa-investor/t/hod-torgov/", datetime(2026, 7, 15, tzinfo=MSK), config())


def test_future_article_is_rejected():
    html = (FIXTURES / "article_38.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="future"):
        parse_article(
            html,
            "https://alfabank.ru/alfa-investor/t/hod-torgov-16-07-2026/",
            datetime(2026, 7, 15, tzinfo=MSK),
            config(),
        )


def test_non_official_domain_is_rejected():
    html = (FIXTURES / "article_38.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="official"):
        parse_article(html, "https://example.com/hod-torgov-15-07-2026/", datetime(2026, 7, 15, tzinfo=MSK), config())


def test_history_is_deduplicated_by_date_value_and_period():
    first, changed = update_history([], article(), "2026-07-15T12:00:00+03:00", 365)
    second, changed_again = update_history(first, article(), "2026-07-15T13:00:00+03:00", 365)
    assert changed is True
    assert changed_again is False
    assert len(second) == 1
    assert second[0]["collected_at"] == "2026-07-15T12:00:00+03:00"


def test_previous_distinct_value_skips_repeated_workflow_values():
    history = [
        {"article_date": "2026-07-12", "value": 43, "value_period": "morning"},
        {"article_date": "2026-07-13", "value": 42, "value_period": "morning"},
        {"article_date": "2026-07-14", "value": 42, "value_period": "morning"},
    ]
    assert previous_distinct_value(history, 42, ("2026-07-15", 42, "morning")) == 43


def test_change_is_based_on_previous_distinct_value():
    history = [
        {"article_date": "2026-07-14", "value": 43, "value_period": "morning"},
        {"article_date": "2026-07-15", "value": 42, "value_period": "morning"},
    ]
    payload = build_current_payload(article(), history, None, datetime(2026, 7, 15, 12, tzinfo=MSK), config())
    assert payload["previous_distinct_value"] == 43
    assert payload["change"] == -1
    assert payload["direction"] == "down"


def test_weekday_stale_check_allows_previous_business_day():
    cfg = config()
    assert is_stale(datetime(2026, 7, 13).date(), datetime(2026, 7, 14, 15, tzinfo=MSK), cfg) is False
    assert is_stale(datetime(2026, 7, 10).date(), datetime(2026, 7, 14, 15, tzinfo=MSK), cfg) is True


def test_weekend_stale_check_accepts_friday_only():
    cfg = config()
    assert is_stale(datetime(2026, 7, 17).date(), datetime(2026, 7, 18, 15, tzinfo=MSK), cfg) is False
    assert is_stale(datetime(2026, 7, 16).date(), datetime(2026, 7, 18, 15, tzinfo=MSK), cfg) is True


def test_only_fetched_at_and_last_valid_at_do_not_trigger_write():
    old = {"value": 42, "status": "ok", "fetched_at": "2026-07-15T10:00:00+03:00", "last_valid_at": "old"}
    new = copy.deepcopy(old)
    new["fetched_at"] = "2026-07-15T11:00:00+03:00"
    new["last_valid_at"] = "new"
    assert should_write_current(old, new) is False


def test_status_change_triggers_write():
    old = {"value": 42, "status": "source_unavailable", "fetched_at": "old"}
    new = {"value": 42, "status": "ok", "fetched_at": "new"}
    assert should_write_current(old, new) is True


def test_source_failure_preserves_last_valid_value():
    old = {"value": 42, "source": {"url": URL}, "status": "ok", "stale": False}
    failed = apply_failure_state(old, "source_unavailable", datetime(2026, 7, 17, 15, tzinfo=MSK), "1.0.0")
    assert failed is not None
    assert failed["value"] == 42
    assert failed["source"]["url"] == URL
    assert failed["stale"] is True
    assert failed["status"] == "source_unavailable"


def test_failure_without_last_good_does_not_create_zero_or_null():
    assert apply_failure_state(None, "source_unavailable", datetime(2026, 7, 17, 15, tzinfo=MSK), "1.0.0") is None


def test_atomic_json_write_replaces_valid_file(tmp_path):
    target = tmp_path / "alfa-index.json"
    write_json_atomic(target, {"value": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 42}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    ("value", "zone"),
    [(0, "extreme_negative"), (20, "extreme_negative"), (21, "negative"), (41, "neutral"), (61, "positive"), (81, "extreme_positive"), (100, "extreme_positive")],
)
def test_site_interpretation_ranges(value, zone):
    assert interpretation(value)["zone"] == zone


def test_end_to_end_source_unavailable_keeps_last_good_files(tmp_path, monkeypatch):
    current_path = tmp_path / "data" / "alfa-index.json"
    history_path = tmp_path / "data" / "alfa-index-history.json"
    site_current = tmp_path / "site" / "alfa-index.json"
    site_history = tmp_path / "site" / "alfa-index-history.json"
    diagnostic = tmp_path / ".artifacts" / "diagnostic.json"
    last_good = {
        "value": 42,
        "source": {"url": URL, "article_date": "2026-07-15"},
        "status": "ok",
        "stale": False,
        "parser_version": "1.0.0",
    }
    write_json_atomic(current_path, last_good)
    write_json_atomic(history_path, [{"article_date": "2026-07-15", "value": 42, "value_period": "morning"}])

    async def blocked(*_args, **_kwargs):
        raise collect_alfa_index.DiscoveryError(
            "source_unavailable",
            "timeout/WAF",
            [{"url": URL, "status": 403, "error": "WAF"}],
        )

    monkeypatch.setattr(collect_alfa_index, "discover_latest_article", blocked)
    args = argparse.Namespace(
        config=ROOT / "config" / "alfa_index.json",
        current=current_path,
        history=history_path,
        site_current=site_current,
        site_history=site_history,
        diagnostic=diagnostic,
        now="2026-07-17T15:00:00+03:00",
        verbose=False,
    )

    assert asyncio.run(collect_alfa_index.collect(args)) == 0
    saved = json.loads(current_path.read_text(encoding="utf-8"))
    public = json.loads(site_current.read_text(encoding="utf-8"))
    assert saved["value"] == public["value"] == 42
    assert saved["status"] == "source_unavailable"
    assert saved["stale"] is True
    assert public["fetched_at"] == saved["fetched_at"]
    assert diagnostic.exists()


def test_tracked_public_payload_is_real_and_consistent():
    current = json.loads((ROOT / "site" / "alfa-index.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "site" / "alfa-index-history.json").read_text(encoding="utf-8"))
    assert current["value"] == 31
    assert current["previous_distinct_value"] == 34
    assert current["source"]["url"] == "https://alfabank.ru/alfa-investor/t/hod-torgov-17-07-2026/"
    assert [(row["article_date"], row["value"]) for row in history] == [
        ("2026-07-14", 43),
        ("2026-07-15", 42),
        ("2026-07-16", 34),
        ("2026-07-17", 31),
    ]
    assert all(row["source_url"].startswith("https://alfabank.ru/alfa-investor/t/hod-torgov-") for row in history)


def test_frontend_loads_public_payload_and_has_all_states():
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    assert 'id="alfa-index-card"' in html
    assert "alfa-index.json" in app and "alfa-index-history.json" in app
    assert "cache: 'no-store'" in app
    assert "source_unavailable" in app and "no_fresh_publication" in app
    assert "role=\"meter\"" in app
    assert "createPriceLine({ price: 50" in app
    assert ".alfa-index-card" in styles


def test_workflow_and_both_full_deploys_publish_alfa_index():
    workflow = (ROOT / ".github" / "workflows" / "update-alfa-index.yml").read_text(encoding="utf-8")
    update = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    manual = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")
    assert 'python-version: "3.12"' in workflow
    assert "workflow_dispatch:" in workflow and "schedule:" in workflow
    assert "scripts/collect_alfa_index.py" in workflow
    assert "pytest -q tests/test_alfa_index.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "data/alfa-index.json data/alfa-index-history.json" in workflow
    for deployment in (workflow, update, manual):
        assert "site/alfa-index.json" in deployment
        assert "site/alfa-index-history.json" in deployment


def test_site_validator_exposes_dedicated_alfa_contract():
    validator = (ROOT / "scripts" / "validate_site_data.py").read_text(encoding="utf-8")
    assert "def check_alfa_index" in validator
    assert '"alfa_index": [check_alfa_index]' in validator
