"""Performance-blind preregistration for the actual comprehensive strategy.

The policy in this module was explicitly adopted before opening any new
qualified historical archive or confirmatory outcome.  This module is an
offline contract validator only: it does not read market data, run selection,
calculate returns, or attach to any formal advice or promotion consumer.

The contract cannot seal itself.  A later verifier must bind the exact
``preregistrationHash`` to the server-side GitHub merge receipt for this node;
until then every formal status remains false.
"""
from __future__ import annotations

import hashlib
import json
import math
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "actual-comprehensive-validation-preregistration-v1"
BASE_MAIN_SHA = "4a31db49a1f1f9e04dd5f42a8e4c6862269001f0"
BASE_PR_NUMBER = 117
BASE_MERGED_AT = "2026-08-13T12:47:06Z"

MAX_NODES = 20_000
MAX_DEPTH = 16
MAX_STRING = 4_096
MAX_CANONICAL_BYTES = 256_000

_SOURCE_PIN_ITEMS = (
    ("actual_comprehensive_selection.py", "840d397957c172b4a543bd7ecc57911e787b93f288366dc016c30f1feadb4be0"),
    ("actual_comprehensive_selection_parity.py", "59ab0ad5175648c49825340569aaacf6000ed0536aeb958dc7fc3aec9094eab8"),
    ("actual_comprehensive_outcome_accounting.py", "e69c13b9f33e36427d6dee86980664f9e66dbd07b43b8cc01f6752989c6b4099"),
    ("execution_accounting.py", "86191bc936dbd3f2f79ef44b084ff8c89f6588e98edeb2e10873d60d5b842c63"),
    ("backtest.py", "aea1fd294108d7083049cb38c1f18e64e0c49e5b3b0ea092994371002b4b3e24"),
    ("strategy_backtest.py", "fc3a81c71349e07367e2d690563e0bc8172c4291ba10ce9401a1e308d069fda7"),
    ("candidate_manifest.py", "e5471941684838779a6e658de8efbce3799300a4fb0c609854f528f0ccd7aa6e"),
    ("daily_report.py", "ca8a78cffeffe63c8503196f28cc58c7f06b7dd05fae12925e339b4c27ce9150"),
    ("data_contract.py", "f91475db7ece2dffef9bed86a1a5c1b0dbf12d4ecaba7a4c6b51447624987c45"),
    ("scoring.py", "3db3aaa02dbf9f419da48a6150ff8e479a42ec8f0bd0b2f55cd9ad74f456a50c"),
    ("strategy_tracker.py", "9468603d5e668e5e795852d7a231a5d2163895a6fe4cff0d6b5318540cb9d3de"),
    ("production_strategy_validation_preflight.py", "d4bf6b7dea61a2b30512731ee8a99955c907d1dbd78aa39b1d3412c154e62462"),
    ("point_in_time_fundamentals.py", "91e3100a138355a08ddf4e8194a0af3f3209d16e5c72f8a27849b085e08b347a"),
    ("authoritative_pit_coverage_certification.py", "8166184916725eb3662d077d5f497ad94c78ce6401fa97cf8207475d6d8d688f"),
    ("official_full_market_population.py", "e383c82a10fdcfd61142ffcf650505b6b34783ff6cf1b0d4973c2927775b3a40"),
    ("market_population_contract.py", "d06a065fa4148063cb89b4d54f0e698190cade140caebdcd3d27aed842d59809"),
    ("official_population_source_admission.py", "73253532654f4cdb30661e4c534731363dbb2bab67590a56870c840af4587ce1"),
    ("official_population_artifact_receipt.py", "2b4d8a19ded8ccf6f02d36b42163e53a0b44ba4068862bc72954e93e23756bc5"),
    ("forward_population_observation.py", "53ef3ffe8fa09a0275551cce3d34a9205cf0de10b954e582315a80ac14ecc572"),
    ("forward_population_ledger_store.py", "37feb51bd369c9661cf5933e6af2eda36fb2f78ce98645da55c2d699685ebcb3"),
)

