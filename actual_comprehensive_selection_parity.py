"""Offline executable parity check for the shipped comprehensive selector.

This verifier runs the production selection entry point and the historical
adapter over the same caller-supplied *fixture*.  A positive result proves only
that both paths select identically.  It does not authenticate market evidence,
define execution/risk policy, validate performance, or enable advice.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from actual_comprehensive_selection import POLICY_VERSION, digest, rank_and_assess
from strategy_backtest import build_selection_evidence, select_signal_candidates


SCHEMA_VERSION = 1
PARITY_POLICY_VERSION = "actual-comprehensive-selection-parity-v1"
ROOT_KEYS = frozenset({
    "schemaVersion", "signalDate", "decisionAsOf", "quotes", "fundamentals",
    "actions", "contractBlockers",
})
FORBIDDEN_KEYS = frozenset({
    "return", "returns", "performance", "outcome", "outcomes", "mdd",
    "benchmark", "pnl", "profit", "loss", "recommendation", "advice",
    "strategyValidated", "promotionEligible", "adviceEnabled", "pitCertified",
})
FORBIDDEN_KEYS_NORMALIZED = frozenset(
    key.replace("_", "").lower() for key in FORBIDDEN_KEYS
)
SENSITIVE_KEYS = frozenset({
    "raw", "rows", "body", "headers", "url", "uri", "query", "authorization",
    "cookie", "password", "secret", "token", "accesstoken", "apitoken",
})
SENSITIVE_KEY_PARTS = (
    "raw", "rows", "body", "header", "url", "uri", "query", "authorization",
    "cookie", "password", "secret", "token", "privatekey",
)
SENSITIVE_MARKERS = (
    "://", "bearer ", "authorization:", "cookie:", "token=", "password=", "-----begin ",
)


def _safe_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    return (
        normalized not in FORBIDDEN_KEYS_NORMALIZED
        and normalized not in SENSITIVE_KEYS
        and not any(part in normalized for part in SENSITIVE_KEY_PARTS)
        and not normalized.endswith(("token", "secret", "password", "cookie"))
        and not normalized.endswith((
            "return", "performance", "outcome", "mdd", "pnl", "profit", "loss",
            "recommendation", "advice",
        ))
    )


def _json_domain(value: Any, *, nodes: list[int] | None = None, depth: int = 0) -> bool:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > 20_000 or depth > 20:
        return False
    if value is None or isinstance(value, (str, bool)):
        return not isinstance(value, str) or (
            len(value) <= 2_000
            and not any(marker in value.lower() for marker in SENSITIVE_MARKERS)
            and all(ord(character) >= 32 or character in "\t\n\r" for character in value)
        )
    if isinstance(value, int):
        return not isinstance(value, bool) and abs(value) <= 10**18
    if isinstance(value, float):
        return math.isfinite(value)
    if type(value) is list:
        return len(value) <= 5_000 and all(_json_domain(item, nodes=nodes, depth=depth + 1) for item in value)
    if type(value) is dict:
        return len(value) <= 10_000 and all(
            isinstance(key, str) and len(key) <= 120
            and _safe_key(key)
            and _json_domain(child, nodes=nodes, depth=depth + 1)
            for key, child in value.items()
        )
    return False


def _public_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"poolTuples", "previewTuples", "selectedTuples"}
    }


def _report(*, parity: bool, evidence: bool, blockers: list[str], production: dict | None = None,
            backtest: dict | None = None) -> dict[str, Any]:
    production = production or {}
    backtest = backtest or {}
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": PARITY_POLICY_VERSION,
        "selectionPolicyVersion": POLICY_VERSION,
        "mode": "research_only",
        "selectionParity": parity,
        "selectionEvidenceShapeComplete": evidence,
        "productionDigest": production.get("selectionDigest"),
        "backtestDigest": backtest.get("selectionDigest"),
        "fullPoolCount": len(production.get("fullPool", [])),
        "previewCount": len(production.get("preview", [])),
        "qualityPassedCount": len(production.get("qualityPassedCodes", [])),
        "noBackfill": production.get("noBackfill") is True,
        "cutoffTieDependent": production.get("cutoffTieDependent") is True,
        "pitCertified": False,
        "executionSpecRegistered": False,
        "riskPolicyRegistered": False,
        "eligiblePoolBenchmarkRegistered": False,
        "performanceEvaluated": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "formalGateAttached": False,
        "blockers": sorted(set(blockers)),
    }
    result["reportDigest"] = digest(result)
    return result


def evaluate(payload: Any) -> dict[str, Any]:
    try:
        if not _json_domain(payload) or not isinstance(payload, dict) or set(payload) != ROOT_KEYS:
            return _report(parity=False, evidence=False, blockers=["input_contract_invalid"])
        if payload.get("schemaVersion") != 1 or not isinstance(payload.get("signalDate"), str):
            return _report(parity=False, evidence=False, blockers=["input_contract_invalid"])
        try:
            signal = date.fromisoformat(payload["signalDate"])
            decision = datetime.fromisoformat(str(payload.get("decisionAsOf", "")).replace("Z", "+00:00"))
        except ValueError:
            return _report(parity=False, evidence=False, blockers=["input_contract_invalid"])
        if signal.isoformat() != payload["signalDate"] or decision.tzinfo is None \
                or decision.utcoffset() != timedelta(hours=8) or decision.date() != signal \
                or (decision.hour, decision.minute, decision.second, decision.microsecond) != (14, 0, 0, 0):
            return _report(parity=False, evidence=False, blockers=["input_contract_invalid"])
        quotes = payload.get("quotes")
        fundamentals = payload.get("fundamentals")
        actions = payload.get("actions")
        contract_blockers = payload.get("contractBlockers")
        if not isinstance(quotes, dict) or not isinstance(fundamentals, dict) \
                or not isinstance(actions, dict) or not isinstance(contract_blockers, list) \
                or any(not isinstance(item, str) for item in contract_blockers):
            return _report(parity=False, evidence=False, blockers=["input_contract_invalid"])
        production = rank_and_assess(
            quotes, fundamentals, actions=actions, contract_blockers=contract_blockers
        )
        backtest = select_signal_candidates(
            quotes,
            fundamentals,
            payload["signalDate"],
            {"byDate": {
                payload["signalDate"]: build_selection_evidence(
                    payload["signalDate"], payload["decisionAsOf"], actions, contract_blockers
                )
            }},
        )
        production_public = _public_summary(production)
        backtest_public = _public_summary(backtest)
        parity = production_public == backtest_public
        blockers = [
            "pit_source_not_certified",
            "execution_spec_unregistered",
            "risk_policy_unregistered",
            "eligible_pool_benchmark_unregistered",
        ]
        if not parity:
            blockers.append("production_backtest_selection_mismatch")
        if not production.get("selectionEvidenceSupplied"):
            blockers.append("selection_evidence_missing")
        if production.get("cutoffTieDependent"):
            blockers.append("cutoff_tie_dependent")
        return _report(
            parity=parity,
            evidence=(
                production.get("selectionEvidenceSupplied") is True
                and backtest.get("selectionEvidenceSupplied") is True
            ),
            blockers=blockers,
            production=production_public,
            backtest=backtest_public,
        )
    except Exception:
        return _report(parity=False, evidence=False, blockers=["input_fail_closed"])


def run(payload: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": PARITY_POLICY_VERSION,
            "mode": "disabled",
            "selectionParity": False,
            "strategyValidated": False,
            "promotionEligible": False,
            "adviceEnabled": False,
        }
    return evaluate(payload)
