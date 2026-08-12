import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from actual_comprehensive_selection import rank_pool
from candidate_manifest import atomic_write_json, build_manifest, load_json
# The ranking rule lives in `scoring` so the backtest can score candidates with
# the identical code path; see that module for why.
from scoring import DEFAULT_PICKS, candidates, number, score_metric, stock_score  # noqa: F401
from strategy_tracker import record_recommendations, review_summary


TZ_TAIPEI = timezone(timedelta(hours=8))
ADVICE_GATE_PATH = Path(os.environ.get("ADVICE_GATE_PATH", "data/investment-advice-gate.json"))
MANIFEST_PATH = Path(os.environ.get("CANDIDATE_MANIFEST", "data/candidate-manifest.json"))
ACTIONS_PATH = Path(os.environ.get("CANDIDATE_ACTIONS", "backtest_data/candidate_actions.json"))
NEWS_PATH = Path(os.environ.get("MARKET_NEWS", "market-news.json"))
PIT_STATUS_PATH = Path(os.environ.get("PIT_STATUS_PATH", "data/point-in-time-universe-status.json"))
CONTRACT_PATH = Path(os.environ.get("DATA_CONTRACT_PATH", "data/evidence-contract.json"))
DATA_ACCESS_STATUS_PATH = Path(os.environ.get("DATA_ACCESS_STATUS", "data/data-access-status.json"))


