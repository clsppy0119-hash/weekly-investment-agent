"""Performance-blind contract tests for Node57 preregistration."""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import actual_comprehensive_validation_preregistration as prereg
import production_strategy_validation_preflight as preflight
import strategy_backtest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "actual_comprehensive_validation_preregistration_v1.json"


def committed():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_default_off_never_inspects_payload():
    class Explodes:
        def __getattribute__(self, name):
            raise RuntimeError("disabled path inspected payload")

    result = prereg.run(Explodes(), enabled=False)
    assert result["mode"] == "disabled"
    assert result["sealed"] is False
    assert result["confirmatoryDataAccessEligible"] is False
    assert result["strategyValidated"] is False
    assert result["adviceEnabled"] is False


def test_committed_policy_is_exact_deterministic_and_ready_only_for_future_seal():
    artifact = committed()
    assert artifact == prereg.preregistration_artifact()
    assert artifact["preregistrationHash"] == prereg.digest(artifact["manifest"])
    first = prereg.run(artifact, enabled=True)
    second = prereg.run(copy.deepcopy(artifact), enabled=True)
    assert first == second
    assert first["contractStructurallyValid"] is True
    assert first["policyValuesFrozen"] is True
    assert first["policyDecisionCommitmentRecorded"] is True
    assert first["policyDecisionRecorded"] is False
    assert first["newQualifiedOutcomeBlind"] is True
    assert first["legacyExploratoryOutcomesKnown"] is True
    assert first["preregistrationReadyForMainSeal"] is True
    assert first["sealed"] is False
    assert first["sealedAt"] is None
    for key in (
        "confirmatoryDataAccessEligible", "pitCoverageCertified",
        "performanceEvaluated", "riskGatePassed", "strategyValidated",
        "promotionEligible", "adviceEnabled", "formalGateAttached",
        "automaticTradingEnabled",
    ):
        assert first[key] is False
    assert "node57_main_merge_seal_receipt_required" in first["blockers"]


def test_explicitly_adopted_execution_allocation_and_risk_are_frozen():
    manifest = committed()["manifest"]
    execution = manifest["primaryExecution"]
    assert execution["primaryHorizonTradingDays"] == 20
    assert execution["holdingIntervalTradingDays"] == 20
    assert execution["exitConvention"] == (
        "twenty_official_trading_intervals_after_entry_close"
    )
    assert execution["cohortOffsetContract"] == {
        "signalSessionOffset": 0,
        "entrySessionOffset": 1,
        "exitSessionOffset": 21,
        "nextSignalSessionOffset": 20,
        "nextEntrySessionOffset": 21,
    }
    assert execution["capitalExposureDoesNotOverlapBeyondSharedBoundaryClose"] is True
    assert execution["laterTradePriceSubstitutionAllowed"] is False
    assert execution["nominalExitUnavailable"] == (
        "verified_terminal_value_at_nominal_exit_or_entire_cohort_invalid"
    )
    assert execution["secondaryHorizons"] == [5, 60]
    allocation = manifest["allocation"]
    assert allocation["confirmatoryNotional"] == "normalized_strategy_sleeve_100_pct"
    assert allocation["targetSlots"] == 3
    live = manifest["futureLivePolicyIntent"]
    assert live["strategySleeveMaxPct"] == 45
    assert live["maxSinglePositionPctOfTotalAssets"] == 15
    assert live["maxSingleSectorPctOfTotalAssets"] == 30
    assert live["minimumCashPctOfTotalAssets"] == 10
    assert live["doesNotAffectConfirmatoryReturnMddOrVerdict"] is True
    assert live["status"] == "adopted_intent_unregistered_for_validation_or_live_use"
    risk = manifest["risk"]
    assert risk["absoluteDailyMddLimit"] == 0.15
    assert risk["benchmarkMddAllowance"] == 0.05
    assert risk["absolutePassFormula"] == "selection_mdd >= -0.15"
    assert risk["relativePassFormula"] == "selection_mdd >= benchmark_0050_mdd - 0.05"
    assert risk["equalityPasses"] is True
    assert risk["comparisonTolerance"] == 0.0
    assert risk["continuousCurveRule"] == (
        "chronological_blocks_joined_without_equity_or_peak_reset_"
        "embargo_sessions_are_flat_cash_and_boundary_costs_remain"
    )
    assert "aggregate_rolling_plus_holdout" in risk["requiredScopes"][-1]
    assert manifest["adviceBoundary"]["modeAfterSuccessfulValidation"] == "shadow_only"


