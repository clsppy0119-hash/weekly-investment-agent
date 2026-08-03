"""Incrementally cache official MOPS XBRL disclosures for semiconductor stocks."""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MOPS_DOWNLOAD = "https://mopsov.twse.com.tw/server-java/FileDownLoad"
# Latest broadly filed reporting period; historical backfill is intentionally
# separate so unavailable companies never block the daily queue.
PERIODS = ((115, 1),)


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_xbrl_document(content: bytes) -> bool:
    return content[:2] == b"PK" or b"ix:nonFraction" in content or b"ix:nonNumeric" in content


def download_filing(code: str, roc_year: int, quarter: int) -> bytes:
    query = urllib.parse.urlencode({"functionName": "t164sb01", "step": "9", "co_id": code, "year": str(roc_year + 1911), "season": str(quarter), "report_id": "C"})
    request = urllib.request.Request(
        f"{MOPS_DOWNLOAD}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; weekly-investment-agent/1.0)", "Referer": "https://mopsov.twse.com.tw/mops/web/t203sb01"},
    )
    # ``urlopen(timeout=...)`` covers connection setup; the process-wide socket
    # timeout also bounds a stalled body read from an otherwise connected host.
    socket.setdefaulttimeout(8)
    with urllib.request.urlopen(request, timeout=8) as response:
        content = response.read()
    if not is_xbrl_document(content):
        raise ValueError("MOPS response is not XBRL")
    return content


def fetch_one(code: str) -> tuple[str, str, bytes | None, str | None]:
    for year, quarter in PERIODS:
        try:
            return code, f"{year}Q{quarter}", download_filing(code, year, quarter), None
        except ValueError:
            continue
        except Exception as error:
            return code, "", None, f"{type(error).__name__}: {error}"
    return code, "", None, None


def full_semiconductor_universe() -> set[str]:
    snapshot = load(ROOT / "quotes.json", {})
    target = "\u534a\u5c0e\u9ad4\u696d"
    return {str(code) for code, details in snapshot.get("fundamentals", {}).items() if isinstance(details, dict) and details.get("industry") == target}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache official MOPS XBRL filings")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "mops-fundamentals-status.json")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    market_progress = load(args.cache_dir / "finmind-market-v1" / "progress.json", {})
    reviewed_by_stage = market_progress.get("reviewed", {})
    codes = {str(code) for code in (reviewed_by_stage.get("\u534a\u5c0e\u9ad4") or reviewed_by_stage.get("semiconductors", []))}
    if not codes:
        codes = full_semiconductor_universe()
    if not codes:
        tracked = load(ROOT / "data" / "fundamentals-progress.json", {})
        codes = {str(code) for code in tracked.get("lastBatch", [])}
    codes = sorted(codes)

    cache_dir = args.cache_dir / "mops-fundamentals-v2"
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    reviewed = set(progress.get("reviewed", []))
    unavailable = dict(progress.get("unavailable", {}))
    selected = [code for code in codes if code not in reviewed and code not in unavailable][: max(1, args.batch_size)]
    stock_dir = cache_dir / "stocks"
    stock_dir.mkdir(parents=True, exist_ok=True)
    cached: dict[str, dict[str, str]] = {}
    failures: dict[str, str] = {}

    # Two concurrent requests are conservative for the public MOPS service and
    # cut the worst-case batch latency roughly in half.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch_one, code) for code in selected]
        for future in as_completed(futures):
            code, period, filing, error = future.result()
            if error:
                failures[code] = error
                continue
            if filing is None:
                unavailable[code] = "MOPS 尚無可下載的合併 XBRL 財報"
                continue
            try:
                suffix = ".zip" if filing[:2] == b"PK" else ".html"
                (stock_dir / f"{code}{suffix}").write_bytes(filing)
                save(stock_dir / f"{code}.json", {"code": code, "period": period, "format": "zip" if suffix == ".zip" else "ixbrl", "source": "MOPS official XBRL download", "cachedAt": datetime.now(timezone.utc).isoformat()})
                cached[code] = {"period": period, "format": "zip" if suffix == ".zip" else "ixbrl"}
                reviewed.add(code)
            except Exception as error:
                failures[code] = f"{type(error).__name__}: {error}"

    save(progress_path, {"reviewed": sorted(reviewed), "unavailable": unavailable, "updatedAt": datetime.now(timezone.utc).isoformat()})
    status = {"schemaVersion": 3, "provider": "MOPS official XBRL disclosures", "cacheVisibility": "private GitHub Actions cache; raw filings are not committed", "updatedAt": datetime.now(timezone.utc).isoformat(), "batch": {"requested": len(selected), "cached": cached, "unavailable": unavailable, "failures": failures}, "coverage": {"total": len(codes), "cached": len(reviewed), "unavailable": len(unavailable), "remaining": max(0, len(codes) - len(reviewed) - len(unavailable))}}
    save(args.status, status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
