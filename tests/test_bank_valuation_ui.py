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


def test_required_return_comes_from_the_model_not_from_a_slider():
    """Требуемая доходность у каждого банка своя и считается моделью.

    Раньше на её месте стоял общий ползунок «COE 20%» — одно произвольное число
    на весь сектор, которым можно было подогнать вывод под любой ответ. Ползунка
    больше нет ни в разметке, ни в состоянии, ни в расчётах.
    """
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")
    css = (ROOT / "site/styles.css").read_text(encoding="utf-8")

    assert "function bvalRequiredReturn(ticker)" in app
    assert "cost_of_equity.cost_of_equity" in app, (
        "ставка обязана приходить из модели остаточного дохода")

    for gone in ("BVAL_COE", "bvalCoeUpdate", "bvalRoeLineDiscount", "bvalScatterDraw",
                 'id="bval-coe"', "bval-scatter"):
        assert gone not in app, f"остаток ползунка COE или ROE-линии в app.js: {gone}"
    for gone in (".bval-coe", ".bval-scatter"):
        assert gone not in css, f"остаток стилей ползунка COE: {gone}"


def test_missing_required_return_shows_nd_instead_of_a_substitute():
    """Модель не оценивает банк — сравнивать не с чем, и подставлять нечего."""
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")

    start = app.index("function bvalRequiredReturn(ticker)")
    fn = app[start:app.index("\n}", start)]
    assert "return isNum(coe) ? coe * 100 : null" in fn, (
        "отсутствие ставки обязано возвращать null, а не значение по умолчанию")
    assert "Требуемая доходность для этого банка моделью не считается" in app
