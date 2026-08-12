"""Offline source-admission policy for a future official population producer.

The module classifies metadata-only fixtures.  It never admits a source,
authenticates an authority, reads a registry, or changes Node49/50/51.  A
structurally complete fixture is only evidence that a future review package
has the expected shape.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

import official_full_market_population as population_producer
import official_population_artifact_receipt as receipt_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "official-population-source-admission-policy-v1"
TERMS_POLICY_VERSION = "official-population-lawful-use-v1"
REGISTRY_SCHEMA_VERSION = 1
TIMEZONE = "Asia/Taipei"

HISTORICAL = "HISTORICAL_PIT_CERTIFIABLE"
FORWARD = "FORWARD_OBSERVED_ONLY"
REJECTED = "REJECTED_UNKNOWN"

MAX_NODES = 40_000
MAX_DEPTH = 14
MAX_STRING = 512
MAX_CANONICAL_BYTES = 1_000_000
MAX_INTEGER_ABS = 10**18

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:+/-]{1,160}$")
DATE_ONLY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

REQUIRED_COMPONENTS = tuple(population_producer.REQUIRED_COMPONENTS)
PINNED_POPULATION_POLICY_HASH = population_producer.population_policy_hash()
PINNED_NODE50_CONTRACT_HASH = receipt_contract.PRODUCER_CONTRACT_HASH
PINNED_NODE51_RECEIPT_POLICY_HASH = hashlib.sha256(
    json.dumps(
        {
            "policyVersion": receipt_contract.POLICY_VERSION,
            "producerContractHash": receipt_contract.PRODUCER_CONTRACT_HASH,
            "sourceContractSetHash": receipt_contract.SOURCE_CONTRACT_SET_HASH,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
PINNED_NODE50_SOURCE_ADMISSION_COUNT = len(
    population_producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _slot_definitions() -> dict[str, dict[str, Any]]:
    """Fresh candidate pins; none of these candidates is admitted."""
    return {
        "twse_current_master": {
            "componentId": "twse_active",
            "providerAlias": "TWSE",
            "sourceContractId": "twse-company-master-candidate-v1",
            "datasetId": "data-gov-tw-18419",
            "endpointContractHash": _digest({
                "hostAlias": "TWSE-openapi", "method": "GET",
                "path": "/opendata/t187ap03_L", "query": "none",
            }),
            "requiredEffectiveSemantics": "complete_membership_snapshot",
            "obligationIds": ("twse_active_snapshot",),
            "maximumEvidenceCapability": "forward_only",
        },
        "tpex_current_master": {
            "componentId": "tpex_active",
            "providerAlias": "TPEx",
            "sourceContractId": "tpex-company-master-candidate-v1",
            "datasetId": "data-gov-tw-25036",
            "endpointContractHash": _digest({
                "hostAlias": "TPEx-openapi", "method": "GET",
                "path": "/mopsfin_t187ap03_O", "query": "none",
            }),
            "requiredEffectiveSemantics": "complete_membership_snapshot",
            "obligationIds": ("tpex_active_snapshot",),
            "maximumEvidenceCapability": "forward_only",
        },
        "tpex_emerging_current_master": {
            "componentId": "emerging_active",
            "providerAlias": "TPEx",
            "sourceContractId": "tpex-emerging-master-candidate-v1",
            "datasetId": "unregistered-emerging-catalog-contract",
            "endpointContractHash": _digest({
                "hostAlias": "TPEx-openapi", "method": "GET",
                "path": "/mopsfin_t187ap04_O", "query": "none",
            }),
            "requiredEffectiveSemantics": "complete_membership_snapshot",
            "obligationIds": ("emerging_active_snapshot",),
            "maximumEvidenceCapability": "forward_only",
        },
        "twse_terminated_master": {
            "componentId": "membership_events",
            "providerAlias": "TWSE",
            "sourceContractId": "twse-terminated-master-candidate-v1",
            "datasetId": "data-gov-tw-11543",
            "endpointContractHash": _digest({
                "hostAlias": "data-gov-tw-metadata", "method": "GET",
                "datasetId": "11543", "operation": "metadata-only",
            }),
            "requiredEffectiveSemantics": "explicit_exit_only",
            "obligationIds": ("twse_exit_event",),
            "maximumEvidenceCapability": "supplemental_exit_only",
        },
        "twse_membership_events": {
            "componentId": "membership_events",
            "providerAlias": "TWSE",
            "sourceContractId": "twse-membership-events-unregistered-v1",
            "datasetId": "unregistered-twse-event-archive",
            "endpointContractHash": _digest({
                "hostAlias": "TWSE-official", "operation": "unregistered",
            }),
            "requiredEffectiveSemantics": "explicit_entry_transfer_identity",
            "obligationIds": (
                "twse_entry_event", "twse_transfer_event",
                "twse_identity_correction",
            ),
            "maximumEvidenceCapability": "unregistered",
        },
        "tpex_membership_events": {
            "componentId": "membership_events",
            "providerAlias": "TPEx",
            "sourceContractId": "tpex-membership-events-unregistered-v1",
            "datasetId": "unregistered-tpex-event-archive",
            "endpointContractHash": _digest({
                "hostAlias": "TPEx-official", "operation": "unregistered",
            }),
            "requiredEffectiveSemantics": "explicit_entry_exit_transfer_identity",
            "obligationIds": (
                "tpex_entry_event", "tpex_exit_event", "tpex_transfer_event",
                "tpex_identity_correction", "emerging_entry_event",
                "emerging_exit_event", "emerging_transfer_event",
                "emerging_identity_correction",
            ),
            "maximumEvidenceCapability": "unregistered",
        },
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


SOURCE_SLOT_PINS: Mapping[str, Any] = _freeze(_slot_definitions())
SOURCE_SLOTS = tuple(_slot_definitions())
OBLIGATIONS = tuple(sorted({
    obligation
    for row in _slot_definitions().values()
    for obligation in row["obligationIds"]
}))
OBLIGATION_MATRIX_HASH = hashlib.sha256(
    json.dumps(
        {key: list(value["obligationIds"]) for key, value in _slot_definitions().items()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "termsPolicyVersion", "timezone",
    "populationPolicyHash", "node50ContractHash", "node51ReceiptPolicyHash",
    "obligationMatrixHash", "studyFrom", "studyTo", "sourceEvidence",
    "evidenceSetHash",
})
SOURCE_KEYS = frozenset({
    "sourceSlot", "componentId", "providerAlias", "sourceContractId",
    "datasetId", "endpointContractHash", "providerLegalIdentityHash",
    "schemaHash", "documentationEvidenceId", "documentationEvidenceHash",
    "obligationIds", "coverageClass", "effectiveSemanticsClass",
    "availabilityEvidenceClass", "revisionPublishedAt",
    "observationCompletedAt", "publicationSemanticsHash",
    "revisionIdentityScheme", "immutableRevision", "appendOnly",
    "correctionPolicy", "conflictStatus", "historyStart", "historyEnd",
    "expectedPages", "parsedPages", "expectedRecords", "parsedRecords",
    "rejectedRecords", "termsVersion", "termsHash", "termsStatus",
    "attributionVersion", "attributionHash", "attributionStatus",
    "legalPermissions", "evidenceContentHash",
})
RESULT_KEYS = frozenset({
    "sourceSlot", "candidateClass", "identityEvidenceComplete",
    "effectiveSemanticsEvidenceComplete", "availabilityEvidenceComplete",
    "legalUseEvidenceComplete", "obligationCount", "reasonCodes",
    "evidenceDigest",
})
REGISTRY_KEYS = frozenset({
    "schemaVersion", "policyVersion", "mode", "entries", "registryDigest",
})

LEGAL_PERMISSIONS = (
    "automated_access", "private_metadata_derivation", "private_retention",
)
HISTORICAL_AVAILABILITY = frozenset({
    "official_timezone_timestamp", "immutable_revision_documented",
})
REJECTED_TIME_BASES = frozenset({
    "unknown", "date_only", "retrieved_at", "generated_at",
    "http_date", "http_last_modified", "catalog_modified",
    "observation_date", "inferred_next_trading_day", "first_seen_backfill",
})
FIXED_BLOCKERS = (
    "official_source_admission_registry_empty",
    "source_admission_review_unimplemented",
    "node50_source_allowlist_unchanged",
    "trusted_artifact_attestation_unimplemented",
)
FORBIDDEN_KEYS = frozenset({
    "raw", "rows", "body", "headers", "url", "query", "token", "secret",
    "authorization", "cookie", "password", "price", "volume", "score",
    "rank", "return", "returns", "mdd", "pnl", "recommendation",
    "candidates", "admitted", "sourceadmitted", "pitcoveragecertified",
    "strategyvalidated", "promotioneligible", "adviceenabled", "firstseen",
    "retrievedat", "generatedat", "currenttime",
})
FORBIDDEN_TEXT = (
    "://", "bearer ", "authorization:", "token=", "password=", "cookie=",
    "-----begin ",
)


def source_slot_contract_hash(source_slot: str) -> str:
    return _digest({"sourceSlot": source_slot, **_slot_definitions()[source_slot]})


def empty_registry() -> dict[str, Any]:
    value = {
        "schemaVersion": REGISTRY_SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "entries": [],
    }
    value["registryDigest"] = _digest(value)
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
        and "?" not in value and "http" not in value.casefold()
    )


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or DATE_ONLY.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or DATE_ONLY.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _counts_complete(value: dict[str, Any]) -> bool:
    values = (
        value.get("expectedPages"), value.get("parsedPages"),
        value.get("expectedRecords"), value.get("parsedRecords"),
        value.get("rejectedRecords"),
    )
    return bool(
        all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in values)
        and value["expectedPages"] > 0
        and value["expectedPages"] == value["parsedPages"]
        and value["expectedRecords"] == value["parsedRecords"]
        and value["rejectedRecords"] == 0
    )


def _identity_complete(value: dict[str, Any], expected: Mapping[str, Any]) -> bool:
    return bool(
        value.get("componentId") == expected["componentId"]
        and value.get("providerAlias") == expected["providerAlias"]
        and value.get("sourceContractId") == expected["sourceContractId"]
        and value.get("datasetId") == expected["datasetId"]
        and value.get("endpointContractHash") == expected["endpointContractHash"]
        and _hex64(value.get("providerLegalIdentityHash"))
        and _hex64(value.get("schemaHash"))
        and _safe_id(value.get("documentationEvidenceId"))
        and _hex64(value.get("documentationEvidenceHash"))
        and value.get("obligationIds") == list(expected["obligationIds"])
        and _hex64(value.get("evidenceContentHash"))
    )


def _legal_complete(value: dict[str, Any]) -> bool:
    return bool(
        _safe_id(value.get("termsVersion")) and _hex64(value.get("termsHash"))
        and value.get("termsStatus") == "allowed"
        and _safe_id(value.get("attributionVersion"))
        and _hex64(value.get("attributionHash"))
        and value.get("attributionStatus") == "satisfied"
        and value.get("legalPermissions") == list(LEGAL_PERMISSIONS)
    )


def _effective_complete(value: dict[str, Any], expected: Mapping[str, Any]) -> bool:
    return value.get("effectiveSemanticsClass") == expected["requiredEffectiveSemantics"]


def _historical_availability_complete(value: dict[str, Any]) -> bool:
    return bool(
        value.get("availabilityEvidenceClass") in HISTORICAL_AVAILABILITY
        and _aware(value.get("revisionPublishedAt")) is not None
        and value.get("observationCompletedAt") is None
        and _hex64(value.get("publicationSemanticsHash"))
        and value.get("revisionIdentityScheme") == "official-immutable-version"
        and value.get("immutableRevision") is True
        and value.get("appendOnly") is True
        and value.get("correctionPolicy") == "append-only-supersedes"
        and value.get("conflictStatus") == "none"
    )


def _forward_availability_complete(value: dict[str, Any]) -> bool:
    return bool(
        value.get("availabilityEvidenceClass") == "observer_first_seen"
        and value.get("revisionPublishedAt") is None
        and _aware(value.get("observationCompletedAt")) is not None
        and value.get("revisionIdentityScheme") == "current-snapshot-no-history"
        and value.get("immutableRevision") is False
        and value.get("appendOnly") is True
        and value.get("correctionPolicy") == "append-only-observations"
        and value.get("conflictStatus") == "none"
    )


def _source_result(
    value: Any, study_from: date, study_to: date,
) -> tuple[dict[str, Any], bool]:
    source_slot = value.get("sourceSlot") if isinstance(value, dict) else "invalid-source"
    reasons: list[str] = []
    if not isinstance(value, dict) or set(value) != SOURCE_KEYS or source_slot not in SOURCE_SLOT_PINS:
        result = {
            "sourceSlot": source_slot if _safe_id(source_slot) else "invalid-source",
            "candidateClass": REJECTED,
            "identityEvidenceComplete": False,
            "effectiveSemanticsEvidenceComplete": False,
            "availabilityEvidenceComplete": False,
            "legalUseEvidenceComplete": False,
            "obligationCount": 0,
            "reasonCodes": ["source_contract_invalid"],
            "evidenceDigest": "",
        }
        return result, False
    expected = SOURCE_SLOT_PINS[source_slot]
    identity = _identity_complete(value, expected)
    effective = _effective_complete(value, expected)
    legal = _legal_complete(value)
    counts = _counts_complete(value)
    history_start = _date(value.get("historyStart"))
    history_end = _date(value.get("historyEnd"))
    history_complete = bool(
        value.get("coverageClass") == "complete_history"
        and history_start is not None and history_end is not None
        and history_start <= study_from <= study_to <= history_end
    )
    historical_availability = _historical_availability_complete(value)
    forward_availability = _forward_availability_complete(value)
    if not identity:
        reasons.append("identity_contract_incomplete")
    if not effective:
        reasons.append("effective_event_semantics_incomplete")
    if not legal:
        reasons.append("lawful_use_or_attribution_incomplete")
    if not counts:
        reasons.append("pagination_or_count_incomplete")
    if value.get("availabilityEvidenceClass") in REJECTED_TIME_BASES:
        reasons.append("prohibited_availability_substitute")
    capability = expected["maximumEvidenceCapability"]
    if (
        capability == "historical_archive"
        and historical_availability and history_complete
        and identity and effective and legal and counts
    ):
        candidate_class = HISTORICAL
        availability = True
    elif (
        capability == "forward_only"
        and forward_availability and value.get("coverageClass") == "forward_only"
        and identity and effective and legal and counts
    ):
        candidate_class = FORWARD
        availability = True
        reasons.append("forward_observation_not_historical_admission")
    else:
        candidate_class = REJECTED
        availability = False
        if capability == "forward_only" and value.get("coverageClass") != "forward_only":
            reasons.append("current_master_not_historical_capable")
        elif capability == "supplemental_exit_only":
            reasons.append("supplemental_exit_source_not_independently_admissible")
        elif capability == "unregistered":
            reasons.append("source_contract_unregistered")
        if not historical_availability and not forward_availability:
            reasons.append("authoritative_availability_incomplete")
        if value.get("coverageClass") == "complete_history" and not history_complete:
            reasons.append("history_coverage_incomplete")
    result = {
        "sourceSlot": source_slot,
        "candidateClass": candidate_class,
        "identityEvidenceComplete": identity,
        "effectiveSemanticsEvidenceComplete": effective,
        "availabilityEvidenceComplete": availability,
        "legalUseEvidenceComplete": legal,
        "obligationCount": len(expected["obligationIds"]),
        "reasonCodes": sorted(set(reasons)),
        "evidenceDigest": _digest(value),
    }
    return result, True


def _report(
    blockers: list[str], *, results: list[dict[str, Any]] | None = None,
    input_digest: str = "", contract_valid: bool = False,
) -> dict[str, Any]:
    source_results = results or []
    counts = {HISTORICAL: 0, FORWARD: 0, REJECTED: 0}
    for row in source_results:
        counts[row["candidateClass"]] += 1
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "contractStructurallyValid": contract_valid,
        "historicalEvidenceShapeComplete": bool(
            contract_valid and len(source_results) == len(SOURCE_SLOTS)
            and counts[HISTORICAL] == len(SOURCE_SLOTS)
        ),
        "officialIdentityReady": False,
        "authoritativeAvailabilityReady": False,
        "node50AdmissionEligible": False,
        "sourceAllowlistEligible": False,
        "sourceAdmitted": False,
        "historicalEligible": False,
        "forwardEligible": False,
        "officialProducerRegistered": False,
        "pitCoverageCertified": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "trustedReceipt": False,
        "registryEligible": False,
        "formalGateAttached": False,
        "registryEntryCount": 0,
        "requiredSourceCount": len(SOURCE_SLOTS),
        "obligationCount": len(OBLIGATIONS),
        "historicalCandidateCount": counts[HISTORICAL],
        "forwardObservedOnlyCount": counts[FORWARD],
        "rejectedUnknownCount": counts[REJECTED],
        "sourceResults": source_results,
        "inputDigest": input_digest,
        "blockers": list(dict.fromkeys([*FIXED_BLOCKERS, *blockers])),
    }
    report["reportDigest"] = _digest(report)
    return report


def _evaluate(value: Any) -> dict[str, Any]:
    if not _json_domain(value) or not isinstance(value, dict):
        return _report(["input_not_bounded_json"])
    encoded = _canonical(value).encode("utf-8")
    if len(encoded) > MAX_CANONICAL_BYTES:
        return _report(["input_too_large"])
    if set(value) != ROOT_KEYS or _contains_forbidden(value):
        return _report(["input_contract_invalid"])
    study_from = _date(value.get("studyFrom"))
    study_to = _date(value.get("studyTo"))
    root_valid = bool(
        value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("policyVersion") == POLICY_VERSION
        and value.get("termsPolicyVersion") == TERMS_POLICY_VERSION
        and value.get("timezone") == TIMEZONE
        and value.get("populationPolicyHash") == PINNED_POPULATION_POLICY_HASH
        and value.get("node50ContractHash") == PINNED_NODE50_CONTRACT_HASH
        and value.get("node51ReceiptPolicyHash") == PINNED_NODE51_RECEIPT_POLICY_HASH
        and value.get("obligationMatrixHash") == OBLIGATION_MATRIX_HASH
        and study_from is not None and study_to is not None and study_from <= study_to
    )
    sources = value.get("sourceEvidence")
    if not isinstance(sources, list):
        return _report(["root_contract_invalid"])
    ordered = sorted(
        sources,
        key=lambda row: row.get("sourceSlot", "") if isinstance(row, dict) else "",
    )
    evidence_hash_valid = value.get("evidenceSetHash") == _digest(ordered)
    slots = [row.get("sourceSlot") for row in ordered if isinstance(row, dict)]
    slot_set_valid = len(slots) == len(SOURCE_SLOTS) and set(slots) == set(SOURCE_SLOTS)
    results: list[dict[str, Any]] = []
    entries_valid = True
    if study_from is not None and study_to is not None:
        for row in ordered:
            result, valid = _source_result(row, study_from, study_to)
            results.append(result)
            entries_valid = entries_valid and valid
    else:
        entries_valid = False
    blockers: list[str] = []
    if not root_valid:
        blockers.append("root_contract_invalid")
    if not evidence_hash_valid:
        blockers.append("evidence_set_hash_mismatch")
    if not slot_set_valid:
        blockers.append("required_source_slot_missing_or_duplicate")
    if not entries_valid:
        blockers.append("source_evidence_contract_invalid")
    if PINNED_NODE50_SOURCE_ADMISSION_COUNT != 0:
        blockers.append("node50_source_allowlist_not_empty")
    contract_valid = not blockers
    normalized_value = {**value, "sourceEvidence": ordered}
    return _report(
        blockers, results=results, input_digest=_digest(normalized_value),
        contract_valid=contract_valid,
    )


def evaluate(value: Any) -> dict[str, Any]:
    """Public pure fail-closed boundary."""
    try:
        return _evaluate(value)
    except Exception:
        return _report(["input_fail_closed"])


def run(value: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off boundary; disabled mode does not inspect ``value``."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "sourceAdmitted": False,
            "historicalEligible": False,
            "registryEligible": False,
        }
    return evaluate(value)
