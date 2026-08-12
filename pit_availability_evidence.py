"""Validate official PIT-membership availability evidence in an isolated sidecar.

This module is intentionally default-off and diagnostic-only.  It never derives an
``availableAt`` value from an observation, retrieval, ingestion, or generation time,
and it is not connected to candidate, backtest, advice, or notification code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

FLAG = "PIT_AVAILABILITY_EVIDENCE_ENABLED"
SCHEMA_VERSION = 1
POLICY = "official-pit-availability-evidence-v1"
VISIBILITY = "private_metadata"
OFFICIAL_SOURCES = {
    "twse_listed": ("twse", "listed_companies"),
    "twse_terminated": ("twse", "terminated_listings"),
    "tpex_listed": ("tpex", "listed_companies"),
    "tpex_emerging": ("tpex", "emerging_companies"),
}
ALLOWED_KEYS = {
    "schemaVersion", "versionId", "sourceKey", "provider", "dataset",
    "evidenceKind", "authorityField", "officialPublishedAt", "availableAt",
    "effectiveDate", "contentHash", "schemaHash", "conflictStatus",
    "supersedesVersionId", "visibility", "observationDate", "generatedAt",
    "retrievedAt",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
FORBIDDEN_TEXT = re.compile(
    r"(?:https?://|(?:token|secret|password|authorization|api[_-]?key)\s*[=:])",
    re.IGNORECASE,
)


def enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aware_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _safe_record(record: dict[str, Any]) -> bool:
    if set(record) - ALLOWED_KEYS:
        return False
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return FORBIDDEN_TEXT.search(rendered) is None


def _base_reasons(record: dict[str, Any], source_key: str) -> list[str]:
    reasons: list[str] = []
    expected_provider, expected_dataset = OFFICIAL_SOURCES[source_key]
    if not _safe_record(record):
        reasons.append("metadata_not_allowlisted")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("schema_version_invalid")
    if record.get("sourceKey") != source_key:
        reasons.append("source_key_invalid")
    if record.get("provider") != expected_provider or record.get("dataset") != expected_dataset:
        reasons.append("official_source_invalid")
    if record.get("visibility") != VISIBILITY:
        reasons.append("visibility_invalid")
    if not isinstance(record.get("versionId"), str) or not ID_RE.fullmatch(record["versionId"]):
        reasons.append("version_id_invalid")
    if not isinstance(record.get("effectiveDate"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["effectiveDate"]):
        reasons.append("effective_date_invalid")
    for key in ("contentHash", "schemaHash"):
        if not isinstance(record.get(key), str) or not HASH_RE.fullmatch(record[key]):
            reasons.append(f"{key}_invalid")
    if record.get("conflictStatus") not in {"no_conflict", "conflict", "unknown"}:
        reasons.append("conflict_status_invalid")
    parent = record.get("supersedesVersionId")
    if parent is not None and (not isinstance(parent, str) or not ID_RE.fullmatch(parent)):
        reasons.append("supersedes_version_invalid")
    return reasons


def _availability_reasons(record: dict[str, Any], decision: datetime) -> tuple[list[str], str]:
    """Only explicit official publication metadata can establish availability."""
    explicit = (
        record.get("evidenceKind") == "official_publication_timestamp"
        and record.get("authorityField") in {"officialPublishedAt", "announcementPublishedAt"}
    )
    published = _aware_time(record.get("officialPublishedAt"))
    available = _aware_time(record.get("availableAt"))
    if not explicit and any(record.get(key) for key in ("observationDate", "generatedAt", "retrievedAt")):
        return ["official_availability_evidence_missing"], "unknown"
    if not explicit or published is None or available is None:
        return ["official_availability_evidence_missing"], "unknown"
    if published.astimezone(timezone.utc) != available.astimezone(timezone.utc):
        return ["official_publication_time_mismatch"], "invalid"
    if available.astimezone(timezone.utc) > decision.astimezone(timezone.utc):
        return ["available_at_after_decision"], "future"
    return [], "usable"


def _chain_reasons(records: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]], int]:
    """Validate one append-only chain, treating byte-identical retries as no-ops."""
    reasons: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_noops = 0
    for record in records:
        version_id = record.get("versionId")
        if not isinstance(version_id, str):
            continue
        previous = by_id.get(version_id)
        if previous is None:
            by_id[version_id] = record
        elif previous == record:
            duplicate_noops += 1
        else:
            reasons.append("version_overwrite_detected")
    unique = list(by_id.values())
    ids = set(by_id)
    parents = {record.get("supersedesVersionId") for record in unique if record.get("supersedesVersionId")}
    if any(parent not in ids for parent in parents):
        reasons.append("supersedes_target_missing")
    if len(unique) > 1:
        roots = [record for record in unique if not record.get("supersedesVersionId")]
        children: dict[str, int] = {}
        for record in unique:
            parent = record.get("supersedesVersionId")
            if parent:
                children[parent] = children.get(parent, 0) + 1
        if len(roots) != 1 or any(value != 1 for value in children.values()):
            reasons.append("append_only_chain_ambiguous")
        for record in unique:
            seen: set[str] = set()
            current = record
            while current.get("supersedesVersionId"):
                parent = current["supersedesVersionId"]
                if parent in seen or parent == record.get("versionId"):
                    reasons.append("append_only_chain_cycle")
                    break
                seen.add(parent)
                current = by_id.get(parent, {})
                if not current:
                    break
    return sorted(set(reasons)), unique, duplicate_noops


def validate(records: list[dict[str, Any]], *, decision_as_of: str) -> dict[str, Any]:
    """Return a non-sensitive, always-research-only availability evidence summary."""
    if not enabled():
        return {"schemaVersion": SCHEMA_VERSION, "policy": POLICY, "mode": "disabled"}
    decision = _aware_time(decision_as_of)
    if decision is None:
        return {
            "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
            "mode": "research_only", "diagnosticOnly": True,
            "blockers": ["decision_as_of_invalid"],
        }
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return {
            "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
            "mode": "research_only", "diagnosticOnly": True,
            "decisionAsOf": decision.isoformat(), "blockers": ["records_invalid"],
        }

    output: list[dict[str, Any]] = []
    counts = {
        "expected": len(OFFICIAL_SOURCES), "present": 0, "missing": 0,
        "unknown": 0, "conflict": 0, "invalid": 0,
        "selectedVersion": 0, "selectedNone": 0, "duplicateNoOp": 0,
    }
    global_blockers: list[str] = []
    unknown_sources = sorted({
        str(item.get("sourceKey")) for item in records
        if item.get("sourceKey") not in OFFICIAL_SOURCES
    })
    if unknown_sources:
        global_blockers.append("out_of_scope_source")

    for source_key in OFFICIAL_SOURCES:
        source_records = [item for item in records if item.get("sourceKey") == source_key]
        if not source_records:
            counts["missing"] += 1
            counts["selectedNone"] += 1
            output.append({
                "sourceKey": source_key, "present": False, "versionCount": 0,
                "authorityState": "unknown", "availableAtState": "missing",
                "appendOnlyState": "unknown", "selection": "selectedNone",
                "reasons": ["source_evidence_missing"],
            })
            continue
        counts["present"] += 1
        chain_reasons, unique_records, noops = _chain_reasons(source_records)
        counts["duplicateNoOp"] += noops
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        reasons = list(chain_reasons)
        states: list[str] = []
        for record in unique_records:
            reasons.extend(_base_reasons(record, source_key))
            availability_reasons, state = _availability_reasons(record, decision)
            reasons.extend(availability_reasons)
            states.append(state)
            if record.get("conflictStatus") == "conflict":
                reasons.append("official_evidence_conflict")
            elif record.get("conflictStatus") == "unknown":
                reasons.append("conflict_status_unknown")
            available = _aware_time(record.get("availableAt"))
            if not availability_reasons and available is not None and record.get("conflictStatus") == "no_conflict":
                candidates.append((available.astimezone(timezone.utc), record))

        reasons = sorted(set(reasons))
        terminal_ids = {
            item.get("versionId") for item in unique_records
        } - {
            item.get("supersedesVersionId") for item in unique_records if item.get("supersedesVersionId")
        }
        selected = [item for _, item in candidates if item.get("versionId") in terminal_ids]
        if len(selected) != 1:
            reasons.append("unique_pit_version_not_selected")
        if global_blockers:
            reasons.append("input_scope_invalid")
        reasons = sorted(set(reasons))
        if any("conflict" in reason for reason in reasons):
            counts["conflict"] += 1
        if "official_availability_evidence_missing" in reasons:
            counts["unknown"] += 1
        invalid_markers = (
            "invalid", "overwrite", "ambiguous", "cycle", "mismatch",
            "target_missing", "after_decision", "scope_invalid",
        )
        if any(any(marker in reason for marker in invalid_markers) for reason in reasons):
            counts["invalid"] += 1
        is_selected = len(selected) == 1 and not reasons
        counts["selectedVersion" if is_selected else "selectedNone"] += 1
        output.append({
            "sourceKey": source_key,
            "present": True,
            "versionCount": len(unique_records),
            "authorityState": "official_explicit" if states and all(state == "usable" for state in states) else "unknown",
            "availableAtState": "usable" if is_selected else "unknown" if "official_availability_evidence_missing" in reasons else "invalid",
            "appendOnlyState": "valid" if not chain_reasons else "invalid",
            "selection": "selectedVersion" if is_selected else "selectedNone",
            "selectedVersionId": selected[0]["versionId"] if is_selected else None,
            "reasons": reasons,
        })

    coverage = round(counts["selectedVersion"] / len(OFFICIAL_SOURCES), 4)
    projection = {
        "schemaVersion": SCHEMA_VERSION,
        "policy": POLICY,
        "decisionAsOf": decision.isoformat(),
        "counts": counts,
        "records": output,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": POLICY,
        "mode": "research_only",
        "diagnosticOnly": True,
        "decisionAsOf": decision.isoformat(),
        "coverageDenominator": len(OFFICIAL_SOURCES),
        "coverage": coverage,
        "coverageState": "complete_shadow_evidence" if coverage == 1.0 and not global_blockers else "incomplete",
        "counts": counts,
        "records": output,
        "blockers": sorted(set(global_blockers)),
        "sidecarDigest": _digest(projection),
        "limitation": (
            "Official metadata identity only; it does not prove normalized values, infer missing "
            "availability, or enable candidates, backtests, advice, notifications, or trading."
        ),
    }
