"""Зона объёма и масштаб графика акции (site/app.js).

Оба дефекта пришли из живого использования и выглядели как «график странный».

Первый: подписи оборота — наш слой поверх ценовой шкалы библиотеки, и в одной колонке
оказывались «2,00 млрд ₽» и «140,00», где первое — рубли оборота, а второе — цена акции.
Вместе они читались как одна сломанная шкала.

Второй: кнопка периода обещала год, а показывала половину — fitContent вызывался до
того, как раскрывшаяся карточка получала ширину.

Логика вырезается из app.js и исполняется в node.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"


def _app():
    return APP.read_text(encoding="utf-8")


def _run(body, extra=""):
    app = _app()
    helpers = app[app.index("const isNum = (x)"):app.index("const instrumentTypeHint")]
    money = app[app.index("function scMoney"):app.index("function scShares")]
    ticks = app[app.index("function scNiceTicks"):]
    ticks = ticks[:ticks.index("\n}\n") + 3]
    script = f"{helpers}\n{money}\n{ticks}\n{extra}\n{body}"
    out = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


# ─────────────────────────── единицы на шкале объёма ───────────────────────────


def test_money_formatter_can_be_pinned_to_one_unit():
    """Ради этого у scMoney и есть forceUnit: на шкале единица должна быть общей.

    На круглых делениях единица и так совпадает; закрепление страхует случаи, где
    максимум и нижнее деление лежат по разные стороны от границы в тысячу раз.
    """
    result = _run("""
console.log(JSON.stringify({
  free: [2.1e9, 1.4e8, 7e7].map((v) => scMoney(v)),
  pinned: [2.1e9, 1.4e8, 7e7].map((v) => scMoney(v, 'млрд')),
}));
""")

    assert result["free"] == ["2,10 млрд ₽", "140 млн ₽", "70,0 млн ₽"], "единицы разъезжаются"
    assert all("млрд" in v for v in result["pinned"]), "закреплённая единица держится"


def test_volume_scale_pins_the_unit_by_the_maximum():
    """Единица закрепляется по максимуму — деления шкалы говорят в одних единицах."""
    source = _app()
    block = source[source.index("function scVolScaleDraw"):]
    block = block[:block.index("\n}\n")]

    assert "const unit = hasValue" in block, "единица считается один раз на всю шкалу"
    assert re.search(r"scMoney\(v,\s*unit\)", block), "все деления форматируются одной единицей"


@pytest.mark.parametrize("maximum, expected_unit", [
    (2.1e9, "млрд"), (5e8, "млн"), (4e4, "тыс"),
])
def test_unit_follows_the_largest_value_on_screen(maximum, expected_unit):
    result = _run(f"""
const max = {maximum};
const unit = max >= 1e9 ? 'млрд' : max >= 1e6 ? 'млн' : max >= 1e3 ? 'тыс' : '';
console.log(JSON.stringify({{ unit, sample: scMoney(max / 4, unit) }}));
""")

    assert result["unit"] == expected_unit
    assert expected_unit in result["sample"], "мелкие деления берут единицу максимума"


# ─────────────────────────── высота зоны объёма ───────────────────────────


def test_volume_zone_is_tall_enough_to_read():
    """При 0.8 на зону оставалось 36 px: столбцы сливались, деление помещалось одно."""
    source = _app()
    top = float(re.search(r"const SC_VOL_TOP = ([\d.]+);", source).group(1))

    assert top <= 0.75, "объёму нужна хотя бы четверть высоты"
    assert top >= 0.6, "цена остаётся главной и площадь ей нужна"


def test_price_and_volume_zones_do_not_leave_a_gap():
    """Между ними было 8% высоты, не занятых ничем."""
    source = _app()
    top = float(re.search(r"const SC_VOL_TOP = ([\d.]+);", source).group(1))
    # Ищем именно шкалу графика акции: у графика рынка свои margins, и первое
    # совпадение в файле относится к нему.
    stock_block = source[source.index("Нижняя граница цены состыкована с зоной объёма"):]
    price_bottom = float(re.search(r"bottom: ([\d.]+) \}", stock_block).group(1))
    gap = price_bottom - (1 - top)

    assert 0 <= gap <= 0.05, f"зазор между зонами {gap:.2f} — либо дыра, либо наложение"


def test_tick_count_grows_with_the_available_height():
    """Число делений считается от реальной высоты зоны, а не задано константой."""
    source = _app()
    block = source[source.index("function scVolScaleDraw"):]

    assert "zonePx" in block[:1400]
    assert "priceToCoordinate" in block[:1400], "высота берётся у графика, а не угадывается"


# ─────────────────────────── масштаб по периоду ───────────────────────────


def test_chart_refits_after_layout_is_applied():
    """Карточка раскрывается строкой таблицы: в момент setData ширины ещё нет.

    Без повторного вызова график оставлял столько баров, сколько влезает при barSpacing
    по умолчанию: на годовом периоде из 191 свечи было видно 105 — и профиль объёма,
    который считается по видимому диапазону, описывал пять месяцев вместо года.
    """
    source = _app()
    block = source[source.index("chart.priceScale('vol').applyOptions"):]
    block = block[:2000]

    assert block.count("fitContent()") >= 2, "нужен повторный вызов после применения layout"
    assert "requestAnimationFrame" in block
    fit_in_raf = block[block.index("requestAnimationFrame"):block.index("requestAnimationFrame") + 220]
    assert "fitContent" in fit_in_raf
    assert "catch" in fit_in_raf, "график мог быть удалён до следующего кадра"


def test_profile_note_states_the_range_it_was_built_for():
    """Профиль считается по видимому диапазону — подпись обязана называть его явно."""
    source = _app()

    assert "Профиль объёма за видимый диапазон" in source


def test_volume_scale_is_visually_separated_from_the_price_scale():
    """Главная причина жалобы: две шкалы в одной колонке без границы между ними."""
    source = _app()
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert "sc-vs-split" in source and "sc-vs-split" in css, "нужна граница между шкалами"
    assert "sc-vs-cap" in source, "подпись «оборот» называет, что за шкала идёт ниже"
    tick_style = css[css.index(".sc-vs-tick {"):css.index(".sc-vs-split")]
    assert "background: var(--surface)" in tick_style, \
        "полупрозрачный фон пропускал деления цены и превращал колонку в мешанину"
