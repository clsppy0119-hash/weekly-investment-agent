"""Cache the current FinMind stock universe for resumable research downloads."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


API = "https://api.finmindtrade.com/api/v4/data"


def main() -> None:
    query = urllib.parse.urlencode({"dataset": "TaiwanStockInfo"})
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    rows = payload.get("data", [])
    latest: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("stock_id", ""))
        # 00xx is the Taiwan ETF/index-fund family; 0050 is added separately
        # as the benchmark and must not enter the stock selection universe.
        if len(code) == 4 and code.isdigit() and not code.startswith("00"):
            latest[code] = {"stock_id": code, "stock_name": row.get("stock_name"), "type": row.get("type"), "date": row.get("date")}
    output = Path(".private-data-cache/historical-universe-v1/all-market.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(sorted(latest.values(), key=lambda row: row["stock_id"]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"codes": len(latest), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
