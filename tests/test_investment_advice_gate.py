import copy
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from backtest import benchmark_total_return
from investment_advice_gate import evaluate
from walk_forward import evaluate as evaluate_walk_forward


def accounting(*, complete=True, invested=5, ties=0):
    scheduled = 5
    closed = invested
    target = scheduled
    return {
        "schemaVersion": 1,
        "policyVersion": "fixed-equal-weight-slots-v1",
        "complete": complete,
        "investedPeriods": invested,
        "scheduledPeriods": scheduled,
        "targetSlotsTotal": target,
        "selectedSlots": closed,
        "filledSlots": closed,
        "closedSlots": closed,
        "unfilledEntrySlots": 0,
        "noCandidateCashSlots": target - closed,
        "unresolvedExitSlots": 0,
        "costedRoundTrips": closed,
        "averageCashWeight": (target - closed) / target,
        "tieBreakDependentSlots": ties,
        "comparisonFrom": "2025-01-02",
        "comparisonTo": "2025-06-30",
        "comparisonTradingDays": 120,
    }


def move_bounds(item, start, end, days=30):
    item["executionAccounting"]["comparisonFrom"] = start
    item["executionAccounting"]["comparisonTo"] = end
    item["executionAccounting"]["comparisonTradingDays"] = days
    item["benchmarkAccounting"] = {
        "comparisonFrom": start,
        "comparisonTo": end,
        "comparisonTradingDays": days,
    }


def strategy(*, complete=True, invested=5, ties=0, risk=True):
    return {
        "return": 0.10,
        "mdd": -0.08,
        "mddBasis": "daily_mark_to_market_including_costs",
        "riskPolicyVersion": "pre-registered-daily-mdd-v1" if risk else "daily-mdd-unconfigured-v1",
        "riskPolicyHash": "f" * 64 if risk else None,
        "dailyMddLimitConfigured": risk,
        "dailyMddGatePassed": risk,
        "riskGateEligible": risk,
        "selectionCertified": ties == 0,
        "executionComplete": complete,
        "executionAccounting": accounting(complete=complete, invested=invested, ties=ties),
    }


def benchmark_run(*, complete=True):
    item = strategy(complete=complete)
    item["riskGateEligible"] = False
    item["dailyMddGatePassed"] = False
    return item


def valid_evidence():
    benchmark_accounting = {
        key: accounting()[key]
        for key in ("comparisonFrom", "comparisonTo", "comparisonTradingDays")
    }
    one_year = {
        "schemaVersion": 2,
        "decision": "candidate",
        "validation": strategy(),
        "test": strategy(),
        "benchmark": {
            "total_return": True,
            "executionComplete": True,
            "validation_net_return": 0.02,
            "test_net_return": 0.03,
            "cost_model": "one exact-boundary buy-and-hold round trip per split",
            "validationAccounting": benchmark_accounting,
            "testAccounting": benchmark_accounting,
        },
    }
    total_return = {
        "schemaVersion": 2,
        "status": "candidate",
        "promotionBlocked": False,
        "promotionBlockers": [],
        "splits": {
            "validation": {"strategy": strategy(), "benchmark0050": benchmark_run()},
            "test": {"strategy": strategy(), "benchmark0050": benchmark_run()},
        },
    }
    for split in total_return["splits"].values():
        strategy_accounting = split["strategy"]["executionAccounting"]
        benchmark_item = split["benchmark0050"]
        benchmark_item["executionAccounting"] = {
            **benchmark_item["executionAccounting"],
            "comparisonFrom": strategy_accounting["comparisonFrom"],
            "comparisonTo": strategy_accounting["comparisonTo"],
            "comparisonTradingDays": strategy_accounting["comparisonTradingDays"],
        }
    rolling = {
        "schemaVersion": 2,
        "promotionPassed": True,
        "blockers": [],
        "windows": [],
    }
    dates = [
        ("2024-01-01", "2024-06-30", "2024-02-01", "2024-03-15", "2024-04-01", "2024-05-31"),
        ("2024-07-01", "2024-12-31", "2024-08-01", "2024-09-15", "2024-10-01", "2024-11-30"),
        ("2025-01-01", "2025-06-30", "2025-02-01", "2025-03-15", "2025-04-01", "2025-05-31"),
    ]
    for window_from, window_to, validation_from, validation_to, test_from, test_to in dates:
        validation = {"strategy": 0.1, "benchmark": 0.02, "benchmarkCostModel": "one exact-boundary buy-and-hold round trip per split", **strategy()}
        test = {"strategy": 0.1, "benchmark": 0.02, "benchmarkCostModel": "one exact-boundary buy-and-hold round trip per split", **strategy()}
        move_bounds(validation, validation_from, validation_to)
        move_bounds(test, test_from, test_to)
        rolling["windows"].append({"from": window_from, "to": window_to, "passed": True, "validation": validation, "test": test})
    return one_year, total_return, rolling


