import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actual_comprehensive_outcome_accounting as accounting
from actual_comprehensive_selection import digest as selection_digest
from actual_comprehensive_selection import rank_and_assess


DATES = [
    "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17",
    "2026-08-18", "2026-08-19", "2026-08-20",
]
CODES = ("7777", "6666", "5555", "4444")
NEXT_DATES = [
    "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24",
    "2026-08-25", "2026-08-26", "2026-08-27",
]


def selector():
    quotes = {
        code: {
            "name": code, "price": 100.0, "volume": 400 - index * 100,
            "change": 1.0, "ma5": 99.0, "ma20": 98.0,
        }
        for index, code in enumerate(CODES)
    }
    fundamentals = {
        code: {
            "revenueYoY": 15.0, "eps": 10.0, "roe": 20.0, "debtRatio": 25.0,
            "pe": 15.0, "pb": 2.0, "dividendYield": 3.0,
            "financialHistoryYears": 4 if code == "7777" else 5,
        }
        for code in CODES
    }
    actions = {"queried_codes": list(CODES), "failures": {}}
    result = rank_and_assess(quotes, fundamentals, actions=actions, contract_blockers=[])
    return {
        key: value for key, value in result.items()
        if key not in {"poolTuples", "previewTuples", "selectedTuples"}
    }


def evidence(*, coverage=True, events=None, terminal=None):
    by_code = {
        code: {
            "coverageComplete": coverage,
            "quality": "verified",
            "conflictStatus": "no_conflict",
            "events": copy.deepcopy((events or {}).get(code, [])),
            "terminal": copy.deepcopy((terminal or {}).get(code)),
        }
        for code in CODES
    }
    return accounting.build_outcome_evidence(DATES[0], DATES[1], DATES[-1], by_code)


def paths():
    values = {
        "7777": [100, 100, 100, 100, 100, 100],
        "6666": [100, 102, 104, 106, 108, 110],
        "5555": [100, 98, 96, 94, 92, 90],
        "4444": [100, 101, 102, 103, 104, 105],
    }
    return {
        code: {day: value for day, value in zip(DATES[1:], series)}
        for code, series in values.items()
    }


def payload():
    return {
        "schemaVersion": 1,
        "selection": selector(),
        "dates": list(DATES),
        "pricePaths": paths(),
        "benchmarkTotalReturn": {day: 100.0 for day in DATES[1:]},
        "outcomeEvidence": evidence(),
    }


def remap_dates(value, new_dates):
    old_dates = list(value["dates"])
    mapping = dict(zip(old_dates, new_dates))
    value["dates"] = list(new_dates)
    value["pricePaths"] = {
        code: {mapping[day]: price for day, price in path.items()}
        for code, path in value["pricePaths"].items()
    }
    value["benchmarkTotalReturn"] = {
        mapping[day]: price for day, price in value["benchmarkTotalReturn"].items()
    }
    value["outcomeEvidence"] = accounting.build_outcome_evidence(
        new_dates[0], new_dates[1], new_dates[-1],
        copy.deepcopy(value["outcomeEvidence"]["byCode"]),
    )
    return value


def test_quality_failure_is_cash_and_rank_four_never_backfills():
    result = accounting.measure_cohort(payload(), enabled=True)

    assert result["accountingComplete"] is True
    assert result["selectionIdentity"]["qualityPassedSlots"] == 2
    assert result["selectionAccounting"]["targetSlots"] == 3
    assert result["selectionAccounting"]["closedSlots"] == 2
    assert result["selectionAccounting"]["noCandidateCashSlots"] == 1
    assert result["selectionAccounting"]["cashWeight"] == 1 / 3
    # +10% and -10%, each with a fixed one-third slot. Rank four's +5% is absent.
    one_up = (1.10 * (1 - accounting.STOCK_BUY_COST) * (1 - accounting.STOCK_SELL_COST) - 1) / 3
    one_down = (0.90 * (1 - accounting.STOCK_BUY_COST) * (1 - accounting.STOCK_SELL_COST) - 1) / 3
    assert math.isclose(result["selectionReturn"], one_up + one_down, abs_tol=1e-12)
    assert result["performanceEligible"] is False
    assert result["liveExecutionSpecRegistered"] is False