# Public inspection view only.  Validation below uses an import-time canonical
# snapshot and never consults this object again.
SOURCE_PINS = MappingProxyType(dict(_SOURCE_PIN_ITEMS))

def drifted_sources(sources: Any, *, _pins: tuple = _SOURCE_PIN_ITEMS) -> tuple[str, ...]:
    """Registered files whose supplied text differs from what was registered.

    A confirmatory run is evidence about the rule that was registered, and
    nothing else. Once production code moves on, the honest response is to
    register again before running -- not to edit this record so a check passes,
    and not to run anyway and call the result preregistered.

    The caller supplies ``{name: text}`` rather than a directory: this module
    stays free of filesystem and network access so that reading it can have no
    effect on anything, and a missing entry counts as drift rather than as
    nothing to check.

    This is a runtime gate, not a test assertion. Drift is expected while
    development continues; it only disqualifies at the moment confirmatory
    evidence is produced.
    """
    if not isinstance(sources, dict):
        return tuple(name for name, _ in _pins)
    drifted = []
    for name, pinned in _pins:
        text = sources.get(name)
        if not isinstance(text, str):
            drifted.append(name)
            continue
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != pinned:
            drifted.append(name)
    return tuple(drifted)


STRATEGY_SPEC_HASH = "e66b2f81b8f6400b6b6239b625f50f3b9a7c0e789ae63250ee6fc261272ae616"
PIT_REQUIREMENTS_HASH = "a7fe498e4367e0b176e007c2b66a1a16ca00edd62febfa94e3bcf5b34a70fb47"

FORBIDDEN_KEYS = frozenset({
    "return", "returns", "pnl", "profit", "performance", "observedmdd",
    "winner", "winninghorizon", "ticker", "tickers", "rank", "ranking",
    "price", "prices", "score", "recommendation", "adviceenabled",
    "promotioneligible", "strategyvalidated",
    "token", "secret", "password", "authorization", "cookie", "raw", "rows",
    "threadid", "turnid", "itemid", "usermessageitemid", "rawmessage",
    "messagewithtrailinglf", "normalizedmessage",
})

FORBIDDEN_KEY_PREFIXES = (
    "authorization", "cookie", "headers", "password", "privatekey",
    "query", "raw", "rows", "secret", "token", "url", "uri",
)
FORBIDDEN_KEY_SUFFIXES = (
    "authorization", "cookie", "password", "privatekey", "profit", "pnl",
    "return", "returns", "secret", "token",
)
FORBIDDEN_KEY_FRAGMENTS = (
    "accesscredential", "actualmdd", "clientsecret", "observedmdd",
    "performanceresult", "pricedata", "pricepath", "pricerows",
    "refreshtoken",
)

