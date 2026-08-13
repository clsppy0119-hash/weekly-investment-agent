"""依事前固定規則判斷投資 Agent 目前獲得哪一種角色。

升級是一項證據主張，而不是偏好。這裡只整理前瞻決策結果與缺口；
它不會開啟交易權限，正式建議仍由獨立的 investment advice gate 控制。
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from strategy_backtest import significance
from strategy_tracker import HORIZONS, load_state

STAGES = ("research_only", "screening_assistant", "assisted_selection", "autonomous_selection")
REQUIRED_SETTLED = {"assisted_selection": (20, 30), "autonomous_selection": (60, 60)}
PROMOTION_EVIDENCE_POLICY_VERSION = "legacy-evidence-quarantine-v1"
MAX_RECOMMENDATIONS = 10_000

LABELS = {
    "research_only": "僅研究：資料或決策紀錄未達門檻，輸出不得視為推薦。",
    "screening_assistant": "篩選助手：可產生研究清單，但仍需由使用者自行查證。",
    "assisted_selection": "輔助選股：前瞻驗證達標，可作為人工決策參考，但不可自動交易。",
    "autonomous_selection": "自主選股：已證明扣除成本後，長期跑贏 0050。",
}


def settled(state: dict, horizon: int, field: str = "excessReturnPct") -> tuple[list[float], int]:
    """依決策日彙總，並同時回傳原始股票結果筆數。"""
    by_date: dict[str, list[float]] = {}
    outcomes = 0
    recommendations = state.get("recommendations", []) if isinstance(state, dict) else []
    if not isinstance(recommendations, list):
        return [], 0
    for item in recommendations:
        if not isinstance(item, dict) or not isinstance(item.get("outcomes", {}), dict):
            continue
        outcome = item.get("outcomes", {}).get(str(horizon), {})
        value = outcome.get(field) if isinstance(outcome, dict) else None
        if (
            outcome.get("status") == "complete"
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            by_date.setdefault(str(item.get("date", "")), []).append(value / 100)
            outcomes += 1
    return [sum(values) / len(values) for values in by_date.values()], outcomes


def _assess(state: dict, advice_gate: dict) -> dict:
    state = state if isinstance(state, dict) else {}
    advice_gate = advice_gate if isinstance(advice_gate, dict) else {}
    horizons = {}
    pools = {}
    for horizon in HORIZONS:
        for store, field in ((horizons, "excessReturnPct"), (pools, "poolExcessPct")):
            values, outcomes = settled(state, horizon, field)
            store[str(horizon)] = {
                "settled": len(values), "outcomes": outcomes, **significance(values)
            }

    # schemaVersion 1 is a mutable diagnostic tracker.  Its outcomes predate
    # the authoritative PIT, Node55 accounting, and preregistered risk
    # contracts, so no numeric value in it is promotion evidence.
    reached = "research_only"
    blockers: dict[str, list[str]] = {}
    for stage in ("assisted_selection", "autonomous_selection"):
        horizon, needed = REQUIRED_SETTLED[stage]
        missing = []
        for stats, against in ((horizons[str(horizon)], "對 0050"),
                               (pools[str(horizon)], "對合格池")):
            if stats["settled"] < needed:
                missing.append(
                    f"{horizon} 日{against}僅有 {stats['settled']}/{needed} 個獨立決策日"
                )
            elif not stats.get("conclusive"):
                missing.append(f"{horizon} 日{against}的 95% 信賴區間仍包含 0")
            elif stats.get("meanExcessPerRebalance", 0) <= 0:
                missing.append(f"{horizon} 日{against}平均超額報酬不為正")
        if stage == "autonomous_selection" and not advice_gate.get("adviceEnabled"):
            missing.append("investment-advice-gate 尚未開啟")
        missing.extend([
            "legacy_tracker_outcomes_quarantined",
            "actual_forward_outcome_contract_not_available",
        ])
        blockers[stage] = list(dict.fromkeys(missing))

    return {
        "schemaVersion": 2,
        "promotionEvidencePolicyVersion": PROMOTION_EVIDENCE_POLICY_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stage": reached,
        "stageLabel": LABELS[reached],
        "recommendations": len(state.get("recommendations", [])),
        "horizons": horizons,
        "versusEligiblePool": pools,
        "promotionEvidenceAccepted": 0,
        "legacyOutcomesExcluded": sum(
            isinstance(item.get("outcomes"), dict)
            and isinstance(item["outcomes"].get(str(horizon)), dict)
            and item["outcomes"][str(horizon)].get("status") == "complete"
            for item in state.get("recommendations", []) if isinstance(item, dict)
            for horizon in HORIZONS
        ) if isinstance(state.get("recommendations", []), list) else 0,
        "formalEvidenceEligible": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "blockers": blockers,
        "note": "每一階都要同時跑贏 0050 與合格池。跑贏 0050 決定這套系統是否值得使用；"
                "跑贏合格池則確認成果來自選股能力，而非整個候選池剛好上漲。"
                "同一天的多檔股票只算一個獨立決策日，避免高估樣本數。",
    }


def assess(state: dict, advice_gate: dict) -> dict:
    """Fail-closed public boundary for mutable legacy tracker state."""
    try:
        if type(state) is not dict or type(advice_gate) is not dict:
            return _assess({}, {})
        recommendations = state.get("recommendations", [])
        if type(recommendations) is not list or len(recommendations) > MAX_RECOMMENDATIONS:
            return _assess({}, {})
        if any(type(item) is not dict or len(item) > 32 for item in recommendations):
            return _assess({}, {})
        return _assess(state, advice_gate)
    except Exception:
        return _assess({}, {})


def render(report: dict) -> str:
    lines = [f"目前階段：{report['stageLabel']}",
             f"已記錄候選：{report['recommendations']} 檔", ""]
    for label, key in (("對 0050", "horizons"), ("對合格池", "versusEligiblePool")):
        lines.append(f"【{label}】")
        for horizon in HORIZONS:
            stats = report[key][str(horizon)]
            if stats["settled"] < 3:
                lines.append(
                    f"  {horizon:>2} 日：{stats['settled']} 個獨立決策日"
                    f"（{stats['outcomes']} 筆股票結果），樣本不足"
                )
                continue
            low, high = (value * 100 for value in stats["ci95"])
            lines.append(
                f"  {horizon:>2} 日：{stats['settled']} 個獨立決策日"
                f"（{stats['outcomes']} 筆股票結果），平均超額 "
                f"{stats['meanExcessPerRebalance'] * 100:+.2f}%，"
                f"95% CI [{low:+.2f}%, {high:+.2f}%]"
                + ("，結果具統計顯著性" if stats["conclusive"] else "，尚未具統計顯著性")
            )
        lines.append("")
    for stage in ("assisted_selection", "autonomous_selection"):
        if stage in report["blockers"]:
            lines.append(f"升級到「{LABELS[stage].split('：')[0]}」仍缺：")
            lines.extend(f"  - {item}" for item in report["blockers"][stage])
            lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    parser = argparse.ArgumentParser(description="輸出投資 Agent 的前瞻驗證階段與缺口")
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
