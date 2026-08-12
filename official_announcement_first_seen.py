"""Default-off, fixture-only forward observation contract and temporary ledger.

The module performs no I/O.  It receives already-minimized official announcement
fixtures, stamps completion with its own UTC clock, and keeps records only in an
in-memory append-only ledger.  Nothing here is historical availability evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
POLICY_VERSION = "official-announcement-first-seen-v1"
CLASSIFICATION = "FORWARD_OBSERVED_ONLY"

SOURCE_CONTRACTS = {
    "twse_official_announcement_detail_v1": {"provider": "TWSE", "markets": {"listed"}},
    "tpex_market_announcement_detail_v1": {"provider": "TPEX", "markets": {"otc", "emerging"}},
}
EVENT_TYPES = {"listing", "delisting"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
FORBIDDEN_INPUT_KEYS = {
    "url", "uri", "query", "token", "secret", "authorization", "cookie",
    "raw", "rawrows", "body", "headers", "availableat", "publishedat",
    "retrievedat", "generatedat", "observedat", "observationcompletedat",
    "firstseenat", "firstseenatutc",
}

# Pinned snapshots of the existing writer/schema contracts.  This validator is
# deliberately unable to repair them; a mismatch must block DB integration.
ACTUAL_WRITER_FIELDS = {
    "provider", "dataset", "entityId", "observationPeriod", "sourceRevision",
    "availableAt", "schemaVersion", "contentHash", "compositeKey",
    "supersedesContentHash", "status", "conflictStatus", "visibility",
}
EXPECTED_DB_COLUMNS = {
    "provider", "dataset", "entity_id", "observation_period", "source_revision",
    "available_at", "schema_version", "content_hash", "composite_key",
    "supersedes_content_hash", "metadata", "ingested_at",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in FORBIDDEN_INPUT_KEYS or _has_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _validate_fixture(fixture: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if _has_forbidden_key(fixture):
        blockers.append("forbidden_or_caller_time_field")
    contract_id = fixture.get("sourceContractId")
    contract = SOURCE_CONTRACTS.get(contract_id)
    if contract is None:
        blockers.append("source_contract_not_allowlisted")
    else:
        if fixture.get("provider") != contract["provider"]:
            blockers.append("provider_contract_mismatch")
        if fixture.get("market") not in contract["markets"]:
            blockers.append("market_contract_mismatch")
    if fixture.get("eventType") not in EVENT_TYPES:
        blockers.append("not_membership_event")
    for field in ("officialDocumentId", "officialLetterNo", "entityId", "sourceRevision"):
        value = fixture.get(field)
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            blockers.append(f"invalid_{field}")
    if not _valid_date(fixture.get("effectiveDate")):
        blockers.append("invalid_effective_date")
    if not HASH_RE.fullmatch(str(fixture.get("contentHash", ""))):
        blockers.append("invalid_content_hash")
    if fixture.get("officialEvidence") is not True:
        blockers.append("not_official_evidence")
    supersedes = fixture.get("supersedesContentHash")
    if supersedes is not None and not HASH_RE.fullmatch(str(supersedes)):
        blockers.append("invalid_supersedes_hash")
    return sorted(set(blockers))


def observe(fixture: dict[str, Any], *, enabled: bool = False) -> dict[str, Any]:
    """Create one forward-only record; the caller cannot supply observation time."""
    if not enabled:
        return {"schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
                "mode": "disabled", "record": None}
    if not isinstance(fixture, dict):
        return _blocked("fixture_not_object")
    blockers = _validate_fixture(fixture)
    if blockers:
        return _blocked(*blockers)

    # The clock is read only after every fixture field has passed validation.
    completed = _utc_now()
    if completed.tzinfo is None or completed.utcoffset() != timezone.utc.utcoffset(completed):
        return _blocked("observer_clock_not_utc")
    completed_utc = completed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    identity = {
        "provider": fixture["provider"], "sourceContractId": fixture["sourceContractId"],
        "officialDocumentId": fixture["officialDocumentId"],
        "entityId": fixture["entityId"], "eventType": fixture["eventType"],
        "effectiveDate": fixture["effectiveDate"], "sourceRevision": fixture["sourceRevision"],
        "schemaVersion": SCHEMA_VERSION,
    }
    record = {
        "schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
        "mode": "research_only", "diagnosticOnly": True,
        "classification": CLASSIFICATION, "historicalEligible": False,
        "provider": fixture["provider"], "market": fixture["market"],
        "sourceContractId": fixture["sourceContractId"],
        "officialDocumentId": fixture["officialDocumentId"],
        "officialLetterNo": fixture["officialLetterNo"],
        "entityId": fixture["entityId"], "eventType": fixture["eventType"],
        "effectiveDate": fixture["effectiveDate"], "sourceRevision": fixture["sourceRevision"],
        "contentHash": fixture["contentHash"], "firstSeenAtUtc": completed_utc,
        "supersedesContentHash": fixture.get("supersedesContentHash"),
        "conflictStatus": "no_conflict", "visibility": "private_lineage",
        "compositeKey": _canonical_hash(identity),
        "limitations": ["forward_only", "no_historical_backfill", "not_formal_advice_evidence"],
    }
    record["recordHash"] = _canonical_hash(record)
    return {"schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
            "mode": "research_only", "diagnosticOnly": True,
            "blockers": [], "record": record}


def _blocked(*blockers: str) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
            "mode": "research_only", "diagnosticOnly": True,
            "blockers": sorted(set(blockers)), "record": None}


class TemporaryAppendOnlyLedger:
    """Process-local fixture ledger.  It exposes append and sanitized summary only."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._document_heads: dict[tuple[str, str, str], str] = {}
        self._conflicts = 0

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        if not _valid_record(record):
            return {"status": "blocked", "reason": "record_contract_invalid"}
        key = record["compositeKey"]
        existing = self._records.get(key)
        if existing is not None:
            if existing == record:
                return {"status": "duplicate_noop"}
            self._conflicts += 1
            return {"status": "conflict", "reason": "append_only_identity_changed"}

        document_key = (record["provider"], record["officialDocumentId"], record["entityId"])
        previous_hash = self._document_heads.get(document_key)
        supersedes = record.get("supersedesContentHash")
        if previous_hash is not None and supersedes != previous_hash:
            self._conflicts += 1
            return {"status": "conflict", "reason": "correction_missing_exact_supersedes"}
        if previous_hash is None and supersedes is not None:
            self._conflicts += 1
            return {"status": "conflict", "reason": "supersedes_unknown_version"}
        self._records[key] = copy.deepcopy(record)
        self._document_heads[document_key] = record["contentHash"]
        return {"status": "appended"}

    def summary(self) -> dict[str, Any]:
        hashes = sorted(record["recordHash"] for record in self._records.values())
        return {"mode": "research_only", "diagnosticOnly": True,
                "recordCount": len(hashes), "conflictCount": self._conflicts,
                "ledgerHash": _canonical_hash(hashes),
                "limitations": ["temporary_memory_only", "forward_only", "no_database_write"]}


