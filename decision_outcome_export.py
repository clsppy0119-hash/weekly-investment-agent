"""Pure bridge from one final candidate manifest to shadow decision events.

This module intentionally performs no I/O.  It does not write a ledger, enable
advice, change ranking, or notify anyone.  It only turns an already frozen,
PIT-certified research snapshot into append-only *candidate* events that remain
ineligible for promotion until later writer, replay, and outcome verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import decision_outcome_event_contract as event_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "candidate-manifest-shadow-export-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = {
    "schemaVersion", "reportDate", "reportMode", "phase", "strategyVersion",
    "quoteUpdatedAt", "adviceGate", "candidateOrder", "previewCandidates",
    "eligibleCandidates", "evidenceInputs", "dataContract",
}
REQUIRED_RECORDS = ("quote", "fundamentals", "corporate_actions", "point_in_time")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("shadow_export_value_not_canonical") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "diagnosticOnly": True,
        "readyForWriterReview": False,
        "promotionEligible": False,
        "events": [],
        "eventCount": 0,
        "manifestHash": None,
        "nextChainHead": None,
        "blockers": sorted(set(codes)),
        "limitations": ["offline_candidate_events_only", "no_ledger_or_external_anchor",
                        "not_investment_or_promotion_evidence"],
    }


def export(manifest: Any, report_text: Any, prior_chain_head: Any,
           *, enabled: bool = False) -> dict[str, Any]:
    """Build deterministic shadow decision candidates from quality-passed previews."""
    if not enabled:
        output = _blocked("feature_disabled")
        output["mode"] = "disabled"
        return output
    try:
        _validate_manifest(manifest)
        if not isinstance(report_text, str) or not report_text.strip():
            raise ValueError("report_text_invalid")
        if not isinstance(prior_chain_head, str) or HEX.fullmatch(prior_chain_head) is None:
            raise ValueError("prior_chain_head_invalid")
        records = {item["name"]: item for item in manifest["dataContract"]["records"]}
        candidates = [item for item in manifest["previewCandidates"] if item["quality"]["passed"]]
        if not candidates:
            raise ValueError("no_quality_passed_shadow_candidates")

        manifest_hash = _digest(manifest)
        report_hash = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
        pool_material = [{key: item[key] for key in
                          ("code", "style", "rank", "score", "coverage", "entryPrice")}
                         for item in candidates]
        pool_hash = _digest(pool_material)
        previous = prior_chain_head
        events = []
        for item in candidates:
            payload = {
                "decisionAsOf": manifest["reportDate"],
                "code": item["code"],
                "mode": manifest["reportMode"],
                "style": item["style"],
                "rank": item["rank"],
                "strategyVersion": manifest["strategyVersion"],
                "score": item["score"],
                "coverage": item["coverage"],
                "entryPrice": item["entryPrice"],
                "candidateManifestHash": manifest_hash,
                "evidenceHash": manifest["dataContract"]["contractHash"],
                "reportHash": report_hash,
                "eligiblePoolHash": pool_hash,
                "quoteProvenanceHash": records["quote"]["evidenceHash"],
                "fundamentalProvenanceHash": records["fundamentals"]["evidenceHash"],
                "dataQuality": "qualified",
                "costModelVersion": event_contract.COST_MODEL_VERSION,
                "claimedPreviousChainHead": previous,
                "researchOnly": True,
            }
            event = event_contract.decision_candidate(payload, enabled=True)
            events.append(event)
            previous = event["eventHash"]
    except (KeyError, TypeError, ValueError) as error:
        code = str(error) if isinstance(error, ValueError) and str(error) else "shadow_export_invalid"
        return _blocked(code)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "diagnosticOnly": True,
        "readyForWriterReview": True,
        "promotionEligible": False,
        "events": events,
        "eventCount": len(events),
        "manifestHash": manifest_hash,
        "nextChainHead": previous,
        "blockers": [],
        "limitations": ["offline_candidate_events_only", "no_ledger_or_external_anchor",
                        "not_investment_or_promotion_evidence"],
    }


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("candidate_manifest_schema_invalid")
    if (manifest["schemaVersion"] != 1 or manifest["phase"] != "final"
            or manifest["reportMode"] != "comprehensive"
            or not isinstance(manifest["reportDate"], str)
            or not isinstance(manifest["strategyVersion"], str)):
        raise ValueError("candidate_manifest_identity_invalid")
    contract = manifest["dataContract"]
    if (not isinstance(contract, dict) or contract.get("certified") is not True
            or contract.get("blockers") != []
            or not isinstance(contract.get("contractHash"), str)
            or HEX.fullmatch(contract["contractHash"]) is None
            or not isinstance(contract.get("records"), list)):
        raise ValueError("data_contract_not_certified")
    if contract["contractHash"] != _digest(contract["records"]):
        raise ValueError("data_contract_hash_mismatch")
    records = {item.get("name"): item for item in contract["records"] if isinstance(item, dict)}
    for name in REQUIRED_RECORDS:
        item = records.get(name)
        if (not isinstance(item, dict) or item.get("quality") != "verified"
                or item.get("conflictStatus") != "no_conflict"
                or not isinstance(item.get("availableAt"), str)
                or not isinstance(item.get("evidenceHash"), str)
                or HEX.fullmatch(item["evidenceHash"]) is None):
            raise ValueError(f"{name}_provenance_not_verified")
    previews = manifest["previewCandidates"]
    order = manifest["candidateOrder"]
    if (not isinstance(previews, list) or not isinstance(order, list)
            or [item.get("code") for item in previews] != order
            or len(order) != len(set(order))):
        raise ValueError("candidate_order_invalid")
    for item in previews:
        if not isinstance(item, dict) or set(item) != {
            "code", "name", "style", "rank", "score", "coverage", "entryPrice", "quality"
        }:
            raise ValueError("preview_candidate_schema_invalid")
        quality = item["quality"]
        if (not isinstance(quality, dict) or set(quality) != {"passed", "blockers"}
                or type(quality["passed"]) is not bool or not isinstance(quality["blockers"], list)
                or quality["passed"] != (quality["blockers"] == [])):
            raise ValueError("preview_quality_invalid")
        if quality["passed"] and (
            not isinstance(item["entryPrice"], (int, float))
            or isinstance(item["entryPrice"], bool)
            or not math.isfinite(float(item["entryPrice"]))
            or item["entryPrice"] <= 0
        ):
            raise ValueError("entry_price_invalid")
