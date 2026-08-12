"""Offline capability audit for official PIT availability evidence sources.

The audit is default-off, read-only, metadata-only, and deliberately does not
emit or propagate any timestamp value.  It classifies endpoint *contracts*, not
live responses, and is never connected to the production data/advice pipeline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

FLAG = "OFFICIAL_AVAILABILITY_CAPABILITY_ENABLED"
SCHEMA_VERSION = 1
POLICY = "official-availability-capability-v1"
EXPECTED = {
    "twse_listed": ("twse", "twse_listing_bulletin"),
    "twse_terminated": ("twse", "twse_delisting_bulletin"),
    "tpex_listed": ("tpex", "tpex_otc_bulletin"),
    "tpex_emerging": ("tpex", "tpex_emerging_bulletin"),
}
ALLOWED_KEYS = {
    "schemaVersion", "sourceKey", "provider", "contractId",
    "documentationEvidenceId", "termsStatus", "evidenceMode",
    "publicationSemantic", "timestampPrecision", "timezoneDocumented",
    "immutableIdentityDocumented", "revisionSemanticsDocumented",
    "historyCoverage", "usesUndocumentedHttpHeaders", "contentHash",
    "schemaHash", "conflictStatus",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
FORBIDDEN_TEXT = re.compile(
    r"(?:https?://|(?:token|secret|password|authorization|api[_-]?key)\s*[=:])",
    re.IGNORECASE,
)
REJECTED_SEMANTICS = {
    "observation_date", "effective_date", "generated_time", "retrieved_time",
    "ingested_time", "quality_check_time", "undocumented_http_header",
    "data_gov_catalog_date", "data_gov_catalog_modified_time",
    "current_snapshot_time", "current_metadata_time", "inferred_next_trading_day",
}


def enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe(record: dict[str, Any]) -> bool:
    if set(record) - ALLOWED_KEYS:
        return False
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return FORBIDDEN_TEXT.search(rendered) is None


def _classify(record: dict[str, Any], source_key: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    provider, contract_id = EXPECTED[source_key]
    if not _safe(record):
        return "UNSUPPORTED", ["metadata_not_allowlisted"]
    if record.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("schema_version_invalid")
    if record.get("sourceKey") != source_key:
        reasons.append("source_key_invalid")
    if record.get("provider") != provider or record.get("contractId") != contract_id:
        reasons.append("endpoint_contract_not_allowlisted")
    for key in ("documentationEvidenceId",):
        if not isinstance(record.get(key), str) or not ID_RE.fullmatch(record[key]):
            reasons.append(f"{key}_invalid")
    for key in ("contentHash", "schemaHash"):
        if not isinstance(record.get(key), str) or not HASH_RE.fullmatch(record[key]):
            reasons.append(f"{key}_invalid")
    if record.get("usesUndocumentedHttpHeaders") is True:
        reasons.append("undocumented_http_header_forbidden")
    semantic = record.get("publicationSemantic")
    if semantic in REJECTED_SEMANTICS:
        reasons.append("non_publication_time_semantic")
    if record.get("termsStatus") == "prohibited":
        reasons.append("official_terms_prohibit_use")
    if record.get("conflictStatus") == "conflict":
        return "CONFLICT", sorted(set(reasons + ["official_evidence_contradiction"]))
    if record.get("conflictStatus") not in {None, "no_conflict"}:
        reasons.append("conflict_status_unknown")

    hard_invalid = any(
        marker in reason
        for reason in reasons
        for marker in ("invalid", "not_allowlisted", "forbidden", "non_publication", "prohibit")
    )
    if hard_invalid:
        return "UNSUPPORTED", sorted(set(reasons))

    accepted_semantic = semantic in {"official_published_timestamp", "official_available_timestamp"}
    accepted_mode = record.get("evidenceMode") in {"official_timestamp", "immutable_revision"}
    accepted = all((
        not reasons,
        record.get("termsStatus") in {"open_data_attribution", "official_terms_permit_metadata"},
        accepted_mode,
        accepted_semantic,
        record.get("timestampPrecision") == "timestamp_with_timezone",
        record.get("timezoneDocumented") is True,
        record.get("immutableIdentityDocumented") is True,
        record.get("historyCoverage") == "complete",
        record.get("usesUndocumentedHttpHeaders") is False,
        record.get("conflictStatus") == "no_conflict",
        record.get("revisionSemanticsDocumented") is True
            if record.get("evidenceMode") == "immutable_revision" else True,
    ))
    if accepted:
        return "CERTIFIABLE", []

    if record.get("termsStatus") not in {"open_data_attribution", "official_terms_permit_metadata"}:
        reasons.append("terms_not_confirmed")
    if not accepted_mode:
        reasons.append("evidence_mode_not_authoritative")
    if not accepted_semantic:
        reasons.append("publication_semantic_not_documented")
    if record.get("timestampPrecision") != "timestamp_with_timezone":
        reasons.append("timestamp_precision_insufficient")
    if record.get("timezoneDocumented") is not True:
        reasons.append("timezone_not_documented")
    if record.get("immutableIdentityDocumented") is not True:
        reasons.append("immutable_identity_not_documented")
    if record.get("historyCoverage") != "complete":
        reasons.append("historical_coverage_incomplete")
    if record.get("evidenceMode") == "immutable_revision" and record.get("revisionSemanticsDocumented") is not True:
        reasons.append("revision_semantics_not_documented")
    date_only = (
        record.get("timestampPrecision") == "date_only"
        and semantic in {"official_publication_date", "official_notice_date"}
    )
    return ("PROVISIONAL_DATE_ONLY" if date_only else "UNSUPPORTED"), sorted(set(reasons))


def audit(contracts: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify fixed official endpoint contracts without emitting source times."""
    if not enabled():
        return {"schemaVersion": SCHEMA_VERSION, "policy": POLICY, "mode": "disabled"}
    if not isinstance(contracts, list) or any(not isinstance(item, dict) for item in contracts):
        return {
            "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
            "mode": "research_only", "diagnosticOnly": True,
            "blockers": ["contracts_invalid"],
        }

    unknown_scope = any(item.get("sourceKey") not in EXPECTED for item in contracts)
    output: list[dict[str, Any]] = []
    counts = {
        "expected": len(EXPECTED), "present": 0, "missing": 0,
        "certifiable": 0, "provisionalDateOnly": 0,
        "unsupported": 0, "conflict": 0,
    }
    for source_key in EXPECTED:
        matches = [item for item in contracts if item.get("sourceKey") == source_key]
        if not matches:
            counts["missing"] += 1
            output.append({
                "sourceKey": source_key, "present": False,
                "classification": "UNSUPPORTED", "reasons": ["contract_missing"],
            })
            counts["unsupported"] += 1
            continue
        counts["present"] += 1
        if len(matches) != 1:
            classification, reasons = "CONFLICT", ["contract_ambiguous"]
        else:
            classification, reasons = _classify(matches[0], source_key)
        if unknown_scope:
            classification = "UNSUPPORTED"
            reasons = sorted(set(reasons + ["input_scope_invalid"]))
        counts[{
            "CERTIFIABLE": "certifiable",
            "PROVISIONAL_DATE_ONLY": "provisionalDateOnly",
            "UNSUPPORTED": "unsupported",
            "CONFLICT": "conflict",
        }[classification]] += 1
        item = matches[0]
        output.append({
            "sourceKey": source_key,
            "present": True,
            "contractId": item.get("contractId") if isinstance(item.get("contractId"), str) and ID_RE.fullmatch(item["contractId"]) else None,
            "documentationEvidenceId": item.get("documentationEvidenceId")
                if isinstance(item.get("documentationEvidenceId"), str) and ID_RE.fullmatch(item["documentationEvidenceId"]) else None,
            "classification": classification,
            "reasons": reasons,
        })

    certifiable_coverage = round(counts["certifiable"] / len(EXPECTED), 4)
    capability_ready = certifiable_coverage == 1.0 and not unknown_scope
    projection = {
        "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
        "counts": counts, "records": output,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": POLICY,
        "mode": "research_only",
        "diagnosticOnly": True,
        "coverageDenominator": len(EXPECTED),
        "certifiableCoverage": certifiable_coverage,
        "coverageRequirement": 1.0,
        "capabilityReady": capability_ready,
        "counts": counts,
        "records": output,
        "blockers": ["input_scope_invalid"] if unknown_scope else [],
        "capabilityDigest": _digest(projection),
        "limitation": (
            "Capability classification only: no source timestamp is emitted or propagated, "
            "and this result cannot enable PIT selection, backtests, advice, notifications, or trading."
        ),
    }
