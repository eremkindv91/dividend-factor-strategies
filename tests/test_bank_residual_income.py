#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты ядра Residual Income (§21.1, §22 спеки).

Лежат в tests/ под pytest СОЗНАТЕЛЬНО: два прежних банковских тестовых файла
написаны на unittest и запускаются отдельными шагами CI, поэтому не попадают в
общий прогон `pytest tests/`. Новые тесты не должны повторить эту судьбу.

Проверяется не «код не падает», а экономический смысл: при ROE = COE банк стоит
ровно капитала, сверхдоходность добавляет стоимость, недоходность отнимает.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "scripts" / "cbr_banks"
sys.path.insert(0, str(CORE))

from cost_of_equity import (adjusted_beta, cost_of_equity, issuer_premium,  # noqa: E402
                            liquidity_premium, raw_beta, scenarios, winsorize)
from forecast import (build_forecast, clean_surplus_check, normalized_roe,  # noqa: E402
                      roe_path, sustainable_growth)
from residual_income import (justified_pbv_single_stage, residual_income,  # noqa: E402
                             terminal_value, value_equity)

BV0 = 1_000_000_000.0
KE = 0.18


def _valuation(roe_start, roe_term, payout=0.5, ke=KE, years=5, g=None):
    rows = build_forecast(BV0, roe_start, roe_term, payout, ke, years, 0.35, 2026)
    growth = sustainable_growth(roe_term, payout) if g is None else g
    return value_equity(BV0, rows, roe_term, ke, growth)


# ── §21.1 базовые математические свойства ────────────────────────────────────

def test_residual_income_is_zero_when_roe_equals_cost_of_equity():
    """Опорное тождество всей модели."""
    assert residual_income(0.18 * BV0, BV0, 0.18) == pytest.approx(0.0, abs=1e-6)


def test_fair_pbv_is_one_when_roe_equals_cost_of_equity():
    """Банк, зарабатывающий ровно требуемую доходность, стоит своего капитала.

    Ни рубля премии, ни рубля дисконта — независимо от payout и горизонта.
    """
    v = _valuation(roe_start=KE, roe_term=KE, payout=0.5)
    assert v.ok, v.reason
    assert v.fair_pbv == pytest.approx(1.0, abs=1e-9)


def test_fair_pbv_is_one_regardless_of_payout():
    """Дивидендная политика сама по себе не создаёт стоимость."""
    a = _valuation(KE, KE, payout=0.0)
    b = _valuation(KE, KE, payout=0.9)
    assert a.fair_pbv == pytest.approx(1.0, abs=1e-9)
    assert b.fair_pbv == pytest.approx(1.0, abs=1e-9)


def test_value_creating_bank_is_worth_more_than_book():
    """§22, банк A: ROE устойчиво выше COE → P/BV > 1."""
    v = _valuation(roe_start=0.25, roe_term=0.24)
    assert v.ok and v.fair_pbv > 1.0
    assert v.pv_explicit > 0 and v.pv_terminal > 0


def test_value_destroying_bank_is_worth_less_than_book():
    """§22, банк C: ROE устойчиво ниже COE → P/BV < 1, и это не ошибка."""
    v = _valuation(roe_start=0.10, roe_term=0.10)
    assert v.ok and v.fair_pbv < 1.0
    assert v.pv_terminal < 0


def test_high_current_roe_fades_and_does_not_capitalize_forever():
    """§22, банк D: высокий текущий ROE при нормализации даёт меньше, чем вечная капитализация.

    Это и есть главное отличие от прежней линии P/BV = ROE/COE, которая
    капитализировала последний ROE навсегда.
    """
    fading = _valuation(roe_start=0.35, roe_term=0.18)
    forever = _valuation(roe_start=0.35, roe_term=0.35)
    assert fading.ok and forever.ok
    assert fading.fair_pbv < forever.fair_pbv


def test_fair_pbv_is_monotonic_in_roe():
    values = [_valuation(r, r).fair_pbv for r in (0.12, 0.16, 0.20, 0.24)]
    assert values == sorted(values), "рост ROE обязан повышать справедливый P/BV"


def test_fair_pbv_is_monotonic_in_cost_of_equity():
    values = [_valuation(0.22, 0.22, ke=k).fair_pbv for k in (0.14, 0.18, 0.22)]
    assert values == sorted(values, reverse=True), "рост требуемой доходности обязан снижать оценку"


# ── Отказы вместо выдуманных чисел (§31: без NaN/Infinity/silent fallback) ───

