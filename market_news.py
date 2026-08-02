"""Fetch a small, attributable market-news briefing for the daily report.

This is a headline monitor, not a trading signal.  It intentionally keeps a
small fixed query set and records the original feed link and publisher so a
reader can verify context before acting.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
FEEDS = (
    ("利率／美元", "美國 聯準會 利率 升息 降息"),
    ("地緣政治／油價", "中東 衝突 戰爭 油價"),
    ("台灣產業／供應鏈", "台灣 半導體 出口 供應鏈"),
)


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
    except Exception as error:
        return [], type(error).__name__


def main() -> None:
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
        "scope": "headline monitor only; verify source context before making investment decisions",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "errors": errors,
    }
    output = ROOT / "market-news.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"items": len(items), "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
