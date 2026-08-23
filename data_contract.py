"""Versioned, compact evidence contract for research candidates.

This module records metadata, not duplicated provider rows.  It deliberately
keeps a candidate in research-only state whenever the system cannot establish
when evidence became usable or whether its point-in-time universe is certified.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REQUIRED_GATE_RECORDS = ("quote", "fundamentals", "corporate_actions", "point_in_time")
MAX_JSON_NODES = 500_000
MAX_JSON_DEPTH = 16
MAX_STRING_BYTES = 4096
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_NUMBER_ABS = 10**18


def _hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_hash(value: Any) -> str | None:
    if not _bounded_json(value):
        return None
    try:
        return _hash(value)
    except (OverflowError, TypeError, UnicodeEncodeError, ValueError):
        return None


def _bounded_json(value: Any) -> bool:
    stack = [(value, 0)]
    seen: set[int] = set()
    nodes = 0
    approximate_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        approximate_bytes += 8
        if (
            nodes > MAX_JSON_NODES
            or depth > MAX_JSON_DEPTH
            or approximate_bytes > MAX_JSON_BYTES
        ):
            return False
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if abs(item) > MAX_NUMBER_ABS:
                return False
            continue
        if type(item) is float:
            if not math.isfinite(item) or abs(item) > MAX_NUMBER_ABS:
                return False
            continue
        if type(item) is str:
            try:
                encoded_size = len(item.encode("utf-8"))
                approximate_bytes += encoded_size
                if encoded_size > MAX_STRING_BYTES or approximate_bytes > MAX_JSON_BYTES:
                    return False
            except UnicodeEncodeError:
                return False
            continue
        if type(item) not in (dict, list):
            return False
        identity = id(item)
        if identity in seen:
            return False
        seen.add(identity)
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    return False
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        else:
            stack.extend((child, depth + 1) for child in item)
    return True


def _iso(value: Any) -> str | None:
    return _text(value)


def _text(value: Any, default: str | None = None) -> str | None:
    if type(value) is not str:
        return default
    text = value.strip()
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return default
    if (
        not text
        or len(encoded) > MAX_STRING_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        return default
    return text


def _provenance(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    value = snapshot.get("provenance", {})
    if not isinstance(value, dict):
        return {}
    item = value.get(key, value.get("default", {}))
    return item if isinstance(item, dict) else {}


def _field_or(mapping: dict[str, Any], key: str, fallback: Any) -> Any:
    return mapping[key] if key in mapping else fallback


def _record(
    name: str,
    source: str,
    dataset: str,
    effective_date: str | None,
    available_at: str | None,
    ingested_at: str | None,
    payload: Any,
    *,
    conflict_status: str = "unknown",
    quality: str | None = None,
    role: str = "decision_input",
) -> dict[str, Any]:
    source = _text(source, "unknown") or "unknown"
    dataset = _text(dataset, "unknown") or "unknown"
    conflict_status = _text(conflict_status, "unknown") or "unknown"
    evidence_hash = _payload_hash(payload)
    if evidence_hash is None:
        quality = "payload_invalid"
    elif quality is None:
        if not source or source == "unknown":
            quality = "source_missing"
        elif not dataset or dataset == "unknown":
            quality = "dataset_missing"
        elif not effective_date:
            quality = "effective_date_missing"
        elif not available_at:
            quality = "as_of_missing"
        elif conflict_status != "no_conflict":
            quality = "conflict_unresolved"
        else:
            quality = "verified"
    return {
        "name": name,
        "role": role,
        "source": source or "unknown",
        "sourceDataset": dataset or "unknown",
        "effectiveDate": effective_date,
        "availableAt": available_at,
        "ingestedAt": ingested_at,
        "evidenceHash": evidence_hash,
        "quality": quality,
        "conflictStatus": conflict_status,
    }


def _point_in_time_quality(pit_status: dict[str, Any]) -> str:
    """Certify against what the report claims, not against the whole universe.

    The universe-wide rule is what a backtest needs: one stock without a listing
    date means the historical sample may be missing companies. A shortlist makes
    a narrower claim -- these named stocks, today -- so when the report names
    specific candidates, their own membership evidence is what has to hold. The
    universe rule still applies whenever no candidates are named, and it remains
    the gate that governs backtest promotion elsewhere.
    """
    scoped = pit_status.get("candidateCertification")
    if isinstance(scoped, dict) and scoped.get("codes"):
        return "verified" if scoped.get("certified") is True else "pit_candidate_not_certified"
    return "verified" if pit_status.get("certified") is True else "pit_not_certified"


def build_contract(
    quote_data: dict[str, Any],
    actions: dict[str, Any],
    news: dict[str, Any],
    pit_status: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return contract metadata at the normalized candidate boundary."""
    generated_at = (
        datetime.now(timezone.utc).isoformat()
        if generated_at is None
        else _iso(generated_at)
    )
    quote_meta = _provenance(quote_data, "quote")
    fundamentals_meta = _provenance(quote_data, "fundamentals")
    quote_time = _iso(_field_or(quote_meta, "effectiveDate", quote_data.get("updatedAt")))
    ingested = _iso(_field_or(quote_meta, "ingestedAt", quote_data.get("updatedAt")))
    records = [
        _record(
            "quote", quote_meta.get("source", "unknown"), quote_meta.get("dataset", "unknown"),
            quote_time, _iso(quote_meta.get("availableAt")), ingested, quote_data.get("quotes", {}),
            conflict_status=quote_meta.get("conflictStatus", "unknown"),
        ),
        _record(
            "fundamentals", fundamentals_meta.get("source", "unknown"), fundamentals_meta.get("dataset", "unknown"),
            _iso(fundamentals_meta.get("effectiveDate")), _iso(fundamentals_meta.get("availableAt")),
            _iso(_field_or(fundamentals_meta, "ingestedAt", quote_data.get("updatedAt"))), quote_data.get("fundamentals", {}),
            conflict_status=fundamentals_meta.get("conflictStatus", "unknown"),
        ),
        _record(
            "corporate_actions", actions.get("source", "FinMind"), actions.get("dataset", "TaiwanStockDividendResult"),
            _iso(actions.get("period", {}).get("end") if isinstance(actions.get("period"), dict) else None),
            _iso(actions.get("availableAt")), _iso(actions.get("updatedAt")), actions.get("events", []),
            conflict_status=actions.get("conflictStatus", "unknown"),
        ),
        _record(
            "point_in_time", "TWSE/TPEx/MOPS official", "listing-and-exit-evidence",
            _iso(pit_status.get("generatedAt")), _iso(pit_status.get("availableAt")), _iso(pit_status.get("generatedAt")), pit_status,
            conflict_status="no_conflict", quality=_point_in_time_quality(pit_status),
        ),
        _record(
            "market_news", "attributable_news_feed", "headline_monitor", None,
            _iso(news.get("updatedAt")), _iso(news.get("updatedAt")), news.get("items", []),
            conflict_status="no_conflict", quality="context_only", role="context_only",
        ),
    ]
    by_name = {record["name"]: record for record in records}
    blockers = [
        f"contract_{name}_{by_name[name]['quality']}"
        for name in REQUIRED_GATE_RECORDS
        if by_name[name]["quality"] != "verified"
    ]
    if generated_at is None:
        blockers.append("contract_generated_at_invalid")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": "layered-hybrid-c1",
        "generatedAt": generated_at,
        "certified": not blockers,
        "blockers": blockers,
        "records": records,
        "contractHash": _hash(records),
    }