class InvestmentAdviceGateTests(unittest.TestCase):
    def test_official_benchmark_loader_accepts_utf8_bom_and_costs(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tai50.json"
            path.write_text('[{"date":"2026-01-01","total_return":100.0},{"date":"2026-01-02","total_return":110.0}]', encoding="utf-8-sig")
            value = benchmark_total_return(path, ["2026-01-01", "2026-01-02"])
        self.assertIsNotNone(value)
        self.assertLess(value, 0.10)

    def test_official_benchmark_requires_every_exact_path_date(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tai50.json"
            rows = [
                {"date": "2026-01-01", "total_return": 100.0},
                {"date": "2026-01-03", "total_return": 110.0},
            ]
            path.write_text(json.dumps(rows), encoding="utf-8")
            self.assertIsNone(benchmark_total_return(
                path, ["2026-01-01", "2026-01-02", "2026-01-03"]
            ))

    def test_no_claim_can_enable_advice_before_risk_policy_is_registered(self):
        one_year, total_return, rolling = valid_evidence()
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("daily_mdd_limit_unconfigured", result["blockers"])
        self.assertEqual(result["schemaVersion"], 2)

    def test_missing_rolling_evidence_fails_closed(self):
        one_year, total_return, _ = valid_evidence()
        result = evaluate(one_year, total_return, None)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("execution_accounting_contract_missing", result["blockers"])

    def test_duplicate_rolling_windows_do_not_count_as_three(self):
        one_year, total_return, rolling = valid_evidence()
        rolling["windows"] = [copy.deepcopy(rolling["windows"][0]) for _ in range(3)]
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("rolling_validation_not_passed", result["blockers"])

    def test_benchmark_accounting_must_match_strategy_exactly(self):
        one_year, total_return, rolling = valid_evidence()
        total_return["splits"]["test"]["benchmark0050"]["executionAccounting"]["comparisonTradingDays"] = 119
        rolling["windows"][0]["test"]["benchmarkAccounting"]["comparisonTo"] = "2025-06-29"
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("benchmark_exact_bounds_missing", result["blockers"])

    def test_counter_drift_is_rejected(self):
        one_year, total_return, rolling = valid_evidence()
        one_year["test"]["executionAccounting"]["costedRoundTrips"] += 1
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("execution_accounting_contract_missing", result["blockers"])

    def test_legacy_or_missing_execution_contract_fails_closed(self):
        one_year, total_return, rolling = valid_evidence()
        one_year["schemaVersion"] = 1
        one_year["test"].pop("executionAccounting")
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("execution_accounting_contract_missing", result["blockers"])

    def test_unresolved_or_unconfigured_risk_fails_closed(self):
        one_year, total_return, rolling = valid_evidence()
        one_year["test"] = strategy(complete=False, risk=False)
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("execution_accounting_incomplete", result["blockers"])

    def test_all_cash_cannot_satisfy_active_sample_gate(self):
        one_year, total_return, rolling = valid_evidence()
        one_year["validation"] = strategy(invested=0)
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("active_sample_missing", result["blockers"])

    def test_cutoff_tie_or_missing_benchmark_bounds_fails_closed(self):
        one_year, total_return, rolling = valid_evidence()
        one_year["test"] = strategy(ties=1)
        one_year["benchmark"]["testAccounting"]["comparisonTo"] = "2025-06-29"
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("cutoff_tie_dependent", result["blockers"])
        self.assertIn("benchmark_exact_bounds_missing", result["blockers"])

    def test_failed_rolling_windows_block_otherwise_valid_evidence(self):
        one_year, total_return, rolling = valid_evidence()
        rolling["promotionPassed"] = False
        rolling["windows"][0]["passed"] = False
        rolling["blockers"] = ["one_or_more_rolling_windows_failed"]
        result = evaluate(one_year, total_return, rolling)
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("one_or_more_rolling_windows_failed", result["blockers"])

    def test_two_year_history_produces_multiple_rolling_windows(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            history_path = root / "history.jsonl"
            benchmark_path = root / "benchmark.json"
            start = date(2024, 1, 2)
            rows = []
            benchmark = []
            price = 20.0
            for offset in range(420):
                day = start + timedelta(days=offset)
                price *= 1.002
                rows.append(json.dumps({"date": day.isoformat(), "rows": [["2330", price, 1_000_000]]}))
                benchmark.append({"date": day.isoformat(), "total_return": 100.0})
            history_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")
            result = evaluate_walk_forward(history_path, benchmark_path)
        self.assertGreaterEqual(len(result["windows"]), 3)
        self.assertNotIn("fewer_than_three_rolling_windows", result["blockers"])
        self.assertIn("daily_mdd_limit_unconfigured", result["blockers"])


if __name__ == "__main__":
    unittest.main()
