"""Offline contract for future forward-only full-market observations.

The contract validates fixture metadata and an append-only observation/gap
chain.  It does not observe a clock, fetch a source, persist a ledger, infer an
official membership event, or make any source/PIT/strategy gate eligible.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping

import official_population_source_admission as source_admission


SCHEMA_VERSION = 1
POLICY_VERSION = "forward-only-full-market-observation-contract-v1"
LEDGER_SCHEMA_VERSION = 1
TIMEZONE = "UTC"

MAX_NODES = 100_000
MAX_DEPTH = 14
MAX_STRING = 512
MAX_CANONICAL_BYTES = 2_000_000
MAX_INTEGER_ABS = 10**18

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:+/-]{1,160}$")

CURRENT_SOURCE_SLOTS = (
    "twse_current_master", "tpex_current_master",
    "tpex_emerging_current_master",
)
EVENT_TYPES = frozenset({
    "complete_observation", "observation_gap", "correction_observation",
})
TRANSITION_TYPES = frozenset({
    "observed_added", "observed_removed", "identity_changed",
    "market_changed", "unknown_gap",
})
GAP_REASONS = frozenset({
    "source_unavailable", "incomplete_pagination", "parse_rejection",
    "observation_window_missed", "source_conflict",
})
CLOCK_EVIDENCE_CLASS = "internal-observation-completion-utc-unverified-shape"
TRANSITION_AUTHORITY = "non_authoritative_transition"


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _forward_source_pins() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source_slot in CURRENT_SOURCE_SLOTS:
        candidate = source_admission.SOURCE_SLOT_PINS[source_slot]
        result[source_slot] = {
            "componentId": candidate["componentId"],
            "sourceContractHash": source_admission.source_slot_contract_hash(source_slot),
            "maximumEvidenceCapability": "forward_only",
            "producerId": "unregistered-forward-population-observer",
            "sourceSchemaHash": digest({
                "schemaVersion": 1,
                "sourceSlot": source_slot,
                "metadataOnly": True,
                "identityRequired": True,
                "pricesOrActivityProhibited": True,
            }),
            "termsContractHash": digest({
                "sourceSlot": source_slot,
                "status": "candidate-unadmitted",
                "attributionRequired": True,
                "privateRetentionUnverified": True,
            }),
        }
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


FORWARD_SOURCE_PINS: Mapping[str, Any] = _freeze(_forward_source_pins())
PINNED_POPULATION_POLICY_HASH = source_admission.PINNED_POPULATION_POLICY_HASH
PINNED_SOURCE_ADMISSION_POLICY_HASH = digest({
    "policyVersion": source_admission.POLICY_VERSION,
    "obligationMatrixHash": source_admission.OBLIGATION_MATRIX_HASH,
    "currentSourceContracts": {
        source_slot: source_admission.source_slot_contract_hash(source_slot)
        for source_slot in CURRENT_SOURCE_SLOTS
    },
})
OBSERVER_CONTRACT_HASH = digest({
    "policyVersion": POLICY_VERSION,
    "populationPolicyHash": PINNED_POPULATION_POLICY_HASH,
    "sourceAdmissionPolicyHash": PINNED_SOURCE_ADMISSION_POLICY_HASH,
    "sourcePins": _forward_source_pins(),
    "clockEvidenceClass": CLOCK_EVIDENCE_CLASS,
    "transitionAuthority": TRANSITION_AUTHORITY,
})

ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "observerContractHash",
    "populationPolicyHash", "sourceAdmissionPolicyHash", "ledgerEvents",
    "ledgerHash",
})
EVENT_KEYS = frozenset({
    "sequenceNumber", "eventId", "eventType", "previousEventHash",
    "observationCompletedAt", "clockEvidenceClass", "sourceBatchHash",
    "components", "transitions", "gapReason", "correctionOfEventId",
    "unionEntityCount", "unionIdentityCount", "unionEntitySetHash",
    "unionIdentitySetHash", "eventHash",
})
COMPONENT_KEYS = frozenset({
    "sourceSlot", "componentId", "sourceContractHash", "producerId",
    "sourceSchemaHash", "termsContractHash", "expectedPages", "parsedPages",
    "expectedRecords", "parsedRecords", "rejectedRecords", "entityCount",
    "identityCommitments", "entitySetHash", "identitySetHash", "contentHash",
    "componentHash",
})
TRANSITION_KEYS = frozenset({
    "transitionType", "authorityClass", "count", "evidenceHash",
})
EMPTY_LEDGER_KEYS = frozenset({
    "schemaVersion", "policyVersion", "mode", "events", "ledgerHash",
})

FORBIDDEN_KEYS = frozenset({
    "raw", "rows", "body", "headers", "url", "query", "token", "secret",
    "authorization", "cookie", "password", "price", "volume", "score",
    "rank", "return", "returns", "mdd", "pnl", "recommendation",
    "candidates", "availableat", "publishedat", "retrievedat", "generatedat",
    "firstseen", "admitted", "historicaleligible", "pitcoveragecertified",
    "strategyvalidated", "promotioneligible", "adviceenabled",
})
FORBIDDEN_TEXT = (
    "://", "bearer ", "authorization:", "token=", "password=", "cookie=",
    "-----begin ",
)
FIXED_BLOCKERS = (
    "forward_observer_runtime_unimplemented",
    "official_source_admission_unregistered",
    "historical_backfill_prohibited",
    "private_append_only_storage_unconfigured",
    "continuous_coverage_not_established",
    "formal_flow_disconnected",
)


def empty_ledger() -> dict[str, Any]:
    value = {
        "schemaVersion": LEDGER_SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "events": [],
    }
    value["ledgerHash"] = digest(value)
    return value


def _json_domain(value: Any) -> bool:
    seen: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            return False
        if item is None or isinstance(item, bool):
            return True
        if isinstance(item, int):
            return not isinstance(item, bool) and abs(item) <= MAX_INTEGER_ABS
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, str):
            return len(item) <= MAX_STRING and all(ord(char) >= 32 for char in item)
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            try:
                if isinstance(item, dict):
                    return all(
                        isinstance(key, str) and len(key) <= 100
                        and visit(child, depth + 1)
                        for key, child in item.items()
                    )
                return all(visit(child, depth + 1) for child in item)
            finally:
                seen.remove(identity)
        return False

    return visit(value, 0)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.replace("_", "").casefold() in FORBIDDEN_KEYS
            or _contains_forbidden(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    if isinstance(value, str):
        folded = value.casefold()
        return any(marker in folded for marker in FORBIDDEN_TEXT)
    return False


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _safe_id(value: Any) -> bool:
    return (
        isinstance(value, str) and SAFE_ID.fullmatch(value) is not None
        and "http" not in value.casefold() and "?" not in value
    )


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def component_hash(value: dict[str, Any]) -> str:
    return digest({key: child for key, child in value.items() if key != "componentHash"})


def identity_commitment_hash(value: dict[str, Any]) -> str:
    return digest(value)


def transition_hash(value: dict[str, Any]) -> str:
    return digest(value)


def _normalized_event(value: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: child for key, child in value.items() if key != "eventHash"
    }
    components = normalized.get("components")
    transitions = normalized.get("transitions")
    if isinstance(components, list) and all(isinstance(row, dict) for row in components):
        normalized["components"] = sorted(components, key=lambda row: row.get("sourceSlot", ""))
    if isinstance(transitions, list) and all(isinstance(row, dict) for row in transitions):
        normalized["transitions"] = sorted(
            transitions,
            key=lambda row: (row.get("transitionType", ""), row.get("evidenceHash", "")),
        )
    return normalized


def event_hash(value: dict[str, Any]) -> str:
    return digest(_normalized_event(value))


def ledger_hash(events: list[dict[str, Any]]) -> str:
    return digest([_normalized_event(event) | {"eventHash": event.get("eventHash")} for event in events])


def _component_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != COMPONENT_KEYS:
        return False
    source_slot = value.get("sourceSlot")
    if source_slot not in FORWARD_SOURCE_PINS:
        return False
    expected = FORWARD_SOURCE_PINS[source_slot]
    counts = (
        value.get("expectedPages"), value.get("parsedPages"),
        value.get("expectedRecords"), value.get("parsedRecords"),
        value.get("rejectedRecords"), value.get("entityCount"),
    )
    commitments = value.get("identityCommitments")
    commitments_valid = bool(
        isinstance(commitments, list)
        and len(commitments) == value.get("entityCount")
        and commitments == sorted(commitments)
        and len(set(commitments)) == len(commitments)
        and all(_hex64(item) for item in commitments)
    )
    return bool(
        value.get("componentId") == expected["componentId"]
        and value.get("sourceContractHash") == expected["sourceContractHash"]
        and value.get("producerId") == expected["producerId"]
        and value.get("sourceSchemaHash") == expected["sourceSchemaHash"]
        and value.get("termsContractHash") == expected["termsContractHash"]
        and all(_nonnegative_int(item) for item in counts)
        and value["expectedPages"] > 0
        and value["expectedPages"] == value["parsedPages"]
        and value["expectedRecords"] == value["parsedRecords"] == value["entityCount"]
        and value["entityCount"] > 0
        and value["rejectedRecords"] == 0
        and commitments_valid
        and value.get("entitySetHash") == digest(commitments)
        and value.get("identitySetHash") == digest(commitments)
        and _hex64(value.get("contentHash"))
        and value.get("componentHash") == component_hash(value)
    )


def _transition_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict) and set(value) == TRANSITION_KEYS
        and value.get("transitionType") in TRANSITION_TYPES
        and value.get("authorityClass") == TRANSITION_AUTHORITY
        and isinstance(value.get("count"), int) and not isinstance(value.get("count"), bool)
        and value["count"] > 0 and _hex64(value.get("evidenceHash"))
    )


def _event_shape_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != EVENT_KEYS:
        return False
    event_type = value.get("eventType")
    components = value.get("components")
    transitions = value.get("transitions")
    if (
        event_type not in EVENT_TYPES
        or not isinstance(value.get("sequenceNumber"), int)
        or isinstance(value.get("sequenceNumber"), bool)
        or value["sequenceNumber"] <= 0
        or not _safe_id(value.get("eventId"))
        or (value.get("previousEventHash") is not None and not _hex64(value["previousEventHash"]))
        or _utc(value.get("observationCompletedAt")) is None
        or value.get("clockEvidenceClass") != CLOCK_EVIDENCE_CLASS
        or not _hex64(value.get("sourceBatchHash"))
        or not isinstance(components, list)
        or not isinstance(transitions, list)
        or not all(_transition_valid(row) for row in transitions)
        or value.get("eventHash") != event_hash(value)
    ):
        return False
    commitments = [
        commitment
        for row in components
        for commitment in row["identityCommitments"]
    ]
    if len(commitments) != len(set(commitments)):
        return False
    ordered_commitments = sorted(commitments)
    if event_type == "observation_gap":
        return bool(
            components == []
            and value.get("gapReason") in GAP_REASONS
            and value.get("correctionOfEventId") is None
            and len(transitions) == 1
            and transitions[0]["transitionType"] == "unknown_gap"
            and value.get("unionEntityCount") == 0
            and value.get("unionIdentityCount") == 0
            and value.get("unionEntitySetHash") is None
            and value.get("unionIdentitySetHash") is None
            and value["sourceBatchHash"] == digest({
                "eventId": value["eventId"], "gapReason": value["gapReason"],
            })
        )
    if (
        len(components) != len(CURRENT_SOURCE_SLOTS)
        or not all(_component_valid(row) for row in components)
        or {row["sourceSlot"] for row in components} != set(CURRENT_SOURCE_SLOTS)
        or any(row["transitionType"] == "unknown_gap" for row in transitions)
        or value.get("gapReason") is not None
        or value.get("unionEntityCount") != sum(row["entityCount"] for row in components)
        or value.get("unionIdentityCount") != sum(row["entityCount"] for row in components)
        or value.get("unionEntitySetHash") != digest(ordered_commitments)
        or value.get("unionIdentitySetHash") != digest(ordered_commitments)
        or value["sourceBatchHash"] != digest(sorted(row["componentHash"] for row in components))
    ):
        return False
    if event_type == "complete_observation":
        return value.get("correctionOfEventId") is None
    return _safe_id(value.get("correctionOfEventId"))


def _report(
    blockers: list[str], *, structural: bool = False,
    events: list[dict[str, Any]] | None = None, input_digest: str = "",
) -> dict[str, Any]:
    rows = events or []
    complete = sum(row.get("eventType") == "complete_observation" for row in rows)
    gaps = sum(row.get("eventType") == "observation_gap" for row in rows)
    corrections = sum(row.get("eventType") == "correction_observation" for row in rows)
    transition_counts = {name: 0 for name in sorted(TRANSITION_TYPES)}
    for row in rows:
        for transition in row.get("transitions", []):
            name = transition.get("transitionType")
            if name in transition_counts:
                transition_counts[name] += transition.get("count", 0)
    first_complete = next(
        (row for row in rows if row.get("eventType") in {
            "complete_observation", "correction_observation",
        }),
        None,
    )
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "ledgerStructurallyValid": structural,
        "forwardObservationShapeComplete": bool(structural and complete > 0),
        "coverageContinuityAssumed": False,
        "forwardEvidenceAdmitted": False,
        "sourceAdmitted": False,
        "historicalEligible": False,
        "officialProducerRegistered": False,
        "pitCoverageCertified": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "registryEligible": False,
        "formalGateAttached": False,
        "preFirstSeenBackfillAllowed": False,
        "officialMembershipTransitionsCertified": False,
        "eventCount": len(rows),
        "completeObservationCount": complete,
        "gapCount": gaps,
        "correctionCount": corrections,
        "firstSeenBoundaryHash": digest({
            "observationCompletedAt": first_complete["observationCompletedAt"],
            "eventHash": first_complete["eventHash"],
        }) if first_complete else "",
        "transitionCounts": transition_counts,
        "inputDigest": input_digest,
        "blockers": list(dict.fromkeys([*FIXED_BLOCKERS, *blockers])),
    }
    report["reportDigest"] = digest(report)
    return report


def _evaluate(value: Any) -> dict[str, Any]:
    if not _json_domain(value) or not isinstance(value, dict):
        return _report(["input_not_bounded_json"])
    if len(canonical(value).encode("utf-8")) > MAX_CANONICAL_BYTES:
        return _report(["input_too_large"])
    if set(value) != ROOT_KEYS or _contains_forbidden(value):
        return _report(["input_contract_invalid"])
    events = value.get("ledgerEvents")
    root_valid = bool(
        value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("policyVersion") == POLICY_VERSION
        and value.get("observerContractHash") == OBSERVER_CONTRACT_HASH
        and value.get("populationPolicyHash") == PINNED_POPULATION_POLICY_HASH
        and value.get("sourceAdmissionPolicyHash") == PINNED_SOURCE_ADMISSION_POLICY_HASH
        and isinstance(events, list)
    )
    if not isinstance(events, list):
        return _report(["root_contract_invalid"])
    blockers: list[str] = []
    if not root_valid:
        blockers.append("root_contract_invalid")
    if not events:
        blockers.append("observation_ledger_empty")
    if not all(_event_shape_valid(row) for row in events):
        blockers.append("observation_event_contract_invalid")
    if events != sorted(
        events,
        key=lambda row: row.get("sequenceNumber", -1) if isinstance(row, dict) else -1,
    ):
        blockers.append("append_only_sequence_order_invalid")
    event_ids: set[str] = set()
    event_hashes: set[str] = set()
    prior_time: datetime | None = None
    prior_hash: str | None = None
    known_events: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(events, start=1):
        if not isinstance(row, dict):
            continue
        event_id = row.get("eventId")
        current_hash = row.get("eventHash")
        completed = _utc(row.get("observationCompletedAt"))
        if row.get("sequenceNumber") != index or row.get("previousEventHash") != prior_hash:
            blockers.append("append_only_hash_chain_invalid")
        if event_id in event_ids or current_hash in event_hashes:
            blockers.append("duplicate_or_conflicting_event")
        if prior_time is not None and (completed is None or completed <= prior_time):
            blockers.append("observation_clock_not_strictly_forward")
        correction_of = row.get("correctionOfEventId")
        if row.get("eventType") == "correction_observation" and (
            correction_of not in known_events
            or known_events[correction_of].get("eventType") == "observation_gap"
        ):
            blockers.append("correction_parent_invalid")
        if isinstance(event_id, str):
            event_ids.add(event_id)
            known_events[event_id] = row
        if isinstance(current_hash, str):
            event_hashes.add(current_hash)
            prior_hash = current_hash
        if completed is not None:
            prior_time = completed
    normalized_events = [
        _normalized_event(row) | {"eventHash": row.get("eventHash")}
        if isinstance(row, dict) else row
        for row in events
    ]
    if value.get("ledgerHash") != digest(normalized_events):
        blockers.append("ledger_hash_mismatch")
    structural = not blockers
    normalized_value = {**value, "ledgerEvents": normalized_events}
    return _report(
        blockers, structural=structural, events=events,
        input_digest=digest(normalized_value),
    )


def evaluate(value: Any) -> dict[str, Any]:
    """Public pure fail-closed validation boundary."""
    try:
        return _evaluate(value)
    except Exception:
        return _report(["input_fail_closed"])


def append_candidate(
    existing_events: Any, candidate: Any,
) -> tuple[list[dict[str, Any]], str]:
    """Pure append model: exact duplicate no-op, collisions fail closed."""
    try:
        if (
            not _json_domain(existing_events) or not isinstance(existing_events, list)
            or not _json_domain(candidate) or not isinstance(candidate, dict)
            or not _event_shape_valid(candidate)
            or not all(isinstance(row, dict) and _event_shape_valid(row) for row in existing_events)
        ):
            return [], "invalid"
        copied = deepcopy(existing_events)
        existing_root = {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "observerContractHash": OBSERVER_CONTRACT_HASH,
            "populationPolicyHash": PINNED_POPULATION_POLICY_HASH,
            "sourceAdmissionPolicyHash": PINNED_SOURCE_ADMISSION_POLICY_HASH,
            "ledgerEvents": copied,
            "ledgerHash": ledger_hash(copied),
        }
        if copied and not _evaluate(existing_root)["ledgerStructurallyValid"]:
            return copied, "invalid"
        if any(row["eventHash"] == candidate["eventHash"] for row in copied):
            return copied, "duplicate_noop"
        if any(
            row["eventId"] == candidate["eventId"]
            or row["sequenceNumber"] == candidate["sequenceNumber"]
            for row in copied
        ):
            return copied, "conflict"
        expected_sequence = len(copied) + 1
        expected_previous = copied[-1]["eventHash"] if copied else None
        if (
            candidate["sequenceNumber"] != expected_sequence
            or candidate["previousEventHash"] != expected_previous
        ):
            return copied, "conflict"
        candidate_events = [*copied, deepcopy(candidate)]
        candidate_root = {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "observerContractHash": OBSERVER_CONTRACT_HASH,
            "populationPolicyHash": PINNED_POPULATION_POLICY_HASH,
            "sourceAdmissionPolicyHash": PINNED_SOURCE_ADMISSION_POLICY_HASH,
            "ledgerEvents": candidate_events,
            "ledgerHash": ledger_hash(candidate_events),
        }
        if not _evaluate(candidate_root)["ledgerStructurallyValid"]:
            return copied, "conflict"
        return candidate_events, "appended"
    except Exception:
        return [], "invalid"


def run(value: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off boundary; disabled mode never inspects ``value``."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "forwardEvidenceAdmitted": False,
            "historicalEligible": False,
            "registryEligible": False,
        }
    return evaluate(value)
