"""Cache official TWSE/TPEx membership snapshots at rebalance dates.

The files remain private.  Only aggregate coverage is written to ``data/``.
An observed daily market list is stronger point-in-time evidence than
projecting today's constituents into the past.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest import fetch_day


ROOT = Path(__file__).resolve().parent
TPEx_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
SNAPSHOT_VERSION = 1


def ordinary_stock(code: object) -> str:
    value = str(code or "").strip()
    return value if re.fullmatch(r"\d{4}", value) and int(value) >= 1000 else ""


def fetch_tpex(day: str) -> set[str]:
    query = urllib.parse.urlencode({"date": day.replace("-", "/"), "type": "EW", "response": "json"})
    request = urllib.request.Request(f"{TPEx_URL}?{query}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
            tables = payload.get("tables", []) if isinstance(payload, dict) else []
            rows = tables[0].get("data", []) if tables else []
            return {stock for row in rows if row and (stock := ordinary_stock(row[0]))}
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TPEx snapshot failed for {day}: {type(last_error).__name__}")


def fetch_twse(day: str) -> set[str]:
    payload = fetch_day(datetime.strptime(day, "%Y-%m-%d").date())
    if not payload:
        raise RuntimeError(f"TWSE snapshot failed for {day}")
    return {stock for row in payload.get("rows", []) if row and (stock := ordinary_stock(row[0]))}


def signal_dates(calendar: list[str], lookback: int = 60, holding: int = 20) -> list[str]:
    """Match the exact split-local signal dates used by total_return_backtest."""
    boundaries = (0, int(len(calendar) * 0.6), int(len(calendar) * 0.8), len(calendar))
    result: set[str] = set()
    for start, end in zip(boundaries, boundaries[1:]):
        dates = calendar[start:end]
        for index in range(lookback, len(dates) - holding, holding):
            result.add(dates[index])
    return sorted(result)


def benchmark_calendar(cache_dir: Path) -> list[str]:
    path = cache_dir / "finmind-backtest-v2" / "stocks" / "0050.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return sorted({
        str(row.get("date", ""))[:10]
        for row in payload.get("TaiwanStockPrice", [])
        if str(row.get("date", ""))[:10]
    })


def collect_one(day: str, output: Path) -> dict[str, Any]:
    if output.exists():
        try:
            cached = json.loads(output.read_text(encoding="utf-8-sig"))
            if cached.get("twse") is not None and cached.get("tpex") is not None:
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    twse, tpex = fetch_twse(day), fetch_tpex(day)
    payload = {"schemaVersion": SNAPSHOT_VERSION, "date": day, "twse": sorted(twse), "tpex": sorted(tpex)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def evaluate_coverage(required: list[str], snapshot_dir: Path, cached_codes: set[str]) -> dict[str, Any]:
    missing_dates: list[str] = []
    missing_codes: set[str] = set()
    observed_codes: set[str] = set()
    ready = 0
    for day in required:
        path = snapshot_dir / f"{day}.json"
        if not path.exists():
            missing_dates.append(day)
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        codes = {ordinary_stock(item) for item in [*payload.get("twse", []), *payload.get("tpex", [])]}
        codes.discard("")
        if not codes:
            missing_dates.append(day)
            continue
        ready += 1
        observed_codes.update(codes)
        missing_codes.update(codes - cached_codes)
    certified = bool(required) and ready == len(required) and not missing_codes
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "TWSE and TPEx official daily market lists",
        "requiredRebalanceDates": len(required),
        "readyRebalanceDates": ready,
        "missingRebalanceDates": len(missing_dates),
        "observedStocks": len(observed_codes),
        "cachedStocks": len(cached_codes),
        "missingCachedStocks": len(missing_codes),
        "coverageRatio": (len(observed_codes & cached_codes) / len(observed_codes)) if observed_codes else 0.0,
        "certified": certified,
        "promotionGate": "open" if certified else "closed: point-in-time snapshots or matching price histories are incomplete",
    }


def load_membership(snapshot_dir: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in snapshot_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        day = str(payload.get("date", ""))
        codes = {ordinary_stock(item) for item in [*payload.get("twse", []), *payload.get("tpex", [])]}
        codes.discard("")
        if day and codes:
            result[day] = codes
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "market-membership-snapshot-status.json")
    args = parser.parse_args()
    calendar = benchmark_calendar(args.cache_dir)
    required = signal_dates(calendar)
    snapshot_dir = args.cache_dir / "point-in-time-snapshots-v1"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    missing = [day for day in required if not (snapshot_dir / f"{day}.json").exists()]
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 2))) as pool:
        futures = {pool.submit(collect_one, day, snapshot_dir / f"{day}.json"): day for day in missing}
        for future in as_completed(futures):
            future.result()
    stocks = args.cache_dir / "finmind-backtest-v2" / "stocks"
    cached_codes = {path.stem for path in stocks.glob("*.json") if ordinary_stock(path.stem)}
    status = evaluate_coverage(required, snapshot_dir, cached_codes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
