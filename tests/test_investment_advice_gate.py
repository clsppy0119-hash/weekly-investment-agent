import unittest

from investment_advice_gate import evaluate
from backtest import benchmark_total_return
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

    def test_only_fully_validated_evidence_enables_advice(self):
        result = evaluate(
            {"decision": "candidate", "benchmark": {"total_return": True}},
            {"status": "candidate", "promotionBlocked": False, "promotionBlockers": []},
        )
        self.assertTrue(result["adviceEnabled"])


if __name__ == "__main__":
    unittest.main()
