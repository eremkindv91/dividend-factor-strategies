"""Полноэкранный режим графиков (site/app.js).

Логика вырезается из app.js и исполняется в node с минимальным DOM-двойником:
проверяется поведение, а не вёрстка. Стережётся то, что ломает режим молча —
залипший класс на body, потерянный обработчик Esc и разворачивание не того элемента.
"""

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APP = ROOT / "site" / "app.js"


def _run(script_body):
    """Исполняет логику fullscreen поверх DOM-двойника и возвращает JSON-результат."""
    app = APP.read_text(encoding="utf-8")
    start = app.index("let CHART_FS_EL = null;")
    src = app[start:app.index("const STOCK_CHART_PERIODS", start)]
    harness = """
class FakeClassList {
  constructor() { this.set = new Set(); }
  add(c) { this.set.add(c); }
  remove(c) { this.set.delete(c); }
  contains(c) { return this.set.has(c); }
}
function makeEl(tag = 'div') {
  return {
    tag, classList: new FakeClassList(), attrs: {}, children: [], _focused: 0,
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k] ?? null; },
    focus() { this._focused += 1; },
    querySelector(sel) {
      if (sel === '.chart-fs-toggle') return this._button || null;
      if (sel === '.chart-fs-label') return this._label || null;
      return null;
    },
    addEventListener(type, fn) { (this._on ||= {})[type] = fn; },
  };
}
function makePanel() {
  const panel = makeEl('section');
  const button = makeEl('button');
  button._label = makeEl('span');
  button._label.textContent = 'Развернуть';
  button.setAttribute('aria-pressed', 'false');
  panel._button = button;
  return { panel, button };
}
const listeners = {};
const document = {
  body: { classList: new FakeClassList() },
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
};
const window = { addEventListener(type, fn) { (listeners[type] ||= []).push(fn); } };
const historyCalls = [];
const history = {
  state: null,
  pushState(state) { this.state = state; historyCalls.push('push'); },
  back() { this.state = null; historyCalls.push('back'); },
};
const frames = [];
function requestAnimationFrame(fn) { frames.push(fn); }
function flushFrames() { while (frames.length) frames.shift()(); }
function fireKey(key) { (listeners.keydown || []).forEach((fn) => fn({ key, stopPropagation() {} })); }
function firePopstate() { (listeners.popstate || []).forEach((fn) => fn()); }
"""
    script = f"{harness}\n{src}\n{script_body}"
    out = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def test_entering_marks_the_panel_and_locks_page_scroll():
    """Без блокировки прокрутки страница едет под развёрнутым графиком."""
    result = _run("""
const { panel, button } = makePanel();
bindChartFullscreen(panel, () => {});
chartFullscreenToggle(panel);
console.log(JSON.stringify({
  panel: panel.classList.contains('is-chart-fullscreen'),
  body: document.body.classList.contains('has-chart-fullscreen'),
  pressed: button.getAttribute('aria-pressed'),
  label: button._label.textContent,
}));
""")

    assert result == {"panel": True, "body": True, "pressed": "true", "label": "Свернуть"}


def test_leaving_releases_everything_it_took():
    result = _run("""
const { panel, button } = makePanel();
bindChartFullscreen(panel, () => {});
chartFullscreenToggle(panel);
chartFullscreenToggle(panel);
console.log(JSON.stringify({
  panel: panel.classList.contains('is-chart-fullscreen'),
  body: document.body.classList.contains('has-chart-fullscreen'),
  pressed: button.getAttribute('aria-pressed'),
  label: button._label.textContent,
  focusReturned: button._focused > 0,
}));
""")

    assert result == {"panel": False, "body": False, "pressed": "false",
                      "label": "Развернуть", "focusReturned": True}


def test_escape_leaves_fullscreen():
    """Esc — первое, что нажимают на десктопе."""
    result = _run("""
const { panel } = makePanel();
bindChartFullscreen(panel, () => {});
chartFullscreenToggle(panel);
fireKey('Escape');
console.log(JSON.stringify({ panel: panel.classList.contains('is-chart-fullscreen') }));
""")

    assert result["panel"] is False


