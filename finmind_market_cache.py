"""Build an incremental, private FinMind market-data cache.

The cache contains provider responses and is intentionally stored only in the
GitHub Actions cache.  The committed status file contains summaries only, so a
public site never republishes licensed bulk data.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
STAGES = ("半導體", "電子其他", "金融", "傳產與其他", "興櫃")


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(dataset: str, **params: str) -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("未設定 FINMIND_TOKEN")
    query = {"dataset": dataset, "token": token, **{key: value for key, value in params.items() if value}}
    request = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(query)}",
        headers={"User-Agent": "weekly-investment-agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return payload.get("data", [])


def stage_for(industry: str, market: str) -> str:
    if industry == "半導體業":
        return "半導體"
    if any(word in industry for word in ("電子", "電腦", "通信", "光電", "資訊", "數位")):
        return "電子其他"
    if "金融" in industry:
        return "金融"
    if market == "emerging":
        return "興櫃"
    return "傳產與其他"


def eligible(row: dict[str, Any]) -> bool:
    code = str(row.get("stock_id", ""))
    return len(code) == 4 and code.isdigit() and str(row.get("type", "")) in {"twse", "tpex", "emerging"}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立 FinMind 私有市場資料快取")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "market-cache-status.json")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--days", type=int, default=400)
    args = parser.parse_args()

    info = [row for row in fetch("TaiwanStockInfo") if eligible(row)]
    universe = {
        str(row["stock_id"]): {
            "stage": stage_for(str(row.get("industry_category") or ""), str(row.get("type") or "")),
            "market": str(row.get("type") or ""),
        }
        for row in info
    }
    cache_dir = args.cache_dir / "finmind-market-v1"
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": {stage: [] for stage in STAGES}, "unavailable": {stage: {} for stage in STAGES}})
    reviewed = {stage: set(progress.get("reviewed", {}).get(stage, [])) for stage in STAGES}
    unavailable = {stage: dict(progress.get("unavailable", {}).get(stage, {})) for stage in STAGES}
    by_stage = {stage: sorted(code for code, meta in universe.items() if meta["stage"] == stage) for stage in STAGES}
    active_stage = next((stage for stage in STAGES if len(reviewed[stage]) + len(unavailable[stage]) < len(by_stage[stage])), STAGES[-1])
    selected = [code for code in by_stage[active_stage] if code not in reviewed[active_stage] and code not in unavailable[active_stage]][: max(1, args.batch_size)]

    start = (date.today() - timedelta(days=max(30, args.days))).isoformat()
    outcomes: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for code in selected:
        try:
            price = fetch("TaiwanStockPrice", data_id=code, start_date=start)
            dividend = fetch("TaiwanStockDividendResult", data_id=code, start_date=start)
            if not price:
                raise RuntimeError("沒有近期價格資料")
            save(cache_dir / "stocks" / f"{code}.json", {"price": price, "dividend": dividend})
            outcomes[code] = {
                "priceRows": len(price),
                "firstDate": str(price[0].get("date", "")),
                "lastDate": str(price[-1].get("date", "")),
                "dividendEvents": len(dividend),
            }
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if "沒有近期價格資料" in str(error):
                unavailable[active_stage][code] = message
            else:
                failures[code] = message
        else:
            reviewed[active_stage].add(code)

    save(progress_path, {
        "reviewed": {stage: sorted(codes) for stage, codes in reviewed.items()},
        "unavailable": unavailable,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    })
    stage_coverage = {
        stage: {
            "total": len(codes),
            "cached": len(reviewed[stage]),
            "unavailable": len(unavailable[stage]),
            "remaining": max(0, len(codes) - len(reviewed[stage]) - len(unavailable[stage])),
        }
        for stage, codes in by_stage.items()
    }
    status = {
        "schemaVersion": 1,
        "provider": "FinMind authorized API",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "universe": {"total": len(universe), "markets": sorted({meta["market"] for meta in universe.values()})},
        "activeStage": active_stage,
        "batch": {"requested": len(selected), "cached": outcomes, "unavailable": unavailable[active_stage], "failures": failures},
        "stageCoverage": stage_coverage,
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
