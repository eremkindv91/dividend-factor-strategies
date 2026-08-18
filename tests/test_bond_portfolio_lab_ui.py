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
    """Матрица пресетов полная, а недоступные — честно помечены.

    Раньше тест прибивал конкретный список недоступных пресетов (defensive:1y) и число
    кандидатов внутри коридора дюрации. И то и другое — рынок, а не контракт: после
    расширения универсума со 180 до 800 корпоратов коротких защитных бумаг стало
    достаточно, и defensive:1y начал строиться. Тест падал на УЛУЧШЕНИИ.

    Проверяем инварианты: все 15 комбинаций объявлены, построенные — подмножество
    объявленных, а каждый непостроенный обязан иметь статус UNAVAILABLE, причину
    INFEASIBLE и непустую диагностику.
    """
    presets = _json("portfolio_presets.json")
    validation = _json("portfolio_validation.json")

    expected = {
        f"{profile}:{horizon}"
        for profile in ("defensive", "balanced", "income")
        for horizon in ("1y", "3y", "5y", "7y", "10y")
    }
    assert set(presets["presets"]) == expected
    built = set(presets["allocations"])
    assert built <= expected, "построены пресеты, которых нет в матрице"
    assert validation["status"] == "PASS"
    assert validation["available_presets"] == len(built)

    unavailable = expected - built
    assert set(validation["unavailable_presets"]) == unavailable
    for key in unavailable:
        record = validation["presets"][key]
        assert record["status"] == "UNAVAILABLE", key
        if record["target_status"] == "INFEASIBLE":
            diagnostics = record["candidate_diagnostics"]
            assert isinstance(diagnostics.get("issues_inside_duration_corridor"), int), key
            assert diagnostics.get("candidates") is not None, (
                f"{key}: недоступность обязана сопровождаться списком кандидатов, "
                "иначе причину не проверить"
            )
        else:
            assert record["target_status"] in {"OPTIMAL", "FEASIBLE"}, key
            assert record.get("allocation_errors"), key
            assert record.get("reason_codes"), key


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


def test_screener_note_matches_the_filter_it_describes():
    """Примечание не должно обещать «ежемес. фикс-купон», когда фильтр открыт для всех частот.

    Раньше расходились: monthly_only сняли, а текст остался — пользователю сообщали
    заведомо неверный состав выборки.
    """
    builder = (ROOT / "bonds" / "update_bonds.py").read_text(encoding="utf-8")

    assert "monthly_only=False" in builder, "скринер снова ограничен одной частотой"
    note_start = builder.index('"note": "Скринер')
    note = builder[note_start:note_start + 400]
    assert "ежемес. фикс-купон" not in note, "примечание описывает снятый фильтр"
    assert "ЛЮБОЙ частоты" in note


def test_screener_actually_contains_more_than_monthly_coupons():
    """Факт, а не декларация: в опубликованном скринере есть не только freq=12."""
    bonds = _json("screener.json")["bonds"]
    freqs = {row.get("freq") for row in bonds if row.get("freq")}

    assert freqs, "у выпусков скринера пропала частота купона"
    assert freqs - {12}, (
        f"в скринере снова только ежемесячные выпуски: {sorted(freqs)} — "
        "проверьте monthly_only в bonds/update_bonds.py"
    )


def test_coupon_frequency_is_both_filterable_and_visible():
    """Фильтровать по частоте, не показывая её, нельзя: пользователь не проверит результат.

    Поля называются по-разному в двух источниках (freq в скринере, coupon_frequency
    в универсуме) — обе ветки обязаны читаться.
    """
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "couponFreq: 'all'" in app, "фильтр частоты пропал из состояния"
    assert "COUPON_FREQ_OPTIONS" in app and "COUPON_FREQ_NAMES" in app
    assert "function couponFreqCell" in app, "колонка частоты должна рендериться"
    assert "'Выплат/год'" in app, "колонку убрали из заголовков"
    assert "freq: ['freq', 'coupon_frequency']" in app, "сортировка не покрывает оба имени поля"
    # в живом скринере (universe.json) поле называется coupon_frequency
    assert "couponFreqCell(row.coupon_frequency" in app


