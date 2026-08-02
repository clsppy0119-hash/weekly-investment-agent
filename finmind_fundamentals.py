"""Incrementally enrich Taiwan semiconductor fundamentals from public FinMind data."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
SECTOR = "半導體業"
STRATEGY = "semiconductor-first-v2"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(dataset: str, code: str = "", start: str = "") -> list[dict]:
    query = {"dataset": dataset}
    if code:
        query["data_id"] = code
    if start:
        query["start_date"] = start
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(query)}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    if payload.get("status") != 200:
        raise RuntimeError(str(payload.get("msg", "API error")))
    return payload.get("data", [])


def candidate_codes(tracker: Path, extra: str) -> list[str]:
    codes = {item.strip() for item in extra.split(",") if item.strip().isdigit()}
    rows = load(tracker).get("recommendations", [])
    if rows:
        newest = max(str(row.get("date", "")) for row in rows)
        codes.update(str(row.get("code", "")) for row in rows if str(row.get("date", "")) == newest)
    return sorted(code for code in codes if code.isdigit())


def latest(rows: list[dict], kind: str) -> tuple[str | None, float | None]:
    items = [row for row in rows if row.get("type") == kind and isinstance(row.get("value"), (int, float))]
    if not items:
        return None, None
    row = max(items, key=lambda item: str(item.get("date", "")))
    return str(row["date"]), float(row["value"])


def ttm(rows: list[dict], kind: str) -> tuple[str | None, float | None, int]:
    values = {str(row.get("date")): float(row["value"]) for row in rows if row.get("type") == kind and isinstance(row.get("value"), (int, float))}
    dates = sorted(values)[-4:]
    return (dates[-1], sum(values[day] for day in dates), len(dates)) if dates else (None, None, 0)


def enrich(code: str, start: str, industry: str) -> tuple[str, dict, str | None]:
    try:
        statements = fetch("TaiwanStockFinancialStatements", code, start)
        balance = fetch("TaiwanStockBalanceSheet", code, start)
        period, eps, eps_count = ttm(statements, "EPS")
        _, income, income_count = ttm(statements, "IncomeAfterTaxes")
        balance_period, assets = latest(balance, "TotalAssets")
        _, liabilities = latest(balance, "Liabilities")
        equities = sorted((row for row in balance if row.get("type") == "Equity" and isinstance(row.get("value"), (int, float))), key=lambda row: str(row.get("date", "")))
        avg_equity = (float(equities[-1]["value"]) + float(equities[-5]["value"])) / 2 if len(equities) >= 5 else None
        roe = income / avg_equity * 100 if income_count == 4 and income is not None and avg_equity else None
        debt = liabilities / assets * 100 if liabilities is not None and assets else None
        years = len({str(row.get("date", ""))[:4] for row in statements if row.get("date")})
        return code, {
            "industry": industry,
            "eps": round(eps, 2) if eps_count == 4 and eps is not None else None,
            "roe": round(roe, 2) if roe is not None else None,
            "debtRatio": round(debt, 2) if debt is not None else None,
            "financialPeriod": max(filter(None, (period, balance_period)), default=None),
            "financialHistoryYears": years,
            "financialSource": "FinMind 公開基本面資料",
            "financialUpdatedAt": datetime.now(timezone.utc).isoformat(),
            "financialNotes": "EPS 為近四季合計；ROE 為近四季稅後淨利／平均權益；負債比為負債／資產。",
        }, None
    except Exception as error:
        return code, {}, type(error).__name__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", type=Path, default=ROOT / "quotes.json")
    parser.add_argument("--tracker", type=Path, default=ROOT / "strategy_data" / "recommendations.json")
    parser.add_argument("--codes", default="")
    parser.add_argument("--coverage", type=Path, default=ROOT / "data" / "fundamentals-coverage.json")
    parser.add_argument("--progress", type=Path, default=ROOT / "data" / "fundamentals-progress.json")
    parser.add_argument("--batch-size", type=int, default=12)
    args = parser.parse_args()

    market = load(args.quotes)
    quotes, fundamentals = market.get("quotes", {}), market.setdefault("fundamentals", {})
    stock_info = fetch("TaiwanStockInfo")
    sector_rows = [row for row in stock_info if str(row.get("industry_category", "")) == SECTOR and str(row.get("stock_id", "")) in quotes]
    industry_by_code = {str(row["stock_id"]): str(row.get("industry_category", SECTOR)) for row in sector_rows}
    universe = sorted(industry_by_code)
    for code, industry in industry_by_code.items():
        fundamentals.setdefault(code, {}).setdefault("industry", industry)

    priority = [code for code in candidate_codes(args.tracker, args.codes) if code in industry_by_code]
    progress = load(args.progress)
    cursor = int(progress.get("cursor", 0)) % max(1, len(universe))
    rotated = universe[cursor:] + universe[:cursor]
    selected = list(dict.fromkeys(priority + rotated))[: max(1, args.batch_size)]
    next_cursor = (cursor + len([code for code in selected if code in universe])) % max(1, len(universe))
    start = f"{date.today().year - 5}-01-01"
    results, failures = {}, {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(enrich, code, start, industry_by_code[code]) for code in selected]
        for future in as_completed(futures):
            code, values, error = future.result()
            if error:
                failures[code] = error
            else:
                results[code] = values
                fundamentals.setdefault(code, {}).update({key: value for key, value in values.items() if value is not None})

    enriched_codes = [code for code in universe if all(key in fundamentals.get(code, {}) for key in ("eps", "roe", "debtRatio"))]
    progress_payload = {
        "strategy": STRATEGY,
        "sector": SECTOR,
        "cursor": next_cursor,
        "lastBatch": selected,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    coverage = {
        "scope": "半導體全產業分批補齊；每次優先處理候選股，再輪替全產業，非全市場覆蓋率。",
        "strategy": STRATEGY,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "universeCodes": len(universe),
        "queriedCodes": selected,
        "priorityCodes": priority,
        "successfulCodes": len(results),
        "enrichedCodes": len(enriched_codes),
        "remainingCodes": max(0, len(universe) - len(enriched_codes)),
        "failures": failures,
        "metrics": {key: sum(1 for code in universe if fundamentals.get(code, {}).get(key) is not None) for key in ("eps", "roe", "debtRatio")},
        "fiveYearHistory": sum(1 for code in universe if fundamentals.get(code, {}).get("financialHistoryYears", 0) >= 5),
    }
    save(args.quotes, market)
    save(args.progress, progress_payload)
    save(args.coverage, coverage)
    print(json.dumps(coverage, ensure_ascii=False))


if __name__ == "__main__":
    main()