def test_terminal_growth_above_cost_of_equity_is_rejected_not_clamped():
    """Спека прямо запрещает скрытую обрезку слишком высокой оценки."""
    tv, reason = terminal_value(BV0, 0.20, 0.15, 0.16)
    assert tv is None and reason == "terminal_growth_ge_cost_of_equity"


def test_degenerate_terminal_spread_is_rejected():
    """При k_e − g → 0 результат определяется третьим знаком предпосылки."""
    tv, reason = terminal_value(BV0, 0.20, 0.15, 0.1490)
    assert tv is None and reason == "terminal_spread_degenerate"


def test_negative_book_value_makes_valuation_unavailable():
    """§14.3: отрицательный капитал ломает базу модели."""
    rows = build_forecast(BV0, 0.2, 0.2, 0.5, KE, 5, 0.35, 2026)
    v = value_equity(-1.0, rows, 0.2, KE, 0.1)
    assert not v.ok and v.reason == "book_value_not_positive"


def test_zero_shares_never_produces_infinite_price():
    rows = build_forecast(BV0, 0.2, 0.2, 0.5, KE, 5, 0.35, 2026)
    v = value_equity(BV0, rows, 0.2, KE, 0.1, diluted_shares=0)
    assert not v.ok and v.reason == "diluted_shares_not_positive"


def test_result_never_contains_nan_or_infinity():
    v = _valuation(0.22, 0.20)
    payload = v.as_dict()
    flat = repr(payload)
    assert "nan" not in flat.lower() and "inf" not in flat.lower()


def test_terminal_contribution_is_measured_against_book_not_against_the_result():
    """Вклад терминала обязан читаться и при разрушении стоимости.

    Доля «от итоговой стоимости» бессмысленна, когда слагаемые разного знака:
    у ВТБ на реальных данных получалось −234%. К капиталу метрика определена всегда.
    """
    good = _valuation(0.22, 0.20)
    assert good.terminal_share > 0
    assert good.as_dict()["decomposition"]["terminal_pv_over_book"] == pytest.approx(
        good.terminal_share, abs=1e-4)

    bad = _valuation(0.08, 0.08)                    # ROE устойчиво ниже k_e
    assert bad.ok and bad.terminal_share < 0, "разрушение стоимости даёт отрицательный терминал"
    assert abs(bad.terminal_share) < 100, "метрика не должна взрываться при смене знака"


# ── Прогноз и clean surplus (§6.3, §7.1) ─────────────────────────────────────

def test_roe_path_converges_towards_terminal():
    path = roe_path(0.35, 0.15, 10, 0.35)
    assert path[0] == pytest.approx(0.35)
    assert path[-1] < path[0]
    assert abs(path[-1] - 0.15) < abs(path[0] - 0.15)


def test_forecast_rolls_equity_by_clean_surplus():
    rows = build_forecast(BV0, 0.20, 0.18, 0.4, KE, 5, 0.35, 2026)
    for r in rows:
        expected = r.opening_equity + r.net_income - r.dividends + r.other_equity_change
        assert r.closing_equity == pytest.approx(expected, rel=1e-12)
    for prev, nxt in zip(rows, rows[1:]):
        assert nxt.opening_equity == pytest.approx(prev.closing_equity, rel=1e-12)


def test_no_dividends_are_paid_out_of_a_loss():
    rows = build_forecast(BV0, -0.05, -0.05, 0.5, KE, 3, 0.0, 2026)
    assert all(r.dividends == 0.0 for r in rows)


def test_clean_surplus_check_flags_unexplained_equity_jump():
    """Если капитал вырос не из прибыли, модель обязана это заметить, а не сгладить."""
    equity = [(2022, 100.0), (2023, 200.0)]          # +100 при прибыли 10
    res = clean_surplus_check(equity, {2023: 10.0}, {2023: 0.0}, tolerance=0.10)
    assert res["checked_years"] == 1 and res["breaches"] == 1
    assert res["status"] == "broken"


def test_clean_surplus_check_passes_on_consistent_history():
    equity = [(2022, 100.0), (2023, 108.0)]
    res = clean_surplus_check(equity, {2023: 10.0}, {2023: 2.0}, tolerance=0.10)
    assert res["breaches"] == 0 and res["status"] == "ok"


def test_normalized_roe_uses_median_not_last_value():
    """Разовый выброс не должен становиться прогнозом."""
    hist = [(2021, 0.15), (2022, 0.16), (2023, 0.60), (2024, 0.15)]
    norm, diag = normalized_roe(hist)
    assert norm == pytest.approx(0.155, abs=1e-9)
    assert diag["max"] == pytest.approx(0.60)


