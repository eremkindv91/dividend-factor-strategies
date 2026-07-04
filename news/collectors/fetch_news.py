#!/usr/bin/env python3
"""Collect Russian market news from Telegram and RSS into news_blob.txt.

The collector is intentionally conservative: missing Telegram credentials,
blocked channels, empty RSS feeds, and individual source failures do not abort
the run. The Gemini step is responsible for final deduplication and selection.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
import yaml


ROOT = Path(__file__).resolve().parents[2]
NEWS_DIR = ROOT / "news"
ARTIFACTS = NEWS_DIR / "artifacts"
MSK = timezone(timedelta(hours=3))
DEFAULT_CHANNELS = NEWS_DIR / "channels.yml"
DEFAULT_OUT = ARTIFACTS / "news_blob.txt"

STOP_PATTERNS = (
    "#инсид", "#инсайд", "переходи по ссылке", "зарабатывать от",
    "закрытый канал", "на правах рекламы", "реклама", "erid",
    "промокод", "реф. ссылка", "розыгрыш", "бесплатный вебинар",
    "успей записаться", "вп", "#реклама",
)
INVITE_RE = re.compile(r"(подписывай|переходи|канал|закрыт).{0,80}(t\.me|telegram)", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^0-9a-zа-яё]+", re.I)


@dataclass(frozen=True)
class NewsItem:
    source: str
    tier: str
    url: str
    published_at: datetime
    text: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--channels", type=Path, default=DEFAULT_CHANNELS)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    p.add_argument("--since", help="ISO datetime, default: yesterday 00:00 MSK")
    p.add_argument("--until", help="ISO datetime, default: now MSK")
    p.add_argument("--max-per-source", type=int, default=200)
    p.add_argument("--rss-only", action="store_true")
    return p.parse_args()


def as_msk(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(MSK)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def parse_dt(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    return as_msk(datetime.fromisoformat(value.replace("Z", "+00:00")))


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    return WS_RE.sub(" ", text).strip()


def clean_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def is_ad(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in STOP_PATTERNS):
        return True
    return bool(INVITE_RE.search(text))


def norm_key(text: str) -> str:
    text = NON_WORD_RE.sub(" ", text.lower())
    return WS_RE.sub(" ", text).strip()[:180]


def bucket_time(dt: datetime, minutes: int = 5) -> int:
    return int(dt.timestamp() // (minutes * 60))


def parse_feed_dt(entry: Any, fallback: datetime) -> datetime:
    for key in ("published", "updated", "created"):
        value = getattr(entry, key, None) or entry.get(key)
        if not value:
            continue
        try:
            return as_msk(parsedate_to_datetime(value))
        except (TypeError, ValueError, AttributeError):
            continue
    return fallback


def load_channels(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def collect_rss(config: dict[str, Any], since: datetime, until: datetime) -> list[NewsItem]:
    items: list[NewsItem] = []
    headers = {"User-Agent": "dividend-factor-strategies-news/1.0"}
    for src in config.get("rss") or []:
        name = str(src.get("name") or src.get("url") or "RSS")
        tier = str(src.get("tier") or "A")
        url = str(src.get("url") or "")
        if not url:
            continue
        try:
            raw = requests.get(url, headers=headers, timeout=20)
            raw.raise_for_status()
            feed = feedparser.parse(raw.content)
        except Exception as e:  # noqa: BLE001
            print(f"[news:rss] skip {name}: {e}", file=sys.stderr)
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            print(f"[news:rss] empty/bozo {name}: {getattr(feed, 'bozo_exception', '')}", file=sys.stderr)
            continue
        for entry in feed.entries:
            published_at = parse_feed_dt(entry, until)
            if published_at < since or published_at > until:
                continue
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
            text = clean_text(f"{title}. {summary}" if summary and summary != title else title)
            if not text or is_ad(text):
                continue
            items.append(NewsItem(
                source=name,
                tier=tier,
                url=clean_url(entry.get("link", "") or url),
                published_at=published_at,
                text=text,
            ))
    return items


async def collect_telegram(config: dict[str, Any], since: datetime, until: datetime, max_per_source: int) -> list[NewsItem]:
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    session = os.getenv("TG_SESSION")
    if not (api_id and api_hash and session):
        print("[news:tg] TG_* env not set; Telegram collection skipped", file=sys.stderr)
        return []
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[news:tg] telethon unavailable: {e}", file=sys.stderr)
        return []

    items: list[NewsItem] = []
    async with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
        for src in config.get("telegram") or []:
            handle = str(src.get("handle") or "")
            if not handle:
                continue
            tier = str(src.get("tier") or "A")
            try:
                count = 0
                async for msg in client.iter_messages(handle, limit=max_per_source):
                    count += 1
                    published_at = as_msk(getattr(msg, "date", None))
                    if published_at > until:
                        continue
                    if published_at < since:
                        break
                    text = clean_text(getattr(msg, "message", "") or "")
                    if not text or is_ad(text):
                        continue
                    url = f"https://t.me/{handle.lstrip('@')}/{getattr(msg, 'id', '')}"
                    items.append(NewsItem(handle, tier, url, published_at, text))
                print(f"[news:tg] {handle}: scanned {count}", file=sys.stderr)
                await asyncio.sleep(0.8)
            except Exception as e:  # noqa: BLE001
                print(f"[news:tg] skip {handle}: {e}", file=sys.stderr)
                continue
    return items


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    out: list[NewsItem] = []
    for item in sorted(items, key=lambda x: x.published_at):
        key_raw = f"{norm_key(item.text)}|{bucket_time(item.published_at)}"
        key = hashlib.sha1(key_raw.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def render_blob(items: list[NewsItem]) -> str:
    blocks = []
    for item in sorted(items, key=lambda x: x.published_at):
        stamp = item.published_at.astimezone(MSK).strftime("%Y-%m-%d %H:%M MSK")
        url = item.url or "-"
        blocks.append(f"[Источник: {item.source} ({item.tier}) | {url} | {stamp}]\n{item.text}\n---")
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def main() -> int:
    args = parse_args()
    now = datetime.now(MSK)
    since_default = datetime.combine((now - timedelta(days=1)).date(), time.min, tzinfo=MSK)
    since = parse_dt(args.since, since_default)
    until = parse_dt(args.until, now)
    config = load_channels(args.channels)
    items = collect_rss(config, since, until)
    if not args.rss_only:
        items.extend(asyncio.run(collect_telegram(config, since, until, args.max_per_source)))
    items = dedupe(items)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_blob(items), encoding="utf-8")
    print(f"[news] wrote {args.output} items={len(items)} since={since.isoformat()} until={until.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
