import copy
import inspect
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

import decision_outcome_event_contract as event_contract
import decision_outcome_manifest as manifest_contract
import decision_outcome_sandbox as sandbox


H = "a" * 64
SCOPE = "b" * 64


def decision(day="2026-08-12", code="2330", previous=event_contract.GENESIS):
    return event_contract.decision_candidate({
        "decisionAsOf": day, "code": code, "mode": "comprehensive",
        "style": "comprehensive", "rank": 1, "strategyVersion": "2.0",
        "score": 88.5, "coverage": 100, "entryPrice": 1200.0,
        "candidateManifestHash": H, "evidenceHash": H, "reportHash": H,
        "eligiblePoolHash": H, "quoteProvenanceHash": H,
        "fundamentalProvenanceHash": H, "dataQuality": "qualified",
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "claimedPreviousChainHead": previous, "researchOnly": True,
    }, enabled=True)


def outcome(parent, previous=event_contract.GENESIS):
    return event_contract.outcome_candidate({
        "decisionEventHash": parent, "horizon": 5, "settledDate": "2026-08-20",
        "netReturnPct": 5.0, "totalReturnNetPct": 5.5,
        "benchmarkNetReturnPct": 2.0, "poolNetReturnPct": 3.0,
        "excessReturnPct": 3.5, "poolExcessPct": 2.0,
        "costModelVersion": event_contract.COST_MODEL_VERSION,
        "corporateActionEvidenceHash": H, "priceSnapshotHash": H,
        "benchmarkArtifactHash": H, "poolArtifactHash": H,
        "pitCoverage": 100, "sourceStatus": "qualified",
        "claimedPreviousOutcomeHash": previous, "researchOnly": True,
    }, enabled=True)


def snapshot(extra=False):
    first = decision()
    events = [first, outcome(first["eventHash"])]
    if extra:
        second = decision(day="2026-08-13", code="2303", previous=first["eventHash"])
        events.append(second)
    manifest = manifest_contract.freeze_manifest(events, SCOPE, enabled=True)
    return events, manifest


