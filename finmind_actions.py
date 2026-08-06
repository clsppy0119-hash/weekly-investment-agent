"""Build a verified, incrementally cached corporate-action ledger.

Cached rows reduce provider calls, but a stale or failed refresh never counts as
verified for the candidate quality gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
CACHE_SCHEMA = 1


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def active_codes(tracker: Path, manifest: Path | None = None) -> list[str]:
    if manifest and manifest.exists():
        payload = _load_json(manifest)
        codes = {
            str(item.get("code", ""))
            for item in payload.get("previewCandidates", [])
            if isinstance(item, dict) and str(item.get("code", "")).isdigit()
        }
        if codes:
            return sorted(codes)
    if not tracker.exists():
        return []
    rows = _load_json(tracker).get("recommendations", [])
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
    except Exception as error:  # provider/network boundary
        return code, [], type(error).__name__


def _merge_events(old: list[dict], new: list[dict], window_start: date) -> list[dict]:
    indexed: dict[tuple[str, str], dict] = {}
    for row in old + new:
        if str(row.get("date", "")) >= window_start.isoformat():
            indexed[(str(row.get("code", "")), str(row.get("date", "")))] = row
    return sorted(indexed.values(), key=lambda row: (str(row.get("date", "")), str(row.get("code", ""))))


def build_payload(codes: list[str], cache_path: Path, days: int, ttl_hours: float, overlap_days: int, workers: int, today: date | None = None, now: datetime | None = None) -> dict:
    today = today or date.today()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = today - timedelta(days=days)
    cache = _load_json(cache_path)
    entries = cache.get("entries", {}) if cache.get("schemaVersion") == CACHE_SCHEMA else {}
    entries = entries if isinstance(entries, dict) else {}
    fetch_ranges: dict[str, tuple[str, str]] = {}
    hits: list[str] = []
    for code in codes:
        entry = entries.get(code, {}) if isinstance(entries.get(code), dict) else {}
        verified_at = None
        try:
            verified_at = datetime.fromisoformat(str(entry.get("verifiedAt", "")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
        if verified_at and now - verified_at <= timedelta(hours=max(0.0, ttl_hours)) and entry.get("queriedThrough") == today.isoformat():
            hits.append(code)
            continue
        through = entry.get("queriedThrough")
        try:
            incremental_start = max(window_start, date.fromisoformat(str(through)) - timedelta(days=max(0, overlap_days)))
        except ValueError:
            incremental_start = window_start
        fetch_ranges[code] = (incremental_start.isoformat(), today.isoformat())

    failures: dict[str, str] = {}
    refreshed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        futures = [pool.submit(fetch, code, start, end) for code, (start, end) in fetch_ranges.items()]
        for future in as_completed(futures):
            code, rows, error = future.result()
            if error:
                failures[code] = error
                continue
            previous = entries.get(code, {}) if isinstance(entries.get(code), dict) else {}
            entries[code] = {
                "queriedThrough": today.isoformat(),
                "verifiedAt": now.isoformat(),
                "lastEventDate": max((str(row.get("date", "")) for row in _merge_events(previous.get("events", []), rows, window_start)), default=None),
                "events": _merge_events(previous.get("events", []), rows, window_start),
            }
            refreshed.append(code)

    verified_codes = [code for code in codes if code in hits or code in refreshed]
    events = [row for code in verified_codes for row in entries.get(code, {}).get("events", [])]
    last_event_dates = {code: entries.get(code, {}).get("lastEventDate") for code in codes}
    candidate_key = hashlib.sha256(json.dumps({"codes": codes, "lastEventDates": last_event_dates}, sort_keys=True).encode()).hexdigest()
    cache_payload = {"schemaVersion": CACHE_SCHEMA, "updatedAt": now.isoformat(), "entries": entries}
    _atomic_json(cache_path, cache_payload)
    return {
        "scope": "active candidate pool only; not full-market total-return coverage",
        "period": {"start": window_start.isoformat(), "end": today.isoformat()},
        "queried_codes": codes,
        "successful_codes": len(verified_codes),
        "events": sorted(events, key=lambda row: (str(row.get("date", "")), str(row.get("code", "")))),
        "failures": failures,
        "cache": {
            "schemaVersion": CACHE_SCHEMA,
            "candidateKey": candidate_key,
            "hits": len(hits),
            "refreshed": len(refreshed),
            "failed": len(failures),
            "ttlHours": ttl_hours,
            "overlapDays": overlap_days,
            "lastEventDates": last_event_dates,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "backtest_data" / "candidate_actions.json")
    parser.add_argument("--tracker", type=Path, default=ROOT / "strategy_data" / "recommendations.json")
    parser.add_argument("--candidate-manifest", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=ROOT / ".private-data-cache" / "market-evidence-v1" / "corporate-actions.json")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--ttl-hours", type=float, default=12.0)
    parser.add_argument("--overlap-days", type=int, default=14)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    codes = active_codes(args.tracker, args.candidate_manifest)
    payload = build_payload(codes, args.cache, args.days, args.ttl_hours, args.overlap_days, args.workers)
    _atomic_json(args.output, payload)
    print(json.dumps({"codes": len(codes), "events": len(payload["events"]), "failures": len(payload["failures"]), "cache": payload["cache"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
