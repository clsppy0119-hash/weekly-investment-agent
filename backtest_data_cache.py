"""Incrementally cache eligible companies' total-return backtest inputs.

Raw provider responses remain in the private Actions cache.  The committed
status contains only coverage, date ranges and access-capability metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from historical_data_quality import inspect_stock


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
CORE_DATASETS = ("TaiwanStockPrice", "TaiwanStockDividend", "TaiwanStockDividendResult")
# FinMind documents TaiwanStockPriceAdj as Backer/Sponsor-only.  It is never a
# required input: total return is calculated from the legal core ledger.
OPTIONAL_DATASETS = ("TaiwanStockPriceAdj",)
BENCHMARK_CODES = ("0050",)


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def eligible_codes(cache_dir: Path) -> list[str]:
    delisted_path = cache_dir / "official-listing-history-v1" / "finmind_delisted.json"
    delisted_rows = load(delisted_path, []) if delisted_path.exists() else []
    delisted_codes = {
        str(row.get("stock_id", "")) for row in delisted_rows
        if str(row.get("stock_id", "")).isdigit() and len(str(row.get("stock_id", ""))) == 4
    }
    all_market = cache_dir / "historical-universe-v1" / "all-market.json"
    if all_market.exists():
        rows = load(all_market, [])
        codes = {
            str(row.get("stock_id", "")) for row in rows
            if str(row.get("stock_id", "")).isdigit()
            and len(str(row.get("stock_id", ""))) == 4
            and not str(row.get("stock_id", "")).startswith("00")
        }
        if codes:
            return sorted(codes | delisted_codes)
    historical_universe = cache_dir / "historical-universe-v1" / "semiconductor.json"
    if historical_universe.exists():
        rows = load(historical_universe, [])
        codes = {str(row.get("stock_id", "")) for row in rows if str(row.get("stock_id", "")).isdigit()}
        if codes:
            return sorted(codes | delisted_codes)
    stocks = cache_dir / "finmind-fundamentals-v1" / "stocks"
    return sorted(path.stem for path in stocks.glob("*.json") if inspect_stock(path)["fiveYearReady"])


def fixed_universe_codes() -> list[str]:
    """Read an explicit user basket without ever treating it as a market universe."""
    raw = os.environ.get("FIXED_UNIVERSE_CODES", "")
    return sorted({item.strip() for item in raw.split(",") if item.strip().isdigit()})


def prioritize_pending(codes: list[str], priority_codes: set[str], delisted_codes: set[str]) -> list[str]:
    return sorted(codes, key=lambda item: (item not in priority_codes, item not in delisted_codes, item))


def existing_cached_codes(stock_dir: Path) -> set[str]:
    return {path.stem for path in stock_dir.glob("*.json") if path.stem.isdigit()}


def fetch(dataset: str, code: str, start: str) -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    query = {"dataset": dataset, "data_id": code, "start_date": start}
    if token:
        query["token"] = token
    request = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(query)}", headers={"User-Agent": "weekly-investment-agent/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return payload.get("data", [])


def fetch_one(code: str, start: str, include_optional: bool) -> tuple[str, dict[str, list[dict[str, Any]]] | None, dict[str, str], str | None]:
    try:
        payload = {dataset: fetch(dataset, code, start) for dataset in CORE_DATASETS}
    except Exception as error:
        return code, None, {}, f"{type(error).__name__}: {error}"
    optional_errors: dict[str, str] = {}
    for dataset in OPTIONAL_DATASETS:
        payload[dataset] = []
        if not include_optional:
            continue
        try:
            payload[dataset] = fetch(dataset, code, start)
        except Exception as error:
            payload[dataset] = []
            optional_errors[dataset] = f"{type(error).__name__}: {error}"
    if not payload["TaiwanStockPrice"]:
        return code, None, optional_errors, "no price data"
    return code, payload, optional_errors, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache total-return inputs incrementally")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "backtest-data-cache-status.json")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--years", type=int, default=6)
    parser.add_argument("--include-optional", action="store_true", help="Probe restricted cross-check datasets")
    args = parser.parse_args()

    # A 402 is a rolling quota response.  Avoid hammering the provider on
    # every 30-minute schedule tick; resume automatically after one hour.
    previous = load(args.status, {})
    quota_at = previous.get("quotaLimitedAt") or previous.get("updatedAt")
    if previous.get("quotaLimited") and quota_at:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(quota_at))
            if age.total_seconds() < 3600:
                skipped = dict(previous)
                skipped["updatedAt"] = datetime.now(timezone.utc).isoformat()
                skipped["skippedDueToQuota"] = True
                save(args.status, skipped)
                print(json.dumps(skipped, ensure_ascii=False))
                return
        except (TypeError, ValueError):
            pass

    eligible = eligible_codes(args.cache_dir)
    fixed_codes = fixed_universe_codes()
    codes = sorted(set(eligible) | set(fixed_codes) | set(BENCHMARK_CODES))
    cache_dir = args.cache_dir / "finmind-backtest-v2"
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    reviewed = (set(progress.get("reviewed", [])) | existing_cached_codes(cache_dir / "stocks")) & set(codes)
    unavailable = {code: reason for code, reason in progress.get("unavailable", {}).items() if code in codes}
    delisted_rows = load(args.cache_dir / "official-listing-history-v1" / "finmind_delisted.json", [])
    delisted_codes = {
        str(row.get("stock_id", "")) for row in delisted_rows
        if str(row.get("stock_id", "")).isdigit()
    }
    priority_path = args.cache_dir / "point-in-time-snapshots-v1" / "missing-price-codes.json"
    priority_codes = set(load(priority_path, [])) if priority_path.exists() else set()
    pending = [code for code in codes if code not in reviewed and code not in unavailable]
    # First fill codes proven to exist in an official rebalance-date snapshot;
    # within that group, historical exits remain first.  This spends the quota
    # directly on the evidence needed to open the point-in-time gate.
    pending = prioritize_pending(pending, priority_codes, delisted_codes)
    selected = pending[: max(1, args.batch_size)]
    start = f"{date.today().year - max(1, args.years)}-01-01"

    cached: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    optional_errors: dict[str, int] = {dataset: 0 for dataset in OPTIONAL_DATASETS}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch_one, code, start, args.include_optional) for code in selected]
        for future in as_completed(futures):
            code, payload, access_errors, error = future.result()
            if error:
                if error == "no price data":
                    unavailable[code] = error
                else:
                    failures[code] = error
                continue
            assert payload is not None
            for dataset in access_errors:
                optional_errors[dataset] = optional_errors.get(dataset, 0) + 1
            save(cache_dir / "stocks" / f"{code}.json", payload)
            prices = payload["TaiwanStockPrice"]
            cached[code] = {
                "priceRows": len(prices),
                "firstPriceDate": min(str(row.get("date", "")) for row in prices),
                "lastPriceDate": max(str(row.get("date", "")) for row in prices),
                "adjustedPriceRows": len(payload["TaiwanStockPriceAdj"]),
                "dividendRows": len(payload["TaiwanStockDividend"]),
                "corporateActionRows": len(payload["TaiwanStockDividendResult"]),
            }
            reviewed.add(code)

    save(progress_path, {"reviewed": sorted(reviewed), "unavailable": unavailable, "updatedAt": datetime.now(timezone.utc).isoformat()})
    status = {
        "schemaVersion": 2,
        "provider": "FinMind authorised API",
        "scope": "resumable historical market research plus explicit user fixed basket",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "batch": {"requested": len(selected), "cached": cached, "unavailable": unavailable, "failures": failures},
        "optionalDatasets": {
            "TaiwanStockPriceAdj": {
                "purpose": "cross-check only; total return uses raw price, dividends and ex-right results",
                "availableThisBatch": len(selected) - optional_errors.get("TaiwanStockPriceAdj", 0),
                "unavailableThisBatch": optional_errors.get("TaiwanStockPriceAdj", 0),
            }
        },
        "coverage": {
            "eligibleStocks": len(eligible), "fixedUniverseCodes": fixed_codes, "benchmarkCodes": list(BENCHMARK_CODES),
            "required": len(codes), "cached": len(reviewed), "unavailable": len(unavailable),
            "remaining": max(0, len(codes) - len(reviewed) - len(unavailable)),
            "pointInTimePriorityRemaining": len(priority_codes - reviewed - set(unavailable)),
        },
        "quotaLimited": any("402" in value or "Payment Required" in value for value in failures.values()),
    }
    if status["quotaLimited"]:
        status["quotaLimitedAt"] = datetime.now(timezone.utc).isoformat()
    else:
        status.pop("quotaLimitedAt", None)
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures and not status["quotaLimited"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
