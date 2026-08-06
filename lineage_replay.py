"""Offline, default-off lineage replay. It never changes an advice gate."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

FLAG = "LINEAGE_REPLAY_ENABLED"
FIELDS = ("compositeKey", "provider", "dataset", "entityId", "observationPeriod", "sourceRevision", "availableAt", "contentHash", "status", "conflictStatus", "schemaVersion")
DENIED = {"endpoint", "raw", "url", "token", "authorization", "scope", "retrievedAt", "ingestedAt"}

def enabled() -> bool: return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}
def digest(value: Any) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
def utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None: raise ValueError("timezone_required")
    return dt.astimezone(timezone.utc)

def replay(summary: dict[str, Any]) -> dict[str, Any]:
    """Pure function: unknown/ambiguous evidence always returns research_only."""
    if not enabled(): return {"mode": "disabled", "blockers": ["replay_disabled"]}
    blockers: list[str] = []
    if summary.get("schemaVersion") != 1: blockers.append("summary_schema_invalid")
    if summary.get("coverage") != 1.0: blockers.append("coverage_incomplete")
    try: as_of = utc(str(summary.get("decisionAsOf", "")))
    except ValueError: as_of = None; blockers.append("decision_as_of_invalid")
    rows = summary.get("records")
    if not isinstance(rows, list): rows = []; blockers.append("records_invalid")
    selected = []
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or DENIED & set(row): blockers.append("summary_field_not_allowed"); continue
        if any(not row.get(key) for key in FIELDS): blockers.append("record_incomplete"); continue
        if row["status"] != "success" or row["conflictStatus"] != "no_conflict": blockers.append("record_not_verified"); continue
        try:
            if as_of is None or utc(row["availableAt"]) > as_of: blockers.append("record_unavailable_at_decision"); continue
        except ValueError: blockers.append("record_available_at_invalid"); continue
        identity = tuple(str(row[key]) for key in ("provider", "dataset", "entityId", "observationPeriod"))
        groups.setdefault(identity, []).append(row)
    for choices in groups.values():
        hashes = {row["contentHash"] for row in choices}
        if len(hashes) != 1: blockers.append("version_selection_not_unique")
        else: selected.append(choices[0])
    if not selected: blockers.append("selection_missing")
    version_set = [{key: row[key] for key in ("compositeKey", "contentHash", "availableAt", "sourceRevision")} for row in sorted(selected, key=lambda r: r["compositeKey"])]
    version_hash = digest(version_set)
    expected = summary.get("expectedVersionSetHash")
    if not expected or expected != version_hash: blockers.append("version_set_hash_mismatch")
    artifact = {"schemaVersion": 1, "mode": "shadow_only" if not blockers else "research_only", "decisionAsOf": summary.get("decisionAsOf"), "coverage": summary.get("coverage"), "inputManifestHash": digest({"decisionAsOf": summary.get("decisionAsOf"), "coverage": summary.get("coverage"), "records": rows}), "expectedSnapshotHash": summary.get("expectedSnapshotHash"), "expectedVersionSetHash": expected, "selectedVersionSetHash": version_hash, "snapshotHashMatch": bool(summary.get("expectedSnapshotHash") and summary.get("expectedSnapshotHash") == version_hash), "selected": version_set if not blockers else [], "blockers": sorted(set(blockers)), "limitation": "Lineage selection does not prove normalized values or strategy correctness."}
    return artifact
