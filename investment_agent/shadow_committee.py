"""Deterministic, read-only investment committee shadow mode.

This module is intentionally isolated from production research and notifications.
It uses a shared evidence packet once and returns compact JSON-like dictionaries.
"""

from __future__ import annotations

from typing import Any

REQUIRED_BEAR_CHALLENGES = (
    "valuation",
    "demand_earnings",
    "geopolitical_supply_chain",
)


def _evidence_ids(packet: dict[str, Any], categories: set[str] | None = None) -> list[str]:
    rows = packet.get("evidence", [])
    result: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if categories and row.get("category") not in categories:
            continue
        evidence_id = row.get("id")
        if evidence_id and evidence_id not in result:
            result.append(str(evidence_id))
    return result


def run_shadow_committee(packet: dict[str, Any], *, enabled: bool = False) -> dict[str, Any]:
    """Return a shadow-only committee result; disabled mode has no side effects."""
    if not enabled:
        return {"status": "disabled", "shadow_only": True, "production_unchanged": True}

    quality = packet.get("quality", {}) if isinstance(packet.get("quality", {}), dict) else {}
    quality_passed = bool(quality.get("passed"))
    positive_ids = _evidence_ids(packet, {"growth", "profitability", "valuation_support"})
    bull = {
        "role": "bull",
        "thesis": "positive evidence supports further research" if positive_ids else "insufficient positive evidence",
        "evidence_ids": positive_ids[:4],
        "confidence": 0.6 if positive_ids else 0.2,
    }

    evidence_categories = set()
    for row in packet.get("evidence", []) if isinstance(packet.get("evidence", []), list) else []:
        if isinstance(row, dict) and row.get("category"):
            evidence_categories.add(str(row["category"]))
    missing_challenges = [category for category in REQUIRED_BEAR_CHALLENGES if category not in evidence_categories]
    bear = {
        "role": "bear",
        "challenges": {category: category not in missing_challenges for category in REQUIRED_BEAR_CHALLENGES},
        "evidence_ids": _evidence_ids(packet, set(REQUIRED_BEAR_CHALLENGES))[:6],
        "falsification_triggers": packet.get("falsification_triggers", [])[:3],
        "confidence": 0.7 if not missing_challenges else 0.2,
        "independent": not missing_challenges,
    }
    risk = {
        "role": "risk",
        "issues": ([] if quality_passed else ["data_quality_gate_failed"]),
        "evidence_ids": _evidence_ids(packet, {"risk", "data_quality"})[:4],
        "confidence": 0.7 if quality_passed else 0.9,
    }
    bear_independent = bool(bear["independent"] and bear["evidence_ids"])
    positive = bool(quality_passed and positive_ids and bear_independent)
    judge = {
        "role": "judge",
        "verdict": "buy_candidate" if positive else "observe",
        "confidence": 0.65 if positive else 0.25,
        "quality_gate_passed": quality_passed,
        "bear_independence_passed": bear_independent,
        "shadow_only": True,
    }
    return {"status": "ok", "shadow_only": True, "production_unchanged": True, "bull": bull, "bear": bear, "risk": risk, "judge": judge}
