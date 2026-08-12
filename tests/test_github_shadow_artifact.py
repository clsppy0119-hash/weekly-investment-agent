import io
import json
import os
import unittest
import zipfile
from email.message import Message
from urllib.error import HTTPError

import github_shadow_artifact as g


def archive(contract=None, manifest=None):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as bundle:
        bundle.writestr("data/evidence-contract.json", json.dumps(contract or {}))
        bundle.writestr("data/candidate-manifest.json", json.dumps(manifest or {}))
    return content.getvalue()


class FakeClient:
    def __init__(self, blob, *, run=None, artifacts=None, error=None):
        self.blob = blob
        self.calls = []
        self.error = error
        run_id = 123
        self.run = run or {
            "id": run_id,
            "repository": {"full_name": g.REPOSITORY},
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
        }
        self.artifacts = artifacts or [
            {
                "id": 456,
                "name": g.ARTIFACT_NAME,
                "expired": False,
                "size_in_bytes": len(blob),
                "workflow_run": {"id": run_id},
            }
        ]

    def get_json(self, path):
        self.calls.append(("get_json", path))
        if self.error:
            raise self.error
        return {"artifacts": self.artifacts} if path.endswith("artifacts?per_page=100") else self.run

    def download_artifact(self, artifact_id, limit):
        self.calls.append(("download", artifact_id, limit))
        if self.error:
            raise self.error
        return self.blob


class FakeResponse:
    def __init__(self, content):
        self.content = io.BytesIO(content)
        self.headers = {"Content-Length": str(len(content))}
        self.status = 200
        self.closed = False

    def read(self, size=-1):
        return self.content.read(size)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class GitHubShadowArtifactTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        for flag in (
            g.FLAG,
            "ARTIFACT_SHADOW_EXTRACT_ENABLED",
            "SHADOW_PIPELINE_ENABLED",
            "LINEAGE_FREEZE_ENABLED",
        ):
            os.environ[flag] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def test_default_off_performs_zero_client_calls(self):
        os.environ.pop(g.FLAG)
        client = FakeClient(archive())
        result = g.run(run_id=123, decision_as_of="2026-08-12T08:00:00+08:00", client=client)
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(client.calls, [])

    def test_controlled_fetch_feeds_offline_pipeline_and_stays_research_only(self):
        client = FakeClient(archive())
        result = g.run(run_id=123, decision_as_of="2026-08-12T08:00:00+08:00", client=client)
        self.assertEqual(result["mode"], "research_only")
        self.assertEqual(result["pipelineMode"], "research_only")
        self.assertEqual(result["runId"], 123)
        self.assertEqual(result["artifactId"], 456)
        self.assertEqual(len(result["archiveSha256"]), 64)
        self.assertEqual([call[0] for call in client.calls], ["get_json", "get_json", "download"])

    def test_invalid_inputs_do_not_touch_client(self):
        client = FakeClient(archive())
        self.assertEqual(g.run(run_id=0, decision_as_of="2026-08-12T08:00:00+08:00", client=client)["mode"], "research_only")
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12", client=client)["mode"], "research_only")
        self.assertEqual(client.calls, [])

    def test_run_repository_and_artifact_are_exact_allowlists(self):
        bad_run = FakeClient(archive(), run={
            "id": 123,
            "repository": {"full_name": "someone/other"},
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
        })
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12T00:00:00Z", client=bad_run)["blockers"], ["run_not_allowlisted"])
        duplicate = FakeClient(archive())
        duplicate.artifacts.append(dict(duplicate.artifacts[0], id=789))
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12T00:00:00Z", client=duplicate)["blockers"], ["artifact_not_unique"])

    def test_expired_oversize_and_truncated_artifacts_fail_closed(self):
        blob = archive()
        expired = FakeClient(blob)
        expired.artifacts[0]["expired"] = True
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12T00:00:00Z", client=expired)["mode"], "research_only")

        oversize = FakeClient(blob)
        oversize.artifacts[0]["size_in_bytes"] = g.MAX_ARCHIVE_BYTES + 1
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12T00:00:00Z", client=oversize)["mode"], "research_only")

        truncated = FakeClient(blob)
        truncated.artifacts[0]["size_in_bytes"] += 1
        self.assertEqual(g.run(run_id=123, decision_as_of="2026-08-12T00:00:00Z", client=truncated)["blockers"], ["artifact_size_mismatch"])

    def test_external_errors_are_sanitized_and_fail_closed(self):
        token = "token-never-print-this"
        result = g.run(
            run_id=123,
            decision_as_of="2026-08-12T00:00:00Z",
            client=FakeClient(archive(), error=RuntimeError(f"network {token} https://bad.invalid/raw")),
        )
        rendered = json.dumps(result)
        self.assertEqual(result["mode"], "research_only")
        self.assertNotIn(token, rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("raw", rendered)

    def test_api_and_redirect_boundaries(self):
        self.assertEqual(
            g._api_url(f"/repos/{g.REPOSITORY}/actions/runs/123"),
            f"https://api.github.com/repos/{g.REPOSITORY}/actions/runs/123",
        )
        with self.assertRaises(g.FetchError):
            g._api_url("/repos/other/repo/actions/runs/123")
        self.assertTrue(g._download_url("https://safe.blob.core.windows.net/actions-results/a.zip?sig=x"))
        self.assertTrue(g._download_url("https://objects.githubusercontent.com/a.zip?sig=x"))
        for bad in (
            "http://safe.blob.core.windows.net/a.zip",
            "https://blob.core.windows.net/a.zip",
            "https://api.github.com/repos/a.zip",
            "https://user:secret@objects.githubusercontent.com/a.zip",
        ):
            with self.assertRaises(g.FetchError):
                g._download_url(bad)

    def test_authorization_is_not_forwarded_to_download_host(self):
        headers = Message()
        headers["Location"] = "https://safe.blob.core.windows.net/actions-results/a.zip?sig=x"
        redirect = HTTPError("https://api.github.com/", 302, "Found", headers, None)
        response = FakeResponse(b"zip")
        client = g.GitHubArtifactClient("secret-token")
        client._api_opener = FakeOpener(redirect)
        client._download_opener = FakeOpener(response)
        self.assertEqual(client.download_artifact(456, 20), b"zip")
        self.assertIn("Bearer secret-token", client._api_opener.requests[0].headers.values())
        self.assertNotIn("Authorization", client._download_opener.requests[0].headers)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