class SandboxLedgerTests(unittest.TestCase):
    def test_default_off_has_zero_io(self):
        missing = Path(tempfile.gettempdir()) / "must-not-exist-outcome-sandbox"
        result = sandbox.write([], {}, missing)
        self.assertFalse(result["sandboxChainVerified"])
        self.assertFalse(missing.exists())

    def test_write_replay_duplicate_and_bounded_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, manifest = snapshot()
            written = sandbox.write(events, manifest, Path(tmp), enabled=True)
            replayed = sandbox.replay(Path(tmp), SCOPE, enabled=True)
            duplicate = sandbox.write(list(reversed(events)), manifest, Path(tmp), enabled=True)
            self.assertEqual(written["status"], "written")
            self.assertTrue(replayed["sandboxChainVerified"])
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertFalse(written["completenessExternallyAnchored"])
            self.assertFalse(written["promotionEligible"])
            self.assertLess(len(json.dumps(written)), 2048)

    def test_append_preserves_prior_set_and_replays_whole_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_events, first_manifest = snapshot()
            second_events, second_manifest = snapshot(extra=True)
            self.assertEqual(sandbox.write(first_events, first_manifest, Path(tmp), enabled=True)["status"], "written")
            result = sandbox.write(second_events, second_manifest, Path(tmp), enabled=True)
            self.assertEqual(result["sequence"], 2)
            self.assertTrue(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])

    def test_regression_tamper_missing_and_pollution_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_events, first_manifest = snapshot()
            second_events, second_manifest = snapshot(extra=True)
            sandbox.write(second_events, second_manifest, Path(tmp), enabled=True)
            self.assertIn("event_set_regression", sandbox.write(first_events, first_manifest, Path(tmp), enabled=True)["blockers"])
            event_path = Path(tmp) / "event_blobs" / f"{second_events[0]['eventHash']}.json"
            event_path.write_text("{}", encoding="utf-8")
            self.assertFalse(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])
        with tempfile.TemporaryDirectory() as tmp:
            events, manifest = snapshot(); sandbox.write(events, manifest, Path(tmp), enabled=True)
            (Path(tmp) / "anchors" / SCOPE / "junk").write_text("x", encoding="utf-8")
            self.assertFalse(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])

    def test_crash_before_anchor_is_retryable_and_after_anchor_is_committed(self):
        for phase, expected in (("manifest_verified", "written"), ("anchor_published", "duplicate")):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                events, manifest = snapshot()
                def crash(current):
                    if current == phase:
                        raise RuntimeError("simulated")
                blocked = sandbox.write(events, manifest, Path(tmp), enabled=True, phase_hook=crash)
                self.assertFalse(blocked["sandboxChainVerified"])
                retry = sandbox.write(events, manifest, Path(tmp), enabled=True)
                self.assertEqual(retry["status"], expected)

    def test_concurrent_different_snapshots_only_one_sequence_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_events, base_manifest = snapshot()
            sandbox.write(base_events, base_manifest, Path(tmp), enabled=True)
            first_events, first_manifest = snapshot(extra=True)
            # A distinct valid superset with a different third decision.
            other = decision(day="2026-08-13", code="2317", previous=base_events[0]["eventHash"])
            second_events = base_events + [other]
            second_manifest = manifest_contract.freeze_manifest(second_events, SCOPE, enabled=True)
            barrier = threading.Barrier(2)
            results = []
            def run(events, manifest):
                results.append(sandbox.write(events, manifest, Path(tmp), enabled=True,
                    phase_hook=lambda phase: barrier.wait(timeout=3) if phase == "before_anchor" else None))
            threads = [threading.Thread(target=run, args=(first_events, first_manifest)),
                       threading.Thread(target=run, args=(second_events, second_manifest))]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(sum(item["status"] == "written" for item in results), 1)
            self.assertEqual(sum("concurrency_conflict" in item["blockers"] for item in results), 1)
            self.assertTrue(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])

    def test_manifest_event_chain_and_legacy_policy_are_revalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, manifest = snapshot()
            tampered = copy.deepcopy(events); tampered[0]["payload"]["score"] = 99
            self.assertIn("candidate_set_not_verified", sandbox.write(tampered, manifest, Path(tmp), enabled=True)["blockers"])
            legacy1 = event_contract.legacy_candidate({"legacySourceHash": H, "recordCount": 1,
                "reason": "mutable_state_without_original_event_chain", "researchOnly": True}, enabled=True)
            legacy2 = event_contract.legacy_candidate({"legacySourceHash": "c" * 64, "recordCount": 1,
                "reason": "mutable_state_without_original_event_chain", "researchOnly": True}, enabled=True)
            with self.assertRaisesRegex(ValueError, "multiple_legacy"):
                manifest_contract.freeze_manifest(events + [legacy1, legacy2], SCOPE, enabled=True)

    def test_relative_root_symlink_and_collision_fail_closed(self):
        events, manifest = snapshot()
        self.assertIn("sandbox_root_not_absolute", sandbox.write(events, manifest, "relative", enabled=True)["blockers"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root / "event_blobs"; directory.mkdir()
            target = directory / f"{events[0]['eventHash']}.json"
            target.write_text("different", encoding="utf-8")
            self.assertIn("sandbox_file_collision", sandbox.write(events, manifest, root, enabled=True)["blockers"])
        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
                link = Path(tmp) / "event_blobs"
                try:
                    os.symlink(outside, link, target_is_directory=True)
                except OSError:
                    return
                self.assertFalse(sandbox.write(events, manifest, Path(tmp), enabled=True)["sandboxChainVerified"])

    def test_anchor_sequence_gap_and_snapshot_deletion_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, manifest = snapshot(); sandbox.write(events, manifest, Path(tmp), enabled=True)
            anchor_dir = Path(tmp) / "anchors" / SCOPE
            (anchor_dir / "00000000000000000003.json").write_text("{}", encoding="utf-8")
            self.assertFalse(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])
        with tempfile.TemporaryDirectory() as tmp:
            events, manifest = snapshot(); sandbox.write(events, manifest, Path(tmp), enabled=True)
            os.remove(Path(tmp) / "manifests" / f"{manifest['manifestDigest']}.json")
            self.assertFalse(sandbox.replay(Path(tmp), SCOPE, enabled=True)["sandboxChainVerified"])

    def test_module_is_not_a_formal_flow_and_never_overwrites_or_deletes(self):
        source = inspect.getsource(sandbox).lower()
        for forbidden in ("requests", "urllib", "socket", "subprocess", "psycopg",
                          "supabase", "strategy_tracker", "promotion_status",
                          "investment_advice", "telegram", "os.replace", "unlink(",
                          "remove(", "rmtree", "write_text(", "write_bytes("):
            self.assertNotIn(forbidden, source)
        self.assertIn("os.o_excl", source)


if __name__ == "__main__":
    unittest.main()