def advice_enabled():
    """Only a verified backtest may turn research candidates into advice."""
    try:
        gate = json.loads(ADVICE_GATE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    return gate.get("status") == "advice_candidate" and gate.get("adviceEnabled") is True


def candidate_line(item):
    score, coverage, code, quote, fund = item
    reasons = []
    if number(fund.get("revenueYoY")):
        reasons.append(f"營收年增 {fund['revenueYoY']:+.1f}%")
    if number(fund.get("dividendYield")):
        reasons.append(f"殖利率 {fund['dividendYield']:.1f}%")
    if number(quote.get("change")):
        reasons.append(f"當日 {quote['change']:+.2f}")
    detail = "、".join(reasons[:2]) or "可用資料有限"
    return f"• {quote.get('name', code)}（{code}）｜{quote['price']:.2f}｜評分 {score}｜{detail}"


with open("quotes.json", encoding="utf-8") as source:
    data = json.load(source)

quotes = data.get("quotes", {})
fundamentals = data.get("fundamentals", {})
valid = [(code, row) for code, row in quotes.items() if number(row.get("price"))]
volume = sorted(valid, key=lambda item: item[1].get("volume", 0), reverse=True)[:3]
today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")


def quote_line(item):
    code, row = item
    return f"• {row.get('name', code)}（{code}）｜{row['price']:.2f}｜{row.get('change', 0):+.2f}"


def market_news_lines():
    path = "market-news.json"
    try:
        with open(path, encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError):
        return ["• 本次未取得可驗證消息來源；不以消息面產生投資結論。"]
    rows = payload.get("items", [])
    if not rows:
        return ["• 本次未取得可驗證消息來源；不以消息面產生投資結論。"]
    return [
        f"• 【{row.get('topic', '市場消息')}】{row.get('title', '無標題')}｜{row.get('publisher', '未知來源')}\n  {row.get('link', '')}"
        for row in rows[:6]
    ]


report_mode = os.environ.get("REPORT_MODE", "comprehensive")
report_phase = os.environ.get("REPORT_PHASE", "final").strip().lower()
if report_phase not in {"preview", "final"}:
    raise SystemExit("REPORT_PHASE must be preview or final")
strategy_advice_enabled = advice_enabled()
styles = [("comprehensive", "綜合投資研究")]
report_title = "綜合投資研究日報"
sections = []
ranked_by_style = {}
# The whole eligible pool, ranked once; the head is what gets recommended and
# the rest is what the recommendation has to be judged against. Beating 0050
# also rewards whatever the pool's size tilt did; beating the pool does not.
eligible_pool_by_style = {
    key: rank_pool(quotes, fundamentals)
    for key, _title in styles
}
preview_ranked_by_style = {
    key: items[:DEFAULT_PICKS] for key, items in eligible_pool_by_style.items()
}
advice_gate = load_json(ADVICE_GATE_PATH)
actions = load_json(ACTIONS_PATH)
manifest = build_manifest(
    report_date=today,
    report_mode=report_mode,
    phase=report_phase,
    ranked=preview_ranked_by_style,
    quote_data=data,
    advice_gate=advice_gate,
    actions=actions,
    news_path=NEWS_PATH,
    actions_path=ACTIONS_PATH,
    gate_path=ADVICE_GATE_PATH,
    pit_path=PIT_STATUS_PATH,
)
atomic_write_json(MANIFEST_PATH, manifest)
atomic_write_json(CONTRACT_PATH, manifest["dataContract"])
eligible_keys = {
    (str(item.get("style", "")), str(item.get("code", "")))
    for item in manifest.get("eligibleCandidates", [])
}
for key, title in styles:
    items = [
        item for item in preview_ranked_by_style[key]
        if (key, str(item[2])) in eligible_keys
    ]
    ranked_by_style[key] = items
    if not strategy_advice_enabled:
        lines = ["\u7b56\u7565\u5c1a\u672a\u901a\u904e\u6a23\u672c\u5916\u9a57\u8b49\uff1b\u76ee\u524d\u50c5\u63d0\u4f9b\u7814\u7a76\u8cc7\u6599\uff0c\u4e0d\u63d0\u4f9b\u8cb7\u9032\u3001\u8ce3\u51fa\u6216\u52a0\u78bc\u5efa\u8b70\u3002"]
    lines = [candidate_line(item) for item in items] or ["• 暫無資料完整度足夠的候選，請待資料更新後再檢視。"]
    sections.extend(["", f"【{title}｜優先研究候選】", *lines])

if not strategy_advice_enabled:
    sections.extend(["", "\u7b56\u7565\u5c1a\u672a\u901a\u904e\u6a23\u672c\u5916\u9a57\u8b49\uff1b\u76ee\u524d\u50c5\u63d0\u4f9b\u7814\u7a76\u8cc7\u6599\uff0c\u4e0d\u63d0\u4f9b\u8cb7\u9032\u3001\u8ce3\u51fa\u6216\u52a0\u78bc\u5efa\u8b70\u3002"])

# A provider outage no longer stops the report, so it has to say so. Silence
# would read as "nothing qualified today" when the truth is "nothing was
# checked today".
data_access = load_json(DATA_ACCESS_STATUS_PATH)
if data_access and data_access.get("ready") is not True:
    unavailable = "\u3001".join(sorted(data_access.get("errors", {}))) or "\u8cc7\u6599\u4f9b\u61c9\u5546"
    sections.extend(["", f"\u26a0 \u672c\u6b21\u7121\u6cd5\u53d6\u5f97 {unavailable} \u6388\u6b0a\u8cc7\u6599\uff0c\u57fa\u672c\u9762\u8207\u9664\u6b0a\u606f\u672a\u66f4\u65b0\uff1b"
                         "\u5019\u9078\u6e05\u55ae\u50c5\u53cd\u6620\u53ef\u53d6\u5f97\u7684\u8cc7\u6599\uff0c\u7f3a\u6f0f\u4e0d\u4ee3\u8868\u8a72\u80a1\u4e0d\u7b26\u5408\u689d\u4ef6\u3002"])

report = "\n".join([
    f"台股{report_title}｜{today}",
    f"行情更新：{data.get('updatedAt', '暫無時間')}",
    "",
    "【今日成交量前段】",
    *[quote_line(item) for item in volume],
    *sections,
    "",
    "【市場消息與風險提示】",
    *market_news_lines(),
    "",
    "消息僅供追蹤與研究，需閱讀原始來源確認脈絡；不構成買賣建議或報酬保證。",
])

if report_phase == "final":
    tracking_state = record_recommendations(today, report_mode, ranked_by_style, data,
                                            pools=eligible_pool_by_style, actions=actions)
    report += "\n\n" + review_summary(tracking_state)

report_output = Path(os.environ.get("REPORT_OUTPUT", "daily-report.txt"))
report_output.parent.mkdir(parents=True, exist_ok=True)
temporary_report = report_output.with_name(f".{report_output.name}.{os.getpid()}.tmp")
try:
    temporary_report.write_text(report, encoding="utf-8")
    os.replace(temporary_report, report_output)
finally:
    temporary_report.unlink(missing_ok=True)
