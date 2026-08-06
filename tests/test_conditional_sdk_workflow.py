import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ConditionalSdkWorkflowTests(unittest.TestCase):
    def test_both_report_workflows_gate_sdk_install_on_manifest(self):
        for name in ("daily-report.yml", "long-research.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("--eligibility-only >> \"$GITHUB_OUTPUT\"", workflow)
            self.assertIn("if: steps.ai-eligibility.outputs.needs_ai == 'true'", workflow)
            self.assertIn("python investment_agent/cloud_report.py --mode comprehensive", workflow)
            self.assertLess(workflow.index("Check deterministic manifest eligibility"), workflow.index("Install AI agent only for eligible candidates"))
            self.assertLess(workflow.index("Install AI agent only for eligible candidates"), workflow.index("Review comprehensive candidates with AI"))


if __name__ == "__main__":
    unittest.main()
