"""Offline, fail-closed structural preflight for authoritative PIT coverage.

This node intentionally cannot certify a real bundle yet.  It verifies the
shape, fixed denominator, market-population semantics, replay cross-hashes,
and point-in-time metadata that a future independently admitted bundle must
contain.  The real official-universe producer, artifact attestation, and
append-only registry admission procedure are deliberately unregistered.  No
caller-supplied bundle or anchor can therefore set a certification flag.

Passing the structural preflight does not certify normalized values, strategy
performance, investment advice, or even the authenticity of the fixture.  It
only proves that the proposed contract fails closed before a trust root exists.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import production_strategy_validation_preflight as strategy_preflight
from market_population_contract import (
    POPULATION_POLICY_VERSION, population_policy_view,
    population_policy_hash as _shared_population_policy_hash,
)


SCHEMA_VERSION = 1
POLICY_VERSION = "authoritative-pit-coverage-certification-v1"
REGISTRY_POLICY_VERSION = "trusted-pit-bundle-registry-v1"
REGISTRY_PATH = Path(__file__).with_name("trusted_pit_bundle_registry_v1.json")
TIMEZONE = "Asia/Taipei"
MARKET_SCOPE = ("TWSE", "TPEx", "emerging", "delisted/terminated")
POPULATION_POLICY = population_policy_view()
REQUIRED_COMPONENTS = tuple(POPULATION_POLICY["marketComponents"])
COMPONENT_MARKETS = {
    "twse_active": "TWSE",
    "tpex_active": "TPEx",
    "emerging_active": "emerging",
    "membership_events": "all",
}
MEMBERSHIP_STATES = frozenset(POPULATION_POLICY["includedStates"])
# No current repository producer is allowed to authenticate a full historical
# TWSE/TPEx/emerging/delisted population.  In particular,
# market_membership_snapshots.py is a traded-observation diagnostic that may
# omit suspended/zero-volume instruments.  A future node must register and
# independently attest a real official-universe producer before certification
# can have a true path.
OFFICIAL_UNIVERSE_PRODUCER_ALLOWLIST = frozenset()

CERTIFICATION_PRODUCER_PINS = {
    **strategy_preflight.EVIDENCE_PRODUCER_PINS,
    "official_availability_capability.py": "dba9b99b0026236d0a4ed3e86f07d72c5e248e201746b0c6675dd041887e932b",
    "pit_availability_evidence.py": "c64fbbfdfb06dcdfc67eb45e8ee3acca70eb561a945523ead9edf5614d701fb2",
    "official_announcement_availability.py": "3024f72313372184c9d206ea6849a9fc19954c26cd4eab112b5082c30f585149",
    "production_strategy_validation_preflight.py": "d4bf6b7dea61a2b30512731ee8a99955c907d1dbd78aa39b1d3412c154e62462",
}

ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "strategySpecHash",
    "pitRequirementsHash", "populationPolicyHash", "producerPins", "scope",
    "universes", "records", "lineage",
})
SCOPE_KEYS = frozenset({
    "marketScope", "timezone", "studyFrom", "studyTo", "decisionAsOfs",
    "decisionCalendarHash", "studyCalendarPolicyVersion",
    "studyCalendarRegistered",
})
UNIVERSE_KEYS = frozenset({
    "decisionAsOf", "schemaVersion", "populationPolicyVersion",
    "populationPolicyHash", "components", "componentSetHash", "memberships",
    "entities", "entitySetHash", "entityCount",
})
COMPONENT_KEYS = frozenset({
    "componentId", "market", "source", "dataset", "schemaVersion",
    "effectiveAt", "availableAt", "availableAtEvidenceClass",
    "availableAtEvidenceId", "sourceRevision", "producer", "producerHash",
    "contentHash", "schemaHash", "evidenceHash", "selectedVersionHash",
    "entityCount", "entitySetHash", "entities", "quality", "conflictStatus",
    "frozenDigest", "replayDigest",
})
MEMBERSHIP_KEYS = frozenset({
    "entityId", "market", "status", "entryEffectiveAt", "exitEffectiveAt",
    "marketComponentId", "eventComponentId", "selectedVersionHash",
})
RECORD_KEYS = frozenset({
    "decisionAsOf", "entityId", "requirementId", "status", "source",
    "dataset", "schemaVersion", "effectiveAt", "availableAt",
    "availableAtEvidenceClass", "availableAtEvidenceId", "evidenceHash",
    "authorityContractHash", "producer", "producerHash", "sourceRevision",
    "contentHash", "schemaHash", "quality", "conflictStatus", "evidenceRole",
    "selectedVersionHash", "frozenDigest", "replayDigest",
})
LINEAGE_KEYS = frozenset({
    "schemaVersion", "frozenSummary", "frozenDigest", "replaySummary",
    "replayDigest", "availabilitySummary", "availabilityDigest",
    "selectedVersionSetHash",
})
FROZEN_KEYS = frozenset({
    "schemaVersion", "policyVersion", "decisionCalendarHash",
    "populationPolicyHash", "componentSetHash", "coverageMatrixHash",
})
REPLAY_KEYS = frozenset({
    "schemaVersion", "policyVersion", "frozenDigest",
    "selectedVersionSetHash", "coverageMatrixHash",
})
AVAILABILITY_KEYS = frozenset({
    "schemaVersion", "policyVersion", "acceptedEvidenceClasses",
    "evidenceSetHash",
})
REGISTRY_KEYS = frozenset({"schemaVersion", "policyVersion", "entries"})

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENTITY_ID = re.compile(r"^[0-9]{4}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:+/-]{1,120}$")
OFFICIAL_AVAILABLE_CLASSES = frozenset({
    "official_timezone_timestamp", "immutable_revision_documented",
})
FORBIDDEN_KEYS = frozenset({
    "score", "rank", "return", "returns", "performance", "pnl", "profit",
    "loss", "mdd", "recommendation", "candidates", "candidateorder", "raw",
    "rows", "url", "token", "secret", "authorization", "cookie", "password",
})


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def population_policy_hash() -> str:
    return _shared_population_policy_hash()


def pit_requirements_hash() -> str:
    return strategy_preflight.digest({
        "requirements": list(strategy_preflight.REQUIRED_REQUIREMENTS),
        "requirementPolicies": strategy_preflight.REQUIREMENT_POLICIES,
        "authorityContracts": strategy_preflight.AUTHORITY_CONTRACTS,
        "producerPins": strategy_preflight.EVIDENCE_PRODUCER_PINS,
        "recordKeys": sorted(strategy_preflight.ALLOWED_RECORD_KEYS),
        "universeKeys": sorted(strategy_preflight.ALLOWED_UNIVERSE_KEYS),
    })


def _json_domain(value: Any, *, max_nodes: int = 200_000) -> bool:
    """Reject hostile/non-JSON values before hashing, sorting, or set use."""
    seen: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > 14:
            return False
        if item is None or isinstance(item, (bool, str)):
            return not isinstance(item, str) or len(item) <= 512
        if isinstance(item, int) and not isinstance(item, bool):
            return len(str(abs(item))) <= 64
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            try:
                if isinstance(item, dict):
                    if len(item) > 100_000 or any(not isinstance(key, str) for key in item):
                        return False
                    return all(visit(child, depth + 1) for child in item.values())
                if len(item) > 500_000:
                    return False
                return all(visit(child, depth + 1) for child in item)
            finally:
                seen.remove(identity)
        return False

    return visit(value, 0)


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _instant(value: Any) -> str | None:
    parsed = _aware(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def _safe_text(value: Any) -> bool:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        return False
    lowered = value.lower()
    return not (
        any(ord(char) < 32 or ord(char) == 127 for char in value)
        or "http://" in lowered or "https://" in lowered or "?" in value
        or "token" in lowered or "secret" in lowered
        or "authorization" in lowered or "cookie" in lowered
        or "bearer" in lowered or "{" in value or "[" in value
    )


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.replace("_", "").lower() in FORBIDDEN_KEYS:
                return True
            if _contains_forbidden(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    elif isinstance(value, str):
        lowered = value.lower()
        return (
            any(ord(char) < 32 and char not in "\t" for char in value)
            or "http://" in lowered or "https://" in lowered
            or "token=" in lowered or "authorization:" in lowered
            or "bearer " in lowered
        )
    return False


def _safe_digest(value: Any) -> str:
    try:
        return digest(value) if _json_domain(value) else digest({})
    except (TypeError, ValueError, OverflowError, RecursionError):
        return digest({})


def _load_registry() -> tuple[bool, list[str]]:
    """Only the committed empty scaffold is supported in this node."""
    try:
        raw = REGISTRY_PATH.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > 256_000:
            return False, ["trusted_root_registry_oversize"]
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, ["trusted_root_registry_unreadable"]
    if (
        not _json_domain(value)
        or not isinstance(value, dict)
        or set(value) != REGISTRY_KEYS
        or value.get("schemaVersion") != 1
        or value.get("policyVersion") != REGISTRY_POLICY_VERSION
        or not isinstance(value.get("entries"), list)
    ):
        return False, ["trusted_root_registry_contract_invalid"]
    if value["entries"]:
        return False, ["trusted_root_admission_unimplemented"]
    return True, [
        "trusted_root_registry_empty", "registry_append_only_admission_unimplemented",
    ]


def _scope(bundle: dict[str, Any], blockers: list[str]) -> tuple[list[str], str | None]:
    scope = bundle.get("scope")
    if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS:
        blockers.append("scope_contract_invalid")
        return [], None
    decisions = scope.get("decisionAsOfs")
    study_from = _date(scope.get("studyFrom"))
    study_to = _date(scope.get("studyTo"))
    if (
        scope.get("marketScope") != list(MARKET_SCOPE)
        or scope.get("timezone") != TIMEZONE
        or scope.get("studyCalendarRegistered") is not True
        or not _safe_text(scope.get("studyCalendarPolicyVersion"))
        or study_from is None or study_to is None or study_from > study_to
        or not isinstance(decisions, list) or not decisions
        or any(not isinstance(item, str) for item in decisions)
    ):
        blockers.append("study_scope_unregistered_or_invalid")
        return [], None
    normalized = [_instant(item) for item in decisions]
    if any(item is None for item in normalized) or len(set(normalized)) != len(normalized):
        blockers.append("decision_calendar_invalid")
        return [], None
    if normalized != sorted(normalized):
        blockers.append("decision_calendar_not_canonical")
    local_dates = [_aware(item).astimezone(_aware("2000-01-01T00:00:00+08:00").tzinfo).date().isoformat()
                   for item in decisions]
    if any(item < study_from or item > study_to for item in local_dates):
        blockers.append("decision_outside_study_scope")
    expected_hash = digest({
        "timezone": TIMEZONE,
        "policyVersion": scope["studyCalendarPolicyVersion"],
        "decisionAsOfs": normalized,
    })
    if scope.get("decisionCalendarHash") != expected_hash:
        blockers.append("decision_calendar_hash_mismatch")
    return [item for item in normalized if item is not None], expected_hash


def _authority_for(requirement: str) -> tuple[str, dict[str, Any]]:
    policy = strategy_preflight.REQUIREMENT_POLICIES[requirement]
    name = policy["authorityContract"]
    return name, strategy_preflight.AUTHORITY_CONTRACTS[name]


def _lineage(bundle: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    lineage = bundle.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != LINEAGE_KEYS:
        blockers.append("lineage_contract_invalid")
        return {}
    frozen = lineage.get("frozenSummary")
    replay = lineage.get("replaySummary")
    availability = lineage.get("availabilitySummary")
    if not isinstance(frozen, dict) or set(frozen) != FROZEN_KEYS:
        blockers.append("frozen_summary_contract_invalid")
    if not isinstance(replay, dict) or set(replay) != REPLAY_KEYS:
        blockers.append("replay_summary_contract_invalid")
    if not isinstance(availability, dict) or set(availability) != AVAILABILITY_KEYS:
        blockers.append("availability_summary_contract_invalid")
    if blockers:
        return lineage
    if (
        lineage.get("schemaVersion") != 1
        or frozen.get("schemaVersion") != 1
        or replay.get("schemaVersion") != 1
        or availability.get("schemaVersion") != 1
        or frozen.get("policyVersion") != "frozen-lineage-v1"
        or replay.get("policyVersion") != "lineage-replay-v1"
        or availability.get("policyVersion") != "official-availability-capability-v1"
        or availability.get("acceptedEvidenceClasses") != sorted(OFFICIAL_AVAILABLE_CLASSES)
        or lineage.get("frozenDigest") != digest(frozen)
        or lineage.get("replayDigest") != digest(replay)
        or lineage.get("availabilityDigest") != digest(availability)
        or replay.get("frozenDigest") != lineage.get("frozenDigest")
        or any(not isinstance(lineage.get(name), str) or HEX64.fullmatch(lineage[name]) is None
               for name in ("frozenDigest", "replayDigest", "availabilityDigest",
                            "selectedVersionSetHash"))
    ):
        blockers.append("lineage_replay_cross_hash_invalid")
    return lineage


def _component_valid(
    component: dict[str, Any], decision: datetime, lineage: dict[str, Any],
) -> bool:
    if set(component) != COMPONENT_KEYS:
        return False
    entities = component.get("entities")
    if (
        component.get("componentId") not in REQUIRED_COMPONENTS
        or component.get("market") != COMPONENT_MARKETS.get(component.get("componentId"))
        or not isinstance(entities, list)
        or any(not isinstance(entity, str) or ENTITY_ID.fullmatch(entity) is None for entity in entities)
        or entities != sorted(entities) or len(set(entities)) != len(entities)
        or component.get("entityCount") != len(entities)
        or component.get("entitySetHash") != digest(entities)
        or component.get("schemaVersion") != 1
        or not all(_safe_text(component.get(name)) for name in (
            "source", "dataset", "availableAtEvidenceId", "sourceRevision", "producer",
        ))
        or component.get("availableAtEvidenceClass") not in OFFICIAL_AVAILABLE_CLASSES
        or component.get("quality") != "verified"
        or component.get("conflictStatus") != "no_conflict"
        or any(not isinstance(component.get(name), str) or HEX64.fullmatch(component[name]) is None
               for name in ("producerHash", "contentHash", "schemaHash", "evidenceHash",
                            "selectedVersionHash"))
        or component.get("frozenDigest") != lineage.get("frozenDigest")
        or component.get("replayDigest") != lineage.get("replayDigest")
    ):
        return False
    available = _aware(component.get("availableAt"))
    effective = _aware(component.get("effectiveAt"))
    return bool(available and effective and available <= decision and effective <= decision)


def _population(
    universe: dict[str, Any], decision: datetime, lineage: dict[str, Any],
    blockers: list[str],
) -> tuple[list[str], list[str]]:
    if set(universe) != UNIVERSE_KEYS or universe.get("schemaVersion") != 1:
        blockers.append("official_universe_contract_invalid")
        return [], []
    if (
        universe.get("populationPolicyVersion") != POPULATION_POLICY_VERSION
        or universe.get("populationPolicyHash") != population_policy_hash()
    ):
        blockers.append("population_policy_mismatch")
    components = universe.get("components")
    memberships = universe.get("memberships")
    entities = universe.get("entities")
    if not isinstance(components, list) or not isinstance(memberships, list) \
            or not isinstance(entities, list):
        blockers.append("population_contract_invalid")
        return [], []
    by_component: dict[str, dict[str, Any]] = {}
    for component in components:
        if not isinstance(component, dict) or not _component_valid(component, decision, lineage):
            blockers.append("official_population_component_invalid")
            continue
        component_id = component["componentId"]
        if component_id in by_component:
            blockers.append("official_population_component_not_unique")
        by_component[component_id] = component
    if set(by_component) != set(REQUIRED_COMPONENTS):
        blockers.append("official_population_component_missing")
    component_set = [
        {
            "componentId": component_id,
            "entityCount": component["entityCount"],
            "entitySetHash": component["entitySetHash"],
            "selectedVersionHash": component["selectedVersionHash"],
        }
        for component_id, component in sorted(by_component.items())
    ]
    component_set_hash = digest(component_set)
    if universe.get("componentSetHash") != component_set_hash:
        blockers.append("population_component_set_hash_mismatch")

    membership_entities: list[str] = []
    membership_hashes: list[str] = []
    seen: set[str] = set()
    for membership in memberships:
        if not isinstance(membership, dict) or set(membership) != MEMBERSHIP_KEYS:
            blockers.append("membership_interval_contract_invalid")
            continue
        entity = membership.get("entityId")
        market = membership.get("market")
        entry = _aware(membership.get("entryEffectiveAt"))
        exit_value = membership.get("exitEffectiveAt")
        exit_at = _aware(exit_value) if exit_value is not None else None
        market_component = membership.get("marketComponentId")
        valid = (
            isinstance(entity, str) and ENTITY_ID.fullmatch(entity) is not None
            and entity not in seen
            and market in ("TWSE", "TPEx", "emerging")
            and membership.get("status") in MEMBERSHIP_STATES
            and market_component in by_component
            and COMPONENT_MARKETS.get(market_component) == market
            and membership.get("eventComponentId") == "membership_events"
            and entity in by_component.get(market_component, {}).get("entities", [])
            and entity in by_component.get("membership_events", {}).get("entities", [])
            and entry is not None and entry <= decision
            and (exit_value is None or (exit_at is not None and decision < exit_at))
            and isinstance(membership.get("selectedVersionHash"), str)
            and HEX64.fullmatch(membership["selectedVersionHash"]) is not None
        )
        if not valid:
            blockers.append("membership_interval_invalid")
            continue
        seen.add(entity)
        membership_entities.append(entity)
        membership_hashes.append(membership["selectedVersionHash"])
    membership_entities.sort()
    union = sorted(
        entity
        for component_id in ("twse_active", "tpex_active", "emerging_active")
        for entity in by_component.get(component_id, {}).get("entities", [])
    )
    events = by_component.get("membership_events", {}).get("entities", [])
    if (
        union != membership_entities or events != membership_entities
        or entities != membership_entities
        or universe.get("entityCount") != len(entities)
        or universe.get("entitySetHash") != digest(entities)
    ):
        blockers.append("official_universe_denominator_invalid")
    return membership_entities, membership_hashes


def _record_valid(
    record: dict[str, Any], decision: datetime, requirement: str,
    lineage: dict[str, Any], blockers: list[str],
) -> bool:
    contract_name, authority = _authority_for(requirement)
    status = record.get("status")
    available = _aware(record.get("availableAt"))
    effective = _aware(record.get("effectiveAt"))
    valid = (
        set(record) == RECORD_KEYS
        and record.get("source") == authority["source"]
        and record.get("dataset") == authority["dataset"]
        and record.get("producer") == authority["producer"]
        and record.get("producerHash") == CERTIFICATION_PRODUCER_PINS.get(authority["producer"])
        and record.get("authorityContractHash") == strategy_preflight.authority_contract_hash(contract_name)
        and status in strategy_preflight.ALLOWED_STATES_BY_REQUIREMENT[requirement]
        and record.get("evidenceRole") == strategy_preflight.REQUIREMENT_POLICIES[requirement]["states"].get(status)
        and record.get("availableAtEvidenceClass") in OFFICIAL_AVAILABLE_CLASSES
        and _safe_text(record.get("availableAtEvidenceId"))
        and _safe_text(record.get("sourceRevision"))
        and record.get("schemaVersion") == 1
        and available is not None and available <= decision
        and effective is not None and effective <= decision
        and record.get("quality") == "verified"
        and record.get("conflictStatus") == "no_conflict"
        and all(isinstance(record.get(name), str) and HEX64.fullmatch(record[name]) is not None
                for name in ("evidenceHash", "contentHash", "schemaHash", "selectedVersionHash"))
        and record.get("frozenDigest") == lineage.get("frozenDigest")
        and record.get("replayDigest") == lineage.get("replayDigest")
    )
    if not valid:
        blockers.append("authoritative_provenance_invalid")
    return bool(valid)


def assess_structure(bundle: Any) -> dict[str, Any]:
    """Assess structural eligibility; this helper can never certify a bundle."""
    blockers: list[str] = []
    if not _json_domain(bundle) or not isinstance(bundle, dict):
        bundle = {}
        blockers.append("input_not_bounded_json")
    elif set(bundle) != ROOT_KEYS or _contains_forbidden(bundle):
        bundle = {}
        blockers.append("bundle_contract_invalid")
    root_digest = _safe_digest(bundle)
    spec_hash = strategy_preflight.strategy_spec_hash()
    requirements_hash = pit_requirements_hash()
    if bundle.get("schemaVersion") != 1 or bundle.get("policyVersion") != POLICY_VERSION:
        blockers.append("bundle_version_mismatch")
    if bundle.get("strategySpecHash") != spec_hash:
        blockers.append("strategy_spec_pin_mismatch")
    if bundle.get("pitRequirementsHash") != requirements_hash:
        blockers.append("pit_requirements_pin_mismatch")
    if bundle.get("populationPolicyHash") != population_policy_hash():
        blockers.append("population_policy_mismatch")
    if bundle.get("producerPins") != CERTIFICATION_PRODUCER_PINS:
        blockers.append("producer_pin_mismatch")

    decisions, calendar_hash = _scope(bundle, blockers)
    lineage = _lineage(bundle, blockers)
    universes = bundle.get("universes") if isinstance(bundle.get("universes"), list) else []
    by_decision: dict[str, list[dict[str, Any]]] = {}
    for universe in universes:
        if not isinstance(universe, dict):
            blockers.append("official_universe_contract_invalid")
            continue
        identity = _instant(universe.get("decisionAsOf"))
        if identity is None:
            blockers.append("official_universe_decision_invalid")
            continue
        by_decision.setdefault(identity, []).append(universe)

    expected_pairs: set[tuple[str, str, str]] = set()
    membership_hashes: list[str] = []
    component_set_hashes: list[str] = []
    universe_entities = 0
    for identity in decisions:
        items = by_decision.get(identity, [])
        if len(items) != 1:
            blockers.append("official_universe_not_unique")
            continue
        decision = _aware(identity)
        if decision is None:
            blockers.append("official_universe_decision_invalid")
            continue
        entities, selected_memberships = _population(items[0], decision, lineage, blockers)
        membership_hashes.extend(selected_memberships)
        if isinstance(items[0].get("componentSetHash"), str):
            component_set_hashes.append(items[0]["componentSetHash"])
        universe_entities += len(entities)
        expected_pairs.update(
            (identity, entity, requirement)
            for entity in entities
            for requirement in strategy_preflight.REQUIRED_REQUIREMENTS
        )
    if set(by_decision) != set(decisions):
        blockers.append("official_universe_scope_drift")

    records = bundle.get("records") if isinstance(bundle.get("records"), list) else []
    by_pair: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            blockers.append("coverage_record_contract_invalid")
            continue
        identity = _instant(record.get("decisionAsOf"))
        entity = record.get("entityId")
        requirement = record.get("requirementId")
        if not isinstance(entity, str) or ENTITY_ID.fullmatch(entity) is None \
                or not isinstance(requirement, str):
            blockers.append("coverage_record_identity_invalid")
            continue
        by_pair.setdefault((identity or "", entity, requirement), []).append(record)

    status_counts = {state: 0 for state in sorted(strategy_preflight.ALL_STATES)}
    selected_hashes = list(membership_hashes)
    valid_pairs = 0
    scoring_inputs_complete = True
    for pair in expected_pairs:
        items = by_pair.get(pair, [])
        if len(items) != 1:
            blockers.append("pit_version_missing_or_not_unique")
            scoring_inputs_complete = False
            continue
        identity, entity, requirement = pair
        decision = _aware(identity)
        record = items[0]
        status = record.get("status")
        if (
            decision is None
            or record.get("entityId") != entity
            or requirement not in strategy_preflight.REQUIRED_REQUIREMENTS
            or status not in strategy_preflight.ALLOWED_STATES_BY_REQUIREMENT[requirement]
        ):
            blockers.append("pit_requirement_state_invalid")
            scoring_inputs_complete = False
            continue
        if not _record_valid(record, decision, requirement, lineage, blockers):
            scoring_inputs_complete = False
            continue
        valid_pairs += 1
        status_counts[status] += 1
        selected_hashes.append(record["selectedVersionHash"])
        if requirement in (
            strategy_preflight.SELECTION_OBSERVED_REQUIREMENTS
            | strategy_preflight.FUNDAMENTAL_REQUIREMENTS
        ) and status != "observed_as_of":
            scoring_inputs_complete = False
    if set(by_pair) != expected_pairs:
        blockers.append("coverage_matrix_scope_drift")

    expected = len(expected_pairs)
    coverage_matrix_hash = digest([
        {"decisionAsOf": decision, "entityId": entity, "requirementId": requirement,
         "selectedVersionHash": by_pair[(decision, entity, requirement)][0]["selectedVersionHash"]}
        for decision, entity, requirement in sorted(expected_pairs)
        if len(by_pair.get((decision, entity, requirement), [])) == 1
        and isinstance(by_pair[(decision, entity, requirement)][0].get("selectedVersionHash"), str)
    ])
    selected_set_hash = digest(sorted(selected_hashes))
    frozen = lineage.get("frozenSummary", {}) if isinstance(lineage, dict) else {}
    replay = lineage.get("replaySummary", {}) if isinstance(lineage, dict) else {}
    availability = lineage.get("availabilitySummary", {}) if isinstance(lineage, dict) else {}
    if (
        frozen.get("decisionCalendarHash") != calendar_hash
        or frozen.get("populationPolicyHash") != population_policy_hash()
        or frozen.get("componentSetHash") != digest(sorted(component_set_hashes))
        or frozen.get("coverageMatrixHash") != coverage_matrix_hash
        or replay.get("coverageMatrixHash") != coverage_matrix_hash
        or replay.get("selectedVersionSetHash") != selected_set_hash
        or lineage.get("selectedVersionSetHash") != selected_set_hash
        or availability.get("evidenceSetHash") != digest(sorted(
            record.get("evidenceHash") for record in records
            if isinstance(record, dict) and isinstance(record.get("evidenceHash"), str)
        ))
    ):
        blockers.append("lineage_replay_evidence_mismatch")

    exact_coverage = expected > 0 and valid_pairs == expected and len(by_pair) == expected
    if not exact_coverage:
        blockers.append("authoritative_pit_coverage_incomplete")
    structural_blockers = list(dict.fromkeys(blockers))
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "structuralCoverageComplete": not structural_blockers,
        "scoringInputsMetadataComplete": not structural_blockers and scoring_inputs_complete,
        "pitCoverageCertified": False,
        "authoritativeProvenanceCoverageCertified": False,
        "scoringInputsReady": False,
        "valuesCertified": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "readyForPerformanceEvaluation": False,
        "formalGateAttached": False,
        "strategySpecHash": spec_hash,
        "pitRequirementsHash": requirements_hash,
        "populationPolicyHash": population_policy_hash(),
        "bundleRootDigest": root_digest,
        "decisionCalendarHash": calendar_hash,
        "scopeHash": _safe_digest(bundle.get("scope", {})),
        "coverage": {
            "decisionInstants": len(decisions),
            "universeEntities": universe_entities,
            "requirementsPerEntity": len(strategy_preflight.REQUIRED_REQUIREMENTS),
            "expected": expected,
            "selected": valid_pairs,
            "missingOrInvalid": max(0, expected - valid_pairs),
            "coverageRate": valid_pairs / expected if expected else 0.0,
            "statusCounts": status_counts,
        },
        "blockers": structural_blockers,
    }
    report["reportDigest"] = digest(report)
    return report


def evaluate(bundle: Any) -> dict[str, Any]:
    """Public fixed-registry boundary; Node49 cannot admit a real trust root."""
    try:
        report = assess_structure(bundle)
        _, registry_blockers = _load_registry()
        report["blockers"] = list(dict.fromkeys([
            *registry_blockers,
            "official_universe_producer_unregistered",
            "trusted_artifact_attestation_unimplemented",
            *report["blockers"],
        ]))
        report["pitCoverageCertified"] = False
        report["authoritativeProvenanceCoverageCertified"] = False
        report["scoringInputsReady"] = False
        report.pop("reportDigest", None)
        report["reportDigest"] = digest(report)
        return report
    except (TypeError, ValueError, OverflowError, RecursionError, KeyError, AttributeError):
        report = assess_structure({})
        report["blockers"] = list(dict.fromkeys([
            "input_fail_closed", "trusted_root_registry_empty", *report["blockers"],
        ]))
        report.pop("reportDigest", None)
        report["reportDigest"] = digest(report)
        return report


def run(bundle: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off boundary; disabled mode does not inspect bundle or registry."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "pitCoverageCertified": False,
            "strategyValidated": False,
            "promotionEligible": False,
        }
    return evaluate(bundle)
