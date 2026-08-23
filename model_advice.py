"""Render a deterministic, tracked model recommendation from the paper ledger.

The report deliberately reads only a fully verified ``signal_decision``.  It
does not rerank today's market snapshot, inspect paper returns, call an LLM, or
open the formal advice/trading gates.  That keeps the recommendation shown to
the user identical to the cohort whose future observations are being tracked.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import paper_trading


SCHEMA_VERSION = 1
REPORT_VERSION = "tracked-fixed-model-advice-v1"
MAX_REPORT_BYTES = 256 * 1024
SIMPLE_DEBT_WARNING_PCT = 50.0
SUPPORT_FIELDS = (
    ("revenueYoY", "營收年增率", "%"),
    ("eps", "EPS", ""),
    ("roe", "ROE", "%"),
    ("debtRatio", "負債比", "%"),
    ("financialHistoryYears", "財務歷史", " 年"),
)
LIMITATIONS = (
    "前向紙上樣本仍在收集，尚未證明策略能產生超額報酬",
    "捕捉日尚未與外部認證的官方交易日曆完成連續性核對",
    "訊號收盤價不是即時成交價；紙上進場基準是下一個捕捉日收盤",
    "本報告不提供下單、停損價、目標價或報酬保證",
)


class ModelAdviceError(ValueError):
    """A fail-closed report input or output error."""


def _path(value: Path | str) -> Path:
    if isinstance(value, Path):
        return value
    if type(value) is str and value:
        return Path(value)
    raise ModelAdviceError("path_invalid")


def _fact_snapshot(slot: dict[str, Any]) -> dict[str, float | None]:
    fundamentals = slot["fundamentals"]
    return {name: fundamentals[name] for name, _, _ in SUPPORT_FIELDS}


def _risk_flags(facts: dict[str, float | None]) -> list[str]:
    flags = []
    if facts["revenueYoY"] is not None and facts["revenueYoY"] < 0:
        flags.append("營收年增率為負")
    if facts["eps"] is not None and facts["eps"] <= 0:
        flags.append("EPS 非正")
    if facts["roe"] is not None and facts["roe"] <= 0:
        flags.append("ROE 非正")
    if facts["debtRatio"] is not None and facts["debtRatio"] >= SIMPLE_DEBT_WARNING_PCT:
        flags.append(f"負債比達 {_number(SIMPLE_DEBT_WARNING_PCT)}% 以上")
    return flags


def build_snapshot(ledger_path: Path | str) -> dict[str, Any]:
    """Return the latest immutable recommendation snapshot, if one exists."""
    try:
        ledger = paper_trading.load_ledger(_path(ledger_path))
    except (paper_trading.PaperTradingError, OSError, TypeError, ValueError) as error:
        raise ModelAdviceError("paper_ledger_invalid") from error

    decisions = [
        event for event in ledger["events"]
        if event["eventType"] == "signal_decision"
    ]
    observations = [
        event for event in ledger["events"]
        if event["eventType"] == "session_observation"
    ]
    base: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "reportVersion": REPORT_VERSION,
        "evidenceTier": "unvalidated" if not decisions else "collecting",
        "ledgerVerified": True,
        "ledgerHeadHash": ledger["headHash"],
        "modelRecommendationAvailable": bool(decisions),
        "formalEvidenceSupported": False,
        "paperOutcomeUsedForTier": False,
        "tradingInstruction": False,
        "limitations": list(LIMITATIONS),
    }
    if not decisions:
        return {
            **base,
            "status": "not_started",
            "decisionEventHash": None,
            "decisionKey": None,
            "decisionAt": None,
            "signalSession": None,
            "latestObservationSession": None,
            "sourceAvailableAt": None,
            "entryConvention": None,
            "holdingIntervalCapturedSessions": paper_trading.SIGNAL_SPACING,
            "capturedSessionsSinceSignal": 0,
            "targetSlots": paper_trading.TARGET_SLOTS,
            "slotAllocationPct": round(100 / paper_trading.TARGET_SLOTS, 6),
            "recommendations": [],
            "cashSlots": paper_trading.TARGET_SLOTS,
            "cashReasons": ["尚無已封存的模型訊號"],
        }

    latest = decisions[-1]
    payload = latest["payload"]
    material = payload["material"]
    signal = material["signalSession"]
    latest_observation = observations[-1]["payload"]["sessionDate"]
    captured_since_signal = sum(
        event["payload"]["sessionDate"] > signal for event in observations
    )
    recommendations = []
    cash_reasons = []
    for slot in material["topSlots"]:
        if slot["code"] is not None and slot["manifestQualityPassed"] is True:
            facts = _fact_snapshot(slot)
            recommendations.append({
                "slot": slot["slot"],
                "code": slot["code"],
                "name": slot["name"],
                "rank": slot["rank"],
                "score": slot["score"],
                "coverage": slot["coverage"],
                "signalClose": slot["signalClose"],
                "signalFacts": facts,
                "riskFlags": _risk_flags(facts),
                "modelSleeveAllocationPct": round(100 / material["targetSlots"], 6),
                "invalidation": "下一個已封存訊號未再列入品質合格前三名",
            })
        else:
            blockers = slot["qualityBlockers"] or ["no_candidate_for_slot"]
            label = slot["code"] or f"slot_{slot['slot']}"
            cash_reasons.append(f"{label}:" + ",".join(blockers))

    return {
        **base,
        "status": "new_signal" if latest_observation == signal else "maintain_signal",
        "decisionEventHash": latest["eventHash"],
        "decisionKey": payload["decisionKey"],
        "decisionAt": payload["decisionAt"],
        "signalSession": signal,
        "latestObservationSession": latest_observation,
        "sourceAvailableAt": material["sourceAvailableAt"],
        "entryConvention": material["entryConvention"],
        "holdingIntervalCapturedSessions": material["signalSpacingCapturedSessions"],
        "capturedSessionsSinceSignal": captured_since_signal,
        "targetSlots": material["targetSlots"],
        "slotAllocationPct": round(100 / material["targetSlots"], 6),
        "recommendations": recommendations,
        "cashSlots": material["targetSlots"] - len(recommendations),
        "cashReasons": cash_reasons,
    }


def _number(value: Any) -> str:
    if type(value) not in (int, float):
        return "缺資料"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_text(snapshot: dict[str, Any]) -> str:
    lines = [
        "【固定規則模型建議】",
        "證據層級：" + (
            "collecting（前向紙上樣本收集中，尚未證明有效）"
            if snapshot["evidenceTier"] == "collecting"
            else "unvalidated（尚無已封存訊號）"
        ),
    ]
    if not snapshot["modelRecommendationAvailable"]:
        lines.extend([
            "目前沒有可追蹤的模型建議；不顯示臨時排行榜，也不以其他股票遞補。",
            "這份模型建議尚未開始；正式績效認證與自動交易未啟用。",
        ])
        return "\n".join(lines) + "\n"

    status = "本期新訊號" if snapshot["status"] == "new_signal" else "維持既有訊號（非新推薦）"
    invested = snapshot["targetSlots"] - snapshot["cashSlots"]
    lines.extend([
        f"狀態：{status}",
        f"訊號日：{snapshot['signalSession']}；最新捕捉日：{snapshot['latestObservationSession']}",
        f"決策識別：{snapshot['decisionKey']}",
        (
            f"固定節奏：每 {snapshot['holdingIntervalCapturedSessions']} 個捕捉日重評；"
            f"本訊號後已捕捉 {snapshot['capturedSessionsSinceSignal']} 日"
        ),
        "紙上進場假設：下一個捕捉日收盤（不是即時成交價）",
        (
            f"本期模型配置：{invested}/{snapshot['targetSlots']} 股票槽位；"
            f"{snapshot['cashSlots']}/{snapshot['targetSlots']} 現金槽位"
        ),
        "",
    ])
    for item in snapshot["recommendations"]:
        facts = item["signalFacts"]
        fact_text = "、".join(
            f"{label} {_number(facts[name])}{suffix}"
            for name, label, suffix in SUPPORT_FIELDS
        )
        lines.extend([
            f"{item['slot']}. {item['code']} {item['name']}",
            (
                f"   模型三槽內權重：{_number(item['modelSleeveAllocationPct'])}%"
                "（非個人總資產配置）｜"
                f"排名 {item['rank']}｜分數 {_number(item['score'])}｜"
                f"資料覆蓋率 {_number(item['coverage'])}%"
            ),
            f"   訊號收盤參考：{_number(item['signalClose'])}",
            f"   訊號時資料（非完整因子歸因）：{fact_text}",
            "   資料警示：" + (
                "、".join(item["riskFlags"])
                if item["riskFlags"]
                else "未觸發簡單財務警示（不代表完整風險評估）"
            ),
            f"   失效條件：{item['invalidation']}",
            "",
        ])
    if snapshot["cashSlots"]:
        lines.append(f"現金槽位原因：{'；'.join(snapshot['cashReasons'])}")
        lines.append("")
    lines.append("限制：")
    lines.extend(f"- {item}" for item in snapshot["limitations"])
    lines.append("這份是固定規則模型建議；正式績效認證與自動交易未啟用。")
    return "\n".join(lines) + "\n"


def write_report(ledger_path: Path | str, output_path: Path | str) -> dict[str, Any]:
    snapshot = build_snapshot(ledger_path)
    encoded = render_text(snapshot).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ModelAdviceError("report_too_large")
    target = _path(output_path)
    if target.exists() and not target.is_file():
        raise ModelAdviceError("output_path_invalid")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as error:
        raise ModelAdviceError("output_write_failed") from error
    finally:
        temporary.unlink(missing_ok=True)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the latest tracked fixed-model recommendation")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = write_report(args.ledger, args.output)
    print(json.dumps({
        "evidenceTier": snapshot["evidenceTier"],
        "recommendations": len(snapshot["recommendations"]),
        "cashSlots": snapshot["cashSlots"],
        "formalEvidenceSupported": False,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
