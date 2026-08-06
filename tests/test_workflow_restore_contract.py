import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowRestoreContractTests(unittest.TestCase):
    def test_shared_restore_contract_matches_previous_inputs(self):
        action = (ROOT / ".github/actions/restore-backtest-inputs/action.yml").read_text(encoding="utf-8")
        self.assertIn("path: .private-data-cache", action)
        self.assertIn("key: backtest-data-v2-${{ runner.os }}-latest", action)
        self.assertIn("restore-keys: backtest-data-v2-${{ runner.os }}-", action)
        self.assertIn('default: "false"', action)
        self.assertIn("lookup-only: ${{ inputs.lookup-only }}", action)
        self.assertIn("supabase_data_restore.py --output-dir .private-data-cache/finmind-backtest-v2/stocks", action)
        self.assertIn("if ! find .private-data-cache/finmind-backtest-v2/stocks", action)

    def test_both_backtests_use_shared_restore_but_keep_independent_gates_and_artifacts(self):
        one_year = (ROOT / ".github/workflows/one-year-backtest.yml").read_text(encoding="utf-8")
        total_return = (ROOT / ".github/workflows/total-return-backtest.yml").read_text(encoding="utf-8")
        for workflow in (one_year, total_return):
            self.assertEqual(workflow.count("uses: ./.github/actions/restore-backtest-inputs"), 1)
            self.assertIn("python corporate_action_audit.py", workflow)
            self.assertIn("python total_return_backtest.py", workflow)
        self.assertIn("python investment_advice_gate.py", one_year)
        self.assertIn("python walk_forward.py", one_year)
        self.assertIn("name: one-year-twse-backtest", one_year)
        self.assertIn("python fixed_universe_backtest.py", total_return)
        self.assertIn("name: total-return-backtest", total_return)


if __name__ == "__main__":
    unittest.main()
