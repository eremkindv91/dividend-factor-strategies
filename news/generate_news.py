#!/usr/bin/env python3
"""Generate site/news.json from collected news inputs via Gemini JSON mode."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from time import sleep   # ВНИМАНИЕ: имя `time` уже занято под datetime.time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "news"
ARTIFACTS = NEWS_DIR / "artifacts"
SITE = ROOT / "site"
MSK = timezone(timedelta(hours=3))
DEFAULT_PROMPT = NEWS_DIR / "prompt.md"
DEFAULT_OUTPUT = SITE / "news.json"
DEFAULT_MODEL = "gemini-3.5-flash"

REQUIRED_TOP = {
    "date": str,
    "generated_at": str,
    "session_open": str,
    "external_backdrop": str,
    "market_snapshot": list,
    "overnight": list,
    "yesterday": list,
    "today_agenda": list,
}
ITEM_CATEGORIES = {"cb_policy", "banks", "markets", "macro", "corporate", "tech", "geopolitics"}
AGENDA_TYPES = {"dividend_cutoff", "earnings", "ofz_auction", "cb_minfin", "macro"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    p.add_argument("--skip-collectors", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="build prompt and validate inputs without Gemini")
    p.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    return p.parse_args()


def run(cmd: list[str]) -> None:
    print("[news] $", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def run_collectors() -> None:
    py = sys.executable
    run([py, "-m", "news.collectors.fetch_news"])
    run([py, "-m", "news.collectors.fetch_markets"])
    run([py, "-m", "news.collectors.load_calendar"])


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def last_trading_day(today) -> Any:
    """Последний ЗАВЕРШЁННЫЙ торговый день до `today` (не календарное «вчера»).

    Раньше промпт подставлял `today - 1 день`, поэтому в понедельник «вчера» было
    воскресенье, а «закрытие РФ вчера» — время, когда биржа не работала. Модель
    получала заведомо ложную опору и путала, что относить в yesterday, а что в
    overnight. По той же причине ломались брифинги после праздников.
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import trading_calendar as tc  # type: ignore
        return tc.prev_trading_day(today)
    except Exception:  # noqa: BLE001 — календарь недоступен: откат на будни без праздников
        probe = today - timedelta(days=1)
        while probe.weekday() >= 5:
            probe -= timedelta(days=1)
        return probe


# Тип выпуска. Один и тот же пайплайн отдаёт РАЗНЫЕ по смыслу сводки, а раньше
# промпт всегда представлялся «утренним блоком до открытия» — в вечернем и
# выходном выпуске это была неправда прямо в первой строке.
BRIEFING_KINDS = {
    "premarket":  ("Сводка до открытия",     "до открытия основной сессии Мосбиржи (10:00 МСК)"),
    "intraday":   ("Сводка к текущей сессии", "уже во время основной сессии Мосбиржи"),
    "evening":    ("Итоги торгового дня",     "после закрытия основной сессии Мосбиржи (18:50 МСК)"),
    "weekend":    ("Обзор недели",            "в выходной день, когда биржа закрыта"),
    "week_ahead": ("К новой неделе",          "в воскресенье, накануне открытия недели"),
}


def briefing_kind(now: datetime) -> str:
    weekday, moment = now.weekday(), now.time()
    if weekday == 5:
        return "weekend"
    if weekday == 6:
        return "week_ahead"
    if moment < time(10, 0):
        return "premarket"
    if moment < time(18, 50):
        return "intraday"
    return "evening"


def build_prompt(template: str, now: datetime) -> str:
    today = now.date()
    yesterday = last_trading_day(today)
    kind = briefing_kind(now)
    kind_title, kind_context = BRIEFING_KINDS[kind]
    values = {
        "TODAY": today.isoformat(),
        "YESTERDAY": yesterday.isoformat(),
        "BRIEFING_TITLE": kind_title,
        "BRIEFING_CONTEXT": kind_context,
        "NOW_ISO": now.isoformat(),
        "SESSION_OPEN": "10:00 MSK",
        "RU_CLOSE": f"{yesterday.isoformat()} 18:50 MSK",
        "OVERNIGHT_MARKETS": read_text(ARTIFACTS / "overnight_markets.txt").strip(),
        "NEWS_BLOB": read_text(ARTIFACTS / "news_blob.txt").strip(),
        "TODAY_CALENDAR": read_text(ARTIFACTS / "today_calendar.txt").strip(),
    }
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


# Коды, при которых модель просто перегружена и повтор через паузу обычно помогает.
# Разбор падений news.yml за 10–29.07.2026: около четверти прогонов гибли на
# «503 UNAVAILABLE: model is currently experiencing high demand», хотя это временно.
# Раньше ретрая не было — одна такая ошибка убивала весь брифинг до следующего слота.
RETRYABLE_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "INTERNAL", "500", "deadline")
GEMINI_ATTEMPTS = 4
GEMINI_BACKOFF_SEC = (8, 25, 60)


def _is_retryable(exc: Exception) -> bool:
    # Обрезанный/битый JSON — почти всегда следствие обрыва генерации, а не
    # систематической ошибки: повтор обычно даёт целый ответ.
    if isinstance(exc, json.JSONDecodeError):
        return True
    text = f"{type(exc).__name__}: {exc}"
    return any(marker.lower() in text.lower() for marker in RETRYABLE_MARKERS)


