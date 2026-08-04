"""Incrementally build private current-market total-return inputs.

The free FinMind endpoint is queried only for companies currently present in
TaiwanStockInfo (TWSE, TPEx and emerging).  Every company needs financials,
prices and corporate actions before it can enter a total-return backtest. At
35 companies x 6 datasets per hour stays below the observed free-data limit,
leaving a generous retry buffer while the later backtest stage is paused.
under the documented 300-request hourly allowance.
Raw rows never leave the private Actions cache.
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


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
DATASETS = (
    "TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockMonthRevenue",
    "TaiwanStockPrice", "TaiwanStockDividend", "TaiwanStockDividendResult",
)
REQUIRED = ("TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockPrice")


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(dataset: str, code: str = "", start: str = "") -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("missing FINMIND_TOKEN")
    params = {"dataset": dataset, "token": token}
    if code:
        params["data_id"] = code
    if start:
        params["start_date"] = start
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return payload.get("data", [])


def current_universe() -> dict[str, dict[str, str]]:
    universe: dict[str, dict[str, str]] = {}
    for row in fetch("TaiwanStockInfo"):
        code = str(row.get("stock_id", ""))
        market = str(row.get("type") or "")
        industry = str(row.get("industry_category") or "").strip()
        # ETFs and ETNs can share the market type with equities but do not have
        # the same income-statement concepts.  They belong in a separate ETF
        # model, not a failed-equity-data queue.
        if not (code.isdigit() and len(code) == 4 and market in {"twse", "tpex", "emerging"} and industry and industry.upper() not in {"ETF", "ETN"}):
            continue
        universe[code] = {"name": str(row.get("stock_name") or ""), "market": market, "industry": industry}
    return universe


def fetch_one(code: str, start: str) -> tuple[str, dict[str, list[dict[str, Any]]] | None, str | None]:
    try:
        data = {dataset: fetch(dataset, code, start) for dataset in DATASETS}
        if any(not data[name] for name in REQUIRED):
            return code, None, "missing required financial statements or price history"
        return code, data, None
    except Exception as error:
        return code, None, f"{type(error).__name__}: {error}"


def cache_has_total_return_inputs(path: Path) -> bool:
    try:
        payload = load(path, {})
    except (OSError, json.JSONDecodeError):
        return False
    return all(isinstance(payload.get(name), list) and payload[name] for name in REQUIRED) and all(name in payload for name in DATASETS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache current full-market free total-return inputs incrementally")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "full-market-fundamentals-status.json")
    parser.add_argument("--batch-size", type=int, default=35)
    parser.add_argument("--years", type=int, default=6)
    args = parser.parse_args()
    universe = current_universe()
    cache = args.cache_dir / "full-market-fundamentals-v1"
    save(cache / "universe.json", {"updatedAt": datetime.now(timezone.utc).isoformat(), "stocks": universe})
    progress_path = cache / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    # Older cache rows contained only financial statements.  Requeue them once
    # so no company is incorrectly treated as backtest-ready without price and
    # corporate-action inputs.
    reviewed = {
        code for code in set(progress.get("reviewed", [])) & set(universe)
        if cache_has_total_return_inputs(cache / "stocks" / f"{code}.json")
    }
    unavailable = {code: reason for code, reason in progress.get("unavailable", {}).items() if code in universe}
    selected = [code for code in sorted(universe) if code not in reviewed and code not in unavailable][: max(1, min(35, args.batch_size))]
    start = f"{date.today().year - max(1, args.years)}-01-01"
    failures: dict[str, str] = {}
    cached: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_one, code, start) for code in selected]
        for future in as_completed(futures):
            code, data, error = future.result()
            if error:
                if error.startswith("missing required"):
                    unavailable[code] = error
                else:
                    failures[code] = error
                continue
            assert data is not None
            save(cache / "stocks" / f"{code}.json", data)
            reviewed.add(code)
            cached[code] = {name: len(rows) for name, rows in data.items()}
    save(progress_path, {"reviewed": sorted(reviewed), "unavailable": unavailable, "updatedAt": datetime.now(timezone.utc).isoformat()})
    status = {
        "schemaVersion": 1, "provider": "FinMind authenticated free individual endpoints", "cacheVisibility": "private GitHub Actions cache; raw rows are not committed", "updatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "current TWSE, TPEx and emerging equities; six-year financials, prices, dividends and ex-right results", "requestBudget": {"perCompany": len(DATASETS), "batchSize": len(selected), "maximumRequestsPerRun": len(selected) * len(DATASETS), "designedHourlyLimit": 300},
        "coverage": {"universe": len(universe), "cached": len(reviewed), "unavailable": len(unavailable), "remaining": max(0, len(universe) - len(reviewed) - len(unavailable)), "complete": len(reviewed) + len(unavailable) >= len(universe)},
        "batch": {"requested": selected, "cached": cached, "failures": failures},
        "quotaLimited": any("402" in reason or "Payment Required" in reason for reason in failures.values()),
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures and not status["quotaLimited"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