FIXED_BLOCKERS = (
    "node57_main_merge_seal_receipt_required",
    "trusted_user_policy_decision_receipt_verification_required",
    "confirmatory_evaluator_unregistered",
    "return_free_inventory_receipt_not_created",
    "authoritative_pit_archive_unavailable",
    "confirmatory_outcomes_not_generated",
    "live_position_state_not_integrated",
    "formal_advice_requires_separate_confirmation",
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _bounded_json(value: Any, depth: int = 0, budget: list[int] | None = None) -> bool:
    if budget is None:
        budget = [MAX_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        return False
    if value is None or type(value) in (str, int, float, bool):
        if type(value) is str:
            return len(value) <= MAX_STRING and not any(ord(char) < 32 for char in value)
        if type(value) is float:
            return math.isfinite(value)
        if type(value) is int:
            return abs(value) <= 10**18
        return True
    if type(value) is list:
        return len(value) <= MAX_NODES and all(
            _bounded_json(item, depth + 1, budget) for item in value
        )
    if type(value) is dict:
        return len(value) <= MAX_NODES and all(
            isinstance(key, str) and len(key) <= 128
            and _bounded_json(item, depth + 1, budget)
            for key, item in value.items()
        )
    return False


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            # Threshold and method names may contain "mdd"; only observed MDD
            # is prohibited.  Exact expected policy keys are compared later.
            forbidden_key = (
                normalized in FORBIDDEN_KEYS
                or normalized.startswith(FORBIDDEN_KEY_PREFIXES)
                or normalized.endswith(FORBIDDEN_KEY_SUFFIXES)
                or any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS)
            )
            if forbidden_key or _contains_forbidden(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in (
            "://", "bearer ", "authorization:", "token=", "password=",
            "cookie=", "set-cookie:", "-----begin ",
        ))
    return False


def _build_preregistration_manifest() -> dict[str, Any]:
    source_pins = dict(_SOURCE_PIN_ITEMS)
    selection_contract_hash = digest({
        "schemaVersion": 1,
        "policyVersion": "actual-comprehensive-selection-v1",
        "sourceHash": source_pins["actual_comprehensive_selection.py"],
        "paritySourceHash": source_pins["actual_comprehensive_selection_parity.py"],
    })
    outcome_contract_hash = digest({
        "schemaVersion": 1,
        "policyVersion": "actual-comprehensive-outcome-accounting-v1",
        "sourceHash": source_pins["actual_comprehensive_outcome_accounting.py"],
        "executionAccountingSourceHash": source_pins["execution_accounting.py"],
        "costSourceHash": source_pins["backtest.py"],
        "runnerSourceHash": source_pins["strategy_backtest.py"],
    })
    pit_contract_hash = digest({
        "requirementsHash": PIT_REQUIREMENTS_HASH,
        "fundamentalsSourceHash": source_pins["point_in_time_fundamentals.py"],
        "certifierSourceHash": source_pins["authoritative_pit_coverage_certification.py"],
        "populationProducerSourceHash": source_pins["official_full_market_population.py"],
        "populationContractSourceHash": source_pins["market_population_contract.py"],
        "sourceAdmissionSourceHash": source_pins["official_population_source_admission.py"],
        "artifactReceiptSourceHash": source_pins["official_population_artifact_receipt.py"],
    })
    forward_ledger_contract_hash = digest({
        "observationSourceHash": source_pins["forward_population_observation.py"],
        "storeSourceHash": source_pins["forward_population_ledger_store.py"],
        "authorityClass": "forward_metadata_only_not_pit_certified",
    })
    adopted_policy_intent = {
        "primaryHorizonTradingDays": 20,
        "entryIntent": "next_official_trading_session_close",
        "exitIntent": "twenty_official_trading_interval_measurement",
        "suspensionIntent": "first_later_legal_execution_or_verified_terminal_value",
        "earlyExitRule": "none_in_v1",
        "strategySleeveMaxPct": 45,
        "maxSinglePositionPctOfTotalAssets": 15,
        "maxSingleSectorPctOfTotalAssets": 30,
        "minimumCashPctOfTotalAssets": 10,
        "absoluteDailyMddLimit": 0.15,
        "benchmarkMddAllowance": 0.05,
        "secondaryHorizons": [5, 60],
        "postValidationMode": "shadow_only",
    }
    decision_receipt_commitment = {
        "schemaVersion": 1,
        "receiptEvidenceClass": "private_codex_user_decision_receipt",
        "commitmentPolicy": "sha256_canonical_private_receipt_v1",
        "opaqueReceiptCommitment": "c169c48ee703c5189a14541c2c113ce202310b3e1d72687e666b4f6249578de3",
        "receivedAt": "2026-08-13T20:46:14+08:00",
        "privateLookupDataPublished": False,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "baseReceipt": {
            "repository": "clsppy0119-hash/weekly-investment-agent",
            "baseMainSha": BASE_MAIN_SHA,
            "basePrNumber": BASE_PR_NUMBER,
            "baseMergedAt": BASE_MERGED_AT,
        },
        "policyDecision": {
            "decisionClass": "explicit_user_adoption_reference_captured",
            "adoptedPolicyPresetId": "conservative-20d-15mdd-45-15-30-10-shadow-v1",
            "adoptedPolicyPresetHash": digest(adopted_policy_intent),
            "adoptedPolicyIntent": adopted_policy_intent,
            "decisionReceiptCommitment": decision_receipt_commitment,
            "decisionReceiptCommitmentHash": digest(decision_receipt_commitment),
            "decisionReceiptVerificationStatus": "pending_trusted_seal_verifier",
            "newQualifiedConfirmatoryOutcomesOpenedBeforeDecision": False,
            "legacyExploratoryOutcomesKnown": True,
        },
        "identity": {
            "strategyIdentity": "production-comprehensive-v1",
            "strategySpecHash": STRATEGY_SPEC_HASH,
            "selectionContractHash": selection_contract_hash,
            "outcomeAccountingContractHash": outcome_contract_hash,
            "pitRequirementsHash": PIT_REQUIREMENTS_HASH,
            "pitContractHash": pit_contract_hash,
            "forwardLedgerContractHash": forward_ledger_contract_hash,
            "sourcePinSetHash": digest(source_pins),
            "sourcePins": source_pins,
            "confirmatoryEvaluator": {
                "status": "unregistered",
                "requiredModule": "actual_comprehensive_confirmatory_evaluator.py",
                "requiredSchemaVersion": 1,
                "requiredPolicyVersion": "actual-comprehensive-confirmatory-evaluator-v1",
                "registrationRule": "git_main_receipt_binds_source_hash_and_preregistration_hash_before_anchor_session",
                "outcomeAccessBeforeRegistrationAllowed": False,
            },
        },
        "primaryExecution": {
            "primaryHorizonTradingDays": 20,
            "holdingIntervalTradingDays": 20,
            "signalConvention": "official_session_close_decision",
            "entryConvention": "next_official_trading_session_close",
            "exitConvention": "twenty_official_trading_intervals_after_entry_close",
            "rebalanceConvention": "strict_non_overlapping_cohorts",
            "cohortOffsetContract": {
                "signalSessionOffset": 0,
                "entrySessionOffset": 1,
                "exitSessionOffset": 21,
                "nextSignalSessionOffset": 20,
                "nextEntrySessionOffset": 21,
            },
            "nextSignalConvention": "prior_nominal_exit_immediately_preceding_official_session_close",
            "nextEntryConvention": "same_close_as_prior_nominal_exit",
            "capitalExposureDoesNotOverlapBeyondSharedBoundaryClose": True,
            "earlyExitRule": "none_in_v1",
            "nominalExitUnavailable": "verified_terminal_value_at_nominal_exit_or_entire_cohort_invalid",
            "laterTradePriceSubstitutionAllowed": False,
            "stalePriceFallbackAllowed": False,
            "secondaryHorizons": [5, 60],
            "secondaryHorizonUse": "diagnostic_only_excluded_from_primary_verdict",
        },
        "allocation": {
            "confirmatoryNotional": "normalized_strategy_sleeve_100_pct",
            "targetSlots": 3,
            "withinSleeveSlotWeight": "one_third_fixed",
            "qualityRejectedOrMissingEntry": "cash_no_backfill_no_reallocation",
            "leverageAllowed": False,
            "shortingAllowed": False,
        },
        "futureLivePolicyIntent": {
            "status": "adopted_intent_unregistered_for_validation_or_live_use",
            "strategySleeveMaxPct": 45,
            "maxSinglePositionPctOfTotalAssets": 15,
            "maxSingleSectorPctOfTotalAssets": 30,
            "minimumCashPctOfTotalAssets": 10,
            "existingHoldingsMustBeCombinedForLimits": True,
            "suspensionIntent": "first_later_legal_execution_or_verified_terminal_value",
            "doesNotAffectConfirmatoryReturnMddOrVerdict": True,
            "requiredFutureGate": "position_sector_holdings_and_delayed_exit_accounting_contract",
        },
        "risk": {
            "evaluationNotional": "normalized_strategy_sleeve_100_pct",
            "mddBasis": "daily_mark_to_market_including_costs_pre_entry_equity_1",
            "mddDomain": "finite_binary64_inclusive_range_minus_1_to_0",
            "absoluteDailyMddLimit": 0.15,
            "benchmarkMddAllowance": 0.05,
            "benchmark": "official_0050_total_return_exact_bounds",
            "absolutePassFormula": "selection_mdd >= -0.15",
            "relativePassFormula": "selection_mdd >= benchmark_0050_mdd - 0.05",
            "equalityPasses": True,
            "comparisonTolerance": 0.0,
            "missingNonfiniteOrOutOfDomain": "fail_closed",
            "requiredScopes": [
                "validation_block", "each_rolling_test_block",
                "untouched_holdout_block",
                "aggregate_rolling_tests_with_embargo_cash_continuous_daily_curve",
                "aggregate_rolling_plus_holdout_with_embargo_cash_continuous_daily_curve",
            ],
            "continuousCurveRule": "chronological_blocks_joined_without_equity_or_peak_reset_embargo_sessions_are_flat_cash_and_boundary_costs_remain",
            "continuousCurveInitialEquity": 1.0,
            "passRule": "both_formulas_must_pass_every_required_scope",
        },
        "accounting": {
            "buyFee": 0.001425,
            "sellFee": 0.001425,
            "stockSellTax": 0.003,
            "etfSellTax": 0.001,
            "oneWaySlippageBps": 10,
            "cashSlotsChargedCosts": False,
            "unfilledEntry": "cash",
            "unresolvedExitOrMark": "invalidate_entire_cohort",
            "corporateActions": "verified_total_return_only",
            "benchmarkCost": "single_round_trip_per_split",
        },
        "comparators": {
            "required": [
                "official_0050_total_return_exact_bounds",
                "signal_date_full_pit_eligible_pool_equal_weight",
            ],
            "passRule": "intersection_union_both_must_pass",
            "sameCalendarCostsAndMissingness": True,
            "survivorOrOutcomeFilteredPoolAllowed": False,
        },
        "pit": {
            "requiredCoveragePct": 100,
            "conflictCount": 0,
            "uniqueVersionRequired": True,
            "availableAtRule": "available_at_must_not_exceed_decision_as_of",
            "fullMarketScope": "TWSE_TPEx_emerging_and_later_delisted_while_effective",
            "firstSeenHistoricalBackfillAllowed": False,
        },
        "confirmatoryEvaluation": {
            "trainMinimumTradingSessions": 504,
            "validationTradingSessions": 252,
            "rollingTestTradingSessions": 252,
            "rollingStepTradingSessionsIncludingEmbargo": 272,
            "purgeTradingSessions": 20,
            "minimumDistinctRollingWindows": 5,
            "minimumScheduledOosCohorts": 60,
            "minimumActiveInvestedOosCohorts": 20,
            "untouchedHoldoutTradingSessions": 252,
            "minimumUntouchedHoldoutCohorts": 12,
            "expectedScheduledCohortsPer252SessionBlock": 12,
            "allCashScheduledCohortsCountInAggregate": True,
            "allCashScheduledCohortsCountAsActive": False,
            "everyWindowMustPassBothComparatorsAndRisk": True,
            "calendarAnchor": {
                "calendar": "official_taiwan_equity_trading_sessions",
                "signalCutoffTimezone": "Asia/Taipei",
                "anchorRule": "first_official_session_whose_signal_cutoff_is_strictly_after_trusted_seal_and_all_required_source_contract_registration_receipts",
                "selectionUsesPerformanceOrCoverageOutcome": False,
                "anchorMovesAfterCoverageGap": False,
                "insufficientOrGapPolicy": "wait_or_fail_never_shift_anchor",
            },
            "windowConstruction": {
                "algorithmVersion": "prospective-sequential-nonoverlap-v1",
                "blockOrder": [
                    "train_504", "embargo_20", "validation_252", "embargo_20",
                    "rolling_test_1_252", "embargo_20", "rolling_test_2_252", "embargo_20",
                    "rolling_test_3_252", "embargo_20", "rolling_test_4_252", "embargo_20",
                    "rolling_test_5_252", "embargo_20", "untouched_holdout_252",
                ],
                "allBlocksUseAllConsecutiveSessions": True,
                "skipReorderOrRegimeSelectionAllowed": False,
                "trainMode": "frozen_strategy_plumbing_only_no_policy_or_model_tuning",
                "cohortAssignment": "signal_entry_all_marks_and_nominal_exit_must_be_inside_one_evaluation_block",
                "cohortSchedule": "signal_offsets_0_20_40_et_seq_entry_offset_plus_1_exit_offset_plus_21_next_entry_equals_prior_exit_close",
                "allRollingTestCohortIdsGloballyUnique": True,
                "rollingAndHoldoutOverlapAllowed": False,
                "partialTailPolicy": "wait_until_complete_never_move_anchor",
            },
            "returnFreeInventory": {
                "schemaVersion": 1,
                "policyVersion": "actual-comprehensive-return-free-inventory-v1",
                "mustPrecedeAnyQualifiedOutcomeAccess": True,
                "binds": [
                    "preregistration_hash", "anchor_session", "ordered_session_ids",
                    "block_bounds", "scheduled_cohort_ids", "source_registration_receipts",
                    "pit_population_requirement_hashes",
                ],
                "forbiddenContent": "prices_returns_mdd_scores_ranks_winner_or_outcome_status",
                "inventoryGapOrMutation": "invalidate_without_reanchoring",
            },
            "bootstrap": {
                "method": "paired_moving_block_percentile",
                "resamples": 10000,
                "blockLengthCohorts": 3,
                "seed": 20260813,
                "confidenceLevel": 0.95,
                "input": "all_unique_accounting_complete_scheduled_cohorts_from_all_5_rolling_test_blocks_in_time_order_including_all_cash",
                "pairedValue": "selection_net_return_minus_same_cohort_comparator_net_return",
                "statistic": "math_fsum_paired_excess_divided_by_cohort_count",
                "blocks": "all_non_circular_consecutive_length_3_blocks",
                "draws": "ceil_n_div_3_block_starts_with_replacement_then_concatenate_and_truncate_to_n",
                "indexGenerator": "sha256_of_uint64be_seed_resample_index_draw_index_modulo_valid_block_start_count",
                "quantile": "sort_10000_statistics_take_zero_based_index_249_for_0.025_nearest_rank",
                "numericRule": "finite_ieee754_binary64_unrounded",
                "passRule": "lower_bound_strictly_greater_than_zero_for_both_comparators",
            },
            "windowVerdict": {
                "cohortCompoundedReturn": "chronological_iterative_binary64_product_of_one_plus_net_return_minus_one_including_all_cash_scheduled_cohorts",
                "validation": "selection_compounded_net_return_strictly_greater_than_each_comparator_compounded_net_return_and_risk_pass",
                "eachRollingTest": "selection_compounded_net_return_strictly_greater_than_each_comparator_compounded_net_return_and_risk_pass",
                "aggregateRollingTests": "selection_compounded_net_return_strictly_greater_than_each_comparator_compounded_net_return_both_bootstrap_lower_bounds_strictly_positive_and_continuous_risk_pass",
                "untouchedHoldout": "selection_compounded_net_return_strictly_greater_than_each_comparator_compounded_net_return_and_risk_pass",
                "rollingPlusHoldout": "continuous_curve_risk_pass_required_without_equity_or_peak_reset",
                "returnDomain": "finite_binary64_each_cohort_net_return_greater_than_or_equal_to_minus_1",
                "oneFailedOrIncompleteScope": "entire_confirmatory_verdict_fail_closed",
            },
            "holdoutAccess": {
                "accessPolicyVersion": "single-use-untouched-holdout-v1",
                "maximumQualifiedAccessCount": 1,
                "mustFollowAllRollingTests": True,
                "mustBeDisjointFromAllPriorAndLegacyOutcomeDates": True,
                "secondAccessOrPolicyMutation": "permanently_invalidate_and_require_new_future_holdout",
                "holdoutExcludedFromRollingBootstrap": True,
            },
            "secondaryHorizonMultiplicity": "not_tested_not_displayed_before_primary_verdict",
            "testFeedbackIntoPolicyAllowed": False,
        },
        "legacyExposureBoundary": {
            "existingBacktestsAndCaches": "exploratory_only",
            "confirmatoryReuseAllowed": False,
            "allowedUses": ["training", "diagnostic"],
            "preSealBlindHoldoutRegistered": False,
            "newConfirmatoryEvidenceRule": "strictly_post_seal_prospective_signal_decisions_only",
            "historicalArchiveAcquiredAfterSealUse": "training_diagnostic_only_not_confirmatory_v1",
        },
        "adviceBoundary": {
            "modeAfterSuccessfulValidation": "shadow_only",
            "formalResearchAdviceRequiresSeparateUserConfirmation": True,
            "automaticTradingAllowed": False,
        },
        "seal": {
            "status": "awaiting_node57_main_merge_receipt",
            "sealedAt": None,
            "sealReceipt": None,
            "requiredReceiptBindings": [
                "repository", "node57_main_merge_sha", "server_merge_time",
                "workflow_file_hash", "preregistration_hash", "decision_receipt_commitment_hash",
            ],
            "effectiveFromRule": "strictly_after_verified_node57_main_merge_time_and_decision_receipt_verification",
            "qualifiedOutcomeAccessBeforeSealAllowed": False,
            "policyMutationRule": "new_version_new_identity_and_new_untouched_holdout",
        },
    }


_INITIAL_MANIFEST = _build_preregistration_manifest()
_EXPECTED_MANIFEST_CANONICAL = canonical(_INITIAL_MANIFEST)
_EXPECTED_PREREGISTRATION_HASH = digest(_INITIAL_MANIFEST)
_EXPECTED_ARTIFACT_CANONICAL = canonical({
    "schemaVersion": SCHEMA_VERSION,
    "policyVersion": POLICY_VERSION,
    "manifest": _INITIAL_MANIFEST,
    "preregistrationHash": _EXPECTED_PREREGISTRATION_HASH,
})


def _make_public_api(
    expected_manifest: str,
    expected_artifact: str,
    expected_hash: str,
):
    """Capture every trust input in closures, then discard this factory."""
    loads = json.loads
    dumps = json.dumps
    sha256 = hashlib.sha256
    schema_version = SCHEMA_VERSION
    policy_version = POLICY_VERSION
    max_nodes = MAX_NODES
    max_depth = MAX_DEPTH
    max_string = MAX_STRING
    max_bytes = MAX_CANONICAL_BYTES
    forbidden_keys = FORBIDDEN_KEYS
    forbidden_prefixes = FORBIDDEN_KEY_PREFIXES
    forbidden_suffixes = FORBIDDEN_KEY_SUFFIXES
    forbidden_fragments = FORBIDDEN_KEY_FRAGMENTS
    fixed_blockers = FIXED_BLOCKERS

    def normalized_key(value: str) -> str:
        return "".join(character for character in value.lower() if character.isalnum())

    def bounded(value: Any, depth: int = 0, budget: list[int] | None = None) -> bool:
        if budget is None:
            budget = [max_nodes]
        budget[0] -= 1
        if budget[0] < 0 or depth > max_depth:
            return False
        if value is None or type(value) in (str, int, float, bool):
            if type(value) is str:
                return len(value) <= max_string and not any(
                    ord(character) < 32 for character in value
                )
            if type(value) is float:
                return math.isfinite(value)
            if type(value) is int:
                return abs(value) <= 10**18
            return True
        if type(value) is list:
            return len(value) <= max_nodes and all(
                bounded(item, depth + 1, budget) for item in value
            )
        if type(value) is dict:
            return len(value) <= max_nodes and all(
                type(key) is str and len(key) <= 128
                and bounded(item, depth + 1, budget)
                for key, item in value.items()
            )
        return False

    def contains_forbidden(value: Any) -> bool:
        if type(value) is dict:
            for key, item in value.items():
                normalized = normalized_key(key)
                if (
                    normalized in forbidden_keys
                    or normalized.startswith(forbidden_prefixes)
                    or normalized.endswith(forbidden_suffixes)
                    or any(fragment in normalized for fragment in forbidden_fragments)
                    or contains_forbidden(item)
                ):
                    return True
        elif type(value) is list:
            return any(contains_forbidden(item) for item in value)
        elif type(value) is str:
            lowered = value.lower()
            return any(marker in lowered for marker in (
                "://", "bearer ", "authorization:", "token=", "password=",
                "cookie=", "set-cookie:", "-----begin ",
            ))
        return False

    def report(valid: bool, blockers: list[str] | None = None) -> dict[str, Any]:
        result = {
            "schemaVersion": schema_version,
            "policyVersion": policy_version,
            "mode": "research_only",
            "contractStructurallyValid": valid,
            "policyValuesFrozen": valid,
            "policyDecisionCommitmentRecorded": valid,
            "policyDecisionRecorded": False,
            "newQualifiedOutcomeBlind": True,
            "legacyExploratoryOutcomesKnown": True,
            "preregistrationReadyForMainSeal": valid,
            "preregistrationHash": expected_hash if valid else "",
            "sealed": False,
            "sealedAt": None,
            "confirmatoryEvaluatorRegistered": False,
            "returnFreeInventoryRegistered": False,
            "confirmatoryDataAccessEligible": False,
            "pitCoverageCertified": False,
            "performanceEvaluated": False,
            "riskGatePassed": False,
            "strategyValidated": False,
            "promotionEligible": False,
            "adviceEnabled": False,
            "formalGateAttached": False,
            "automaticTradingEnabled": False,
            "livePositionSizingPolicyRegistered": False,
            "liveDelayedExitPolicyRegistered": False,
            "blockers": list(dict.fromkeys([*(blockers or []), *fixed_blockers])),
        }
        serialized = dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        result["reportDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
        return result

    def manifest() -> dict[str, Any]:
        """Return a fresh copy of the import-time frozen canonical policy."""
        return loads(expected_manifest)

    def artifact() -> dict[str, Any]:
        return loads(expected_artifact)

    def evaluator(value: Any) -> dict[str, Any]:
        """Validate only the exact performance-blind committed policy artifact."""
        try:
            if not bounded(value) or type(value) is not dict:
                return report(False, ["input_not_bounded_json"])
            candidate = dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
            if len(candidate.encode("utf-8")) > max_bytes:
                return report(False, ["input_too_large"])
            if candidate == expected_artifact:
                return report(True)
            if contains_forbidden(value):
                return report(False, ["performance_or_sensitive_input_prohibited"])
            return report(False, ["preregistration_contract_or_hash_mismatch"])
        except Exception:
            return report(False, ["input_fail_closed"])

    def runner(value: Any = None, *, enabled: bool = False) -> dict[str, Any]:
        """Default-off boundary; disabled mode never inspects ``value``."""
        if not enabled:
            return {
                "schemaVersion": schema_version,
                "policyVersion": policy_version,
                "mode": "disabled",
                "sealed": False,
                "confirmatoryDataAccessEligible": False,
                "strategyValidated": False,
                "adviceEnabled": False,
            }
        return evaluator(value)

    manifest.__name__ = "preregistration_manifest"
    artifact.__name__ = "preregistration_artifact"
    evaluator.__name__ = "evaluate"
    runner.__name__ = "run"
    return manifest, artifact, evaluator, runner


(
    preregistration_manifest,
    preregistration_artifact,
    evaluate,
    run,
) = _make_public_api(
    _EXPECTED_MANIFEST_CANONICAL,
    _EXPECTED_ARTIFACT_CANONICAL,
    _EXPECTED_PREREGISTRATION_HASH,
)

# Do not expose a factory or mutable expected bytes that can mint alternate
# validators.  The function closures above retain the only accepted values.
del _INITIAL_MANIFEST
del _EXPECTED_MANIFEST_CANONICAL
del _EXPECTED_ARTIFACT_CANONICAL
del _EXPECTED_PREREGISTRATION_HASH
del _SOURCE_PIN_ITEMS
del _build_preregistration_manifest
del _make_public_api