def test_finder_failure_reports_the_real_cause():
    """«Недоступен» не должно быть ответом на ошибку ОТРИСОВКИ.

    cb вызывался внутри .then(), поэтому исключение рендера попадало в .catch()
    и вызывало cb(e) второй раз — настоящая причина терялась.
    """
    app = (SITE / "app.js").read_text(encoding="utf-8")

    loader = app[app.index("function loadFinder"):app.index("function renderFinder")]
    assert ".then((err) => { cb(err); });" in loader, (
        "cb снова вызывается внутри цепочки — ошибки рендера будут маскироваться"
    )

    render = app[app.index("function renderFinder"):app.index("function finderShellHTML")]
    assert "ошибка отрисовки" in render, "ошибка рендера обязана называться своим именем"
    assert "fnd-retry" in render, "нужна кнопка повтора: сбой источника бывает временным"
    assert "Bond Finder недоступен" not in app, "вернулась формулировка, скрывающая причину"


def test_liquidity_is_not_measured_by_an_unformed_session():
    """Состав выборки не должен зависеть от времени запуска сборки.

    VALTODAY — оборот ТЕКУЩЕГО дня. Запуск в 08:15 МСК дал 3 бумаги из 3016 и пустой
    скринер: сборка честно остановилась, но и не собралась. Нормализация подставляет
    оборот последнего завершённого торгового дня, когда сессия ещё не набрана.
    """
    builder = (ROOT / "bonds" / "update_bonds.py").read_text(encoding="utf-8")

    assert "def last_session_turnover" in builder
    assert "def _normalize_turnover" in builder
    assert "_normalize_turnover(board, rows)" in builder, (
        "нормализация должна жить в load_board — иначе скринер и универсум разойдутся составом"
    )
    # оба поля оборота: скринер читает VALTODAY, билдер универсума — VALTODAY_RUR
    normalize = builder[builder.index("def _normalize_turnover"):builder.index("def load_board")]
    assert '"VALTODAY"' in normalize and '"VALTODAY_RUR"' in normalize

    # ночной прогон (сессия набрана) обязан остаться нетронутым
    assert "if formed < MIN_FORMED_SESSION:" in normalize, (
        "подмена должна быть условной, иначе она перепишет и полноценную вечернюю сессию"
    )
    assert '"turnover_basis": dict(TURNOVER_BASIS)' in builder, (
        "за какой день мерили оборот — обязано попадать в meta, а не подразумеваться"
    )


def test_unknown_sector_warns_instead_of_blocking():
    """Отрасль эмитента — пробел НАШИХ данных (покрытие ~40%), а не свойство выпуска.

    Блокировать ею нельзя: портфельный движок трактует её так же — не исключает бумагу,
    а ограничивает совокупную долю неизвестного сектора (max_unknown_sector).
    """
    retail = (SITE / "bond_retail.js").read_text(encoding="utf-8")
    config = json.loads((ROOT / "bonds" / "portfolio_config.json").read_text(encoding="utf-8"))

    assert "requireKnownSector: false" in retail, "отсутствие отрасли снова блокирует выпуск"
    assert "else warn('UNKNOWN_SECTOR')" in retail, "предупреждение обязано остаться, а не исчезнуть"

    for name, profile in config["profiles"].items():
        cap = float(profile["max_unknown_sector"])
        assert 0 < cap < 1, f"{name}: доля неизвестного сектора должна быть ограничена, а не запрещена"


def test_warnings_are_visible_and_the_useless_one_does_not_crowd_them_out():
    """Пропустить бумагу и промолчать о неподтверждённых данных — та же ложь, что и «проверено».

    Связей «материнская — дочерняя» в данных MOEX нет вообще, поэтому это предупреждение висит
    на КАЖДОЙ бумаге; в компактной строке оно обязано уступать место информативным.
    """
    app = (SITE / "app.js").read_text(encoding="utf-8")

    assert "function bondWarningListHTML" in app
    assert "bondWarningListHTML(row, true)" in app, "предупреждения не выводятся в строке скринера"
    assert "bond-detail-note" in app, "в карточке выпуска предупреждения тоже обязаны быть видны"
    assert "BOND_WARN_UNIVERSAL = 'GROUP_DATA_UNAVAILABLE'" in app
    warn = app[app.index("function bondWarningListHTML"):]
    warn = warn[:warn.index("\nfunction ")]
    assert "sort(" in warn, "универсальное предупреждение должно уводиться в конец"


