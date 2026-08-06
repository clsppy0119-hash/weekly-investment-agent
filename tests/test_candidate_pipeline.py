import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import finmind_actions
import finmind_fundamentals


ROOT = Path(__file__).resolve().parent.parent


class CandidatePipelineTests(unittest.TestCase):
    def _write_fixture(self, root: Path, gate: dict) -> None:
        quote = {
            "updatedAt": "2026-08-06T08:00:00+08:00",
            "quotes": {
                "2330": {
                    "name": "台積電",
                    "price": 1000.0,
                    "volume": 100,
                    "change": 1.0,
                    "ma5": 990.0,
                    "ma20": 950.0,
                }
            },
            "fundamentals": {
                "2330": {
                    "revenueYoY": 15.0,
                    "eps": 40.0,
                    "roe": 25.0,
                    "debtRatio": 20.0,
                    "pe": 20.0,
                    "pb": 4.0,
                    "dividendYield": 2.5,
                    "financialHistoryYears": 5,
                }
            },
            "history": {},
        }
        (root / "quotes.json").write_text(json.dumps(quote, ensure_ascii=False), encoding="utf-8")
        (root / "data").mkdir()
        (root / "data" / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
        (root / "market-news.json").write_text(
            json.dumps({"updatedAt": "2026-08-06T00:00:00Z", "items": []}),
            encoding="utf-8",
        )
        (root / "backtest_data").mkdir()
        (root / "backtest_data" / "candidate_actions.json").write_text(
            json.dumps({"queried_codes": ["2330"], "failures": {}, "period": {"start": "2025-01-01", "end": "2026-08-06"}}),
            encoding="utf-8",
        )

    def _run_report(self, root: Path, phase: str) -> None:
        env = os.environ.copy()
        env.update(
            {
                "REPORT_PHASE": phase,
                "REPORT_MODE": "comprehensive",
                "REPORT_OUTPUT": str(root / f"{phase}.txt"),
                "CANDIDATE_MANIFEST": str(root / "data" / "candidate-manifest.json"),
                "ADVICE_GATE_PATH": str(root / "data" / "gate.json"),
                "CANDIDATE_ACTIONS": str(root / "backtest_data" / "candidate_actions.json"),
                "MARKET_NEWS": str(root / "market-news.json"),
            }
        )
        subprocess.run(
            [sys.executable, str(ROOT / "daily_report.py")],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_preview_never_writes_tracker_and_research_only_has_no_eligible_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_fixture(root, {"status": "research_only", "adviceEnabled": False, "blockers": ["test"]})

            self._run_report(root, "preview")

            manifest = json.loads((root / "data" / "candidate-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["candidateOrder"], ["2330"])
            self.assertEqual(manifest["eligibleCandidates"], [])
            self.assertFalse((root / "strategy_data" / "recommendations.json").exists())

    def test_final_writes_one_quality_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_fixture(root, {"status": "research_only", "adviceEnabled": False})
            self._run_report(root, "preview")
            (root / "data" / "gate.json").write_text(
                json.dumps({"status": "advice_candidate", "adviceEnabled": True, "blockers": []}),
                encoding="utf-8",
            )

            self._run_report(root, "final")

            tracker = json.loads((root / "strategy_data" / "recommendations.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "data" / "candidate-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(tracker["recommendations"]), 1)
            self.assertEqual([item["code"] for item in manifest["eligibleCandidates"]], ["2330"])
            self.assertFalse(list(root.rglob("*.tmp")))

    def test_final_does_not_record_candidate_without_verified_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._write_fixture(root, {"status": "advice_candidate", "adviceEnabled": True, "blockers": []})
            (root / "backtest_data" / "candidate_actions.json").write_text(
                json.dumps({"queried_codes": [], "failures": {}}),
                encoding="utf-8",
            )

            self._run_report(root, "final")

            tracker = json.loads((root / "strategy_data" / "recommendations.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "data" / "candidate-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(tracker["recommendations"], [])
            self.assertIn("corporate_actions_not_verified", manifest["previewCandidates"][0]["quality"]["blockers"])
            self.assertEqual(manifest["eligibleCandidates"], [])

    def test_enrichment_and_actions_prefer_manifest_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"previewCandidates": [{"code": "2330"}]}), encoding="utf-8")
            tracker = root / "missing.json"
            self.assertEqual(finmind_fundamentals.candidate_codes(tracker, "", manifest), ["2330"])
            self.assertEqual(finmind_actions.active_codes(tracker, manifest), ["2330"])


if __name__ == "__main__":
    unittest.main()
