import argparse
import json
from pathlib import Path


DEFAULT_PATH = Path("strategy_data/recommendations.json")
HORIZONS = (5, 20, 60)


def load_state(path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return {"schemaVersion": 1, "recommendations": []}
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def save_state(state, path=DEFAULT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(state, output, ensure_ascii=False, indent=2)
        output.write("\n")


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


def record_recommendations(report_date, report_mode, ranked, quote_data, path=DEFAULT_PATH):
    state = load_state(path)
    recommendations = state.setdefault("recommendations", [])
    existing = {item.get("id") for item in recommendations}
    history = quote_data.get("history", {})
    for style, items in ranked.items():
        for rank, item in enumerate(items, 1):
            score, coverage, code, quote, _fund = item
            record_id = f"{report_date}:{report_mode}:{style}:{code}"
            if record_id in existing:
                continue
            recommendations.append({"id": record_id, "date": report_date, "mode": report_mode, "style": style, "rank": rank, "code": code, "name": quote.get("name", code), "entryPrice": quote.get("price"), "score": score, "coverage": coverage, "strategyVersion": "1.0", "quoteUpdatedAt": quote_data.get("updatedAt"), "outcomes": {}})
            existing.add(record_id)
    for item in recommendations:
        price = item.get("entryPrice")
        if isinstance(price, (int, float)) and price > 0:
            item["outcomes"] = _outcomes(history.get(item.get("code"), []), item.get("date", ""), price)
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
