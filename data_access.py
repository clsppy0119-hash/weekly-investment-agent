"""Verify licensed FinMind and FinLab access without publishing raw provider data.

This is deliberately a data-access gate, not a stock-selection or backtest
script.  It stores only compact, non-sensitive metadata in the cache/status
file.  Provider responses and credentials must never be committed to Git or
served through GitHub Pages.
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
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
FINLAB_DATASETS = (
    "price:收盤價",
    "monthly_revenue:當月營收",
    "financial_statement:每股盈餘",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finmind_request(dataset: str, **params: str) -> list[dict[str, Any]]:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("未設定 FINMIND_TOKEN")
    query = {"dataset": dataset, "token": token, **{key: value for key, value in params.items() if value}}
    request = urllib.request.Request(
        f"{FINMIND_API}?{urllib.parse.urlencode(query)}",
        headers={"User-Agent": "weekly-investment-agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "FinMind API error")))
    return payload.get("data", [])


def verify_finmind() -> dict[str, Any]:
    stock_info = finmind_request("TaiwanStockInfo")
    price_rows = finmind_request("TaiwanStockPrice", data_id="2330", start_date="2025-01-01")
    if not stock_info or not price_rows:
        raise RuntimeError("FinMind 回傳空資料，無法確認資料權限")
    return {
        "provider": "FinMind",
        "credential": "FINMIND_TOKEN",
        "verifiedAt": utc_now(),
        "probes": {
            "TaiwanStockInfo": {"rows": len(stock_info)},
            "TaiwanStockPrice": {
                "rows": len(price_rows),
                "firstDate": str(price_rows[0].get("date", "")),
                "lastDate": str(price_rows[-1].get("date", "")),
            },
        },
    }


def dataframe_summary(frame: Any) -> dict[str, Any]:
    if getattr(frame, "empty", True):
        raise RuntimeError("FinLab 回傳空資料")
    index = frame.index
    return {
        "rows": int(len(frame.index)),
        "columns": int(len(frame.columns)),
        "firstDate": str(index.min()),
        "lastDate": str(index.max()),
    }


def verify_finlab() -> dict[str, Any]:
    if not os.environ.get("FINLAB_API_TOKEN", "").strip():
        raise RuntimeError("未設定 FINLAB_API_TOKEN")
    try:
        from finlab import data  # type: ignore
    except ImportError as error:
        raise RuntimeError("缺少 finlab 套件") from error

    probes = {dataset: dataframe_summary(data.get(dataset)) for dataset in FINLAB_DATASETS}
    return {
        "provider": "FinLab",
        "credential": "FINLAB_API_TOKEN",
        "verifiedAt": utc_now(),
        "probes": probes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="驗證資料供應商存取並建立私有快取中繼資料")
    parser.add_argument("--provider", choices=("all", "finmind", "finlab"), default="all")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".cache")))
    parser.add_argument("--status", type=Path, default=ROOT / "data" / "data-access-status.json")
    args = parser.parse_args()

    # This file is suitable for a private GitHub Actions cache, not for Git.
    cache_file = args.cache_dir / "data-access.json"
    results: dict[str, Any] = {"schemaVersion": 1, "updatedAt": utc_now(), "providers": {}}
    errors: dict[str, str] = {}
    checks = (("finmind", verify_finmind), ("finlab", verify_finlab))
    for name, check in checks:
        if args.provider not in ("all", name):
            continue
        try:
            results["providers"][name] = check()
        except Exception as error:  # keep a safe diagnostic, never credentials or responses
            errors[name] = f"{type(error).__name__}: {error}"

    results["errors"] = errors
    results["ready"] = not errors and bool(results["providers"])
    write_json(cache_file, results)
    # The tracked status exposes only health/coverage metadata, never provider rows.
    write_json(args.status, results)
    print(json.dumps(results, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
