import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def _json(name: str) -> dict:
    return json.loads((SITE / "bonds" / name).read_text(encoding="utf-8"))


def test_bond_lab_has_one_workspace_and_three_user_scenarios():
    """Верхних вкладок три — по числу пользовательских сценариев, а не по числу инструментов.

    Прежние пять (Портфели/Скринер/Relative Value/G-кривая/Finder) дублировали друг друга.
    Три аналитических представления никуда не делись: они живут под-переключателем внутри
    вкладки «Продвинутая аналитика» (см. тест ниже) — функциональность не удалена.
    """
    html = (SITE / "index.html").read_text(encoding="utf-8")

    assert html.count('id="bondlab-workspace"') == 1
    assert html.count('data-bondlab-tab=') == 3
    for tab in ("portfolios", "screener", "analytics"):
        assert f'data-bondlab-tab="{tab}"' in html
    assert 'role="tablist"' in html
    assert 'aria-live="polite"' in html


def test_analytics_tab_still_reaches_relative_curve_and_finder():
    """Сведение вкладок не должно осиротить прежние представления."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "function bondAnalyticsHTML" in app, "вкладка аналитики вызывает эту функцию"
    for view in ("relative", "curve", "finder"):
        assert f"data-bond-analytics=\"{view}\"" in app or f"id: '{view}'" in app
    # прежние рендеры вызываются, а не переписаны заново
    assert "bondRelativeValueHTML(" in app
    assert "bondCurveLabHTML(" in app
    assert "renderFinder()" in app


def test_bond_detail_card_is_wired():
    """Карточка выпуска: кнопки, обработчик и диалог должны существовать вместе."""
    app = (SITE / "app.js").read_text(encoding="utf-8")
    html = (SITE / "index.html").read_text(encoding="utf-8")

    assert 'id="bond-detail-dialog"' in html
    assert "function bondDetailHTML" in app
    assert "function openBondDetail" in app
    assert "data-bond-open" in app
    assert "closeBondDetail()" in app


def test_bond_lab_frontend_uses_all_v3_artifacts_and_preserves_finder():
    app = (SITE / "app.js").read_text(encoding="utf-8")
    html = (SITE / "index.html").read_text(encoding="utf-8")

    for name in (
        "universe.json",
        "portfolio_presets.json",
        "portfolio_validation.json",
        "portfolio_last_valid.json",
    ):
        assert name in app
    assert "bondPortfolioLabHTML" in app
    assert "bondUniverseScreenerHTML" in app
    assert "bondRelativeValueHTML" in app
    assert "bondCurveLabHTML" in app
    assert "renderFinder();" in app
    assert '<script src="bond_allocator.js" defer></script>' in html
    assert "bondlab-mobile-cards" in app
    assert "Расширенные настройки · только просмотр" in app


def test_bond_allocator_is_in_both_full_site_publish_paths():
    deploy = (ROOT / "scripts" / "deploy_ghpages.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert "site/bond_allocator.js" in deploy
    assert "bond_allocator.js?v=" in deploy
    assert "site/bond_allocator.js" in workflow
    assert "bond_allocator.js?v=${V}" in workflow


def test_bond_lab_frontend_is_honest_about_failed_fresh_run():
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "currentGatePassed" in app
    assert "hasLastValid" in app
    assert "Показан последний валидный расчёт" in app
    assert "Свежий расчёт не прошёл контроль качества" in app
    assert "lastValid.allocations" in app


def test_published_v3_presets_cover_matrix_without_relaxing_unavailable_case():
    presets = _json("portfolio_presets.json")
    validation = _json("portfolio_validation.json")

    expected = {
        f"{profile}:{horizon}"
        for profile in ("defensive", "balanced", "income")
        for horizon in ("1y", "3y", "5y", "7y", "10y")
    }
    assert set(presets["presets"]) == expected
    assert set(presets["allocations"]) == expected - {"defensive:1y"}
    assert validation["status"] == "PASS"
    assert validation["available_presets"] == 14
    assert validation["unavailable_presets"] == ["defensive:1y"]
    unavailable = validation["presets"]["defensive:1y"]
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["target_status"] == "INFEASIBLE"
    assert unavailable["candidate_diagnostics"]["issues_inside_duration_corridor"] == 1


def test_published_allocations_reconcile_budget_and_use_integer_lots():
    presets = _json("portfolio_presets.json")

    for key, allocation in presets["allocations"].items():
        assert allocation["status"] == "VALIDATED", key
        assert allocation["positions"], key
        assert all(isinstance(row["lots"], int) and row["lots"] > 0 for row in allocation["positions"])
        assert allocation["invested_with_costs_rub"] <= allocation["budget_rub"] + 0.01
        assert abs(
            allocation["invested_with_costs_rub"]
            + allocation["cash_rub"]
            - allocation["budget_rub"]
        ) <= 0.05


def test_browser_allocator_preserves_composition_and_matches_python_within_one_lot():
    script = r"""
