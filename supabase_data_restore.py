"""Restore the durable Supabase market tables back into the local cache."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from collections import defaultdict
from pathlib import Path


RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


def get_rows(
    url: str,
    key: str,
    table: str,
    offset: int,
    limit: int = 1000,
    max_attempts: int = 6,
) -> list[dict]:
    query = urllib.parse.urlencode({"select": "*", "offset": offset, "limit": limit, "order": "stock_id,trading_date" if table.endswith("daily") else "stock_id,event_date"})
    request = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/{table}?{query}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUS or attempt >= max_attempts:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
        except (URLError, TimeoutError):
            if attempt >= max_attempts:
                raise
            retry_after = None
        delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 2.0 ** (attempt - 1))
        print(json.dumps({"table": table, "offset": offset, "retry": attempt, "waitSeconds": delay}, ensure_ascii=False), flush=True)
        time.sleep(delay)
    raise RuntimeError("unreachable retry state")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ".private-data-cache")) / "finmind-backtest-v2" / "stocks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if args.dry_run:
        print(json.dumps({"restore": "requires credentials", "output": str(args.output_dir)}, ensure_ascii=False))
        return
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required (use --dry-run to inspect locally)")
    daily: list[dict] = []
    actions: list[dict] = []
    for table, target in (("investment_market_daily", daily), ("investment_corporate_actions", actions)):
        offset = 0
        while True:
            batch = get_rows(url, key, table, offset)
            target.extend(batch)
            if offset == 0 or (offset + len(batch)) % 50000 == 0:
                print(json.dumps({"table": table, "rowsRead": offset + len(batch)}, ensure_ascii=False), flush=True)
            if len(batch) < 1000:
                break
            offset += len(batch)
    grouped: dict[str, dict[str, list]] = defaultdict(lambda: {"TaiwanStockPrice": [], "TaiwanStockDividend": [], "TaiwanStockDividendResult": [], "TaiwanStockPriceAdj": []})
    for row in daily:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {"date": row.get("trading_date"), "close": row.get("close"), "Trading_Volume": row.get("volume")}
        grouped[str(row["stock_id"])] ["TaiwanStockPrice"].append(raw)
    for row in actions:
        event_type = str(row.get("event_type", ""))
        if event_type in grouped[str(row["stock_id"])]:
            grouped[str(row["stock_id"])] [event_type].append(row.get("payload") or {})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for code, payload in grouped.items():
        (args.output_dir / f"{code}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stocks": len(grouped), "dailyRows": len(daily), "actionRows": len(actions), "restored": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
