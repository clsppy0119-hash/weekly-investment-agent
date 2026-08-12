"""Controlled, default-off GitHub artifact fetcher for the shadow pipeline.

The authenticated request is restricted to the allowlisted GitHub API
repository.  Artifact bytes are downloaded without the Authorization header
from a small allowlist of GitHub-managed HTTPS storage hosts, kept in memory,
and then handed to the offline D-4/D-3 validators.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import artifact_shadow_extract
import shadow_pipeline

FLAG = "GITHUB_SHADOW_FETCH_ENABLED"
OWNER = "clsppy0119-hash"
REPO = "weekly-investment-agent"
REPOSITORY = f"{OWNER}/{REPO}"
ARTIFACT_NAME = "daily-investment-report"
API_ORIGIN = "https://api.github.com"
API_PREFIX = f"/repos/{REPOSITORY}/"
MAX_ARCHIVE_BYTES = artifact_shadow_extract.MAX_ARCHIVE
MAX_JSON_BYTES = 500_000
CHUNK_BYTES = 64 * 1024
DOWNLOAD_HOST_SUFFIXES = (".blob.core.windows.net",)
DOWNLOAD_HOSTS = {"objects.githubusercontent.com"}


class FetchError(RuntimeError):
    """A deliberately non-detailed, fail-closed fetch error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


