"""Default-off metadata-only coverage diagnostics for a frozen contract."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

FLAG = "CONTRACT_GAP_REPORT_ENABLED"
SCHEMA_VERSION = 1
POLICY = "contract-gap-report-v1"
EXPECTED = ("quote", "fundamentals", "corporate_actions", "point_in_time")
DENOMINATOR = len(EXPECTED)
STALE_AFTER_DAYS = {
    "quote": 7,
    "fundamentals": 200,
    "corporate_actions": 32,
    "point_in_time": 7,
}


def enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _ratio(value: int) -> float:
    return round(value / DENOMINATOR, 4)


def _integrity(contract: dict, frozen: dict, manifest: dict) -> tuple[list[str], dict[str, str]]:
    blockers: list[str] = []
    records = contract.get("records")
    records = records if isinstance(records, list) else []
    contract_hash = contract.get("contractHash")
    manifest_contract = manifest.get("dataContract") if isinstance(manifest.get("dataContract"), dict) else {}
    if contract.get("schemaVersion") != 1 or manifest.get("schemaVersion") != 1 or frozen.get("schemaVersion") != 1:
        blockers.append("schema_version_invalid")
    by_name = {name: [item for item in records if item.get("name") == name] for name in EXPECTED}
    computed_certified = all(
        len(items) == 1 and items[0].get("quality") == "verified"
        for items in by_name.values()
    )
    certified_state = "consistent" if (contract.get("certified") is True) == computed_certified else "mismatch"
    if certified_state != "consistent":
        blockers.append("contract_certified_state_mismatch")
    contract_hash_state = "match" if isinstance(contract_hash, str) and contract_hash == _hash(records) else "mismatch"
    if contract_hash_state != "match":
        blockers.append("contract_hash_mismatch")
    manifest_hash_state = "match" if manifest_contract.get("contractHash") == contract_hash else "mismatch"
    if manifest_hash_state != "match":
        blockers.append("manifest_contract_hash_mismatch")
    frozen_records = frozen.get("records") if isinstance(frozen.get("records"), list) else []
    frozen_core = {
        "schemaVersion": 1,
        "policy": "frozen-lineage-v1",
        "candidateOrder": manifest.get("candidateOrder", []),
        "records": frozen_records,
    }
    frozen_hash_state = "match" if frozen.get("frozenDigest") == _hash(frozen_core) else "mismatch"
    if frozen_hash_state != "match":
        blockers.append("frozen_digest_mismatch")
    return sorted(set(blockers)), {
        "contractHash": contract_hash_state,
        "manifestContractHash": manifest_hash_state,
        "frozenDigest": frozen_hash_state,
        "contractCertified": certified_state,
    }


def report(contract: dict, frozen: dict, manifest: dict, *, decision_as_of: str) -> dict[str, Any]:
    if not enabled():
        return {"schemaVersion": SCHEMA_VERSION, "mode": "disabled"}
    decision = _time(decision_as_of)
    if decision is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policy": POLICY,
            "mode": "research_only",
            "coverageState": "incomplete",
            "blockers": ["decision_as_of_invalid"],
        }
    decision_utc = decision.astimezone(timezone.utc)
    global_blockers, hash_states = _integrity(contract, frozen, manifest)
    records = contract.get("records") if isinstance(contract.get("records"), list) else []
    records = [item for item in records if isinstance(item, dict)]
    frozen_records = frozen.get("records") if isinstance(frozen.get("records"), list) else []
    frozen_records = [item for item in frozen_records if isinstance(item, dict)]
    output_records: list[dict[str, Any]] = []
    counts = {
        "expected": DENOMINATOR,
        "present": 0,
        "missing": 0,
        "unknown": 0,
        "conflict": 0,
        "invalid": 0,
        "stale": 0,
        "outOfScope": sum(1 for item in records if item.get("name") not in EXPECTED),
        "selectedVersion": 0,
        "selectedNone": 0,
    }
    availability_ready = quality_ready = conflict_ready = 0

    for name in EXPECTED:
        matches = [item for item in records if item.get("name") == name]
        reasons: list[str] = []
        if not matches:
            counts["missing"] += 1
            counts["selectedNone"] += 1
            output_records.append({
                "name": name, "present": False, "availableAtState": "missing",
                "qualityState": "unknown", "conflictState": "unknown", "stale": False,
                "selection": "selectedNone", "reasons": ["record_missing"],
            })
            continue

        counts["present"] += 1
        if len(matches) != 1:
            counts["invalid"] += 1
            counts["selectedNone"] += 1
            output_records.append({
                "name": name, "present": True, "availableAtState": "invalid",
                "qualityState": "unknown", "conflictState": "unknown", "stale": False,
                "selection": "selectedNone", "reasons": ["version_ambiguous"],
            })
            continue

        item = matches[0]
        available_raw = item.get("availableAt")
        available = _time(available_raw)
        if not available_raw:
            available_state = "missing"
            reasons.append("available_at_missing")
        elif available is None:
            available_state = "invalid"
            reasons.append("available_at_invalid")
        elif available.astimezone(timezone.utc) > decision_utc:
            available_state = "future"
            reasons.append("available_at_after_decision")
        else:
            available_state = "usable"
            availability_ready += 1

        quality = item.get("quality")
        quality_state = "verified" if quality == "verified" else "unknown" if quality in (None, "", "unknown") else "not_verified"
        if quality_state == "verified":
            quality_ready += 1
        else:
            reasons.append("quality_not_verified")
        conflict = item.get("conflictStatus")
        conflict_state = "clear" if conflict == "no_conflict" else "unknown" if conflict in (None, "", "unknown") else "conflict"
        if conflict_state == "clear":
            conflict_ready += 1
        else:
            reasons.append("conflict_unknown" if conflict_state == "unknown" else "conflict_unresolved")
        if not item.get("effectiveDate"):
            reasons.append("effective_date_missing")
        if not item.get("evidenceHash"):
            reasons.append("evidence_hash_missing")
        if item.get("source") in (None, "", "unknown") or item.get("sourceDataset") in (None, "", "unknown"):
            reasons.append("source_unknown")

        stale = False
        if available_state == "usable" and available is not None:
            age = decision_utc - available.astimezone(timezone.utc)
            stale = age.total_seconds() > STALE_AFTER_DAYS[name] * 86400
            if stale:
                reasons.append("available_at_stale")
                counts["stale"] += 1

        frozen_matches = [
            value for value in frozen_records
            if value.get("name") == name and value.get("evidenceHash") == item.get("evidenceHash")
        ]
        if len(frozen_matches) != 1:
            reasons.append("frozen_selection_missing")
        if global_blockers:
            reasons.append("contract_integrity_invalid")
        reasons = sorted(set(reasons))
        if global_blockers or any(reason in reasons for reason in ("available_at_invalid", "available_at_after_decision")):
            counts["invalid"] += 1
        if any(reason in reasons for reason in ("quality_not_verified", "conflict_unknown", "available_at_missing", "source_unknown", "effective_date_missing", "evidence_hash_missing")):
            counts["unknown"] += 1
        if "conflict_unresolved" in reasons:
            counts["conflict"] += 1
        selected = not reasons
        counts["selectedVersion" if selected else "selectedNone"] += 1
        output_records.append({
            "name": name,
            "present": True,
            "availableAtState": available_state,
            "qualityState": quality_state,
            "conflictState": conflict_state,
            "stale": stale,
            "selection": "selectedVersion" if selected else "selectedNone",
            "reasons": reasons,
        })

    input_projection = {
        "decisionAsOf": decision.isoformat(),
        "hashStates": hash_states,
        "records": output_records,
        "counts": counts,
    }
    complete = counts["selectedVersion"] == DENOMINATOR and not global_blockers
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": POLICY,
        "stalePolicy": "availability-age-v1",
        "mode": "research_only",
        "diagnosticOnly": True,
        "decisionAsOf": decision.isoformat(),
        "timezone": str(decision.tzinfo),
        "coverageDenominator": DENOMINATOR,
        "coverageState": "complete" if complete else "incomplete",
        "counts": counts,
        "coverage": {
            "present": _ratio(counts["present"]),
            "availableAt": _ratio(availability_ready),
            "quality": _ratio(quality_ready),
            "conflictFree": _ratio(conflict_ready),
            "pitSelected": _ratio(counts["selectedVersion"]),
        },
        "hashStates": hash_states,
        "records": output_records,
        "blockers": global_blockers,
        "inputDigest": _hash(input_projection),
        "limitation": "Metadata-only diagnostic; it never certifies normalized values or enables advice.",
    }