def test_confirmatory_protocol_is_unique_nonoverlapping_and_conservative():
    policy = committed()["manifest"]["confirmatoryEvaluation"]
    assert policy["minimumDistinctRollingWindows"] == 5
    assert policy["rollingTestTradingSessions"] == 252
    assert policy["rollingStepTradingSessionsIncludingEmbargo"] == 272
    assert policy["purgeTradingSessions"] == 20
    assert policy["minimumScheduledOosCohorts"] == 60
    assert policy["minimumActiveInvestedOosCohorts"] == 20
    assert policy["untouchedHoldoutTradingSessions"] == 252
    assert policy["minimumUntouchedHoldoutCohorts"] == 12
    assert policy["allCashScheduledCohortsCountInAggregate"] is True
    assert policy["allCashScheduledCohortsCountAsActive"] is False
    assert policy["everyWindowMustPassBothComparatorsAndRisk"] is True
    bootstrap = policy["bootstrap"]
    assert bootstrap["method"] == "paired_moving_block_percentile"
    assert bootstrap["resamples"] == 10000
    assert bootstrap["blockLengthCohorts"] == 3
    assert bootstrap["seed"] == 20260813
    assert bootstrap["quantile"] == (
        "sort_10000_statistics_take_zero_based_index_249_for_0.025_nearest_rank"
    )
    assert bootstrap["blocks"] == "all_non_circular_consecutive_length_3_blocks"
    assert "including_all_cash" in bootstrap["input"]
    assert policy["windowConstruction"]["skipReorderOrRegimeSelectionAllowed"] is False
    assert policy["holdoutAccess"]["maximumQualifiedAccessCount"] == 1
    assert policy["windowConstruction"]["rollingAndHoldoutOverlapAllowed"] is False


def test_user_decision_reference_is_auditable_but_not_self_verified():
    policy = committed()["manifest"]["policyDecision"]
    assert policy["adoptedPolicyPresetId"] == (
        "conservative-20d-15mdd-45-15-30-10-shadow-v1"
    )
    receipt = policy["decisionReceiptCommitment"]
    assert receipt == {
        "schemaVersion": 1,
        "receiptEvidenceClass": "private_codex_user_decision_receipt",
        "commitmentPolicy": "sha256_canonical_private_receipt_v1",
        "opaqueReceiptCommitment": (
            "c169c48ee703c5189a14541c2c113ce202310b3e1d72687e666b4f6249578de3"
        ),
        "receivedAt": "2026-08-13T20:46:14+08:00",
        "privateLookupDataPublished": False,
    }
    assert policy["decisionReceiptCommitmentHash"] == prereg.digest(receipt)
    assert policy["decisionReceiptVerificationStatus"] == (
        "pending_trusted_seal_verifier"
    )
    result = prereg.evaluate(committed())
    assert result["policyDecisionCommitmentRecorded"] is True
    assert result["policyDecisionRecorded"] is False
    assert "trusted_user_policy_decision_receipt_verification_required" in result["blockers"]


def test_only_strictly_post_seal_prospective_decisions_can_be_confirmatory():
    manifest = committed()["manifest"]
    legacy = manifest["legacyExposureBoundary"]
    assert legacy["preSealBlindHoldoutRegistered"] is False
    assert legacy["newConfirmatoryEvidenceRule"] == (
        "strictly_post_seal_prospective_signal_decisions_only"
    )
    assert legacy["historicalArchiveAcquiredAfterSealUse"] == (
        "training_diagnostic_only_not_confirmatory_v1"
    )
    anchor = manifest["confirmatoryEvaluation"]["calendarAnchor"]
    assert anchor["selectionUsesPerformanceOrCoverageOutcome"] is False
    assert anchor["anchorMovesAfterCoverageGap"] is False
    blocks = manifest["confirmatoryEvaluation"]["windowConstruction"]["blockOrder"]
    assert blocks[0] == "train_504"
    assert blocks[-1] == "untouched_holdout_252"
    assert blocks.count("embargo_20") == 7