def test_missing_entry_stays_cash_without_reallocating_the_other_slot():
    value = payload()
    value["pricePaths"]["6666"].pop(DATES[1])
    result = accounting.measure_cohort(value, enabled=True)

    assert result["accountingComplete"] is True
    assert result["selectionAccounting"]["unfilledEntrySlots"] == 1
    assert result["selectionAccounting"]["closedSlots"] == 1
    assert result["selectionAccounting"]["cashWeight"] == 2 / 3
    expected = (0.90 * (1 - accounting.STOCK_BUY_COST) * (1 - accounting.STOCK_SELL_COST) - 1) / 3
    assert math.isclose(result["selectionReturn"], expected, abs_tol=1e-12)


def test_missing_daily_mark_or_exit_is_unresolved_not_a_stale_sale():
    for missing in (DATES[3], DATES[-1]):
        value = payload()
        value["pricePaths"]["6666"].pop(missing)
        result = accounting.measure_cohort(value, enabled=True)

        assert result["accountingComplete"] is False
        assert result["selectionReturn"] is None
        assert result["eligiblePoolReturn"] is None
        assert result["selectionAccounting"]["unresolvedExitSlots"] == 1
        assert "daily_mark_or_exit_missing" in result["blockers"]


def test_verified_terminal_value_can_settle_an_exit_but_not_a_missing_mid_mark():
    terminal = {
        "6666": {
            "date": DATES[-1], "availableAt": f"{DATES[-1]}T17:00:00+08:00",
            "value": 80.0, "quality": "verified", "conflictStatus": "no_conflict",
        }
    }
    value = payload()
    value["pricePaths"]["6666"].pop(DATES[-1])
    value["outcomeEvidence"] = evidence(terminal=terminal)
    result = accounting.measure_cohort(value, enabled=True)
    assert result["accountingComplete"] is True

    value = payload()
    value["pricePaths"]["6666"].pop(DATES[3])
    value["outcomeEvidence"] = evidence(terminal=terminal)
    result = accounting.measure_cohort(value, enabled=True)
    assert result["accountingComplete"] is False


def test_corporate_action_factor_is_included_in_total_return_path():
    event = {
        "eventId": "6666-dividend-20260818",
        "effectiveDate": "2026-08-18",
        "availableAt": "2026-08-18T09:00:00+08:00",
        "factor": 1.1,
        "quality": "verified",
        "conflictStatus": "no_conflict",
    }
    value = payload()
    without = accounting.measure_cohort(value, enabled=True)
    value["outcomeEvidence"] = evidence(events={"6666": [event]})
    with_dividend = accounting.measure_cohort(value, enabled=True)

    assert with_dividend["selectionReturn"] > without["selectionReturn"] + 0.03


def test_action_coverage_must_be_explicit_for_every_pool_constituent():
    value = payload()
    value["outcomeEvidence"] = evidence(coverage=False)
    result = accounting.measure_cohort(value, enabled=True)

    assert result["accountingComplete"] is False
    assert result["selectionReturn"] is None
    assert result["eligiblePoolAccounting"]["unresolvedExitSlots"] == 4
    assert "corporate_action_coverage_incomplete" in result["blockers"]


def test_eligible_pool_uses_the_original_fixed_denominator():
    value = payload()
    value["pricePaths"]["4444"].pop(DATES[1])
    result = accounting.measure_cohort(value, enabled=True)

    assert result["accountingComplete"] is True
    assert result["eligiblePoolAccounting"]["targetSlots"] == 4
    assert result["eligiblePoolAccounting"]["unfilledEntrySlots"] == 1
    assert result["eligiblePoolAccounting"]["cashWeight"] == 0.25