def call_gemini(prompt: str, model_name: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"google-genai is not installed: {e}") from e
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1,
        # Без явного потолка модель обрывала ответ на середине JSON, и парсинг падал
        # «Unterminated string». Брифинг — это до ~30 новостей с контекстом и источниками,
        # поэтому запас нужен ощутимый.
        max_output_tokens=32768,
    )
    last_error: Exception | None = None
    for attempt in range(1, GEMINI_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model_name, contents=prompt, config=config)
            text = getattr(response, "text", "") or ""
            if not text.strip():
                raise RuntimeError("Gemini returned empty response")
            # Разбор ДОЛЖЕН быть внутри цикла: обрезанный ответ — это тоже сбой попытки,
            # а не фатальная ошибка. Раньше json.loads стоял снаружи, поэтому удачный
            # ретрай по 503 всё равно ронял прогон, если ответ пришёл неполным.
            json.loads(text)
            if attempt > 1:
                print(f"[news] Gemini ответил с попытки {attempt}", file=sys.stderr)
            return text
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt == GEMINI_ATTEMPTS or not _is_retryable(e):
                raise
            pause = GEMINI_BACKOFF_SEC[min(attempt - 1, len(GEMINI_BACKOFF_SEC) - 1)]
            print(f"[news] Gemini временно недоступен (попытка {attempt}/{GEMINI_ATTEMPTS}): "
                  f"{e}; повтор через {pause} с", file=sys.stderr)
            sleep(pause)
    raise last_error if last_error else RuntimeError("Gemini call failed")


def ensure_iso(value: Any, field: str) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"{field}: invalid ISO datetime {value!r}") from e


def validate_item(item: Any, idx: int, section: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{section}[{idx}] is not object")
    for key in ("id", "headline", "context", "category", "published_at"):
        if not isinstance(item.get(key), str):
            raise ValueError(f"{section}[{idx}].{key} must be string")
    if item.get("category") not in ITEM_CATEGORIES:
        raise ValueError(f"{section}[{idx}].category invalid: {item.get('category')}")
    if not isinstance(item.get("investment_relevant"), bool):
        raise ValueError(f"{section}[{idx}].investment_relevant must be bool")
    imp = item.get("importance")
    if not isinstance(imp, int) or not (1 <= imp <= 5):
        raise ValueError(f"{section}[{idx}].importance must be int 1..5")
    ensure_iso(item.get("published_at"), f"{section}[{idx}].published_at")
    sources = item.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{section}[{idx}].sources must be non-empty list")
    for sidx, src in enumerate(sources):
        if not isinstance(src, dict) or not isinstance(src.get("name"), str) or not isinstance(src.get("url"), str):
            raise ValueError(f"{section}[{idx}].sources[{sidx}] invalid")


def normalize_importance_fields(data: Any) -> int:
    """Repair only Gemini's numeric formatting for the subjective 1..5 rank."""
    if not isinstance(data, dict):
        return 0
    repaired = 0
    for section in ("overnight", "yesterday", "today_agenda"):
        rows = data.get(section)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            value = item.get("importance")
            if isinstance(value, bool) or isinstance(value, int) and 1 <= value <= 5:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric):
                continue
            normalized = max(1, min(5, int(math.floor(numeric + 0.5))))
            if value != normalized:
                item["importance"] = normalized
                repaired += 1
    return repaired


def validate_news_json(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    for key, typ in REQUIRED_TOP.items():
        if not isinstance(data.get(key), typ):
            raise ValueError(f"{key} must be {typ.__name__}")
    datetime.fromisoformat(data["date"])
    ensure_iso(data["generated_at"], "generated_at")
    for idx, row in enumerate(data["market_snapshot"]):
        if not isinstance(row, dict):
            raise ValueError(f"market_snapshot[{idx}] is not object")
        for key in ("name", "value", "change_pct", "as_of", "group"):
            if not isinstance(row.get(key), str):
                raise ValueError(f"market_snapshot[{idx}].{key} must be string")
    for section in ("overnight", "yesterday"):
        for idx, item in enumerate(data[section]):
            validate_item(item, idx, section)
    for idx, item in enumerate(data["today_agenda"]):
        if not isinstance(item, dict):
            raise ValueError(f"today_agenda[{idx}] is not object")
        for key in ("time", "event", "ticker", "type"):
            if not isinstance(item.get(key), str):
                raise ValueError(f"today_agenda[{idx}].{key} must be string")
        if item.get("type") not in AGENDA_TYPES:
            raise ValueError(f"today_agenda[{idx}].type invalid: {item.get('type')}")
        imp = item.get("importance")
        if not isinstance(imp, int) or not (1 <= imp <= 5):
            raise ValueError(f"today_agenda[{idx}].importance must be int 1..5")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    args = parse_args()
    if not args.skip_collectors:
        run_collectors()
    now = datetime.now(MSK).replace(microsecond=0)
    prompt = build_prompt(args.prompt.read_text(encoding="utf-8"), now)
    (ARTIFACTS / "compiled_prompt.txt").write_text(prompt, encoding="utf-8")
    if args.dry_run:
        print(f"[news] dry-run ok, prompt_chars={len(prompt)}")
        return 0
    try:
        raw = call_gemini(prompt, args.model)
        parsed = json.loads(raw)
        repaired = normalize_importance_fields(parsed)
        data = validate_news_json(parsed)
    except Exception as e:  # noqa: BLE001
        print(f"[news] generation failed; existing {args.output} was not overwritten: {e}", file=sys.stderr)
        return 1
    # Тип выпуска проставляем САМИ, а не спрашиваем у модели: это факт о времени
    # запуска, а не предмет генерации. Фронт по нему честно подписывает блок —
    # раньше вечерняя и выходная сводка одинаково называлась «утренним брифингом».
    kind = briefing_kind(now)
    data["briefing"] = {
        "kind": kind,
        "title": BRIEFING_KINDS[kind][0],
        "context": BRIEFING_KINDS[kind][1],
        "last_trading_day": last_trading_day(now.date()).isoformat(),
    }
    write_json_atomic(args.output, data)
    print(f"[news] wrote {args.output} model={args.model} "
          f"importance_repairs={repaired} briefing={kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
