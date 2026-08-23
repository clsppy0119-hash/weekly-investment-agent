import argparse
import json
import os
from pathlib import Path

from actual_comprehensive_selection import display_name
from backtest import BUY_FEE, ETF_SELL_TAX, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX
from scoring import number


DEFAULT_PATH = Path("strategy_data/recommendations.json")
HORIZONS = (5, 20, 60)
STRATEGY_VERSION = "2.1"
BENCHMARK_CODE = "0050"

def load_state(path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return {"schemaVersion": 1, "recommendations": []}
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def save_state(state, path=DEFAULT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _net(gross, sell_tax=STOCK_SELL_TAX):
    """Round-trip return after costs, on the same model the backtest uses.

    Tracking that ignores costs reports a better number than the backtest for
    the identical trade, so the two can never be checked against each other.
    """
    return (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - sell_tax - SLIPPAGE_BPS / 10_000) - 1


def _bounded_ratio(numerator, denominator):
    """Return a positive finite ratio inside the registered numeric domain."""
    if not (
        number(numerator) and numerator > 0
        and number(denominator) and denominator > 0
    ):
        return None
    ratio = numerator / denominator
    return ratio if number(ratio) and ratio > 0 else None


def _bounded_percent(value):
    if not number(value):
        return None
    percent = round(value * 100, 2)
    return percent if number(percent) else None


def _extend_trail(trail, history, entry_date):
    """Accumulate observed closes after ``entry_date`` into an append-only trail.

    ``quotes.json`` keeps only a short rolling window of history, so outcomes
    derived from it directly can never reach the longer horizons, and the ones
    that do settle drift as the window slides.  Each recommendation therefore
    carries its own trail, which only ever grows.
    """
    for row in history:
        day = row.get("date", "")
        close = row.get("close")
        if day > entry_date and number(close) and close > 0 and day not in trail:
            trail[day] = float(close)
    return trail


def _extend_pool_trail(trail, history, pool_prices, entry_date):
    """Equal-weighted index of everything that was eligible on the entry date.

    Beating 0050 answers two questions at once: did the ranking pick well, and
    did the eligible universe happen to beat a large-cap index. A shortlist is
    only claiming the first, so it has to be judged against the pool it was
    drawn from. The pool is fixed at decision time and cannot be reconstructed
    afterwards, which is why it is recorded rather than recomputed.
    """
    ratios: dict[str, list[float]] = {}
    for code, entry_price in pool_prices.items():
        if not (number(entry_price) and entry_price > 0):
            continue
        for row in history.get(code, []):
            day = row.get("date", "")
            close = row.get("close")
            ratio = _bounded_ratio(close, entry_price)
            if day > entry_date and ratio is not None and day not in trail:
                ratios.setdefault(day, []).append(ratio)
    for day, values in ratios.items():
        average = sum(values) / len(values)
        if number(average):
            trail[day] = average
    return trail


def _extend_dividend_factors(factors, events, code, entry_date):
    """Accumulate ex-rights adjustment factors for one holding.

    A stock going ex-dividend drops by the distribution, which the raw close
    records as a loss the holder never took. ``before_close / reference_price``
    is the exchange's own statement of that drop, so it restores what the
    holder actually received.
    """
    for event in events:
        if str(event.get("code")) != str(code):
            continue
        day = str(event.get("date", ""))[:10]
        before, reference = event.get("before_close"), event.get("reference_price")
        factor = _bounded_ratio(before, reference)
        if day > entry_date and day not in factors and factor is not None:
            factors[day] = factor
    return factors


def _dividend_factor(factors, day):
    product = 1.0
    for event_day, factor in factors.items():
        if event_day <= day:
            if not (number(factor) and factor > 0):
                return None
            product *= factor
            if not number(product) or product <= 0:
                return None
    return product


def _settle(outcomes, trail, entry_price, benchmark_trail, benchmark_entry, pool_trail=None,
            dividend_factors=None):
    """Fill in horizons the trail can now support; never revise a settled one."""
    days = sorted(trail)
    for horizon in HORIZONS:
        key = str(horizon)
        settled = outcomes.get(key, {})
        if settled.get("status") == "complete" and "netReturnPct" in settled:
            continue  # already settled; the trail behind it must not be re-read
        # A legacy "complete" without a net return came from the old rolling
        # window, where the horizon drifted and costs were ignored.  Those are
        # re-settled from the trail rather than trusted.
        if len(days) < horizon:
            outcomes[key] = {"status": "pending", "observations": len(days)}
            continue
        day = days[horizon - 1]
        price_ratio = _bounded_ratio(trail.get(day), entry_price)
        # Two figures, because the two comparisons need different ones. 0050 is
        # a total-return index, so the holding has to include its distributions
        # to be comparable. The pool is an equal-weighted price series with no
        # dividend data of its own, so that comparison stays price-only on both
        # sides rather than crediting one and not the other.
        factor = _dividend_factor(dividend_factors or {}, day)
        total_ratio = price_ratio * factor if price_ratio is not None and factor is not None else None
        gross = price_ratio - 1 if price_ratio is not None else None
        total_gross = (
            total_ratio - 1
            if number(total_ratio) and total_ratio > 0 else None
        )
        net = _net(gross) if number(gross) else None
        total_net = _net(total_gross) if number(total_gross) else None
        gross_pct = _bounded_percent(gross)
        net_pct = _bounded_percent(net)
        total_net_pct = _bounded_percent(total_net)
        rounded_factor = round(factor, 6) if number(factor) and factor > 0 else None
        if rounded_factor == 0:
            rounded_factor = None
        if None in (rounded_factor, gross_pct, net_pct, total_net_pct):
            outcomes[key] = {
                "status": "pending",
                "observations": len(days),
                "reason": "derived_return_out_of_numeric_domain",
            }
            continue
        record = {
            "status": "complete",
            "date": day,
            "price": trail[day],
            "grossReturnPct": gross_pct,
            "netReturnPct": net_pct,
            "totalReturnNetPct": total_net_pct,
            "exRightsFactor": rounded_factor,
            # True while no ex-rights event is known for this holding: either
            # none occurred, or none was fetched. The distinction matters, so
            # the factor above is reported rather than folded in silently.
            "priceReturnOnly": factor == 1.0,
        }
        if number(benchmark_entry) and benchmark_entry > 0 and day in benchmark_trail:
            benchmark_ratio = _bounded_ratio(benchmark_trail[day], benchmark_entry)
            benchmark_net = (
                _net(benchmark_ratio - 1, ETF_SELL_TAX)
                if benchmark_ratio is not None else None
            )
            benchmark_pct = _bounded_percent(benchmark_net)
            excess_pct = _bounded_percent(total_net - benchmark_net) if number(benchmark_net) else None
            if benchmark_pct is not None and excess_pct is not None:
                record["benchmarkNetReturnPct"] = benchmark_pct
                # Total return on both sides: 0050 is a total-return index.
                record["excessReturnPct"] = excess_pct
            else:
                record["benchmarkAvailable"] = False
        else:
            record["benchmarkAvailable"] = False
        if pool_trail and day in pool_trail:
            # The pool is charged the same round trip, so this compares
            # selection skill rather than a costs artefact.
            pool_ratio = pool_trail[day]
            pool_net = (
                _net(pool_ratio - 1)
                if number(pool_ratio) and pool_ratio > 0 else None
            )
            pool_pct = _bounded_percent(pool_net)
            pool_excess_pct = _bounded_percent(net - pool_net) if number(pool_net) else None
            if pool_pct is not None and pool_excess_pct is not None:
                record["poolNetReturnPct"] = pool_pct
                record["poolExcessPct"] = pool_excess_pct
            else:
                record["poolAvailable"] = False
        else:
            record["poolAvailable"] = False
        outcomes[key] = record
    return outcomes


def _risk_flags(quote, fund, coverage):
    flags = []
    if coverage < 70:
        flags.append("分析權重未達 70%，僅供追蹤")
    if not number(fund.get("revenueYoY")):
        flags.append("缺少月營收年增率")
    if not number(fund.get("eps")):
        flags.append("缺少近四季 EPS")
    if not number(fund.get("roe")):
        flags.append("缺少 ROE")
    if not number(fund.get("debtRatio")):
        flags.append("缺少負債比")
    history_years = fund.get("financialHistoryYears")
    if not number(history_years) or history_years < 5:
        flags.append("近五年財務歷史不足")
    if number(fund.get("revenueYoY")) and fund["revenueYoY"] < 0:
        flags.append("月營收年增為負")
    if number(fund.get("roe")) and fund["roe"] < 8:
        flags.append("ROE 低於 8%")
    if number(fund.get("debtRatio")) and fund["debtRatio"] >= 60:
        flags.append("負債比達 60% 以上")
    if not number(quote.get("ma20")):
        flags.append("20 日均線資料不足")
    return flags


def _decision_snapshot(report_date, score, coverage, quote, fund):
    required = ("revenueYoY", "eps", "roe", "debtRatio")
    missing = [field for field in required if not number(fund.get(field))]
    history_years = fund.get("financialHistoryYears")
    five_year_ready = number(history_years) and history_years >= 5
    eligible = coverage >= 80 and not missing and five_year_ready
    if eligible:
        decision = "正式研究候選"
        rationale = "核心財務欄位、近五年歷史與分析權重均達門檻，進入後續人工研究；不是買進指令。"
    elif coverage >= 70:
        decision = "僅追蹤"
        rationale = "分析權重達基本門檻，但關鍵財務或歷史資料尚未完整，不產生投資結論。"
    else:
        decision = "暫不決策"
        rationale = "資料不足，僅保留觀察紀錄。"
    return {
        "schemaVersion": 2,
        "recordedAt": report_date,
        "strategyVersion": STRATEGY_VERSION,
        "decision": decision,
        "rationale": rationale,
        "dataCompleteness": {
            "analysisWeightPct": coverage,
            "requiredMetrics": required,
            "missingMetrics": missing,
            "fiveYearHistory": five_year_ready,
        },
        "sources": {
            "market": "TWSE／TPEx 公開行情（由既有行情流程整理）",
            "fundamentals": fund.get("financialSource", "公開基本面資料／尚待補齊"),
            "quoteUpdatedAt": quote.get("updatedAt"),
            "financialPeriod": fund.get("financialPeriod"),
        },
        "snapshot": {
            "price": quote.get("price") if number(quote.get("price")) else None,
            "change": quote.get("change") if number(quote.get("change")) else None,
            "ma5": quote.get("ma5") if number(quote.get("ma5")) else None,
            "ma20": quote.get("ma20") if number(quote.get("ma20")) else None,
            "revenueYoY": fund.get("revenueYoY") if number(fund.get("revenueYoY")) else None,
            "epsTTM": fund.get("eps") if number(fund.get("eps")) else None,
            "roeTTM": fund.get("roe") if number(fund.get("roe")) else None,
            "debtRatio": fund.get("debtRatio") if number(fund.get("debtRatio")) else None,
            "pe": fund.get("pe") if number(fund.get("pe")) else None,
            "pb": fund.get("pb") if number(fund.get("pb")) else None,
            "dividendYield": fund.get("dividendYield") if number(fund.get("dividendYield")) else None,
        },
        "riskFlags": _risk_flags(quote, fund, coverage),
    }


def record_recommendations(report_date, report_mode, ranked, quote_data, path=DEFAULT_PATH,
                           pools=None, actions=None):
    state = load_state(path)
    recommendations = state.setdefault("recommendations", [])
    stored_pools = state.setdefault("pools", {})
    existing = {item.get("id") for item in recommendations}
    history = quote_data.get("history", {})
    benchmark_history = history.get(BENCHMARK_CODE, [])
    benchmark_price = quote_data.get("quotes", {}).get(BENCHMARK_CODE, {}).get("price")
    benchmark_price = benchmark_price if number(benchmark_price) and benchmark_price > 0 else None
    action_events = (actions or {}).get("events", []) if isinstance(actions, dict) else (actions or [])
    for style, items in ranked.items():
        pool_key = f"{report_date}:{report_mode}:{style}"
        # Which names were eligible depends on that day's scores and coverage,
        # so it cannot be rebuilt later; record it once per report.
        if pools and style in pools and pool_key not in stored_pools:
            stored_pools[pool_key] = {
                "entryDate": report_date,
                "prices": {entry[2]: entry[3].get("price") for entry in pools[style]
                           if number(entry[3].get("price")) and entry[3].get("price") > 0},
            }
        for rank, item in enumerate(items, 1):
            score, coverage, code, quote, fund = item
            record_id = f"{report_date}:{report_mode}:{style}:{code}"
            if record_id in existing:
                continue
            snapshot_quote = {**quote, "updatedAt": quote_data.get("updatedAt")}
            recommendations.append({"id": record_id, "date": report_date, "mode": report_mode, "style": style, "rank": rank, "code": code, "name": display_name(quote.get("name"), str(code)), "entryPrice": quote.get("price"), "benchmarkEntryPrice": benchmark_price, "poolKey": pool_key, "score": score, "coverage": coverage, "strategyVersion": STRATEGY_VERSION, "quoteUpdatedAt": quote_data.get("updatedAt"), "decisionRecord": _decision_snapshot(report_date, score, coverage, snapshot_quote, fund), "outcomes": {}, "priceTrail": {}})
            existing.add(record_id)
    for item in recommendations:
        price = item.get("entryPrice")
        if number(price) and price > 0:
            entry_date = item.get("date", "")
            trail = _extend_trail(item.setdefault("priceTrail", {}), history.get(item.get("code"), []), entry_date)
            benchmark_trail = _extend_trail(item.setdefault("benchmarkTrail", {}), benchmark_history, entry_date)
            pool = stored_pools.get(item.get("poolKey"), {})
            pool_trail = _extend_pool_trail(item.setdefault("poolTrail", {}), history,
                                            pool.get("prices", {}), entry_date) if pool else {}
            factors = _extend_dividend_factors(item.setdefault("exRightsFactors", {}),
                                               action_events, item.get("code"), entry_date)
            item["outcomes"] = _settle(item.get("outcomes") or {}, trail, price,
                                       benchmark_trail, item.get("benchmarkEntryPrice"),
                                       pool_trail, factors)
        if "decisionRecord" not in item:
            # 舊紀錄沒有原始快照；明確標示為補建，不把今天資料偽裝成當日資料。
            quote = {**quote_data.get("quotes", {}).get(item.get("code"), {}), "updatedAt": quote_data.get("updatedAt")}
            fund = quote_data.get("fundamentals", {}).get(item.get("code"), {})
            reconstructed = _decision_snapshot(report_date, item.get("score", 0), item.get("coverage", 0), quote, fund)
            reconstructed["reconstructed"] = True
            reconstructed["reconstructionNote"] = "此筆為舊紀錄補建；資料快照日期以本次補建日為準。"
            item["decisionRecord"] = reconstructed
    state["lastReviewedAt"] = report_date
    save_state(state, path)
    return state


def horizon_review(state):
    """Settled results per horizon.

    The 5-, 20- and 60-day results for one recommendation are three views of the
    same position, not three independent observations, and they cover different
    holding periods.  Pooling them inflates the sample and averages returns that
    are not comparable, so each horizon is reported on its own.
    """
    review = {}
    for horizon in HORIZONS:
        net = []
        excess = []
        pool_excess = []
        for item in state.get("recommendations", []):
            result = item.get("outcomes", {}).get(str(horizon), {})
            if result.get("status") != "complete":
                continue
            net.append(result["netReturnPct"])
            if "excessReturnPct" in result:
                excess.append(result["excessReturnPct"])
            if "poolExcessPct" in result:
                pool_excess.append(result["poolExcessPct"])
        entry = {"settled": len(net)}
        if pool_excess:
            entry["versusPool"] = len(pool_excess)
            entry["meanPoolExcessPct"] = round(sum(pool_excess) / len(pool_excess), 2)
            entry["beatPoolPct"] = round(sum(1 for value in pool_excess if value > 0) / len(pool_excess) * 100)
        if net:
            entry["winRatePct"] = round(sum(1 for value in net if value > 0) / len(net) * 100)
            entry["meanNetReturnPct"] = round(sum(net) / len(net), 2)
        if excess:
            entry["benchmarked"] = len(excess)
            entry["meanExcessReturnPct"] = round(sum(excess) / len(excess), 2)
            entry["beatBenchmarkPct"] = round(sum(1 for value in excess if value > 0) / len(excess) * 100)
        review[str(horizon)] = entry
    return review


def review_summary(state):
    legacy_notice = "【舊版診斷】以下統計不是策略驗證或升級證據。\n"
    review = horizon_review(state)
    lines = []
    for horizon in HORIZONS:
        entry = review[str(horizon)]
        if not entry["settled"]:
            lines.append(f"　{horizon:>2} 日：尚未結算。")
            continue
        line = f"　{horizon:>2} 日：{entry['settled']} 筆，勝率 {entry['winRatePct']}%，扣成本平均 {entry['meanNetReturnPct']:+.2f}%"
        if "meanExcessReturnPct" in entry:
            line += f"，對 0050 超額 {entry['meanExcessReturnPct']:+.2f}%（{entry['beatBenchmarkPct']}% 跑贏）"
        if "meanPoolExcessPct" in entry:
            line += f"，對合格池超額 {entry['meanPoolExcessPct']:+.2f}%（{entry['beatPoolPct']}% 跑贏）"
        lines.append(line + "。")
    if all(not review[str(horizon)]["settled"] for horizon in HORIZONS):
        return legacy_notice + "策略追蹤：已開始保存候選紀錄，尚無任何期間結算。"
    return (legacy_notice + "策略追蹤（各期間分開統計，報酬已扣手續費、證交稅與滑價）：\n"
            "　對 0050 為總報酬對總報酬；對合格池雙方皆為價格報酬。\n" + "\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="檢視投資候選追蹤狀態")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args()
    state = load_state(args.path)
    print(review_summary(state))
    print(f"累積推薦：{len(state.get('recommendations', []))} 筆")


if __name__ == "__main__":
    main()
