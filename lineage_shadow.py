"""Fail-closed, append-only lineage evidence for shadow validation only.

This does not select investments or alter any advice/promotion gate.  It
records only non-sensitive metadata hashes.  Hash/pointer lineage proves the
identity of an input, not the correctness of a normalized value.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
FEATURE_FLAG = "LINEAGE_SHADOW_ENABLED"
REQUIRED_FIELDS = ("provider", "dataset", "entityId", "observationPeriod", "sourceRevision", "availableAt", "schemaVersion", "contentHash")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def enabled() -> bool:
    """Default-off switch for C-3 producers/readers; never changes advice gates."""
    return os.environ.get(FEATURE_FLAG, "").strip().lower() in {"1", "true", "yes"}


def _safe_endpoint(value: str) -> str:
    return value.split("?", 1)[0]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def build_record(*, provider: str, dataset: str, entity_id: str, observation_period: str,
                 source_revision: str, available_at: str | None, content: Any,
                 endpoint: str = "", scope: Any = None, supersedes_content_hash: str | None = None,
                 status: str = "success", conflict_status: str = "no_conflict") -> dict[str, Any]:
    """Build a private append-only metadata record; unavailable data fails closed."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "schemaVersion": SCHEMA_VERSION, "provider": provider, "dataset": dataset,
        "entityId": entity_id, "observationPeriod": observation_period,
        "sourceRevision": source_revision, "availableAt": available_at,
        "retrievedAt": now, "ingestedAt": now, "timezone": "UTC",
        "endpoint": _safe_endpoint(endpoint), "scopeHash": _hash(scope or {}),
        "contentHash": _hash(content), "supersedesContentHash": supersedes_content_hash,
        "status": status, "conflictStatus": conflict_status, "visibility": "private_lineage",
    }
    record["compositeKey"] = _hash({field: record[field] for field in REQUIRED_FIELDS if field != "contentHash"})
    return record


def validate(record: dict[str, Any], *, decision_as_of: str | None = None, coverage: float = 1.0) -> list[str]:
    """Return deterministic blockers. Never substitute retrieval/latest timestamps."""
    blockers = [f"lineage_{field}_missing" for field in REQUIRED_FIELDS if not record.get(field)]
    if record.get("visibility") != "private_lineage":
        blockers.append("lineage_visibility_invalid")
    if record.get("conflictStatus") != "no_conflict":
        blockers.append("lineage_conflict_unresolved")
    if coverage != 1.0:
        blockers.append("lineage_coverage_incomplete")
    available_at = record.get("availableAt")
    if decision_as_of and available_at:
        try:
            if _utc(str(available_at)) > _utc(decision_as_of):
                blockers.append("lineage_available_after_decision")
        except ValueError:
            blockers.append("lineage_timezone_invalid")
    return sorted(set(blockers))


def select_for_shadow(records: list[dict[str, Any]], *, decision_as_of: str, coverage: float) -> dict[str, Any]:
    """Select exactly one valid version per identity, otherwise produce research-only evidence."""
    eligible = [row for row in records if not validate(row, decision_as_of=decision_as_of, coverage=coverage)]
    # A same source identity with multiple valid versions is intentionally ambiguous.
    identities: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        identities.setdefault(row["compositeKey"], []).append(row)
    ambiguous = [key for key, rows in identities.items() if len({row["contentHash"] for row in rows}) != 1]
    if ambiguous or len(eligible) != 1:
        return {"mode": "research_only", "selected": None, "blockers": ["lineage_selection_not_unique" if ambiguous else "lineage_selection_missing"], "coverage": coverage}
    return {"mode": "shadow_only", "selected": {field: eligible[0][field] for field in ("compositeKey", "contentHash", "availableAt", "sourceRevision")}, "blockers": [], "coverage": coverage}


def artifact_summary(records: list[dict[str, Any]], *, decision_as_of: str, coverage: float) -> dict[str, Any]:
    """Allowlist artifact fields; no raw data, pointers, endpoints, or credentials."""
    selected = select_for_shadow(records, decision_as_of=decision_as_of, coverage=coverage)
    return {
        "schemaVersion": SCHEMA_VERSION, "mode": selected["mode"], "decisionAsOf": decision_as_of,
        "coverage": coverage, "selection": selected, "records": [
            {field: row.get(field) for field in ("compositeKey", "provider", "dataset", "entityId", "observationPeriod", "sourceRevision", "availableAt", "contentHash", "status", "conflictStatus")}
            for row in records
        ],
        "limitation": "Lineage hashes identify inputs only; they do not prove normalized values are correct.",
    }
