"""Fixture-first assembler for a complete official Taiwan equity population.

The module defines how a future authoritative producer must combine TWSE,
TPEx, emerging-board, and membership-event evidence for one decision instant.
It is deliberately offline, default-off, and unregistered.  It can prove that
a fixture obeys the population contract, but it cannot authenticate a real
provider or certify PIT coverage, values, a strategy, or investment advice.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from market_population_contract import (
    POPULATION_POLICY_VERSION, population_policy_hash, population_policy_view,
)


SCHEMA_VERSION = 1
POLICY_VERSION = "official-full-market-population-producer-v1"
TIMEZONE = "Asia/Taipei"
POPULATION_POLICY = population_policy_view()
REQUIRED_COMPONENTS = (
    "twse_active", "tpex_active", "emerging_active", "membership_events",
)
ACTIVE_COMPONENTS = REQUIRED_COMPONENTS[:3]
COMPONENT_MARKETS = {
    "twse_active": "TWSE",
    "tpex_active": "TPEx",
    "emerging_active": "emerging",
    "membership_events": "all",
}
ALLOWED_MARKETS = frozenset({"TWSE", "TPEx", "emerging"})
ALLOWED_STATES = frozenset({"active", "suspended", "zero_volume"})
ALLOWED_SECURITY_CLASSES = frozenset({"common_equity"})
ACCEPTED_AVAILABILITY_CLASSES = frozenset({
    "official_timezone_timestamp", "immutable_revision_documented",
})

# Fixture-only source contracts.  They freeze parser/assembler semantics but
# do not authenticate a real endpoint, artifact, or official publication.
SOURCE_CONTRACTS = {
    "twse_active": {
        "sourceContractId": "fixture-twse-company-master-v1",
        "source": "TWSE-official-fixture",
        "dataset": "twse-company-master-fixture-v1",
        "producerId": "unregistered-official-population-producer",
    },
    "tpex_active": {
        "sourceContractId": "fixture-tpex-company-master-v1",
        "source": "TPEx-official-fixture",
        "dataset": "tpex-company-master-fixture-v1",
        "producerId": "unregistered-official-population-producer",
    },
    "emerging_active": {
        "sourceContractId": "fixture-emerging-company-master-v1",
        "source": "TPEx-official-fixture",
        "dataset": "emerging-company-master-fixture-v1",
        "producerId": "unregistered-official-population-producer",
    },
    "membership_events": {
        "sourceContractId": "fixture-membership-events-v1",
        "source": "TWSE-TPEx-official-fixture",
        "dataset": "listing-delisting-transfer-events-fixture-v1",
        "producerId": "unregistered-official-population-producer",
    },
}
OFFICIAL_SOURCE_ADMISSION_ALLOWLIST = frozenset()


ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "populationPolicyHash", "timezone",
    "decisionAsOf", "components",
})
COMPONENT_KEYS = frozenset({
    "componentId", "sourceContractId", "sourceContractHash", "source",
    "dataset", "schemaVersion", "producerId", "producerHash",
    "expectedRecordCount", "parsedRecordCount", "rejectedRecordCount",
    "pageCount", "parsedPageCount", "revisions",
})
REVISION_KEYS = frozenset({
    "revisionId", "supersedesRevisionId", "effectiveAt", "availableAt",
    "availabilityEvidenceClass", "availabilityEvidenceId",
    "publicationSemanticsHash", "schemaHash", "recordSetHash", "records",
    "revisionHash",
})
SECURITY_KEYS = frozenset({
    "securityId", "issuerId", "securityCode", "securityClass", "market",
    "status", "entryEffectiveAt", "exitEffectiveAt", "identityEvidenceHash",
    "selectedVersionHash",
})

HEX64 = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[0-9]{4}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:+/-]{1,120}$")
FORBIDDEN_KEYS = frozenset({
    "raw", "rows", "body", "url", "token", "secret", "authorization",
    "cookie", "password", "price", "volume", "fundamentals", "score",
    "rank", "return", "returns", "mdd", "pnl", "recommendation",
    "candidates", "candidateorder", "generatedat", "retrievedat",
    "firstseen", "httpheaders",
})


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def source_contract_hash(component_id: str) -> str:
    return digest({"componentId": component_id, **SOURCE_CONTRACTS[component_id]})


def security_schema_hash() -> str:
    return digest({
        "schemaVersion": 1,
        "keys": sorted(SECURITY_KEYS),
        "markets": sorted(ALLOWED_MARKETS),
        "states": sorted(ALLOWED_STATES),
        "securityClasses": sorted(ALLOWED_SECURITY_CLASSES),
    })


def _json_domain(value: Any, *, max_nodes: int = 500_000) -> bool:
    seen: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > 12:
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
                    return (
                        len(item) <= 100_000
                        and all(isinstance(key, str) for key in item)
                        and all(visit(child, depth + 1) for child in item.values())
                    )
                return len(item) <= 100_000 and all(visit(child, depth + 1) for child in item)
            finally:
                seen.remove(identity)
        return False

    return visit(value, 0)


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


def _safe(value: Any) -> bool:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        return False
    lowered = value.lower()
    return not (
        "http" in lowered or "token" in lowered or "secret" in lowered
        or "authorization" in lowered or "cookie" in lowered
        or "bearer" in lowered or "?" in value
    )


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


def _revision_hash(revision: dict[str, Any]) -> str:
    normalized = {
        key: value for key, value in revision.items()
        if key not in {"revisionHash", "records"}
    }
    records = revision.get("records", [])
    normalized["records"] = sorted(
        records, key=lambda row: (row["securityId"], row["securityCode"])
    ) if isinstance(records, list) and all(isinstance(row, dict) for row in records) else records
    return digest(normalized)


def _security_hash(record: dict[str, Any]) -> str:
    return digest({key: value for key, value in record.items() if key != "selectedVersionHash"})


def _security_valid(record: Any, component_market: str) -> bool:
    if not isinstance(record, dict) or set(record) != SECURITY_KEYS:
        return False
    entry = _aware(record.get("entryEffectiveAt"))
    exit_value = record.get("exitEffectiveAt")
    exit_at = _aware(exit_value) if exit_value is not None else None
    return bool(
        _safe(record.get("securityId"))
        and _safe(record.get("issuerId"))
        and isinstance(record.get("securityCode"), str)
        and CODE.fullmatch(record["securityCode"]) is not None
        and record.get("securityClass") in ALLOWED_SECURITY_CLASSES
        and record.get("market") in ALLOWED_MARKETS
        and (component_market == "all" or record.get("market") == component_market)
        and record.get("status") in ALLOWED_STATES
        and entry is not None
        and (exit_value is None or (exit_at is not None and entry < exit_at))
        and isinstance(record.get("identityEvidenceHash"), str)
        and HEX64.fullmatch(record["identityEvidenceHash"]) is not None
        and record.get("selectedVersionHash") == _security_hash(record)
    )


def _intervals_valid(records: list[dict[str, Any]]) -> bool:
    """A code may be reused only across exactly adjacent, non-overlapping intervals."""
    by_code: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_code.setdefault(record["securityCode"], []).append(record)
    for items in by_code.values():
        ordered = sorted(items, key=lambda row: _aware(row["entryEffectiveAt"]))
        for previous, current in zip(ordered, ordered[1:]):
            previous_exit = _aware(previous["exitEffectiveAt"])
            current_entry = _aware(current["entryEffectiveAt"])
            if previous_exit is None or current_entry is None or previous_exit != current_entry:
                return False
    return True


def _transition_valid(
    parent: dict[str, Any], child: dict[str, Any], blockers: list[str],
) -> bool:
    """Require append-only identity history and explicit replacement boundaries."""
    parent_records = parent["records"]
    child_records = child["records"]
    parent_by_id = {row["securityId"]: row for row in parent_records}
    child_by_id = {row["securityId"]: row for row in child_records}
    valid = True
    stable_fields = (
        "securityId", "issuerId", "securityCode", "securityClass", "market",
        "entryEffectiveAt", "identityEvidenceHash",
    )
    for security_id, old in parent_by_id.items():
        new = child_by_id.get(security_id)
        if new is None:
            valid = False
            continue
        if any(old[field] != new[field] for field in stable_fields):
            valid = False
        old_exit = _aware(old["exitEffectiveAt"]) if old["exitEffectiveAt"] else None
        new_exit = _aware(new["exitEffectiveAt"]) if new["exitEffectiveAt"] else None
        if old_exit is not None and new_exit != old_exit:
            valid = False
        if old_exit is None and new_exit is not None \
                and new_exit != _aware(child["effectiveAt"]):
            valid = False
    for security_id, new in child_by_id.items():
        if security_id in parent_by_id:
            continue
        new_entry = _aware(new["entryEffectiveAt"])
        if new_entry != _aware(child["effectiveAt"]):
            valid = False
            continue
        replaced = [
            row for row in child_records
            if row["securityCode"] == new["securityCode"]
            and row["securityId"] != security_id
        ]
        if replaced and not any(
            _aware(row["exitEffectiveAt"]) == new_entry
            for row in replaced if row["exitEffectiveAt"] is not None
        ):
            valid = False
    if not _intervals_valid(child_records):
        valid = False
    if not valid:
        blockers.append("identity_transition_invalid")
    return valid


def _selected_revision(
    component: dict[str, Any], decision: datetime, blockers: list[str],
) -> dict[str, Any] | None:
    revisions = component.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        blockers.append("component_revision_missing")
        return None
    by_id: dict[str, dict[str, Any]] = {}
    available_times: dict[str, datetime] = {}
    for revision in revisions:
        if not isinstance(revision, dict) or set(revision) != REVISION_KEYS:
            blockers.append("revision_contract_invalid")
            continue
        revision_id = revision.get("revisionId")
        available = _aware(revision.get("availableAt"))
        effective = _aware(revision.get("effectiveAt"))
        records = revision.get("records")
        valid = (
            _safe(revision_id)
            and revision_id not in by_id
            and available is not None and effective is not None
            and revision.get("availabilityEvidenceClass") in ACCEPTED_AVAILABILITY_CLASSES
            and _safe(revision.get("availabilityEvidenceId"))
            and isinstance(revision.get("publicationSemanticsHash"), str)
            and HEX64.fullmatch(revision["publicationSemanticsHash"]) is not None
            and revision.get("schemaHash") == security_schema_hash()
            and isinstance(records, list)
            and all(_security_valid(record, COMPONENT_MARKETS[component["componentId"]])
                    for record in records)
        )
        if not valid:
            blockers.append("revision_evidence_or_records_invalid")
            continue
        ordered_records = sorted(records, key=lambda row: (row["securityId"], row["securityCode"]))
        if len({row["securityId"] for row in records}) != len(records) \
                or not _intervals_valid(records):
            blockers.append("revision_security_identity_not_unique")
        if revision.get("recordSetHash") != digest(ordered_records) \
                or revision.get("revisionHash") != _revision_hash(revision):
            blockers.append("revision_content_hash_mismatch")
        by_id[revision_id] = revision
        available_times[revision_id] = available
    if len(by_id) != len(revisions):
        return None
    for revision_id, revision in by_id.items():
        parent = revision.get("supersedesRevisionId")
        if parent is None:
            continue
        if parent not in by_id or parent == revision_id \
                or available_times[parent] >= available_times[revision_id]:
            blockers.append("revision_supersedes_chain_invalid")
    if sum(revision.get("supersedesRevisionId") is None for revision in by_id.values()) != 1:
        blockers.append("revision_supersedes_chain_invalid")
    for revision in by_id.values():
        parent_id = revision.get("supersedesRevisionId")
        if parent_id in by_id:
            _transition_valid(by_id[parent_id], revision, blockers)
    eligible = {
        revision_id: revision for revision_id, revision in by_id.items()
        if available_times[revision_id] <= decision
    }
    superseded = {
        revision["supersedesRevisionId"] for revision in eligible.values()
        if revision.get("supersedesRevisionId") in eligible
    }
    terminals = [revision for revision_id, revision in eligible.items() if revision_id not in superseded]
    if len(terminals) != 1:
        blockers.append("revision_not_uniquely_selectable_as_of")
        return None
    selected = terminals[0]
    if _aware(selected["effectiveAt"]) > decision:
        blockers.append("selected_revision_future_effective")
        return None
    return selected


def _component(
    value: Any, decision: datetime, blockers: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != COMPONENT_KEYS:
        blockers.append("component_contract_invalid")
        return None, []
    component_id = value.get("componentId")
    if component_id not in SOURCE_CONTRACTS:
        blockers.append("component_unknown_or_activity_list")
        return None, []
    expected = SOURCE_CONTRACTS[component_id]
    if (
        value.get("sourceContractId") != expected["sourceContractId"]
        or value.get("sourceContractHash") != source_contract_hash(component_id)
        or value.get("source") != expected["source"]
        or value.get("dataset") != expected["dataset"]
        or value.get("producerId") != expected["producerId"]
        or not isinstance(value.get("producerHash"), str)
        or HEX64.fullmatch(value["producerHash"]) is None
        or value.get("schemaVersion") != 1
    ):
        blockers.append("component_source_contract_invalid")
    counts = (
        value.get("expectedRecordCount"), value.get("parsedRecordCount"),
        value.get("rejectedRecordCount"), value.get("pageCount"),
        value.get("parsedPageCount"),
    )
    counts_valid = not any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in counts
    )
    if not counts_valid:
        blockers.append("component_count_contract_invalid")
    selected = _selected_revision(value, decision, blockers)
    records = selected.get("records", []) if selected else []
    if selected and component_id in ACTIVE_COMPONENTS and not records:
        blockers.append("active_component_empty")
    if selected and counts_valid and not (
        value["expectedRecordCount"] == value["parsedRecordCount"] == len(records)
        and value["rejectedRecordCount"] == 0
        and value["pageCount"] > 0
        and value["parsedPageCount"] == value["pageCount"]
    ):
        blockers.append("component_partial_or_rejected_input")
    if not selected:
        return None, []
    summary = {
        "componentId": component_id,
        "sourceContractHash": value["sourceContractHash"],
        "selectedRevisionHash": selected["revisionHash"],
        "entityCount": len(records),
        "entitySetHash": digest(sorted(row["securityCode"] for row in records)),
        "membershipSetHash": digest(sorted(row["selectedVersionHash"] for row in records)),
        "availabilityEvidenceHash": digest({
            "class": selected["availabilityEvidenceClass"],
            "id": selected["availabilityEvidenceId"],
            "semantics": selected["publicationSemanticsHash"],
        }),
    }
    return summary, records


def _active_at(record: dict[str, Any], decision: datetime) -> bool:
    entry = _aware(record["entryEffectiveAt"])
    exit_value = record["exitEffectiveAt"]
    exit_at = _aware(exit_value) if exit_value is not None else None
    return bool(entry and entry <= decision and (exit_at is None or decision < exit_at))


def _identity(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["securityId"], record["issuerId"], record["securityCode"],
        record["securityClass"], record["market"], record["status"],
        _instant(record["entryEffectiveAt"]), _instant(record["exitEffectiveAt"])
        if record["exitEffectiveAt"] is not None else None,
    )


def _research_report(blockers: list[str], *, input_digest: str = "") -> dict[str, Any]:
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "structuralPopulationComplete": False,
        "officialProducerRegistered": False,
        "historicalEligible": False,
        "populationPolicyHash": population_policy_hash(),
        "decisionAsOfHash": "",
        "inputDigest": input_digest or digest({}),
        "componentCount": 0,
        "universeEntityCount": 0,
        "universeEntitySetHash": digest([]),
        "componentSetHash": digest([]),
        "selectedRevisionSetHash": digest([]),
        "blockers": list(dict.fromkeys(blockers)),
    }
    report["artifactDigest"] = digest(report)
    return report


def _semantic_input_digest(payload: dict[str, Any]) -> str:
    """Hash schema semantics independent of component/revision/record ordering."""
    normalized_components = []
    for component in payload.get("components", []):
        if not isinstance(component, dict):
            normalized_components.append(component)
            continue
        normalized_component = {
            key: value for key, value in component.items() if key != "revisions"
        }
        normalized_revisions = []
        revisions = component.get("revisions", [])
        if not isinstance(revisions, list):
            normalized_component["revisions"] = revisions
            normalized_components.append(normalized_component)
            continue
        for revision in revisions:
            if not isinstance(revision, dict):
                normalized_revisions.append(revision)
                continue
            normalized_revision = {
                key: value for key, value in revision.items() if key != "records"
            }
            records = revision.get("records", [])
            normalized_revision["records"] = sorted(
                records,
                key=lambda row: (
                    row.get("securityId", ""), row.get("securityCode", ""),
                    digest(row) if isinstance(row, dict) else digest({}),
                ),
            ) if isinstance(records, list) and all(isinstance(row, dict) for row in records) else records
            normalized_revisions.append(normalized_revision)
        normalized_component["revisions"] = sorted(
            normalized_revisions,
            key=lambda row: (
                row.get("availableAt", ""), row.get("revisionId", ""),
            ) if isinstance(row, dict) else ("", ""),
        )
        normalized_components.append(normalized_component)
    normalized = {
        key: value for key, value in payload.items() if key != "components"
    }
    normalized["components"] = sorted(
        normalized_components,
        key=lambda row: row.get("componentId", "") if isinstance(row, dict) else "",
    )
    return digest(normalized)


def assemble(payload: Any) -> dict[str, Any]:
    """Assemble a sanitized structural report; never authenticate a source."""
    if not _json_domain(payload) or not isinstance(payload, dict):
        return _research_report(["input_not_bounded_json"])
    if set(payload) != ROOT_KEYS or _contains_forbidden(payload):
        return _research_report(["input_contract_invalid"], input_digest=digest(payload))
    blockers: list[str] = []
    decision = _aware(payload.get("decisionAsOf"))
    if (
        payload.get("schemaVersion") != 1
        or payload.get("policyVersion") != POLICY_VERSION
        or payload.get("populationPolicyHash") != population_policy_hash()
        or payload.get("timezone") != TIMEZONE
        or decision is None
    ):
        blockers.append("root_policy_or_decision_invalid")
    components = payload.get("components")
    if not isinstance(components, list):
        components = []
        blockers.append("components_missing")
    if decision is None:
        return _research_report(blockers, input_digest=digest(payload))
    summaries: dict[str, dict[str, Any]] = {}
    records_by_component: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        summary, records = _component(component, decision, blockers)
        if summary is None:
            continue
        component_id = summary["componentId"]
        if component_id in summaries:
            blockers.append("component_not_unique")
        summaries[component_id] = summary
        records_by_component[component_id] = records
    if set(summaries) != set(REQUIRED_COMPONENTS):
        blockers.append("required_component_missing")

    active_records = [
        record for component_id in ACTIVE_COMPONENTS
        for record in records_by_component.get(component_id, [])
        if _active_at(record, decision)
    ]
    event_records = [
        record for record in records_by_component.get("membership_events", [])
        if _active_at(record, decision)
    ]
    active_identities = sorted(_identity(record) for record in active_records)
    event_identities = sorted(_identity(record) for record in event_records)
    codes = [record["securityCode"] for record in active_records]
    security_ids = [record["securityId"] for record in active_records]
    if (
        active_identities != event_identities
        or len(codes) != len(set(codes))
        or len(security_ids) != len(set(security_ids))
    ):
        blockers.append("market_union_or_membership_events_mismatch")

    structural_complete = not blockers
    ordered_summaries = [summaries[key] for key in sorted(summaries)]
    entity_codes = sorted(codes)
    selected_revisions = sorted(
        summary["selectedRevisionHash"] for summary in ordered_summaries
    )
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "structuralPopulationComplete": structural_complete,
        "officialProducerRegistered": False,
        "historicalEligible": False,
        "populationPolicyHash": population_policy_hash(),
        "decisionAsOfHash": digest(_instant(payload["decisionAsOf"])),
        "inputDigest": _semantic_input_digest(payload),
        "componentCount": len(summaries),
        "universeEntityCount": len(entity_codes),
        "universeEntitySetHash": digest(entity_codes),
        "componentSetHash": digest(ordered_summaries),
        "selectedRevisionSetHash": digest(selected_revisions),
        "blockers": list(dict.fromkeys([
            "official_source_admission_unregistered",
            "historical_available_at_authority_unregistered",
            *blockers,
        ])),
    }
    report["artifactDigest"] = digest(report)
    return report


def run(payload: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off boundary; disabled mode does not inspect the payload."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "structuralPopulationComplete": False,
            "officialProducerRegistered": False,
            "historicalEligible": False,
        }
    try:
        return assemble(payload)
    except (TypeError, ValueError, OverflowError, RecursionError, KeyError, AttributeError):
        return _research_report(["input_fail_closed"])
