"""Compact, non-sensitive provenance metadata for fetch boundaries.

Retrieval time is never treated as the time a source became publicly available.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def schema_hash(rows: Any) -> str:
    if isinstance(rows, list):
        shape = sorted({key for row in rows if isinstance(row, dict) for key in row})
    elif isinstance(rows, dict):
        shape = sorted(rows)
    else:
        shape = []
    return stable_hash(shape)


def record(*, provider: str, dataset: str, endpoint: str, scope: Any,
           retrieved_at: str, effective_date: str | None = None,
           available_at: str | None = None, observation_time: str | None = None,
           content: Any = None, status: str = "success",
           visibility: str = "private_cache", conflict_status: str = "unknown") -> dict[str, Any]:
    """Return audit metadata only; never include raw rows or query secrets."""
    verified = bool(available_at) and bool(effective_date) and conflict_status == "no_conflict" and status == "success"
    return {
        "schemaVersion": SCHEMA_VERSION, "provider": provider, "source": provider,
        "sourceDataset": dataset, "dataset": dataset, "endpoint": endpoint.split("?")[0],
        "scopeHash": stable_hash(scope), "observationTime": observation_time,
        "effectiveDate": effective_date, "availableAt": available_at,
        "retrievedAt": retrieved_at, "ingestedAt": retrieved_at, "timezone": "UTC",
        "contentHash": stable_hash(content if content is not None else {}), "schemaHash": schema_hash(content),
        "status": status,
        "quality": "verified" if verified else "as_of_missing" if not available_at else "effective_date_missing" if not effective_date else "conflict_unresolved",
        "conflictStatus": conflict_status, "visibility": visibility,
    }
