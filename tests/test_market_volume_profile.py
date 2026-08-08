"""Профиль объёма на графике индекса (site/app.js, market-chart-dialog).

Индекс МосБиржи не торгуется — оборота у него не существует, поэтому профиль строится
по вечному фьючерсу IMOEXF и обязан быть так и подписан. Тесты стерегут три вещи, на
которых эта конструкция ломается молча: отсутствие слоя в разметке диалога, подмену
фьючерсных данных дневными свечами индекса и появление профиля у инструментов, для
которых торгуемый эквивалент не выбран.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"
INDEX = ROOT / "site" / "index.html"


def _app():
    return APP.read_text(encoding="utf-8")


# ─────────────────────────── слой в разметке ───────────────────────────


def test_dialog_has_a_volume_profile_layer():
    """Без слоя в диалоге профиль негде рисовать — именно этого и не было."""
    html = INDEX.read_text(encoding="utf-8")
    dialog_at = html.index('id="market-chart-dialog"')
    dialog = html[dialog_at:]

    assert 'id="market-volume-profile"' in dialog, "в диалоге графика нет слоя профиля"
    assert 'class="market-volume-profile"' in dialog


def test_layer_shares_the_coordinate_system_with_the_canvas():
    """Полосы позиционируются по координатам графика: общая обёртка обязательна."""
    html = INDEX.read_text(encoding="utf-8")
    plot = html[html.index('class="market-chart-plot"'):]
    plot = plot[:plot.index("</div>", plot.index('id="market-volume-profile"'))]

    assert 'id="market-chart-canvas"' in plot
    assert 'id="market-volume-profile"' in plot
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    assert ".market-chart-plot { position: relative; }" in css


def test_toggle_lives_next_to_the_chart_mode():
    html = INDEX.read_text(encoding="utf-8")
    head = html[html.index('id="market-chart-mode"'):]

    assert 'id="market-pf-toggle"' in head[:400], "переключатель должен стоять рядом с режимом"


# ─────────────────────────── источник данных ───────────────────────────


def test_profile_is_built_from_the_futures_not_the_index():
    """Индекс не торгуется: профиль по его «объёму» был бы выдумкой."""
    source = _app()
    proxy = re.search(r"const MARKET_PROFILE_PROXY = \{(.+?)\};", source, re.S).group(1)

    assert "IMOEX:" in proxy and "IMOEXF" in proxy
    assert "forts" in source[source.index("function marketPfFetchCandles"):
                             source.index("function marketPfFetchCandles") + 800], \
        "свечи берутся со срочного рынка"


def test_only_instruments_with_a_verified_proxy_get_a_profile():
    """MCFTR, RTSI и валютные пары остаются без профиля, пока эквивалент не выбран."""
    source = _app()
    proxy = re.search(r"const MARKET_PROFILE_PROXY = \{(.+?)\};", source, re.S).group(1)

    for absent in ("MCFTR", "RTSI", "USD000UTSTOM", "CNYRUB_TOM"):
        assert absent not in proxy, f"{absent} получил бы непроверенный proxy"


def test_toggle_is_hidden_where_there_is_no_proxy():
    source = _app()
    setup = source[source.index("function marketPfSetup"):]
    setup = setup[:setup.index("\n}\n")]

    assert "button.hidden = !proxy" in setup, "кнопка не должна обещать то, чего нет"


def test_roubles_are_derived_from_contracts_by_the_spec():
    """ISS по срочному рынку отдаёт VALUE = 0 — рубли считаются из контрактов."""
    source = _app()
    block = source[source.index("function marketPfFetchCandles"):]
    block = block[:block.index("\nfunction ")]

    assert "d[iVol] * d[iClose] * multiplier" in block
    mult = source[source.index("function marketPfMultiplier"):]
    assert "STEPPRICE" in mult[:900] and "MINSTEP" in mult[:900], \
        "множитель берётся из спецификации ISS, а не зашивается в код"


def test_daily_candles_are_never_used_as_a_substitute():
    """Дневная свеча не содержит распределения сделок внутри диапазона."""
    source = _app()
    refresh = source[source.index("function marketPfRefresh"):]
    refresh = refresh[:refresh.index("\nfunction ")]

    assert "Профиль недоступен" in refresh
    assert "не подделываем" in refresh
    assert "interval=${interval}" in source[source.index("function marketPfFetchCandles"):
                                            source.index("function marketPfFetchCandles") + 900]


def test_insufficient_data_reports_unavailable():
    source = _app()
    refresh = source[source.index("function marketPfRefresh"):]
    refresh = refresh[:refresh.index("\nfunction ")]

    assert "usable.length < 20" in refresh, "мало свечей — профиль не строится"
    assert refresh.count("Профиль недоступен") >= 3, \
        "каждый отказ обязан быть назван, а не оставлять пустое место"


# ─────────────────────────── поведение ───────────────────────────


def test_profile_reuses_the_shared_algorithm():
    """Второй алгоритм означал бы два определения POC, расходящихся со временем."""
    source = _app()
    refresh = source[source.index("function marketPfRefresh"):]

    assert "scProfileCompute(" in refresh[:4000]
    draw = source[source.index("function marketPfDraw"):]
    assert "scProfileBarsHTML(" in draw[:1600]


def test_profile_recomputes_on_pan_and_zoom():
    source = _app()
    block = source[source.index("MARKET_PRICE_SERIES = primary;"):]
    block = block[:1200]

    assert "subscribeVisibleLogicalRangeChange" in block
    assert "marketPfRefresh" in block and "marketPfDraw" in block
    assert "setTimeout" in block, "сеть за профилем дорогая — вызов откладывается"


def test_profile_uses_only_the_visible_range():
    source = _app()
    refresh = source[source.index("function marketPfRefresh"):]
    refresh = refresh[:refresh.index("\nfunction ")]

    assert "getVisibleLogicalRange" in refresh
    assert "rows[i0][0]" in refresh and "rows[i1][0]" in refresh


def test_bars_do_not_cover_the_price_scale():
    source = _app()
    draw = source[source.index("function marketPfDraw"):]
    draw = draw[:draw.index("\nfunction ")]

    assert "priceScale('right').width()" in draw, "ширина шкалы спрашивается у графика"
    assert "right: scaleW" in draw


def test_tooltip_reports_price_and_rouble_turnover():
    source = _app()
    tip = source[source.index("function marketPfTooltipHTML"):]
    tip = tip[:tip.index("\nfunction ")]

    assert "coordinateToPrice" in tip, "уровень берётся под курсором"
    assert "scMoney(bin.value)" in tip, "оборот показывается в рублях"
    assert "POC" in tip


def test_note_names_the_proxy_explicitly():
    """Пользователь обязан видеть, что оборот пришёл с фьючерса, а не с индекса."""
    source = _app()
    note = source[source.index("function marketPfNoteHTML"):]
    note = note[:note.index("\nfunction ")]

    assert "Профиль объёма · ${esc(proxy.label)}" in note
    assert "не торгуется" in note
    assert "базиса" in note, "уровни фьючерса отличаются от индекса — это надо сказать"


# ─────────────────────────── масштаб периода ───────────────────────────


def test_bars_can_be_compressed_enough_to_fit_five_years():
    """На «5Л» в ряду 1260 дневных точек: при minBarSpacing 3 им нужно 3780 px.

    График молча показывал последние 270 дней — кнопка обещала пять лет, а профиль
    объёма, который считается по видимому диапазону, описывал этот же обрезок.
    """
    source = _app()
    spacings = [float(m) for m in re.findall(r"minBarSpacing: ([\d.]+)", source)]

    assert spacings, "minBarSpacing должен быть задан явно"
    for value in spacings:
        assert value <= 0.5, f"minBarSpacing {value} не даёт показать длинные периоды"


def test_market_chart_rescales_after_layout():
    """Диалог открывается вместе с построением: в момент setData ширины ещё нет."""
    source = _app()
    block = source[source.index("marketFitPeriod(rows.length);"):]
    block = block[:600]

    assert "requestAnimationFrame" in block
    assert block.count("marketFitPeriod(") >= 1
    assert "catch" in block, "график мог быть удалён до следующего кадра"


def test_period_scale_is_set_explicitly_not_via_fitcontent():
    """fitContent библиотека здесь игнорирует — окно остаётся тем, что влезло само.

    Проверено вживую: ни fitContent, ни setVisibleLogicalRange не меняли видимый
    диапазон, barSpacing замирал на значении по умолчанию, и «5Л» показывал девять
    месяцев. Масштаб считается из ширины области построения и числа баров.
    """
    source = _app()
    fit = source[source.index("function marketFitPeriod"):]
    fit = fit[:fit.index("\nfunction ")]

    assert "plot / count" in fit, "шаг = ширина области построения / число баров"
    assert "priceScale('right').width()" in fit, "ценовая шкала не входит в область построения"
    assert "scrollToPosition(0" in fit, "правый край прижимается к последней свече"
    assert "minBarSpacing" in fit, "ниже минимума библиотека всё равно не сожмёт"

    draw = source[source.index("function drawMarketChart"):]
    draw = draw[:draw.index("\nfunction ")]
    assert draw.count("marketFitPeriod(") >= 2, "нужен повтор после применения layout"
