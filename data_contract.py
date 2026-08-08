"""Versioned, compact evidence contract for research candidates.

This module records metadata, not duplicated provider rows.  It deliberately
keeps a candidate in research-only state whenever the system cannot establish
when evidence became usable or whether its point-in-time universe is certified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REQUIRED_GATE_RECORDS = ("quote", "fundamentals", "corporate_actions", "point_in_time")


def _hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _provenance(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    value = snapshot.get("provenance", {})
    if not isinstance(value, dict):
        return {}
    item = value.get(key, value.get("default", {}))
    return item if isinstance(item, dict) else {}


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
    if quality is None:
        if not source or source == "unknown":
            quality = "source_missing"
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
        "evidenceHash": _hash(payload),
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
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    quote_meta = _provenance(quote_data, "quote")
    fundamentals_meta = _provenance(quote_data, "fundamentals")
    quote_time = _iso(quote_meta.get("effectiveDate") or quote_data.get("updatedAt"))
    ingested = _iso(quote_meta.get("ingestedAt") or quote_data.get("updatedAt"))
    records = [
        _record(
            "quote", str(quote_meta.get("source", "unknown")), str(quote_meta.get("dataset", "unknown")),
            quote_time, _iso(quote_meta.get("availableAt")), ingested, quote_data.get("quotes", {}),
            conflict_status=str(quote_meta.get("conflictStatus", "unknown")),
        ),
        _record(
            "fundamentals", str(fundamentals_meta.get("source", "unknown")), str(fundamentals_meta.get("dataset", "unknown")),
            _iso(fundamentals_meta.get("effectiveDate")), _iso(fundamentals_meta.get("availableAt")),
            _iso(fundamentals_meta.get("ingestedAt") or quote_data.get("updatedAt")), quote_data.get("fundamentals", {}),
            conflict_status=str(fundamentals_meta.get("conflictStatus", "unknown")),
        ),
        _record(
            "corporate_actions", str(actions.get("source", "FinMind")), str(actions.get("dataset", "TaiwanStockDividendResult")),
            _iso(actions.get("period", {}).get("end") if isinstance(actions.get("period"), dict) else None),
            _iso(actions.get("availableAt")), _iso(actions.get("updatedAt")), actions.get("events", []),
            conflict_status=str(actions.get("conflictStatus", "unknown")),
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
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": "layered-hybrid-c1",
        "generatedAt": generated_at,
        "certified": not blockers,
        "blockers": blockers,
        "records": records,
        "contractHash": _hash(records),
    }

