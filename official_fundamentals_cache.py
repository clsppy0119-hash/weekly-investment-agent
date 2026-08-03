"""Cache official public monthly-revenue and financial-statement snapshots.

The source APIs are published by TWSE and TPEx/MOPS.  Raw rows are retained
only in the private Actions cache; the status file exposes coverage only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
TWSE = "https://openapi.twse.com.tw/v1/opendata/"
TPEX = "https://www.tpex.org.tw/openapi/v1/"
SOURCES = {
    "twse_revenue": TWSE + "t187ap05_L",
    "twse_income": TWSE + "t187ap06_L_ci",
    "twse_balance": TWSE + "t187ap07_L_ci",
    "tpex_revenue": TPEX + "t187ap05_O",
    "tpex_income": TPEX + "mopsfin_t187ap06_O_ci",
    "tpex_balance": TPEX + "mopsfin_t187ap07_O_ci",
    "emerging_revenue": TPEX + "t187ap05_R",
    "emerging_income": TPEX + "mopsfin_t187ap06_U_ci",
    "emerging_balance": TPEX + "mopsfin_t187ap07_U_ci",
}
CODE_KEYS = ("公司代號", "出表日期", "公司", "Code", "SecuritiesCompanyCode", "股票代號")


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url: str) -> list[dict[str, Any]]:
    error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "weekly-investment-agent/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
            if not isinstance(payload, list):
                raise RuntimeError("官方 API 未回傳清單資料")
            return [row for row in payload if isinstance(row, dict)]
        except Exception as caught:
            error = caught
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    assert error is not None
    raise error


def codes(rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        for key in CODE_KEYS:
            code = str(row.get(key, "")).strip()
            if code.isdigit() and len(code) == 4:
                found.add(code)
                break
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="建立官方月營收與財報私有快取")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "official-fundamentals-status.json")
    args = parser.parse_args()

    progress_path = args.cache_dir / "finmind-market-v1" / "progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    semiconductors = {str(code) for code in progress.get("reviewed", {}).get("半導體", [])}
    snapshots: dict[str, list[dict[str, Any]]] = {}
    source_status: dict[str, dict[str, Any]] = {}
    for name, url in SOURCES.items():
        try:
            rows = fetch(url)
            snapshots[name] = rows
            source_status[name] = {
                "url": url,
                "rows": len(rows),
                "codes": len(codes(rows)),
                "sampleFields": sorted(rows[0]) if rows else [],
                "ready": bool(rows),
            }
        except Exception as error:
            source_status[name] = {"url": url, "ready": False, "error": f"{type(error).__name__}: {error}"}

    save(args.cache_dir / "official-fundamentals-v1" / "latest-snapshots.json", snapshots)
    market_sources = {
        "twse": ("twse_revenue", "twse_income", "twse_balance"),
        "tpex": ("tpex_revenue", "tpex_income", "tpex_balance"),
        "emerging": ("emerging_revenue", "emerging_income", "emerging_balance"),
    }
    coverage: dict[str, Any] = {}
    for market, names in market_sources.items():
        available = [codes(snapshots[name]) for name in names if name in snapshots]
        intersection = set.intersection(*available) if len(available) == len(names) else set()
        coverage[market] = {
            "fullSnapshotCodes": len(intersection),
            "semiconductorCodes": len(semiconductors & intersection),
            "missingSemiconductorCount": len(semiconductors - intersection) if intersection else len(semiconductors),
            "missingSemiconductorSample": sorted(semiconductors - intersection)[:20] if intersection else sorted(semiconductors)[:20],
        }
    covered = set().union(*(codes(rows) for rows in snapshots.values())) if snapshots else set()
    status = {
        "schemaVersion": 1,
        "provider": "TWSE／TPEx／MOPS official open data",
        "cacheVisibility": "private GitHub Actions cache; raw rows are not committed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": source_status,
        "semiconductorUniverse": len(semiconductors),
        "semiconductorAnyFundamentalCoverage": len(semiconductors & covered),
        "coverageByMarket": coverage,
        "ready": bool(snapshots),
    }
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if not status["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
