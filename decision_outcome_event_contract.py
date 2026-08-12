"""Pure E1A contracts for diagnostic decision/outcome event candidates.

These builders do not write a ledger and do not verify a chain.  Every output
is therefore permanently research-only and ineligible for promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-event-candidate-v1"
COST_MODEL_VERSION = "tracker-net-cost-v1"
GENESIS = "0" * 64
HEX = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[0-9]{4}$")
LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
SENSITIVE = re.compile(
    r"(?i)(?:https?://|postgres(?:ql)?://|service[_-]?role|authorization|bearer|"
    r"password|cookie|private[_-]?(?:url|key)|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|eyj[a-z0-9_-]*\.[a-z0-9_-]+\.[a-z0-9_-]+|[;\r\n])"
)

DECISION_KEYS = {
    "decisionAsOf", "code", "mode", "style", "rank", "strategyVersion",
    "score", "coverage", "entryPrice", "candidateManifestHash",
    "evidenceHash", "reportHash", "eligiblePoolHash", "quoteProvenanceHash",
    "fundamentalProvenanceHash", "dataQuality", "costModelVersion",
    "claimedPreviousChainHead", "researchOnly",
}
OUTCOME_KEYS = {
    "decisionEventHash", "horizon", "settledDate", "netReturnPct",
    "totalReturnNetPct", "benchmarkNetReturnPct", "poolNetReturnPct",
    "excessReturnPct", "poolExcessPct", "costModelVersion",
    "corporateActionEvidenceHash", "priceSnapshotHash",
    "benchmarkArtifactHash", "poolArtifactHash", "pitCoverage",
    "sourceStatus", "claimedPreviousOutcomeHash", "researchOnly",
}
LEGACY_KEYS = {"legacySourceHash", "recordCount", "reason", "researchOnly"}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("event_value_not_canonical") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _clone(value: Any) -> Any:
    try:
        return json.loads(_canonical(value).decode("utf-8"))
    except json.JSONDecodeError as error:  # defensive; canonical always emits JSON
        raise ValueError("event_value_not_canonical") from error


def _sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_sensitive(key) or _sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_sensitive(item) for item in value)
    return isinstance(value, str) and SENSITIVE.search(value) is not None


def _exact(payload: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("event_schema_mismatch")
    if _sensitive(payload):
        raise ValueError("event_sensitive_content")
    return _clone(payload)


def _hashes(payload: dict[str, Any], names: tuple[str, ...]) -> None:
    if any(not isinstance(payload[name], str) or HEX.fullmatch(payload[name]) is None
           for name in names):
        raise ValueError("event_hash_invalid")


def _iso_day(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _candidate(event_type: str, logical_key: str,
               payload: dict[str, Any]) -> dict[str, Any]:
    material = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "eventType": event_type,
        "logicalKey": logical_key,
        "diagnosticOnly": True,
        "promotionEligible": False,
        "chainVerified": False,
        "payload": payload,
    }
    return {**material, "eventHash": _digest(material)}


def decision_candidate(payload: Any, *, enabled: bool = False) -> dict[str, Any] | None:
    if not enabled:
        return None
    value = _exact(payload, DECISION_KEYS)
    _hashes(value, (
        "candidateManifestHash", "evidenceHash", "reportHash", "eligiblePoolHash",
        "quoteProvenanceHash", "fundamentalProvenanceHash",
        "claimedPreviousChainHead",
    ))
    if (not _iso_day(value["decisionAsOf"])
            or not isinstance(value["code"], str) or CODE.fullmatch(value["code"]) is None
            or not isinstance(value["mode"], str) or LABEL.fullmatch(value["mode"]) is None
            or not isinstance(value["style"], str) or LABEL.fullmatch(value["style"]) is None
            or type(value["rank"]) is not int or value["rank"] < 1
            or not isinstance(value["strategyVersion"], str)
            or VERSION.fullmatch(value["strategyVersion"]) is None
            or not _finite(value["score"])
            or not _finite(value["coverage"]) or not 0 <= value["coverage"] <= 100
            or not _finite(value["entryPrice"]) or value["entryPrice"] <= 0
            or value["dataQuality"] not in {"qualified", "incomplete", "unknown"}
            or value["costModelVersion"] != COST_MODEL_VERSION
            or value["researchOnly"] is not True):
        raise ValueError("decision_policy_invalid")
    logical_key = ":".join((value["decisionAsOf"], value["mode"],
                            value["style"], value["code"]))
    return _candidate("decision_candidate", logical_key, value)


def outcome_candidate(payload: Any, *, enabled: bool = False) -> dict[str, Any] | None:
    if not enabled:
        return None
    value = _exact(payload, OUTCOME_KEYS)
    _hashes(value, (
        "decisionEventHash", "corporateActionEvidenceHash", "priceSnapshotHash",
        "benchmarkArtifactHash", "poolArtifactHash", "claimedPreviousOutcomeHash",
    ))
    metrics = ("netReturnPct", "totalReturnNetPct", "benchmarkNetReturnPct",
               "poolNetReturnPct", "excessReturnPct", "poolExcessPct")
    if (type(value["horizon"]) is not int or value["horizon"] not in {5, 20, 60}
            or not _iso_day(value["settledDate"])
            or any(not _finite(value[name]) for name in metrics)
            or value["costModelVersion"] != COST_MODEL_VERSION
            or type(value["pitCoverage"]) is not int or value["pitCoverage"] != 100
            or value["sourceStatus"] != "qualified"
            or value["researchOnly"] is not True):
        raise ValueError("outcome_provenance_incomplete")
    if (not math.isclose(value["excessReturnPct"],
                         value["totalReturnNetPct"] - value["benchmarkNetReturnPct"],
                         abs_tol=0.011)
            or not math.isclose(value["poolExcessPct"],
                                value["netReturnPct"] - value["poolNetReturnPct"],
                                abs_tol=0.011)):
        raise ValueError("outcome_arithmetic_invalid")
    logical_key = f"outcome:{value['decisionEventHash']}:{value['horizon']}"
    return _candidate("outcome_candidate", logical_key, value)


def legacy_candidate(payload: Any, *, enabled: bool = False) -> dict[str, Any] | None:
    if not enabled:
        return None
    value = _exact(payload, LEGACY_KEYS)
    _hashes(value, ("legacySourceHash",))
    if (type(value["recordCount"]) is not int or value["recordCount"] < 1
            or value["reason"] != "mutable_state_without_original_event_chain"
            or value["researchOnly"] is not True):
        raise ValueError("legacy_policy_invalid")
    return _candidate("legacy_candidate", f"legacy:{value['legacySourceHash']}", value)
