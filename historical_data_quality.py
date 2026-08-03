"""Report whether privately cached history is sufficient for five-year research.

Raw FinMind rows remain inside the private Actions cache.  This script writes
only period counts and readiness labels, which are safe to expose as an
artifact and later use as a hard gate before any strategy backtest.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = {
    "statementPeriods": 20,  # five years of quarterly statements
    "balancePeriods": 20,
    "revenueMonths": 60,
}
MAX_AGE_DAYS = {
    "latestStatement": 190,
    "latestBalance": 190,
    "latestRevenue": 70,
}


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dates(rows: Any) -> set[str]:
    return {str(row.get("date", "")) for row in rows if isinstance(row, dict) and row.get("date")}


def inspect_stock(path: Path) -> dict[str, Any]:
    payload = load(path, {})
    statement = dates(payload.get("TaiwanStockFinancialStatements", []))
    balance = dates(payload.get("TaiwanStockBalanceSheet", []))
    revenue = dates(payload.get("TaiwanStockMonthRevenue", []))
    counts = {
        "statementPeriods": len(statement),
        "balancePeriods": len(balance),
        "revenueMonths": len(revenue),
    }
    latest = {
        "latestStatement": max(statement, default=None),
        "latestBalance": max(balance, default=None),
        "latestRevenue": max(revenue, default=None),
    }
    missing = [key for key, minimum in REQUIREMENTS.items() if counts[key] < minimum]
    today = date.today()
    for key, maximum_age in MAX_AGE_DAYS.items():
        value = latest[key]
        try:
            stale = value is None or (today - date.fromisoformat(value)).days > maximum_age
        except ValueError:
            stale = True
        if stale:
            missing.append(f"fresh:{key}")
    return {
        "statementPeriods": counts["statementPeriods"],
        "balancePeriods": counts["balancePeriods"],
        "revenueMonths": counts["revenueMonths"],
        **latest,
        "fiveYearReady": not missing,
        "missingRequirements": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="檢查五年研究所需的歷史資料完整度")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "historical-data-quality-status.json")
    args = parser.parse_args()

    stock_dir = args.cache_dir / "finmind-fundamentals-v1" / "stocks"
    stocks = {path.stem: inspect_stock(path) for path in sorted(stock_dir.glob("*.json"))}
    ready = sum(1 for details in stocks.values() if details["fiveYearReady"])
    status = {
        "schemaVersion": 1,
        "provider": "FinMind authorised private cache",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "requirements": REQUIREMENTS,
        "maxAgeDays": MAX_AGE_DAYS,
        "coverage": {
            "cachedStocks": len(stocks),
            "fiveYearReady": ready,
            "insufficientHistory": len(stocks) - ready,
        },
        "stocks": stocks,
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
