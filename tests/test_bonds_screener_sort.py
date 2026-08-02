import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "site" / "app.js"


def _run_sort(rows: list[dict], key: str, direction: int) -> dict:
    app = APP.read_text(encoding="utf-8")
    start = app.index("const BOND_RATING_SCALE")
    end = app.index("function bondSortHeaderHTML", start)
    source = app[start:end]
    script = f"""
let BONDS_SORT = {{ key: 'ytm_net', dir: -1 }};
{source}
const input = {json.dumps(rows, ensure_ascii=False)};
const before = JSON.stringify(input);
const result = sortedBonds(input, {{key: {json.dumps(key)}, dir: {direction}}});
console.log(JSON.stringify({{
  secids: result.map((row) => row.secid),
  unchanged: before === JSON.stringify(input),
}}));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def test_numeric_sort_is_stable_and_does_not_mutate_source():
    rows = [
        {"secid": "C", "ytm_net": None},
        {"secid": "B", "ytm_net": "12.5"},
        {"secid": "A", "ytm_net": 12.5},
        {"secid": "D", "ytm_net": 8},
    ]

    ascending = _run_sort(rows, "ytm_net", 1)
    descending = _run_sort(rows, "ytm_net", -1)

    assert ascending == {"secids": ["D", "A", "B", "C"], "unchanged": True}
    assert descending == {"secids": ["A", "B", "D", "C"], "unchanged": True}


@pytest.mark.parametrize(
    ("key", "field"),
    [
        ("price_market", "price_market"),
        ("ytm_market", "ytm_market"),
        ("ytm_fair", "ytm_fair"),
        ("deviation", "deviation"),
        ("duration_years", "duration_years"),
        ("coupon_pct", "coupon_pct"),
        ("liquidity", "valtoday"),
    ],
)
def test_all_requested_numeric_fields_sort_as_numbers(key, field):
    rows = [
        {"secid": "MISSING"},
        {"secid": "BAD", field: "not-a-number"},
        {"secid": "TEN", field: "10"},
        {"secid": "TWO", field: 2},
    ]

    assert _run_sort(rows, key, 1)["secids"] == ["TWO", "TEN", "BAD", "MISSING"]
    assert _run_sort(rows, key, -1)["secids"] == ["TEN", "TWO", "BAD", "MISSING"]


def test_name_sort_uses_secid_as_stable_secondary_key():
    rows = [
        {"secid": "B", "name": "Одинаковая"},
        {"secid": "A", "name": "Одинаковая"},
        {"secid": "C", "name": "Якорь"},
    ]

    assert _run_sort(rows, "name", 1)["secids"] == ["A", "B", "C"]
    assert _run_sort(rows, "name", -1)["secids"] == ["C", "A", "B"]


def test_rating_sort_uses_credit_scale_and_missing_is_always_last():
    rows = [
        {"secid": "MISSING", "rating": None},
        {"secid": "BBB", "rating": "BBB-"},
        {"secid": "AAA", "rating": "AAA"},
        {"secid": "A", "rating": "A+"},
        {"secid": "AA", "rating": "AA"},
    ]

    assert _run_sort(rows, "rating", 1)["secids"] == ["BBB", "A", "AA", "AAA", "MISSING"]
    assert _run_sort(rows, "rating", -1)["secids"] == ["AAA", "AA", "A", "BBB", "MISSING"]


def test_maturity_sort_is_chronological_and_invalid_is_last():
    rows = [
        {"secid": "INVALID", "maturity": "not-a-date"},
        {"secid": "LATE", "maturity": "2030-01-01"},
        {"secid": "EARLY", "maturity": "2027-03-15"},
        {"secid": "EMPTY", "maturity": None},
    ]

    assert _run_sort(rows, "maturity", 1)["secids"] == ["EARLY", "LATE", "EMPTY", "INVALID"]
    assert _run_sort(rows, "maturity", -1)["secids"] == ["LATE", "EARLY", "EMPTY", "INVALID"]


def test_sort_headers_expose_accessibility_and_click_state():
    app = APP.read_text(encoding="utf-8")
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert "let BONDS_SORT = { key: 'ytm_net', dir: -1 };" in app
    assert 'aria-sort="' in app
    assert "data-bonds-sort" in app
    assert "bonds-sort-active" in app
    # Сортировка трёхсостоянийная: по возрастанию → по убыванию → без сортировки.
    # Прежний двухсостоянийный toggle (BONDS_SORT.dir * -1) снят намеренно — пользователь
    # должен иметь возможность вернуться к нейтральному порядку, не перезагружая страницу.
    assert "BONDS_SORT.dir === 1 ? { key, dir: -1 }" in app
    assert "{ key: null, dir: 0 }" in app
    assert "if (!sort || !sort.key || !sort.dir) return bonds.slice()" in app
    assert "sortedBonds(bonds)" in app
    assert "bonds.slice().sort((a, b) => b.deviation - a.deviation)" not in app
    assert ".bonds-table th[data-bonds-sort] { cursor: pointer;" in css
    assert "cursor: default;" in css
