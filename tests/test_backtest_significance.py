"""A cumulative return is not evidence; the per-rebalance interval is.

Compounding turns a handful of lucky rebalances into a headline number, so the
harness reports the mean excess per rebalance with a bootstrap interval and
refuses to call a result conclusive while that interval contains zero.
"""

import json
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy_backtest
from backtest import benchmark_total_return
from strategy_backtest import (
    benchmark_between, benchmark_schedule, benchmark_series, evaluate, run_range, significance,
)


BUY_FACTOR = 1 - 0.001425 - 10 / 10_000
SELL_FACTOR = 1 - 0.001425 - 0.001 - 10 / 10_000
FLAT_SINGLE_ROUND_TRIP = BUY_FACTOR * SELL_FACTOR - 1


def _dates(count):
    from datetime import date, timedelta
    start = date(2026, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def _intervals(dates):
    return [
        {"entry": dates[index], "exit": dates[index + 1], "net": 0.0}
        for index in range(len(dates) - 1)
    ]


def test_a_noisy_edge_is_not_called_conclusive():
    # Mean is positive but the spread dwarfs it.
    noisy = [0.30, -0.28, 0.25, -0.22, 0.20, -0.18, 0.02] * 3
    result = significance(noisy)

    assert result["meanExcessPerRebalance"] > 0
    assert result["ci95"][0] < 0 < result["ci95"][1]
    assert result["conclusive"] is False, "an interval spanning zero decides nothing"


def test_a_consistent_edge_is_called_conclusive():
    steady = [0.02, 0.025, 0.018, 0.022, 0.021, 0.019, 0.023, 0.020] * 3
    result = significance(steady)

    assert result["ci95"][0] > 0
    assert result["conclusive"] is True
    assert result["tStat"] > 3


def test_a_consistent_loss_is_conclusive_too():
    losing = [-0.02, -0.025, -0.018, -0.022, -0.021, -0.019] * 3
    result = significance(losing)

    assert result["ci95"][1] < 0
    assert result["conclusive"] is True, "a reliably negative edge is also a finding"


def test_too_few_rebalances_never_conclude():
    result = significance([0.5, 0.6])

    assert result["conclusive"] is False
    assert result["reason"] == "fewer_than_three_rebalances"


def test_bootstrap_is_reproducible():
    sample = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.015]

    assert significance(sample)["ci95"] == significance(sample)["ci95"]


def test_benchmark_period_return_is_net_of_etf_costs():
    series = {"2026-01-02": 100.0, "2026-01-09": 110.0}
    net = benchmark_between(series, "2026-01-02", "2026-01-09")

    assert net is not None
    assert net < 0.10, "a 10% gross move must not survive costs intact"
    assert net > 0.09


def test_flat_ten_period_schedule_charges_exactly_one_round_trip():
    dates = _dates(11)
    schedule = benchmark_schedule(
        {day: 100.0 for day in dates}, dates, _intervals(dates)
    )

    assert schedule["complete"] is True
    assert schedule["costedRoundTrips"] == 1
    assert len(schedule["scheduledReturns"]) == 10
    assert math.isclose(schedule["return"], FLAT_SINGLE_ROUND_TRIP, abs_tol=1e-15)
    assert math.isclose(
        math.prod(1 + value for value in schedule["scheduledReturns"]) - 1,
        schedule["return"],
        abs_tol=1e-15,
    )
    repeated = (1 + FLAT_SINGLE_ROUND_TRIP) ** 10 - 1
    assert schedule["return"] > repeated + 0.04, "the old per-period overcharge must be impossible"


