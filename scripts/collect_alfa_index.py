#!/usr/bin/env python3
"""Collect the public Alfa Index from the latest official "Hod torgov" article.

The collector is intentionally conservative: it checks a small date window, parses
only the article body, validates provenance and confidence, and never replaces a
last-good value with an empty or unverified result.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "alfa_index.json"
DEFAULT_CURRENT = ROOT / "data" / "alfa-index.json"
DEFAULT_HISTORY = ROOT / "data" / "alfa-index-history.json"
DEFAULT_SITE_CURRENT = ROOT / "site" / "alfa-index.json"
DEFAULT_SITE_HISTORY = ROOT / "site" / "alfa-index-history.json"
DEFAULT_DIAGNOSTIC = ROOT / ".artifacts" / "alfa-index" / "diagnostic.json"

LOGGER = logging.getLogger("alfa_index")
WAF_MARKERS = (
    "if you are not a bot",
    "access denied",
    "request-id",
    "<h1>forbidden</h1>",
)


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int | None
    text: str | None
    error: str | None = None


@dataclass(frozen=True)
class ArticleContent:
    text: str
    method: str
    title: str | None
    published_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class IndexCandidate:
    value: int
    score: int
    pattern: str
    context: str
    position: int
    value_period: str


@dataclass(frozen=True)
class ArticleResult:
    url: str
    article_date: date
    title: str | None
    published_at: str | None
    updated_at: str | None
    body_method: str
    candidate: IndexCandidate


class DiscoveryError(RuntimeError):
    def __init__(self, status: str, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.status = status
        self.attempts = attempts


PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    (
        "value_out_of_100",
        re.compile(
            r"(?:Значение\s+)?Альфа[-\u2011\u2013\u2014\s]?Индекс(?:а)?"
            r"[^0-9]{0,80}(?P<value>\d{1,3})\s*(?:/|из)\s*100",
            re.IGNORECASE,
        ),
        5,
    ),
    (
        "points_out_of_100",
        re.compile(
            r"(?:Значение\s+)?Альфа[-\u2011\u2013\u2014\s]?Индекс(?:а)?"
            r"[^0-9]{0,80}(?P<value>\d{1,3})\s*балл(?:а|ов)?\s+из\s+100",
            re.IGNORECASE,
        ),
        5,
    ),
    (
        "changed_from_to",
        re.compile(
            r"Альфа[-\u2011\u2013\u2014\s]?Индекс\s+"
            r"(?:вырос|поднялся|снизился|опустился|упал)[^0-9]{0,40}"
            r"с\s+\d{1,3}\s+до\s+(?P<value>\d{1,3})",
            re.IGNORECASE,
        ),
        5,
    ),
    (
        "changed_to",
        re.compile(
            r"Альфа[-\u2011\u2013\u2014\s]?Индекс\s+"
            r"(?:вырос|поднялся|снизился|опустился|упал)\s+до\s+"
            r"(?P<value>\d{1,3})",
            re.IGNORECASE,
        ),
        5,
    ),
    (
        "value_points",
        re.compile(
            r"(?:Значение\s+)?Альфа[-\u2011\u2013\u2014\s]?Индекс(?:а)?\s*"
            r"(?:составляет|равно|равен|[-\u2011\u2013\u2014:])\s*"
            r"(?P<value>\d{1,3})\s*(?:балл(?:а|ов)?|пункт(?:а|ов)?)",
            re.IGNORECASE,
        ),
        5,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--site-current", type=Path, default=DEFAULT_SITE_CURRENT)
    parser.add_argument("--site-history", type=Path, default=DEFAULT_SITE_HISTORY)
    parser.add_argument("--diagnostic", type=Path, default=DEFAULT_DIAGNOSTIC)
    parser.add_argument("--now", help="ISO datetime override for deterministic checks")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "source_base_url",
        "sitemap_url",
        "article_slug",
        "lookback_days",
        "request_timeout_seconds",
        "max_retries",
        "max_response_bytes",
        "request_delay_seconds",
        "history_days",
        "timezone",
        "stale_after_business_days",
        "stale_check_hour",
        "min_candidate_score",
        "parser_version",
    }
    missing = required - set(config)
    if missing:
        raise ValueError(f"config is missing keys: {sorted(missing)}")
    return config


def parse_now(value: str | None, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if not value:
        return datetime.now(tz).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).replace(microsecond=0)


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").replace("\u202f", " ").split())


def is_official_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "alfabank.ru" or host.endswith(".alfabank.ru"))


def article_date_from_url(url: str) -> date | None:
    match = re.search(r"hod-torgov-(\d{2})-(\d{2})-(\d{4})(?:/|$)", urlparse(url).path)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _jsonld_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _jsonld_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _jsonld_objects(child)


def _article_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        for obj in _jsonld_objects(parsed):
            raw_type = obj.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if any(str(item).lower() in {"article", "newsarticle"} for item in types):
                rows.append(obj)
    return rows


def extract_article_content(html: str) -> ArticleContent:
    lowered = html.lower()
    if any(marker in lowered for marker in WAF_MARKERS):
        raise ValueError("WAF/error page, not an article")
    soup = BeautifulSoup(html, "html.parser")
    jsonld = _article_jsonld(soup)
    for row in jsonld:
        body = row.get("articleBody")
        if isinstance(body, str) and normalize_text(body):
            return ArticleContent(
                text=normalize_text(body),
                method="articleBody",
                title=normalize_text(str(row.get("headline"))) if row.get("headline") else None,
                published_at=str(row.get("datePublished")) if row.get("datePublished") else None,
                updated_at=str(row.get("dateModified")) if row.get("dateModified") else None,
            )

    title_node = soup.find("h1")
    title = normalize_text(title_node.get_text(" ", strip=True)) if title_node else None
    selectors = (
        ("article", soup.find("article")),
        ("article", soup.find(attrs={"itemprop": "articleBody"})),
        ("article", soup.find(class_=re.compile(r"(?:article|publication).*(?:body|content)", re.I))),
        ("main", soup.find("main")),
    )
    for method, node in selectors:
        if node is None:
            continue
        text_value = normalize_text(node.get_text(" ", strip=True))
        if text_value:
            metadata = jsonld[0] if jsonld else {}
            return ArticleContent(
                text=text_value,
                method=method,
                title=title or (normalize_text(str(metadata.get("headline"))) if metadata.get("headline") else None),
                published_at=str(metadata.get("datePublished")) if metadata.get("datePublished") else None,
                updated_at=str(metadata.get("dateModified")) if metadata.get("dateModified") else None,
            )
    raise ValueError("article body not found")


def extract_index_candidates(text: str, body_method: str) -> list[IndexCandidate]:
    clean = normalize_text(text)
    method_bonus = {"articleBody": 3, "article": 2, "main": 1}.get(body_method, 0)
    candidates: dict[tuple[int, int], IndexCandidate] = {}
    for pattern_name, pattern, base_score in PATTERNS:
        for match in pattern.finditer(clean):
            value = int(match.group("value"))
            start = max(0, match.start() - 90)
            end = min(len(clean), match.end() + 90)
            context = clean[start:end]
            score = base_score + method_bonus
            if re.search(r"Значение\s+Альфа", match.group(0), re.IGNORECASE):
                score += 4
            if re.search(r"на\s+утро", context, re.IGNORECASE):
                score += 1
            value_period = "morning" if re.search(r"на\s+утро", context, re.IGNORECASE) else "intraday"
            candidate = IndexCandidate(
                value=value,
                score=score,
                pattern=pattern_name,
                context=context,
                position=match.start(),
                value_period=value_period,
            )
            key = (match.start(), value)
            if key not in candidates or candidate.score > candidates[key].score:
                candidates[key] = candidate
    return sorted(candidates.values(), key=lambda row: (-row.score, row.position))


def select_best_candidate(candidates: list[IndexCandidate], min_score: int) -> IndexCandidate:
    valid = [row for row in candidates if 0 <= row.value <= 100 and row.score >= min_score]
    if not valid:
        raise ValueError("no high-confidence Alfa Index candidate")
    valid.sort(key=lambda row: (-row.score, row.value_period != "morning", row.position))
    return valid[0]


def parse_article(html: str, url: str, now: datetime, config: dict[str, Any]) -> ArticleResult:
    if not is_official_url(url):
        raise ValueError("article URL is not on official alfabank.ru domain")
    if config["article_slug"] not in urlparse(url).path:
        raise ValueError("URL is not a Hod torgov article")
    article_date = article_date_from_url(url)
    if article_date is None:
        raise ValueError("article date missing from URL")
    if article_date > now.date():
        raise ValueError("article date is in the future")
    content = extract_article_content(html)
    if not re.search(r"Альфа[-\u2011\u2013\u2014\s]?Индекс", content.text, re.IGNORECASE):
        raise ValueError("article body has no Alfa Index marker")
    candidates = extract_index_candidates(content.text, content.method)
    candidate = select_best_candidate(candidates, int(config["min_candidate_score"]))
    return ArticleResult(
        url=url,
        article_date=article_date,
        title=content.title,
        published_at=content.published_at,
        updated_at=content.updated_at,
        body_method=content.method,
        candidate=candidate,
    )


async def fetch_html(session: aiohttp.ClientSession, url: str, config: dict[str, Any]) -> FetchResult:
    timeout = aiohttp.ClientTimeout(total=float(config["request_timeout_seconds"]))
    max_bytes = int(config["max_response_bytes"])
    retries = max(1, int(config["max_retries"]))
    last_status: int | None = None
    last_error: str | None = None
    for attempt in range(retries):
        if attempt:
            await asyncio.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
        try:
            async with session.get(url, timeout=timeout, allow_redirects=True) as response:
                last_status = response.status
                if response.status == 404:
                    return FetchResult(url=str(response.url), status=404, text=None, error="not found")
                if response.status != 200:
                    last_error = f"HTTP {response.status}"
                    if response.status not in {403, 429} and response.status < 500:
                        break
                    continue
                raw = await response.content.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    return FetchResult(url=str(response.url), status=200, text=None, error="response too large")
                charset = response.charset or "utf-8"
                return FetchResult(
                    url=str(response.url),
                    status=200,
                    text=raw.decode(charset, errors="replace"),
                    error=None,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
    return FetchResult(url=url, status=last_status, text=None, error=last_error or "request failed")


def direct_article_urls(now: datetime, config: dict[str, Any]) -> list[str]:
    root = "https://alfabank.ru/alfa-investor/t/"
    return [
        urljoin(root, f"{config['article_slug']}-{(now.date() - timedelta(days=offset)):%d-%m-%Y}/")
        for offset in range(int(config["lookback_days"]) + 1)
    ]


def links_from_html(html: str, base_url: str, slug: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for node in soup.find_all("a", href=True):
        url = urljoin(base_url, str(node["href"]))
        if slug in urlparse(url).path and is_official_url(url):
            urls.append(url)
    return urls


def sitemap_locations(xml_text: str) -> tuple[str, list[str]]:
    root = ET.fromstring(xml_text)
    kind = root.tag.rsplit("}", 1)[-1]
    locations = [normalize_text(node.text or "") for node in root.findall(".//{*}loc") if node.text]
    return kind, locations


async def fallback_discovery_urls(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> list[str]:
    discovered: list[str] = []
    portal = await fetch_html(session, str(config["source_base_url"]), config)
    attempts.append({"url": portal.url, "status": portal.status, "error": portal.error, "kind": "portal"})
    if portal.text:
        discovered.extend(links_from_html(portal.text, portal.url, str(config["article_slug"])))

    sitemap = await fetch_html(session, str(config["sitemap_url"]), config)
    attempts.append({"url": sitemap.url, "status": sitemap.status, "error": sitemap.error, "kind": "sitemap"})
    if not sitemap.text:
        return discovered
    try:
        kind, locations = sitemap_locations(sitemap.text)
    except ET.ParseError as exc:
        attempts.append({"url": sitemap.url, "status": 200, "error": f"invalid sitemap: {exc}", "kind": "sitemap"})
        return discovered
    if kind == "urlset":
        discovered.extend(url for url in locations if config["article_slug"] in url)
        return discovered

    preferred = [url for url in locations if any(token in url.lower() for token in ("alfa", "invest"))]
    sitemap_urls = (preferred or locations)[: int(config.get("max_sitemap_files", 8))]
    for child_url in sitemap_urls:
        await asyncio.sleep(float(config["request_delay_seconds"]))
        child = await fetch_html(session, child_url, config)
        attempts.append({"url": child.url, "status": child.status, "error": child.error, "kind": "sitemap_child"})
        if not child.text:
            continue
        try:
            _, child_locations = sitemap_locations(child.text)
        except ET.ParseError:
            continue
        discovered.extend(url for url in child_locations if config["article_slug"] in url)
    return discovered


async def discover_latest_article(
    session: aiohttp.ClientSession,
    now: datetime,
    config: dict[str, Any],
) -> tuple[ArticleResult, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    checked: set[str] = set()
    blocked = 0

    async def inspect(url: str, kind: str) -> ArticleResult | None:
        nonlocal blocked
        if url in checked:
            return None
        checked.add(url)
        fetched = await fetch_html(session, url, config)
        attempts.append({"url": fetched.url, "status": fetched.status, "error": fetched.error, "kind": kind})
        if fetched.status in {403, 429}:
            blocked += 1
        if not fetched.text:
            return None
        try:
            result = parse_article(fetched.text, fetched.url, now, config)
        except ValueError as exc:
            attempts[-1]["parse_error"] = str(exc)
            return None
        attempts[-1].update(
            {
                "article_date": result.article_date.isoformat(),
                "body_method": result.body_method,
                "candidate_value": result.candidate.value,
                "candidate_score": result.candidate.score,
                "candidate_pattern": result.candidate.pattern,
            }
        )
        return result

    for url in direct_article_urls(now, config):
        result = await inspect(url, "direct")
        if result:
            return result, attempts
        if blocked >= 2:
            LOGGER.warning("source is blocking direct requests; stopping date scan")
            break
        await asyncio.sleep(float(config["request_delay_seconds"]))

    fallback_urls = await fallback_discovery_urls(session, config, attempts)
    cutoff = now.date() - timedelta(days=int(config["lookback_days"]))
    candidates = []
    for url in fallback_urls:
        parsed_date = article_date_from_url(url)
        if parsed_date and cutoff <= parsed_date <= now.date():
            candidates.append((parsed_date, url))
    for _, url in sorted(set(candidates), reverse=True):
        result = await inspect(url, "discovered")
        if result:
            return result, attempts
        await asyncio.sleep(float(config["request_delay_seconds"]))

    statuses = {row.get("status") for row in attempts}
    source_unavailable = bool(statuses & {403, 429}) or any(
        row.get("status") is None or (isinstance(row.get("status"), int) and row["status"] >= 500)
        for row in attempts
    )
    status = "source_unavailable" if source_unavailable else "no_fresh_publication"
    raise DiscoveryError(status, "no valid Alfa Index article found", attempts)


def interpretation(value: int) -> dict[str, str]:
    if value <= 20:
        return {"zone": "extreme_negative", "label": "Крайне негативное"}
    if value <= 40:
        return {"zone": "negative", "label": "Негативное"}
    if value <= 60:
        return {"zone": "neutral", "label": "Нейтральное"}
    if value <= 80:
        return {"zone": "positive", "label": "Позитивное"}
    return {"zone": "extreme_positive", "label": "Крайне позитивное"}


def history_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (str(row.get("article_date") or ""), int(row.get("value")), str(row.get("value_period") or ""))


def previous_distinct_value(
    history: list[dict[str, Any]],
    current_value: int,
    current_key: tuple[str, int, str],
    current_state: dict[str, Any] | None = None,
) -> int | None:
    rows = sorted(history, key=lambda row: (str(row.get("article_date") or ""), str(row.get("collected_at") or "")))
    for row in reversed(rows):
        try:
            if history_key(row) == current_key:
                continue
            value = int(row.get("value"))
        except (TypeError, ValueError):
            continue
        if value != current_value:
            return value
    fallback = (current_state or {}).get("previous_distinct_value")
    return int(fallback) if isinstance(fallback, int) and fallback != current_value else None


def update_history(
    history: list[dict[str, Any]],
    article: ArticleResult,
    collected_at: str,
    history_days: int,
) -> tuple[list[dict[str, Any]], bool]:
    entry = {
        "article_date": article.article_date.isoformat(),
        "value": article.candidate.value,
        "value_period": article.candidate.value_period,
        "source_url": article.url,
        "article_published_at": article.published_at,
        "article_updated_at": article.updated_at,
        "collected_at": collected_at,
    }
    key = history_key(entry)
    existing: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in history:
        try:
            existing[history_key(row)] = copy.deepcopy(row)
        except (TypeError, ValueError):
            continue
    changed = key not in existing
    if key in existing:
        stable = existing[key]
        for field in ("source_url", "article_published_at", "article_updated_at"):
            if stable.get(field) != entry.get(field):
                stable[field] = entry.get(field)
                changed = True
        entry = stable
    existing[key] = entry
    cutoff = article.article_date - timedelta(days=history_days)
    rows = [row for row in existing.values() if (date.fromisoformat(str(row["article_date"])) >= cutoff)]
    rows.sort(key=lambda row: (row["article_date"], row["value_period"], row["value"]))
    changed = changed or rows != history
    return rows, changed


def business_days_elapsed(article_date: date, today: date) -> int:
    if article_date >= today:
        return 0
    cursor = article_date + timedelta(days=1)
    count = 0
    while cursor <= today:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def previous_weekday(value: date) -> date:
    cursor = value
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def is_stale(article_date: date, now: datetime, config: dict[str, Any]) -> bool:
    if article_date > now.date():
        return True
    if now.weekday() >= 5:
        return article_date < previous_weekday(now.date())
    elapsed = business_days_elapsed(article_date, now.date())
    threshold = int(config["stale_after_business_days"])
    if now.time() < time(int(config["stale_check_hour"]), 0):
        threshold += 1
    return elapsed > threshold


def build_current_payload(
    article: ArticleResult,
    history: list[dict[str, Any]],
    current_state: dict[str, Any] | None,
    now: datetime,
    config: dict[str, Any],
) -> dict[str, Any]:
    value = article.candidate.value
    key = (article.article_date.isoformat(), value, article.candidate.value_period)
    previous = previous_distinct_value(history, value, key, current_state)
    change = value - previous if previous is not None else None
    direction = "unknown" if change is None else ("up" if change > 0 else "down" if change < 0 else "flat")
    timestamp = now.isoformat()
    return {
        "indicator": "alfa_index",
        "name": "Настроение российского рынка",
        "value": value,
        "previous_distinct_value": previous,
        "change": change,
        "direction": direction,
        "value_period": article.candidate.value_period,
        "value_time": None,
        "official_scale": {"min": 0, "max": 100},
        "site_interpretation": interpretation(value),
        "source": {
            "name": "Альфа-Инвестиции",
            "provider": "Альфа-Банк",
            "url": article.url,
            "article_title": article.title,
            "article_date": article.article_date.isoformat(),
            "article_published_at": article.published_at,
            "article_updated_at": article.updated_at,
        },
        "extraction": {
            "body_method": article.body_method,
            "pattern": article.candidate.pattern,
            "confidence_score": article.candidate.score,
        },
        "last_valid_at": timestamp,
        "fetched_at": timestamp,
        "stale": is_stale(article.article_date, now, config),
        "status": "ok",
        "parser_version": str(config["parser_version"]),
    }


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(fallback)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(fallback)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def semantic_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    stable = copy.deepcopy(payload)
    stable.pop("fetched_at", None)
    stable.pop("last_valid_at", None)
    return stable


def should_write_current(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    return semantic_payload(old) != semantic_payload(new)


def sync_json(path: Path, payload: Any) -> bool:
    old = load_json(path, None)
    if old == payload:
        return False
    write_json_atomic(path, payload)
    return True


def apply_failure_state(
    current: dict[str, Any] | None,
    status: str,
    now: datetime,
    parser_version: str,
) -> dict[str, Any] | None:
    if not isinstance(current, dict) or not isinstance(current.get("value"), int):
        return None
    failed = copy.deepcopy(current)
    failed["stale"] = True
    failed["status"] = status
    failed["fetched_at"] = now.isoformat()
    failed["parser_version"] = parser_version
    return failed


def write_diagnostic(path: Path, status: str, message: str, attempts: list[dict[str, Any]], config: dict[str, Any]) -> None:
    payload = {
        "status": status,
        "message": message[:500],
        "parser_version": config["parser_version"],
        "attempts": attempts[-20:],
    }
    write_json_atomic(path, payload)


async def collect(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not config.get("enabled", True):
        LOGGER.info("collector disabled by config")
        return 0
    now = parse_now(args.now, str(config["timezone"]))
    current = load_json(args.current, None)
    history = load_json(args.history, [])
    if not isinstance(history, list):
        history = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
            "DividendFactorStrategies/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    connector = aiohttp.TCPConnector(limit_per_host=1)
    try:
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            article, attempts = await discover_latest_article(session, now, config)
    except DiscoveryError as exc:
        LOGGER.warning("%s: %s", exc.status, exc)
        write_diagnostic(args.diagnostic, exc.status, str(exc), exc.attempts, config)
        fallback = apply_failure_state(current, exc.status, now, str(config["parser_version"]))
        if fallback is None:
            LOGGER.error("no last-good Alfa Index value is available")
            return 2
        persisted = current
        if should_write_current(current, fallback):
            write_json_atomic(args.current, fallback)
            persisted = fallback
        sync_json(args.site_current, persisted)
        sync_json(args.site_history, history)
        return 0

    LOGGER.info(
        "selected %s date=%s method=%s candidate=%s score=%s pattern=%s",
        article.url,
        article.article_date,
        article.body_method,
        article.candidate.value,
        article.candidate.score,
        article.candidate.pattern,
    )
    updated_history, history_changed = update_history(
        history,
        article,
        now.isoformat(),
        int(config["history_days"]),
    )
    payload = build_current_payload(article, updated_history, current, now, config)
    current_changed = should_write_current(current, payload)
    if current_changed:
        write_json_atomic(args.current, payload)
    if history_changed:
        write_json_atomic(args.history, updated_history)
    sync_json(args.site_current, payload if current_changed or current is None else current)
    sync_json(args.site_history, updated_history)
    if args.diagnostic.exists():
        args.diagnostic.unlink()
    LOGGER.info("current_changed=%s history_changed=%s", current_changed, history_changed)
    return 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(collect(args))


if __name__ == "__main__":
    raise SystemExit(main())
