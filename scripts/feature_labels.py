"""
Русские подписи признаков для SHAP-объяснений на сайте.
Ключи — имена колонок панели РФ; значения — короткие подписи для UI.
Незнакомые признаки получают аккуратный fallback (см. get_label).
"""

FEATURE_LABELS = {
    # дивидендная история
    "div_streak": "Серия лет с выплатами",
    "div_growth_streak": "Серия лет роста дивиденда",
    "div_cut_count": "Число срезов дивиденда в истории",
    "div_cut_count_5y": "Срезы дивиденда за 5 лет",
    "dps_cagr_3y": "Рост дивиденда (CAGR 3 года)",
    "dsi_proxy": "Индекс стабильности дивидендов (DSI)",
    "lag1_div_yield_pct": "Дивдоходность годом ранее",
    "lag1_payout_ratio_pct": "Payout годом ранее",
    # прибыльность
    "roe_pct": "Рентабельность капитала (ROE)",
    "roa_pct": "Рентабельность активов (ROA)",
    "lag1_roe_pct": "ROE годом ранее",
    "lag1_roa_pct": "ROA годом ранее",
    "net_margin_pct": "Чистая маржа",
    "lag1_net_margin_pct": "Чистая маржа годом ранее",
    "ebitda_margin_pct": "Маржа EBITDA",
    "lag1_ebitda_margin_pct": "Маржа EBITDA годом ранее",
    "gross_profit_margin_pct": "Валовая маржа",
    "trend3y_roe_pct": "Тренд ROE за 3 года",
    "trend3y_net_margin_pct": "Тренд чистой маржи за 3 года",
    # денежный поток / качество
    "fcf_yield_pct": "Доходность свободного денежного потока (FCF)",
    "fcf_to_net_profit": "FCF к чистой прибыли",
    "cash_conversion": "Конверсия прибыли в денежный поток",
    "accruals_ratio": "Начисления (accruals)",
    "interest_coverage": "Покрытие процентов",
    "capex_to_revenue": "CAPEX к выручке",
    "cash_to_total_debt": "Денежные средства к долгу",
    "FCF": "Свободный денежный поток",
    "CFO_": "Операционный денежный поток",
    "CAPEX_": "Капзатраты (CAPEX)",
    # долг / безопасность
    "net_debt_to_ebitda": "Чистый долг / EBITDA",
    "lag1_net_debt_to_ebitda": "Чистый долг / EBITDA годом ранее",
    "trend3y_net_debt_to_ebitda": "Тренд долг/EBITDA за 3 года",
    "debt_to_equity": "Долг / капитал",
    "lag1_debt_to_equity": "Долг / капитал годом ранее",
    "debt_to_ebitda": "Долг / EBITDA",
    "equity_to_assets_pct": "Капитал / активы",
    "vol3y_net_profit": "Волатильность прибыли за 3 года",
    "vol3y_ebitda": "Волатильность EBITDA за 3 года",
    # размер / оценка
    "market_cap_mln": "Рыночная капитализация",
    "log_market_cap": "Лог. капитализации",
    "ev_mln": "Стоимость предприятия (EV)",
    "pe": "P/E",
    "pbv": "P/BV",
    "ps": "P/S",
    "ev_ebitda": "EV / EBITDA",
    "EV_Sales": "EV / Sales",
    "p_fcf": "P/FCF",
    "revenue_mln": "Выручка",
    "ebitda_mln": "EBITDA",
    "net_profit_mln": "Чистая прибыль",
    "operating_income_mln": "Операционная прибыль",
    "equity_mln": "Собственный капитал",
    "assets_mln": "Активы",
    "cash_mln": "Денежные средства",
    "total_debt_mln": "Совокупный долг",
    "net_debt_mln": "Чистый долг",
    "total_liabilities_mln": "Обязательства",
    "interest_expense_mln": "Процентные расходы",
    "interest_income_mln": "Процентные доходы",
    "credit_portfolio_mln": "Кредитный портфель",
    "nim_pct": "Чистая процентная маржа (NIM)",
    "cost_of_risk_pct": "Стоимость риска",
    "npl_ratio_pct": "Доля проблемных кредитов (NPL)",
    "coverage_ratio_pct": "Покрытие резервами",
    "loans_to_assets_pct": "Кредиты к активам",
    "revenue_yoy_pct": "Рост выручки г/г",
    "ebitda_yoy_pct": "Рост EBITDA г/г",
    "revenue_cagr3y_calc": "Рост выручки (CAGR 3 года)",
    "ebitda_cagr3y_calc": "Рост EBITDA (CAGR 3 года)",
    # рынок / ликвидность
    "mom_12_1_pct": "Моментум 12–1 мес.",
    "mom_6_1_pct": "Моментум 6–1 мес.",
    "mom_3_0_pct": "Моментум 3 мес.",
    "total_return_pct": "Полная доходность за год",
    "vol_price_pct": "Волатильность цены",
    "down_vol_pct": "Волатильность вниз",
    "idio_vol_pct": "Идиосинкратическая волатильность",
    "beta_imoex": "Бета к индексу МосБиржи",
    "amihud_12m": "Неликвидность (Amihud)",
    "turnover_12m": "Оборачиваемость за год",
    # отрасль/структура
    "sector": "Отрасль",
    "listing_level": "Уровень листинга",
    "gov_share_pct": "Доля государства",
    "is_state_owned": "Госкомпания",
    "free_float_pct": "Free float",
    # макро
    "key_rate_pct": "Ключевая ставка ЦБ",
    "gdp_growth_yoy_pct": "Рост ВВП",
    "inflation_dec_yoy_pct": "Инфляция (дек., г/г)",
    "brent_usd": "Нефть Brent",
    "usdrub_avg": "Курс USD/RUB",
    "cbr_bci": "Индекс делового климата ЦБ",
    "moex_bluechip_index": "Индекс голубых фишек",
    "ruonia_index": "RUONIA",
    # interaction
    "inter_brent_x_fcf": "Brent × FCF",
    "inter_usdrub_x_revenue": "USD/RUB × выручка",
    "inter_bci_x_streak": "Деловой климат × серия выплат",
    # цены / прочее
    "price_start": "Цена на начало года",
    "price_end": "Цена на конец года",
    "gdp_construction_bln": "ВВП: строительство",
    "gdp_manufacturing_bln": "ВВП: обработка",
    "gdp_mining_bln": "ВВП: добыча",
    "ofz_pk_gross_bln": "Размещение ОФЗ-ПК",
    "total_ofz_gross_bln": "Размещение ОФЗ",
    "oil_gas_revenue_bln": "Нефтегазовые доходы бюджета",
    "money_supply_m2_bln": "Денежная масса M2",
}


def get_label(feature: str) -> str:
    """Русская подпись признака; для незнакомых — аккуратный fallback."""
    if feature in FEATURE_LABELS:
        return FEATURE_LABELS[feature]
    # fallback: убрать частые суффиксы/префиксы, заменить подчёркивания
    f = feature
    for suf, rep in (("_pct", ", %"), ("_mln", ", млн"), ("_yoy", " г/г")):
        if f.endswith(suf):
            f = f[: -len(suf)] + rep
            break
    return f.replace("_", " ").strip()


def direction_ru(impact: float) -> str:
    """Знак SHAP → направление влияния на вероятность выплаты."""
    if impact > 0:
        return "↑ повышает вероятность выплаты"
    if impact < 0:
        return "↓ снижает вероятность выплаты"
    return "≈ нейтрально"