def test_empty_signal_pool_is_a_scheduled_all_cash_period_not_a_missing_sample():
    value = payload()
    selection = value["selection"]
    selection["fullPool"] = []
    selection["preview"] = []
    selection["qualityPassedCodes"] = []
    body = {
        key: item for key, item in selection.items()
        if key != "selectionDigest"
    }
    selection["selectionDigest"] = selection_digest(body)
    value["pricePaths"] = {}
    value["outcomeEvidence"] = accounting.build_outcome_evidence(
        DATES[0], DATES[1], DATES[-1], {},
    )
    result = accounting.measure_cohort(value, enabled=True)

    assert result["accountingComplete"] is True
    assert result["selectionIdentity"]["poolSize"] == 0
    assert result["selectionAccounting"]["targetSlots"] == 3
    assert result["selectionAccounting"]["noCandidateCashSlots"] == 3
    assert result["selectionReturn"] == 0.0
    assert result["eligiblePoolReturn"] == 0.0


def test_benchmark_requires_the_same_complete_exact_daily_path():
    for mutate in (
        lambda value: value["benchmarkTotalReturn"].pop(DATES[4]),
        lambda value: value["benchmarkTotalReturn"].__setitem__("2099-01-01", 100.0),
    ):
        value = payload()
        mutate(value)
        result = accounting.measure_cohort(value, enabled=True)

        assert result["accountingComplete"] is False
        assert result["benchmarkReturn"] is None
        assert any("benchmark" in blocker for blocker in result["blockers"])


def test_daily_mdd_sees_an_intrahorizon_crash_and_recovery():
    value = payload()
    value["pricePaths"]["6666"] = {
        day: price for day, price in zip(DATES[1:], [100, 100, 40, 100, 100, 100])
    }
    value["pricePaths"]["5555"] = {day: 100 for day in DATES[1:]}
    result = accounting.measure_cohort(value, enabled=True)

    assert result["selectionReturn"] < 0
    assert result["selectionMdd"] < -0.19
    assert result["mddBasis"] == "daily_mark_to_market_including_costs"
    assert result["riskGateEligible"] is False


def test_tampered_selection_digest_population_or_evidence_fails_closed():
    mutations = []
    value = payload()
    value["selection"]["selectionDigest"] = "f" * 64
    mutations.append(value)
    value = payload()
    value["pricePaths"].pop("4444")
    mutations.append(value)
    value = payload()
    value["outcomeEvidence"]["byCode"].pop("4444")
    body = {key: item for key, item in value["outcomeEvidence"].items() if key != "evidenceHash"}
    value["outcomeEvidence"]["evidenceHash"] = accounting.digest(body)
    mutations.append(value)
    for item in mutations:
        result = accounting.measure_cohort(item, enabled=True)
        assert result["accountingComplete"] is False
        assert result["performanceEligible"] is False


def test_unknown_selection_fields_sensitive_event_ids_and_extra_price_dates_fail_closed():
    mutations = []
    value = payload()
    value["selection"]["raw_payload"] = "secret"
    mutations.append(value)
    value = payload()
    value["selection"]["preview"][0]["secret_value"] = "SECRET"
    body = {key: item for key, item in value["selection"].items() if key != "selectionDigest"}
    value["selection"]["selectionDigest"] = selection_digest(body)
    mutations.append(value)
    value = payload()
    value["selection"]["poolTuples"] = [{"raw_payload": "SECRET"}]
    mutations.append(value)
    value = payload()
    value["outcomeEvidence"] = evidence(events={
        "6666": [{
            "eventId": "https://example.invalid/action?token=secret",
            "effectiveDate": DATES[3],
            "availableAt": f"{DATES[3]}T09:00:00+08:00",
            "factor": 1.01,
            "quality": "verified",
            "conflictStatus": "no_conflict",
        }],
    })
    mutations.append(value)
    value = payload()
    value["pricePaths"]["6666"]["2099-01-01"] = 999.0
    mutations.append(value)

    for item in mutations:
        result = accounting.measure_cohort(item, enabled=True)
        assert result["accountingComplete"] is False
        assert result["performanceEligible"] is False


