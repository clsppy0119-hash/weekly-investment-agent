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
    cache = args.cache_dir / "full-market-fundamentals-v1"
    universe = load(cache / "universe.json", {}).get("stocks", {})
    progress = load(cache / "progress.json", {"reviewed": [], "unavailable": {}})
    reviewed = {str(code) for code in progress.get("reviewed", [])}
    unavailable = {str(code) for code in progress.get("unavailable", {})}
    candidates: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for market in ("twse", "tpex", "emerging"):
        codes = {code for code, item in universe.items() if item.get("market") == market}
        available = codes & reviewed
        coverage[market] = {"universe": len(codes), "completeFinancialRecords": len(available), "remaining": len(codes - reviewed - unavailable)}
        for stock_code in available:
            raw = load(cache / "stocks" / f"{stock_code}.json", {})
            statements, balance_rows, revenue_rows = raw.get("TaiwanStockFinancialStatements", []), raw.get("TaiwanStockBalanceSheet", []), raw.get("TaiwanStockMonthRevenue", [])
            def latest_value(rows: list[dict[str, Any]], kind: str) -> float | None:
                matches = [row for row in rows if row.get("type") == kind and isinstance(row.get("value"), (int, float))]
                return float(max(matches, key=lambda row: str(row.get("date", "")))["value"]) if matches else None
            revenue_yoy = None
            revenue_by_month = {str(row.get("date", ""))[:7]: number(row.get("revenue")) for row in revenue_rows}
            if revenue_by_month:
                latest_month = max(revenue_by_month)
                revenue_value = revenue_by_month.get(latest_month)
                prior = f"{int(latest_month[:4]) - 1:04d}{latest_month[4:]}"
                if revenue_by_month.get(latest_month) and revenue_by_month.get(prior):
                    revenue_yoy = (revenue_by_month[latest_month] / revenue_by_month[prior] - 1) * 100
            else:
                revenue_value = None
            operating_income = latest_value(statements, "OperatingIncome")
            assets = latest_value(balance_rows, "TotalAssets")
            liabilities = latest_value(balance_rows, "Liabilities")
            debt_ratio = liabilities / assets if assets and assets > 0 and liabilities is not None else None
            # These are transparent research-queue gates, not a buy signal.
            checks = {"positiveRevenue": revenue_value is not None and revenue_value > 0, "positiveRevenueYoy": revenue_yoy is not None and revenue_yoy > 0, "positiveOperatingIncome": operating_income is not None and operating_income > 0, "debtBelow50pct": debt_ratio is not None and debt_ratio < 0.5}
            score = sum(checks.values())
            if score >= 3:
                candidates.append({"code": stock_code, "name": universe[stock_code].get("name", ""), "industry": universe[stock_code].get("industry", ""), "market": market, "screenScore": score, "checks": checks, "revenueYoyPct": revenue_yoy, "debtRatio": debt_ratio, "classification": "priority_research_only"})
    candidates.sort(key=lambda row: (-row["screenScore"], -(row["revenueYoyPct"] or -10_000), row["code"]))
    output = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "status": "complete" if universe and not (set(universe) - reviewed - unavailable) else "coverage_incomplete",
        "scope": "Current TWSE/TPEx/emerging market only; no historical performance or investment recommendation is implied.",
        "sources": "FinMind individual public financial datasets; cached privately", "coverage": coverage,
        "screen": {"rules": ["positive reported revenue", "positive latest disclosed revenue YoY", "positive operating income", "debt ratio below 50%"], "minimumScore": 3, "resultCount": len(candidates), "candidates": candidates[:50]},
        "nextGate": "No candidates may be pushed until all current-market records are processed, then the 12-part research record and separately validated historical backtest must pass.",
    }
    save(args.output, output)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