def test_index_linker_is_not_marked_verified_or_given_a_fake_internal_ytm():
    script = """
    const B = require('./site/bond_retail.js');
    const row = {
      secid: 'RU000A10F504', instrument_type: 'corp', rating: 'AAA', qualified_only: false,
      source_dates: { price: '2026-08-07' }, clean_price_pct: 99.95,
      dirty_price_per_lot_rub: 1038.80, face_value_per_bond_rub: 1034.70, lot_size: 1,
      maturity_date: '2029-06-16', years_to_maturity: 2.85, duration_value: null,
      ytm_gross_pct: null, ytm_net_est_pct: null, moex_yield_pct: 1.88,
      median_volume_20d_rub: 50000000, value_today_rub: 50000000, history_sessions: 20,
      coupon_type: 'index_linked', bond_structure_type: 'INDEX_LINKED',
      index_linked: true, variable_nominal: true, cashflows_deterministic: false,
      sector: 'Финансы', ultimate_parent_id: 'state:veb', peer_n: 10,
      has_put_offer: false, has_call: false, amortizing: false,
      data_quality_flags: ['INDEX_LINKED', 'VARIABLE_NOMINAL', 'INDETERMINATE_CASHFLOWS'],
    };
    console.log(JSON.stringify(B.classifyBond(row, { asOf: '2026-08-10' })));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    safety = json.loads(result.stdout)

    assert safety["investable"] is False
    assert safety["status"] == "requires_review"
    assert safety["ytmConfirmed"] is False
    assert "INDEX_LINKED" in safety["reasonCodes"]
    assert "VARIABLE_NOMINAL" in safety["reasonCodes"]
    assert "INDETERMINATE_CASHFLOWS" in safety["reasonCodes"]
    assert "INVALID_YTM" not in safety["reasonCodes"], (
        "an intentionally omitted YTM is not a failed numerical solver"
    )

    app = (SITE / "app.js").read_text(encoding="utf-8")
    assert "Расчётный YTM" in app and "не рассчитан" in app
    assert "Доходность MOEX" in app
    assert "официальный индикатор, не наш YTM" in app
    assert "Частично проверено" in app and "Требует проверки" in app


def test_null_numeric_fields_are_not_silently_coerced_to_zero():
    script = """
    const B = require('./site/bond_retail.js');
    const row = {
      secid: 'NULLS', instrument_type: 'ofz', source_dates: { price: '2026-08-07' },
      clean_price_pct: 100, dirty_price_per_lot_rub: 1000,
      face_value_per_bond_rub: 1000, lot_size: 1, maturity_date: '2029-01-01',
      duration_value: null, ytm_gross_pct: null, ytm_net_est_pct: null,
      median_volume_20d_rub: 50000000, value_today_rub: 50000000, history_sessions: 20,
      coupon_type: 'fixed', sector: 'Государственные облигации', ultimate_parent_id: 'state',
      data_quality_flags: [],
    };
    const out = B.classifyBond(row, { asOf: '2026-08-10' });
    console.log(JSON.stringify(out.reasonCodes));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    reasons = json.loads(result.stdout)

    assert "INVALID_CASHFLOWS" in reasons
    assert "INVALID_YTM" in reasons


def test_offer_and_amortizing_rows_are_not_presented_as_verified_internal_ytm():
    script = """
    const B = require('./site/bond_retail.js');
    const base = {
      secid: 'COMPLEX', instrument_type: 'corp', rating: 'AAA', qualified_only: false,
      source_dates: { price: '2026-08-07' }, clean_price_pct: 100,
      dirty_price_per_lot_rub: 1010, face_value_per_bond_rub: 1000, lot_size: 1,
      maturity_date: '2029-01-01', years_to_maturity: 2.4, duration_value: null,
      ytm_gross_pct: null, ytm_net_est_pct: null, moex_yield_pct: 15,
      median_volume_20d_rub: 50000000, value_today_rub: 50000000, history_sessions: 20,
      coupon_type: 'fixed', cashflows_deterministic: false,
      sector: 'Финансы', ultimate_parent_id: 'group', peer_n: 10,
      has_call: false, qualified_only: false, data_quality_flags: ['INDETERMINATE_CASHFLOWS'],
    };
    const offer = B.classifyBond({ ...base, bond_structure_type: 'OFFER', has_put_offer: true, amortizing: false }, { asOf: '2026-08-10' });
    const amortizing = B.classifyBond({ ...base, bond_structure_type: 'AMORTIZING', has_put_offer: false, amortizing: true }, { asOf: '2026-08-10' });
    console.log(JSON.stringify({ offer, amortizing }));
    """
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    checks = json.loads(result.stdout)

    for safety in checks.values():
        assert safety["investable"] is False
        assert safety["status"] == "requires_review"
        assert safety["ytmConfirmed"] is False
        assert "INDETERMINATE_CASHFLOWS" in safety["reasonCodes"]

    app = (SITE / "app.js").read_text(encoding="utf-8")
    assert "сценарий выкупа" in app
    assert "полного графика амортизации" in app
    assert "Расчётный YTM не подтверждён" in app
    assert "не используется в портфеле" in app
