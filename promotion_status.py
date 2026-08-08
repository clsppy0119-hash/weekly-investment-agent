"""Which role the system has currently earned, and what the next one needs.

Moving from "a shortlist you check" to "a shortlist you follow" is a claim about
evidence, not a preference, so it should be decided by a rule written down in
advance rather than by how the last few weeks felt.

Each stage states its requirement, and the report says exactly what is still
missing. Nothing here grants permission to trade; the advice gate remains the
separate hard block it already was.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from strategy_backtest import significance
from strategy_tracker import HORIZONS, load_state

STAGES = ("research_only", "screening_assistant", "assisted_selection", "autonomous_selection")

# Forward outcomes needed before a stage can even be assessed.  The counts are
# set so a conclusive interval is achievable rather than merely hoped for.
REQUIRED_SETTLED = {"assisted_selection": (20, 30), "autonomous_selection": (60, 60)}

LABELS = {
    "research_only": "僅研究：資料或決策紀錄未達門檻，輸出不得視為推薦。",
    "screening_assistant": "篩選助手：可提供附證據的候選清單，最終判斷由人做。",
    "assisted_selection": "輔助選股：清單已證明優於同池平均，可作為決策主要依據。",
    "autonomous_selection": "自主選股：已證明扣成本後長期跑贏 0050。",
}


def settled(state: dict, horizon: int) -> list[float]:
    """Excess over 0050 for every settled outcome at ``horizon``."""
    values = []
    for item in state.get("recommendations", []):
        outcome = item.get("outcomes", {}).get(str(horizon), {})
        if outcome.get("status") == "complete" and "excessReturnPct" in outcome:
            values.append(outcome["excessReturnPct"] / 100)
    return values


def assess(state: dict, advice_gate: dict) -> dict:
    horizons = {}
    for horizon in HORIZONS:
        excess = settled(state, horizon)
        horizons[str(horizon)] = {"settled": len(excess), **significance(excess)}

    reached = "screening_assistant" if state.get("recommendations") else "research_only"
    blockers: dict[str, list[str]] = {}

    for stage in ("assisted_selection", "autonomous_selection"):
        horizon, needed = REQUIRED_SETTLED[stage]
        stats = horizons[str(horizon)]
        missing = []
        if stats["settled"] < needed:
            missing.append(f"{horizon} 日已結算 {stats['settled']}/{needed} 筆")
        elif not stats.get("conclusive"):
            missing.append(f"{horizon} 日超額報酬信賴區間仍橫跨 0")
        elif stats.get("meanExcessPerRebalance", 0) <= 0:
            missing.append(f"{horizon} 日超額報酬顯著為負")
        if stage == "autonomous_selection" and not advice_gate.get("adviceEnabled"):
            missing.append("investment-advice-gate 尚未開啟")
        if missing:
            blockers[stage] = missing
        else:
            reached = stage

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stage": reached,
        "stageLabel": LABELS[reached],
        "recommendations": len(state.get("recommendations", [])),
        "horizons": horizons,
        "blockers": blockers,
        "note": "前瞻結果以 0050 總報酬為基準；追蹤層尚未記錄合格池，"
                "因此無法在此檢驗『優於同池』，該項目目前僅能由回測評估。",
    }


def render(report: dict) -> str:
    lines = [f"目前階段：{report['stageLabel']}", f"累積推薦 {report['recommendations']} 筆", ""]
    for horizon in HORIZONS:
        stats = report["horizons"][str(horizon)]
        if stats["settled"] < 3:
            lines.append(f"　{horizon:>2} 日：已結算 {stats['settled']} 筆，樣本不足。")
            continue
        low, high = (value * 100 for value in stats["ci95"])
        lines.append(
            f"　{horizon:>2} 日：{stats['settled']} 筆，平均超額 "
            f"{stats['meanExcessPerRebalance'] * 100:+.2f}%　95%CI [{low:+.2f}, {high:+.2f}]"
            + ("　顯著" if stats["conclusive"] else "　尚無結論"))
    for stage in ("assisted_selection", "autonomous_selection"):
        if stage in report["blockers"]:
            lines.append("")
            lines.append(f"距離「{LABELS[stage].split('：')[0]}」還差：")
            lines.extend(f"　- {item}" for item in report["blockers"][stage])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="檢視系統目前贏得的角色與下一階的條件")
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--advice-gate", type=Path, default=Path("data/investment-advice-gate.json"))
    parser.add_argument("--output", type=Path, default=Path("data/promotion-status.json"))
    args = parser.parse_args()

    state = load_state(args.path) if args.path else load_state()
    try:
        gate = json.loads(args.advice_gate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        gate = {}
    report = assess(state, gate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
