"""Offline E1C-B1 serializer, model and static SQL contract validator."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import decision_outcome_manifest as manifest_contract
import decision_outcome_sandbox as sandbox_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-db-contract-v1"
RECEIPT_POLICY = "decision-outcome-db-receipt-v1"
MIGRATION_FILE = "supabase-decision-outcome-shadow-v1.sql"
PINNED_MIGRATION_SHA256 = "a1c295a6193b7b69ad606c6f025e1a11d081333b6650d0f7df0cdd7d35cf15f5"
PINNED_SEMANTIC_SHA256 = "e4c9e68f5bc4396c697f4432d958dca93485eeecaf7b88c1273fe75707b1cf0c"
PRIVATE_SCHEMA = "investment_decision_shadow_v1"
RPC = "append_decision_outcome_snapshot_v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
EVENT_ITEM_KEYS = {"eventHash", "eventType", "logicalKeyHash", "canonicalText", "transportBlobHash"}
PAYLOAD_KEYS = {"p_events", "p_manifest_text", "p_manifest_transport_hash",
                "p_anchor_text", "p_anchor_transport_hash"}
RECEIPT_KEYS = {"schemaVersion", "policyVersion", "scopeId", "sequence", "anchorHash",
                "previousAnchorHash", "manifestDigest", "eventCount", "decisionDateCount",
                "receiptHash", "status", "diagnosticOnly", "promotionEligible"}
SEMANTIC_MANIFEST = {
    "schemaVersion": 1, "policyVersion": POLICY_VERSION, "privateSchema": PRIVATE_SCHEMA,
    "tables": ["event_blob_v1", "manifest_blob_v1", "anchor_v1", "audit_v1"],
    "forceRls": True, "appendOnly": True,
    "roles": ["decision_outcome_owner", "decision_outcome_writer"],
    "deniedRoles": ["PUBLIC", "anon", "authenticated", "service_role"],
    "rpc": RPC, "rpcSearchPath": "pg_catalog", "transactionLock": "scope_advisory_xact",
    "transportHash": "sha256_exact_utf8_text", "logicalHash": "python_replay_only",
    "formalIntegration": False, "promotionEligible": False,
}


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("db_contract_non_canonical") from error


def _hash(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _event_item(event: dict[str, Any]) -> dict[str, str]:
    canonical = _canonical(event)
    logical_key = event.get("logicalKey")
    if not isinstance(logical_key, str):
        raise ValueError("event_logical_key_invalid")
    result = {
        "eventHash": event["eventHash"], "eventType": event["eventType"],
        "logicalKeyHash": _hash(logical_key), "canonicalText": canonical,
        "transportBlobHash": _hash(canonical),
    }
    if set(result) != EVENT_ITEM_KEYS:
        raise AssertionError("event_item_schema_drift")
    return result


def serialize(events: Any, manifest: Any, anchor: Any, *, enabled: bool = False) -> dict[str, Any]:
    """Build an in-memory private RPC payload; never connects or writes."""
    if not enabled:
        return {"mode": "disabled", "mappingReady": False, "payload": None}
    replay = manifest_contract.verify(events, manifest, enabled=True)
    if replay.get("verifiedCandidateSet") is not True:
        return _blocked("candidate_set_not_verified")
    if not _anchor_matches(anchor, manifest):
        return _blocked("anchor_contract_invalid")
    items = sorted((_event_item(copy.deepcopy(event)) for event in events),
                   key=lambda item: item["eventHash"])
    manifest_text = _canonical(manifest)
    anchor_text = _canonical(anchor)
    payload = {
        "p_events": items, "p_manifest_text": manifest_text,
        "p_manifest_transport_hash": _hash(manifest_text),
        "p_anchor_text": anchor_text, "p_anchor_transport_hash": _hash(anchor_text),
    }
    return {"mode": "research_only", "diagnosticOnly": True, "mappingReady": True,
            "promotionEligible": False, "blockers": [], "payload": payload,
            "payloadContractHash": _hash(_canonical({"keys": sorted(PAYLOAD_KEYS), "version": 1}))}


def _anchor_matches(anchor: Any, manifest: Any) -> bool:
    if not isinstance(anchor, dict) or set(anchor) != sandbox_contract.ANCHOR_KEYS:
        return False
    material = {key: value for key, value in anchor.items() if key != "anchorHash"}
    return (
        anchor.get("schemaVersion") == 1
        and anchor.get("policyVersion") == sandbox_contract.POLICY_VERSION
        and anchor.get("scopeId") == manifest.get("scopeId")
        and anchor.get("manifestDigest") == manifest.get("manifestDigest")
        and anchor.get("expectedEventCount") == manifest.get("expectedEventCount")
        and anchor.get("expectedDecisionCount") == manifest.get("expectedDecisionCount")
        and anchor.get("expectedOutcomeCount") == manifest.get("expectedOutcomeCount")
        and anchor.get("expectedLegacyCount") == manifest.get("expectedLegacyCount")
        and anchor.get("decisionDateCount") == manifest.get("decisionDateCount")
        and anchor.get("writerContractHash") == sandbox_contract.WRITER_CONTRACT_HASH
        and anchor.get("diagnosticOnly") is True and anchor.get("researchOnly") is True
        and anchor.get("promotionEligible") is False
        and anchor.get("completenessExternallyAnchored") is False
        and isinstance(anchor.get("anchorHash"), str)
        and anchor["anchorHash"] == _hash(_canonical(material))
    )


def validate_receipt(receipt: Any, *, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return {"mode": "disabled", "valid": False}
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        return {"mode": "research_only", "diagnosticOnly": True, "valid": False,
                "blockers": ["receipt_schema_invalid"]}
    hashes = ("scopeId", "anchorHash", "previousAnchorHash", "manifestDigest", "receiptHash")
    valid = (
        receipt["schemaVersion"] == 1 and receipt["policyVersion"] == RECEIPT_POLICY
        and all(isinstance(receipt[name], str) and HEX.fullmatch(receipt[name]) for name in hashes)
        and type(receipt["sequence"]) is int and receipt["sequence"] >= 1
        and type(receipt["eventCount"]) is int and receipt["eventCount"] >= 0
        and type(receipt["decisionDateCount"]) is int and receipt["decisionDateCount"] >= 0
        and receipt["status"] in {"inserted", "duplicate"}
        and receipt["diagnosticOnly"] is True and receipt["promotionEligible"] is False
        and receipt["receiptHash"] == _receipt_hash(receipt)
    )
    return {"mode": "research_only", "diagnosticOnly": True, "valid": valid,
            "promotionEligible": False, "blockers": [] if valid else ["receipt_contract_invalid"]}


def _receipt_hash(receipt: dict[str, Any]) -> str:
    values = (receipt["scopeId"], str(receipt["sequence"]), receipt["anchorHash"],
              receipt["previousAnchorHash"], receipt["manifestDigest"],
              str(receipt["eventCount"]), str(receipt["decisionDateCount"]), RECEIPT_POLICY)
    return _hash("\x1f".join(values))


def validate_migration(sql: str, *, enabled: bool = False) -> dict[str, Any]:
    """Static-only validation.  It never executes or introspects SQL."""
    if not enabled:
        return {"mode": "disabled", "contractReady": False, "migrationExecuted": False}
    lowered = sql.lower()
    blockers: list[str] = []
    migration_hash = _hash(sql)
    semantic_hash = _hash(_canonical(SEMANTIC_MANIFEST))
    if migration_hash != PINNED_MIGRATION_SHA256:
        blockers.append("migration_hash_unpinned_or_drifted")
    if semantic_hash != PINNED_SEMANTIC_SHA256:
        blockers.append("semantic_hash_unpinned_or_drifted")
    if f"semantic-contract-sha256: {PINNED_SEMANTIC_SHA256}" not in sql:
        blockers.append("semantic_hash_not_embedded")
    expected_tables = {"event_blob_v1", "manifest_blob_v1", "anchor_v1", "audit_v1"}
    created = set(re.findall(r"(?i)create table investment_decision_shadow_v1\.([a-z0-9_]+)", sql))
    if created != expected_tables:
        blockers.append("private_table_set_invalid")
    required = (
        "current_user <> 'postgres'", "pgcrypto' and n.nspname = 'extensions'",
        "decision_outcome_owner','decision_outcome_writer", "pg_auth_members",
        "create schema investment_decision_shadow_v1 authorization decision_outcome_owner",
        "force row level security", "from public, anon, authenticated, service_role",
        "before update or delete or truncate", "security definer set search_path = pg_catalog",
        "pg_advisory_xact_lock", "decision_outcome_sequence_conflict",
        "decision_outcome_parent_or_sequence_conflict", "decision_outcome_snapshot_regression",
        "extensions.digest(pg_catalog.convert_to", "decision-outcome-db-receipt-v1",
        "revoke all on function public.append_decision_outcome_snapshot_v1",
        "grant execute on function public.append_decision_outcome_snapshot_v1",
        "to decision_outcome_writer", "not completeness_externally_anchored",
    )
    for fragment in required:
        if fragment not in lowered:
            blockers.append("required_guard_missing")
    destructive = (
        r"(?im)^\s*create\s+table\s+if\s+not\s+exists\b",
        r"(?im)^\s*create\s+or\s+replace\b",
        r"(?im)^\s*drop\b",
        r"(?im)^\s*truncate\s+table\b",
        r"(?im)^\s*update\b",
        r"(?im)^\s*delete\s+from\b",
    )
    if any(re.search(pattern, sql) for pattern in destructive):
        blockers.append("destructive_or_rerun_statement_forbidden")
    if re.search(r"(?i)grant\s+.*\s+to\s+(?:public|anon|authenticated|service_role)\b", sql):
        blockers.append("denied_role_grant_forbidden")
    if re.search(r"(?i)grant\s+(?:select|insert|update|delete|truncate|all).*decision_outcome_writer", sql):
        blockers.append("writer_direct_dml_forbidden")
    if "available_at" in lowered or "retrieved_at" in lowered or "generated_at" in lowered:
        blockers.append("pit_time_alias_forbidden")
    return {"mode": "research_only", "diagnosticOnly": True,
            "contractReady": not blockers, "migrationExecuted": False,
            "productionApproved": False, "migrationHash": migration_hash,
            "semanticHash": semantic_hash, "blockers": sorted(set(blockers)) +
            ["migration_not_executed", "staging_authority_not_granted"],
            "limitations": ["offline_static_validation_only", "no_database_introspection",
                            "no_rpc_invocation", "external_receipt_not_verified"]}


class AppendRpcModel:
    """Transaction-like in-memory model of sequence and append semantics."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.manifests: dict[str, dict[str, Any]] = {}
        self.anchors: dict[str, list[dict[str, Any]]] = {}

    def append(self, events: Any, manifest: Any, anchor: Any) -> dict[str, Any]:
        mapped = serialize(events, manifest, anchor, enabled=True)
        if mapped.get("mappingReady") is not True:
            return {"status": "blocked", "reason": "payload_invalid"}
        chain = self.anchors.setdefault(anchor["scopeId"], [])
        if chain and anchor["sequence"] <= len(chain):
            existing = chain[anchor["sequence"] - 1]
            return {"status": "duplicate"} if existing == anchor else {"status": "conflict", "reason": "sequence_conflict"}
        expected_sequence = len(chain) + 1
        expected_previous = chain[-1]["anchorHash"] if chain else sandbox_contract.GENESIS
        if anchor["sequence"] != expected_sequence or anchor["previousAnchorHash"] != expected_previous:
            return {"status": "conflict", "reason": "parent_or_sequence_conflict"}
        if chain:
            prior = self.manifests[chain[-1]["manifestDigest"]]
            if not set(prior["eventHashes"]).issubset(set(manifest["eventHashes"])):
                return {"status": "conflict", "reason": "snapshot_regression"}
            for name in ("expectedEventCount", "expectedDecisionCount", "expectedOutcomeCount",
                         "expectedLegacyCount", "decisionDateCount"):
                if manifest[name] < prior[name]:
                    return {"status": "conflict", "reason": "snapshot_regression"}
        staged_events = copy.deepcopy(self.events)
        for event in events:
            existing = staged_events.get(event["eventHash"])
            if existing is not None and existing != event:
                return {"status": "conflict", "reason": "event_identity_conflict"}
            staged_events[event["eventHash"]] = copy.deepcopy(event)
        existing_manifest = self.manifests.get(manifest["manifestDigest"])
        if existing_manifest is not None and existing_manifest != manifest:
            return {"status": "conflict", "reason": "manifest_identity_conflict"}
        self.events = staged_events
        self.manifests[manifest["manifestDigest"]] = copy.deepcopy(manifest)
        chain.append(copy.deepcopy(anchor))
        return {"status": "inserted"}


def _blocked(*codes: str) -> dict[str, Any]:
    return {"mode": "research_only", "diagnosticOnly": True, "mappingReady": False,
            "promotionEligible": False, "blockers": sorted(set(codes)), "payload": None}
