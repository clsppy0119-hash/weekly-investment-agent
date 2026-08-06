import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from investment_agent import cloud_report


def manifest(*, status="advice_candidate", enabled=True, news="news-a"):
    candidate = {"code": "2330", "quality": {"passed": True, "blockers": []}}
    return {
        "schemaVersion": 1,
        "phase": "final",
        "reportMode": "comprehensive",
        "strategyVersion": "2.0",
        "candidateOrder": ["2330"],
        "eligibleCandidates": [candidate],
        "adviceGate": {"status": status, "adviceEnabled": enabled},
        "evidenceInputs": {"newsSha256": news, "corporateActionsSha256": "actions-a"},
    }


class CloudReportCacheTests(unittest.TestCase):
    def test_usage_status_distinguishes_zero_call_hit_and_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            calls = {"count": 0}

            async def run_team(codes, evidence):
                calls["count"] += 1
                return {"output": "review", "cacheable": True, "runner_invocations": len(codes) + 1}

            components = (lambda codes: [{"stock_code": code} for code in codes], lambda: {"model": "test-model"}, run_team)
            with patch.object(cloud_report, "load_market_data", return_value={"quotes": {"2330": {}}}), patch.object(cloud_report, "_research_components", return_value=components):
                _, generated = asyncio.run(cloud_report._build_with_status("comprehensive", path, 3, root / "cache"))
                _, hit = asyncio.run(cloud_report._build_with_status("comprehensive", path, 3, root / "cache"))
            self.assertEqual(generated["runnerInvocations"], 2)
            self.assertEqual(generated["cacheStatus"], "stored")
            self.assertEqual(hit["runnerInvocations"], 0)
            self.assertEqual(hit["avoidedRunnerInvocations"], 2)
            self.assertEqual(calls["count"], 1)

    def test_research_only_usage_status_is_zero_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest(status="research_only", enabled=False)), encoding="utf-8")
            with patch.object(cloud_report, "load_market_data", return_value={"quotes": {"2330": {}}}), patch.object(cloud_report, "_research_components", side_effect=AssertionError("must not load")):
                _, status = asyncio.run(cloud_report._build_with_status("comprehensive", path, 3, root / "cache"))
            self.assertEqual(status["runnerInvocations"], 0)
            self.assertEqual(status["outcome"], "no_eligible_candidates")

    def test_hash_invalidates_for_every_declared_contract_input(self):
        base_manifest = manifest()
        base_packets = [{"stock_code": "2330", "quote_updated_at": "a"}]
        base_contract = {"model": "m1", "riskPromptSha256": "r1", "reportPromptSha256": "p1"}
        baseline = cloud_report._evidence_hash(base_manifest, base_packets, base_contract)
        mutations = []
        changed = manifest()
        changed["strategyVersion"] = "2.1"
        mutations.append((changed, base_packets, base_contract))
        changed = manifest()
        changed["adviceGate"]["blockers"] = ["new-gate"]
        mutations.append((changed, base_packets, base_contract))
        changed = manifest()
        changed["evidenceInputs"]["corporateActionsSha256"] = "actions-b"
        mutations.append((changed, base_packets, base_contract))
        mutations.append((base_manifest, [{"stock_code": "2330", "quote_updated_at": "b"}], base_contract))
        mutations.append((base_manifest, base_packets, {**base_contract, "model": "m2"}))
        mutations.append((base_manifest, base_packets, {**base_contract, "riskPromptSha256": "r2"}))
        for changed_manifest, changed_packets, changed_contract in mutations:
            self.assertNotEqual(
                baseline,
                cloud_report._evidence_hash(changed_manifest, changed_packets, changed_contract),
            )

    def test_research_only_short_circuits_before_research_components(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest(status="research_only", enabled=False)), encoding="utf-8")
            with patch.object(cloud_report, "load_market_data", return_value={"quotes": {"2330": {}}}), patch.object(
                cloud_report, "_research_components", side_effect=AssertionError("LLM path must not load")
            ):
                output = asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))
            self.assertEqual(output, cloud_report.NO_ELIGIBLE_CANDIDATES)

    def test_identical_evidence_reuses_cache_and_changed_news_invalidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            calls = {"count": 0}

            def packets(codes):
                return [{"stock_code": code, "quote_updated_at": "2026-08-06"} for code in codes]

            def contract():
                return {"model": "test-model", "prompt": "prompt-a", "gate": "gate-a"}

            async def run_team(codes, evidence):
                calls["count"] += 1
                return {"output": f"review-{calls['count']}", "cacheable": True}

            components = (packets, contract, run_team)
            with patch.object(cloud_report, "load_market_data", return_value={"quotes": {"2330": {}}}), patch.object(
                cloud_report, "_research_components", return_value=components
            ):
                first = asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))
                second = asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))
                path.write_text(json.dumps(manifest(news="news-b")), encoding="utf-8")
                third = asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))

            self.assertEqual((first, second, third), ("review-1", "review-1", "review-2"))
            self.assertEqual(calls["count"], 2)

    def test_failed_ai_result_is_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            calls = {"count": 0}

            async def run_team(_codes, _evidence):
                calls["count"] += 1
                return {"output": "fallback", "cacheable": False}

            components = (lambda codes: [{"stock_code": code} for code in codes], lambda: {"model": "x"}, run_team)
            with patch.object(cloud_report, "load_market_data", return_value={"quotes": {"2330": {}}}), patch.object(
                cloud_report, "_research_components", return_value=components
            ):
                asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))
                asyncio.run(cloud_report._build("comprehensive", path, 3, root / "cache"))
            self.assertEqual(calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
