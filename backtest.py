"""Download official TWSE daily data and run a leakage-resistant 1-year backtest.

The first version intentionally covers listed stocks only.  It uses the public
TWSE daily closing file, does not use today's stock universe for past dates,
and reports a price-return comparison with 0050 (not a total-return index).
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "backtest_data"
DEFAULT_DATA = DATA_DIR / "twse_daily.jsonl"
TWSE_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
BUY_FEE = 0.001425
SELL_FEE = 0.001425
STOCK_SELL_TAX = 0.003
ETF_SELL_TAX = 0.001


def number(value: object) -> float | None:
    if value in (None, "", "--", "---"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def weekday_dates(days: int) -> list[date]:
    end = date.today()
    start = end - timedelta(days=days)
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def fetch_day(day: date) -> dict | None:
    query = urllib.parse.urlencode({"response": "json", "date": day.strftime("%Y%m%d"), "type": "ALL"})
    request = urllib.request.Request(f"{TWSE_URL}?{query}", headers={"User-Agent": "weekly-investment-agent/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get("stat") != "OK":
                return None
            table = next((item for item in payload.get("tables", []) if "每日收盤行情" in item.get("title", "")), None)
            if not table:
                return None
            fields = table.get("fields", [])
            rows = []
            for values in table.get("data", []):
                row = dict(zip(fields, values))
                code = str(row.get("證券代號", "")).strip()
                close = number(row.get("收盤價"))
                volume = number(row.get("成交股數"))
                if len(code) == 4 and code.isdigit() and close and close > 0 and volume and volume > 0:
                    rows.append([code, close, volume])
            return {"date": day.isoformat(), "rows": rows}
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def collect(days: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            existing[row["date"]] = row
    wanted = weekday_dates(days)
    for index, day in enumerate(wanted, 1):
        key = day.isoformat()
        if key not in existing:
            payload = fetch_day(day)
            if payload:
                existing[key] = payload
            time.sleep(0.15)
        if index % 25 == 0 or index == len(wanted):
            print(f"資料下載進度：{index}/{len(wanted)}")
    output.write_text("\n".join(json.dumps(existing[key], ensure_ascii=False) for key in sorted(existing)) + "\n", encoding="utf-8")
    print(f"已保存 {len(existing)} 個交易日到 {output}")


def load_history(path: Path) -> tuple[list[str], list[dict[str, tuple[float, float]]]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records.sort(key=lambda row: row["date"])
    return [row["date"] for row in records], [{code: (float(close), float(volume)) for code, close, volume in row["rows"]} for row in records]


def run_slice(history: list[dict[str, tuple[float, float]]], lookback: int, count: int, holding: int) -> dict:
    returns: list[float] = []
    signals = 0
    wins = 0
    index = lookback
    while index + holding + 1 < len(history):
        today, past, entry, exit_ = history[index], history[index - lookback], history[index + 1], history[index + holding + 1]
        ranked = []
        for code, (price, volume) in today.items():
            previous = past.get(code)
            entry_price = entry.get(code)
            exit_price = exit_.get(code)
            if not previous or not entry_price or not exit_price or volume < 500_000:
                continue
            momentum = price / previous[0] - 1
            if price >= 10 and momentum > -0.25:
                ranked.append((momentum, code, entry_price[0], exit_price[0]))
        picks = sorted(ranked, reverse=True)[:count]
        if picks:
            gross = mean(exit_price / entry_price - 1 for _, _, entry_price, exit_price in picks)
            net = (1 + gross) * (1 - BUY_FEE) * (1 - SELL_FEE - STOCK_SELL_TAX) - 1
            returns.append(net)
            signals += 1
            wins += net > 0
        index += holding
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    return {"return": equity - 1, "mdd": drawdown, "trades": signals, "win_rate": wins / signals if signals else 0.0, "returns": returns}


def baseline_0050(history: list[dict[str, tuple[float, float]]]) -> float | None:
    prices = [day.get("0050", (None, 0))[0] for day in history]
    prices = [price for price in prices if price]
    if len(prices) < 2:
        return None
    return (prices[-1] / prices[0]) * (1 - BUY_FEE) * (1 - SELL_FEE - ETF_SELL_TAX) - 1


def select_parameters(history: list[dict[str, tuple[float, float]]]) -> tuple[dict, list[dict]]:
    candidates = []
    for lookback in (10, 20, 40):
        for count in (3, 5, 10):
            for holding in (5, 10):
                result = run_slice(history, lookback, count, holding)
                # Favour return but penalise unstable drawdowns; no test data is used here.
                score = result["return"] + 0.35 * result["mdd"]
                candidates.append({"lookback": lookback, "count": count, "holding": holding, "score": score, **result})
    return max(candidates, key=lambda row: row["score"]), candidates


def pct(value: float | None) -> str:
    return "資料不足" if value is None else f"{value * 100:+.2f}%"


def report(path: Path, output: Path) -> None:
    dates, history = load_history(path)
    if len(history) < 120:
        raise SystemExit("歷史資料不足 120 個交易日，無法進行樣本外回測。")
    train_end = int(len(history) * 0.60)
    validation_end = int(len(history) * 0.80)
    train, validation, test = history[:train_end], history[train_end:validation_end], history[validation_end:]
    chosen, _ = select_parameters(train)
    validation_result = run_slice(validation, chosen["lookback"], chosen["count"], chosen["holding"])
    test_result = run_slice(test, chosen["lookback"], chosen["count"], chosen["holding"])
    benchmark = baseline_0050(test)
    result = {
        "source": "TWSE official daily close data; listed stocks only",
        "data_start": dates[0], "data_end": dates[-1], "trading_days": len(history),
        "split": {"train": len(train), "validation": len(validation), "test": len(test)},
        "selected": {key: chosen[key] for key in ("lookback", "count", "holding")},
        "validation": {key: validation_result[key] for key in ("return", "mdd", "trades", "win_rate")},
        "test": {key: test_result[key] for key in ("return", "mdd", "trades", "win_rate")},
        "benchmark_0050_price_return": benchmark,
        "cost_assumptions": {"buy_fee": BUY_FEE, "sell_fee": SELL_FEE, "stock_sell_tax": STOCK_SELL_TAX, "etf_sell_tax": ETF_SELL_TAX},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    output.write_text("\n".join([
        "【近一年上市股票樣本外回測】",
        f"資料區間：{dates[0]} 至 {dates[-1]}，共 {len(history)} 個交易日（僅上市股票）。",
        f"訓練／驗證／測試：{len(train)}／{len(validation)}／{len(test)} 個交易日。",
        f"訓練選出的參數：動能 {chosen['lookback']} 日、持有前 {chosen['count']} 檔、持有 {chosen['holding']} 日。",
        f"驗證期：報酬 {pct(validation_result['return'])}，最大回撤 {pct(validation_result['mdd'])}，勝率 {pct(validation_result['win_rate'])}，{validation_result['trades']} 次再平衡。",
        f"保留測試期：報酬 {pct(test_result['return'])}，最大回撤 {pct(test_result['mdd'])}，勝率 {pct(test_result['win_rate'])}，{test_result['trades']} 次再平衡。",
        f"0050 價格報酬（同測試期、含 ETF 費稅假設）：{pct(benchmark)}。",
        "成本已計入：買進手續費 0.1425%、賣出手續費 0.1425%、股票賣出證交稅 0.3%。",
        "限制：未計入股利／除權息還原、滑價、融資券與上櫃股票；結果不構成投資建議。",
    ]) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--days", type=int, default=365)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--input", type=Path, default=DEFAULT_DATA)
    report_parser.add_argument("--output", type=Path, default=DATA_DIR / "one_year_backtest.md")
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.days, args.output)
    else:
        report(args.input, args.output)


if __name__ == "__main__":
    main()
