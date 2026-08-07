"""Сборщик нетто-позиций физлиц во фьючерсах (scripts/build_futoi.py).

Сеть не трогается: ISS подменяется, и проверяется поведение на тех ответах, которые
источник реально отдаёт — включая отказ, приходящий телом обычного 200-ответа.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("build_futoi", ROOT / "scripts" / "build_futoi.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_futoi"] = module
    spec.loader.exec_module(module)
    return module


futoi = _load()


def _futoi_payload(rows):
    cols = ["tradedate", "tradetime", "ticker", "clgroup", "pos", "pos_long", "pos_short",
            "pos_long_num", "pos_short_num"]
    return {"futoi": {"columns": cols, "data": [[r.get(c) for c in cols] for r in rows]}}


def _row(date, pos, long, short, group="FIZ", time="23:50:00", ln=10, sn=5):
    return {"tradedate": date, "tradetime": time, "ticker": "SR", "clgroup": group,
            "pos": pos, "pos_long": long, "pos_short": short,
            "pos_long_num": ln, "pos_short_num": sn}


# ─────────────────────────── отказ источника ───────────────────────────


def test_access_denial_is_not_mistaken_for_an_empty_period():
    """Отказ приходит строкой внутри 200-ответа — принять его за «торгов не было» нельзя."""
    payload = {"futoi": {"columns": ["ERROR_MESSAGE"],
                         "data": [["Free users can't receive data for the last 14 days"]]}}

    with pytest.raises(futoi.IssError) as exc:
        futoi.block(payload, "futoi")

    assert "14 days" in str(exc.value)


def test_denied_window_does_not_silently_shorten_history(monkeypatch):
    """Отказ по одному окну не должен обнулять ряд молча: если данных нет совсем — ошибка."""
    monkeypatch.setattr(futoi, "http_json", lambda url, tries=3: {
        "futoi": {"columns": ["ERROR_MESSAGE"], "data": [["denied"]]}})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    with pytest.raises(futoi.IssError):
        futoi.fetch_series("SR", futoi.HISTORY_FROM_YEAR)


# ─────────────────────────── отбор строк ───────────────────────────


def test_only_individuals_are_taken(monkeypatch):
    rows = [_row("2020-03-02", 100, 300, -200), _row("2020-03-02", -100, 200, -300, group="YUR")]
    monkeypatch.setattr(futoi, "http_json", lambda url, tries=3: _futoi_payload(rows))
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    series = futoi.fetch_series("SR", futoi.HISTORY_FROM_YEAR)

    assert [p["pos"] for p in series] == [100]


def test_rows_violating_the_source_invariant_are_dropped(monkeypatch):
    """pos != pos_long + pos_short означает, что поля значат не то, что мы думаем."""
    rows = [_row("2020-03-02", 100, 300, -200), _row("2020-03-03", 999, 300, -200)]
    monkeypatch.setattr(futoi, "http_json", lambda url, tries=3: _futoi_payload(rows))
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    series = futoi.fetch_series("SR", futoi.HISTORY_FROM_YEAR)

    assert [p["d"] for p in series] == ["2020-03-02"]


def test_one_point_per_day_and_sorted(monkeypatch):
    """На дату приходится ~202 среза; в ряду обязан остаться ровно один, и по возрастанию."""
    rows = [_row("2020-03-03", 50, 250, -200, time="10:15:00"),
            _row("2020-03-03", 70, 270, -200, time="23:50:00"),
            _row("2020-03-02", 10, 210, -200)]
    monkeypatch.setattr(futoi, "http_json", lambda url, tries=3: _futoi_payload(rows))
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    series = futoi.fetch_series("SR", futoi.HISTORY_FROM_YEAR)

    assert [p["d"] for p in series] == ["2020-03-02", "2020-03-03"]
    assert [p["pos"] for p in series] == [10, 70]


# ─────────────────────────── статистика ───────────────────────────


def test_z_score_needs_enough_history():
    assert futoi.z_score([1, 2, 3]) is None
    assert futoi.z_score([]) is None


def test_z_score_is_zero_on_a_flat_series_with_a_jump():
    values = [100] * 60 + [100] * 59 + [200]
    z = futoi.z_score(values)
    assert z is not None and z > 3, "резкий отрыв обязан читаться как аномалия"


def test_constant_series_gives_no_z_score():
    """Нулевое отклонение — не «ноль сигм», а отсутствие базы для сравнения."""
    assert futoi.z_score([500] * 100) is None


def test_summary_reports_long_share_inside_individuals_only():
    series = [{"d": f"2026-01-{i:02d}", "pos": 10 * i, "long": 300, "short": -100,
               "long_num": 7, "short_num": 3} for i in range(1, 41)]

    summary = futoi.summarize(series)

    assert summary["as_of"] == "2026-01-40"[:10]
    assert summary["long_share"] == pytest.approx(300 / 400, rel=1e-6)
    assert summary["points"] == 40
    assert summary["min"] == 10 and summary["max"] == 400


# ─────────────────────────── состав инструментов ───────────────────────────


def test_perpetual_futures_are_excluded_from_quarterly_groups(monkeypatch):
    """Вечные фьючерсы появились в источнике позже квартальных — сумма дала бы скачок ряда."""
    payload = {"securities": {
        "columns": ["SECID", "ASSETCODE", "LASTTRADEDATE"],
        "data": [["SRU6", "SBRF", "2026-09-17"], ["SRZ6", "SBRF", "2026-12-17"],
                 ["SBERF", "SBERF", futoi.PERPETUAL_LAST_TRADE]]}}
    monkeypatch.setattr(futoi, "http_json", lambda url, tries=3: payload)

    groups = futoi.forts_quarterly_groups()

    assert groups == {"SBRF": "SRU6"}, "вечный контракт не должен попасть в ряд"


def test_mapping_is_built_through_emitent_id_not_code_similarity(monkeypatch):
    """У Сбербанка ASSETCODE=SBRF, код FUTOI=SR, тикер акции=SBER — общих букв нет."""
    monkeypatch.setattr(futoi, "futoi_universe", lambda: {"SR"})
    monkeypatch.setattr(futoi, "forts_quarterly_groups", lambda: {"SBRF": "SRU6"})
    monkeypatch.setattr(futoi, "contract_facts", lambda secid: {
        "emitent_id": 484, "group_type": "Акции", "lot_size": "100", "asset_code": "SBRF"})
    monkeypatch.setattr(futoi, "shares_by_emitent", lambda: {"484": "SBER"})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    mapping = futoi.build_mapping()

    assert mapping == {"SBER": {"futoi_code": "SR", "asset_code": "SBRF",
                                "lot_size": 100, "in_futoi": True}}


def test_non_equity_underlyings_are_skipped(monkeypatch):
    """В FUTOI есть валюты и товары — на графике акции им делать нечего."""
    monkeypatch.setattr(futoi, "futoi_universe", lambda: {"Si"})
    monkeypatch.setattr(futoi, "forts_quarterly_groups", lambda: {"USD": "SiU6"})
    monkeypatch.setattr(futoi, "contract_facts", lambda secid: {
        "emitent_id": None, "group_type": "Валюта", "lot_size": "1000", "asset_code": "USD"})
    monkeypatch.setattr(futoi, "shares_by_emitent", lambda: {})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    assert futoi.build_mapping() == {}


# ─────────────────────────── сборка файла ───────────────────────────


def _stub_build(monkeypatch, mapping, series_by_code):
    monkeypatch.setattr(futoi, "build_mapping", lambda: mapping)

    def fake_series(code, year_to, till_cap=None):
        result = series_by_code.get(code)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(futoi, "fetch_series", fake_series)


def test_traded_future_without_futoi_rows_is_a_separate_state(monkeypatch):
    """«Фьючерс есть, но данных нет» и «фьючерса нет» — разные выводы для инвестора."""
    series = [{"d": f"2026-01-{i:02d}", "pos": i, "long": 100, "short": -50,
               "long_num": 1, "short_num": 1} for i in range(1, 41)]
    _stub_build(monkeypatch,
                {"SBER": {"futoi_code": "SR", "asset_code": "SBRF", "lot_size": 100, "in_futoi": True},
                 "BSPB": {"futoi_code": "BM", "asset_code": "BSPB", "lot_size": 100, "in_futoi": True}},
                {"SR": series, "BM": futoi.IssError("нет строк")})

    payload = futoi.build()

    assert payload["tickers"]["SBER"]["status"] == "ok"
    assert payload["tickers"]["BSPB"]["status"] == "futoi_unavailable"
    assert payload["tickers"]["BSPB"]["reason_code"] == "source_error"
    assert "futoi_code" in payload["tickers"]["BSPB"], "код фьючерса известен — бумага не без него"
    assert payload["meta"]["tickers_ok"] == 1 and payload["meta"]["tickers_total"] == 2


def test_short_series_is_not_published_as_a_line(monkeypatch):
    series = [{"d": f"2026-01-{i:02d}", "pos": i, "long": 10, "short": -5,
               "long_num": 1, "short_num": 1} for i in range(1, 5)]
    _stub_build(monkeypatch, {"SBER": {"futoi_code": "SR", "asset_code": "SBRF", "lot_size": 100, "in_futoi": True}},
                {"SR": series})

    payload = futoi.build()

    assert payload["tickers"]["SBER"]["status"] == "futoi_unavailable"
    assert payload["tickers"]["SBER"]["reason_code"] == "short_series"


def test_meta_declares_delay_scope_and_the_metric_we_refuse_to_publish(monkeypatch):
    series = [{"d": f"2026-01-{i:02d}", "pos": i, "long": 100, "short": -50,
               "long_num": 1, "short_num": 1} for i in range(1, 41)]
    _stub_build(monkeypatch, {"SBER": {"futoi_code": "SR", "asset_code": "SBRF", "lot_size": 100, "in_futoi": True}},
                {"SR": series})
    monkeypatch.delenv("MOEX_TOKEN", raising=False)

    meta = futoi.build()["meta"]

    assert meta["delayed"] is True and meta["delay_days"] == 14
    assert meta["excludes_perpetual"] is True
    assert "квартальные" in meta["contracts_scope"]
    assert "открытого интереса" in meta["no_oi_share"], (
        "% OI не публикуется — причина обязана быть записана в самих данных")
    assert meta["as_of"] == "2026-01-40"[:10]


def test_token_removes_the_declared_delay(monkeypatch):
    series = [{"d": f"2026-01-{i:02d}", "pos": i, "long": 100, "short": -50,
               "long_num": 1, "short_num": 1} for i in range(1, 41)]
    _stub_build(monkeypatch, {"SBER": {"futoi_code": "SR", "asset_code": "SBRF", "lot_size": 100, "in_futoi": True}},
                {"SR": series})
    monkeypatch.setenv("MOEX_TOKEN", "x")

    meta = futoi.build()["meta"]

    assert meta["delayed"] is False and meta["delay_days"] == 0


def test_current_year_window_is_capped_by_the_last_available_day():
    """Окно текущего года не должно заканчиваться будущим: внутри закрытых 14 дней
    источник отвечает отказом на ВЕСЬ год, и ряд молча обрывается прошлым декабрём."""
    assert futoi.window_end(2026, "2026-07-23") == "2026-07-23"
    assert futoi.window_end(2025, "2026-07-23") == "2025-12-31"
    assert futoi.window_end(2020, "2026-07-23") == "2020-12-31"


def test_window_for_a_year_after_the_cap_is_skipped(monkeypatch):
    calls = []

    def fake_http(url, tries=3):
        calls.append(url)
        return _futoi_payload([_row("2020-06-01", 10, 100, -90)])

    monkeypatch.setattr(futoi, "http_json", fake_http)
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    futoi.fetch_series("SR", 2026, "2021-06-30")

    years = {url.split("from=")[1][:4] for url in calls}
    assert years == {"2020", "2021"}, "годы за пределами доступного не запрашиваются"


def test_future_absent_from_futoi_is_kept_with_a_reason(monkeypatch):
    """У БСП торгуются BSU6/BSZ6, но кода BS в FUTOI нет вовсе.

    Выбросить такую бумагу из файла — значит заставить интерфейс сказать «фьючерса нет»,
    что неправда. В файле она остаётся со статусом и причиной.
    """
    monkeypatch.setattr(futoi, "futoi_universe", lambda: {"SR"})
    monkeypatch.setattr(futoi, "forts_quarterly_groups", lambda: {"BSPB": "BSU6"})
    monkeypatch.setattr(futoi, "contract_facts", lambda secid: {
        "emitent_id": 777, "group_type": "Акции", "lot_size": "100", "asset_code": "BSPB"})
    monkeypatch.setattr(futoi, "shares_by_emitent", lambda: {"777": "BSPB"})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    mapping = futoi.build_mapping()
    assert mapping["BSPB"]["in_futoi"] is False

    monkeypatch.setattr(futoi, "build_mapping", lambda: mapping)
    payload = futoi.build()

    row = payload["tickers"]["BSPB"]
    assert row["status"] == "futoi_unavailable"
    assert row["futoi_code"] == "BS", "код известен — бумага не «без фьючерса»"
    assert row["reason_code"] == "not_in_futoi", (
        "причина обязана быть машинным кодом: формулировку выбирает интерфейс")


def test_preferred_and_mini_series_never_win_over_the_ordinary_contract(monkeypatch):
    """У Татнефти emitent_id общий для TT (обыкновенные) и TP (привилегированные).

    Без явного выбора побеждала бы последняя серия по алфавиту, и график обыкновенной
    акции показывал бы позиции по префам — молча и незаметно.
    """
    facts = {
        "TTU6": {"emitent_id": 1, "group_type": "Акции", "lot_size": "100",
                 "asset_code": "TATN", "on_preferred": False, "is_mini": False},
        "TPU6": {"emitent_id": 1, "group_type": "Акции", "lot_size": "100",
                 "asset_code": "TATP", "on_preferred": True, "is_mini": False},
    }
    monkeypatch.setattr(futoi, "futoi_universe", lambda: {"TT", "TP"})
    monkeypatch.setattr(futoi, "forts_quarterly_groups", lambda: {"TATN": "TTU6", "TATP": "TPU6"})
    monkeypatch.setattr(futoi, "contract_facts", lambda secid: facts[secid])
    monkeypatch.setattr(futoi, "shares_by_emitent", lambda: {"1": "TATN"})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    assert futoi.build_mapping()["TATN"]["futoi_code"] == "TT"


def test_series_present_in_futoi_wins_over_one_that_is_absent(monkeypatch):
    """Между двумя сериями одной бумаги выбирается та, по которой данные вообще есть."""
    facts = {
        "PZU6": {"emitent_id": 2, "group_type": "Акции", "lot_size": "1",
                 "asset_code": "PLZL", "on_preferred": False, "is_mini": False},
        "PXU6": {"emitent_id": 2, "group_type": "Акции", "lot_size": "1",
                 "asset_code": "PLZLM", "on_preferred": False, "is_mini": True},
    }
    monkeypatch.setattr(futoi, "futoi_universe", lambda: {"PX"})       # основной серии в FUTOI нет
    monkeypatch.setattr(futoi, "forts_quarterly_groups", lambda: {"PLZL": "PZU6", "PLZLM": "PXU6"})
    monkeypatch.setattr(futoi, "contract_facts", lambda secid: facts[secid])
    monkeypatch.setattr(futoi, "shares_by_emitent", lambda: {"2": "PLZL"})
    monkeypatch.setattr(futoi.time, "sleep", lambda _s: None)

    picked = futoi.build_mapping()["PLZL"]
    assert picked["futoi_code"] == "PX" and picked["in_futoi"] is True
