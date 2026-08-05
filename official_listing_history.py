"""Cache official Taiwan market listing metadata without publishing raw rows.

The cache is deliberately separate from the FinMind price cache.  It is used
to establish whether a security belonged to a market at a given rebalance
date, which is required before a strategy can be promoted beyond research.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCES = {
    "twse_listed": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "twse_terminated": "https://www.twse.com.tw/company/suspendListingCsvAndHtml?lang=zh&startYear=&type=csv",
    "tpex_listed": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "tpex_emerging": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
}


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "weekly-investment-agent/1.0"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_finmind_delisting(token: str) -> list[dict[str, Any]]:
    """Fetch the licensed delisting dataset used for historical exit evidence."""
    if not token:
        raise RuntimeError("FINMIND_TOKEN is required for TaiwanStockDelisting")
    url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDelisting"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if not payload.get("status"):
        raise RuntimeError(str(payload.get("msg", "FinMind delisting request failed")))
    return payload.get("data", [])


def fetch_csv(url: str) -> list[dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "weekly-investment-agent/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    # TWSE's historical delisting CSV is presently Big5/CP950 while newer
    # endpoints use UTF-8.  Keep both paths so the official record remains
    # usable when the publisher changes encoding.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp950")
    return list(csv.DictReader(io.StringIO(text)))


def record_count(payload: Any) -> int:
    return len(payload) if isinstance(payload, list) else 0


def main() -> None:
    cache_root = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")) / "official-listing-history-v1"
    cache_root.mkdir(parents=True, exist_ok=True)
    source_status: dict[str, dict[str, Any]] = {}
    successful = 0

    for name, url in SOURCES.items():
        try:
            payload = fetch_csv(url) if name == "twse_terminated" else fetch_json(url)
            (cache_root / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            source_status[name] = {"officialUrl": url, "ready": True, "records": record_count(payload)}
            successful += 1
        except Exception as error:
            source_status[name] = {"officialUrl": url, "ready": False, "error": type(error).__name__}

    try:
        payload = fetch_finmind_delisting(os.environ.get("FINMIND_TOKEN", "").strip())
        (cache_root / "finmind_delisted.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        source_status["finmind_delisted"] = {
            "officialUrl": "https://finmindtrade.com/analysis/#/data/taiwan_stock_delisting",
            "ready": True,
            "records": record_count(payload),
        }
    except Exception as error:
        source_status["finmind_delisted"] = {"ready": False, "error": type(error).__name__}

    status = {
        "schemaVersion": 1,
        "provider": "TWSE and TPEx official open data",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cacheVisibility": "private GitHub Actions cache; raw official records are not committed",
        "purpose": "membership and exit-date evidence for point-in-time backtest universes",
        "sources": source_status,
        "ready": successful == len(SOURCES) and source_status.get("finmind_delisted", {}).get("ready", False),
        "promotionGate": "still closed until historical entry and exit dates are reconstructed and audited",
    }
    output = ROOT / "data" / "official-listing-history-status.json"
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
