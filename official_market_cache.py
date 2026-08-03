"""Cache current Taiwan market quotes from official exchange open-data APIs.

This fallback needs no FinMind or FinLab credential.  It deliberately stores
only the latest official quote snapshot in the private Actions cache; it is a
freshness fallback, not a replacement for a licensed long-history database.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCES = {
    "twse": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "tpex": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
    "emerging": "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics",
}
CODE_KEYS = ("Code", "code", "SecuritiesCompanyCode", "股票代號", "代號")
NAME_KEYS = ("Name", "name", "CompanyName", "SecuritiesCompanyName", "股票名稱", "名稱")
CLOSE_KEYS = ("ClosingPrice", "Close", "ClosePrice", "收盤價", "收盤")


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": "weekly-investment-agent/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise RuntimeError("官方 API 未回傳清單資料")
    return [row for row in payload if isinstance(row, dict)]


def pick(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def normalize(rows: list[dict[str, Any]], market: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        code = pick(row, CODE_KEYS)
        if not code or not code.isdigit() or len(code) != 4:
            continue
        result[code] = {
            "market": market,
            "name": pick(row, NAME_KEYS) or "",
            "close": pick(row, CLOSE_KEYS) or "",
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="建立 TWSE／TPEx 官方最新行情私有快取")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "official-market-status.json")
    args = parser.parse_args()

    quotes: dict[str, dict[str, str]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for market, url in SOURCES.items():
        try:
            rows = fetch(url)
            normalized = normalize(rows, market)
            quotes.update(normalized)
            sources[market] = {"url": url, "rows": len(rows), "normalizedQuotes": len(normalized), "ready": bool(normalized)}
        except Exception as error:
            sources[market] = {"url": url, "ready": False, "error": f"{type(error).__name__}: {error}"}

    payload = {
        "source": "TWSE／TPEx official open data",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "quotes": quotes,
    }
    save(args.cache_dir / "official-market-v1" / "latest-quotes.json", payload)
    status = {
        "schemaVersion": 1,
        "provider": "TWSE／TPEx official open data",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": payload["updatedAt"],
        "sources": sources,
        "quoteCount": len(quotes),
        "ready": any(item.get("ready") for item in sources.values()),
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if not status["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
