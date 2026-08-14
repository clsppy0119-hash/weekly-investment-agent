"""Download official TWSE daily data and run a leakage-resistant rolling backtest.

The first version intentionally covers listed stocks only.  It uses the public
TWSE daily closing file, does not use today's stock universe for past dates,
and compares the strategy with the official Taiwan 50 total-return index.
If that benchmark cache is missing, the run is rejected rather than silently
falling back to an unfair price-only comparison.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from execution_accounting import (
    aggregate_periods,
    daily_equity_curve,
    max_drawdown_from_equity,
    max_drawdown_from_period_returns,
    rebalance_schedule,
    settle_equal_weight_period,
    unconfigured_risk_policy,
)

try:
    import requests
except ImportError:  # GitHub runner can use the standard-library fallback.
    requests = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "backtest_data"
DEFAULT_DATA = DATA_DIR / "twse_daily.jsonl"
DEFAULT_BENCHMARK = DATA_DIR / "tai50_total_return.json"
TWSE_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
BUY_FEE = 0.001425
SELL_FEE = 0.001425
STOCK_SELL_TAX = 0.003
ETF_SELL_TAX = 0.001
SLIPPAGE_BPS = 10


def number(value: object) -> float | None:
    if value in (None, "", "--", "---"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def fetch_json(request: urllib.request.Request, timeout: int = 12) -> dict:
    if requests is not None:
        response = requests.get(request.full_url, headers=dict(request.header_items()), timeout=timeout)
        response.raise_for_status()
        return response.json()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def weekday_dates(days: int, end: date | None = None) -> list[date]:
    end = end or date.today()
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
            payload = fetch_json(request)
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
            time.sleep(0.8 * (attempt + 1))
    return None


def month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    result: list[date] = []
    while current <= end:
        result.append(current)
        year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
        current = date(year, month, 1)
    return result


def fetch_tai50_month(month: date) -> list[dict]:
    """Fetch official Taiwan 50 price and total-return index for one month."""
    query = urllib.parse.urlencode({"response": "json", "date": month.strftime("%Y%m%d")})
    request = urllib.request.Request(
        f"https://www.twse.com.tw/indicesReport/TAI50I?{query}",
        headers={"User-Agent": "weekly-investment-agent/1.0"},
    )
    for attempt in range(3):
        try:
            payload = fetch_json(request)
            if payload.get("stat") != "OK":
                return []
            rows = []
            for values in payload.get("data", []):
                if len(values) < 3 or values[2] in ("--", ""):
                    continue
                try:
                    roc_year, month_no, day_no = (int(part) for part in str(values[0]).split("/"))
                    rows.append({
                        "date": date(roc_year + 1911, month_no, day_no).isoformat(),
                        "price": number(values[1]),
                        "total_return": number(values[2]),
                    })
                except (TypeError, ValueError):
                    continue
            return [row for row in rows if row["total_return"] is not None]
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    return []


def collect_tai50(days: int, output: Path, end: date | None = None) -> None:
    end = end or date.today()
    start = end - timedelta(days=days)
    rows: dict[str, dict] = {}
    if output.exists():
        try:
            rows = {str(row["date"]): row for row in json.loads(output.read_text(encoding="utf-8-sig")) if isinstance(row, dict) and row.get("date")}
        except (OSError, json.JSONDecodeError):
            rows = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_tai50_month, month): month for month in month_starts(start, end)}
        for future in as_completed(futures):
            for row in future.result():
                if start.isoformat() <= row["date"] <= end.isoformat():
                    rows[row["date"]] = row
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps([rows[key] for key in sorted(rows)], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([rows[key] for key in sorted(rows)], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Taiwan 50 total-return rows: {len(rows)} -> {output}")
    if not rows:
        raise RuntimeError("official Taiwan 50 total-return endpoint returned no usable rows")
    # A month that failed all three attempts returns [] and merges in as
    # nothing, so partial coverage has to be reported rather than inferred from
    # the row count looking plausible.
    expected = {month.strftime("%Y-%m") for month in month_starts(start, end)}
    covered = {day[:7] for day in rows}
    if expected - covered:
        print(f"警告：{len(expected - covered)} 個月份沒有任何指數資料："
              f"{', '.join(sorted(expected - covered)[:8])}")
        print("  基準不完整時，該期間的超額報酬無法計算，回測會靜默略過那些窗口。")


# Lunar New Year closes the exchange for up to nine weekdays, and a warning
# that fires every February is a warning nobody reads.
MAX_HOLIDAY_RUN = 9


def report_gaps(existing: dict[str, dict], failed_days: list[str]) -> None:
    """Say out loud when the cache is not continuous.

    Both fetchers give up quietly -- a day returns None, a month returns [] --
    and a partial download then looks exactly like a complete one. The backtest
    indexes history by position, so a hole does not raise: it silently stretches
    "twenty trading days later" into whatever the gap spans. A run that ended
    with three years missing in the middle still printed a success line.
    """
    days_present = sorted(existing)
    if len(days_present) < 2:
        return
    start, end = date.fromisoformat(days_present[0]), date.fromisoformat(days_present[-1])
    absent, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor.isoformat() not in existing:
            absent.append(cursor)
        cursor += timedelta(days=1)
    runs: list[list[date]] = []
    for day in absent:
        if runs and (day - runs[-1][-1]).days <= 3:
            runs[-1].append(day)
        else:
            runs.append([day])
    # Public holidays cluster; a longer run is a download that did not finish.
    suspicious = [run for run in runs if len(run) > MAX_HOLIDAY_RUN]
    if failed_days:
        print(f"警告：{len(failed_days)} 個交易日抓取失敗，最早 {min(failed_days)}")
    if suspicious:
        print(f"警告：資料不連續，{len(suspicious)} 個區段的缺口長於假期可解釋的範圍：")
        for run in suspicious[:10]:
            print(f"  {run[0].isoformat()} → {run[-1].isoformat()}（缺 {len(run)} 個平日）")
        print("  回測以位置索引，缺口會讓持有期被無聲拉長；補齊後再使用。")


def collect(days: int, output: Path, workers: int, end: date | None = None, request_delay: float = 2.0) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            # An interrupted or fully failed run can leave blank lines behind;
            # without this the cache is poisoned and every later run dies here.
            if not line.strip():
                continue
            row = json.loads(line)
            existing[row["date"]] = row
    wanted = weekday_dates(days, end)
    missing = [day for day in wanted if day.isoformat() not in existing]
    failed_days: list[str] = []
    completed = len(wanted) - len(missing)
    # Keep parallelism deliberately low: this shortens a first full download
    # while remaining polite to the public data source.
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        futures = {pool.submit(fetch_day, day): day for day in missing}
        for future in as_completed(futures):
            payload = future.result()
            if payload:
                existing[payload["date"]] = payload
            else:
                failed_days.append(futures[future].isoformat())
            # Stay below the public endpoint's burst threshold; the cache is
            # resumable, so a slower reliable run is preferable to retries.
            time.sleep(max(0.0, request_delay))
            completed += 1
            if completed % 25 == 0 or completed == len(wanted):
                output.write_text("\n".join(json.dumps(existing[key], ensure_ascii=False) for key in sorted(existing)) + "\n", encoding="utf-8")
            if completed % 25 == 0 or completed == len(wanted):
                print(f"資料下載進度：{completed}/{len(wanted)}")
    output.write_text("\n".join(json.dumps(existing[key], ensure_ascii=False) for key in sorted(existing)) + "\n", encoding="utf-8")
    print(f"已保存 {len(existing)} 個交易日到 {output}")

    if not existing:
        raise RuntimeError(f"official daily endpoint returned no usable rows; failed_dates={len(failed_days)}")
    report_gaps(existing, failed_days)


def load_history(path: Path) -> tuple[list[str], list[dict[str, tuple[float, float]]]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records.sort(key=lambda row: row["date"])
    return [row["date"] for row in records], [{code: (float(close), float(volume)) for code, close, volume in row["rows"]} for row in records]


def run_slice(history: list[dict[str, tuple[float, float]]], lookback: int, count: int,
              holding: int, dates: list[str] | None = None) -> dict:
    if dates is not None and len(dates) != len(history):
        raise ValueError("history_date_length_mismatch")
    labels = dates if dates is not None else [str(index) for index in range(len(history))]
    periods: list[dict] = []
    buy_cost = BUY_FEE + SLIPPAGE_BPS / 10_000
    sell_cost = SELL_FEE + STOCK_SELL_TAX + SLIPPAGE_BPS / 10_000
    schedule = rebalance_schedule(
        len(history), lookback, holding, convention="entry_plus_holding"
    )
    for point in schedule:
        index = point.signal_index
        today, past = history[index], history[index - lookback]
        window = history[point.entry_index:point.exit_index + 1]
        ranked = []
        # Rank on signal-day information only.  Requiring an entry or exit price
        # here would let a stock that stops trading during the holding period
        # leave the candidate pool instead of taking its loss, which is exactly
        # the survivorship bias the point-in-time gate is meant to prevent.
        for code, (price, volume) in today.items():
            previous = past.get(code)
            if not previous or volume < 500_000:
                continue
            momentum = price / previous[0] - 1
            if price >= 10 and momentum > -0.25:
                ranked.append((momentum, code))
        ranked_order = sorted(ranked, reverse=True)
        selected = ranked_order[:count]
        tie_dependent = 0
        if len(ranked_order) > count and selected and ranked_order[count][0] == selected[-1][0]:
            cutoff = selected[-1][0]
            tie_dependent = sum(1 for score, _ in selected if score == cutoff)
        outcomes: list[dict] = []
        for _, code in selected:
            entry_price = window[0].get(code)
            if not entry_price:
                outcomes.append({"status": "cash_unfilled", "pathLength": len(window)})
                continue
            exit_price = window[-1].get(code)
            if not exit_price:
                outcomes.append({"status": "unresolved_exit", "reason": "nominal_exit_missing", "pathLength": len(window)})
                continue
            path_prices = [day.get(code) for day in window]
            if any(price is None for price in path_prices):
                outcomes.append({"status": "unresolved_exit", "reason": "daily_mark_missing", "pathLength": len(window)})
                continue
            factors = [price[0] / entry_price[0] for price in path_prices]
            outcomes.append({"status": "closed", "grossReturn": factors[-1] - 1, "dailyGrossFactors": factors})
        period = settle_equal_weight_period(
            outcomes,
            count,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
        )
        period["tieBreakDependentSlots"] = tie_dependent
        if tie_dependent:
            period["blockers"] = sorted(set(period["blockers"]) | {"cutoff_tie_dependent"})
        period.update({
            "signalDate": labels[point.signal_index],
            "entryDate": labels[point.entry_index],
            "exitDate": labels[point.exit_index],
        })
        periods.append(period)
    comparison_from = labels[schedule[0].entry_index] if schedule else None
    comparison_to = labels[schedule[-1].exit_index] if schedule else None
    accounting = aggregate_periods(periods, comparison_from=comparison_from, comparison_to=comparison_to)
    comparison_days = (
        labels.index(comparison_to) - labels.index(comparison_from) + 1
        if comparison_from and comparison_to else 0
    )
    accounting["comparisonTradingDays"] = comparison_days
    selection_certified = accounting["tieBreakDependentSlots"] == 0
    returns = [period["return"] for period in periods] if accounting["complete"] else None
    total = math.prod(1 + value for value in returns) - 1 if returns is not None else None
    invested_returns = [period["return"] for period in periods if period.get("closedSlots", 0) > 0] if returns is not None else []
    wins = sum(value > 0 for value in invested_returns)
    result = {
        "return": total,
        "mdd": max_drawdown_from_equity(daily_equity_curve(periods)),
        "mddBasis": "daily_mark_to_market_including_costs",
        "selectionMdd": max_drawdown_from_period_returns(returns),
        "selectionCertified": selection_certified,
        "trades": accounting["investedPeriods"],
        "win_rate": wins / len(invested_returns) if invested_returns else 0.0,
        "returns": returns,
        "unfilled": accounting["unfilledEntrySlots"],
        "stale_exits": accounting["unresolvedExitSlots"],
        "executionComplete": accounting["complete"],
        "executionAccounting": accounting,
    }
    result.update(unconfigured_risk_policy())
    return result


def baseline_0050_price_proxy(history: list[dict[str, tuple[float, float]]]) -> float | None:
    prices = [day.get("0050", (None, 0))[0] for day in history]
    prices = [price for price in prices if price]
    if len(prices) < 2:
        return None
    gross = prices[-1] / prices[0] - 1
    return (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - ETF_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1


def benchmark_total_return(path: Path, dates: list[str]) -> float | None:
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    values = {row.get("date"): row.get("total_return") for row in rows if isinstance(row, dict)}
    if len(dates) < 2:
        return None
    start, end = dates[0], dates[-1]
    if any(not isinstance(values.get(day), (int, float)) for day in dates):
        return None
    if values[start] <= 0:
        return None
    gross = values[end] / values[start] - 1
    return (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - ETF_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1


def select_parameters(history: list[dict[str, tuple[float, float]]], dates: list[str] | None = None) -> tuple[dict, list[dict]]:
    candidates = []
    # A one-year split leaves roughly 48 trading days in each holdout period.
    # Keep the candidate window short enough that both holdouts contain several
    # non-overlapping trades; otherwise a zero-trade "result" is meaningless.
    for lookback in (5, 10, 20):
        for count in (3, 5, 10):
            for holding in (5,):
                result = run_slice(history, lookback, count, holding, dates=dates)
                # Favour return but penalise unstable drawdowns; no test data is used here.
                score = (
                    result["return"] + 0.35 * result["selectionMdd"]
                    if result["executionComplete"] and result["selectionCertified"] and result["trades"] >= 6
                    else float("-inf")
                )
                candidates.append({"lookback": lookback, "count": count, "holding": holding, "score": score, **result})
    valid = [candidate for candidate in candidates if math.isfinite(candidate["score"])]
    if not valid:
        raise ValueError("execution_accounting_incomplete")
    return max(valid, key=lambda row: row["score"]), candidates


def pct(value: float | None) -> str:
    return "資料不足" if value is None else f"{value * 100:+.2f}%"


def report(path: Path, output: Path, benchmark_path: Path = DEFAULT_BENCHMARK) -> None:
    dates, history = load_history(path)
    if len(history) < 120:
        raise SystemExit("歷史資料不足 120 個交易日，無法進行樣本外回測。")
    train_end = int(len(history) * 0.60)
    validation_end = int(len(history) * 0.80)
    train, validation, test = history[:train_end], history[train_end:validation_end], history[validation_end:]
    train_dates = dates[:train_end]
    validation_dates = dates[train_end:validation_end]
    test_dates = dates[validation_end:]
    try:
        chosen, _ = select_parameters(train, train_dates)
    except ValueError as exc:
        if str(exc) != "execution_accounting_incomplete":
            raise
        result = {
            "schemaVersion": 2,
            "source": "TWSE official daily close data; listed stocks only",
            "data_start": dates[0], "data_end": dates[-1], "trading_days": len(history),
            "split": {"train": len(train), "validation": len(validation), "test": len(test)},
            "selected": None, "validation": None, "test": None,
            "benchmark": {"total_return": False, "source": "TWSE official Taiwan 50 total-return index (IR0002)"},
            "decision": "research_only",
            "promotionBlockers": ["execution_accounting_incomplete"],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        output.write_text("研究模式：訓練資料的執行會計不完整，未產生可比較績效。\n", encoding="utf-8")
        print(output.read_text(encoding="utf-8"))
        return
    validation_result = run_slice(validation, chosen["lookback"], chosen["count"], chosen["holding"], validation_dates)
    test_result = run_slice(test, chosen["lookback"], chosen["count"], chosen["holding"], test_dates)
    validation_bounds = [validation_result["executionAccounting"]["comparisonFrom"], validation_result["executionAccounting"]["comparisonTo"]]
    test_bounds = [test_result["executionAccounting"]["comparisonFrom"], test_result["executionAccounting"]["comparisonTo"]]
    validation_path = validation_dates[
        validation_dates.index(validation_bounds[0]):validation_dates.index(validation_bounds[1]) + 1
    ] if all(day in validation_dates for day in validation_bounds) else []
    test_path = test_dates[
        test_dates.index(test_bounds[0]):test_dates.index(test_bounds[1]) + 1
    ] if all(day in test_dates for day in test_bounds) else []
    validation_benchmark = benchmark_total_return(benchmark_path, validation_path)
    test_benchmark = benchmark_total_return(benchmark_path, test_path)
    benchmark = test_benchmark
    passed = (
        validation_result["executionComplete"]
        and test_result["executionComplete"]
        and validation_result["selectionCertified"]
        and test_result["selectionCertified"]
        and validation_result["dailyMddGatePassed"]
        and test_result["dailyMddGatePassed"]
        and validation_benchmark is not None
        and test_benchmark is not None
        and validation_result["return"] > validation_benchmark
        and test_result["return"] > test_benchmark
        and test_result["trades"] >= 5
    )
    result = {
        "schemaVersion": 2,
        "source": "TWSE official daily close data; listed stocks only",
        "data_start": dates[0], "data_end": dates[-1], "trading_days": len(history),
        "split": {"train": len(train), "validation": len(validation), "test": len(test)},
        "selected": {key: chosen[key] for key in ("lookback", "count", "holding")},
        "validation": {key: validation_result[key] for key in ("return", "mdd", "mddBasis", "riskPolicyVersion", "dailyMddLimitConfigured", "dailyMddGatePassed", "riskGateEligible", "selectionCertified", "trades", "win_rate", "executionComplete", "executionAccounting")},
        "test": {key: test_result[key] for key in ("return", "mdd", "mddBasis", "riskPolicyVersion", "dailyMddLimitConfigured", "dailyMddGatePassed", "riskGateEligible", "selectionCertified", "trades", "win_rate", "executionComplete", "executionAccounting")},
        "benchmark": {
            "total_return": validation_benchmark is not None and test_benchmark is not None,
            "executionComplete": validation_benchmark is not None and test_benchmark is not None,
            "source": "TWSE official Taiwan 50 total-return index (IR0002)",
            "validation_net_return": validation_benchmark,
            "test_net_return": test_benchmark,
            "validationAccounting": {
                key: validation_result["executionAccounting"][key]
                for key in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")
            },
            "testAccounting": {
                key: test_result["executionAccounting"][key]
                for key in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")
            },
            "etf_costs_applied": True,
            "cost_model": "one exact-boundary buy-and-hold round trip per split",
        },
        "benchmark_0050_price_return": baseline_0050_price_proxy(test),
        "benchmark_0050_total_return": test_benchmark,
        "decision": "candidate" if passed else (
            "research_only" if not (
                validation_result["executionComplete"]
                and test_result["executionComplete"]
                and validation_result["selectionCertified"]
                and test_result["selectionCertified"]
                and validation_result["dailyMddGatePassed"]
                and test_result["dailyMddGatePassed"]
            ) else "rejected"
        ),
        "promotionBlockers": [
            blocker for blocker, active in {
                "execution_accounting_incomplete": not (
                    validation_result["executionComplete"] and test_result["executionComplete"]
                ),
                "cutoff_tie_dependent": not (
                    validation_result["selectionCertified"] and test_result["selectionCertified"]
                ),
                "daily_mdd_limit_unconfigured": not (
                    validation_result["dailyMddGatePassed"] and test_result["dailyMddGatePassed"]
                ),
                "benchmark_exact_bounds_missing": validation_benchmark is None or test_benchmark is None,
            }.items() if active
        ],
        "cost_assumptions": {"buy_fee": BUY_FEE, "sell_fee": SELL_FEE, "stock_sell_tax": STOCK_SELL_TAX, "etf_sell_tax": ETF_SELL_TAX, "one_way_slippage_bps": SLIPPAGE_BPS},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    output.write_text("\n".join([
        "【官方上市股票滾動樣本外回測】",
        f"資料區間：{dates[0]} 至 {dates[-1]}，共 {len(history)} 個交易日（僅上市股票）。",
        f"訓練／驗證／測試：{len(train)}／{len(validation)}／{len(test)} 個交易日。",
        f"訓練選出的參數：動能 {chosen['lookback']} 日、持有前 {chosen['count']} 檔、持有 {chosen['holding']} 日。",
        f"驗證期：報酬 {pct(validation_result['return'])}，最大回撤 {pct(validation_result['mdd'])}，勝率 {pct(validation_result['win_rate'])}，{validation_result['trades']} 次再平衡。",
        f"保留測試期：報酬 {pct(test_result['return'])}，最大回撤 {pct(test_result['mdd'])}，勝率 {pct(test_result['win_rate'])}，{test_result['trades']} 次再平衡。",
        f"0050 官方總報酬指數（同測試期、含 ETF 費稅假設）：{pct(benchmark)}。",
        "結論：可列入下一輪候選。" if passed else "結論：不採用此參數作正式推薦；保留測試未跑贏 0050，需增加資料期間或改良訊號後再驗證。",
        "成本已計入：買進手續費 0.1425%、賣出手續費 0.1425%、股票賣出證交稅 0.3%、單邊滑價 10 bps。",
        "限制：此官方日行情分支未自行還原股利／除權息，會由同輪總報酬回測交叉驗證；尚未涵蓋上櫃股票，結果不構成投資建議。",
    ]) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--days", type=int, default=365)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    collect_parser.add_argument("--benchmark-output", type=Path, default=DEFAULT_BENCHMARK)
    collect_parser.add_argument("--workers", type=int, default=3)
    collect_parser.add_argument("--end-date", type=lambda value: date.fromisoformat(value), default=None)
    collect_parser.add_argument("--request-delay", type=float, default=2.0)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--input", type=Path, default=DEFAULT_DATA)
    report_parser.add_argument("--output", type=Path, default=DATA_DIR / "one_year_backtest.md")
    report_parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.days, args.output, args.workers, args.end_date, args.request_delay)
        collect_tai50(args.days, args.benchmark_output, args.end_date)
    else:
        report(args.input, args.output, args.benchmark)


if __name__ == "__main__":
    main()
