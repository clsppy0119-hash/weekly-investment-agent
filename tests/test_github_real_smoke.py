import io
import hashlib
import json
import os
import unittest
import zipfile

import github_real_smoke as smoke


def archive():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as bundle:
        bundle.writestr("data/evidence-contract.json", "{}")
        bundle.writestr("data/candidate-manifest.json", "{}")
        bundle.writestr("daily-report.md", "safe extra")
    return content.getvalue()


class FakeClient:
    def __init__(self, blob, *, head_sha=smoke.PINNED_HEAD_SHA, artifact_id=smoke.PINNED_ARTIFACT_ID, size=None):
        self.blob = blob
        self.head_sha = head_sha
        self.artifact_id = artifact_id
        self.size = len(blob) if size is None else size
        self.calls = []

    def get_json(self, path):
        self.calls.append(("get", path))
        if path.endswith("artifacts?per_page=100"):
            return {"artifacts": [{
                "id": self.artifact_id,
                "name": "daily-investment-report",
                "expired": False,
                "size_in_bytes": self.size,
                "workflow_run": {"id": smoke.PINNED_RUN_ID},
            }]}
        return {
            "id": smoke.PINNED_RUN_ID,
            "head_sha": self.head_sha,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": "clsppy0119-hash/weekly-investment-agent"},
        }

    def download_artifact(self, artifact_id, limit):
        self.calls.append(("download", artifact_id, limit))
        return self.blob


class RealSmokeTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        self.old_size = smoke.PINNED_ARTIFACT_SIZE
        self.old_hash = smoke.PINNED_ARCHIVE_SHA256
        os.environ[smoke.FLAG] = "true"

    def tearDown(self):
        smoke.PINNED_ARTIFACT_SIZE = self.old_size
        smoke.PINNED_ARCHIVE_SHA256 = self.old_hash
        os.environ.clear()
        os.environ.update(self.old)

    def test_default_off_is_zero_call(self):
        os.environ.pop(smoke.FLAG)
        client = FakeClient(archive())
        self.assertEqual(smoke.run(client=client)["mode"], "disabled")
        self.assertEqual(client.calls, [])

    def test_pinned_smoke_is_sanitized_research_only_and_restores_flags(self):
        blob = archive()
        smoke.PINNED_ARTIFACT_SIZE = len(blob)
        smoke.PINNED_ARCHIVE_SHA256 = hashlib.sha256(blob).hexdigest()
        before = {name: os.environ.get(name) for name in smoke.SCOPED_FLAGS}
        result = smoke.run(client=FakeClient(blob))
        self.assertEqual(result["mode"], "research_only")
        self.assertEqual(result["pipelineMode"], "research_only")
        self.assertEqual(result["artifactId"], smoke.PINNED_ARTIFACT_ID)
        self.assertEqual({name: os.environ.get(name) for name in smoke.SCOPED_FLAGS}, before)
        rendered = json.dumps(result)
        for forbidden in ("GITHUB_TOKEN", "https://", "contract", "manifest", "raw"):
            self.assertNotIn(forbidden, rendered)

    def test_pin_mismatches_fail_closed_before_download(self):
        blob = archive()
        smoke.PINNED_ARTIFACT_SIZE = len(blob)
        smoke.PINNED_ARCHIVE_SHA256 = hashlib.sha256(blob).hexdigest()
        for client, blocker in (
            (FakeClient(blob, head_sha="0" * 40), "pinned_head_sha_mismatch"),
            (FakeClient(blob, artifact_id=999), "pinned_artifact_metadata_mismatch"),
            (FakeClient(blob, size=len(blob) + 1), "pinned_artifact_metadata_mismatch"),
        ):
            with self.subTest(blocker=blocker):
                result = smoke.run(client=client)
                self.assertEqual(result["mode"], "research_only")
                self.assertEqual(result["blockers"], [blocker])
                self.assertNotIn("download", [call[0] for call in client.calls])


if __name__ == "__main__":
    unittest.main()
