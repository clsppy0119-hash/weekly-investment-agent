"""Enrich a bounded Taiwan-stock pool with verified FinMind fundamentals."""

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


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fetch(dataset: str, code: str, start: str = "") -> list[dict]:
    query = {"dataset": dataset, "data_id": code}
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


def is_semiconductor(info: list[dict]) -> bool:
    text = " ".join(str(value) for row in info for value in row.values()).lower()
    return any(key in text for key in ("semiconductor", "半導體", "ic", "晶圓"))


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


def enrich(code: str, start: str) -> tuple[str, dict, str | None]:
    try:
        info = fetch("TaiwanStockInfo", code)
        if not is_semiconductor(info):
            return code, {"skipped": "non_semiconductor"}, None
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
            "industry": "半導體",
            "eps": round(eps, 2) if eps_count == 4 and eps is not None else None,
            "roe": round(roe, 2) if roe is not None else None,
            "debtRatio": round(debt, 2) if debt is not None else None,
            "financialPeriod": max(filter(None, (period, balance_period)), default=None),
            "financialHistoryYears": years,
            "financialSource": "FinMind 財報與資產負債表",
            "financialUpdatedAt": datetime.now(timezone.utc).isoformat(),
            "financialNotes": "EPS 為最近四季加總；ROE 為最近四季稅後淨利／平均權益；負債比＝負債／資產。",
        }, None
    except Exception as error:
        return code, {}, type(error).__name__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", type=Path, default=ROOT / "quotes.json")
    parser.add_argument("--tracker", type=Path, default=ROOT / "strategy_data" / "recommendations.json")
    parser.add_argument("--codes", default="")
    parser.add_argument("--coverage", type=Path, default=ROOT / "data" / "fundamentals-coverage.json")
    args = parser.parse_args()
    market, codes = load(args.quotes), candidate_codes(args.tracker, args.codes)
    results, failures = {}, {}
    start = f"{date.today().year - 5}-01-01"
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(enrich, code, start) for code in codes]
        for future in as_completed(futures):
            code, values, error = future.result()
            if error:
                failures[code] = error
            elif values.get("skipped") != "non_semiconductor":
                results[code] = values
                market.setdefault("fundamentals", {}).setdefault(code, {}).update({key: value for key, value in values.items() if value is not None})
    coverage = {
        "scope": "半導體優先候選池；不是全市場資料覆蓋率",
        "updatedAt": datetime.now(timezone.utc).isoformat(), "queriedCodes": codes,
        "semiconductorCodes": sorted(results), "successfulCodes": len(results), "failures": failures,
        "metrics": {key: sum(1 for row in results.values() if row.get(key) is not None) for key in ("eps", "roe", "debtRatio")},
        "fiveYearHistory": sum(1 for row in results.values() if row.get("financialHistoryYears", 0) >= 5),
    }
    args.quotes.write_text(json.dumps(market, ensure_ascii=False, indent=2), encoding="utf-8")
    args.coverage.parent.mkdir(parents=True, exist_ok=True)
    args.coverage.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(coverage, ensure_ascii=False))


if __name__ == "__main__":
    main()
