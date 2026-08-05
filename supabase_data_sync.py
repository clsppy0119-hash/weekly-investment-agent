"""Persist private FinMind cache rows in Supabase.

Writes require SUPABASE_SERVICE_ROLE_KEY and are never performed with the
browser's publishable key.  Existing rows are upserted by (stock_id, date),
so reruns are idempotent and do not depend on Actions cache retention.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def post(url: str, key: str, table: str, rows: list[dict]) -> None:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{table}",
        data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"Supabase {table} returned HTTP {response.status}")


def collect(cache_dir: Path) -> tuple[list[dict], list[dict]]:
    daily: list[dict] = []
    actions: list[dict] = []
    for path in sorted((cache_dir / "finmind-backtest-v2" / "stocks").glob("*.json")):
        payload = load(path)
        code = path.stem
        for row in payload.get("TaiwanStockPrice", []):
            day = str(row.get("date", ""))[:10]
            try:
                close = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if len(day) == 10 and close > 0:
                daily.append({"stock_id": code, "trading_date": day, "close": close, "volume": row.get("Trading_Volume"), "source": "finmind", "raw": row})
        for dataset in ("TaiwanStockDividend", "TaiwanStockDividendResult"):
            for row in payload.get(dataset, []):
                day = str(row.get("CashExDividendTradingDate") or row.get("StockExDividendTradingDate") or row.get("date") or "")[:10]
                if len(day) == 10:
                    actions.append({"stock_id": code, "event_date": day, "event_type": dataset, "payload": row})
    return daily, actions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ".private-data-cache")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    daily, actions = collect(args.cache_dir)
    summary = {"dailyRows": len(daily), "actionRows": len(actions), "dryRun": args.dry_run}
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False))
        return
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required (use --dry-run to inspect locally)")
    for table, rows in (("investment_market_daily", daily), ("investment_corporate_actions", actions)):
        for start in range(0, len(rows), max(1, args.batch_size)):
            post(url, key, table, rows[start:start + args.batch_size])
    print(json.dumps(summary | {"persisted": True, "finishedAt": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
