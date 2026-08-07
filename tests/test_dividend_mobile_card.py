"""Мобильная карточка дивидендного календаря (site/app.js).

Куски app.js вырезаются по именам и исполняются node — проверяется тот код, что уходит
в браузер. Главное, что здесь стережётся: карточку должно быть видно КЕМ она является,
а подписи дат не должны обещать больше, чем есть в данных.
"""

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "site" / "app.js"


def _slice(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a : text.index(end, a)]


def _source() -> str:
    app = APP.read_text(encoding="utf-8")
    return "\n".join([
        _slice(app, "const isNum = (x)", "const instrumentTypeHint"),
        "const mdash = '—';",
        "const instrumentAvatarHTML = (secid) => `<i>${secid}</i>`;",
        "const dividendPortfolioGross = () => null;",
        "const dividendSourceDetails = () => '<details class=\"dc-source-details\"></details>';",
        "const DIVIDEND_DECISION_LABELS = { unknown: ['статус неизвестен', 'neutral'],"
        " market_confirmed: ['подтверждено рынком', 'ok'] };",
        _slice(app, "function dividendDateLabel(", "function dividendSafeUrl("),
        _slice(app, "function dividendDecisionBadge(", "function dividendSourceDetails("),
        _slice(app, "function dividendPaymentLabel(", "function dividendCashflowStrip"),
    ])


def _run(expr: str) -> object:
    script = f"{_source()}\nconsole.log(JSON.stringify({expr}));\n"
    out = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def _card(event: dict) -> str:
    return _run(f"dividendMobileCardHTML({json.dumps(event, ensure_ascii=False)}, {{}}, '')")


BASE = {
    "secid": "YDEX", "name": "Яндекс", "instrument_type": "equity",
    "dividend_value": 110.0, "currency": "RUB", "yield_pct": 2.7,
    "price": 4074.0, "price_asof": "2026-08-06",
    "last_buy_date": "2026-09-18", "record_date": "2026-09-21",
    "payment_date": "2026-10-05", "decision_status": "market_confirmed",
    "verification_status": "official_market_data", "source_evidence": [{"source": "moex_iss"}],
}


# ─────────────────────────── идентификация бумаги ───────────────────────────


def test_card_starts_with_company_name_and_ticker():
    """Логотипа недостаточно: в ленте из десятка событий бумагу опознают по названию."""
    html = _card(BASE)

    name_at = html.index("Яндекс")
    money_at = html.index("110,00")
    assert name_at < money_at, "название компании обязано стоять выше суммы дивиденда"
    assert "YDEX" in html
    assert html.index("YDEX") < money_at, "тикер тоже выше денег"


def test_ticker_is_present_even_when_the_name_is_long():
    html = _card({**BASE, "name": "Публичное акционерное общество «Очень Длинное Название»"})

    assert "YDEX" in html


# ─────────────────────────── подписи дат ───────────────────────────


def test_announced_payment_date_is_called_a_date():
    html = _card(BASE)

    assert "Дата выплаты" in html
    assert "05.10.2026" in html
    assert "Выплата / до" not in html, "двусмысленная подпись убрана"


def test_deadline_without_announced_date_is_called_a_deadline():
    """Предельный срок номинальному держателю — не обещание конкретной даты."""
    event = {**BASE}
    del event["payment_date"]
    event["payment_deadline_nominee"] = "2026-10-05"

    html = _card(event)

    assert "Ожидаемая выплата до" in html
    assert "Дата выплаты" not in html


def test_missing_payment_data_shows_a_dash_not_a_guess():
    event = {**BASE}
    del event["payment_date"]

    html = _card(event)

    assert "Дата выплаты" in html and "—" in html


# ─────────────────────────── доходность ───────────────────────────


def test_yield_tooltip_names_the_price_and_its_date():
    """Доходность считается по конкретной цене на конкретную дату — и это должно быть видно."""
    html = _card(BASE)

    assert "06.08.2026" in html
    assert "4 074,00" in html.replace(" ", " ") or "4074,00" in html
    assert "2,7%" in html


def test_missing_yield_is_nd_not_zero():
    event = {**BASE}
    del event["yield_pct"]

    html = _card(event)

    assert "н/д" in html
    assert "0,0%" not in html


# ─────────────────────────── источник и статусы ───────────────────────────


def test_broker_source_is_not_shown_twice():
    """«Источник: Т-Инвестиции» и бейдж «календарь Т-Инвестиций» — одно и то же дважды."""
    html = _card({**BASE, "verification_status": "broker_structured_discovery"})

    assert "Источник: Т-Инвестиции" in html
    assert "календарь T-Инвестиций" not in html and "календарь Т-Инвестиций" not in html


def test_official_source_keeps_its_status_badge():
    html = _card(BASE)

    assert "Источник: MOEX ISS" in html
    assert "dc-status" in html, "у подтверждённых событий статус несёт свою информацию"


def test_currency_code_is_rendered_as_a_sign():
    html = _card(BASE)

    assert "110,00 ₽" in html.replace(" ", " ")
    assert "RUB" not in html


# ─────────────────────────── что изменилось ───────────────────────────


def test_changed_fields_are_listed_in_plain_language():
    html = _card({**BASE, "change_type": "updated",
                  "changed_fields": ["record_date", "dividend_value"]})

    assert "Что изменилось" in html
    assert "дата закрытия реестра" in html and "дивиденд на акцию" in html


def test_no_change_list_when_nothing_changed():
    html = _card(BASE)

    assert "Что изменилось" not in html


def test_previous_values_are_not_invented():
    """Источник не отдаёт прежние значения — стрелки «было → стало» быть не должно."""
    html = _card({**BASE, "change_type": "updated", "changed_fields": ["record_date"]})

    assert "→" not in html
    assert "Прежние значения источник не передаёт" in html
