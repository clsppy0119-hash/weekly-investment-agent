"""Run bounded AI reviews for candidates selected by deterministic reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from agent import load_market_data, research


ROOT = Path(__file__).resolve().parent.parent
TRACKER = ROOT / "strategy_data" / "recommendations.json"
ACTIONS = ROOT / "backtest_data" / "candidate_actions.json"


def _candidate_codes(mode: str, report_path: Path, limit: int) -> list[str]:
    market = load_market_data().get("quotes", {})
    codes: list[str] = []

    if mode in ("daily", "comprehensive") and TRACKER.exists():
        state = json.loads(TRACKER.read_text(encoding="utf-8"))
        rows = [row for row in state.get("recommendations", []) if row.get("mode") == mode]
        if rows:
            newest = max(str(row.get("date", "")) for row in rows)
            rows = sorted(
                (row for row in rows if str(row.get("date", "")) == newest),
                key=lambda row: int(row.get("rank", 999)),
            )
            codes.extend(str(row.get("code", "")).strip() for row in rows)

    if report_path.exists():
        report = report_path.read_text(encoding="utf-8")
        codes.extend(re.findall(r"(?<!\d)(\d{4})(?!\d)", report))

    unique: list[str] = []
    for code in codes:
        if code in market and code not in unique:
            unique.append(code)
        if len(unique) >= limit:
            break
    return unique


def _corporate_action_summary(codes: list[str]) -> str:
    """Describe only the verified candidate-pool ledger generated for this run."""
    if not ACTIONS.exists():
        return "除權息資料：本次尚未建立候選池資料檔，請勿將報告視為含息總報酬判斷。"
    try:
        payload = json.loads(ACTIONS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "除權息資料：候選池資料檔無法讀取，請勿將報告視為含息總報酬判斷。"

    queried = payload.get("queried_codes", [])
    succeeded = payload.get("successful_codes", 0)
    events = payload.get("events", [])
    selected_events = [item for item in events if str(item.get("code")) in codes]
    period = payload.get("period", {})
    return (
        "除權息資料：FinMind 候選池逐檔查詢成功 "
        f"{succeeded}/{len(queried)} 檔；本次研究股票可驗證事件 {len(selected_events)} 筆"
        f"（查詢區間 {period.get('start', '未知')}～{period.get('end', '未知')}）。"
        "此為候選池覆蓋率，不代表全市場或完整含息回測。"
    )


async def _build(mode: str, report_path: Path, limit: int) -> str:
    codes = _candidate_codes(mode, report_path, limit)
    title = {"daily": "AI 每日候選審查", "long": "AI 長線深入研究", "comprehensive": "AI 綜合投資審查"}[mode]
    if not codes:
        return f"【{title}】\n本次沒有可驗證的候選股票代碼。"

    sections = [
        f"【{title}】",
        "AI 僅審查規則式篩選結果，不會下單或修改投資策略。",
        _corporate_action_summary(codes),
    ]
    for code in codes:
        try:
            answer = await research(code)
            sections.extend(["", answer.strip()])
        except Exception:
            sections.extend(["", f"{code}：AI 研究暫時無法使用；原始規則式報告仍有效。"])
    return "\n".join(sections).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "long", "comprehensive"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        args.output.write_text("【AI 研究】\n尚未設定 OPENAI_API_KEY，已保留原始報告。\n", encoding="utf-8")
        return 2
    text = asyncio.run(_build(args.mode, args.report, max(1, min(args.limit, 3))))
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
