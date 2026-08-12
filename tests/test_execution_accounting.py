import math
import unittest

from execution_accounting import (
    aggregate_periods,
    daily_equity_curve,
    max_drawdown_from_equity,
    rebalance_schedule,
    settle_equal_weight_period,
)
from total_return_backtest import Series, buy_and_hold_metrics, run_period
from backtest import run_slice


BUY = 0.001425 + 0.001
SELL = 0.001425 + 0.003 + 0.001


class ExecutionAccountingTests(unittest.TestCase):
    def test_partial_fill_keeps_four_fifths_in_cash(self):
        period = settle_equal_weight_period(
            [
                {"status": "closed", "grossReturn": 0.10, "dailyGrossFactors": [1.0, 1.10]},
                {"status": "cash_unfilled"},
            ],
            5,
            buy_cost=BUY,
            sell_cost=SELL,
        )
        one_slot = (1.10 * (1 - BUY) * (1 - SELL) - 1) / 5
        self.assertAlmostEqual(period["return"], one_slot, places=12)
        self.assertEqual(period["filledSlots"], 1)
        self.assertEqual(period["unfilledEntrySlots"], 1)
        self.assertEqual(period["noCandidateCashSlots"], 3)
        self.assertAlmostEqual(period["cashWeight"], 0.8)
        self.assertEqual(period["costedRoundTrips"], 1)

    def test_all_cash_is_a_scheduled_zero_not_an_active_sample(self):
        period = settle_equal_weight_period([], 3, buy_cost=BUY, sell_cost=SELL)
        summary = aggregate_periods([period], comparison_from="d1", comparison_to="d3")
        self.assertEqual(period["return"], 0.0)
        self.assertEqual(summary["scheduledPeriods"], 1)
        self.assertEqual(summary["investedPeriods"], 0)
        self.assertEqual(summary["costedRoundTrips"], 0)

    def test_unresolved_exit_never_emits_partial_performance(self):
        period = settle_equal_weight_period(
            [
                {"status": "closed", "grossReturn": 0.50, "dailyGrossFactors": [1.0, 1.5]},
                {"status": "unresolved_exit", "reason": "nominal_exit_missing"},
            ],
            2,
            buy_cost=BUY,
            sell_cost=SELL,
        )
        self.assertFalse(period["complete"])
        self.assertIsNone(period["return"])
        self.assertIsNone(period["equityFactors"])
        self.assertIn("nominal_exit_missing", period["blockers"])

    def test_daily_path_captures_intraperiod_crash(self):
        period = settle_equal_weight_period(
            [{"status": "closed", "grossReturn": 0.0, "dailyGrossFactors": [1.0, 0.5, 1.0]}],
            1,
            buy_cost=BUY,
            sell_cost=SELL,
        )
        mdd = max_drawdown_from_equity(daily_equity_curve([period]))
        self.assertIsNotNone(mdd)
        self.assertLess(mdd, -0.49)
        self.assertGreater(mdd, -0.51)

    def test_holding_definition_is_shared_and_exact(self):
        total_return = rebalance_schedule(
            12, lookback=2, holding=3, convention="signal_plus_holding"
        )
        official = rebalance_schedule(
            12, lookback=2, holding=3, convention="entry_plus_holding"
        )
        self.assertEqual(
            [(point.signal_index, point.entry_index, point.exit_index) for point in total_return],
            [(2, 3, 5), (5, 6, 8), (8, 9, 11)],
        )
        self.assertEqual(
            [(point.signal_index, point.entry_index, point.exit_index) for point in official],
            [(2, 3, 6), (5, 6, 9)],
        )

    def test_unknown_or_non_finite_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            settle_equal_weight_period([{"status": "mystery"}], 1, buy_cost=BUY, sell_cost=SELL)
        with self.assertRaises(ValueError):
            settle_equal_weight_period(
                [{"status": "closed", "grossReturn": math.nan, "dailyGrossFactors": [1.0, 1.0]}],
                1,
                buy_cost=BUY,
                sell_cost=SELL,
            )

    def test_run_period_does_not_reallocate_an_unfilled_pick(self):
        dates = [f"d{index}" for index in range(5)]
        winner = Series("1111", {"d0": 1.0, "d1": 2.0, "d2": 2.0, "d3": 2.1, "d4": 2.2}, 0)
        unfilled = Series("2222", {"d0": 1.0, "d1": 1.5, "d3": 1.6, "d4": 1.7}, 0)
        result = run_period({"1111": winner, "2222": unfilled}, dates, lookback=1, holding=2, picks_count=2)
        winner_net = (1.05 * (1 - BUY) * (1 - SELL) - 1) / 2
        self.assertAlmostEqual(result["totalReturn"], winner_net, places=12)
        self.assertEqual(result["executionAccounting"]["unfilledEntrySlots"], 1)
        self.assertAlmostEqual(result["executionAccounting"]["averageCashWeight"], 0.5)

    def test_flat_official_benchmark_charges_one_round_trip(self):
        dates = [f"d{index}" for index in range(8)]
        benchmark = Series("0050_TR", {day: 100.0 for day in dates}, 0)
        result = buy_and_hold_metrics(benchmark, dates, "d2", "d7", is_etf=True)
        etf_sell = 0.001425 + 0.001 + 0.001
        expected = (1 - BUY) * (1 - etf_sell) - 1
        self.assertAlmostEqual(result["totalReturn"], expected, places=12)
        self.assertEqual(result["executionAccounting"]["costedRoundTrips"], 1)
        self.assertEqual(result["executionAccounting"]["comparisonTradingDays"], 6)

    def test_official_benchmark_requires_start_end_and_every_intermediate_mark(self):
        dates = [f"d{index}" for index in range(6)]
        complete = {day: 100.0 for day in dates}
        for missing in ("d1", "d3", "d5"):
            values = dict(complete)
            values.pop(missing)
            result = buy_and_hold_metrics(Series("0050_TR", values, 0), dates, "d1", "d5")
            self.assertFalse(result["executionComplete"], missing)

    def test_strategy_annualization_uses_exact_comparison_path(self):
        dates = [f"d{index}" for index in range(10)]
        stock = Series("1111", {day: 100 + index for index, day in enumerate(dates)}, 0)
        result = run_period({"1111": stock}, dates, lookback=2, holding=3, picks_count=1)
        days = result["executionAccounting"]["comparisonTradingDays"]
        expected = (1 + result["totalReturn"]) ** (252 / days) - 1
        self.assertEqual(days, 6)
        self.assertAlmostEqual(result["annualizedReturn"], expected, places=12)

    def test_run_period_daily_mdd_sees_recovery_after_crash(self):
        dates = [f"d{index}" for index in range(5)]
        stock = Series("1111", {"d0": 50.0, "d1": 100.0, "d2": 100.0, "d3": 50.0, "d4": 100.0}, 0)
        result = run_period({"1111": stock}, dates, lookback=1, holding=2, picks_count=1)
        self.assertFalse(result["riskGateEligible"])
        self.assertFalse(result["dailyMddLimitConfigured"])
        self.assertIn("daily_mdd_limit_unconfigured", result["riskBlockers"])
        self.assertEqual(result["mddBasis"], "daily_mark_to_market_including_costs")
        self.assertLess(result["mdd"], -0.49)

    def test_official_engine_missing_exit_is_not_a_stale_sale(self):
        volume = 1_000_000.0
        history = [
            {"1111": (100.0, volume), "2222": (100.0, volume)},
            {"1111": (200.0, volume), "2222": (110.0, volume)},
            {"1111": (200.0, volume), "2222": (110.0, volume)},
            {"1111": (50.0, volume), "2222": (115.0, volume)},
            {"2222": (121.0, volume)},
        ]
        result = run_slice(history, lookback=1, count=1, holding=2)
        self.assertFalse(result["executionComplete"])
        self.assertIsNone(result["return"])
        self.assertEqual(result["executionAccounting"]["selectedSlots"], 1)
        self.assertEqual(result["executionAccounting"]["unresolvedExitSlots"], 1)

    def test_cutoff_tie_is_diagnostic_and_cannot_be_promoted(self):
        dates = [f"d{index}" for index in range(5)]
        values = {day: 100 + index for index, day in enumerate(dates)}
        result = run_period(
            {code: Series(code, values, 0) for code in ("1111", "2222", "3333")},
            dates,
            lookback=1,
            holding=2,
            picks_count=2,
        )
        self.assertFalse(result["selectionCertified"])
        self.assertGreater(result["executionAccounting"]["tieBreakDependentSlots"], 0)
        self.assertIn("cutoff_tie_dependent", result["executionAccounting"]["blockers"])


if __name__ == "__main__":
    unittest.main()
