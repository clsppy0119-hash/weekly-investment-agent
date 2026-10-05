"""Offline expectation contract for the future Node57 GitHub main receipt.

This module does not call GitHub, verify a signature, attest an artifact, or
seal the preregistration.  It validates only the bounded shape and internal
cross-bindings of metadata that a later controlled verifier must independently
fetch from GitHub.  Consequently every authority, data-access, performance,
promotion, advice, and trading flag remains false even for a complete fixture.
Like any in-process Python object, the return value is not a security boundary
against arbitrary same-process code execution, trace hooks, or debugger
mutation.  A later authoritative verifier must run in isolation and authenticate
an external GitHub receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "actual-comprehensive-main-seal-receipt-v1"
REPOSITORY = "clsppy0119-hash/weekly-investment-agent"
REPOSITORY_ID = 1314663807
OWNER_ID = 307921499
VISIBILITY = "public"
PR_NUMBER = 118
BASE_REF = "refs/heads/main"
HEAD_REF = "codex/actual-comprehensive-preregistration"
REVIEWED_BASE_MAIN_SHA = "4a31db49a1f1f9e04dd5f42a8e4c6862269001f0"
REVIEWED_HEAD_SHA = "fb87b111aa605a1902acb9120bafea7a977ed05c"
REVIEWED_TREE_SHA = "6057e846170c7d216935b040671247d015848a1b"
MERGE_COMMIT_SHA = "e383a9851d080f5cde1354cb5aaa69fbd2988497"
MERGE_PARENT_SHA = "a7f3ea4d4fe692c5be7f636df9d4e063f23f64d2"
SERVER_MERGED_AT = "2026-08-13T14:57:44Z"

PREREGISTRATION_HASH = "8ad3599eb1d7b1adc3ae8d59fd19c1d3d42aeebf6e1928fe52afb5fc55eb13d5"
DECISION_RECEIPT_COMMITMENT_HASH = (
    "171f69c4d809de5365cb28c6d8fc38e6f6fafeb571c906f4c42cbdbae533be46"
)
SOURCE_PIN_SET_HASH = "42b19558e9e0e3bf6ed67064dff609f67e9f376e02acd089919155e325889aa3"
STRATEGY_SPEC_HASH = "e66b2f81b8f6400b6b6239b625f50f3b9a7c0e789ae63250ee6fc261272ae616"
PIT_REQUIREMENTS_HASH = "a7fe498e4367e0b176e007c2b66a1a16ca00edd62febfa94e3bcf5b34a70fb47"

SAFETY_WORKFLOW_PATH = ".github/workflows/pipeline-safety-validation.yml"
SAFETY_WORKFLOW_NAME = "Pipeline safety validation"
SAFETY_JOB_NAME = "local-equivalent-safety-suite"
SAFETY_RUN_ID = 31705383828
SAFETY_RUN_ATTEMPT = 1
SAFETY_JOB_ID = 94464501553
SAFETY_WORKFLOW_ID = 328269653
SAFETY_CHECK_SUITE_ID = 86010068679
SAFETY_RUN_CREATED_AT = "2026-08-13T13:31:52Z"
SAFETY_JOB_STARTED_AT = "2026-08-13T13:31:55Z"
SAFETY_JOB_COMPLETED_AT = "2026-08-13T13:32:08Z"

EXPECTED_FILE_PINS = (
    (
        ".github/workflows/pipeline-safety-validation.yml",
        "5c2f1c5f9583aabe19e64be83722b11734211cc77d0d10b8383b8a3c573fa543",
        "45598786db93088525b5153de18a696ab88d9bed",
    ),
    (
        "actual_comprehensive_validation_preregistration.py",
        "91e03767112a5dee4b772229b296da09ec23340342c8600158faea21d0563c67",
        "dbc167368fc6dfd046650eda5f6cfc8898e23162",
    ),
    (
        "actual_comprehensive_validation_preregistration_v1.json",
        "2801b590f4dee9c360bd36b4189b96f9bbd3be033eaf20c696fc4dcf80767899",
        "c7be5c7e33ab5e3bac8e62b4941862a7d49da87e",
    ),
    (
        "tests/test_actual_comprehensive_validation_preregistration.py",
        "8b85c4de93fc7c7331a2b65cd9577cbaa42be27f23d7b2a7a78940ee43dc5f39",
        "8464b9784f7f25be2027bac742f9250177d407a9",
    ),
)

MAX_NODES = 30_000
MAX_DEPTH = 12
MAX_STRING = 512
MAX_CANONICAL_BYTES = 500_000
MAX_INTEGER_ABS = 10**18

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

ROOT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "repository", "pullRequest",
    "mergeCommit", "safetyRun", "node57Bindings", "controlledFetch",
    "receiptDigest",
})
REPOSITORY_KEYS = frozenset({
    "repositoryId", "ownerId", "fullName", "visibility",
})
PR_KEYS = frozenset({
    "number", "state", "isDraft", "baseRef", "baseSha", "headRef",
    "headSha", "headRepositoryId", "mergedAt", "mergeCommitSha",
})
MERGE_KEYS = frozenset({
    "sha", "treeSha", "parents", "mainRef", "mainHeadSha",
})
SAFETY_KEYS = frozenset({
    "workflowPath", "workflowName", "workflowFileSha256",
    "workflowGitBlobSha1", "runId", "runAttempt", "event", "headSha",
    "headBranch", "status", "conclusion", "createdAt", "completedAt",
    "jobId", "jobName", "jobStatus", "jobConclusion", "jobStartedAt",
    "jobCompletedAt",
    "workflowId", "checkSuiteId", "repositoryId", "headRepositoryId",
    "pullRequestAssociationCount", "associationEvidenceClass",
})
BINDING_KEYS = frozenset({
    "reviewedHeadSha", "reviewedTreeSha", "preregistrationPath",
    "preregistrationHash", "decisionReceiptCommitmentHash",
    "sourcePinSetHash", "strategySpecHash", "pitRequirementsHash",
    "filePins",
})
FILE_PIN_KEYS = frozenset({"path", "sha256", "gitBlobSha1"})
FETCH_KEYS = frozenset({
    "evidenceClass", "githubApiHostAlias", "apiResponseDigest",
    "fetcherPolicyVersion", "fetchedAt",
})

FIXED_BLOCKERS = (
    "controlled_github_server_receipt_fetch_unimplemented",
    "github_attestation_verification_unimplemented",
    "trusted_user_policy_decision_receipt_verification_required",
    "independent_custody_unverified",
    "source_registration_receipts_missing",
    "confirmatory_evaluator_unregistered",
    "return_free_inventory_not_created",
)

_FORBIDDEN_KEY_PARTS = (
    "raw", "body", "headers", "url", "uri", "query", "token", "secret",
    "authorization", "cookie", "password", "price", "return", "outcome",
    "mdd", "pnl", "score", "rank", "winner", "adviceenabled",
    "promotioneligible", "strategyvalidated", "tradingenabled",
    "confirmatorydataaccesseligible", "sealed",
)
_FORBIDDEN_TEXT = (
    "://", "bearer ", "authorization:", "token=", "password=", "cookie=",
    "-----begin ",
)


def _canonical(value: Any, _dumps: Any = json.dumps) -> bytes:
    return _dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(
    value: Any,
    _canonical_bytes: Any = _canonical,
    _sha256: Any = hashlib.sha256,
) -> str:
    return _sha256(_canonical_bytes(value)).hexdigest()


def _aware_utc(value: Any, _datetime: Any = datetime, _utc: Any = timezone.utc) -> datetime | None:
    if type(value) is not str or len(value) > 40:
        return None
    try:
        parsed = _datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        return None
    return parsed.astimezone(_utc)


def _bounded_json(
    value: Any,
    _max_nodes: int = MAX_NODES,
    _max_depth: int = MAX_DEPTH,
    _max_string: int = MAX_STRING,
    _max_integer_abs: int = MAX_INTEGER_ABS,
    _finite: Any = math.isfinite,
) -> bool:
    seen: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > _max_nodes or depth > _max_depth:
            return False
        item_type = type(item)
        if item is None or item_type is bool:
            return True
        if item_type is int:
            return abs(item) <= _max_integer_abs
        if item_type is float:
            return _finite(item)
        if item_type is str:
            return len(item) <= _max_string and all(ord(char) >= 32 for char in item)
        if item_type not in (dict, list):
            return False
        identity = id(item)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            if item_type is dict:
                return all(
                    type(key) is str and len(key) <= 100 and visit(child, depth + 1)
                    for key, child in item.items()
                )
            return all(visit(child, depth + 1) for child in item)
        finally:
            seen.remove(identity)

    return visit(value, 0)


def _contains_forbidden(
    value: Any,
    _key_parts: tuple[str, ...] = _FORBIDDEN_KEY_PARTS,
    _text: tuple[str, ...] = _FORBIDDEN_TEXT,
) -> bool:
    def visit(item: Any) -> bool:
        if type(item) is dict:
            for key, child in item.items():
                normalized = "".join(character for character in key.casefold() if character.isalnum())
                if any(part in normalized for part in _key_parts) or visit(child):
                    return True
            return False
        if type(item) is list:
            return any(visit(child) for child in item)
        if type(item) is str:
            folded = item.casefold()
            return any(marker in folded for marker in _text)
        return False

    return visit(value)


def _expectation_definition() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "repository": {
            "repositoryId": REPOSITORY_ID,
            "ownerId": OWNER_ID,
            "fullName": REPOSITORY,
            "visibility": VISIBILITY,
        },
        "pullRequest": {
            "number": PR_NUMBER,
            "baseRef": BASE_REF,
            "baseSha": REVIEWED_BASE_MAIN_SHA,
            "headRef": HEAD_REF,
            "headSha": REVIEWED_HEAD_SHA,
            "headRepositoryId": REPOSITORY_ID,
            "requiredState": "MERGED",
            "requiredDraftState": False,
            "requiredMergedAt": SERVER_MERGED_AT,
        },
        "mergeCommit": {
            "requiredSha": MERGE_COMMIT_SHA,
            "requiredTreeSha": REVIEWED_TREE_SHA,
            "requiredParents": [MERGE_PARENT_SHA],
            "requiredMainRef": BASE_REF,
            "mergeMethod": "squash",
        },
        "safetyRun": {
            "workflowPath": SAFETY_WORKFLOW_PATH,
            "workflowName": SAFETY_WORKFLOW_NAME,
            "workflowFileSha256": EXPECTED_FILE_PINS[0][1],
            "workflowGitBlobSha1": EXPECTED_FILE_PINS[0][2],
            "runId": SAFETY_RUN_ID,
            "runAttempt": SAFETY_RUN_ATTEMPT,
            "event": "pull_request",
            "headSha": REVIEWED_HEAD_SHA,
            "headBranch": HEAD_REF,
            "status": "completed",
            "conclusion": "success",
            "createdAt": SAFETY_RUN_CREATED_AT,
            "completedAt": SAFETY_JOB_COMPLETED_AT,
            "jobId": SAFETY_JOB_ID,
            "jobName": SAFETY_JOB_NAME,
            "jobStatus": "completed",
            "jobConclusion": "success",
            "jobStartedAt": SAFETY_JOB_STARTED_AT,
            "jobCompletedAt": SAFETY_JOB_COMPLETED_AT,
            "workflowId": SAFETY_WORKFLOW_ID,
            "checkSuiteId": SAFETY_CHECK_SUITE_ID,
            "repositoryId": REPOSITORY_ID,
            "headRepositoryId": REPOSITORY_ID,
            "pullRequestAssociationCount": 0,
            "associationEvidenceClass": "pull_request_event_exact_head_ref_no_check_run_pr_link",
        },
        "node57Bindings": {
            "reviewedHeadSha": REVIEWED_HEAD_SHA,
            "reviewedTreeSha": REVIEWED_TREE_SHA,
            "preregistrationPath": "actual_comprehensive_validation_preregistration_v1.json",
            "preregistrationHash": PREREGISTRATION_HASH,
            "decisionReceiptCommitmentHash": DECISION_RECEIPT_COMMITMENT_HASH,
            "sourcePinSetHash": SOURCE_PIN_SET_HASH,
            "strategySpecHash": STRATEGY_SPEC_HASH,
            "pitRequirementsHash": PIT_REQUIREMENTS_HASH,
            "filePins": [
                {"path": path, "sha256": sha256, "gitBlobSha1": git_blob}
                for path, sha256, git_blob in EXPECTED_FILE_PINS
            ],
        },
        "evidenceBoundary": {
            "acceptedInputClass": "caller_supplied_github_server_receipt_shape",
            "serverAuthenticationProvidedByThisModule": False,
            "githubAttestationVerifiedByThisModule": False,
            "decisionReceiptVerifiedByThisModule": False,
            "sealedByThisModule": False,
            "runtimeTrustBoundary": (
                "same_process_arbitrary_code_execution_trace_and_debugger_hooks_out_of_scope"
            ),
            "authoritativeVerificationRequirement": (
                "isolated_process_and_authenticated_external_github_receipt_required"
            ),
        },
    }


def _factory() -> tuple[Any, Any, Any]:
    canonical = _canonical
    bounded = _bounded_json
    forbidden = _contains_forbidden
    loads = json.loads
    sha256 = hashlib.sha256
    schema_version = SCHEMA_VERSION
    policy_version = POLICY_VERSION
    max_bytes = MAX_CANONICAL_BYTES
    hex40 = HEX40
    hex64 = HEX64
    aware = _aware_utc
    fixed_blockers = FIXED_BLOCKERS
    root_keys = ROOT_KEYS
    repository_keys = REPOSITORY_KEYS
    pr_keys = PR_KEYS
    merge_keys = MERGE_KEYS
    safety_keys = SAFETY_KEYS
    binding_keys = BINDING_KEYS
    file_pin_keys = FILE_PIN_KEYS
    fetch_keys = FETCH_KEYS
    expectation_bytes = canonical(_expectation_definition())
    expected = loads(expectation_bytes.decode("utf-8"))
    expected_repository = expected["repository"]
    expected_pr = expected["pullRequest"]
    expected_merge = expected["mergeCommit"]
    expected_safety = expected["safetyRun"]
    expected_bindings = expected["node57Bindings"]
    repository_tuple = tuple(sorted(expected_repository.items()))
    expected_pr_tuple = tuple(sorted(
        (key, value) for key, value in expected_pr.items()
        if key not in ("requiredState", "requiredDraftState", "requiredMergedAt")
    ))
    required_state = expected_pr["requiredState"]
    required_draft = expected_pr["requiredDraftState"]
    required_merged_at = expected_pr["requiredMergedAt"]
    required_merge_sha = expected_merge["requiredSha"]
    required_tree = expected_merge["requiredTreeSha"]
    required_parents = tuple(expected_merge["requiredParents"])
    required_main_ref = expected_merge["requiredMainRef"]
    safety_tuple = tuple(sorted(expected_safety.items()))
    binding_scalar_tuple = tuple(sorted(
        (key, value) for key, value in expected_bindings.items() if key != "filePins"
    ))
    file_pin_tuple = tuple(
        (item["path"], item["sha256"], item["gitBlobSha1"])
        for item in expected_bindings["filePins"]
    )
    del expected
    del expected_repository
    del expected_pr
    del expected_merge
    del expected_safety
    del expected_bindings

    def expectation() -> dict[str, Any]:
        return loads(expectation_bytes.decode("utf-8"))

    def report(valid: bool, receipt_digest: str, blockers: list[str]) -> dict[str, Any]:
        return {
            "schemaVersion": schema_version,
            "policyVersion": policy_version,
            "mode": "research_only",
            "receiptStructurallyValid": valid,
            "crossBindingsMatch": valid,
            "receiptDigest": receipt_digest if valid else "",
            "serverReceiptAuthenticated": False,
            "mainMergeVerified": False,
            "githubSealIntegrityVerified": False,
            "decisionReceiptVerified": False,
            "policyDecisionRecorded": False,
            "sealed": False,
            "confirmatoryDataAccessEligible": False,
            "officialSourceAuthenticated": False,
            "pitCoverageCertified": False,
            "performanceEvaluated": False,
            "riskPolicyPassed": False,
            "strategyValidated": False,
            "promotionEligible": False,
            "adviceEnabled": False,
            "formalGateAttached": False,
            "notificationEnabled": False,
            "tradingEnabled": False,
            "blockers": list(fixed_blockers) if valid else blockers,
        }

    def evaluate(value: Any) -> dict[str, Any]:
        try:
            if not bounded(value) or forbidden(value):
                return report(False, "", ["input_not_bounded_metadata_only_json"])
            encoded = canonical(value)
            if len(encoded) > max_bytes or type(value) is not dict or set(value) != root_keys:
                return report(False, "", ["receipt_schema_invalid"])
            if (
                type(value.get("schemaVersion")) is not int
                or value["schemaVersion"] != schema_version
                or type(value.get("policyVersion")) is not str
                or value["policyVersion"] != policy_version
            ):
                return report(False, "", ["receipt_schema_invalid"])

            repository = value.get("repository")
            pr = value.get("pullRequest")
            merge = value.get("mergeCommit")
            safety = value.get("safetyRun")
            bindings = value.get("node57Bindings")
            fetch = value.get("controlledFetch")
            if not (
                type(repository) is dict and set(repository) == repository_keys
                and type(pr) is dict and set(pr) == pr_keys
                and type(merge) is dict and set(merge) == merge_keys
                and type(safety) is dict and set(safety) == safety_keys
                and type(bindings) is dict and set(bindings) == binding_keys
                and type(fetch) is dict and set(fetch) == fetch_keys
                and type(bindings.get("filePins")) is list
            ):
                return report(False, "", ["receipt_schema_invalid"])

            strict_integer_fields = (
                (repository, "repositoryId"),
                (repository, "ownerId"),
                (pr, "number"),
                (pr, "headRepositoryId"),
                (safety, "runId"),
                (safety, "runAttempt"),
                (safety, "jobId"),
                (safety, "workflowId"),
                (safety, "checkSuiteId"),
                (safety, "repositoryId"),
                (safety, "headRepositoryId"),
                (safety, "pullRequestAssociationCount"),
            )
            if any(type(parent.get(key)) is not int for parent, key in strict_integer_fields):
                return report(False, "", ["receipt_schema_invalid"])
            if type(pr.get("isDraft")) is not bool:
                return report(False, "", ["receipt_schema_invalid"])

            actual_repository_tuple = tuple(sorted(repository.items()))
            actual_pr_tuple = tuple(sorted(
                (key, pr[key]) for key in pr
                if key not in ("state", "isDraft", "mergedAt", "mergeCommitSha")
            ))
            actual_safety_tuple = tuple(sorted(safety.items()))
            actual_binding_scalar_tuple = tuple(sorted(
                (key, bindings[key]) for key in bindings if key != "filePins"
            ))
            actual_file_pin_tuple = tuple(
                (item.get("path"), item.get("sha256"), item.get("gitBlobSha1"))
                for item in bindings["filePins"]
                if type(item) is dict and set(item) == file_pin_keys
            )
            if (
                len(actual_file_pin_tuple) != len(bindings["filePins"])
                or actual_repository_tuple != repository_tuple
                or actual_pr_tuple != expected_pr_tuple
                or pr["state"] != required_state
                or pr["isDraft"] is not required_draft
                or actual_safety_tuple != safety_tuple
                or actual_binding_scalar_tuple != binding_scalar_tuple
                or actual_file_pin_tuple != file_pin_tuple
            ):
                return report(False, "", ["reviewed_identity_or_pin_mismatch"])

            merge_sha = merge.get("sha")
            parents = merge.get("parents")
            if not (
                type(merge_sha) is str and hex40.fullmatch(merge_sha)
                and merge_sha == required_merge_sha
                and merge_sha not in (pr["headSha"], pr["baseSha"])
                and merge.get("treeSha") == required_tree
                and type(parents) is list and tuple(parents) == required_parents
                and merge.get("mainRef") == required_main_ref
                and merge.get("mainHeadSha") == merge_sha
                and pr.get("mergeCommitSha") == merge_sha
            ):
                return report(False, "", ["squash_merge_shape_or_tree_mismatch"])

            merged_at = aware(pr.get("mergedAt"))
            safety_completed_at = aware(safety.get("completedAt"))
            job_completed_at = aware(safety.get("jobCompletedAt"))
            fetched_at = aware(fetch.get("fetchedAt"))
            if not (
                merged_at is not None and safety_completed_at is not None
                and job_completed_at is not None and fetched_at is not None
                and safety_completed_at == job_completed_at
                and safety_completed_at < merged_at <= fetched_at
                and pr.get("mergedAt") == required_merged_at
                and fetch.get("evidenceClass") == "unverified_github_server_receipt_shape"
                and fetch.get("githubApiHostAlias") == "api.github.com"
                and type(fetch.get("apiResponseDigest")) is str
                and hex64.fullmatch(fetch["apiResponseDigest"])
                and fetch.get("fetcherPolicyVersion") == "controlled-github-main-receipt-fetch-v1-unregistered"
            ):
                return report(False, "", ["timestamp_or_fetch_shape_invalid"])

            claimed_digest = value.get("receiptDigest")
            projection = {key: child for key, child in value.items() if key != "receiptDigest"}
            expected_digest = sha256(canonical(projection)).hexdigest()
            if type(claimed_digest) is not str or claimed_digest != expected_digest:
                return report(False, "", ["receipt_digest_mismatch"])
            return report(True, expected_digest, [])
        except Exception:
            return report(False, "", ["input_fail_closed"])

    def run(value: Any, *, enabled: bool = False) -> dict[str, Any]:
        if enabled is not True:
            return {
                "schemaVersion": schema_version,
                "policyVersion": policy_version,
                "mode": "disabled",
                "runtimeTrustBoundary": (
                    "same_process_arbitrary_code_execution_trace_and_debugger_hooks_out_of_scope"
                ),
                "authoritativeVerificationRequirement": (
                    "isolated_process_and_authenticated_external_github_receipt_required"
                ),
                "receiptStructurallyValid": False,
                "serverReceiptAuthenticated": False,
                "sealed": False,
                "confirmatoryDataAccessEligible": False,
                "strategyValidated": False,
                "adviceEnabled": False,
                "tradingEnabled": False,
            }
        return evaluate(value)

    return expectation, evaluate, run


expectation, evaluate, run = _factory()

EXPECTATION_HASH = digest(expectation())

del _factory
del _expectation_definition
