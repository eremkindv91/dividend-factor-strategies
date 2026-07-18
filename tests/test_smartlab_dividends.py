# -*- coding: utf-8 -*-
"""Тесты парсера SmartLab-дивкалендаря (forward-источник отсечек). Детерминированно, без сети."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_smartlab_dividends as sl  # noqa: E402

# компактная фикстура, повторяющая реальную структуру smart-lab.ru/dividends/
FIXTURE = """
<html><body>
<table>
<tr><th>Название</th><th>Тикер</th><th>Период</th><th>Дивиденд, руб</th><th>Див. Дох.</th>
    <th>СД</th><th>Купить До</th><th>Дата закрытия реестра</th><th>Выплата До</th><th>Цена акции</th><th>&nbsp;</th></tr>
<tr><td><a href="/q/SBER/">Сбербанк</a></td><td>SBER</td><td>2025год</td><td>37,64</td><td>13,6%</td>
    <td></td><td>17.07.2026</td><td>20.07.2026</td><td>30.07.2026</td><td>277,43</td><td></td></tr>
<tr><td>Сбербанк-п</td><td>SBERP</td><td>2025год</td><td>37,64</td><td>13,5%</td>
    <td></td><td>17.07.2026</td><td>20.07.2026</td><td>30.07.2026</td><td>279,81</td><td></td></tr>
<tr><td>МТС</td><td>MTSS</td><td>2025</td><td>35,00</td><td>16,7%</td>
    <td></td><td>08.07.2026</td><td>09.07.2026</td><td>20.07.2026</td><td>210,00</td><td></td></tr>
<tr><td>Мусор</td><td>не тикер</td><td>x</td><td>—</td><td>—</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

WAF_PAGE = '<html><head><div class="spinner-loader"></div><script>function get_cookie_spsn(){}</script></head></html>'


def test_parse_basic():
    rows = sl.parse_dividends(FIXTURE)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"SBER", "SBERP", "MTSS"}             # мусорная строка отброшена


def test_parse_sber_fields():
    rows = sl.parse_dividends(FIXTURE)
    sber = next(r for r in rows if r["ticker"] == "SBER")
    assert sber["record_date"] == "2026-07-20"             # отсечка
    assert sber["buy_before"] == "2026-07-17"              # купить до
    assert sber["dividend"] == 37.64                        # запятая-десятичная → точка
    assert abs(sber["yield"] - 13.6) < 1e-9
    assert sber["payment_date"] == "2026-07-30"


def test_date_and_num_helpers():
    assert sl._date("20.07.2026") == "2026-07-20"
    assert sl._date("нет даты") is None
    assert sl._num("37,64") == 37.64
    assert sl._num("13,5%") == 13.5
    assert sl._num("1 234,5") == 1234.5                     # разделитель тысяч
    assert sl._num("—") is None


def test_waf_page_returns_empty():
    assert sl.parse_dividends(WAF_PAGE) == []               # анти-бот заглушка → пусто, не мусор


def test_filter_by_universe():
    rows = sl.parse_dividends(FIXTURE)
    # эмулируем фильтр как в fetch_dividends
    filt = [r for r in rows if r["ticker"] in {"SBER"}]
    assert len(filt) == 1 and filt[0]["ticker"] == "SBER"


def test_no_table_returns_empty():
    assert sl.parse_dividends("<html><body>ничего</body></html>") == []


def test_column_mapping_survives_reorder():
    # порядок колонок другой — маппинг по заголовку, не по позиции
    html = """<table>
    <tr><th>Тикер</th><th>Дата закрытия реестра</th><th>Купить До</th><th>Дивиденд, руб</th></tr>
    <tr><td>LKOH</td><td>15.12.2026</td><td>12.12.2026</td><td>500,00</td></tr>
    </table>"""
    rows = sl.parse_dividends(html)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "LKOH" and rows[0]["record_date"] == "2026-12-15"
    assert rows[0]["buy_before"] == "2026-12-12" and rows[0]["dividend"] == 500.0
