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
