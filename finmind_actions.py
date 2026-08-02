"""Build a verified corporate-action ledger for the active research universe.

The free FinMind endpoint is deliberately used only for tracked holdings and
strategy candidates.  This prevents a rate-limited public API from being
mistaken for a full-market bulk feed.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"


def active_codes(tracker: Path) -> list[str]:
    if not tracker.exists():
        return []
    rows = json.loads(tracker.read_text(encoding="utf-8")).get("recommendations", [])
    if not rows:
        return []
    newest = max(str(row.get("date", "")) for row in rows)
    return sorted({str(row.get("code", "")) for row in rows if str(row.get("date", "")) == newest and str(row.get("code", "")).isdigit()})


def fetch(code: str, start: str, end: str) -> tuple[str, list[dict], str | None]:
    params = {"dataset": "TaiwanStockDividendResult", "data_id": code, "start_date": start, "end_date": end}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        params["token"] = token
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        if payload.get("status") != 200:
            return code, [], str(payload.get("msg", "unknown API error"))
        events = []
        for item in payload.get("data", []):
            before, reference = item.get("before_price"), item.get("reference_price")
            if isinstance(before, (int, float)) and isinstance(reference, (int, float)) and before > 0 and reference > 0:
                events.append({
                    "date": item["date"], "code": code, "market": "candidate_pool",
                    "before_close": before, "reference_price": reference,
                    "after_price": item.get("after_price"), "kind": item.get("stock_and_cache_dividend", ""),
                    "source": "FinMind TaiwanStockDividendResult",
                })
        return code, events, None
    except Exception as error:
        return code, [], type(error).__name__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "backtest_data" / "candidate_actions.json")
    parser.add_argument("--tracker", type=Path, default=ROOT / "strategy_data" / "recommendations.json")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    codes = active_codes(args.tracker)
    end = date.today()
    start = end - timedelta(days=args.days)
    events: list[dict] = []
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        futures = [pool.submit(fetch, code, start.isoformat(), end.isoformat()) for code in codes]
        for future in as_completed(futures):
            code, rows, error = future.result()
            events.extend(rows)
            if error:
                failures[code] = error
    payload = {
        "scope": "active candidate pool only; not full-market total-return coverage",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "queried_codes": codes, "successful_codes": len(codes) - len(failures),
        "events": events, "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"codes": len(codes), "events": len(events), "failures": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
