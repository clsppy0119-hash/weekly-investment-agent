import argparse
import json
import os
from pathlib import Path


from backtest import BUY_FEE, ETF_SELL_TAX, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX


DEFAULT_PATH = Path("strategy_data/recommendations.json")
HORIZONS = (5, 20, 60)
STRATEGY_VERSION = "2.0"
BENCHMARK_CODE = "0050"


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
            json.dump(state, output, ensure_ascii=False, indent=2)
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
        if day > entry_date and number(close) and day not in trail:
            trail[day] = float(close)
    return trail


def _settle(outcomes, trail, entry_price, benchmark_trail, benchmark_entry):
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
        gross = trail[day] / entry_price - 1
        record = {
            "status": "complete",
            "date": day,
            "price": trail[day],
            "grossReturnPct": round(gross * 100, 2),
            "netReturnPct": round(_net(gross) * 100, 2),
            # Cash and stock dividends are not reconstructed here, so a holding
            # that goes ex-dividend inside the window reads as a loss it did not
            # take.  Flagged rather than silently folded into the number.
            "priceReturnOnly": True,
        }
        if number(benchmark_entry) and benchmark_entry > 0 and day in benchmark_trail:
            benchmark_net = _net(benchmark_trail[day] / benchmark_entry - 1, ETF_SELL_TAX)
            record["benchmarkNetReturnPct"] = round(benchmark_net * 100, 2)
            record["excessReturnPct"] = round((_net(gross) - benchmark_net) * 100, 2)
        else:
            record["benchmarkAvailable"] = False
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
    if fund.get("financialHistoryYears", 0) < 5:
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
    five_year_ready = fund.get("financialHistoryYears", 0) >= 5
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
            "price": quote.get("price"),
            "change": quote.get("change"),
            "ma5": quote.get("ma5"),
            "ma20": quote.get("ma20"),
            "revenueYoY": fund.get("revenueYoY"),
            "epsTTM": fund.get("eps"),
            "roeTTM": fund.get("roe"),
            "debtRatio": fund.get("debtRatio"),
            "pe": fund.get("pe"),
            "pb": fund.get("pb"),
            "dividendYield": fund.get("dividendYield"),
        },
        "riskFlags": _risk_flags(quote, fund, coverage),
    }


def record_recommendations(report_date, report_mode, ranked, quote_data, path=DEFAULT_PATH):
    state = load_state(path)
    recommendations = state.setdefault("recommendations", [])
    existing = {item.get("id") for item in recommendations}
    history = quote_data.get("history", {})
    benchmark_history = history.get(BENCHMARK_CODE, [])
    benchmark_price = quote_data.get("quotes", {}).get(BENCHMARK_CODE, {}).get("price")
    for style, items in ranked.items():
        for rank, item in enumerate(items, 1):
            score, coverage, code, quote, fund = item
            record_id = f"{report_date}:{report_mode}:{style}:{code}"
            if record_id in existing:
                continue
            snapshot_quote = {**quote, "updatedAt": quote_data.get("updatedAt")}
            recommendations.append({"id": record_id, "date": report_date, "mode": report_mode, "style": style, "rank": rank, "code": code, "name": quote.get("name", code), "entryPrice": quote.get("price"), "benchmarkEntryPrice": benchmark_price, "score": score, "coverage": coverage, "strategyVersion": STRATEGY_VERSION, "quoteUpdatedAt": quote_data.get("updatedAt"), "decisionRecord": _decision_snapshot(report_date, score, coverage, snapshot_quote, fund), "outcomes": {}, "priceTrail": {}})
            existing.add(record_id)
    for item in recommendations:
        price = item.get("entryPrice")
        if number(price) and price > 0:
            entry_date = item.get("date", "")
            trail = _extend_trail(item.setdefault("priceTrail", {}), history.get(item.get("code"), []), entry_date)
            benchmark_trail = _extend_trail(item.setdefault("benchmarkTrail", {}), benchmark_history, entry_date)
            item["outcomes"] = _settle(item.get("outcomes") or {}, trail, price,
                                       benchmark_trail, item.get("benchmarkEntryPrice"))
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
        for item in state.get("recommendations", []):
            result = item.get("outcomes", {}).get(str(horizon), {})
            if result.get("status") != "complete":
                continue
            net.append(result["netReturnPct"])
            if "excessReturnPct" in result:
                excess.append(result["excessReturnPct"])
        entry = {"settled": len(net)}
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
        lines.append(line + "。")
    if all(not review[str(horizon)]["settled"] for horizon in HORIZONS):
        return "策略追蹤：已開始保存候選紀錄，尚無任何期間結算。"
    return "策略追蹤（各期間分開統計，報酬已扣手續費、證交稅與滑價；未還原股利）：\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="檢視投資候選追蹤狀態")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args()
    state = load_state(args.path)
    print(review_summary(state))
    print(f"累積推薦：{len(state.get('recommendations', []))} 筆")


if __name__ == "__main__":
    main()