def test_partitioning_one_continuous_path_never_changes_benchmark_return():
    dates = _dates(3)
    series = {dates[0]: 100.0, dates[1]: 110.0, dates[2]: 121.0}
    one = benchmark_schedule(
        series, dates, [{"entry": dates[0], "exit": dates[2], "net": 0.0}]
    )
    two = benchmark_schedule(series, dates, _intervals(dates))
    expected = 1.21 * BUY_FACTOR * SELL_FACTOR - 1

    assert one["complete"] is True and two["complete"] is True
    assert math.isclose(one["return"], expected, abs_tol=1e-15)
    assert math.isclose(two["return"], expected, abs_tol=1e-15)
    assert math.isclose(one["return"], two["return"], abs_tol=1e-15)
    assert two["scheduledReturns"][0] < 0.10
    assert two["scheduledReturns"][1] < 0.10


def test_schedule_equals_the_outer_boundary_buy_and_hold_across_partitions_and_scales():
    dates = _dates(6)
    for scale in (1.0, 1_000_000.0):
        series = {
            day: scale * value
            for day, value in zip(dates, (100.0, 83.0, 125.0, 99.0, 140.0, 117.0))
        }
        expected_factor = series[dates[-1]] / series[dates[0]] * BUY_FACTOR * SELL_FACTOR
        partitions = (
            [{"entry": dates[0], "exit": dates[-1], "net": 0.0}],
            _intervals(dates),
            [
                {"entry": dates[0], "exit": dates[2], "net": 0.0},
                {"entry": dates[2], "exit": dates[-1], "net": 0.0},
            ],
        )
        for intervals in partitions:
            schedule = benchmark_schedule(series, dates, intervals)
            assert schedule["complete"] is True
            assert math.isclose(1 + schedule["return"], expected_factor, rel_tol=1e-12)


def test_schedule_matches_the_independent_exact_bounds_benchmark_loader():
    dates = _dates(6)
    rows = [
        {"date": day, "price": 50.0 + index, "total_return": 100.0 + index * 2}
        for index, day in enumerate(dates)
    ]
    series = {row["date"]: row["total_return"] for row in rows}
    schedule = benchmark_schedule(series, dates, _intervals(dates))
    with TemporaryDirectory() as folder:
        path = Path(folder) / "benchmark.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        independent = benchmark_total_return(path, dates)

    assert schedule["complete"] is True
    assert independent is not None
    assert math.isclose(schedule["return"], independent, abs_tol=1e-12)


def test_benchmark_parser_rejects_ambiguous_nonstandard_or_resource_hostile_json():
    hostile = [
        '[{"date":"2026-01-01","total_return":100,"total_return":101}]',
        '[{"date":"2026-01-01","total_return":NaN}]',
        '[{"date":"2026-01-01","total_return":1000000000000000001}]',
        '[{"date":"2026-01-01","total_return":' + "9" * 10_000 + '}]',
        "[" * 2_000 + "0" + "]" * 2_000,
    ]
    with TemporaryDirectory() as folder:
        path = Path(folder) / "benchmark.json"
        for payload in hostile:
            path.write_text(payload, encoding="utf-8")
            assert benchmark_series(path) == {}


def test_invalid_holding_never_enters_the_backtest_loop():
    dates = _dates(30)
    history = [{} for _ in dates]
    for holding in (0, -1, True, 1.5):
        try:
            run_range(history, dates, 0, len(history), "swing", 3, holding, 0)
        except ValueError as error:
            assert str(error) == "invalid_backtest_schedule"
        else:
            raise AssertionError("invalid holding must fail before entering the loop")


def test_invalid_volume_floor_never_changes_the_legacy_pool():
    dates = _dates(30)
    history = [{} for _ in dates]
    for floor in (True, float("nan"), float("inf"), -1, 10**18 + 1):
        try:
            run_range(history, dates, 0, len(history), "swing", 3, 5, floor)
        except ValueError as error:
            assert str(error) == "invalid_backtest_schedule"
        else:
            raise AssertionError("an invalid volume floor must fail before selection")


