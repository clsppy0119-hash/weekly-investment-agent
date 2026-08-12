"""One-shot, pinned, default-off real-data shadow smoke check."""
from __future__ import annotations

import os
from typing import Any

import github_shadow_artifact as fetcher

FLAG = "GITHUB_REAL_SMOKE_ENABLED"
PINNED_RUN_ID = 31567642261
PINNED_HEAD_SHA = "fb5f1c70a78701d65a91bc3969f038ad486bf8ad"
PINNED_ARTIFACT_ID = 9130064991
PINNED_ARTIFACT_SIZE = 21123
PINNED_ARCHIVE_SHA256 = "89b55f245374a0a14d6c87418e31c4ec1206e26686eb70e00c538ff0a925099c"
PINNED_DECISION_AS_OF = "2026-08-12T13:47:02+08:00"
SCOPED_FLAGS = (
    fetcher.FLAG,
    "ARTIFACT_SHADOW_EXTRACT_ENABLED",
    "SHADOW_PIPELINE_ENABLED",
    "LINEAGE_FREEZE_ENABLED",
)


def _enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


class _PinnedClient:
    def __init__(self, delegate: Any):
        self.delegate = delegate

    def get_json(self, path: str):
        value = self.delegate.get_json(path)
        if path.endswith(f"/actions/runs/{PINNED_RUN_ID}"):
            if value.get("head_sha") != PINNED_HEAD_SHA:
                raise fetcher.FetchError("pinned_head_sha_mismatch")
        elif path.endswith(f"/actions/runs/{PINNED_RUN_ID}/artifacts?per_page=100"):
            matches = [
                item for item in value.get("artifacts", [])
                if isinstance(item, dict) and item.get("name") == fetcher.ARTIFACT_NAME
            ]
            if len(matches) != 1:
                raise fetcher.FetchError("pinned_artifact_not_unique")
            item = matches[0]
            if item.get("id") != PINNED_ARTIFACT_ID or item.get("size_in_bytes") != PINNED_ARTIFACT_SIZE:
                raise fetcher.FetchError("pinned_artifact_metadata_mismatch")
        else:
            raise fetcher.FetchError("pinned_api_path_mismatch")
        return value

    def download_artifact(self, artifact_id: int, limit: int):
        if artifact_id != PINNED_ARTIFACT_ID:
            raise fetcher.FetchError("pinned_artifact_id_mismatch")
        return self.delegate.download_artifact(artifact_id, limit)


def run(*, client: Any | None = None) -> dict[str, Any]:
    if not _enabled():
        return {"schemaVersion": 1, "mode": "disabled", "blockers": ["real_smoke_disabled"]}

    previous = {name: os.environ.get(name) for name in SCOPED_FLAGS}
    try:
        for name in SCOPED_FLAGS:
            os.environ[name] = "true"
        active = client or fetcher.GitHubArtifactClient(os.environ.get("GITHUB_TOKEN"))
        result = fetcher.run(
            run_id=PINNED_RUN_ID,
            decision_as_of=PINNED_DECISION_AS_OF,
            client=_PinnedClient(active),
        )
        allowed = {
            key: result[key]
            for key in (
                "schemaVersion", "mode", "runId", "artifactId", "archiveSha256",
                "archiveBytes", "pipelineMode", "blockers", "limitation",
            )
            if key in result
        }
        if allowed.get("artifactId") not in (None, PINNED_ARTIFACT_ID):
            return {"schemaVersion": 1, "mode": "research_only", "blockers": ["pinned_result_mismatch"]}
        if allowed.get("archiveBytes") not in (None, PINNED_ARTIFACT_SIZE):
            return {"schemaVersion": 1, "mode": "research_only", "blockers": ["pinned_result_mismatch"]}
        if allowed.get("archiveSha256") not in (None, PINNED_ARCHIVE_SHA256):
            return {"schemaVersion": 1, "mode": "research_only", "blockers": ["pinned_result_mismatch"]}
        return allowed
    except Exception as exc:
        blocker = exc.args[0] if isinstance(exc, fetcher.FetchError) and exc.args else type(exc).__name__
        return {"schemaVersion": 1, "mode": "research_only", "blockers": [str(blocker)]}
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
