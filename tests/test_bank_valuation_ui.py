from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_bank_ui_names_form_123_ratio_as_regulatory_capital_not_book_value():
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")

    assert "P/капитал ЦБ" in html
    assert "P/капитал ЦБ" in app
    assert "P/BV ${pbv}" not in app
    assert "Это не IFRS P/BV" in app
    assert "const BVAL_TOOLTIPS" in app
    assert "function bvalWarnings(bank)" in app


def test_bank_builder_metadata_discloses_form_123_denominator():
    builder = (ROOT / "scripts/cbr_banks/build_banks_valuation.py").read_text(
        encoding="utf-8"
    )
    history = (ROOT / "scripts/cbr_banks/build_banks_history.py").read_text(
        encoding="utf-8"
    )

    assert "регуляторный капитал формы 123 ЦБ РФ" in builder
    assert "P/капитал ЦБ не равен IFRS P/BV" in builder
    assert "P/капитал ЦБ(t)" in history