def test_selected_entry_without_a_valid_exit_invalidates_legacy_performance():
    dates = _dates(30)
    history = [{} for _ in dates]
    history[21] = {"2330": (100.0, 1_000.0)}
    selected = [(80, 100, "2330", {}, {})]
    with patch.object(strategy_backtest, "factor_quotes", return_value={"2330": {}}), \
            patch.object(strategy_backtest, "candidates", return_value=selected):
        run = run_range(history, dates, 0, len(history), "swing", 1, 5, 0)

    assert run["legacyReturnComplete"] is False
    assert run["return"] is None
    assert run["trades"] == 0
    assert run["unresolved_exits"] == 1
    assert run["benchmarkIntervals"][0]["net"] is None
    assert "unresolved_exit_price" in run["selectionBlockers"]


def test_schedule_rejects_missing_marks_gaps_overlaps_and_bad_values():
    dates = _dates(4)
    series = {day: 100.0 for day in dates}
    valid = _intervals(dates)
    hostile = [
        ({key: value for key, value in series.items() if key != dates[1]}, dates, valid),
        (series, dates, [valid[0], valid[2]]),
        (series, dates, [valid[0], valid[0], valid[1], valid[2]]),
        ({**series, dates[1]: float("nan")}, dates, valid),
        ({**series, dates[1]: float("inf")}, dates, valid),
        ({**series, dates[1]: 0.0}, dates, valid),
        ({**series, dates[-1]: 5e-324}, dates, valid),
        ({"a": 100.0, "b": 100.0}, ["a", "b"], [{"entry": "a", "exit": "b", "net": 0.0}]),
    ]
    for values, calendar, intervals in hostile:
        result = benchmark_schedule(values, calendar, intervals)
        assert result["complete"] is False
        assert result["return"] is None
        assert result["scheduledReturns"] == []


def test_standalone_interval_rejects_non_dates_order_and_subnormal_cancellation():
    good = {"2026-01-01": 100.0, "2026-01-02": 100.0}
    assert benchmark_between(good, "2026-01-01", "2026-01-01") is None
    assert benchmark_between(good, "2026-01-02", "2026-01-01") is None
    assert benchmark_between({"a": 1.0, "b": 1.0}, "a", "b") is None
    assert benchmark_between({"2026-01-01": 1.0, "2026-01-02": 5e-324},
                             "2026-01-01", "2026-01-02") is None
    assert benchmark_between({"2026-01-01": 1.0, "2026-01-02": 10**18 + 1},
                             "2026-01-01", "2026-01-02") is None


def test_exact_integer_bounds_cannot_round_down_into_the_numeric_domain():
    assert significance([10**18 + 1] * 3)["reason"] == "invalid_significance_sample"
    for gross in (10**18 + 1, -(10**18 + 1)):
        try:
            strategy_backtest.net_of_costs(gross)
        except ValueError as error:
            assert str(error) == "invalid_strategy_return"
        else:
            raise AssertionError("an out-of-domain exact integer must not be float-rounded")


