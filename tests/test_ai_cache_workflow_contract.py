import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class AiCacheWorkflowContractTests(unittest.TestCase):
    def test_both_reports_restore_content_addressed_cache_and_save_only_new_verified_entries(self):
        for relative in ("daily-report.yml", "long-research.yml"):
            workflow = (ROOT / ".github/workflows" / relative).read_text(encoding="utf-8")
            self.assertIn("path: .private-data-cache/ai-review-v1", workflow)
            self.assertIn("restore-keys: ai-review-v1-${{ runner.os }}-", workflow)
            self.assertIn("payload.get(\"cacheStatus\") == \"stored\"", workflow)
            self.assertIn("if: steps.ai-cache-status.outputs.save == 'true'", workflow)
            self.assertEqual(workflow.count("uses: actions/cache/save@v4"), 2)

    def test_cache_path_remains_private_and_report_notifications_remain_separate(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".private-data-cache/", gitignore)
        for relative in ("daily-report.yml", "long-research.yml"):
            workflow = (ROOT / ".github/workflows" / relative).read_text(encoding="utf-8")
            self.assertLess(workflow.index("Save newly verified AI cache entry"), workflow.index("Push to Telegram"))


if __name__ == "__main__":
    unittest.main()
