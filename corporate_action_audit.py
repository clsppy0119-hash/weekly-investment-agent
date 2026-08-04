"""Validate stock-dividend adjustment factors against ex-right result prices."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    cache = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")) / "finmind-backtest-v2" / "stocks"
    errors: list[float] = []
    events = 0
    matched = 0
    for path in cache.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        policies = {str(row.get("StockExDividendTradingDate") or "")[:10]: row for row in data.get("TaiwanStockDividend", [])}
        for action in data.get("TaiwanStockDividendResult", []):
            day = str(action.get("date") or "")[:10]
            row = policies.get(day)
            if not row:
                continue
            stock = number(row.get("StockEarningsDistribution")) + number(row.get("StockStatutorySurplus"))
            if stock <= 0:
                continue
            events += 1
            before, after = number(action.get("before_price")), number(action.get("after_price"))
            cash = number(row.get("CashEarningsDistribution")) + number(row.get("CashStatutorySurplus"))
            if before <= 0 or after <= 0:
                continue
            # Taiwan stock dividends are stated in nominal dollars per share;
            # each NT$10 corresponds to one additional share.
            expected_after = max(0.0, before - cash) / (1 + stock / 10)
            errors.append(abs(after / expected_after - 1) if expected_after else 1.0)
            matched += 1
    mean_error = sum(errors) / len(errors) if errors else None
    status = {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "stockDividendEvents": events,
        "matchedEvents": matched,
        "meanAbsoluteReferencePriceError": mean_error,
        "validated": bool(mean_error is not None and matched >= 20 and mean_error <= 0.03),
        "method": "compare policy cash/stock dividends with FinMind ex-right before/after prices",
    }
    output = ROOT / "data" / "corporate-action-audit-status.json"
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