def test_full_legacy_wiring_reverses_the_old_false_positive_and_keeps_cash_periods():
    dates = _dates(150)
    history = [{} for _ in dates]

    def fake_run_range(_history, labels, start, end, *_args, **_kwargs):
        bounds = labels[start + 1:start + 12]
        active_indexes = {0, 1, 2, 7, 8, 9}
        intervals = [
            {
                "entry": bounds[index], "exit": bounds[index + 1],
                "net": -0.003 if index in active_indexes else 0.0,
            }
            for index in range(10)
        ]
        # Active-only pool diagnostics deliberately omit four legitimate cash
        # periods; the benchmark denominator must still retain all ten.
        active = [
            {**row, "poolExcess": -0.001, "poolSize": 100}
            for index, row in enumerate(intervals) if index in active_indexes
        ]
        return {
            "return": (1 - 0.003) ** 6 - 1,
            "mdd": -0.01,
            "legacyReturnComplete": True,
            "trades": 6,
            "scheduledPeriods": 10,
            "win_rate": 0.0,
            "unfilled": 4,
            "stale_exits": 0,
            "unresolved_exits": 0,
            "rebalances_without_candidates": 4,
            "returns": [-0.003] * 6,
            "rebalances": active,
            "benchmarkIntervals": intervals,
            "comparisonFrom": bounds[0],
            "comparisonTo": bounds[-1],
            "selectionPolicyVersion": "legacy-research-selection",
            "selectionAdapterUsed": False,
            "selectionPeriods": 10,
            "selectionEvidencePeriods": 0,
            "selectionEvidenceShapeComplete": True,
            "selectionEvidenceComplete": True,
            "selectionCertified": True,
            "performanceEligible": False,
            "executionAccountingStatus": "legacy_unregistered",
            "cutoffTieDependentPeriods": 0,
            "selectionBlockers": [],
            "versusEligiblePool": {"rebalances": 6, "medianPoolSize": 100},
        }

    with TemporaryDirectory() as folder:
        benchmark_path = Path(folder) / "benchmark.json"
        benchmark_path.write_text(json.dumps([
            {"date": day, "total_return": 100.0} for day in dates
        ]), encoding="utf-8")
        with patch.object(strategy_backtest, "load_history", return_value=(dates, history)), \
                patch.object(strategy_backtest, "run_range", side_effect=fake_run_range):
            result = evaluate(
                Path(folder) / "unused.jsonl", benchmark_path,
                "swing", picks=3, holding=5, min_volume=0,
            )

    assert result["artifactPolicyVersion"] == "legacy-strategy-backtest-single-split-benchmark-v2"
    for split in result["splits"].values():
        assert split["benchmarkAccountingComplete"] is True
        assert split["performanceAccountingComplete"] is True
        assert split["benchmarkCostedRoundTrips"] == 1
        assert split["scheduledPeriods"] == 10
        assert split["significance"]["rebalances"] == 10
        assert split["significance"]["meanExcessPerRebalance"] < 0
        assert split["significance"]["ci95"][1] < 0
        assert split["significance"]["conclusive"] is True
        assert math.isclose(split["benchmark"], FLAT_SINGLE_ROUND_TRIP, abs_tol=1e-15)
        assert split["versusEligiblePool"]["rebalances"] == 6


