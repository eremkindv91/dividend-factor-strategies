import json
import subprocess
from pathlib import Path

from scripts.build_data import classify_ranking_quality


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"


def test_clean_complete_row_is_eligible_for_main_ranking():
    result = classify_ranking_quality("ok", [])

    assert result == {"status": "eligible", "eligible": True, "reasons": []}


def test_extreme_yield_payout_and_stale_price_require_review():
    result = classify_ranking_quality(
        "ok", ["y_exp_high", "payout_high", "price_stale", "unrelated_flag"]
    )

    assert result["status"] == "review"
    assert result["eligible"] is False
    assert result["reasons"] == ["payout_high", "price_stale", "y_exp_high"]


def test_incomplete_row_is_never_ranking_eligible():
    result = classify_ranking_quality("insufficient_data", [])

    assert result["status"] == "insufficient"
    assert result["eligible"] is False


def test_equities_ui_defaults_to_fail_closed_ranking_filter():
    html = (ROOT / "site/index.html").read_text(encoding="utf-8")
    app = (ROOT / "site/app.js").read_text(encoding="utf-8")

    assert '<option value="rankable" selected>Основной рейтинг</option>' in html
    assert 'id="stock-quality-gate"' in html
    assert "function stockRankingEligible(t)" in app
    assert "payout выше 100%" in app


def _run_view(tickers, query, status_filter="rankable"):
    """computeView с подставленным DOM: проверяем отбор, а не вёрстку."""
    app = APP.read_text(encoding="utf-8")
    start = app.index("function stockMatchesQuery")
    src = app[start:app.index("function renderStockChips", start)]
    helpers = app[app.index("const isNum = (x)"):app.index("const instrumentTypeHint")]
    gate = app[app.index("const STOCK_REVIEW_FLAGS"):app.index("function stockRankingStatus")]
    script = f"""
{helpers}
{gate}
function stockRankingStatus(t) {{
  if (t.status !== 'ok') return 'insufficient';
  return stockRankingEligible(t) ? 'eligible' : 'review';
}}
const DATA = {{ tickers: {json.dumps(tickers, ensure_ascii=False)} }};
let sortKey = 'ticker', sortDir = 1;
const values = {{ search: {json.dumps(query)}, sector: '', statusFilter: {json.dumps(status_filter)} }};
document = {{ getElementById: (id) => ({{ value: values[id] }}) }};
{src}
console.log(JSON.stringify(computeView().map((t) => t.ticker)));
"""
    out = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


SEVERSTAL = {"ticker": "CHMF", "name": "СевСт-ао", "full_name": "Северсталь (ПАО)ао",
             "status": "ok", "sector": "Металлы и добыча", "flags": ["payout_high"]}
SBER = {"ticker": "SBER", "name": "Сбербанк", "full_name": "Сбербанк России ПАО ао",
        "status": "ok", "sector": "Финансы", "flags": []}


def test_search_finds_a_paper_hidden_by_the_quality_filter():
    """Поиск — намерение найти конкретную бумагу; фильтр рейтинга его не перебивает.

    У Северстали payout выше 100%, поэтому она в разделе «на проверке». Раньше поиск
    по ней при фильтре по умолчанию возвращал пустоту, и бумага выглядела отсутствующей.
    """
    assert _run_view([SBER, SEVERSTAL], "CHMF") == ["CHMF"]
    assert _run_view([SBER, SEVERSTAL], "") == ["SBER"], "без запроса фильтр работает как прежде"


def test_search_matches_the_full_company_name():
    """На доске бумага зовётся «СевСт-ао», а ищут её как «Северсталь»."""
    assert _run_view([SBER, SEVERSTAL], "северсталь") == ["CHMF"]
    assert _run_view([SBER, SEVERSTAL], "сбербанк россии") == ["SBER"]


def test_search_still_matches_ticker_and_short_name():
    assert _run_view([SBER, SEVERSTAL], "севст") == ["CHMF"]
    assert _run_view([SBER, SEVERSTAL], "sber") == ["SBER"]


def test_missing_full_name_does_not_break_search():
    no_full = {"ticker": "MSNG", "name": "+МосЭнерго", "status": "ok",
               "sector": "Энергетика", "flags": []}

    assert _run_view([no_full], "мосэнерго") == ["MSNG"]
    assert _run_view([no_full], "MSNG") == ["MSNG"]