def test_unrelated_key_does_not_leave_fullscreen():
    result = _run("""
const { panel } = makePanel();
bindChartFullscreen(panel, () => {});
chartFullscreenToggle(panel);
fireKey('a');
console.log(JSON.stringify({ panel: panel.classList.contains('is-chart-fullscreen') }));
""")

    assert result["panel"] is True


def test_back_button_leaves_fullscreen_on_phones():
    """На телефоне из полноэкранного выходят системным «назад», а не кнопкой в углу."""
    result = _run("""
const { panel } = makePanel();
bindChartFullscreen(panel, () => {});
chartFullscreenToggle(panel);
const pushed = historyCalls.slice();
firePopstate();
console.log(JSON.stringify({
  pushedOnEnter: pushed,
  panel: panel.classList.contains('is-chart-fullscreen'),
  // выход по popstate не должен звать history.back() ещё раз — иначе уедет предыдущая страница
  calls: historyCalls,
}));
""")

    assert result["pushedOnEnter"] == ["push"]
    assert result["panel"] is False
    assert result["calls"] == ["push"], "повторный back() увёл бы пользователя со страницы"


def test_resize_callback_runs_after_layout_applies():
    """Графику нужен пересчёт геометрии — но по новому layout, а не по старому."""
    result = _run("""
const { panel } = makePanel();
let calls = 0;
bindChartFullscreen(panel, () => { calls += 1; });
chartFullscreenToggle(panel);
const beforeFlush = calls;
flushFrames();
const afterEnter = calls;
chartFullscreenToggle(panel);
flushFrames();
console.log(JSON.stringify({ beforeFlush, afterEnter, afterExit: calls }));
""")

    assert result["beforeFlush"] == 0, "замер до применения layout дал бы старый размер"
    assert result["afterEnter"] == 1
    assert result["afterExit"] == 2


def test_only_one_panel_stays_expanded():
    """Второй график не должен разворачиваться поверх первого."""
    result = _run("""
const a = makePanel(), b = makePanel();
bindChartFullscreen(a.panel, () => {});
bindChartFullscreen(b.panel, () => {});
chartFullscreenToggle(a.panel);
chartFullscreenToggle(b.panel);
console.log(JSON.stringify({
  first: a.panel.classList.contains('is-chart-fullscreen'),
  second: b.panel.classList.contains('is-chart-fullscreen'),
  body: document.body.classList.contains('has-chart-fullscreen'),
}));
""")

    assert result == {"first": False, "second": True, "body": True}


def test_binding_twice_does_not_double_the_handler():
    """Перерисовка карточки не должна множить обработчики: два срабатывания = мгновенный выход."""
    result = _run("""
const { panel, button } = makePanel();
bindChartFullscreen(panel, () => {});
bindChartFullscreen(panel, () => {});
button._on.click();
console.log(JSON.stringify({ panel: panel.classList.contains('is-chart-fullscreen') }));
""")

    assert result["panel"] is True


def test_missing_panel_is_ignored():
    result = _run("""
chartFullscreenToggle(null);
bindChartFullscreen(null, () => {});
console.log(JSON.stringify({ ok: true, body: document.body.classList.contains('has-chart-fullscreen') }));
""")

    assert result == {"ok": True, "body": False}


def test_market_dialog_is_declared_outside_tab_sections():
    """<dialog> внутри скрытой секции не показывается: display:none на предке гасит и top layer.

    Диалог графика рынка лежал в секции «Новости», из-за чего showModal() с других
    вкладок открывал элемент нулевого размера — график просто не появлялся.
    """
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    dialog_at = html.index('id="market-chart-dialog"')

    assert html.index("</main>") < dialog_at, "диалог должен быть объявлен вне вкладок"
    assert "chart-fs-toggle" in html[dialog_at:dialog_at + 4000], "кнопка развёртки живёт в диалоге"


@pytest.mark.parametrize("selector", [".chart-fs-toggle", ".is-chart-fullscreen"])
def test_fullscreen_styles_are_shipped(selector):
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert selector in css


def test_fullscreen_uses_dynamic_viewport_height():
    """100vh на телефоне уезжает под панель браузера — низ графика оказывается за экраном."""
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    block = css[css.index(".is-chart-fullscreen {"):]

    assert "100dvh" in block[:400]
    assert "safe-area-inset" in block[:600], "вырез и полоса жестов не должны резать управление"