const fs = require('fs');
const allocator = require('./site/bond_allocator.js');
const universe = JSON.parse(fs.readFileSync('site/bonds/universe.json'));
const presets = JSON.parse(fs.readFileSync('site/bonds/portfolio_presets.json'));
const key = 'balanced:3y';
const client = allocator.allocate(
  presets.presets[key], universe, 1000000,
  presets.profiles.balanced, presets.horizons['3y'], presets.costs
);
const server = presets.allocations[key];
const serverLots = Object.fromEntries(server.positions.map((row) => [row.secid, row.lots]));
console.log(JSON.stringify({
  status: client.status,
  budgetDelta: Math.abs(client.invested_with_costs_rub + client.cash_rub - client.budget_rub),
  sameComposition: client.positions.map((row) => row.secid).sort().join(',') === server.positions.map((row) => row.secid).sort().join(','),
  maximumLotDifference: Math.max(...client.positions.map((row) => Math.abs(row.lots - serverLots[row.secid]))),
}));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    parity = json.loads(result.stdout)

    assert parity["status"] == "CLIENT_VALIDATED"
    assert parity["budgetDelta"] <= 0.01
    assert parity["sameComposition"] is True
    assert parity["maximumLotDifference"] <= 1


def test_portfolio_controls_are_wired_not_just_rendered():
    """Разметка без обработчика — мёртвая кнопка.

    Все пять контролов «Моего портфеля» были отрисованы, но ни один не имел слушателя:
    импорт, выбор файла, режим ребаланса, резервная копия и удаление ничего не делали.
    """
    app = (SITE / "app.js").read_text(encoding="utf-8")

    for control in (
        "bond-portfolio-import",
        "bond-portfolio-file",
        "bond-portfolio-clear",
        "bond-portfolio-backup",
        "bond-rebalance-mode",
    ):
        assert f"getElementById('{control}')" in app, f"нет обработчика для {control}"

    # разбор таблицы делегирован расчётному слою, а не продублирован во фронте
    assert "window.BondRetail.parsePortfolioText(text)" in app
    assert "savePortfolio(window.localStorage" in app
    assert "clearPortfolio(window.localStorage)" in app


def test_portfolio_text_parser_handles_broker_exports():
    """Разбор вставки: заголовок, русская запятая, битые строки не теряются молча."""
    script = """
    const B = require('./site/bond_retail.js');
    const out = B.parsePortfolioText([
      'SECID;Количество;Средняя цена',
      'RU000A0ZYLG5;100;101,20',
      'RU000A107RZ0;50;99,80',
      'BROKEN;;',
    ].join('\\n'));
    console.log(JSON.stringify(out));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    parsed = json.loads(result.stdout)

    assert len(parsed["positions"]) == 2, "заголовок не должен попадать в позиции"
    assert parsed["positions"][0]["identifier"] == "RU000A0ZYLG5"
    assert parsed["positions"][0]["quantity_bonds"] == 100
    assert parsed["positions"][0]["average_price"] == 101.20, "русская запятая как разделитель"
    assert len(parsed["errors"]) == 1, "битая строка должна вернуться ошибкой, а не исчезнуть"
    assert parsed["errors"][0]["code"] == "INVALID_ROW"


def test_new_money_mode_never_sells():
    """Режим «только новые деньги» не должен продавать существующие позиции."""
    script = """
    const B = require('./site/bond_retail.js');
    const universe = [
      { secid: 'A', lot_size: 1, face_value_per_bond_rub: 1000, clean_price_pct: 100,
        aci_per_bond_rub: 0, dirty_price_per_bond_rub: 1000, dirty_price_per_lot_rub: 1000 },
      { secid: 'B', lot_size: 1, face_value_per_bond_rub: 1000, clean_price_pct: 100,
        aci_per_bond_rub: 0, dirty_price_per_bond_rub: 1000, dirty_price_per_lot_rub: 1000 },
    ];
    const current = [{ secid: 'A', quantity_bonds: 100, bond: universe[0] }];
    const target = { positions: [{ secid: 'B', lots: 50, actual_weight: 1 }], budget_rub: 50000 };
    const out = B.reconcile(current, target, universe, { mode: 'new_money', minTradeRub: 0, noTradeBandPct: 0 });
    console.log(JSON.stringify(out.trades.map(t => ({ secid: t.secid, action: t.action, lots: t.trade_lots }))));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    trades = json.loads(result.stdout)

    assert not any(t["action"] == "SELL" for t in trades), "новые деньги не продают позиции"
    assert all(float(t["lots"]).is_integer() for t in trades), "лоты только целые"


