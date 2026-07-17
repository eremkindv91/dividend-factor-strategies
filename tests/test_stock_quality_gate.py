from pathlib import Path

from scripts.build_data import classify_ranking_quality


ROOT = Path(__file__).parents[1]


def test_clean_complete_row_is_eligible_for_main_ranking():
    result = classify_ranking_quality("ok", [])

    assert result == {"status": "eligible", "eligible": True, "reasons": []}


def test_extreme_yield_payout_and_stale_price_require_review():
    result = classify_ranking_quality(
        "ok", ["y_exp_high", "payout_high", "price_stale", "unrelated_flag"]
    )

    assert result["status"] == "review"
    assert result["eligible"] is False
    assert result["reasons"] == ["payout_high", "price_stale", "y_exp_high"]


def test_incomplete_row_is_never_ranking_eligible():
    result = classify_ranking_quality("insufficient_data", [])

    assert result["status"] == "insufficient"
    assert result["eligible"] is False


def test_equities_ui_defaults_to_fail_closed_ranking_filter():
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")

    assert '<option value="rankable" selected>Основной рейтинг</option>' in html
    assert 'id="stock-quality-gate"' in html
    assert "function stockRankingEligible(t)" in app
    assert "payout выше 100%" in app
