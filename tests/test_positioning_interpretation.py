"""Интерпретация позиционирования физлиц (scripts/build_positioning_interpretation.py).

Главное, что здесь стережётся: режим определяется арифметикой сторон, а не звучанием
фразы, и языковая модель не может его переписать. Одно и то же изменение Net получается
из противоположных процессов — набора длинных и закрытия коротких, — и подмена одного
другим была бы содержательной ошибкой, незаметной на глаз.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "build_positioning_interpretation.py"
    spec = importlib.util.spec_from_file_location("build_positioning_interpretation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_positioning_interpretation"] = module
    spec.loader.exec_module(module)
    return module


bpi = _load()
GROSS = 100_000          # значимым считается изменение от 500 (0.5% от gross)


# ─────────────────────────── режим потока ───────────────────────────


@pytest.mark.parametrize("d_long, d_short, expected", [
    (+10_000, 0, "long_building"),
    (0, -10_000, "short_covering"),
    (0, +10_000, "short_building"),
    (-10_000, 0, "long_unwinding"),
    (+10_000, +9_000, "two_sided_expansion"),
    (-10_000, -9_000, "deleveraging"),
    (+50, -30, "stable"),
])
def test_regime_follows_the_arithmetic_of_both_sides(d_long, d_short, expected):
    assert bpi.flow_regime(d_long, d_short, GROSS) == expected


def test_rising_net_is_not_automatically_new_longs():
    """Net растёт одинаково от набора длинных и от закрытия коротких — это разные события."""
    building = bpi.flow_regime(+10_000, 0, GROSS)
    covering = bpi.flow_regime(0, -10_000, GROSS)

    assert building == "long_building" and covering == "short_covering"
    assert bpi.REGIME_COPY[building][1] != bpi.REGIME_COPY[covering][1]


def test_falling_net_is_not_automatically_new_shorts():
    assert bpi.flow_regime(0, +10_000, GROSS) == "short_building"
    assert bpi.flow_regime(-10_000, 0, GROSS) == "long_unwinding"


def test_dominant_side_explains_the_move():
    """Когда двигаются обе стороны в разные стороны, объясняет та, чей вклад больше."""
    assert bpi.flow_regime(+9_000, +900, GROSS) == "two_sided_expansion"
    assert bpi.flow_regime(+9_000, -900, GROSS) == "long_building"
    assert bpi.flow_regime(+900, -9_000, GROSS) == "short_covering"


def test_comparable_opposite_moves_are_not_attributed_to_one_side():
    assert bpi.flow_regime(+5_000, -5_000, GROSS) == "mixed"


def test_regime_is_unknown_without_inputs():
    assert bpi.flow_regime(None, 100, GROSS) == "unknown"
    assert bpi.flow_regime(100, 100, None) == "unknown"


# ─────────────────────────── состояние и контекст ───────────────────────────


@pytest.mark.parametrize("ratio, expected", [
    (0.16, "net_long"), (-0.16, "net_short"),
    (0.01, "neutral"), (-0.01, "neutral"), (None, "unknown"),
])
def test_net_state_uses_a_normalised_ratio(ratio, expected):
    assert bpi.net_state(ratio) == expected


@pytest.mark.parametrize("ret, d_net, expected", [
    (+0.03, +100, "price_up_net_up"),
    (+0.03, -100, "price_up_net_down"),
    (-0.03, +100, "price_down_net_up"),
    (-0.03, -100, "price_down_net_down"),
    (0.001, +100, "price_flat_net_up"),
])
def test_all_price_quadrants_are_covered(ret, d_net, expected):
    context = bpi.price_context(ret, d_net)

    assert context == expected
    assert context in bpi.PRICE_COPY, "у каждого квадранта должна быть формулировка"


def test_price_context_is_unknown_without_a_price():
    assert bpi.price_context(None, 100) == "unknown"


@pytest.mark.parametrize("z, expected", [
    (0.2, "weak"), (-0.2, "weak"), (1.0, "medium"), (-1.0, "medium"),
    (2.0, "strong"), (-2.0, "strong"), (None, "unknown"),
])
def test_strength_is_measured_against_own_history(z, expected):
    assert bpi.flow_strength(z) == expected


# ─────────────────────────── текст по правилам ───────────────────────────


def test_rule_copy_never_recommends_or_predicts():
    for regime in bpi.REGIME_COPY:
        for context in list(bpi.PRICE_COPY) + ["unknown"]:
            copy = bpi.rule_copy("net_long", regime, context, "medium", 50.0, None)
            text = " ".join(copy.values()).lower()

            for word in bpi.FORBIDDEN:
                assert word not in text, f"{regime}/{context}: запрещённое «{word}»"


def test_rule_copy_fits_the_ui_limits():
    for regime in bpi.REGIME_COPY:
        copy = bpi.rule_copy("net_short", regime, "price_down_net_down", "strong", 95.0, 2.0)

        for key, limit in bpi.LIMITS.items():
            assert len(copy[key]) <= limit, f"{regime}: {key} длиннее {limit}"


def test_gross_divergence_is_surfaced_when_net_hides_it():
    """Net может стоять на месте, пока обе стороны растут — молчать об этом нельзя."""
    copy = bpi.rule_copy("net_long", "two_sided_expansion", "price_up_net_up", "weak", 50.0, 2.4)

    assert "Суммарный объём" in copy["watch"]


def test_gross_note_is_absent_when_one_side_explains_everything():
    copy = bpi.rule_copy("net_long", "long_building", "price_up_net_up", "medium", 50.0, 2.4)

    assert "Суммарный объём" not in copy["watch"]


# ─────────────────────────── валидация ответа модели ───────────────────────────


def _ok_payload(**over):
    base = {"headline": "Физлица наращивают длинные позиции",
            "summary": "Чистая позиция растёт за счёт новых длинных.",
            "watch": "Изменение заметное относительно собственной истории."}
    base.update(over)
    return base


def test_valid_llm_answer_passes():
    out, why = bpi.validate_llm(_ok_payload())

    assert out and not why


@pytest.mark.parametrize("payload, reason", [
    ({"headline": "текст"}, "неполный объект"),
    (_ok_payload(headline="Ф" * 200), "слишком длинный заголовок"),
    (_ok_payload(summary="Скоро вырастет индекс."), "прогноз"),
    (_ok_payload(watch="Это сигнал на покупку."), "рекомендация"),
    (_ok_payload(summary="Net вырос на 27446 контрактов."), "выдуманное число"),
    (_ok_payload(headline="<b>Физлица</b>"), "разметка"),
    (_ok_payload(summary=""), "пустая строка"),
    ({"headline": 1, "summary": "x", "watch": "y"}, "не строка"),
    ("не объект", "не объект"),
])
def test_unsafe_llm_answers_are_rejected(payload, reason):
    out, why = bpi.validate_llm(payload)

    assert out is None, f"должно быть отклонено: {reason}"
    assert why


def test_numbers_are_never_accepted_from_the_model():
    """Числа рисует фронтенд из фактов: сверять придуманную цифру дороже, чем не пускать."""
    out, _why = bpi.validate_llm(_ok_payload(summary="Позиция выросла на 5%."))

    assert out is None


# ─────────────────────────── сборка артефакта ───────────────────────────


def _index(dates, longs, shorts, **summary):
    base = {"as_of": dates[-1], "long": longs[-1], "short": shorts[-1],
            "net": longs[-1] - shorts[-1], "gross": longs[-1] + shorts[-1],
            "net_ratio": 0.16, "percentile": 44.0, "change_5d": 100,
            "long_change_5d": 100, "short_change_5d": 0, "gross_change_5d": 100,
            "net_change_5d_robust_z": 0.3, "gross_change_5d_robust_z": 1.5,
            "persons_long": 8000, "persons_short": 6000}
    base.update(summary)
    return {"status": "ok", "dates": dates, "long": longs, "short": shorts,
            "net": [a - b for a, b in zip(longs, shorts)], "summary": base}


def _series(dates, closes):
    return [[d, c, c, c, c] for d, c in zip(dates, closes)]


def _write(tmp_path, monkeypatch, index, series):
    (tmp_path / "futures_positions.json").write_text(
        json.dumps({"meta": {}, "indices": {"IMOEX": index}, "tickers": {}}), encoding="utf-8")
    (tmp_path / "market_history.json").write_text(
        json.dumps({"instruments": [{"id": "IMOEX", "series": series}]}), encoding="utf-8")
    monkeypatch.setattr(bpi, "POSITIONS", tmp_path / "futures_positions.json")
    monkeypatch.setattr(bpi, "HISTORY", tmp_path / "market_history.json")
    monkeypatch.setattr(bpi, "OUT", tmp_path / "out.json")


def test_build_produces_a_deterministic_artifact(tmp_path, monkeypatch):
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    longs = [100_000 + i * 1_000 for i in range(20)]
    shorts = [80_000] * 20
    _write(tmp_path, monkeypatch, _index(dates, longs, shorts), _series(dates, range(2600, 2620)))

    payload = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))

    assert payload["IMOEX"]["flow_regime"] == "long_building"
    assert payload["IMOEX"]["net_state"] == "net_long"
    assert payload["meta"]["llm_used"] is False and payload["meta"]["fallback_used"] is True
    assert payload["meta"]["input_hash"].startswith("sha256:")
    assert payload["IMOEX"]["copy"] == payload["IMOEX"]["rule_copy"], "без ключа — текст правил"


def test_price_never_comes_from_after_the_position_date(tmp_path, monkeypatch):
    """Look-ahead: объяснять вчерашние позиции сегодняшней ценой нельзя."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 11)]
    longs = [100_000 + i * 1_000 for i in range(10)]
    shorts = [80_000] * 10
    # цена уходит на три дня вперёд относительно позиций
    price_dates = dates + ["2026-07-11", "2026-07-12", "2026-07-13"]
    _write(tmp_path, monkeypatch, _index(dates, longs, shorts),
           _series(price_dates, list(range(2600, 2613))))

    payload = bpi.build(datetime(2026, 7, 14, tzinfo=timezone.utc))

    assert payload["meta"]["price_as_of"] <= payload["meta"]["position_as_of"]
    assert payload["meta"]["as_of"] == "2026-07-10"


