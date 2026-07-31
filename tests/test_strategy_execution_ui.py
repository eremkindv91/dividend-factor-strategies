from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_strategy_tabs_are_isolated_and_keyboard_accessible():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    assert 'id="momentum-panel"' in html
    assert '<option value="momentum">' in html
    assert "momentum.hidden = method !== 'momentum'" in app
    assert "marlamov.hidden = method !== 'marlamov'" in app
    assert "['ArrowLeft', 'ArrowRight', 'Home', 'End']" in app


def test_rejected_ml_candidate_is_non_executable_and_collapsed():
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    assert "candidate_portfolio" in app
    assert "published_portfolio" in app
    assert "Исследовательский кандидат — не используется для операций" in app
    assert "Расчётный вес" in app
    assert "Модель не уверена" not in app
    assert "candidate.diagnostic_turnover" not in app
    assert "из 4 оценены" in app
    assert "Веса production-модели используют только packs" in app


def test_dividend_empty_state_preserves_cash_and_watchlist_has_no_targets():
    app = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    assert "По ожидаемой чистой дивдоходности ни одна бумага не прошла порог" in app
    assert "100% капитала остаётся в cash/RFR" in app
    assert "expected_net_spread" in app
    assert "marlamovPortfolioCandidates" in app
