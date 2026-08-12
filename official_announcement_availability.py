"""Default-off offline validator for official announcement availability evidence."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1
POLICY_VERSION = "official-announcement-availability-v1"
HISTORICAL_CERTIFIABLE = "HISTORICAL_CERTIFIABLE"
FORWARD_OBSERVED_ONLY = "FORWARD_OBSERVED_ONLY"
REJECTED = "REJECTED"
CONFLICT = "CONFLICT"
ALLOWED_PROVIDERS = {"TWSE", "TPEX"}
ALLOWED_MODES = {"historical", "forward"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_KEYS = {
    "url", "uri", "token", "secret", "authorization", "cookie", "raw",
    "rawrows", "retrievedat", "generatedat", "currenttime",
}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in FORBIDDEN_KEYS or _has_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _identity(record: dict[str, Any]) -> tuple[str, ...] | None:
    fields = ("provider", "sourceKey", "documentId", "revisionId", "evidenceKey")
    values = tuple(record.get(field) for field in fields)
    if any(not isinstance(value, str) or not value for value in values):
        return None
    return values


def _classify(record: dict[str, Any]) -> tuple[str, str, datetime | None]:
    if _has_forbidden_key(record):
        return REJECTED, "forbidden_or_sensitive_field", None
    if _identity(record) is None or record.get("provider") not in ALLOWED_PROVIDERS:
        return REJECTED, "invalid_identity", None
    if not HASH_RE.fullmatch(str(record.get("contentHash", ""))):
        return REJECTED, "invalid_content_hash", None
    if record.get("officialEvidence") is not True:
        return REJECTED, "not_official_evidence", None
    if any(record.get(field) for field in (
        "dateOnlyEvidence", "bareDailySchedule", "usesHttpDate", "usesLastModified",
        "usesObservationDate", "usesInferredTradingDay",
    )):
        return REJECTED, "unsupported_time_semantics", None

    published = _time(record.get("publishedAt"))
    if (
        published is not None
        and record.get("publishedAtBindsExactRevision") is True
        and isinstance(record.get("publicationEvidenceId"), str)
        and bool(record.get("publicationEvidenceId"))
    ):
        return HISTORICAL_CERTIFIABLE, "individual_official_timestamp", published

    revision_available = _time(record.get("revisionAvailableAt"))
    if (
        revision_available is not None
        and record.get("immutableRevision") is True
        and record.get("publicationSemanticsDocumented") is True
        and record.get("revisionAvailableAtBindsExactRevision") is True
        and isinstance(record.get("publicationSemanticsId"), str)
        and bool(record.get("publicationSemanticsId"))
    ):
        return HISTORICAL_CERTIFIABLE, "immutable_revision_documented_semantics", revision_available

    first_seen = _time(record.get("firstSeenAt"))
    if first_seen is not None and record.get("firstSeenAppendOnly") is True:
        return FORWARD_OBSERVED_ONLY, "conservative_first_seen_boundary", first_seen
    return REJECTED, "no_certifiable_availability_evidence", None


def validate(
    records: list[dict[str, Any]], *, expected_keys: list[str], decision_as_of: str,
    evaluation_mode: str, enabled: bool = False,
) -> dict[str, Any]:
    """Validate supplied fixtures without I/O; output always remains research-only."""
    if not enabled:
        return {"schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
                "mode": "disabled", "recordCount": 0}
    decision = _time(decision_as_of)
    if decision is None or evaluation_mode not in ALLOWED_MODES:
        return _failed("invalid_decision_context", len(expected_keys))
    if len(expected_keys) != len(set(expected_keys)) or any(
        not isinstance(key, str) or not key for key in expected_keys
    ):
        return _failed("invalid_expected_scope", len(expected_keys))

    identities: dict[tuple[str, ...], tuple[str, str | None]] = {}
    conflicts: set[str] = set()
    unique_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        identity = _identity(record)
        if identity is None:
            unique_records.append(record)
            continue
        signature = (str(record.get("contentHash", "")), record.get("firstSeenAt"))
        if identity in identities:
            if identities[identity] != signature:
                conflicts.add(identity[-1])
            continue
        identities[identity] = signature
        unique_records.append(record)

    evaluated: list[dict[str, Any]] = []
    selected: set[str] = set()
    eligible_indices: dict[str, list[int]] = {}
    for record in unique_records:
        key = record.get("evidenceKey") if isinstance(record.get("evidenceKey"), str) else "invalid"
        classification, reason, boundary = _classify(record)
        if key in conflicts:
            classification, reason, boundary = CONFLICT, "append_only_identity_conflict", None
        eligible = False
        if boundary is not None and boundary <= decision:
            eligible = classification == HISTORICAL_CERTIFIABLE or (
                classification == FORWARD_OBSERVED_ONLY and evaluation_mode == "forward"
            )
        if boundary is not None and boundary > decision:
            reason = "not_available_at_decision"
        elif classification == FORWARD_OBSERVED_ONLY and evaluation_mode == "historical":
            reason = "first_seen_forbidden_for_historical_backfill"
        if eligible and key in expected_keys:
            selected.add(key)
            eligible_indices.setdefault(key, []).append(len(evaluated))
        evaluated.append({"evidenceKey": key, "classification": classification,
                          "eligible": eligible and key in expected_keys, "reason": reason,
                          "evidenceHash": _canonical_hash(record)})

    # A PIT decision must resolve to exactly one version.  Distinct eligible
    # revisions for the same evidence key are ambiguous and fail closed.
    for key, indices in eligible_indices.items():
        if len(indices) <= 1:
            continue
        conflicts.add(key)
        selected.discard(key)
        for index in indices:
            evaluated[index].update({
                "classification": CONFLICT,
                "eligible": False,
                "reason": "multiple_eligible_versions",
            })

    missing = sorted(set(expected_keys) - selected)
    coverage = 1.0 if not expected_keys else len(selected) / len(expected_keys)
    ready = coverage == 1.0 and not conflicts
    report = {
        "schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
        "mode": "research_only", "diagnosticOnly": True,
        "evaluationMode": evaluation_mode, "decisionAsOf": decision.isoformat(),
        "expectedCount": len(expected_keys), "selectedCount": len(selected),
        "coverage": coverage, "coverageComplete": coverage == 1.0,
        "conflictCount": len(conflicts), "missingKeys": missing,
        "contractReady": ready,
        "records": sorted(evaluated, key=lambda item: (item["evidenceKey"], item["evidenceHash"])),
        "blockers": [] if ready else ["announcement_availability_contract_incomplete"],
        "limitations": ["first_seen_is_forward_only", "no_historical_backfill_from_observation",
                        "shadow_contract_does_not_enable_formal_advice"],
    }
    report["reportHash"] = _canonical_hash(report)
    return report


def _failed(reason: str, expected_count: int) -> dict[str, Any]:
    report = {
        "schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
        "mode": "research_only", "diagnosticOnly": True,
        "expectedCount": expected_count, "selectedCount": 0, "coverage": 0.0,
        "coverageComplete": False, "conflictCount": 0, "missingKeys": [],
        "contractReady": False, "records": [], "blockers": [reason],
    }
    report["reportHash"] = _canonical_hash(report)
    return report