def test_import_time_frozen_contract_ignores_runtime_trust_root_mutation():
    artifact = committed()
    original = {
        "SOURCE_PINS": prereg.SOURCE_PINS,
        "STRATEGY_SPEC_HASH": prereg.STRATEGY_SPEC_HASH,
        "PIT_REQUIREMENTS_HASH": prereg.PIT_REQUIREMENTS_HASH,
        "digest": prereg.digest,
        "preregistration_manifest": prereg.preregistration_manifest,
        "preregistration_artifact": prereg.preregistration_artifact,
    }
    with unittest.TestCase().assertRaises(TypeError):
        prereg.SOURCE_PINS["backtest.py"] = "0" * 64
    try:
        prereg.SOURCE_PINS = {"backtest.py": "0" * 64}
        prereg.STRATEGY_SPEC_HASH = "0" * 64
        prereg.PIT_REQUIREMENTS_HASH = "f" * 64
        prereg.digest = lambda value: "0" * 64
        prereg.preregistration_manifest = lambda: {"forged": True}
        prereg.preregistration_artifact = lambda: {"forged": True}
        result = prereg.evaluate(copy.deepcopy(artifact))
        assert result["contractStructurallyValid"] is True
        assert result["preregistrationHash"] == artifact["preregistrationHash"]
        forged = copy.deepcopy(artifact)
        forged["manifest"]["identity"]["sourcePins"]["backtest.py"] = "0" * 64
        assert prereg.evaluate(forged)["contractStructurallyValid"] is False
    finally:
        for name, value in original.items():
            setattr(prereg, name, value)


def test_public_api_has_no_dependency_injection_or_self_seal_parameters():
    assert not inspect.signature(prereg.preregistration_manifest).parameters
    assert not inspect.signature(prereg.preregistration_artifact).parameters
    assert tuple(inspect.signature(prereg.evaluate).parameters) == ("value",)
    assert tuple(inspect.signature(prereg.run).parameters) == ("value", "enabled")
    for call in (
        lambda: prereg.preregistration_manifest('{"sealed":true}'),
        lambda: prereg.preregistration_artifact('{"sealed":true}'),
        lambda: prereg.evaluate(
            {"forged": "anything"}, _expected='{"forged":"anything"}'
        ),
        lambda: prereg.run(
            {}, enabled=True,
            _evaluate=lambda _: {"sealed": True, "adviceEnabled": True},
        ),
    ):
        with unittest.TestCase().assertRaises(TypeError):
            call()
    assert not hasattr(prereg, "_make_public_api")
    assert not hasattr(prereg, "_EXPECTED_ARTIFACT_CANONICAL")


def test_canonical_comparison_is_json_type_strict():
    mutations = (
        ("primaryExecution", "primaryHorizonTradingDays", 20.0),
        ("allocation", "leverageAllowed", 0),
        ("allocation", "shortingAllowed", 0),
        ("risk", "comparisonTolerance", -0.0),
        ("confirmatoryEvaluation", "minimumDistinctRollingWindows", 5.0),
    )
    for section, key, replacement in mutations:
        changed = committed()
        changed["manifest"][section][key] = replacement
        # Deliberately preserve the original outer hash: Python loose equality
        # must not make these distinct JSON encodings acceptable.
        result = prereg.evaluate(changed)
        assert result["contractStructurallyValid"] is False, (section, key)


def test_public_artifact_contains_no_private_codex_identifiers_or_message_hashes():
    serialized = ARTIFACT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "threadid", "turnid", "itemid", "usermessageitemid", "rawmessage",
        "messagewithtrailinglf", "normalizedmessage", "019f",
    ):
        assert forbidden not in serialized


