"""Incrementally retrieve disclosed MOPS financial statements by company.

MOPS returns the official statement as HTML.  It is retained privately first;
normalisation is deliberately a separate step so numbers are not guessed from
an unverified table layout.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MOPS = "https://mops.twse.com.tw/mops/web/"
STATEMENTS = {
    "income": "ajax_t163sb04",
    "balance": "ajax_t163sb05",
}


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_statement(code: str, endpoint: str, year: int, season: int) -> str:
    fields = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "queryName": "co_id",
        "inpuType": "co_id",
        "TYPEK": "all",
        "isnew": "false",
        "co_id": code,
        "year": str(year),
        "season": str(season),
    }
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        MOPS + endpoint,
        data=body,
        headers={"User-Agent": "weekly-investment-agent/1.0", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def has_statement(html: str) -> bool:
    return len(html) > 1_000 and "查無資料" not in html and "無資料" not in html


def main() -> None:
    parser = argparse.ArgumentParser(description="逐家補齊 MOPS 半導體財報")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "mops-fundamentals-status.json")
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    market_progress = load(args.cache_dir / "finmind-market-v1" / "progress.json", {})
    codes = sorted(str(code) for code in market_progress.get("reviewed", {}).get("半導體", []))
    cache_dir = args.cache_dir / "mops-fundamentals-v1"
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    reviewed = set(progress.get("reviewed", []))
    unavailable = dict(progress.get("unavailable", {}))
    selected = [code for code in codes if code not in reviewed and code not in unavailable][: max(1, args.batch_size)]
    # At the beginning of August 2026, the latest broadly available quarter is
    # 2026 Q1 (ROC 115/1); then walk back in case of different filing timing.
    periods = ((115, 1), (114, 4), (114, 3))
    cached: dict[str, dict[str, str]] = {}
    failures: dict[str, str] = {}
    for code in selected:
        try:
            result: dict[str, str] = {}
            for kind, endpoint in STATEMENTS.items():
                for year, season in periods:
                    html = request_statement(code, endpoint, year, season)
                    if has_statement(html):
                        result[kind] = html
                        result[f"{kind}Period"] = f"{year}Q{season}"
                        break
            if not all(kind in result for kind in STATEMENTS):
                raise ValueError("MOPS 未找到完整損益表與資產負債表")
            save(cache_dir / "stocks" / f"{code}.json", result)
            cached[code] = {"incomePeriod": result["incomePeriod"], "balancePeriod": result["balancePeriod"]}
            reviewed.add(code)
        except ValueError as error:
            unavailable[code] = f"{type(error).__name__}: {error}"
        except Exception as error:
            failures[code] = f"{type(error).__name__}: {error}"

    save(progress_path, {"reviewed": sorted(reviewed), "unavailable": unavailable, "updatedAt": datetime.now(timezone.utc).isoformat()})
    status = {
        "schemaVersion": 1,
        "provider": "MOPS official disclosed statements",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "batch": {"requested": len(selected), "cached": cached, "unavailable": unavailable, "failures": failures},
        "coverage": {"total": len(codes), "cached": len(reviewed), "unavailable": len(unavailable), "remaining": max(0, len(codes) - len(reviewed) - len(unavailable))},
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