def test_hostile_or_unbounded_input_fails_closed_without_escaping():
    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    for item in (BadDict(), {"schemaVersion": 10**10_000}):
        result = accounting.measure_cohort(item, enabled=True)
        assert result["accountingComplete"] is False
        assert result["performanceEligible"] is False


def test_cutoff_tie_is_reported_and_never_promotable():
    value = payload()
    selection = value["selection"]
    selection["cutoffTieDependent"] = True
    body = {
        key: item for key, item in selection.items()
        if key != "selectionDigest"
    }
    selection["selectionDigest"] = selection_digest(body)
    result = accounting.measure_cohort(value, enabled=True)

    assert result["accountingComplete"] is True
    assert "cutoff_tie_dependent" in result["blockers"]
    assert result["strategyValidated"] is False
    assert result["promotionEligible"] is False
    assert result["adviceEnabled"] is False


def test_default_off_and_unregistered_policies_remain_fail_closed():
    disabled = accounting.measure_cohort(payload())
    enabled = accounting.measure_cohort(payload(), enabled=True)

    assert disabled["mode"] == "disabled"
    assert disabled["accountingComplete"] is False
    assert enabled["mode"] == "research_only"
    assert enabled["accountingComplete"] is True
    for key in (
        "selectionCertified", "outcomeEvidenceAuthorityRegistered", "liveExecutionSpecRegistered",
        "performanceEligible", "strategyValidated", "promotionEligible", "adviceEnabled",
        "formalGateAttached", "dailyMddLimitConfigured", "riskGateEligible",
    ):
        assert enabled[key] is False


def test_aggregate_keeps_all_cash_periods_and_fails_closed_on_any_unresolved_cohort():
    first = accounting.measure_cohort(payload(), enabled=True)
    all_cash_payload = payload()
    for row in all_cash_payload["selection"]["preview"]:
        row["quality"] = {"passed": False, "blockers": ["fixture_quality_rejection"]}
    all_cash_payload["selection"]["qualityPassedCodes"] = []
    body = {
        key: item for key, item in all_cash_payload["selection"].items()
        if key != "selectionDigest"
    }
    all_cash_payload["selection"]["selectionDigest"] = selection_digest(body)
    remap_dates(all_cash_payload, NEXT_DATES)
    second = accounting.measure_cohort(all_cash_payload, enabled=True)
    summary = accounting.aggregate_measurements([first, second])

    assert summary["complete"] is True
    assert summary["executionAccounting"]["scheduledPeriods"] == 2
    assert summary["executionAccounting"]["investedPeriods"] == 1
    assert summary["executionAccounting"]["noCandidateCashSlots"] == 4
    assert math.isclose(summary["benchmarkReturn"], first["benchmarkReturn"], abs_tol=1e-12)
    assert summary["benchmarkCostedRoundTrips"] == 1
    assert len(summary["benchmarkScheduledReturns"]) == 2
    scheduled_benchmark = math.prod(
        1 + value for value in summary["benchmarkScheduledReturns"]
    ) - 1
    assert math.isclose(summary["benchmarkReturn"], scheduled_benchmark, abs_tol=1e-12)
    assert summary["benchmarkMdd"] is not None

    broken_payload = payload()
    broken_payload["pricePaths"]["6666"].pop(DATES[-1])
    remap_dates(broken_payload, NEXT_DATES)
    broken = accounting.measure_cohort(broken_payload, enabled=True)
    failed = accounting.aggregate_measurements([first, broken])
    assert failed["complete"] is False
    assert failed["return"] is None
    assert "actual_comprehensive_outcome_accounting_incomplete" in failed["blockers"]

    duplicate = accounting.aggregate_measurements([first, copy.deepcopy(first)])
    assert duplicate["complete"] is False
    assert "cohort_schedule_not_unique_and_contiguous" in duplicate["blockers"]


