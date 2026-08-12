"""One executable selection path for the comprehensive product strategy.

The website and its historical evaluator must not merely describe equivalent
rules: they must invoke the same ranking and final-quality functions.  This
module owns that deterministic boundary.  It deliberately stops before
execution, risk, performance, promotion, or advice decisions.

``rank_and_assess`` ranks the complete eligible pool exactly once, truncates it
to the production preview, applies the production quality gate only to those
preview names, and never backfills a rejected preview name with rank four or
later.  A backtest may consume ``selectedTuples`` only when it supplies a
point-in-time evidence snapshot; absence of such evidence is fail-closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from scoring import DEFAULT_PICKS, candidates, number


SCHEMA_VERSION = 1
POLICY_VERSION = "actual-comprehensive-selection-v1"
STYLE = "comprehensive"
REQUIRED_FINAL_METRICS = ("revenueYoY", "eps", "roe", "debtRatio")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def quality_blockers(
    code: str,
    coverage: int,
    fundamentals: dict[str, Any],
    actions: dict[str, Any],
    contract_blockers: Iterable[str] = (),
) -> list[str]:
    """Apply the exact final-quality gate used by the product manifest."""
    blockers: list[str] = []
    if coverage < 80:
        blockers.append("analysis_coverage_below_80")
    blockers.extend(
        f"missing_{metric}"
        for metric in REQUIRED_FINAL_METRICS
        if not number(fundamentals.get(metric))
    )
    history_years = fundamentals.get("financialHistoryYears", 0)
    if not number(history_years) or history_years < 5:
        blockers.append("fewer_than_five_financial_years")
    queried = {str(item) for item in actions.get("queried_codes", [])}
    failures = actions.get("failures", {}) if isinstance(actions.get("failures"), dict) else {}
    if code not in queried or code in failures:
        blockers.append("corporate_actions_not_verified")
    blockers.extend(str(item) for item in contract_blockers)
    return list(dict.fromkeys(blockers))


def rank_pool(
    quotes: dict[str, dict[str, Any]],
    fundamentals: dict[str, dict[str, Any]],
) -> list[tuple]:
    """Rank the complete production comprehensive pool, without a volume filter."""
    return candidates(STYLE, quotes, fundamentals, picks=None)


def assess_ranked_preview(
    ranked: dict[str, list[tuple]],
    actions: dict[str, Any],
    contract_blockers: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return manifest-shaped assessments for an already truncated preview."""
    preview: list[dict[str, Any]] = []
    for style, items in ranked.items():
        for rank, item in enumerate(items, 1):
            score, coverage, code, quote, fundamentals = item
            blockers = quality_blockers(
                str(code), int(coverage), fundamentals, actions, contract_blockers
            )
            preview.append({
                "code": str(code),
                "name": quote.get("name", code),
                "style": style,
                "rank": rank,
                "score": score,
                "coverage": coverage,
                "entryPrice": quote.get("price"),
                "quality": {"passed": not blockers, "blockers": blockers},
            })
    return preview


def _cutoff_tie(pool: list[tuple], preview_picks: int) -> bool:
    if preview_picks < 1 or len(pool) <= preview_picks:
        return False
    selected_boundary = pool[preview_picks - 1]
    excluded_boundary = pool[preview_picks]
    selected_key = (
        selected_boundary[0], selected_boundary[1],
        selected_boundary[3].get("volume") or 0,
    )
    excluded_key = (
        excluded_boundary[0], excluded_boundary[1],
        excluded_boundary[3].get("volume") or 0,
    )
    return selected_key == excluded_key


def rank_and_assess(
    quotes: dict[str, dict[str, Any]],
    fundamentals: dict[str, dict[str, Any]],
    *,
    actions: dict[str, Any] | None,
    contract_blockers: Iterable[str] | None,
    preview_picks: int = DEFAULT_PICKS,
) -> dict[str, Any]:
    """Execute actual ranking + top-N quality/no-backfill semantics.

    ``actions`` and ``contract_blockers`` must both be supplied by the caller's
    evidence adapter.  They are not authenticated here; this layer proves
    selection parity only, never evidence authority or strategy validity.
    """
    if preview_picks != DEFAULT_PICKS:
        raise ValueError("comprehensive_preview_picks_must_match_production")
    pool = rank_pool(quotes, fundamentals)
    preview = pool[:preview_picks]
    evidence_supplied = (
        isinstance(actions, dict)
        and isinstance(contract_blockers, (list, tuple))
        and all(isinstance(item, str) for item in contract_blockers)
    )
    if evidence_supplied:
        assessed = assess_ranked_preview(
            {STYLE: preview}, actions, tuple(contract_blockers or ())
        )
    else:
        assessed = assess_ranked_preview(
            {STYLE: preview}, {}, ("selection_evidence_missing",)
        )
    passed_codes = {
        item["code"] for item in assessed if item["quality"]["passed"] is True
    }
    selected = [item for item in preview if str(item[2]) in passed_codes]
    rows = [
        {
            "code": str(item[2]),
            "score": item[0],
            "coverage": item[1],
            "volume": item[3].get("volume") or 0,
        }
        for item in pool
    ]
    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "style": STYLE,
        "previewPicks": preview_picks,
        "fullPool": rows,
        "preview": assessed,
        "qualityPassedCodes": [str(item[2]) for item in selected],
        "noBackfill": True,
        "cutoffTieDependent": _cutoff_tie(pool, preview_picks),
        "selectionEvidenceSupplied": evidence_supplied,
    }
    summary["selectionDigest"] = digest(summary)
    return {
        **summary,
        # Internal tuples are deliberately excluded from selectionDigest.  The
        # public summary is deterministic JSON while callers can continue into
        # execution accounting without rebuilding or re-ranking the pool.
        "poolTuples": pool,
        "previewTuples": preview,
        "selectedTuples": selected,
    }
