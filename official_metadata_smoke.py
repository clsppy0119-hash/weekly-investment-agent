"""Controlled, default-off official metadata capability smoke.

Exactly five allowlisted metadata documents may be read.  Membership endpoints
are never invoked, response bodies are never persisted, and no timestamp value
is emitted or forwarded to the PIT evidence selector.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import official_availability_capability as capability

FLAG = "OFFICIAL_METADATA_SMOKE_ENABLED"
SCHEMA_VERSION = 1
POLICY = "controlled-official-metadata-smoke-v1"
MAX_REQUESTS = 5
MAX_SWAGGER_BYTES = 8 * 1024 * 1024
MAX_DATASET_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 15

TWSE_SWAGGER = "https://openapi.twse.com.tw/v1/swagger.json"
TPEX_SWAGGER = "https://www.tpex.org.tw/openapi/swagger.json"
DATASET_URLS = {
    "18419": "https://data.gov.tw/api/v2/rest/dataset/18419",
    "11543": "https://data.gov.tw/api/v2/rest/dataset/11543",
    "25036": "https://data.gov.tw/api/v2/rest/dataset/25036",
}
REQUEST_PLAN = (
    ("twse_swagger", TWSE_SWAGGER, MAX_SWAGGER_BYTES),
    ("data_gov_18419", DATASET_URLS["18419"], MAX_DATASET_BYTES),
    ("data_gov_11543", DATASET_URLS["11543"], MAX_DATASET_BYTES),
    ("data_gov_25036", DATASET_URLS["25036"], MAX_DATASET_BYTES),
    ("tpex_swagger", TPEX_SWAGGER, MAX_SWAGGER_BYTES),
)
DATASET_CONTRACTS = {
    "18419": ("twse_listed", "上市公司基本資料"),
    "11543": ("twse_terminated", "終止上市公司"),
    "25036": ("tpex_listed", "上櫃公司基本資料"),
}
OPERATION_CONTRACTS = {
    "twse_listed": ("twse_swagger", "/opendata/t187ap03_L"),
    "tpex_listed": ("tpex_swagger", "/mopsfin_t187ap03_O"),
    "tpex_emerging": ("tpex_swagger", "/mopsfin_t187ap04_O"),
}
FORBIDDEN_OUTPUT = re.compile(
    r"(?:https?://|availableAt|publishedAt|retrievedAt|generatedAt|authorization|cookie|token|secret|rawRows)",
    re.IGNORECASE,
)


def enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in {"1", "true", "yes"}


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes


class Client(Protocol):
    def get(self, url: str, *, max_bytes: int, timeout: int) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibClient:
    """GET-only client with no redirects, auth, cookies, retries, or persistence."""

    def __init__(self) -> None:
        # Ignore environment proxy configuration so proxy credentials cannot be
        # attached implicitly to this tightly controlled metadata probe.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, url: str, *, max_bytes: int, timeout: int) -> Response:
        if url not in {item[1] for item in REQUEST_PLAN}:
            raise ValueError("endpoint_not_allowlisted")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "weekly-investment-agent-metadata-audit/1.0"},
        )
        with self._opener.open(request, timeout=timeout) as remote:
            if getattr(remote, "geturl", lambda: url)() != url:
                raise ValueError("redirect_forbidden")
            body = remote.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError("response_too_large")
            return Response(
                status=int(getattr(remote, "status", 0)),
                content_type=str(remote.headers.get("Content-Type", "")),
                body=body,
            )


def _json(response: Response, max_bytes: int) -> dict[str, Any]:
    if response.status != 200:
        raise ValueError("status_not_200")
    if len(response.body) > max_bytes:
        raise ValueError("response_too_large")
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/vnd.oai.openapi+json"}:
        raise ValueError("content_type_not_json")
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("malformed_json") from error
    if not isinstance(value, dict):
        raise ValueError("json_root_not_object")
    return value


def _find_dataset(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        if keys & {"datasetid", "identifier"} and keys & {"title", "name", "datasetname"}:
            return value
        for child in value.values():
            found = _find_dataset(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_dataset(child)
            if found is not None:
                return found
    return None


def _field(record: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in record.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _dataset_evidence(document: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    source_key, expected_title = DATASET_CONTRACTS[dataset_id]
    record = _find_dataset(document)
    if record is None:
        return {"sourceKey": source_key, "identity": "invalid", "terms": "unknown", "semantic": "unsupported"}
    actual_id = str(_field(record, "datasetId", "identifier") or "")
    title = str(_field(record, "title", "name", "datasetName") or "")
    license_value = str(_field(record, "license", "licenseName") or "")
    publisher = str(_field(record, "publisher", "publisherName", "organization") or "")
    identity = "match" if actual_id == dataset_id and title == expected_title else "mismatch"
    terms = "open_data_attribution" if (
        identity == "match"
        and publisher
        and (license_value == "1" or "政府資料開放授權條款" in license_value or "Open Government Data License" in license_value)
    ) else "unknown"
    return {"sourceKey": source_key, "identity": identity, "terms": terms, "semantic": "unsupported"}


def _swagger_version(document: dict[str, Any]) -> bool:
    swagger = document.get("swagger")
    openapi = document.get("openapi")
    return (isinstance(swagger, str) and swagger.startswith("2.")) or (
        isinstance(openapi, str) and openapi.startswith("3.")
    )


def _operation_evidence(document: dict[str, Any], path: str) -> dict[str, Any]:
    if not _swagger_version(document) or not isinstance(document.get("paths"), dict):
        return {"operation": "schema_invalid", "semantic": "unsupported"}
    paths = document["paths"]
    matches = [value for key, value in paths.items() if key == path]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        return {"operation": "missing_or_ambiguous", "semantic": "unsupported"}
    operation = matches[0]
    methods = [key.lower() for key in operation if key.lower() in {"get", "post", "put", "patch", "delete"}]
    if methods != ["get"] or not isinstance(operation.get("get"), dict):
        return {"operation": "unsafe_or_ambiguous", "semantic": "unsupported"}
    # Only the operation definition is inspected.  A certifiable semantic must
    # explicitly document publication/availability, timezone, immutable
    # identity, revision semantics, and complete historical coverage.
    definition = operation["get"]
    rendered = json.dumps(definition, ensure_ascii=False, sort_keys=True).lower()
    has_publication = "publishedat" in rendered or "availableat" in rendered
    has_timezone = "date-time" in rendered and any(word in rendered for word in ("timezone", "utc", "offset"))
    has_immutable = any(word in rendered for word in ("immutable", "revisionid", "versionid", "archiveid"))
    has_revision = any(word in rendered for word in ("supersedes", "revision semantics", "version history"))
    has_history = any(word in rendered for word in ("complete history", "historical archive", "all revisions"))
    certifiable = all((has_publication, has_timezone, has_immutable, has_revision, has_history))
    date_only_notice = (
        not certifiable
        and any(word in rendered for word in ("publication date", "notice date", "公告日期"))
        and "date-time" not in rendered
    )
    return {
        "operation": "exact_get",
        "semantic": "certifiable" if certifiable else "date_only" if date_only_notice else "unsupported",
        "schemaHash": _hash(definition),
    }


def _contract(source_key: str, operation: dict[str, Any], dataset: dict[str, Any] | None) -> dict[str, Any]:
    provider, contract_id = capability.EXPECTED[source_key]
    semantic = operation.get("semantic")
    conflict = (
        operation.get("operation") in {"schema_invalid", "missing_or_ambiguous", "unsafe_or_ambiguous"}
        or (dataset is not None and dataset.get("identity") == "mismatch")
    )
    terms = dataset.get("terms") if dataset is not None else "unknown"
    if source_key == "tpex_emerging":
        # No verified data.gov dataset ID exists in this contract.  Swagger
        # terms alone are not inferred, so absence remains fail-closed.
        terms = "unknown"
    return {
        "schemaVersion": capability.SCHEMA_VERSION,
        "sourceKey": source_key,
        "provider": provider,
        "contractId": contract_id,
        "documentationEvidenceId": f"metadata-smoke:{source_key}:{operation.get('schemaHash', 'none')[:16]}",
        "termsStatus": terms,
        "evidenceMode": "official_timestamp" if semantic == "certifiable" else "unsupported_metadata",
        "publicationSemantic": (
            "official_published_timestamp" if semantic == "certifiable"
            else "official_notice_date" if semantic == "date_only"
            else "current_metadata_time"
        ),
        "timestampPrecision": (
            "timestamp_with_timezone" if semantic == "certifiable"
            else "date_only" if semantic == "date_only"
            else "none"
        ),
        "timezoneDocumented": semantic == "certifiable",
        "immutableIdentityDocumented": semantic == "certifiable",
        "revisionSemanticsDocumented": semantic == "certifiable",
        "historyCoverage": "complete" if semantic == "certifiable" else "current_only",
        "usesUndocumentedHttpHeaders": False,
        "conflictStatus": "conflict" if conflict else "no_conflict",
        "contentHash": _hash({"sourceKey": source_key, "operation": operation, "dataset": dataset}),
        "schemaHash": operation.get("schemaHash") if isinstance(operation.get("schemaHash"), str) else "0" * 64,
    }


def run(client: Client | None = None) -> dict[str, Any]:
    if not enabled():
        return {"schemaVersion": SCHEMA_VERSION, "policy": POLICY, "mode": "disabled", "requestCount": 0}
    client = client or UrllibClient()
    documents: dict[str, dict[str, Any]] = {}
    request_count = 0
    try:
        for name, url, maximum in REQUEST_PLAN:
            response = client.get(url, max_bytes=maximum, timeout=TIMEOUT_SECONDS)
            request_count += 1
            documents[name] = _json(response, maximum)
    except Exception as error:  # Fail closed with a sanitized error type only.
        return {
            "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
            "mode": "research_only", "diagnosticOnly": True,
            "requestCount": request_count,
            "blockers": [f"metadata_fetch_failed:{type(error).__name__}"],
        }

    datasets = {
        DATASET_CONTRACTS[dataset_id][0]: _dataset_evidence(documents[f"data_gov_{dataset_id}"], dataset_id)
        for dataset_id in DATASET_CONTRACTS
    }
    operations = {
        source_key: _operation_evidence(documents[document_key], path)
        for source_key, (document_key, path) in OPERATION_CONTRACTS.items()
    }
    # The approved TWSE delisting source is a fixed data.gov dataset contract,
    # not an allowlisted Swagger operation.  Keep it explicitly unsupported;
    # never invent or invoke a membership endpoint to fill this gap.
    operations["twse_terminated"] = {
        "operation": "metadata_only",
        "semantic": "unsupported",
        "schemaHash": _hash({"datasetId": "11543", "kind": "metadata_only"}),
    }
    contracts = [
        _contract(source_key, operations[source_key], datasets.get(source_key))
        for source_key in capability.EXPECTED
    ]
    old = os.environ.get(capability.FLAG)
    os.environ[capability.FLAG] = "true"
    try:
        matrix = capability.audit(contracts)
    finally:
        if old is None:
            os.environ.pop(capability.FLAG, None)
        else:
            os.environ[capability.FLAG] = old
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "policy": POLICY,
        "mode": "research_only",
        "diagnosticOnly": True,
        "requestCount": request_count,
        "operationInvokeCount": 0,
        "persistenceWriteCount": 0,
        "capabilityMatrix": matrix,
        "smokeDigest": _hash({"policy": POLICY, "requestCount": request_count, "matrix": matrix}),
        "blockers": [] if matrix.get("capabilityReady") else ["official_availability_capability_incomplete"],
        "limitation": "Metadata contract audit only; no membership data or availability timestamp was fetched or propagated.",
    }
    if FORBIDDEN_OUTPUT.search(json.dumps(result, ensure_ascii=False, sort_keys=True)):
        return {
            "schemaVersion": SCHEMA_VERSION, "policy": POLICY,
            "mode": "research_only", "diagnosticOnly": True,
            "requestCount": request_count, "blockers": ["sanitized_output_violation"],
        }
    return result
