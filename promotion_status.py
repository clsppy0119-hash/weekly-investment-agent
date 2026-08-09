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


def settled(state: dict, horizon: int, field: str = "excessReturnPct") -> tuple[list[float], int]:
    """One observation per decision date, plus the raw outcome count.

    Three picks made on the same day are one decision held three ways, not
    three independent draws: they share the day's market move, so counting them
    separately narrows the interval as if each were new evidence. Averaging
    within a date removes that. Holds started on consecutive days still overlap,
    which the interval cannot see, so a short run of dates stays weak evidence
    however tight it looks.
    """
    by_date: dict[str, list[float]] = {}
    outcomes = 0
    for item in state.get("recommendations", []):
        outcome = item.get("outcomes", {}).get(str(horizon), {})
        if outcome.get("status") == "complete" and field in outcome:
            by_date.setdefault(item.get("date", ""), []).append(outcome[field] / 100)
            outcomes += 1
    return [sum(values) / len(values) for values in by_date.values()], outcomes


def assess(state: dict, advice_gate: dict) -> dict:
    horizons = {}
    pools = {}
    for horizon in HORIZONS:
        for store, field in ((horizons, "excessReturnPct"), (pools, "poolExcessPct")):
            values, outcomes = settled(state, horizon, field)
            # `settled` counts decision dates, which is what the thresholds and
            # the interval are both stated in.
            store[str(horizon)] = {"settled": len(values), "outcomes": outcomes,
                                   **significance(values)}

    reached = "screening_assistant" if state.get("recommendations") else "research_only"
    blockers: dict[str, list[str]] = {}

    for stage in ("assisted_selection", "autonomous_selection"):
        horizon, needed = REQUIRED_SETTLED[stage]
        # A shortlist earns trust by beating the pool it was drawn from -- that
        # is the claim it makes. Replacing stock picking is a different claim:
        # it has to beat the passive alternative the user would otherwise hold.
        stats = pools[str(horizon)] if stage == "assisted_selection" else horizons[str(horizon)]
        against = "對合格池" if stage == "assisted_selection" else "對 0050 "
        missing = []
        if stats["settled"] < needed:
            missing.append(f"{horizon} 日{against}已結算 {stats['settled']}/{needed} 個決策日")
        elif not stats.get("conclusive"):
            missing.append(f"{horizon} 日{against}超額報酬信賴區間仍橫跨 0")
        elif stats.get("meanExcessPerRebalance", 0) <= 0:
            missing.append(f"{horizon} 日{against}超額報酬顯著為負")
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
        "versusEligiblePool": pools,
        "blockers": blockers,
        "note": "輔助選股以『對合格池超額』判定，因為那才是清單本身的主張；"
                "自主選股以『對 0050 超額』判定，因為那是使用者原本就能持有的替代方案。",
    }


def render(report: dict) -> str:
    lines = [f"目前階段：{report['stageLabel']}", f"累積推薦 {report['recommendations']} 筆", ""]
    for label, key in (("對 0050", "horizons"), ("對合格池", "versusEligiblePool")):
        lines.append(f"【{label}】")
        for horizon in HORIZONS:
            stats = report[key][str(horizon)]
            if stats["settled"] < 3:
                lines.append(f"　{horizon:>2} 日：{stats['settled']} 個決策日"
                             f"（{stats['outcomes']} 筆結果），樣本不足。")
                continue
            low, high = (value * 100 for value in stats["ci95"])
            lines.append(
                f"　{horizon:>2} 日：{stats['settled']} 個決策日（{stats['outcomes']} 筆結果），平均超額 "
                f"{stats['meanExcessPerRebalance'] * 100:+.2f}%　95%CI [{low:+.2f}, {high:+.2f}]"
                + ("　顯著" if stats["conclusive"] else "　尚無結論"))
        lines.append("")
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
