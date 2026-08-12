"""Pure E1B frozen-manifest and replay checks for E1A event candidates."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

import decision_outcome_event_contract as event_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-frozen-manifest-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = {
    "schemaVersion", "policyVersion", "scopeId", "eventHashes",
    "expectedEventCount", "expectedDecisionCount", "expectedOutcomeCount",
    "expectedLegacyCount", "decisionDateCount", "manifestDigest",
}
OUTPUT_KEYS = {
    "schemaVersion", "mode", "diagnosticOnly", "verifiedCandidateSet",
    "completenessExternallyAnchored", "readyForWriterReview",
    "promotionEligible", "manifestDigest", "eventCount", "decisionCount",
    "outcomeCount", "legacyCount", "decisionDateCount", "blockers",
    "limitations", "replayDigest",
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("manifest_value_not_canonical") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _blocked(*codes: str) -> dict[str, Any]:
    result = {
        "schemaVersion": SCHEMA_VERSION, "mode": "research_only",
        "diagnosticOnly": True, "verifiedCandidateSet": False,
        "completenessExternallyAnchored": False,
        "readyForWriterReview": False, "promotionEligible": False,
        "manifestDigest": None, "eventCount": 0, "decisionCount": 0,
        "outcomeCount": 0, "legacyCount": 0, "decisionDateCount": 0,
        "blockers": sorted(set(codes)),
        "limitations": ["offline_candidate_set_only", "no_external_anchor",
                        "not_investment_or_promotion_evidence"],
        "replayDigest": None,
    }
    assert set(result) == OUTPUT_KEYS
    return result


def _rebuild(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event_candidate_invalid")
    kind = event.get("eventType")
    builder = {
        "decision_candidate": event_contract.decision_candidate,
        "outcome_candidate": event_contract.outcome_candidate,
        "legacy_candidate": event_contract.legacy_candidate,
    }.get(kind)
    if builder is None:
        raise ValueError("event_type_invalid")
    rebuilt = builder(event.get("payload"), enabled=True)
    if event != rebuilt or event.get("promotionEligible") is not False or event.get("chainVerified") is not False:
        raise ValueError("event_candidate_invalid")
    return json.loads(_canonical(rebuilt).decode("utf-8"))


def _validated_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        raise ValueError("event_set_invalid")
    rebuilt = [_rebuild(event) for event in events]
    hashes = [event["eventHash"] for event in rebuilt]
    keys = [event["logicalKey"] for event in rebuilt]
    if len(set(hashes)) != len(hashes):
        raise ValueError("duplicate_event_hash")
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_logical_key")
    return rebuilt


def freeze_manifest(events: Any, scope_id: str, *, enabled: bool = False) -> dict[str, Any] | None:
    """Build a deterministic candidate-set manifest; it is not an external anchor."""
    if not enabled:
        return None
    if not isinstance(scope_id, str) or HEX.fullmatch(scope_id) is None:
        raise ValueError("scope_id_invalid")
    verified = _validated_events(events)
    counts = _counts(verified)
    material = {
        "schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
        "scopeId": scope_id,
        "eventHashes": sorted(event["eventHash"] for event in verified),
        "expectedEventCount": len(verified),
        "expectedDecisionCount": counts["decisionCount"],
        "expectedOutcomeCount": counts["outcomeCount"],
        "expectedLegacyCount": counts["legacyCount"],
        "decisionDateCount": counts["decisionDateCount"],
    }
    return {**material, "manifestDigest": _digest(material)}


def verify(events: Any, manifest: Any, *, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return _blocked("feature_disabled")
    try:
        verified = _validated_events(events)
        _verify_manifest(verified, manifest)
        _verify_relations(verified)
        counts = _counts(verified)
        replay_material = {
            "manifestDigest": manifest["manifestDigest"],
            "eventHashes": sorted(event["eventHash"] for event in verified),
            **counts,
        }
    except (KeyError, TypeError, ValueError) as error:
        code = str(error) if isinstance(error, ValueError) and str(error) else "verification_failed"
        return _blocked(code)
    result = {
        "schemaVersion": SCHEMA_VERSION, "mode": "research_only",
        "diagnosticOnly": True, "verifiedCandidateSet": True,
        "completenessExternallyAnchored": False,
        "readyForWriterReview": True, "promotionEligible": False,
        "manifestDigest": manifest["manifestDigest"],
        "eventCount": len(verified), **counts,
        "blockers": [],
        "limitations": ["offline_candidate_set_only", "no_external_anchor",
                        "not_investment_or_promotion_evidence"],
        "replayDigest": _digest(replay_material),
    }
    assert set(result) == OUTPUT_KEYS
    return result


def _verify_manifest(events: list[dict[str, Any]], manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("manifest_schema_mismatch")
    if (manifest["schemaVersion"] != SCHEMA_VERSION
            or manifest["policyVersion"] != POLICY_VERSION
            or not isinstance(manifest["scopeId"], str)
            or HEX.fullmatch(manifest["scopeId"]) is None):
        raise ValueError("manifest_identity_invalid")
    hashes = manifest["eventHashes"]
    if (not isinstance(hashes, list) or any(not isinstance(item, str) or HEX.fullmatch(item) is None for item in hashes)
            or hashes != sorted(set(hashes))):
        raise ValueError("manifest_hash_list_invalid")
    actual_hashes = sorted(event["eventHash"] for event in events)
    if hashes != actual_hashes:
        raise ValueError("manifest_event_set_mismatch")
    counts = _counts(events)
    expected = {
        "expectedEventCount": len(events),
        "expectedDecisionCount": counts["decisionCount"],
        "expectedOutcomeCount": counts["outcomeCount"],
        "expectedLegacyCount": counts["legacyCount"],
        "decisionDateCount": counts["decisionDateCount"],
    }
    if any(type(manifest[name]) is not int or manifest[name] != value for name, value in expected.items()):
        raise ValueError("manifest_count_mismatch")
    material = {key: value for key, value in manifest.items() if key != "manifestDigest"}
    if not isinstance(manifest["manifestDigest"], str) or manifest["manifestDigest"] != _digest(material):
        raise ValueError("manifest_digest_invalid")


def _counts(events: list[dict[str, Any]]) -> dict[str, int]:
    decisions = [item for item in events if item["eventType"] == "decision_candidate"]
    outcomes = [item for item in events if item["eventType"] == "outcome_candidate"]
    legacy = [item for item in events if item["eventType"] == "legacy_candidate"]
    if len(legacy) > 1:
        raise ValueError("multiple_legacy_summaries_forbidden")
    return {
        "decisionCount": len(decisions), "outcomeCount": len(outcomes),
        "legacyCount": len(legacy),
        "decisionDateCount": len({item["payload"]["decisionAsOf"] for item in decisions}),
    }


def _verify_relations(events: list[dict[str, Any]]) -> None:
    decisions = sorted((item for item in events if item["eventType"] == "decision_candidate"),
                       key=lambda item: item["logicalKey"])
    decision_by_hash = {item["eventHash"]: item for item in decisions}
    previous = event_contract.GENESIS
    for item in decisions:
        if item["payload"]["claimedPreviousChainHead"] != previous:
            raise ValueError("decision_chain_claim_invalid")
        previous = item["eventHash"]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in events:
        if item["eventType"] != "outcome_candidate":
            continue
        parent = item["payload"]["decisionEventHash"]
        if parent not in decision_by_hash:
            raise ValueError("outcome_parent_missing")
        if item["payload"]["settledDate"] <= decision_by_hash[parent]["payload"]["decisionAsOf"]:
            raise ValueError("outcome_settlement_not_after_decision")
        grouped[parent].append(item)
    for parent, items in grouped.items():
        ordered = sorted(items, key=lambda item: item["payload"]["horizon"])
        previous_hash = event_contract.GENESIS
        previous_date: str | None = None
        for item in ordered:
            if item["payload"]["claimedPreviousOutcomeHash"] != previous_hash:
                raise ValueError("outcome_chain_claim_invalid")
            if previous_date is not None and item["payload"]["settledDate"] < previous_date:
                raise ValueError("outcome_horizon_date_reversed")
            previous_hash = item["eventHash"]
            previous_date = item["payload"]["settledDate"]