def test_stale_price_pulls_positions_back_to_the_common_date(tmp_path, monkeypatch):
    """Иначе изменение позиций и доходность индекса относились бы к разным неделям."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    longs = [100_000 + i * 1_000 for i in range(20)]
    shorts = [80_000] * 20
    _write(tmp_path, monkeypatch, _index(dates, longs, shorts),
           _series(dates[:10], range(2600, 2610)))     # цена обрывается на 10-м дне

    payload = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))
    facts = payload["IMOEX"]["facts"]

    assert payload["meta"]["as_of"] == "2026-07-10"
    assert payload["meta"]["position_latest"] == "2026-07-20"
    assert facts["long"] == longs[9], "признаки взяты на общую дату, а не на последнюю"
    # величины, посчитанные для последней точки ряда, к сдвинутой дате не относятся
    assert facts["percentile_1y"] is None and facts["net_change_5d_robust_z"] is None


def test_missing_price_keeps_the_flow_regime(tmp_path, monkeypatch):
    """Нет цены — нет контекста, но разбор потока остаётся."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    longs = [100_000 + i * 1_000 for i in range(20)]
    _write(tmp_path, monkeypatch, _index(dates, longs, [80_000] * 20), [])

    payload = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))

    assert payload["IMOEX"]["price_context"] == "unknown"
    assert payload["IMOEX"]["flow_regime"] == "long_building"
    assert payload["IMOEX"]["copy"]["headline"]


