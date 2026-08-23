"""Deterministic candidate hand-off between data enrichment and AI review."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from actual_comprehensive_selection import (
    REQUIRED_FINAL_METRICS,
    assess_ranked_preview,
    quality_blockers,
)
from data_contract import build_contract
from strategy_tracker import STRATEGY_VERSION


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def evidence_sha256(path: Path) -> str | None:
    """Hash evidence content while excluding retrieval-cache diagnostics."""
    try:
        payload = load_json(path)
    except (ValueError, OverflowError, UnicodeError):
        return None
    if not payload:
        return file_sha256(path)
    payload.pop("cache", None)
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        return None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _quality_blockers(
    code: str,
    coverage: int,
    fundamentals: dict[str, Any],
    actions: dict[str, Any],
) -> list[str]:
    """Backward-compatible wrapper around the shared production gate."""
    return quality_blockers(code, coverage, fundamentals, actions)


def build_manifest(
    *,
    report_date: str,
    report_mode: str,
    phase: str,
    ranked: dict[str, list[tuple]],
    quote_data: dict[str, Any],
    advice_gate: dict[str, Any],
    actions: dict[str, Any],
    news_path: Path,
    actions_path: Path,
    gate_path: Path,
    pit_path: Path | None = None,
) -> dict[str, Any]:
    news = load_json(news_path)
    pit_status = load_json(pit_path) if pit_path else {}
    contract = build_contract(quote_data, actions, news, pit_status)
    preview = assess_ranked_preview(ranked, actions, contract["blockers"])
    advice_enabled = (
        advice_gate.get("status") == "advice_candidate"
        and advice_gate.get("adviceEnabled") is True
    )
    eligible = [item for item in preview if advice_enabled and item["quality"]["passed"]]
    return {
        "schemaVersion": 1,
        "reportDate": report_date,
        "reportMode": report_mode,
        "phase": phase,
        "strategyVersion": STRATEGY_VERSION,
        "quoteUpdatedAt": quote_data.get("updatedAt"),
        "adviceGate": {
            "status": advice_gate.get("status", "research_only"),
            "adviceEnabled": advice_enabled,
            "blockers": advice_gate.get("blockers", []),
        },
        "candidateOrder": [item["code"] for item in preview],
        "previewCandidates": preview,
        "eligibleCandidates": eligible if phase == "final" else [],
        "evidenceInputs": {
            "gateSha256": file_sha256(gate_path),
            "newsSha256": evidence_sha256(news_path),
            "corporateActionsSha256": evidence_sha256(actions_path),
            "newsUpdatedAt": load_json(news_path).get("updatedAt"),
            "corporateActionsPeriod": actions.get("period"),
        },
        "dataContract": contract,
    }