def test_20_interval_schedule_matches_the_pinned_node55_runner():
    dates = [f"D{index:03d}" for index in range(90)]
    history = [{} for _ in dates]
    captured = []
    selection = {
        "fullPool": [], "poolTuples": [], "previewTuples": [],
        "selectedTuples": [], "qualityPassedCodes": [],
        "cutoffTieDependent": False,
    }

    def measure(payload, enabled=False):
        captured.append(list(payload["dates"]))
        return {
            "accountingComplete": False,
            "selectionAccounting": {},
            "blockers": ["fixture_stop"],
        }

    failed = {
        "complete": False, "return": None, "mdd": None,
        "executionAccounting": {}, "eligiblePoolAccounting": {},
        "eligiblePoolReturn": None, "eligiblePoolMdd": None,
        "benchmarkReturn": None, "benchmarkMdd": None,
        "benchmarkCostedRoundTrips": 0, "benchmarkCostModel": {},
        "benchmarkScheduledReturns": [], "blockers": ["fixture_stop"],
    }
    with patch.object(strategy_backtest, "factor_quotes", return_value={}), \
            patch.object(strategy_backtest, "fundamental_records", return_value={}), \
            patch.object(strategy_backtest, "select_signal_candidates", return_value=selection), \
            patch.object(strategy_backtest, "measure_cohort", side_effect=measure), \
            patch.object(strategy_backtest, "aggregate_measurements", return_value=failed):
        strategy_backtest.run_range(
            history, dates, 0, len(dates), "comprehensive", 3, 20, 0,
        )

    assert captured[0][0] == "D020"       # signal
    assert captured[0][1] == "D021"       # entry
    assert captured[0][-1] == "D041"      # exit: 20 intervals after entry
    assert captured[1][0] == "D040"       # next signal
    assert captured[1][1] == "D041"       # next entry == prior exit close
    assert len(captured[0]) == 22


def test_compounded_comparator_and_continuous_mdd_rules_cannot_be_reinterpreted():
    policy = committed()["manifest"]["confirmatoryEvaluation"]
    verdict = policy["windowVerdict"]
    assert "compounded_net_return_strictly_greater" in verdict["validation"]
    assert "compounded_net_return_strictly_greater" in verdict["eachRollingTest"]
    assert "compounded_net_return_strictly_greater" in verdict["aggregateRollingTests"]
    assert "compounded_net_return_strictly_greater" in verdict["untouchedHoldout"]
    # Arithmetic mean excess is positive here, but compounding correctly says
    # the selection (0%) lost to the comparator (+20%).
    selection = (1 + 1.0) * (1 - 0.5) - 1
    comparator = (1 + 0.5) * (1 - 0.2) - 1
    arithmetic_mean_excess = ((1.0 - 0.5) + (-0.5 + 0.2)) / 2
    assert arithmetic_mean_excess > 0
    assert selection <= comparator
    risk = committed()["manifest"]["risk"]
    assert risk["continuousCurveInitialEquity"] == 1.0
    assert "without_equity_or_peak_reset" in risk["continuousCurveRule"]
    assert "embargo_sessions_are_flat_cash" in risk["continuousCurveRule"]


def test_any_policy_or_hash_mutation_fails_even_when_caller_recomputes_outer_hash():
    paths = (
        ("primaryExecution", "primaryHorizonTradingDays", 5),
        ("primaryExecution", "earlyExitRule", "stop_loss"),
        ("futureLivePolicyIntent", "strategySleeveMaxPct", 60),
        ("futureLivePolicyIntent", "maxSinglePositionPctOfTotalAssets", 20),
        ("risk", "absoluteDailyMddLimit", 0.25),
        ("risk", "benchmarkMddAllowance", 0.10),
        ("confirmatoryEvaluation", "minimumDistinctRollingWindows", 3),
        ("confirmatoryEvaluation", "minimumScheduledOosCohorts", 10),
        ("adviceBoundary", "modeAfterSuccessfulValidation", "formal"),
        ("seal", "status", "sealed"),
    )
    for section, key, replacement in paths:
        changed = committed()
        changed["manifest"][section][key] = replacement
        changed["preregistrationHash"] = prereg.digest(changed["manifest"])
        result = prereg.evaluate(changed)
        assert result["contractStructurallyValid"] is False, (section, key)
        assert result["sealed"] is False
        assert result["confirmatoryDataAccessEligible"] is False
    changed = committed()
    changed["manifest"]["confirmatoryEvaluation"]["bootstrap"]["seed"] = 1
    changed["preregistrationHash"] = prereg.digest(changed["manifest"])
    assert prereg.evaluate(changed)["contractStructurallyValid"] is False


