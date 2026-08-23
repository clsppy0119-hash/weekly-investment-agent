from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import model_advice
import paper_trading as paper
from provenance import schema_hash, stable_hash
from tests.test_paper_trading import (
    TAIPEI,
    action_payload,
    manifest_for,
    quote_payload,
    sessions,
)


class ModelAdviceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.manifest = self.root / "manifest.json"
        self.quotes = self.root / "quotes.json"
        self.actions = self.root / "actions.json"
        self.ledger = self.root / "ledger.json"
        self.progress = self.root / "progress.json"
        self.output = self.root / "model-advice.txt"

    def tearDown(self):
        self.folder.cleanup()

    def advance(self, session: str, step: int, *, failed_slot: int | None = None) -> dict:
        payload = quote_payload(session, step)
        manifest = manifest_for(payload, session)
        if failed_slot is not None:
            manifest["previewCandidates"][failed_slot - 1]["quality"] = {
                "passed": False,
                "blockers": ["synthetic_quality_blocker"],
            }
        self.quotes.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self.actions.write_text(
            json.dumps(action_payload(session=session), ensure_ascii=False),
            encoding="utf-8",
        )
        now = datetime.combine(
            date.fromisoformat(session) + timedelta(days=1), datetime.min.time(), TAIPEI,
        ).replace(hour=8)
        return paper.advance(
            self.manifest, self.quotes, self.actions, self.ledger, self.progress, now=now,
        )

    def test_empty_valid_ledger_is_unvalidated(self):
        snapshot = model_advice.build_snapshot(self.ledger)
        text = model_advice.render_text(snapshot)
        self.assertEqual(snapshot["evidenceTier"], "unvalidated")
        self.assertFalse(snapshot["modelRecommendationAvailable"])
        self.assertEqual(snapshot["recommendations"], [])
        self.assertEqual(snapshot["cashSlots"], 3)
        self.assertIn("不顯示臨時排行榜", text)

    def test_latest_verified_signal_ignores_trailing_observations(self):
        days = sessions(22)
        for step, session in enumerate(days):
            self.advance(session, step)
        ledger = paper.load_ledger(self.ledger)
        decisions = [event for event in ledger["events"] if event["eventType"] == "signal_decision"]
        self.assertEqual(len(decisions), 2)
        self.assertEqual(ledger["events"][-1]["eventType"], "session_observation")

        snapshot = model_advice.build_snapshot(self.ledger)
        self.assertEqual(snapshot["decisionEventHash"], decisions[-1]["eventHash"])
        self.assertEqual(snapshot["signalSession"], decisions[-1]["payload"]["material"]["signalSession"])
        self.assertEqual(snapshot["status"], "maintain_signal")
        self.assertGreater(snapshot["latestObservationSession"], snapshot["signalSession"])

    def test_only_quality_passed_slots_are_recommended_without_rank_four_backfill(self):
        session = sessions(1)[0]
        self.advance(session, 0, failed_slot=2)
        ledger = paper.load_ledger(self.ledger)
        material = ledger["events"][1]["payload"]["material"]
        fourth = material["rankedPool"][3]["code"]

        snapshot = model_advice.build_snapshot(self.ledger)
        codes = [item["code"] for item in snapshot["recommendations"]]
        self.assertEqual(codes, [material["topSlots"][0]["code"], material["topSlots"][2]["code"]])
        self.assertNotIn(fourth, codes)
        self.assertEqual(snapshot["cashSlots"], 1)
        self.assertTrue(any(material["topSlots"][1]["code"] in item for item in snapshot["cashReasons"]))
        self.assertEqual(
            set(material["strategySourceHashes"]),
            {
                "scoring.py", "actual_comprehensive_selection.py", "candidate_manifest.py",
                "data_contract.py", "execution_accounting.py", "backtest.py", "paper_trading.py",
            },
        )

    def test_positive_completed_paper_observations_never_promote_the_evidence_tier(self):
        for step, session in enumerate(sessions(7)):
            progress = self.advance(session, step)
        self.assertEqual(
            progress["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]["status"],
            "complete",
        )
        snapshot = model_advice.build_snapshot(self.ledger)
        self.assertEqual(snapshot["evidenceTier"], "collecting")
        self.assertFalse(snapshot["formalEvidenceSupported"])
        self.assertFalse(snapshot["paperOutcomeUsedForTier"])
        self.assertFalse(snapshot["tradingInstruction"])

    def test_adverse_but_complete_financials_are_neutral_facts_with_risk_flags(self):
        session = sessions(1)[0]
        payload = quote_payload(session, 0)
        for row in payload["fundamentals"].values():
            row["revenueYoY"] = -3.0
            row["debtRatio"] = 60.0
        provenance = payload["provenance"]["fundamentals"]
        provenance["snapshotContentHash"] = stable_hash(payload["fundamentals"])
        provenance["snapshotSchemaHash"] = schema_hash(payload["fundamentals"])
        self.quotes.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.manifest.write_text(
            json.dumps(manifest_for(payload, session), ensure_ascii=False), encoding="utf-8",
        )
        self.actions.write_text(
            json.dumps(action_payload(session=session), ensure_ascii=False), encoding="utf-8",
        )
        paper.advance(
            self.manifest, self.quotes, self.actions, self.ledger, self.progress,
            now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
        )
        snapshot = model_advice.build_snapshot(self.ledger)
        text = model_advice.render_text(snapshot)
        self.assertTrue(snapshot["recommendations"])
        for item in snapshot["recommendations"]:
            self.assertIn("營收年增率為負", item["riskFlags"])
            self.assertIn("負債比達 50% 以上", item["riskFlags"])
        self.assertIn("訊號時資料", text)
        self.assertNotIn("支持資料", text)

    def test_report_uses_only_the_frozen_signal_snapshot(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        before = model_advice.build_snapshot(self.ledger)
        self.quotes.write_text('{"quotes":{"9999":{"price":999999}}}', encoding="utf-8")
        self.manifest.write_text('{"previewCandidates":[{"code":"9999"}]}', encoding="utf-8")
        after = model_advice.build_snapshot(self.ledger)
        self.assertEqual(before, after)
        self.assertNotIn("9999", model_advice.render_text(after))

    def test_corrupt_ledger_fails_closed_without_overwriting_an_existing_report(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        value = json.loads(self.ledger.read_text(encoding="utf-8"))
        value["events"][1]["payload"]["material"]["topSlots"][0]["code"] = "9999"
        self.ledger.write_text(json.dumps(value), encoding="utf-8")
        self.output.write_text("previous verified output\n", encoding="utf-8")
        with self.assertRaisesRegex(model_advice.ModelAdviceError, "paper_ledger_invalid"):
            model_advice.write_report(self.ledger, self.output)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous verified output\n")

    def test_rendered_advice_is_explicitly_tracked_and_not_formal(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        snapshot = model_advice.write_report(self.ledger, self.output)
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual(snapshot["status"], "new_signal")
        for item in snapshot["recommendations"]:
            self.assertIn(item["code"], text)
        self.assertIn("模型三槽內權重", text)
        self.assertIn("非個人總資產配置", text)
        self.assertIn("尚未證明有效", text)
        self.assertIn("這份是固定規則模型建議", text)
        self.assertIn("正式績效認證與自動交易未啟用", text)

    def test_workflow_sends_model_advice_only_after_ledger_persistence(self):
        project = Path(paper.__file__).resolve().parent
        workflow = (project / ".github" / "workflows" / "daily-report.yml").read_text(encoding="utf-8")
        safety = (project / ".github" / "workflows" / "pipeline-safety-validation.yml").read_text(encoding="utf-8")
        review = workflow.index("Review comprehensive candidates with AI")
        paper_advance = workflow.index("Advance prospective paper-only ledger")
        render = workflow.index("Render tracked model advice")
        persist = workflow.index("Require and persist prospective paper ledger")
        upload = workflow.index("Upload prospective paper artifact")
        notify = workflow.index("Push persisted model advice to Telegram")
        self.assertIn(
            "if: steps.ai-eligibility.outputs.needs_ai == 'true'",
            workflow[review:paper_advance],
        )
        self.assertLess(paper_advance, render)
        self.assertLess(render, persist)
        self.assertLess(persist, upload)
        self.assertLess(upload, notify)
        self.assertLess(persist, notify)
        self.assertIn("id: model_advice", workflow[render:persist])
        self.assertIn("id: paper_persist", workflow[persist:notify])
        self.assertIn("steps.paper_persist.outcome", workflow[notify:])
        self.assertIn("steps.model_advice.outcome", workflow[notify:])
        self.assertIn("model-advice.txt", workflow[render:])
        self.assertIn('      - "model_advice.py"', safety)
        self.assertIn("          model_advice.py", safety)

    def test_renderer_has_no_live_market_ai_gate_or_execution_inputs(self):
        source = Path(model_advice.__file__).read_text(encoding="utf-8").lower()
        for forbidden in (
            "quotes.json", "candidate-manifest", "investment_advice_gate",
            "cloud_report", "openai", "urllib", "requests", "broker",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
