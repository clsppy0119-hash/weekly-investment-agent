import unittest
import json

from investment_advice_gate import evaluate
from backtest import benchmark_total_return
from walk_forward import evaluate as evaluate_walk_forward
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory


class InvestmentAdviceGateTests(unittest.TestCase):
    def test_official_benchmark_loader_accepts_utf8_bom_and_costs(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tai50.json"
            path.write_text('[{"date":"2026-01-01","total_return":100.0},{"date":"2026-01-02","total_return":110.0}]', encoding="utf-8-sig")
            value = benchmark_total_return(path, ["2026-01-01", "2026-01-02"])
        self.assertIsNotNone(value)
        self.assertLess(value, 0.10)

    def test_failed_test_set_blocks_advice(self):
        result = evaluate(
            {"decision": "rejected", "benchmark": {"total_return": False}},
            {"status": "research_only", "promotionBlocked": True, "promotionBlockers": ["survivorship_bias"]},
        )
        self.assertFalse(result["adviceEnabled"])
        self.assertIn("one_year_out_of_sample_failed", result["blockers"])
        self.assertIn("benchmark_not_total_return", result["blockers"])
        self.assertEqual(len(result["blockerDetails"]), len(result["blockers"]))
        self.assertTrue(all(item["message"] for item in result["blockerDetails"]))

    def test_only_fully_validated_evidence_enables_advice(self):
        result = evaluate(
            {"decision": "candidate", "benchmark": {"total_return": True}},
            {"status": "candidate", "promotionBlocked": False, "promotionBlockers": []},
        )
        self.assertTrue(result["adviceEnabled"])

    def test_failed_rolling_windows_block_otherwise_valid_evidence(self):
        result = evaluate(
            {"decision": "candidate", "benchmark": {"total_return": True}},
            {"status": "candidate", "promotionBlocked": False, "promotionBlockers": []},
            {"promotionPassed": False, "blockers": ["one_or_more_rolling_windows_failed"]},
        )
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


if __name__ == "__main__":
    unittest.main()
