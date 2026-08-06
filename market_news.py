"""Fetch a small, attributable market-news briefing with a private TTL cache.

The cache only avoids duplicate retrievals. Expired or malformed cache entries
are never presented as current evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
FEEDS = (
    ("總體經濟", "台股 美國 利率 通膨 央行"),
    ("地緣政治", "台股 戰爭 地緣政治 供應鏈"),
    ("科技產業", "台灣 半導體 AI 光通訊 產業"),
)
CACHE_SCHEMA = 1


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_feed(topic: str, query: str) -> tuple[list[dict], str | None]:
    url = "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
    )
    request = Request(url, headers={"User-Agent": "weekly-investment-agent/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
        rows = []
        for item in root.findall("./channel/item")[:2]:
            source = item.find("source")
            rows.append(
                {
                    "topic": topic,
                    "title": (item.findtext("title") or "").strip(),
                    "publisher": (source.text or "Google News") if source is not None else "Google News",
                    "publishedAt": (item.findtext("pubDate") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                }
            )
        return rows, None
    except Exception as error:  # provider/network boundary
        return [], type(error).__name__


def build_payload(cache_path: Path, ttl_hours: float, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cache = _load_json(cache_path)
    cached_at = _parse_time(cache.get("updatedAt"))
    cache_valid = (
        cache.get("schemaVersion") == CACHE_SCHEMA
        and cached_at is not None
        and now - cached_at <= timedelta(hours=max(0.0, ttl_hours))
        and isinstance(cache.get("items"), list)
        and isinstance(cache.get("errors"), dict)
        and not cache.get("errors")
    )
    if cache_valid:
        payload = dict(cache)
        payload["cache"] = {"status": "hit", "ageSeconds": int((now - cached_at).total_seconds()), "ttlHours": ttl_hours}
        return payload

    items: list[dict] = []
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for topic, query in FEEDS:
        rows, error = fetch_feed(topic, query)
        if error:
            errors[topic] = error
        for row in rows:
            key = row["title"].casefold()
            if row["title"] and key not in seen:
                items.append(row)
                seen.add(key)
    payload = {
        "schemaVersion": CACHE_SCHEMA,
        "scope": "headline monitor only; verify source context before making investment decisions",
        "updatedAt": now.isoformat(),
        "items": items,
        "errors": errors,
        "cache": {"status": "miss" if not cache else "expired", "ttlHours": ttl_hours},
    }
    # Only a complete refresh is reusable as verified current evidence.
    if not errors:
        _atomic_json(cache_path, {key: value for key, value in payload.items() if key != "cache"})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "market-news.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".private-data-cache" / "market-evidence-v1" / "market-news.json")
    parser.add_argument("--ttl-hours", type=float, default=6.0)
    args = parser.parse_args()
    payload = build_payload(args.cache, args.ttl_hours)
    _atomic_json(args.output, payload)
    print(json.dumps({"items": len(payload["items"]), "errors": len(payload["errors"]), "cache": payload["cache"]["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
