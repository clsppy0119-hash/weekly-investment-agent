"""Screen the *current* Taiwan market using free official snapshots.

This is deliberately a current-date research queue, not an historical
strategy backtest.  It joins only TWSE/TPEx public quote, revenue, income and
balance-sheet snapshots that are simultaneously available, and records its
coverage so partial data can never be presented as the whole market.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CODE_KEYS = ("\u516c\u53f8\u4ee3\u865f", "\u8b49\u5238\u4ee3\u865f", "Code", "code", "SecuritiesCompanyCode")
NAME_KEYS = ("\u516c\u53f8\u540d\u7a31", "\u8b49\u5238\u540d\u7a31", "Name", "name", "CompanyName")
REVENUE_KEYS = ("\u7576\u6708\u71df\u6536", "\u71df\u696d\u6536\u5165", "revenue")
REVENUE_YOY_KEYS = ("\u53bb\u5e74\u540c\u6708\u589e\u6e1b(%)", "\u53bb\u5e74\u540c\u671f\u589e\u6e1b(%)", "\u71df\u6536\u5e74\u589e\u7387")
OPERATING_INCOME_KEYS = ("\u71df\u696d\u5229\u76ca", "\u71df\u696d\u5229\u76ca\uff08\u640d\u5931\uff09", "operatingIncome")
ASSET_KEYS = ("\u8cc7\u7522\u7e3d\u984d", "assets")
LIABILITY_KEYS = ("\u8ca0\u50b5\u7e3d\u984d", "liabilities")


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", "--", "-"):
            return value
    return None


def code(row: dict[str, Any]) -> str | None:
    value = str(pick(row, CODE_KEYS) or "").strip()
    return value if value.isdigit() and len(value) == 4 else None


def number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def by_code(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: row for row in rows if (key := code(row))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Current official-market research screen")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache")))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "current-market-screen-status.json")
    args = parser.parse_args()
    quotes = load(args.cache_dir / "official-market-v1" / "latest-quotes.json", {}).get("quotes", {})
    snapshots = load(args.cache_dir / "official-fundamentals-v1" / "latest-snapshots.json", {})
    source_names = {
        "twse": ("twse_revenue", "twse_income", "twse_balance"),
        "tpex": ("tpex_revenue", "tpex_income", "tpex_balance"),
        "emerging": ("emerging_revenue", "emerging_income", "emerging_balance"),
    }
    candidates: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for market, names in source_names.items():
        revenue, income, balance = (by_code(snapshots.get(name, [])) for name in names)
        available = set(revenue) & set(income) & set(balance) & {key for key, item in quotes.items() if item.get("market") == market}
        coverage[market] = {"quotes": sum(1 for item in quotes.values() if item.get("market") == market), "completeSnapshotRecords": len(available)}
        for stock_code in available:
            revenue_value = number(pick(revenue[stock_code], REVENUE_KEYS))
            revenue_yoy = number(pick(revenue[stock_code], REVENUE_YOY_KEYS))
            operating_income = number(pick(income[stock_code], OPERATING_INCOME_KEYS))
            assets = number(pick(balance[stock_code], ASSET_KEYS))
            liabilities = number(pick(balance[stock_code], LIABILITY_KEYS))
            debt_ratio = liabilities / assets if assets and assets > 0 and liabilities is not None else None
            # These are transparent research-queue gates, not a buy signal.
            checks = {"positiveRevenue": revenue_value is not None and revenue_value > 0, "positiveRevenueYoy": revenue_yoy is not None and revenue_yoy > 0, "positiveOperatingIncome": operating_income is not None and operating_income > 0, "debtBelow50pct": debt_ratio is not None and debt_ratio < 0.5}
            score = sum(checks.values())
            if score >= 3:
                quote = quotes[stock_code]
                candidates.append({"code": stock_code, "name": quote.get("name", ""), "market": market, "close": quote.get("close", ""), "screenScore": score, "checks": checks, "revenueYoyPct": revenue_yoy, "debtRatio": debt_ratio, "classification": "priority_research_only"})
    candidates.sort(key=lambda row: (-row["screenScore"], -(row["revenueYoyPct"] or -10_000), row["code"]))
    output = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "status": "complete" if quotes and snapshots else "waiting_for_official_cache",
        "scope": "Current TWSE/TPEx market only; no historical performance or investment recommendation is implied.",
        "sources": "TWSE and TPEx official open-data snapshots", "coverage": coverage,
        "screen": {"rules": ["positive reported revenue", "positive latest disclosed revenue YoY", "positive operating income", "debt ratio below 50%"], "minimumScore": 3, "resultCount": len(candidates), "candidates": candidates[:50]},
        "nextGate": "A candidate requires the 12-part research record and the separately validated historical backtest before any automated recommendation or Telegram push.",
    }
    save(args.output, output)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