def test_normalized_roe_refuses_short_history():
    norm, diag = normalized_roe([(2024, 0.2)], min_years=3)
    assert norm is None and diag["reason"] == "not_enough_history"


def test_sustainable_growth_is_tied_to_retention():
    assert sustainable_growth(0.20, 0.4) == pytest.approx(0.12)
    assert sustainable_growth(0.20, 1.0) == pytest.approx(0.0)


# ── Cost of equity (§8) ──────────────────────────────────────────────────────

def test_beta_of_the_benchmark_itself_is_one():
    r = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02]
    beta, _ = raw_beta(r, r)
    assert beta == pytest.approx(1.0, abs=1e-12)


def test_beta_pairs_are_aligned_by_position_not_compacted_separately():
    """Пропуск обязан выбрасывать ПАРУ, иначе ряды разъедутся по времени."""
    asset = [0.01, None, 0.03, 0.02]
    bench = [0.01, 0.05, 0.03, 0.02]
    beta, diag = raw_beta(asset, bench)
    assert diag["observations"] == 3
    assert beta == pytest.approx(1.0, abs=1e-12)


def test_adjusted_beta_shrinks_towards_one():
    assert adjusted_beta(1.6) == pytest.approx(0.67 * 1.6 + 0.33)
    assert adjusted_beta(0.4) > 0.4
    assert adjusted_beta(1.0) == pytest.approx(1.0)


def test_winsorize_caps_the_outlier_but_keeps_length():
    vals = [0.01, 0.02, -0.50, 0.015, 0.012]
    w = winsorize(vals, 0.2)
    assert len(w) == len(vals)
    assert min(w) > -0.50


def test_cost_of_equity_components_add_up():
    ke = cost_of_equity(0.14, 1.1, 0.075, 0.005, 0.01)
    assert ke == pytest.approx(0.14 + 1.1 * 0.075 + 0.005 + 0.01)


def test_cost_of_equity_rejects_non_finite_input():
    assert cost_of_equity(float("nan"), 1.0, 0.075) is None
    assert cost_of_equity(0.14, float("inf"), 0.075) is None


def test_liquidity_premium_is_highest_when_turnover_is_unknown():
    cfg = {"unknown": 0.02, "tiers": [
        {"min_turnover_rub": 1e9, "premium": 0.0, "label": "высокий"},
        {"min_turnover_rub": 0.0, "premium": 0.02, "label": "низкий"}]}
    assert liquidity_premium(None, cfg)[0] == 0.02
    assert liquidity_premium(2e9, cfg)[0] == 0.0


def test_issuer_premium_is_capped_and_named():
    cfg = {"max_total": 0.02, "flags": {
        "sanctions": {"premium": 0.01, "label": "санкции"},
        "capital_shortage": {"premium": 0.015, "label": "дефицит капитала"}}}
    total, applied = issuer_premium(["sanctions", "capital_shortage"], cfg)
    assert total == 0.02, "премия обязана иметь потолок"
    assert len(applied) == 2, "каждая надбавка обязана быть названа"


def test_unknown_issuer_flag_adds_nothing():
    total, applied = issuer_premium(["выдуманный_флаг"], {"flags": {}})
    assert total == 0.0 and applied == []


def test_scenarios_shift_required_return_not_profit():
    s = scenarios(0.18, {"scenario_shift_pp": 2.0})
    assert s["bull"] < s["base"] < s["bear"]
    assert s["bear"] - s["base"] == pytest.approx(0.02)


# ── Запрет подмены базы оценки регуляторным капиталом (§3, §31) ──────────────

def test_core_modules_never_reference_regulatory_capital():
    """Ядро оценки не должно даже знать про формы ЦБ.

    Если в него однажды просочится Ф.123, оценка молча начнёт считаться от
    регуляторного капитала — ровно то, что спека запрещает.
    """
    for name in ("residual_income.py", "forecast.py", "cost_of_equity.py"):
        src = (CORE / name).read_text(encoding="utf-8")
        body = "\n".join(line for line in src.splitlines()
                         if not line.strip().startswith("#"))
        for forbidden in ("123", "f123", "capital_rub", "regnum", "Ф.102"):
            assert forbidden not in body, f"{name}: в ядро оценки просочился {forbidden}"


def test_single_stage_formula_is_available_only_as_a_benchmark():
    """Одностадийный ориентир существует, но обязан оставаться отдельной функцией."""
    assert justified_pbv_single_stage(0.20, 0.10, 0.18) == pytest.approx((0.20 - 0.10) / (0.18 - 0.10))
    assert justified_pbv_single_stage(0.20, 0.18, 0.18) is None, "при g ≥ k_e ориентир не определён"


