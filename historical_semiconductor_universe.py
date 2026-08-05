"""Cache historical semiconductor membership metadata privately.

The metadata includes inactive records when the provider supplies them.  Price
availability then determines the first and last tradable date at each rebalance.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise SystemExit("missing FINMIND_TOKEN")
    query = urllib.parse.urlencode({"dataset": "TaiwanStockInfo", "token": token})
    cache = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")) / "historical-universe-v1"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(f"{API}?{query}", timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 402 and (cache / "semiconductor.json").exists():
            print(json.dumps({"status": "stale_cache", "reason": "FinMind quota limit", "records": len(json.loads((cache / "semiconductor.json").read_text(encoding="utf-8")))}, ensure_ascii=False))
            return
        raise
    if payload.get("status") != 200:
        raise SystemExit(str(payload.get("msg", "FinMind API error")))
    rows = [
        row for row in payload.get("data", [])
        if str(row.get("stock_id", "")).isdigit()
        and len(str(row.get("stock_id", ""))) == 4
        and str(row.get("industry_category") or "") == "半導體業"
    ]
    (cache / "semiconductor.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    types: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("type") or "unknown")
        types[kind] = types.get(kind, 0) + 1
    status = {
        "schemaVersion": 1,
        "provider": "FinMind authorised API",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "all TaiwanStockInfo records classified as semiconductor, including non-current types",
        "records": len(rows),
        "types": types,
        "cacheVisibility": "private GitHub Actions cache; raw membership records are not committed",
    }
    output = ROOT / "data" / "historical-universe-status.json"
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
