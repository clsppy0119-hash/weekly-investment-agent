"""Expose only safe field metadata for privately cached backtest inputs."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fields(rows: Any) -> list[str]:
    return sorted({str(key) for row in rows if isinstance(row, dict) for key in row})


def main() -> None:
    parser = argparse.ArgumentParser(description="輸出回測輸入欄位名稱，不輸出原始資料")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "backtest-schema-status.json")
    args = parser.parse_args()

    stock_dir = args.cache_dir / "finmind-backtest-v2" / "stocks"
    samples = sorted(stock_dir.glob("*.json"))[:5]
    price_fields: set[str] = set()
    adjusted_price_fields: set[str] = set()
    dividend_fields: set[str] = set()
    action_fields: set[str] = set()
    for path in samples:
        payload = load(path)
        price_fields.update(fields(payload.get("TaiwanStockPrice", [])))
        adjusted_price_fields.update(fields(payload.get("TaiwanStockPriceAdj", [])))
        dividend_fields.update(fields(payload.get("TaiwanStockDividend", [])))
        action_fields.update(fields(payload.get("TaiwanStockDividendResult", [])))
    status = {
        "schemaVersion": 1,
        "cacheVisibility": "private GitHub Actions cache; field names only",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "sampledStocks": len(samples),
        "priceFields": sorted(price_fields),
        "adjustedPriceFields": sorted(adjusted_price_fields),
        "dividendFields": sorted(dividend_fields),
        "corporateActionFields": sorted(action_fields),
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
