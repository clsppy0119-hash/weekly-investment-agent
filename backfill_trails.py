"""Rebuild the price trail of recommendations that were already recorded.

``quotes.json`` keeps only a short rolling window, so the days between an older
recommendation and today have fallen out of the live feed.  Official TWSE daily
closes can put them back.

This is not a way to invent history: it only ever fills prices *after* an entry
date that a real run already committed to, and it never creates, re-ranks, or
re-scores a recommendation.  Settling logic is imported from ``strategy_tracker``
so the backfilled numbers cannot drift from the ones the daily run produces.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from strategy_tracker import (
    BENCHMARK_CODE, HORIZONS, _extend_trail, _settle, load_state, save_state,
)

DEFAULT_DAILY = Path("backtest_data/twse_daily.jsonl")


def load_daily_closes(path: Path) -> dict[str, list[dict]]:
    """Official daily closes as ``{code: [{date, close}, ...]}`` sorted by date."""
    by_code: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        day = record["date"]
        for code, close, _volume in record["rows"]:
            by_code.setdefault(code, []).append({"date": day, "close": close})
    for rows in by_code.values():
        rows.sort(key=lambda row: row["date"])
    return by_code


def entry_reference(rows: list[dict], entry_date: str) -> float | None:
    """Close on the entry date, or the last one before it."""
    usable = [row for row in rows if row["date"] <= entry_date]
    return usable[-1]["close"] if usable else None


def backfill(state: dict, by_code: dict[str, list[dict]]) -> list[dict]:
    benchmark_rows = by_code.get(BENCHMARK_CODE, [])
    changes = []
    for item in state.get("recommendations", []):
        code, entry_date = item.get("code"), item.get("date", "")
        price = item.get("entryPrice")
        if not code or not entry_date or not isinstance(price, (int, float)) or price <= 0:
            continue
        before = len(item.get("priceTrail") or {})
        trail = _extend_trail(item.setdefault("priceTrail", {}), by_code.get(code, []), entry_date)
        benchmark_trail = _extend_trail(item.setdefault("benchmarkTrail", {}), benchmark_rows, entry_date)
        if item.get("benchmarkEntryPrice") is None:
            item["benchmarkEntryPrice"] = entry_reference(benchmark_rows, entry_date)
        # Reuse whatever the daily run has already recorded, so a backfilled
        # settlement cannot disagree with one the report would have produced.
        item["outcomes"] = _settle(item.get("outcomes") or {}, trail, price,
                                   benchmark_trail, item.get("benchmarkEntryPrice"),
                                   item.get("poolTrail") or {}, item.get("exRightsFactors") or {})
        settled = [str(h) for h in HORIZONS if item["outcomes"].get(str(h), {}).get("status") == "complete"]
        changes.append({"id": item["id"], "added": len(trail) - before, "trail": len(trail), "settled": settled})
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="以官方日行情回填既有推薦的價格軌跡")
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--path", type=Path, default=None, help="recommendations.json；預設用 strategy_tracker 的路徑")
    parser.add_argument("--dry-run", action="store_true", help="只顯示會變動什麼，不寫檔")
    args = parser.parse_args()

    if not args.daily.exists():
        raise SystemExit(f"找不到官方日行情快取：{args.daily}；請先執行 backtest.py collect")
    state = load_state(args.path) if args.path else load_state()
    by_code = load_daily_closes(args.daily)
    changes = backfill(state, by_code)

    for change in changes:
        settled = ", ".join(f"{key}日" for key in change["settled"]) or "無"
        print(f"{change['id']}  +{change['added']} 筆 → 軌跡 {change['trail']} 筆，已結算：{settled}")
    total = sum(change["added"] for change in changes)
    print(f"\n{len(changes)} 筆推薦，共補入 {total} 個交易日。")
    if args.dry_run:
        print("（dry-run，未寫入）")
        return
    save_state(state, args.path) if args.path else save_state(state)
    print("已寫入。")


if __name__ == "__main__":
    main()