# ── Пайплайн: контракт и запреты публикации (§14, §31) ───────────────────────

PIPELINE = CORE / "build_banks_valuation_v2.py"


def test_pipeline_never_substitutes_regulatory_capital_for_book_value():
    """Главный запрет спеки (§3): капитал Ф.123 не может стать базой оценки."""
    src = PIPELINE.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "total_equity" in body, "база оценки — бухгалтерский капитал"
    for forbidden in ("f123_capital", "capital_rub", "profit_ttm"):
        assert forbidden not in body, f"в оценку просочился регуляторный показатель {forbidden}"


def test_pipeline_uses_total_return_index_not_price_index():
    """Бета к ценовому индексу смещена: дивиденды есть в акции и нет в индексе."""
    src = PIPELINE.read_text(encoding="utf-8")
    assert "MCFTR" in src
    assert "unexpected_index" in src, "подмена индекса обязана ломать сборку, а не проходить молча"


def test_pipeline_uses_curve_point_not_key_rate_as_risk_free():
    src = PIPELINE.read_text(encoding="utf-8")
    assert "ofz_curve" in src and "risk_free_from_gcurve" in src
    assert "macro_cbr" not in src, "ключевая ставка не является безрисковой ставкой на 5 лет"


def test_config_forbids_publishing_fair_price_without_primary_ifrs():
    """Вариант А: справедливый P/BV публикуем, цену акции — нет."""
    cfg = json.loads((CORE / "valuation_config.json").read_text(encoding="utf-8"))
    pub = cfg["publication"]
    assert pub["publish_fair_price_per_share"] is False
    assert pub["publish_fair_pbv"] is True
    assert pub["max_status_without_primary_ifrs"] == "limited"


def test_published_output_has_no_full_status_and_no_share_price():
    """Проверка по фактическому артефакту, а не по намерению."""
    out = ROOT / "site" / "cbr" / "valuation_v2.json"
    if not out.exists():
        pytest.skip("valuation_v2.json ещё не собран")
    d = json.loads(out.read_text(encoding="utf-8"))
    for b in d["banks"]:
        assert b["status"] in ("limited", "unavailable"), (
            f"{b['ticker']}: статус full без первичной отчётности МСФО запрещён")
        v = b.get("valuation") or {}
        assert v.get("fair_price_per_share") is None, (
            f"{b['ticker']}: цена акции не должна публиковаться на вторичной базе капитала")


def test_published_output_is_free_of_nan_and_infinity():
    out = ROOT / "site" / "cbr" / "valuation_v2.json"
    if not out.exists():
        pytest.skip("valuation_v2.json ещё не собран")
    raw = out.read_text(encoding="utf-8")
    for bad in ("NaN", "Infinity", "-Infinity"):
        assert bad not in raw, f"в опубликованном JSON есть {bad}"


def test_every_bank_has_either_a_valuation_or_a_named_reason():
    out = ROOT / "site" / "cbr" / "valuation_v2.json"
    if not out.exists():
        pytest.skip("valuation_v2.json ещё не собран")
    d = json.loads(out.read_text(encoding="utf-8"))
    for b in d["banks"]:
        assert b.get("valuation") or b.get("reason"), (
            f"{b['ticker']}: ни оценки, ни причины её отсутствия")


# ── Фронт: контракт вкладки (§21.5, §25) ────────────────────────────────────

APP = ROOT / "site" / "app.js"
INDEX = ROOT / "site" / "index.html"


def test_second_contour_is_mounted_and_separate_from_the_regulatory_one():
    """Два контура обязаны стоять раздельно: у них разный периметр.

    Смешать их в одной таблице значило бы предложить сравнивать капитал группы
    с капиталом отдельного банка по форме 123.
    """
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert 'id="banks-fair-value"' in html and 'id="riv-body"' in html
    assert 'id="banks-valuation"' in html, "регуляторный контур должен остаться на месте"
    assert "renderRiv()" in app and "function renderRiv" in app
    assert "cbr/valuation_v2.json" in app, "второй контур читает свой файл, а не valuation.json"


def test_frontend_never_shows_a_fair_share_price():
    """Запрет варианта А доведён до интерфейса, а не только до конфига.

    Проверяется не точная фраза, а то, что объяснение на месте и называет причину:
    формулировку можно переписать, но читатель обязан узнать, ПОЧЕМУ цены нет.
    """
    app = APP.read_text(encoding="utf-8")
    start = app.index("function rivShellHTML")
    shell = app[start:app.index("function renderRiv")]
    assert "fair_price_per_share" not in shell, "цена акции просочилась в интерфейс"
    assert "справедлив" in shell and "цены акции" in shell, (
        "отсутствие целевой цены обязано быть объяснено, а не просто пропущено")
    assert "МСФО" in shell and "База капитала" in shell, (
        "объяснение обязано называть причину — вторичный источник базы капитала")