def _valid_decision_as_of(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except (AttributeError, TypeError, ValueError):
        return False


def _api_url(path: str) -> str:
    if not isinstance(path, str) or not path.startswith(API_PREFIX):
        raise FetchError("api_path_not_allowlisted")
    if "?" in path:
        clean_path, query = path.split("?", 1)
        if not query or "#" in query:
            raise FetchError("api_path_not_allowlisted")
    else:
        clean_path = path
    if any(part in {"", ".", ".."} for part in clean_path.split("/")[1:]):
        raise FetchError("api_path_not_allowlisted")
    return API_ORIGIN + path


def _download_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = host in DOWNLOAD_HOSTS or any(
        host.endswith(suffix) and host != suffix[1:] for suffix in DOWNLOAD_HOST_SUFFIXES
    )
    if (
        parsed.scheme != "https"
        or not allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise FetchError("download_host_not_allowlisted")
    return url


def _read_limited(response: Any, limit: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) < 0 or int(declared) > limit:
                raise FetchError("response_too_large")
        except ValueError as exc:
            raise FetchError("invalid_content_length") from exc
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = response.read(CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise FetchError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


class GitHubArtifactClient:
    """Minimal read-only GitHub API client with an explicit auth boundary."""

    def __init__(self, token: str | None):
        if not token:
            raise FetchError("github_token_unavailable")
        self._token = token
        self._api_opener = build_opener(_NoRedirect())
        self._download_opener = build_opener(_NoRedirect())

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "weekly-investment-agent-shadow-fetcher",
        }

    def get_json(self, path: str) -> dict[str, Any]:
        request = Request(_api_url(path), headers=self._headers(), method="GET")
        try:
            with self._api_opener.open(request, timeout=30) as response:
                if response.status != 200:
                    raise FetchError("github_api_failed")
                body = _read_limited(response, MAX_JSON_BYTES)
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError("github_api_failed") from exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError("github_api_invalid_json") from exc
        if not isinstance(value, dict):
            raise FetchError("github_api_invalid_json")
        return value

    def download_artifact(self, artifact_id: int, limit: int) -> bytes:
        path = f"{API_PREFIX}actions/artifacts/{artifact_id}/zip"
        request = Request(_api_url(path), headers=self._headers(), method="GET")
        location: str | None = None
        try:
            response = self._api_opener.open(request, timeout=30)
            try:
                raise FetchError("artifact_redirect_missing")
            finally:
                response.close()
        except HTTPError as exc:
            try:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                else:
                    raise FetchError("artifact_redirect_failed") from exc
            finally:
                exc.close()
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError("artifact_redirect_failed") from exc
        if not location:
            raise FetchError("artifact_redirect_missing")

        # Deliberately construct a new request without Authorization.
        download_request = Request(
            _download_url(location),
            headers={"Accept": "application/octet-stream", "User-Agent": "weekly-investment-agent-shadow-fetcher"},
            method="GET",
        )
        try:
            with self._download_opener.open(download_request, timeout=60) as response:
                if response.status != 200:
                    raise FetchError("artifact_download_failed")
                return _read_limited(response, limit)
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError("artifact_download_failed") from exc


def _fetch_bundle(client: Any, run_id: int) -> tuple[int, bytes]:
    run = client.get_json(f"{API_PREFIX}actions/runs/{run_id}")
    repository = (run.get("repository") or {}).get("full_name")
    if (
        run.get("id") != run_id
        or repository != REPOSITORY
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_branch") != "main"
    ):
        raise FetchError("run_not_allowlisted")

    listing = client.get_json(f"{API_PREFIX}actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = listing.get("artifacts")
    if not isinstance(artifacts, list):
        raise FetchError("artifact_listing_invalid")
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("name") == ARTIFACT_NAME]
    if len(matches) != 1:
        raise FetchError("artifact_not_unique")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    declared_size = artifact.get("size_in_bytes")
    workflow_run = artifact.get("workflow_run") or {}
    if (
        not isinstance(artifact_id, int)
        or artifact_id <= 0
        or artifact.get("expired") is not False
        or not isinstance(declared_size, int)
        or declared_size <= 0
        or declared_size > MAX_ARCHIVE_BYTES
        or workflow_run.get("id") != run_id
    ):
        raise FetchError("artifact_metadata_invalid")
    blob = client.download_artifact(artifact_id, MAX_ARCHIVE_BYTES)
    if not isinstance(blob, bytes) or len(blob) != declared_size:
        raise FetchError("artifact_size_mismatch")
    return artifact_id, blob


def run(*, run_id: int, decision_as_of: str, client: Any | None = None) -> dict[str, Any]:
    """Fetch one allowlisted artifact and run it through D-4/D-3 shadow checks."""
    if not _enabled():
        return {"schemaVersion": 1, "mode": "disabled", "blockers": ["github_artifact_fetch_disabled"]}
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        return {"schemaVersion": 1, "mode": "research_only", "blockers": ["run_id_invalid"]}
    if not _valid_decision_as_of(decision_as_of):
        return {"schemaVersion": 1, "mode": "research_only", "blockers": ["decision_as_of_invalid"]}

    try:
        active_client = client or GitHubArtifactClient(os.environ.get("GITHUB_TOKEN"))
        artifact_id, blob = _fetch_bundle(active_client, run_id)
        archive_hash = hashlib.sha256(blob).hexdigest()
        extracted = artifact_shadow_extract.extract(blob)
        if extracted.get("mode") != "shadow_only":
            return {
                "schemaVersion": 1,
                "mode": "research_only",
                "runId": run_id,
                "artifactId": artifact_id,
                "archiveSha256": archive_hash,
                "archiveBytes": len(blob),
                "blockers": ["artifact_extract_not_certified"],
            }
        pipeline = shadow_pipeline.run(
            extracted["contract"], extracted["manifest"], decision_as_of=decision_as_of
        )
        mode = "shadow_only" if pipeline.get("mode") == "shadow_only" else "research_only"
        return {
            "schemaVersion": 1,
            "mode": mode,
            "runId": run_id,
            "artifactId": artifact_id,
            "archiveSha256": archive_hash,
            "archiveBytes": len(blob),
            "pipelineMode": pipeline.get("mode", "research_only"),
            "blockers": pipeline.get("blockers", ["pipeline_not_certified"]),
            "limitation": "Read-only shadow validation; no formal advice, notification, or trading effect.",
        }
    except Exception as exc:
        blocker = exc.args[0] if isinstance(exc, FetchError) and exc.args else type(exc).__name__
        return {"schemaVersion": 1, "mode": "research_only", "blockers": [str(blocker)]}