def test_single_cohort_mdd_is_identical_after_aggregation_and_includes_initial_cost_drop():
    value = payload()
    for code in CODES:
        value["pricePaths"][code] = {day: 100.0 for day in DATES[1:]}
    cohort = accounting.measure_cohort(value, enabled=True)
    summary = accounting.aggregate_measurements([cohort])

    assert math.isclose(summary["mdd"], cohort["selectionMdd"], abs_tol=1e-12)
    assert math.isclose(summary["eligiblePoolMdd"], cohort["eligiblePoolMdd"], abs_tol=1e-12)
    assert math.isclose(summary["benchmarkMdd"], cohort["benchmarkMdd"], abs_tol=1e-12)
    assert cohort["selectionMdd"] < 0
    assert cohort["eligiblePoolMdd"] < 0
    assert cohort["benchmarkMdd"] < 0


def test_aggregate_rejects_mutated_return_digest_curve_accounting_cost_or_formal_flag():
    original = accounting.measure_cohort(payload(), enabled=True)
    mutations = []
    for field, value in (
        ("selectionReturn", 99.0),
        ("selectionDailyEquityFactors", [1.0] * len(DATES[1:])),
        ("costModel", {**original["costModel"], "buyFee": 0.0}),
        ("promotionEligible", True),
    ):
        item = copy.deepcopy(original)
        item[field] = value
        body = {key: current for key, current in item.items() if key != "measurementDigest"}
        item["measurementDigest"] = accounting.digest(body)
        mutations.append(item)
    item = copy.deepcopy(original)
    item["measurementDigest"] = "0" * 64
    mutations.append(item)
    item = copy.deepcopy(original)
    item["selectionAccounting"]["closedSlots"] += 1
    body = {key: value for key, value in item.items() if key != "measurementDigest"}
    item["measurementDigest"] = accounting.digest(body)
    mutations.append(item)
    item = copy.deepcopy(original)
    item["selectionIdentity"]["qualityPassedSlots"] = 0
    item["selectionIdentity"]["qualityRejectedOrEmptySlots"] = 3
    body = {key: value for key, value in item.items() if key != "measurementDigest"}
    item["measurementDigest"] = accounting.digest(body)
    mutations.append(item)
    item = copy.deepcopy(original)
    item["eligiblePoolAccounting"]["selectedSlots"] -= 1
    item["eligiblePoolAccounting"]["noCandidateCashSlots"] += 1
    body = {key: value for key, value in item.items() if key != "measurementDigest"}
    item["measurementDigest"] = accounting.digest(body)
    mutations.append(item)

    for item in mutations:
        result = accounting.aggregate_measurements([item])
        assert result["complete"] is False
        assert result["return"] is None
        assert "cohort_contract_or_digest_invalid" in result["blockers"]


def test_aggregate_hostile_malformed_or_unbounded_cohorts_always_fail_closed():
    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    original = accounting.measure_cohort(payload(), enabled=True)
    bad_excess = copy.deepcopy(original)
    bad_excess["selectionExcessVersusPool"] = "not-a-number"
    body = {key: value for key, value in bad_excess.items() if key != "measurementDigest"}
    bad_excess["measurementDigest"] = accounting.digest(body)

    for value in (
        [None], ["x"], [123], [BadDict()], [bad_excess],
        [copy.deepcopy(original)] * 10_001,
    ):
        result = accounting.aggregate_measurements(value)
        assert result["complete"] is False
        assert result["return"] is None
        assert result["eligiblePoolReturn"] is None
        assert result["benchmarkReturn"] is None
        assert result["mdd"] is None
        assert result["benchmarkMdd"] is None
        assert result["blockers"] == ["cohort_contract_or_digest_invalid"]


def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
