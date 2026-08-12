"""Offline identity and PIT-readiness preflight for the shipped strategy.

This module deliberately does *not* rank stocks or calculate performance.  It
freezes the deterministic part of the comprehensive production screen, checks
that a backtest adapter describes the same rule, and measures whether every
expected entity/decision-date/input has auditable point-in-time metadata.

Passing this preflight only means that it is legitimate to begin a performance
study.  It never enables advice, promotion, notifications, or trading.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "production-strategy-validation-preflight-v1"
STRATEGY_IDENTITY = "production-comprehensive-v1"

# These pins bind the specification to the exact production selection path on
# main at the time the contract was registered.  A source change must update
# the contract and therefore produces a new strategySpecHash.
SOURCE_PINS = {
    "candidate_manifest.py": "0963853c011f95af1cdfceef8e66d75773f3de9d658db7c2d41b9fb7690e6dbc",
    "daily_report.py": "c3ce38aa07e5b27ef00cb8de3b78d2147b497fd9be7f81462bdc9c1011379efc",
    "data_contract.py": "cea060fbe8bfcc25818be8cdbd9840891c433e644ada69e33c3d1f559c7f57a8",
    "scoring.py": "bcbd59d8ad215dbe8cc4ea09e2d43e55896dc12eb378bf35937741be1cafe9c5",
    "strategy_tracker.py": "c42556a24428c44273f0369411ae5aa4e173022a29d0f9641e3dadb8b7f6ca15",
}

# Only fixed, reviewed producers may describe evidence to this preflight.  The
# hashes identify code, not market data.  A fixture can prove that a metadata
# package obeys this contract; it cannot authenticate that the package came
# from an official provider, so this node never sets ``pitCertified`` true.
EVIDENCE_PRODUCER_PINS = {
    "freeze_lineage_summary.py": "6d183139f9a81081c92e6fe09593ca12af74959208894d6e8eef7dfc523249e3",
    "lineage_replay.py": "69b5cb18dfd0791273c14238a89a0da93a56fab3f60dad9019d666eb41ca03c6",
    "market_membership_snapshots.py": "80fdb3c919d859d08eb30f05c1591be59f3ca22ac91d58fc64da0ec45acfc9c7",
    "point_in_time_fundamentals.py": "24cdbe37e210bad6a89944b9fd18804bd17b766070e08028c9df776f1d583d0e",
    "strategy_backtest.py": "f07dd6c04e234ffe8121887883ac5696154af1aa1767686802c24d42d02f459a",
}

AUTHORITY_CONTRACTS = {
    "official_membership_v1": {
        "source": "TWSE-TPEx-official",
        "dataset": "historical-market-membership-v1",
        "producer": "market_membership_snapshots.py",
    },
    "official_quote_v1": {
        "source": "TWSE-TPEx-official",
        "dataset": "daily-trading-observation-v1",
        "producer": "freeze_lineage_summary.py",
    },
    "official_fundamentals_v1": {
        "source": "MOPS-official",
        "dataset": "pit-financial-disclosure-v1",
        "producer": "point_in_time_fundamentals.py",
    },
    "official_actions_v1": {
        "source": "TWSE-TPEx-MOPS-official",
        "dataset": "corporate-actions-and-terminal-events-v1",
        "producer": "freeze_lineage_summary.py",
    },
}

COMPREHENSIVE_WEIGHTS = {
    "change": 9,
    "debt": 10,
    "dividend": 7,
    "eps": 12,
    "pb": 6,
    "pe": 10,
    "revenue": 15,
    "roe": 12,
    "trend20": 12,
    "trend5": 7,
}

# Every entity on every decision date must have one deterministic state for
# every requirement.  A legitimate structural absence is allowed; silent
# exclusion or an unclassified cache miss is not.
REQUIRED_REQUIREMENTS = (
    "market.membership",
    "market.listing_entry",
    "market.listing_exit",
    "quote.price",
    "quote.volume",
    "quote.previous_close",
    "quote.ma5_history",
    "quote.ma20_history",
    "fundamentals.revenue_yoy",
    "fundamentals.eps",
    "fundamentals.roe",
    "fundamentals.debt_ratio",
    "fundamentals.pe_inputs",
    "fundamentals.pb",
    "fundamentals.dividend_yield",
    "fundamentals.financial_history_years",
    "corporate_actions.cash_dividend",
    "corporate_actions.stock_dividend_ex_rights",
    "corporate_actions.terminal_value",
)

ALLOWED_RECORD_KEYS = frozenset({
    "entityId", "requirementId", "status", "source", "dataset",
    "schemaVersion", "effectiveDate", "availableAt", "evidenceHash",
    "quality", "conflictStatus", "sourceRevision", "authorityContractHash",
    "producerHash", "evidenceRole",
})
ALLOWED_SNAPSHOT_KEYS = frozenset({
    "decisionAsOf", "expectedEntities", "universeEvidence", "records",
})
ALLOWED_UNIVERSE_KEYS = frozenset({
    "source", "dataset", "schemaVersion", "effectiveDate", "availableAt",
    "evidenceHash", "entitySetHash", "expectedEntityCount", "quality",
    "conflictStatus", "sourceRevision", "authorityContractHash", "producerHash",
    "replayPolicyVersion", "replayProducerHash", "selectedVersionHash",
})
ALLOWED_ROOT_KEYS = frozenset({"schemaVersion", "snapshots"})

ACCEPTED_STATES = frozenset({"observed_as_of", "not_yet_published", "not_applicable"})
ALL_STATES = ACCEPTED_STATES | {"source_missing", "conflict"}
SELECTION_OBSERVED_REQUIREMENTS = frozenset({
    "market.membership", "market.listing_entry", "quote.price", "quote.volume",
    "quote.previous_close", "quote.ma5_history", "quote.ma20_history",
})
FUNDAMENTAL_REQUIREMENTS = frozenset(
    requirement for requirement in REQUIRED_REQUIREMENTS if requirement.startswith("fundamentals.")
)
ACTION_REQUIREMENTS = frozenset(
    requirement for requirement in REQUIRED_REQUIREMENTS if requirement.startswith("corporate_actions.")
)

REQUIREMENT_POLICIES: dict[str, dict[str, Any]] = {}
for _requirement in REQUIRED_REQUIREMENTS:
    if _requirement.startswith("market."):
        _contract = "official_membership_v1"
    elif _requirement.startswith("quote."):
        _contract = "official_quote_v1"
    elif _requirement.startswith("fundamentals."):
        _contract = "official_fundamentals_v1"
    else:
        _contract = "official_actions_v1"
    if _requirement in SELECTION_OBSERVED_REQUIREMENTS:
        _states = {"observed_as_of": "observed_value"}
    elif _requirement == "market.listing_exit" or _requirement in ACTION_REQUIREMENTS:
        _states = {
            "observed_as_of": "observed_value",
            "not_applicable": "official_active_no_event",
        }
    else:
        _states = {
            "observed_as_of": "observed_value",
            "not_yet_published": "official_not_yet_published",
            "not_applicable": "official_not_applicable",
        }
    REQUIREMENT_POLICIES[_requirement] = {
        "authorityContract": _contract,
        "states": _states,
    }

ALLOWED_STATES_BY_REQUIREMENT = {
    requirement: frozenset(policy["states"])
    for requirement, policy in REQUIREMENT_POLICIES.items()
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENTITY_ID = re.compile(r"^[0-9]{4}$")
SAFE_REVISION = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
SAFE_TEXT = re.compile(r"^[A-Za-z0-9._:+/-]{1,120}$")
FORBIDDEN_KEYS = frozenset({
    "score", "rank", "return", "returns", "totalreturn", "annualizedreturn",
    "mdd", "pnl", "profit", "loss", "excess", "outcome", "outcomes",
    "performance", "recommendation", "candidates", "candidateorder", "raw",
    "rows", "url", "token", "secret", "authorization", "cookie",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def authority_contract_hash(contract_name: str) -> str:
    return digest({"name": contract_name, **AUTHORITY_CONTRACTS[contract_name]})


def strategy_spec() -> dict[str, Any]:
    """Return the immutable description of the current live selection rule."""
    spec = {
        "schemaVersion": 1,
        "strategyIdentity": STRATEGY_IDENTITY,
        "strategyTrackerVersion": "2.0",
        "candidateManifestSchemaVersion": 1,
        "reportMode": "comprehensive",
        "style": "comprehensive",
        "sourcePins": dict(SOURCE_PINS),
        "selection": {
            "weights": dict(COMPREHENSIVE_WEIGHTS),
            "minimumScore": 60,
            "minimumScoringCoverage": 70,
            "previewPicks": 3,
            "sort": ["score:desc", "coverage:desc", "volume:desc", "code:desc"],
            "scoreRounding": "python-round-nearest-even",
            "availableFactorNormalization": True,
            "numericBooleansRejected": True,
            "continuousTrend": False,
            "reversalAware": False,
            "universeIteration": "fundamentals keys with four-digit numeric code and numeric price",
            "explicitMinimumVolume": None,
        },
        "finalQualityGate": {
            "phase": "final_only",
            "adviceGate": {"status": "advice_candidate", "adviceEnabled": True},
            "ordering": [
                "rank_full_eligible_pool_once",
                "truncate_to_preview_top_3",
                "apply_candidate_manifest_quality_to_preview_only",
                "do_not_backfill_failed_preview_with_rank_4_or_later",
            ],
            "minimumAnalysisCoverage": 80,
            "requiredMetrics": ["revenueYoY", "eps", "roe", "debtRatio"],
            "minimumFinancialHistoryYears": 5,
            "corporateActionsVerified": True,
            "dataContractCertified": True,
        },
        "factorDefinitions": {
            "revenue": "thresholds:20/90,10/80,0/65,-10/45,else20",
            "eps": "positive=70,nonpositive=15,missing=None",
            "roe": "thresholds:20/90,15/80,8/65,5/45,else25",
            "debt": "lower-is-better:<=30/90,<=50/75,<=70/50,else20",
            "pe": "positive-only;<=12/85,<=20/75,<=30/60,<=45/40,else20",
            "pb": "positive-only;<=1.5/85,<=3/70,<=6/50,else30",
            "dividend": "3..8=85,>10=45,>=2=65,else35",
            "trend20": "price>=ma20=75,else35",
            "trend5": "price>=ma5=75,else35",
            "change": "positive=min(90,65+3x),nonpositive=max(15,50+3x)",
        },
        # The live product has no registered investment holding/execution/risk
        # policy yet.  Keeping these explicitly unregistered prevents a
        # screening identity from being mistaken for a validated strategy.
        "executionSpecStatus": "unregistered",
        "riskPolicyStatus": "unregistered",
        "eligiblePoolBenchmarkStatus": "unregistered",
        "llmRole": "explanation_only_no_score_or_rank_authority",
        "nonScoringContext": ["market_news", "macro_headlines", "ai_research_summary"],
    }
    return spec


def strategy_spec_hash() -> str:
    return digest(strategy_spec())


def production_engine_contract() -> dict[str, Any]:
    return {
        "selectionEntryPoint": "daily_report->scoring.candidates",
        "strategySpecHash": strategy_spec_hash(),
        "minimumVolumePolicy": "no_explicit_threshold",
        "universePolicy": "production_quote_and_fundamentals_snapshot",
        "finalQualityGate": "candidate_manifest_v1",
        "finalQualityGateOrdering": "top3_then_quality_no_backfill",
        "finalEligibility": "phase_final_and_advice_candidate_and_advice_enabled",
        "executionAccounting": "not_registered_for_live_screen",
    }


def current_backtest_engine_contract() -> dict[str, Any]:
    """Describe the current adapter honestly; this is expected to mismatch."""
    return {
        "selectionEntryPoint": "strategy_backtest->scoring.candidates",
        "strategySpecHash": strategy_spec_hash(),
        "minimumVolumePolicy": "cli_default_500000",
        "universePolicy": "signal_quotes_filtered_by_minimum_volume",
        "finalQualityGate": "not_equivalent_to_candidate_manifest_v1",
        "finalQualityGateOrdering": "not_registered",
        "finalEligibility": "not_registered",
        "executionAccounting": "legacy_mean_filled_slots_with_stale_exit_fallback",
    }


def _aware_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _effective_time(value: Any, decision_as_of: datetime) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            parsed = datetime.fromisoformat(value).replace(tzinfo=decision_as_of.tzinfo)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _safe_scalar(value: Any, *, revision: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "http://" in lowered or "https://" in lowered or "?" in value
        or "token" in lowered or "secret" in lowered or "authorization" in lowered
        or "cookie" in lowered or "{" in value or "[" in value
    ):
        return False
    matcher = SAFE_REVISION if revision else SAFE_TEXT
    return matcher.fullmatch(value) is not None


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in FORBIDDEN_KEYS or _contains_forbidden(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    elif isinstance(value, float) and not math.isfinite(value):
        return True
    elif isinstance(value, str):
        lowered = value.lower()
        if (
            any(ord(character) < 32 and character not in "\t" for character in value)
            or "http://" in lowered or "https://" in lowered or "token=" in lowered
            or "authorization:" in lowered or "bearer " in lowered
        ):
            return True
    return False


def _record_valid(record: dict[str, Any], decision_as_of: datetime) -> tuple[bool, str | None]:
    if set(record) != ALLOWED_RECORD_KEYS:
        return False, "record_contract_invalid"
    if record.get("requirementId") not in REQUIRED_REQUIREMENTS:
        return False, "requirement_unknown"
    if record.get("status") not in ALL_STATES:
        return False, "status_unknown"
    requirement = record["requirementId"]
    policy = REQUIREMENT_POLICIES[requirement]
    if record.get("status") not in ALLOWED_STATES_BY_REQUIREMENT[requirement] \
            and record.get("status") not in {"source_missing", "conflict"}:
        return False, "status_not_allowed_for_requirement"
    if not isinstance(record.get("entityId"), str) or not ENTITY_ID.fullmatch(record["entityId"]):
        return False, "entity_invalid"
    if not isinstance(record.get("schemaVersion"), int) or isinstance(record["schemaVersion"], bool) or record["schemaVersion"] < 1:
        return False, "record_schema_invalid"
    available = _aware_time(record.get("availableAt"))
    effective = _effective_time(record.get("effectiveDate"), decision_as_of)
    state = record["status"]
    if state == "source_missing":
        return False, "source_missing"
    if state == "conflict" or record.get("conflictStatus") != "no_conflict":
        return False, "conflict_unresolved"
    if state != "source_missing":
        contract_name = policy["authorityContract"]
        authority = AUTHORITY_CONTRACTS[contract_name]
        if (
            record.get("source") != authority["source"]
            or record.get("dataset") != authority["dataset"]
            or record.get("authorityContractHash") != authority_contract_hash(contract_name)
            or record.get("producerHash") != EVIDENCE_PRODUCER_PINS[authority["producer"]]
            or record.get("evidenceRole") != policy["states"].get(state)
            or effective is None
            or available is None
            or not isinstance(record.get("evidenceHash"), str)
            or not HEX64.fullmatch(record["evidenceHash"])
            or not _safe_scalar(record.get("sourceRevision"), revision=True)
            or not _safe_scalar(record.get("source"))
            or not _safe_scalar(record.get("dataset"))
        ):
            return False, "provenance_incomplete"
    if state in ACCEPTED_STATES and (available is None or available > decision_as_of):
        return False, "post_as_of_evidence"
    if state in ACCEPTED_STATES and (effective is None or effective > decision_as_of):
        return False, "future_effective_evidence"
    if state in ACCEPTED_STATES and record.get("quality") != "verified":
        return False, "quality_unverified"
    return True, None


def _universe_valid(value: Any, entities: list[str], decision_as_of: datetime) -> tuple[bool, str | None]:
    if not isinstance(value, dict) or set(value) != ALLOWED_UNIVERSE_KEYS:
        return False, "universe_evidence_contract_invalid"
    available = _aware_time(value.get("availableAt"))
    effective = _effective_time(value.get("effectiveDate"), decision_as_of)
    contract_name = "official_membership_v1"
    authority = AUTHORITY_CONTRACTS[contract_name]
    if (
        value.get("source") != authority["source"]
        or value.get("dataset") != authority["dataset"]
        or value.get("authorityContractHash") != authority_contract_hash(contract_name)
        or value.get("producerHash") != EVIDENCE_PRODUCER_PINS[authority["producer"]]
        or value.get("replayPolicyVersion") != "lineage-replay-v1"
        or value.get("replayProducerHash") != EVIDENCE_PRODUCER_PINS["lineage_replay.py"]
        or not isinstance(value.get("schemaVersion"), int)
        or isinstance(value.get("schemaVersion"), bool)
        or value["schemaVersion"] < 1
        or effective is None or effective > decision_as_of
        or available is None or available > decision_as_of
        or not isinstance(value.get("evidenceHash"), str) or not HEX64.fullmatch(value["evidenceHash"])
        or not isinstance(value.get("selectedVersionHash"), str) or not HEX64.fullmatch(value["selectedVersionHash"])
        or not _safe_scalar(value.get("sourceRevision"), revision=True)
        or not _safe_scalar(value.get("source"))
        or not _safe_scalar(value.get("dataset"))
        or value.get("quality") != "verified"
        or value.get("conflictStatus") != "no_conflict"
    ):
        return False, "universe_evidence_not_certified"
    if (
        value.get("expectedEntityCount") != len(entities)
        or value.get("entitySetHash") != digest(sorted(entities))
    ):
        return False, "fixed_denominator_hash_mismatch"
    return True, None


def evaluate(payload: Any) -> dict[str, Any]:
    """Evaluate metadata only and return a compact, non-promotable report."""
    spec = strategy_spec()
    spec_hash = digest(spec)
    blockers: list[str] = []
    if not isinstance(payload, dict) or set(payload) != ALLOWED_ROOT_KEYS or _contains_forbidden(payload):
        blockers.append("input_contract_invalid")
        payload = {}
    if payload.get("schemaVersion") != 1:
        blockers.append("input_schema_invalid")

    # A caller-supplied equality hash is not parity evidence.  A later node
    # must run both engines over the same frozen fixture and provide a pinned
    # replay artifact.  Until then this field stays deterministically false.
    fixture_parity = False
    blockers.append("deterministic_fixture_parity_not_implemented")
    production_contract = production_engine_contract()
    backtest_contract = current_backtest_engine_contract()
    engine_contract_parity = production_contract == backtest_contract
    if not engine_contract_parity:
        blockers.append("production_backtest_engine_mismatch")

    snapshots = payload.get("snapshots")
    total_expected = total_present = total_accepted = 0
    status_counts = {state: 0 for state in sorted(ALL_STATES)}
    requirement_counts = {requirement: {"expected": 0, "accepted": 0} for requirement in REQUIRED_REQUIREMENTS}
    snapshot_summaries: list[dict[str, Any]] = []
    if not isinstance(snapshots, list) or not snapshots:
        blockers.append("decision_snapshots_missing")
        snapshots = []
    seen_decisions: set[str] = set()
    scoring_inputs_ready = True
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or set(snapshot) != ALLOWED_SNAPSHOT_KEYS:
            blockers.append("snapshot_contract_invalid")
            continue
        decision_text = snapshot.get("decisionAsOf")
        decision = _aware_time(decision_text)
        entities = snapshot.get("expectedEntities")
        records = snapshot.get("records")
        universe_evidence = snapshot.get("universeEvidence")
        decision_identity = (
            decision.astimezone(timezone.utc).isoformat() if decision is not None else None
        )
        if decision is None or decision_identity in seen_decisions:
            blockers.append("decision_as_of_invalid")
            continue
        seen_decisions.add(decision_identity)
        if (
            not isinstance(entities, list) or not entities
            or any(not isinstance(entity, str) or not ENTITY_ID.fullmatch(entity) for entity in entities)
            or len(set(entities)) != len(entities)
        ):
            blockers.append("fixed_denominator_invalid")
            continue
        universe_valid, universe_reason = _universe_valid(universe_evidence, entities, decision)
        if not universe_valid and universe_reason:
            blockers.append(universe_reason)
        if not isinstance(records, list):
            blockers.append("record_set_invalid")
            records = []
        expected_pairs = {(entity, requirement) for entity in entities for requirement in REQUIRED_REQUIREMENTS}
        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        out_of_scope = 0
        for record in records:
            if not isinstance(record, dict):
                blockers.append("record_contract_invalid")
                continue
            pair = (record.get("entityId"), record.get("requirementId"))
            if pair not in expected_pairs:
                out_of_scope += 1
                continue
            by_pair.setdefault(pair, []).append(record)
        duplicate_pairs = sum(1 for items in by_pair.values() if len(items) != 1)
        present_pairs = sum(1 for items in by_pair.values() if len(items) == 1)
        accepted_pairs = 0
        local_counts = {state: 0 for state in sorted(ALL_STATES)}
        for entity, requirement in expected_pairs:
            requirement_counts[requirement]["expected"] += 1
            items = by_pair.get((entity, requirement), [])
            if len(items) != 1:
                continue
            record = items[0]
            state = record.get("status")
            if state in local_counts:
                local_counts[state] += 1
                status_counts[state] += 1
            valid, reason = _record_valid(record, decision)
            if valid:
                accepted_pairs += 1
                requirement_counts[requirement]["accepted"] += 1
                if requirement in SELECTION_OBSERVED_REQUIREMENTS | FUNDAMENTAL_REQUIREMENTS \
                        and record.get("status") != "observed_as_of":
                    scoring_inputs_ready = False
            elif reason:
                blockers.append(reason)
                scoring_inputs_ready = False
        missing = len(expected_pairs) - present_pairs
        if missing:
            blockers.append("pit_requirement_coverage_incomplete")
        if duplicate_pairs:
            blockers.append("multiple_versions_without_unique_selection")
        if out_of_scope:
            blockers.append("denominator_scope_drift")
        total_expected += len(expected_pairs)
        total_present += present_pairs
        total_accepted += accepted_pairs
        snapshot_summaries.append({
            "decisionAsOfHash": digest(decision_text),
            "scopeHash": digest(sorted(entities)),
            "expected": len(expected_pairs),
            "present": present_pairs,
            "accepted": accepted_pairs,
            "missing": missing,
            "duplicate": duplicate_pairs,
            "outOfScope": out_of_scope,
            "statusCounts": local_counts,
        })

    blockers.extend(["execution_spec_unregistered", "risk_policy_unregistered", "eligible_pool_benchmark_unregistered"])
    blockers = list(dict.fromkeys(blockers))
    pit_blockers = (
        "input_contract_invalid", "input_schema_invalid", "decision_snapshots_missing",
        "snapshot_contract_invalid", "decision_as_of_invalid", "fixed_denominator_invalid",
        "record_set_invalid", "record_contract_invalid", "requirement_unknown", "status_unknown",
        "entity_invalid", "record_schema_invalid", "provenance_incomplete", "post_as_of_evidence",
        "absence_semantics_invalid", "future_effective_evidence", "conflict_unresolved", "quality_unverified", "source_missing",
        "pit_requirement_coverage_incomplete", "multiple_versions_without_unique_selection",
        "denominator_scope_drift",
        "universe_evidence_contract_invalid", "universe_evidence_not_certified",
        "fixed_denominator_hash_mismatch", "status_not_allowed_for_requirement",
    )
    pit_metadata_complete = (
        total_expected > 0
        and total_accepted == total_expected
        and not any(code in blockers for code in pit_blockers)
    )
    scoring_inputs_ready = pit_metadata_complete and scoring_inputs_ready
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "strategyValidated": False,
        "promotionEligible": False,
        "strategyIdentity": STRATEGY_IDENTITY,
        "strategySpecHash": spec_hash,
        "pitRequirementsHash": digest({
            "requirements": list(REQUIRED_REQUIREMENTS),
            "requirementPolicies": REQUIREMENT_POLICIES,
            "authorityContracts": AUTHORITY_CONTRACTS,
            "producerPins": EVIDENCE_PRODUCER_PINS,
            "recordKeys": sorted(ALLOWED_RECORD_KEYS),
            "universeKeys": sorted(ALLOWED_UNIVERSE_KEYS),
        }),
        "strategySpecRegistered": True,
        "sourcePinsRegistered": True,
        "strategyIdentityCertified": False,
        "productionBacktestParity": False,
        "engineContractParity": engine_contract_parity,
        "fixtureParity": fixture_parity,
        "paritySummary": {},
        "readyForPerformanceEvaluation": False,
        "dataReady": False,
        "pitCertified": False,
        "pitMetadataComplete": pit_metadata_complete,
        "provenanceMetadataComplete": pit_metadata_complete,
        "provenanceCoverageCertified": False,
        "scoringInputsMetadataComplete": scoring_inputs_ready,
        "scoringInputsReady": False,
        "coverage": {
            "expected": total_expected,
            "present": total_present,
            "accepted": total_accepted,
            "presentRate": total_present / total_expected if total_expected else 0.0,
            "acceptedRate": total_accepted / total_expected if total_expected else 0.0,
            "statusCounts": status_counts,
            "requirements": requirement_counts,
        },
        "snapshots": snapshot_summaries,
        "engineContractHashes": {
            "production": digest(production_contract),
            "backtest": digest(backtest_contract),
        },
        "executionSpecStatus": "unregistered",
        "riskPolicyStatus": "unregistered",
        "eligiblePoolBenchmarkStatus": "unregistered",
        "blockers": blockers,
    }
    report["reportDigest"] = digest(report)
    return report


def run(payload: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Default-off boundary.  Disabled mode does not inspect its payload."""
    if not enabled:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "policyVersion": POLICY_VERSION,
            "mode": "disabled",
            "strategyValidated": False,
            "promotionEligible": False,
        }
    return evaluate(payload)
