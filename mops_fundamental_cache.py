"""Incrementally cache official MOPS XBRL disclosures for semiconductor stocks.

MOPS publishes company filings as XBRL/iXBRL downloads.  The raw document is
kept only in the GitHub Actions cache; this script writes public, non-numeric
coverage metadata to the repository.  Parsing and metric normalisation remain
a separate, auditable step.
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
MOPS_DOWNLOAD = "https://mopsov.twse.com.tw/server-java/FileDownLoad"
PERIODS = ((115, 1), (114, 4), (114, 3))


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_xbrl_document(content: bytes) -> bool:
    return content[:2] == b"PK" or b"ix:nonFraction" in content or b"ix:nonNumeric" in content


def download_filing(code: str, roc_year: int, quarter: int) -> bytes:
    """Download the consolidated MOPS XBRL/iXBRL filing for one reporting period."""
    query = urllib.parse.urlencode(
        {
            "functionName": "t164sb01",
            "step": "9",
            "co_id": code,
            "year": str(roc_year + 1911),
            "season": str(quarter),
            "report_id": "C",
        }
    )
    request = urllib.request.Request(
        f"{MOPS_DOWNLOAD}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; weekly-investment-agent/1.0)",
            "Referer": "https://mopsov.twse.com.tw/mops/web/t203sb01",
        },
    )
    # A company without an available filing can otherwise consume three
    # 60-second requests per daily batch.  Twenty seconds is ample for the
    # small official XBRL response while keeping the batch predictable.
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read()
    if not is_xbrl_document(content):
        raise ValueError("MOPS 回應不是有效的 XBRL／iXBRL 財報")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="快取公開資訊觀測站 XBRL 財報")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "mops-fundamentals-status.json")
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    market_progress = load(args.cache_dir / "finmind-market-v1" / "progress.json", {})
    # The market cache uses the Traditional-Chinese stage name.  Retain the
    # English fallback for compatibility with any older cache schema.
    reviewed_by_stage = market_progress.get("reviewed", {})
    codes = {
        str(code)
        for code in (reviewed_by_stage.get("半導體") or reviewed_by_stage.get("semiconductors", []))
    }
    # Official-data.yml must work on the default branch even when GitHub Actions
    # cannot restore a cache created on a non-default branch.  The tracked
    # progress file contains the already validated semiconductor queue and is
    # metadata only (no licensed rows), so it is a safe fallback.
    if not codes:
        tracked_progress = load(ROOT / "data" / "fundamentals-progress.json", {})
        tracked_by_stage = tracked_progress.get("reviewedCodes", {})
        codes.update(str(code) for code in tracked_by_stage.get("半導體", []))
        codes.update(str(code) for code in tracked_progress.get("lastBatch", []))
    codes = sorted(codes)
    cache_dir = args.cache_dir / "mops-fundamentals-v2"
    progress_path = cache_dir / "progress.json"
    progress = load(progress_path, {"reviewed": [], "unavailable": {}})
    reviewed = set(progress.get("reviewed", []))
    unavailable = dict(progress.get("unavailable", {}))
    selected = [code for code in codes if code not in reviewed and code not in unavailable][: max(1, args.batch_size)]

    cached: dict[str, dict[str, str]] = {}
    failures: dict[str, str] = {}
    for code in selected:
        try:
            filing = None
            period = None
            for year, quarter in PERIODS:
                try:
                    filing = download_filing(code, year, quarter)
                    period = f"{year}Q{quarter}"
                    break
                except ValueError:
                    continue
            if filing is None or period is None:
                unavailable[code] = "MOPS 尚無可下載的合併 XBRL 財報"
                continue
            stock_dir = cache_dir / "stocks"
            stock_dir.mkdir(parents=True, exist_ok=True)
            suffix = ".zip" if filing[:2] == b"PK" else ".html"
            (stock_dir / f"{code}{suffix}").write_bytes(filing)
            save(
                stock_dir / f"{code}.json",
                {
                    "code": code,
                    "period": period,
                    "format": "zip" if suffix == ".zip" else "ixbrl",
                    "source": "MOPS official XBRL download",
                    "cachedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
            cached[code] = {"period": period, "format": "zip" if suffix == ".zip" else "ixbrl"}
            reviewed.add(code)
        except Exception as error:
            failures[code] = f"{type(error).__name__}: {error}"

    save(progress_path, {"reviewed": sorted(reviewed), "unavailable": unavailable, "updatedAt": datetime.now(timezone.utc).isoformat()})
    status = {
        "schemaVersion": 2,
        "provider": "MOPS official XBRL disclosures",
        "cacheVisibility": "private GitHub Actions cache; raw filings are not committed",
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
