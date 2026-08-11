from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai_research_smoke.yml"


def _workflow() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    payload = yaml.load(text, Loader=yaml.BaseLoader)
    return text, payload


def test_ai_smoke_workflow_is_manual_only_and_read_only():
    text, payload = _workflow()
    assert set(payload["on"]) == {"workflow_dispatch"}
    assert payload["permissions"] == {"contents": "read"}
    assert "schedule:" not in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "workflow_run:" not in text
    assert "git push" not in text
    assert "actions/deploy-pages" not in text
    assert "peaceiris/actions-gh-pages" not in text


def test_ai_smoke_workflow_is_secret_safe_and_bounded():
    text, _payload = _workflow()
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in text
    assert 'AI_BILLING_ALLOWED: "false"' in text
    assert 'AI_REAL_GEMINI_SMOKE_AUTHORIZED: "true"' in text
    assert 'MAX_STOCK_MEMOS_PER_RUN: "3"' in text
    assert "--tickers SBER,GAZP,YDEX" in text
    assert "--require-real" in text
    assert "actions/upload-artifact@v4" in text
    assert "actions/upload-pages-artifact" not in text
    assert "site/data/research/ai" not in text.split("Upload sanitized smoke artifacts", 1)[1]
