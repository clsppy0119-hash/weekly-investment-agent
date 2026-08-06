import argparse
import json
import os
from pathlib import Path


DEFAULT_PATH = Path("strategy_data/recommendations.json")
HORIZONS = (5, 20, 60)
STRATEGY_VERSION = "2.0"


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


def _outcomes(history, entry_date, entry_price):
    rows = [row for row in history if row.get("date", "") > entry_date and isinstance(row.get("close"), (int, float))]
    result = {}
    for horizon in HORIZONS:
        if len(rows) >= horizon:
            exit_row = rows[horizon - 1]
            result[str(horizon)] = {"status": "complete", "date": exit_row["date"], "price": exit_row["close"], "returnPct": round((exit_row["close"] / entry_price - 1) * 100, 2)}
        else:
            result[str(horizon)] = {"status": "pending", "observations": len(rows)}
    return result


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
    for style, items in ranked.items():
        for rank, item in enumerate(items, 1):
            score, coverage, code, quote, fund = item
            record_id = f"{report_date}:{report_mode}:{style}:{code}"
            if record_id in existing:
                continue
            snapshot_quote = {**quote, "updatedAt": quote_data.get("updatedAt")}
            recommendations.append({"id": record_id, "date": report_date, "mode": report_mode, "style": style, "rank": rank, "code": code, "name": quote.get("name", code), "entryPrice": quote.get("price"), "score": score, "coverage": coverage, "strategyVersion": STRATEGY_VERSION, "quoteUpdatedAt": quote_data.get("updatedAt"), "decisionRecord": _decision_snapshot(report_date, score, coverage, snapshot_quote, fund), "outcomes": {}})
            existing.add(record_id)
    for item in recommendations:
        price = item.get("entryPrice")
        if isinstance(price, (int, float)) and price > 0:
            item["outcomes"] = _outcomes(history.get(item.get("code"), []), item.get("date", ""), price)
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


def review_summary(state):
    completed = []
    for item in state.get("recommendations", []):
        for result in item.get("outcomes", {}).values():
            if result.get("status") == "complete":
                completed.append(result["returnPct"])
    if not completed:
        return "策略追蹤：已開始保存候選紀錄；累積至少 5 個後續交易日後產生首輪績效檢討。"
    positive = sum(1 for value in completed if value > 0)
    average = sum(completed) / len(completed)
    return f"策略追蹤：已完成 {len(completed)} 筆區間檢核，正報酬率 {positive / len(completed) * 100:.0f}%，平均報酬 {average:+.2f}%。"


def main():
    parser = argparse.ArgumentParser(description="檢視投資候選追蹤狀態")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args()
    state = load_state(args.path)
    print(review_summary(state))
    print(f"累積推薦：{len(state.get('recommendations', []))} 筆")


if __name__ == "__main__":
    main()