def test_unavailable_positions_do_not_overwrite_the_artifact(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"status": "unavailable"}, [])

    assert bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc)) is None


def test_artifact_carries_its_own_audit_trail(tmp_path, monkeypatch):
    """По артефакту должно восстанавливаться, почему на эту дату показан этот текст."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    _write(tmp_path, monkeypatch, _index(dates, [100_000] * 20, [80_000] * 20),
           _series(dates, range(2600, 2620)))

    meta = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))["meta"]

    for key in ("engine_version", "prompt_version", "input_hash", "llm_used", "provider",
                "model", "fallback_used", "generated_at", "as_of", "thresholds"):
        assert key in meta
    assert "не прогноз" in meta["disclaimer"]


def test_llm_failure_falls_back_without_breaking_the_build(tmp_path, monkeypatch):
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    _write(tmp_path, monkeypatch, _index(dates, [100_000] * 20, [80_000] * 20),
           _series(dates, range(2600, 2620)))
    monkeypatch.setenv("POSITIONING_LLM_ENABLED", "1")
    monkeypatch.setattr(bpi, "call_llm", lambda brief: (None, "провайдер недоступен"))

    payload = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))

    assert payload["IMOEX"]["copy"] == payload["IMOEX"]["rule_copy"]
    assert payload["meta"]["fallback_used"] is True
    assert "недоступен" in payload["meta"]["llm_note"]


def test_llm_cannot_change_the_regime(tmp_path, monkeypatch):
    """Модель редактирует текст; режим и факты остаются вычисленными."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    longs = [100_000 + i * 1_000 for i in range(20)]
    _write(tmp_path, monkeypatch, _index(dates, longs, [80_000] * 20),
           _series(dates, range(2600, 2620)))
    monkeypatch.setenv("POSITIONING_LLM_ENABLED", "1")
    monkeypatch.setattr(bpi, "call_llm", lambda brief: ({
        "headline": "Физлица закрывают короткие позиции",
        "summary": "Совсем другой текст про другое.",
        "watch": "И другая пометка.",
    }, ""))

    payload = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))

    assert payload["IMOEX"]["flow_regime"] == "long_building", "режим считает код, не модель"
    assert payload["IMOEX"]["rule_copy"]["headline"] != payload["IMOEX"]["copy"]["headline"]
    assert payload["meta"]["llm_used"] is True


def test_llm_is_not_called_when_the_input_did_not_change(tmp_path, monkeypatch):
    """Тот же вход — тот же текст: иначе формулировка мерцает между прогонами."""
    dates = [f"2026-07-{d:02d}" for d in range(1, 21)]
    _write(tmp_path, monkeypatch, _index(dates, [100_000] * 20, [80_000] * 20),
           _series(dates, range(2600, 2620)))
    monkeypatch.setenv("POSITIONING_LLM_ENABLED", "1")
    calls = []
    monkeypatch.setattr(bpi, "call_llm", lambda brief: (calls.append(1), (
        {"headline": "Отредактированный заголовок", "summary": "Текст.", "watch": "Пометка."}, ""))[1])

    first = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))
    bpi.OUT.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    second = bpi.build(datetime(2026, 7, 21, tzinfo=timezone.utc))

    assert len(calls) == 1, "второй прогон на тех же данных не должен звать модель"
    assert second["IMOEX"]["copy"] == first["IMOEX"]["copy"]


def test_no_api_key_means_no_call(monkeypatch):
    monkeypatch.delenv("POSITIONING_LLM_API_KEY", raising=False)

    out, why = bpi.call_llm({"instrument": "IMOEX"})

    assert out is None and "ключ" in why