def test_alternatives_rank_by_profile_not_by_yield():
    """Аналоги подбираются по близости профиля.

    Список «где больше процент» увёл бы пользователя ровно в те выпуски, от которых
    защищает safe-фильтр, поэтому сортировка идёт по рейтингу, дюрации, сектору и
    ликвидности, а сам исходный выпуск в список не попадает.
    """
    script = """
    const B = require('./site/bond_retail.js');
    const uni = require('./site/bonds/universe.json').bonds;
    const asOf = '2026-08-01';
    const safe = B.classifyUniverse(uni, { asOf }).filter(r => r.bond_safety.investable);
    const src = safe[0];
    const alts = B.findAlternatives(src, uni, { asOf, limit: 5 });
    console.log(JSON.stringify({
      source: src.secid,
      count: alts.length,
      allSafe: alts.every(a => a.bond_safety.investable),
      selfExcluded: !alts.some(a => a.secid === src.secid),
      sortedByYield: alts.every((a, i) => i === 0 || a.ytm_net_delta_pp <= alts[i - 1].ytm_net_delta_pp),
    }));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    out = json.loads(result.stdout)

    assert out["count"] > 0
    assert out["allSafe"], "аналог обязан сам проходить безопасный фильтр"
    assert out["selfExcluded"], "исходный выпуск не может быть себе аналогом"
    assert not out["sortedByYield"], "порядок не должен совпадать с сортировкой по доходности"


def test_coverage_reports_missing_group_data_honestly():
    """Покрытие классификаций показывается как есть, а unknown не считается нормой."""
    script = """
    const B = require('./site/bond_retail.js');
    const uni = require('./site/bonds/universe.json').bonds;
    console.log(JSON.stringify(B.coverage(uni)));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    cov = json.loads(result.stdout)

    # ultimate_parent_id не заполнен ни у одного выпуска — это должно быть видно, а не скрыто
    assert cov["groups_pct"] == 0.0
    assert 0 < cov["ratings_pct"] < 100, "рейтинг известен не у всех — покрытие честное"
    assert cov["sectors_pct"] < 100

    app = (SITE / "app.js").read_text(encoding="utf-8")
    assert "BondRetail.coverage(all)" in app, "покрытие должно показываться в интерфейсе"


def test_stale_snapshot_does_not_blame_user_filters():
    """Если пусто из-за устаревших цен, совет «ослабьте фильтры» вводит в заблуждение."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "bond-empty-stale" in app
    assert "Данные о ценах устарели" in app
    assert "Ослаблять фильтры смысла нет" in app


def test_settings_are_wired_into_calculations_not_just_stored():
    """Панель настроек обязана менять цифры, иначе она вводит в заблуждение."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "data-bond-setting" in app
    assert "BondRetail.saveSettings(window.localStorage" in app
    # налог, комиссия и пороги сделок берутся из настроек, а не зашиты
    assert "taxRate: BOND_SETTINGS.taxRate" in app
    assert "commissionBps: BOND_SETTINGS.commissionBps" in app
    assert "Number(BOND_SETTINGS.minTradeRub)" in app
    assert "{ taxRate: 0.13 }" not in app, "налог не должен быть зашит в вызов календаря"
    # умолчания берутся у расчётного слоя, а не дублируются во фронте
    assert "window.BondRetail.loadSettings(null)" in app


def test_settings_defaults_match_calculation_layer():
    """Кнопка «вернуть по умолчанию» должна давать то же, что первый запуск."""
    script = """
    const B = require('./site/bond_retail.js');
    console.log(JSON.stringify(B.loadSettings(null)));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    defaults = json.loads(result.stdout)

    for key in ("taxRate", "commissionBps", "slippageBps", "minTradeRub", "noTradeBandPct", "reinvestRatePct"):
        assert key in defaults, f"умолчание {key} должно жить в расчётном слое"
    assert 0 < defaults["taxRate"] < 1, "ставка налога хранится долей, а не процентами"


def test_calendar_shows_empty_months_and_configurable_tax():
    """Месяц без выплат — факт о потоке, а не пропуск данных."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "bond-month-empty" in app, "пустые месяцы должны быть видны на графике"
    assert "for (let i = 0; i < 12; i += 1)" in app, "календарь строится на все 12 месяцев"
    # ставка налога в подписи берётся из настроек, а не зашита
    assert "ru(BOND_SETTINGS.taxRate * 100, 0)" in app
    assert "по ставке 13% только для купонов" not in app


def test_reinvestment_uses_user_rate_and_is_off_by_default():
    """Ставку реинвестирования задаёт пользователь.

    Подстановка YTM самой облигации молча предположила бы неизменные условия рынка
    и завысила бы результат тем сильнее, чем выше текущая доходность.
    """
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "function bondReinvestHTML" in app
    assert "BOND_SETTINGS.reinvestRatePct" in app
    assert "bond-reinvest-off" in app, "выключенный режим должен объясняться, а не молчать"
    assert "Ставка задана вами, а не выведена из доходности выпусков" in app


def test_downgrade_scenario_does_not_invent_price_loss():
    """Без матрицы спредов по ступеням рублёвую потерю считать не из чего."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "function bondDowngradeHTML" in app
    assert "перестанут проходить ваш минимум" in app
    assert "Потеря стоимости в рублях не оценивается" in app


def test_alternatives_comparison_hides_missing_deposit_rates():
    """Ставки вкладов не выдумываются: надёжного источника в проекте нет."""
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "function bondAlternativesCompareHTML" in app
    assert "актуальных данных нет" in app
    assert "MACRO_CBR.key_rate.current" in app, "ключевая ставка берётся из макро-модуля"