def _valid_record(record: Any) -> bool:
    if not (
        isinstance(record, dict) and record.get("mode") == "research_only"
        and record.get("classification") == CLASSIFICATION
        and record.get("historicalEligible") is False
        and record.get("visibility") == "private_lineage"
        and _time_is_utc(record.get("firstSeenAtUtc"))
        and "availableAt" not in record and "publishedAt" not in record
        and HASH_RE.fullmatch(str(record.get("recordHash", ""))) is not None
    ):
        return False
    payload = copy.deepcopy(record)
    stored_record_hash = payload.pop("recordHash")
    if _canonical_hash(payload) != stored_record_hash:
        return False
    identity = {
        "provider": record.get("provider"), "sourceContractId": record.get("sourceContractId"),
        "officialDocumentId": record.get("officialDocumentId"),
        "entityId": record.get("entityId"), "eventType": record.get("eventType"),
        "effectiveDate": record.get("effectiveDate"), "sourceRevision": record.get("sourceRevision"),
        "schemaVersion": record.get("schemaVersion"),
    }
    return record.get("compositeKey") == _canonical_hash(identity)


def _time_is_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    except (ValueError, AttributeError):
        return False


def validate_schema_mapping(*, enabled: bool = False) -> dict[str, Any]:
    """Diagnose the pinned writer/DB mismatch; never creates a mapping or writes."""
    if not enabled:
        return {"mode": "disabled", "mappingReady": False}
    missing_in_writer = sorted(EXPECTED_DB_COLUMNS - ACTUAL_WRITER_FIELDS - {"ingested_at"})
    unexpected_writer = sorted(ACTUAL_WRITER_FIELDS - EXPECTED_DB_COLUMNS)
    return {
        "mode": "research_only", "diagnosticOnly": True, "mappingReady": False,
        "status": "blocked_contract_mismatch",
        "expectedDbColumns": sorted(EXPECTED_DB_COLUMNS),
        "actualWriterFields": sorted(ACTUAL_WRITER_FIELDS),
        "missingInWriter": missing_in_writer,
        "unexpectedWriter": unexpected_writer,
        "blockers": ["camel_case_writer_vs_snake_case_database", "required_metadata_not_written"],
        "limitations": ["no_shim", "no_migration", "no_writer_call"],
    }