def test_rising_benchmark_cash_periods_are_negative_excess_not_zero():
    dates = _dates(150)
    history = [{} for _ in dates]
    active_indexes = {0, 1, 2, 7, 8, 9}
    cash_indexes = {3, 4, 5, 6}

    def fake_run_range(_history, labels, start, end, *_args, **_kwargs):
        bounds = labels[start + 1:start + 12]
        intervals = [
            {
                "entry": bounds[index], "exit": bounds[index + 1],
                "net": 0.01 if index in active_indexes else 0.0,
            }
            for index in range(10)
        ]
        active = [
            {**row, "poolExcess": 0.01, "poolSize": 100}
            for index, row in enumerate(intervals) if index in active_indexes
        ]
        return {
            "return": 1.01 ** 6 - 1,
            "mdd": 0.0,
            "legacyReturnComplete": True,
            "trades": 6,
            "scheduledPeriods": 10,
            "win_rate": 1.0,
            "unfilled": 4,
            "stale_exits": 0,
            "unresolved_exits": 0,
            "rebalances_without_candidates": 4,
            "returns": [0.01] * 6,
            "rebalances": active,
            "benchmarkIntervals": intervals,
            "comparisonFrom": bounds[0],
            "comparisonTo": bounds[-1],
            "selectionPolicyVersion": "legacy-research-selection",
            "selectionAdapterUsed": False,
            "selectionPeriods": 10,
            "selectionEvidencePeriods": 0,
            "selectionEvidenceShapeComplete": True,
            "selectionEvidenceComplete": True,
            "selectionCertified": True,
            "performanceEligible": False,
            "executionAccountingStatus": "legacy_unregistered",
            "cutoffTieDependentPeriods": 0,
            "selectionBlockers": [],
            "versusEligiblePool": {"rebalances": 6, "medianPoolSize": 100},
        }

    # The benchmark is flat during every active interval and rises 20% during
    # each cash interval.  An active-only denominator would report all six
    # active excess observations as positive; the full scheduled denominator
    # must retain four -20% opportunity-cost observations.
    growth_days = {
        start + offset
        for start in (0, 90, 120)
        for offset in range(5, 9)
    }
    level = 100.0
    benchmark_rows = []
    for index, day in enumerate(dates):
        if index in growth_days:
            level *= 1.2
        benchmark_rows.append({"date": day, "total_return": level})

    captured_excess = []
    real_significance = strategy_backtest.significance

    def capture_significance(values, *args, **kwargs):
        captured_excess.append(list(values))
        return real_significance(values, *args, **kwargs)

    with TemporaryDirectory() as folder:
        benchmark_path = Path(folder) / "benchmark.json"
        benchmark_path.write_text(json.dumps(benchmark_rows), encoding="utf-8")
        with patch.object(strategy_backtest, "load_history", return_value=(dates, history)), \
                patch.object(strategy_backtest, "run_range", side_effect=fake_run_range), \
                patch.object(strategy_backtest, "significance", side_effect=capture_significance):
            result = evaluate(
                Path(folder) / "unused.jsonl", benchmark_path,
                "swing", picks=3, holding=5, min_volume=0,
            )

    assert result["artifactPolicyVersion"] == "legacy-strategy-backtest-single-split-benchmark-v2"
    assert result["benchmarkComparatorVersion"] \
        == "full-scheduled-calendar-0050-total-return-comparator-v2"
    assert len(captured_excess) == 3
    for split, excess in zip(result["splits"].values(), captured_excess):
        references = split["benchmarkScheduledReturns"]
        expected = [
            (0.01 if index in active_indexes else 0.0) - reference
            for index, reference in enumerate(references)
        ]
        assert len(excess) == 10
        assert all(math.isclose(actual, wanted, abs_tol=1e-15)
                   for actual, wanted in zip(excess, expected))
        assert all(0.01 - references[index] > 0 for index in active_indexes)
        assert all(math.isclose(references[index], 0.2, abs_tol=1e-12)
                   for index in cash_indexes)
        assert all(math.isclose(excess[index], -0.2, abs_tol=1e-12)
                   and excess[index] < 0 for index in cash_indexes)
        assert split["benchmarkComparatorVersion"] \
            == "full-scheduled-calendar-0050-total-return-comparator-v2"
        assert split["benchmarkCostModel"] \
            == "official-0050-total-return-single-split-round-trip-v1"
        assert split["significance"]["rebalances"] == 10
        assert split["significance"]["meanExcessPerRebalance"] < 0
        assert split["significance"]["ci95"][1] < 0
        assert split["significance"]["conclusive"] is True


def test_one_missing_benchmark_mark_invalidates_the_whole_split_without_sample_shrinkage():
    dates = _dates(150)
    history = [{} for _ in dates]

    def fake_run_range(_history, labels, start, end, *_args, **_kwargs):
        bounds = labels[start + 1:start + 5]
        intervals = [
            {"entry": bounds[index], "exit": bounds[index + 1], "net": 0.01}
            for index in range(3)
        ]
        return {
            "return": (1.01 ** 3) - 1,
            "mdd": 0.0,
            "legacyReturnComplete": True,
            "trades": 3,
            "scheduledPeriods": 3,
            "win_rate": 1.0,
            "unfilled": 0,
            "stale_exits": 0,
            "unresolved_exits": 0,
            "rebalances_without_candidates": 0,
            "returns": [0.01] * 3,
            "rebalances": [],
            "benchmarkIntervals": intervals,
            "comparisonFrom": bounds[0],
            "comparisonTo": bounds[-1],
            "selectionPolicyVersion": "legacy-research-selection",
            "selectionAdapterUsed": False,
            "selectionPeriods": 3,
            "selectionEvidencePeriods": 0,
            "selectionEvidenceShapeComplete": True,
            "selectionEvidenceComplete": True,
            "selectionCertified": True,
            "performanceEligible": False,
            "executionAccountingStatus": "legacy_unregistered",
            "cutoffTieDependentPeriods": 0,
            "selectionBlockers": [],
            "versusEligiblePool": {"rebalances": 0, "medianPoolSize": 0},
        }

    with TemporaryDirectory() as folder:
        benchmark_path = Path(folder) / "benchmark.json"
        missing = dates[2]
        benchmark_path.write_text(json.dumps([
            {"date": day, "total_return": 100.0}
            for day in dates if day != missing
        ]), encoding="utf-8")
        with patch.object(strategy_backtest, "load_history", return_value=(dates, history)), \
                patch.object(strategy_backtest, "run_range", side_effect=fake_run_range):
            result = evaluate(
                Path(folder) / "unused.jsonl", benchmark_path,
                "swing", picks=3, holding=5, min_volume=0,
            )

    train = result["splits"]["train"]
    assert train["benchmarkAccountingComplete"] is False
    assert train["performanceAccountingComplete"] is False
    assert train["benchmark"] is None
    assert train["excess"] is None
    assert train["significance"]["rebalances"] == 0
    assert train["significance"]["conclusive"] is False
    assert "benchmark_exact_path_missing" in train["significance"]["blockers"]


