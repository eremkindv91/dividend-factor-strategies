"""Профиль объёма на графике акции: расчёт по внутридневным свечам и его отрисовка.

Логика живёт в site/app.js, поэтому куски файла вырезаются по именам и исполняются
node — так тест проверяет ровно тот код, который уходит в браузер, а не его копию.
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


def _profile_source() -> str:
    app = APP.read_text(encoding="utf-8")
    return "\n".join(
        [
            _slice(app, "const isNum = (x)", "const instrumentTypeHint"),
            _slice(app, "const sawDate = (s)", "\n", ),
            _slice(app, "function scMoney(", "function stockOhlcReadout"),
            _slice(app, "const SC_VALUE_AREA", "function scNiceTicks"),
        ]
    )


def _run(expr: str) -> object:
    script = f"{_profile_source()}\nconsole.log(JSON.stringify({expr}));\n"
    out = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def _compute(candles: list[dict], bins: int) -> dict:
    return _run(f"scProfileCompute({json.dumps(candles)}, {bins})")


# ─────────────────────────── расчёт профиля ───────────────────────────


def test_turnover_is_split_across_bins_proportionally_to_overlap():
    """Свеча шире корзины раскладывается по пересечению, а не падает в одну точку."""
    profile = _compute([{"low": 100.0, "high": 110.0, "value": 1000.0}], 10)

    assert [round(b["value"], 6) for b in profile["bins"]] == [100.0] * 10
    assert profile["lo"] == 100.0 and profile["hi"] == 110.0


def test_motionless_candle_puts_all_turnover_into_one_bin():
    candles = [
        {"low": 100.0, "high": 110.0, "value": 1000.0},
        {"low": 105.0, "high": 105.0, "value": 500.0},
    ]
    profile = _compute(candles, 10)

    heavy = [i for i, b in enumerate(profile["bins"]) if b["value"] > 100.0]
    assert heavy == [5]
    assert round(profile["bins"][5]["value"], 6) == 600.0


def test_total_turnover_is_preserved():
    """Профиль перераспределяет оборот, а не создаёт и не теряет его."""
    candles = [
        {"low": 91.5, "high": 103.25, "value": 1234.5},
        {"low": 98.0, "high": 99.0, "value": 777.0},
        {"low": 88.0, "high": 88.0, "value": 12.0},
    ]
    profile = _compute(candles, 24)

    assert round(profile["total"], 6) == round(1234.5 + 777.0 + 12.0, 6)
    assert round(sum(b["value"] for b in profile["bins"]), 6) == round(profile["total"], 6)


def test_poc_is_the_price_with_the_largest_turnover():
    candles = [
        {"low": 100.0, "high": 110.0, "value": 100.0},
        {"low": 104.0, "high": 105.0, "value": 900.0},
    ]
    profile = _compute(candles, 10)

    assert 104.0 <= profile["poc"] <= 105.0
    assert profile["bins"][4]["value"] == max(b["value"] for b in profile["bins"])


def test_value_area_covers_at_least_the_declared_share():
    candles = [{"low": 100.0 + i, "high": 101.0 + i, "value": 10.0 + i * 30} for i in range(10)]
    profile = _compute(candles, 20)

    assert profile["valueAreaShare"] >= 0.70
    assert profile["val"] < profile["vah"]
    inside = sum(
        b["value"] for b in profile["bins"] if profile["val"] <= b["price"] <= profile["vah"]
    )
    assert inside / profile["total"] >= 0.70


def test_value_area_bounds_lie_inside_the_profile_range():
    candles = [{"low": 100.0 + i * 0.5, "high": 102.0 + i * 0.5, "value": 50.0 + i} for i in range(12)]
    profile = _compute(candles, 16)

    assert profile["lo"] <= profile["val"] < profile["vah"] <= profile["hi"]
    assert profile["val"] <= profile["poc"] <= profile["vah"]


@pytest.mark.parametrize(
    "candle",
    [
        {"low": 100.0, "high": 110.0, "value": 0.0},
        {"low": 100.0, "high": 110.0, "value": -5.0},
        {"low": 100.0, "high": 110.0, "value": None},
        {"low": None, "high": 110.0, "value": 100.0},
        {"low": 110.0, "high": 100.0, "value": 100.0},
        {"low": "100", "high": "110", "value": "100"},
    ],
)
def test_unusable_candles_are_dropped_not_guessed(candle):
    """Свеча без оборота или с битыми ценами выбрасывается — подстановки нет."""
    good = {"low": 200.0, "high": 210.0, "value": 400.0}
    profile = _compute([good, candle], 10)

    assert profile["total"] == 400.0
    assert profile["lo"] == 200.0 and profile["hi"] == 210.0


def test_no_usable_candles_gives_no_profile():
    assert _compute([{"low": 100.0, "high": 110.0, "value": 0.0}], 10) is None
    assert _compute([], 10) is None
    assert _compute([{"low": 100.0, "high": 110.0, "value": 5.0}], 0) is None


def test_single_price_period_collapses_to_one_level():
    """Весь период по одной цене — один уровень, а не деление на ноль."""
    profile = _compute(
        [{"low": 100.0, "high": 100.0, "value": 10.0}, {"low": 100.0, "high": 100.0, "value": 5.0}],
        10,
    )

    assert profile["bins"] == [{"price": 100.0, "value": 15.0}]
    assert profile["poc"] == profile["vah"] == profile["val"] == 100.0
    assert profile["binSize"] == 0
    assert profile["valueAreaShare"] == 1


# ─────────────────────────── выбор корзин и интервала ───────────────────────────


@pytest.mark.parametrize(
    ("count", "mobile", "expected"),
    [
        (20, False, 10),
        (20, True, 10),
        (4, False, 8),
        (60, False, 24),
        (60, True, 24),
        (400, False, 48),
        (400, True, 28),
    ],
)
def test_bin_count_shrinks_with_the_sample(count, mobile, expected):
    assert _run(f"scProfileBinCount({count}, {json.dumps(mobile)})") == expected


@pytest.mark.parametrize(("days", "interval"), [(1, 10), (60, 10), (100, 10), (101, 60), (756, 60)])
def test_intraday_interval_follows_range_length(days, interval):
    assert _run(f"scIntradayInterval({days})") == interval


def test_interval_switches_before_ten_minute_candles_stop_fitting():
    """Переход на часовые свечи происходит раньше, чем десятиминутные упрутся в потолок —
    иначе между двумя ветками появилась бы дыра, где профиль не строится вообще."""
    switch = 100
    assert _run(f"scIntradayInterval({switch})") == 10
    assert _run(f"scProfileFits({switch}, 10)") is True


# ─────────────────────────── честный отказ на длинном диапазоне ───────────────────────────


@pytest.mark.parametrize("days", [1, 30, 100, 127, 277])
def test_ranges_up_to_a_year_fit_into_a_single_pass(days):
    """Периоды кнопок 6М и 1Г должны считаться без обрезки выборки."""
    interval = _run(f"scIntradayInterval({days})")
    assert _run(f"scProfileFits({days}, {interval})") is True


@pytest.mark.parametrize("days", [781, 4600])
def test_long_ranges_are_refused_instead_of_silently_truncated(days):
    """3Г и «Макс»: свечей больше, чем отдаёт один проход, — профиль не строится вовсе.

    Обрезанная выборка не «менее точный» профиль: пагинация ISS идёт от начала периода,
    поэтому потолок отрезал бы его конец, а подпись продолжала бы обещать весь диапазон.
    """
    interval = _run(f"scIntradayInterval({days})")
    assert _run(f"scProfileFits({days}, {interval})") is False


def test_estimate_matches_measured_candle_density():
    """Оценка не должна занижать выборку: MOEX торгует и в выходные, поэтому счёт идёт
    по календарным дням. Замер на живом ISS: 84,6 десятиминутных и 14,7 часовых в день."""
    assert _run("scIntradayEstimate(92, 10)") >= 7785      # SBER, 06.05–05.08.2026
    assert _run("scIntradayEstimate(365, 60)") >= 5361     # SBER, 06.08.2025–05.08.2026


def test_estimate_stays_under_the_cap_exactly_at_the_declared_limit():
    for interval in (10, 60):
        limit = _run(f"scProfileMaxDays({interval})")
        assert _run(f"scProfileFits({limit}, {interval})") is True
        assert _run(f"scProfileFits({limit + 1}, {interval})") is False


# ─────────────────────────── отрисовка ───────────────────────────


_GEOM = "{ y: (p) => 400 - p, zoneW: 100, right: 60 }"


def _bars_html(candles: list[dict], bins: int, geom: str = _GEOM) -> str:
    return _run(
        f"scProfileBarsHTML(scProfileCompute({json.dumps(candles)}, {bins}), {geom})"
    )


def test_bar_width_is_proportional_to_turnover():
    candles = [
        {"low": 100.0, "high": 101.0, "value": 1000.0},
        {"low": 101.0, "high": 102.0, "value": 250.0},
    ]
    html = _bars_html(candles, 2)

    assert "width:100.0px" in html          # максимум занимает всю зону
    assert "width:25.0px" in html           # четверть оборота — четверть длины


def test_bars_stop_at_the_price_scale():
    html = _bars_html([{"low": 100.0, "high": 110.0, "value": 500.0}], 4)

    assert html.count("right:60.0px") >= 4  # полосы упираются в край области построения
    assert "right:164.0px" in html          # подписи уходят левее зоны (60 + 100 + 4)


def test_value_area_bars_are_marked_separately():
    candles = [{"low": 100.0 + i, "high": 101.0 + i, "value": 10.0 + i * 40} for i in range(8)]
    profile = _compute(candles, 8)
    html = _bars_html(candles, 8)

    marked = html.count('class="sc-pf-bar va"')
    inside = sum(1 for b in profile["bins"] if profile["val"] <= b["price"] <= profile["vah"])
    assert marked == inside
    assert 0 < marked < 8


def test_levels_are_drawn_with_poc_labelled():
    html = _bars_html([{"low": 100.0 + i, "high": 101.0 + i, "value": 10.0 + i * 40} for i in range(8)], 8)

    assert 'class="sc-pf-line poc"' in html
    assert 'class="sc-pf-line vah"' in html
    assert 'class="sc-pf-line val"' in html
    assert 'class="sc-pf-tag poc"' in html


def test_labels_that_would_overlap_are_dropped_but_lines_stay():
    """Уровни сошлись вплотную — подпись остаётся только у POC, линии рисуются все."""
    candles = [{"low": 100.0, "high": 100.4, "value": 900.0}, {"low": 100.4, "high": 100.5, "value": 5.0}]
    html = _bars_html(candles, 4)

    assert html.count("sc-pf-line") == 3
    assert html.count("sc-pf-tag") == 1
    assert 'class="sc-pf-tag poc"' in html


def test_bars_without_a_coordinate_are_skipped():
    """Серия не отдала координату — полосу не рисуем, а не ставим её в ноль."""
    html = _bars_html(
        [{"low": 100.0, "high": 110.0, "value": 500.0}], 4, "{ y: () => null, zoneW: 100, right: 60 }"
    )

    assert html == ""


def test_empty_profile_draws_nothing():
    assert _run(f"scProfileBarsHTML(null, {_GEOM})") == ""
    assert _run("scProfileBarsHTML({ bins: [{ price: 1, value: 1 }] }, { y: () => 10, zoneW: 0, right: 0 })") == ""


# ─────────────────────────── подпись под графиком ───────────────────────────


def test_note_states_period_source_and_value_area():
    candles = [{"low": 100.0 + i, "high": 101.0 + i, "value": 10.0 + i * 40} for i in range(8)]
    note = _run(
        f"scProfileNoteHTML(scProfileCompute({json.dumps(candles)}, 8),"
        " { from: '2026-02-12', till: '2026-08-06', interval: 10, bars: 4812 })"
    )

    assert "12.02.2026 – 06.08.2026" in note        # период, к которому относится профиль
    assert "10-минутным" in note                     # база расчёта
    assert "MOEX ISS" in note                        # источник
    assert "4 812" in note.replace(" ", " ")    # объём выборки
    assert "POC" in note and "Зона стоимости" in note
    assert "денежный оборот" in note


def test_note_names_hourly_candles_on_long_ranges():
    candles = [{"low": 100.0, "high": 110.0, "value": 500.0}]
    note = _run(
        f"scProfileNoteHTML(scProfileCompute({json.dumps(candles)}, 8),"
        " { from: '2024-01-09', till: '2026-08-06', interval: 60, bars: 7100 })"
    )

    assert "часовым" in note
