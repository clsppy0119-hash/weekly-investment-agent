"""Incrementally cache fundamentals for the semiconductor universe.

Raw licensed responses stay only in the private GitHub Actions cache.  The
published status contains coverage and freshness metadata, never raw rows.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
SEMICONDUCTOR = "半導體業"
DATASETS = (
    "TaiwanStockFinancialStatements",
    "TaiwanStockBalanceSheet",
    "TaiwanStockMonthRevenue",
)


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(dataset: str, code: str, start: str) -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 FINMIND_TOKEN")
    query = {"dataset": dataset, "data_id": code, "start_date": start, "token": token}
    request = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(query)}",
        headers={"User-Agent": "weekly-investment-agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return payload.get("data", [])


def semiconductors(cache_dir: Path) -> list[str]:
    # The market-cache stage has already removed duplicated historical rows
    # and recorded stocks without a recent tradable price.  Reuse that exact
    # reviewed universe so fundamentals and prices always have the same scope.
    market_progress = load(cache_dir / "finmind-market-v1" / "progress.json", {})
    reviewed = market_progress.get("reviewed", {}).get("半導體", [])
    scoped = {str(code) for code in reviewed}
    if scoped:
        return sorted(scoped)

    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 FINMIND_TOKEN")
    query = {"dataset": "TaiwanStockInfo", "token": token}
    with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(query)}", timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return sorted({
        str(row["stock_id"])
        for row in payload.get("data", [])
        if str(row.get("industry_category") or "") == SEMICONDUCTOR
        and str(row.get("type") or "") in {"twse", "tpex", "emerging"}
        and str(row.get("stock_id", "")).isdigit()
        and len(str(row["stock_id"])) == 4
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="建立半導體基本面私有快取")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "fundamentals-cache-status.json")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--years", type=int, default=6)
    args = parser.parse_args()

    cache_dir = args.cache_dir / "finmind-fundamentals-v1"
    codes = semiconductors(args.cache_dir)
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    reviewed = set(progress.get("reviewed", [])) & set(codes)
    unavailable = {code: reason for code, reason in progress.get("unavailable", {}).items() if code in codes}
    selected = [code for code in codes if code not in reviewed and code not in unavailable][: max(1, args.batch_size)]
    start = f"{date.today().year - max(1, args.years)}-01-01"

    cached: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for code in selected:
        try:
            result = {dataset: fetch(dataset, code, start) for dataset in DATASETS}
            # Financial statements and balance sheets are required for the
            # long-term research checklist; revenue may legitimately be empty
            # for a newly listed company.
            if not result["TaiwanStockFinancialStatements"] or not result["TaiwanStockBalanceSheet"]:
                raise ValueError("缺少財務報表或資產負債表")
            save(cache_dir / "stocks" / f"{code}.json", result)
            cached[code] = {
                "statementRows": len(result["TaiwanStockFinancialStatements"]),
                "balanceRows": len(result["TaiwanStockBalanceSheet"]),
                "revenueRows": len(result["TaiwanStockMonthRevenue"]),
                "latestStatement": max(str(row.get("date", "")) for row in result["TaiwanStockFinancialStatements"]),
                "latestBalance": max(str(row.get("date", "")) for row in result["TaiwanStockBalanceSheet"]),
                "latestRevenue": max((str(row.get("date", "")) for row in result["TaiwanStockMonthRevenue"]), default=None),
            }
        except ValueError as error:
            unavailable[code] = f"{type(error).__name__}: {error}"
        except Exception as error:
            failures[code] = f"{type(error).__name__}: {error}"
        else:
            reviewed.add(code)

    save(progress_path, {
        "reviewed": sorted(reviewed),
        "unavailable": unavailable,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    })
    status = {
        "schemaVersion": 1,
        "provider": "FinMind authorized API",
        "scope": "半導體業基本面：財報、資產負債表、月營收",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "batch": {"requested": len(selected), "cached": cached, "unavailable": unavailable, "failures": failures},
        "coverage": {
            "total": len(codes),
            "cached": len(reviewed),
            "unavailable": len(unavailable),
            "remaining": max(0, len(codes) - len(reviewed) - len(unavailable)),
        },
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