def test_unresolved_exit_blocks_performance_even_when_the_benchmark_path_is_complete():
    dates = _dates(150)
    history = [{} for _ in dates]

    def fake_run_range(_history, labels, start, end, *_args, **_kwargs):
        bounds = labels[start + 1:start + 5]
        intervals = [
            {
                "entry": bounds[index], "exit": bounds[index + 1],
                "net": None if index == 0 else 0.0,
            }
            for index in range(3)
        ]
        return {
            "return": None,
            "mdd": None,
            "legacyReturnComplete": False,
            "trades": 0,
            "scheduledPeriods": 3,
            "win_rate": 0.0,
            "unfilled": 0,
            "stale_exits": 1,
            "unresolved_exits": 1,
            "rebalances_without_candidates": 0,
            "returns": [],
            "rebalances": [],
            "benchmarkIntervals": intervals,
            "comparisonFrom": bounds[0],
            "comparisonTo": bounds[-1],
            "selectionPolicyVersion": "legacy-research-selection",
            "selectionAdapterUsed": False,
            "selectionPeriods": 3,
            "selectionEvidencePeriods": 0,
            "selectionEvidenceShapeComplete": True,
            "selectionEvidenceComplete": True,
            "selectionCertified": True,
            "performanceEligible": False,
            "executionAccountingStatus": "legacy_unregistered",
            "cutoffTieDependentPeriods": 0,
            "selectionBlockers": ["unresolved_exit_price"],
            "versusEligiblePool": {"rebalances": 0, "medianPoolSize": 0},
        }

    with TemporaryDirectory() as folder:
        benchmark_path = Path(folder) / "benchmark.json"
        benchmark_path.write_text(json.dumps([
            {"date": day, "total_return": 100.0} for day in dates
        ]), encoding="utf-8")
        with patch.object(strategy_backtest, "load_history", return_value=(dates, history)), \
                patch.object(strategy_backtest, "run_range", side_effect=fake_run_range):
            result = evaluate(
                Path(folder) / "unused.jsonl", benchmark_path,
                "swing", picks=3, holding=5, min_volume=0,
            )

    for split in result["splits"].values():
        assert split["benchmarkAccountingComplete"] is True
        assert split["performanceAccountingComplete"] is False
        assert split["excess"] is None
        assert split["significance"]["rebalances"] == 0
        assert split["significance"]["conclusive"] is False
        assert "unresolved_exit_price" in split["significance"]["blockers"]
        assert "benchmarkIntervals" not in split


def test_missing_benchmark_dates_yield_none():
    series = {"2026-01-02": 100.0}

    assert benchmark_between(series, "2026-01-02", "2026-01-09") is None
    assert benchmark_between(series, "2025-12-31", "2026-01-02") is None


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