def test_caller_cannot_self_seal_or_inject_observed_performance_or_sensitive_data():
    for key, value in (
        ("sealedAt", "2026-08-13T13:00:00Z"),
        ("sealReceipt", {"caller": True}),
        ("observedReturn", 9.9),
        ("actualMdd", -0.01),
        ("winner", "20"),
        ("apiToken", "SECRET"),
        ("access_token", "SECRET"),
        ("secret_value", "SECRET"),
        ("private_key", "SECRET"),
        ("authorization_value", "SECRET"),
        ("query_params", "SECRET"),
        ("headers_map", "SECRET"),
        ("url_value", "SECRET"),
        ("raw_payload", [{}]),
        ("rows_data", [{}]),
        ("price_rows", [{}]),
        ("threadId", "019f-secret"),
        ("turnId", "019f-secret"),
        ("userMessageItemId", "item-secret"),
        ("rawMessageSha256", "0" * 64),
    ):
        changed = committed()
        changed["manifest"][key] = value
        changed["preregistrationHash"] = prereg.digest(changed["manifest"])
        result = prereg.evaluate(changed)
        assert result["contractStructurallyValid"] is False
        assert result["sealed"] is False
        assert result["strategyValidated"] is False
        if key not in {"sealedAt", "sealReceipt"}:
            assert "performance_or_sensitive_input_prohibited" in result["blockers"]


def test_malformed_unbounded_cyclic_and_hostile_inputs_fail_closed():
    cycle = []
    cycle.append(cycle)

    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    class BadList(list):
        def __iter__(self):
            raise RuntimeError("boom")

    for value in (
        None, [], {"x": float("nan")}, {"x": float("inf")},
        {1: "bad"}, {"x": {1}}, {"x": 10**10000},
        {"x": "a" * (prereg.MAX_STRING + 1)}, cycle, BadDict(), BadList(),
    ):
        result = prereg.evaluate(value)
        assert result["contractStructurallyValid"] is False
        assert result["confirmatoryDataAccessEligible"] is False
        assert result["adviceEnabled"] is False


def test_source_strategy_and_pit_pins_match_the_exact_node56_main_tree():
    def source_hash(name):
        text = (ROOT / name).read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    assert {name: source_hash(name) for name in prereg.SOURCE_PINS} == dict(prereg.SOURCE_PINS)
    assert prereg.STRATEGY_SPEC_HASH == preflight.strategy_spec_hash()
    expected_pit_hash = preflight.digest({
        "requirements": list(preflight.REQUIRED_REQUIREMENTS),
        "requirementPolicies": preflight.REQUIREMENT_POLICIES,
    })
    assert prereg.PIT_REQUIREMENTS_HASH == expected_pit_hash
    assert set(preflight.SOURCE_PINS).issubset(prereg.SOURCE_PINS)
    assert all(prereg.SOURCE_PINS[name] == value for name, value in preflight.SOURCE_PINS.items())
    assert prereg.BASE_MAIN_SHA == "4a31db49a1f1f9e04dd5f42a8e4c6862269001f0"


def test_module_is_offline_and_disconnected_from_formal_or_data_flows():
    path = ROOT / "actual_comprehensive_validation_preregistration.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "hashlib", "json", "math", "types", "typing"}
    source = path.read_text(encoding="utf-8")
    for forbidden in (
        "open(", "Path(", "requests", "urllib", "socket", "subprocess",
        "getenv", "supabase", "backtest_data", "datetime.now", "utcnow",
    ):
        assert forbidden not in source
    for consumer in (
        "daily_report.py", "investment_advice_gate.py", "promotion_status.py",
        "strategy_tracker.py", "candidate_manifest.py",
    ):
        assert "actual_comprehensive_validation_preregistration" not in (
            ROOT / consumer
        ).read_text(encoding="utf-8")


def test_safety_workflow_covers_contract_artifact_and_tests_without_new_permissions():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "actual_comprehensive_validation_preregistration.py" in workflow
    assert "actual_comprehensive_validation_preregistration_v1.json" in workflow
    assert "tests/**" in workflow
    assert "contents: read" in workflow
    for forbidden in ("id-token: write", "attestations: write", "secrets."):
        assert forbidden not in workflow


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