def test_frontend_separates_the_verdict_from_market_mispricing():
    """Вердикт и расхождение с рынком — ответы на разные вопросы.

    Банк может зарабатывать меньше требуемой доходности и при этом стоить дешевле,
    чем следует даже из этого. Одинаковая формулировка читалась бы как противоречие.
    """
    app = APP.read_text(encoding="utf-8")
    assert "модель ${gap >= 0 ? 'выше' : 'ниже'} рынка" in app
    assert "зарабатывает больше, чем требует риск" in app
    assert "зарабатывает меньше, чем требует риск" in app


def test_verdict_does_not_imply_the_bank_is_loss_making():
    """«Разрушает стоимость» — жаргон, который читается как «теряет деньги».

    Банк с ROE 6,5% прибыльный: он зарабатывает меньше, чем требует риск вложения.
    Подменять одно другим — вводить в заблуждение там, где цена ошибки высока.
    """
    app = APP.read_text(encoding="utf-8")
    # комментарии не считаются: в них этот термин как раз объясняется как нежелательный
    body = "\n".join(ln for ln in app.splitlines() if not ln.strip().startswith("//"))
    assert "разрушает стоимость" not in body, "вернулась формулировка, намекающая на убыток"
    assert "не значит «убыточен»" in body, "разницу обязано объяснять само описание модели"


def test_frontend_states_that_cbr_forms_are_not_used_in_this_model():
    app = APP.read_text(encoding="utf-8")
    assert "формы 102/123/135" in app and "не используются" in app


def test_riv_failure_reports_the_real_cause_like_finder_does():
    """Тот же урок, что и с Bond Finder: «недоступно» не должно скрывать причину."""
    app = APP.read_text(encoding="utf-8")
    loader = app[app.index("function loadRiv"):app.index("function rivMoney")]
    assert ".then((err) => { cb(err); });" in loader, (
        "cb внутри цепочки промисов снова замаскирует ошибку отрисовки под «файла нет»")
    render = app[app.index("function renderRiv"):app.index("function renderBanksValuation")]
    assert "ошибка отрисовки" in render


def test_pipeline_runs_where_its_inputs_actually_exist():
    """Сборка обязана стоять в update.yml, а не в банковском workflow.

    site_financials.json, returns.json и marketsaw.json в git НЕ хранятся — их
    генерирует update.yml. В update-cbr-banks.yml их в checkout нет, и модель
    молча осталась бы без данных.
    """
    upd = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    cbr = (ROOT / ".github" / "workflows" / "update-cbr-banks.yml").read_text(encoding="utf-8")

    assert "build_banks_valuation_v2.py" in upd
    assert "build_banks_valuation_v2.py" not in cbr, (
        "в банковском workflow нет site_financials.json/returns.json/marketsaw.json")
    assert "tests/test_bank_residual_income.py" in upd, "тесты модели должны гоняться в CI"


def test_valuation_v2_is_kept_in_git_as_last_good():
    """Артефакт обязан быть в git: orphan-републикация в update.yml копирует
    site/cbr/*.json из checkout, и несохранённый файл она бы просто стёрла."""
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "site/cbr/valuation_v2.json"],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert tracked, "valuation_v2.json не в git — при сбое генерации файл исчезнет с сайта"


def test_workflow_installs_every_tool_it_invokes():
    """Шаг CI, который зовёт pytest, обязан его ставить.

    Проверено на живом деплое: шаг падал за 30 мс — pytest не успевал стартовать,
    потому что его не было в окружении. Модель была ни при чём, но публикация
    сорвалась. Дешёвая проверка вместо повторения той же ошибки.
    """
    wf = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    if "python -m pytest" not in wf:
        pytest.skip("update.yml больше не зовёт pytest")
    install = wf[:wf.index("python -m pytest")]
    assert "pip install" in install and "pytest" in install.split("pip install")[1][:200], (
        "update.yml вызывает pytest, но не устанавливает его")


def test_failing_model_tests_do_not_publish_a_broken_artifact():
    """Провал тестов обязан возвращать last-good, а не публиковать что вышло."""
    wf = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    step = wf[wf.index("Тесты модели остаточного дохода"):]
    step = step[:step.index("- name:", 10)]
    assert "git checkout -- site/cbr/valuation_v2.json" in step, (
        "при провале тестов артефакт обязан откатываться к закоммиченному last-good")
