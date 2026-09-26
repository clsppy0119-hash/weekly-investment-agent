import ast
import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import unittest

import actual_comprehensive_main_seal_receipt as seal


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    expected = seal.expectation()
    receipt = {
        "schemaVersion": seal.SCHEMA_VERSION,
        "policyVersion": seal.POLICY_VERSION,
        "repository": copy.deepcopy(expected["repository"]),
        "pullRequest": {
            "number": expected["pullRequest"]["number"],
            "state": "MERGED",
            "isDraft": False,
            "baseRef": expected["pullRequest"]["baseRef"],
            "baseSha": expected["pullRequest"]["baseSha"],
            "headRef": expected["pullRequest"]["headRef"],
            "headSha": expected["pullRequest"]["headSha"],
            "headRepositoryId": expected["pullRequest"]["headRepositoryId"],
            "mergedAt": seal.SERVER_MERGED_AT,
            "mergeCommitSha": seal.MERGE_COMMIT_SHA,
        },
        "mergeCommit": {
            "sha": seal.MERGE_COMMIT_SHA,
            "treeSha": expected["mergeCommit"]["requiredTreeSha"],
            "parents": copy.deepcopy(expected["mergeCommit"]["requiredParents"]),
            "mainRef": expected["mergeCommit"]["requiredMainRef"],
            "mainHeadSha": seal.MERGE_COMMIT_SHA,
        },
        "safetyRun": copy.deepcopy(expected["safetyRun"]),
        "node57Bindings": copy.deepcopy(expected["node57Bindings"]),
        "controlledFetch": {
            "evidenceClass": "unverified_github_server_receipt_shape",
            "githubApiHostAlias": "api.github.com",
            "apiResponseDigest": "b" * 64,
            "fetcherPolicyVersion": "controlled-github-main-receipt-fetch-v1-unregistered",
            "fetchedAt": "2026-08-13T14:58:00Z",
        },
        "receiptDigest": "",
    }
    rehash(receipt)
    return receipt


def rehash(value):
    value["receiptDigest"] = seal.digest(
        {key: child for key, child in value.items() if key != "receiptDigest"}
    )


def test_complete_fixture_only_proves_shape_never_a_seal_or_formal_evidence():
    value = fixture()
    result = seal.evaluate(value)
    assert result["receiptStructurallyValid"] is True
    assert result["crossBindingsMatch"] is True
    assert result["receiptDigest"] == value["receiptDigest"]
    for key in (
        "serverReceiptAuthenticated", "mainMergeVerified",
        "githubSealIntegrityVerified", "decisionReceiptVerified",
        "policyDecisionRecorded", "sealed", "confirmatoryDataAccessEligible",
        "officialSourceAuthenticated", "pitCoverageCertified",
        "performanceEvaluated", "riskPolicyPassed", "strategyValidated",
        "promotionEligible", "adviceEnabled", "formalGateAttached",
        "notificationEnabled", "tradingEnabled",
    ):
        assert result[key] is False
    assert set(seal.FIXED_BLOCKERS).issubset(result["blockers"])


def test_default_off_never_inspects_payload_and_public_apis_are_closed():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    class BoolExplodes:
        def __bool__(self):
            raise AssertionError("enabled truthiness was inspected")

    expected_disabled = {
        "schemaVersion": 1,
        "policyVersion": seal.POLICY_VERSION,
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
    assert seal.run(Explodes(), enabled=False) == expected_disabled
    for alias in (1, 1.0, float("nan"), "true", "false", [], object(), BoolExplodes()):
        assert seal.run(Explodes(), enabled=alias) == expected_disabled
    assert not inspect.signature(seal.expectation).parameters
    assert tuple(inspect.signature(seal.evaluate).parameters) == ("value",)
    assert tuple(inspect.signature(seal.run).parameters) == ("value", "enabled")
    for attack in (
        lambda: seal.expectation({"sealed": True}),
        lambda: seal.evaluate({}, _expected={}),
        lambda: seal.run({}, enabled=True, _evaluate=lambda _: {"sealed": True}),
    ):
        with unittest.TestCase().assertRaises(TypeError):
            attack()


def test_expectation_pins_exact_node57_tree_files_policy_and_safety_run():
    expected = seal.expectation()
    assert seal.EXPECTATION_HASH == seal.digest(expected)
    assert expected["repository"] == {
        "repositoryId": 1314663807,
        "ownerId": 307921499,
        "fullName": "clsppy0119-hash/weekly-investment-agent",
        "visibility": "public",
    }
    assert expected["pullRequest"]["number"] == 118
    assert expected["pullRequest"]["baseSha"] == seal.REVIEWED_BASE_MAIN_SHA
    assert expected["pullRequest"]["headSha"] == seal.REVIEWED_HEAD_SHA
    assert expected["pullRequest"]["requiredMergedAt"] == seal.SERVER_MERGED_AT
    assert expected["mergeCommit"]["requiredSha"] == seal.MERGE_COMMIT_SHA
    assert expected["mergeCommit"]["requiredTreeSha"] == seal.REVIEWED_TREE_SHA
    assert expected["mergeCommit"]["requiredParents"] == [seal.MERGE_PARENT_SHA]
    boundary = expected["evidenceBoundary"]
    assert boundary["runtimeTrustBoundary"] == (
        "same_process_arbitrary_code_execution_trace_and_debugger_hooks_out_of_scope"
    )
    assert boundary["authoritativeVerificationRequirement"] == (
        "isolated_process_and_authenticated_external_github_receipt_required"
    )
    assert expected["mergeCommit"]["mergeMethod"] == "squash"
    assert expected["safetyRun"]["runId"] == 31705383828
    assert expected["safetyRun"]["jobId"] == 94464501553
    assert expected["safetyRun"]["runAttempt"] == 1
    assert expected["safetyRun"]["workflowId"] == 328269653
    assert expected["safetyRun"]["checkSuiteId"] == 86010068679
    assert expected["safetyRun"]["pullRequestAssociationCount"] == 0

    bindings = expected["node57Bindings"]
    merge_tree = subprocess.check_output(
        ["git", "rev-parse", f"{seal.MERGE_COMMIT_SHA}^{{tree}}"],
        cwd=ROOT,
        text=True,
    ).strip()
    merge_parents = subprocess.check_output(
        ["git", "show", "-s", "--format=%P", seal.MERGE_COMMIT_SHA],
        cwd=ROOT,
        text=True,
    ).split()
    assert merge_tree == seal.REVIEWED_TREE_SHA
    assert merge_parents == [seal.MERGE_PARENT_SHA]
    assert seal.MERGE_COMMIT_SHA not in (
        seal.REVIEWED_HEAD_SHA,
        seal.REVIEWED_BASE_MAIN_SHA,
    )
    assert bindings["preregistrationHash"] == seal.PREREGISTRATION_HASH
    assert bindings["decisionReceiptCommitmentHash"] == (
        seal.DECISION_RECEIPT_COMMITMENT_HASH
    )
    assert bindings["sourcePinSetHash"] == seal.SOURCE_PIN_SET_HASH
    assert bindings["strategySpecHash"] == seal.STRATEGY_SPEC_HASH
    assert bindings["pitRequirementsHash"] == seal.PIT_REQUIREMENTS_HASH

    actual = []
    for item in bindings["filePins"]:
        data = subprocess.check_output(
            ["git", "show", f"{seal.MERGE_COMMIT_SHA}:{item['path']}"],
            cwd=ROOT,
        )
        sha256 = hashlib.sha256(data).hexdigest()
        git_blob = hashlib.sha1(
            b"blob " + str(len(data)).encode("ascii") + b"\0" + data
        ).hexdigest()
        actual.append((item["path"], sha256, git_blob))
    assert tuple(actual) == seal.EXPECTED_FILE_PINS


def test_wrong_repo_fork_pr_base_head_or_draft_state_fails_even_if_rehashed():
    mutations = (
        ("repository", "repositoryId", 1),
        ("repository", "ownerId", 1),
        ("repository", "fullName", "fork/repository"),
        ("pullRequest", "number", 119),
        ("pullRequest", "baseRef", "refs/heads/dev"),
        ("pullRequest", "baseSha", "c" * 40),
        ("pullRequest", "headRef", "fork-branch"),
        ("pullRequest", "headSha", "c" * 40),
        ("pullRequest", "headRepositoryId", 1),
        ("pullRequest", "state", "OPEN"),
        ("pullRequest", "isDraft", True),
    )
    for section, key, replacement in mutations:
        value = fixture()
        value[section][key] = replacement
        rehash(value)
        result = seal.evaluate(value)
        assert result["receiptStructurallyValid"] is False
        assert result["mainMergeVerified"] is False
        assert result["sealed"] is False


def test_open_pr_with_squash_equivalent_main_tree_is_not_merged():
    value = fixture()
    value["pullRequest"]["state"] = "OPEN"
    value["pullRequest"]["mergedAt"] = None
    value["pullRequest"]["mergeCommitSha"] = None
    value["mergeCommit"]["sha"] = seal.MERGE_COMMIT_SHA
    value["mergeCommit"]["mainHeadSha"] = value["mergeCommit"]["sha"]
    rehash(value)
    result = seal.evaluate(value)
    assert result["receiptStructurallyValid"] is False
    assert result["mainMergeVerified"] is False
    assert result["sealed"] is False


def test_squash_merge_tree_parent_and_main_head_are_exact():
    mutations = (
        ("sha", seal.REVIEWED_HEAD_SHA),
        ("sha", seal.REVIEWED_BASE_MAIN_SHA),
        ("sha", "z" * 40),
        ("treeSha", "c" * 40),
        ("parents", []),
        ("parents", [seal.REVIEWED_BASE_MAIN_SHA, "c" * 40]),
        ("parents", ["c" * 40]),
        ("mainRef", "refs/heads/dev"),
        ("mainHeadSha", "c" * 40),
    )
    for key, replacement in mutations:
        value = fixture()
        value["mergeCommit"][key] = replacement
        rehash(value)
        result = seal.evaluate(value)
        assert result["receiptStructurallyValid"] is False
        assert "squash_merge_shape_or_tree_mismatch" in result["blockers"]

    value = fixture()
    value["pullRequest"]["mergeCommitSha"] = "c" * 40
    rehash(value)
    assert "squash_merge_shape_or_tree_mismatch" in seal.evaluate(value)["blockers"]


def test_safety_check_must_be_the_exact_successful_run_for_reviewed_head():
    mutations = (
        ("workflowPath", ".github/workflows/other.yml"),
        ("workflowName", "Other"),
        ("workflowFileSha256", "c" * 64),
        ("workflowGitBlobSha1", "c" * 40),
        ("runId", 1),
        ("runAttempt", 2),
        ("event", "push"),
        ("headSha", "c" * 40),
        ("headBranch", "fork"),
        ("status", "queued"),
        ("conclusion", "failure"),
        ("jobId", 1),
        ("jobName", "other"),
        ("jobStatus", "in_progress"),
        ("jobConclusion", "cancelled"),
        ("workflowId", 1),
        ("checkSuiteId", 1),
        ("repositoryId", 1),
        ("headRepositoryId", 1),
        ("pullRequestAssociationCount", 1),
        ("associationEvidenceClass", "self_asserted_pr_link"),
    )
    for key, replacement in mutations:
        value = fixture()
        value["safetyRun"][key] = replacement
        rehash(value)
        result = seal.evaluate(value)
        assert result["receiptStructurallyValid"] is False
        assert "reviewed_identity_or_pin_mismatch" in result["blockers"]


def test_node57_content_or_contract_pin_drift_fails_closed():
    scalar_fields = (
        "reviewedHeadSha", "reviewedTreeSha", "preregistrationPath",
        "preregistrationHash", "decisionReceiptCommitmentHash",
        "sourcePinSetHash", "strategySpecHash", "pitRequirementsHash",
    )
    for key in scalar_fields:
        value = fixture()
        value["node57Bindings"][key] = "c" * 64 if key.endswith("Hash") else "changed"
        rehash(value)
        assert seal.evaluate(value)["receiptStructurallyValid"] is False
    for key, replacement in (
        ("path", "other.py"), ("sha256", "c" * 64),
        ("gitBlobSha1", "c" * 40),
    ):
        value = fixture()
        value["node57Bindings"]["filePins"][0][key] = replacement
        rehash(value)
        assert seal.evaluate(value)["receiptStructurallyValid"] is False


def test_timestamps_are_utc_ordered_and_fetch_time_is_never_a_seal_time():
    mutations = (
        ("pullRequest", "mergedAt", "2026-08-13"),
        ("pullRequest", "mergedAt", "2026-08-13T14:00:00"),
        ("pullRequest", "mergedAt", "2026-08-13T14:00:00+08:00"),
        ("pullRequest", "mergedAt", seal.SAFETY_JOB_COMPLETED_AT),
        ("pullRequest", "mergedAt", "2026-08-13T13:31:00Z"),
        ("controlledFetch", "fetchedAt", "2026-08-13T13:59:59Z"),
        ("controlledFetch", "fetchedAt", "2026-08-13T14:01:00"),
        ("controlledFetch", "evidenceClass", "authenticated"),
        ("controlledFetch", "githubApiHostAlias", "example.invalid"),
        ("controlledFetch", "apiResponseDigest", "not-a-hash"),
        ("controlledFetch", "fetcherPolicyVersion", "trusted"),
    )
    for section, key, replacement in mutations:
        value = fixture()
        value[section][key] = replacement
        rehash(value)
        result = seal.evaluate(value)
        assert result["receiptStructurallyValid"] is False
        assert result["serverReceiptAuthenticated"] is False


def test_caller_rehash_and_self_asserted_true_flags_never_authenticate_or_seal():
    value = fixture()
    value["serverReceiptAuthenticated"] = True
    value["sealed"] = True
    value["adviceEnabled"] = True
    rehash(value)
    result = seal.evaluate(value)
    assert result["receiptStructurallyValid"] is False
    for key in (
        "serverReceiptAuthenticated", "mainMergeVerified", "sealed",
        "confirmatoryDataAccessEligible", "strategyValidated", "adviceEnabled",
        "tradingEnabled",
    ):
        assert result[key] is False

    structurally_valid = seal.evaluate(fixture())
    assert structurally_valid["receiptStructurallyValid"] is True
    assert structurally_valid["serverReceiptAuthenticated"] is False
    assert structurally_valid["mainMergeVerified"] is False
    assert structurally_valid["sealed"] is False


def test_receipt_digest_exact_types_extra_fields_and_aliases_fail_closed():
    value = fixture()
    value["receiptDigest"] = "c" * 64
    assert "receipt_digest_mismatch" in seal.evaluate(value)["blockers"]

    for section, key, replacement in (
        (None, "schemaVersion", 1.0),
        ("repository", "repositoryId", float(seal.REPOSITORY_ID)),
        ("pullRequest", "isDraft", 0),
        ("safetyRun", "runAttempt", 1.0),
        ("safetyRun", "checkSuiteId", float(seal.SAFETY_CHECK_SUITE_ID)),
    ):
        value = fixture()
        target = value if section is None else value[section]
        target[key] = replacement
        rehash(value)
        assert seal.evaluate(value)["receiptStructurallyValid"] is False

    for section, key, replacement in (
        (None, "retrievedAt", "2026-08-13T14:01:00Z"),
        ("pullRequest", "serverVerified", True),
        ("controlledFetch", "signatureVerified", True),
        ("node57Bindings", "availableAt", "2026-08-13T14:00:00Z"),
    ):
        value = fixture()
        target = value if section is None else value[section]
        target[key] = replacement
        rehash(value)
        assert seal.evaluate(value)["receiptStructurallyValid"] is False


def test_malformed_oversize_sensitive_performance_and_hostile_inputs_fail_closed():
    cycle = []
    cycle.append(cycle)

    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    class BadList(list):
        def __iter__(self):
            raise RuntimeError("boom")

    for item in (
        cycle, {"x": float("nan")}, {"x": 10**10000}, {1: "bad"},
        {"x": "a" * (seal.MAX_STRING + 1)}, BadDict(), BadList(), None, "x",
    ):
        result = seal.evaluate(item)
        assert result["receiptStructurallyValid"] is False
        assert result["sealed"] is False

    for key, unsafe in (
        ("raw_payload", {"x": 1}), ("apiToken", "SECRET"),
        ("authorization_value", "SECRET"), ("url_value", "https://example.invalid"),
        ("price_series", [1.0]), ("observedReturn", 0.5),
        ("strategyValidated", True),
    ):
        value = fixture()
        value["controlledFetch"][key] = unsafe
        rehash(value)
        result = seal.evaluate(value)
        assert result["receiptStructurallyValid"] is False
        assert result["adviceEnabled"] is False


def test_global_name_rebinding_cannot_change_the_frozen_expectation():
    expected = seal.expectation()
    valid = fixture()
    originals = {
        "REPOSITORY": seal.REPOSITORY,
        "REVIEWED_HEAD_SHA": seal.REVIEWED_HEAD_SHA,
        "PREREGISTRATION_HASH": seal.PREREGISTRATION_HASH,
        "_canonical": seal._canonical,
        "_bounded_json": seal._bounded_json,
        "_contains_forbidden": seal._contains_forbidden,
        "hashlib": seal.hashlib,
        "ROOT_KEYS": seal.ROOT_KEYS,
        "REPOSITORY_KEYS": seal.REPOSITORY_KEYS,
        "PR_KEYS": seal.PR_KEYS,
        "MERGE_KEYS": seal.MERGE_KEYS,
        "SAFETY_KEYS": seal.SAFETY_KEYS,
        "BINDING_KEYS": seal.BINDING_KEYS,
        "FILE_PIN_KEYS": seal.FILE_PIN_KEYS,
        "FETCH_KEYS": seal.FETCH_KEYS,
    }
    try:
        seal.REPOSITORY = "fork/repo"
        seal.REVIEWED_HEAD_SHA = "c" * 40
        seal.PREREGISTRATION_HASH = "c" * 64
        seal._canonical = lambda _: b"{}"
        seal._bounded_json = lambda _: True
        seal._contains_forbidden = lambda _: False
        class FakeHashlib:
            @staticmethod
            def sha256(*_args, **_kwargs):
                raise AssertionError("runtime hashlib mutation reached frozen verifier")
        seal.hashlib = FakeHashlib()
        seal.ROOT_KEYS = frozenset((*seal.ROOT_KEYS, "extra"))
        seal.REPOSITORY_KEYS = frozenset((*seal.REPOSITORY_KEYS, "extra"))
        seal.PR_KEYS = frozenset((*seal.PR_KEYS, "extra"))
        seal.MERGE_KEYS = frozenset((*seal.MERGE_KEYS, "extra"))
        seal.SAFETY_KEYS = frozenset((*seal.SAFETY_KEYS, "extra"))
        seal.BINDING_KEYS = frozenset((*seal.BINDING_KEYS, "extra"))
        seal.FILE_PIN_KEYS = frozenset((*seal.FILE_PIN_KEYS, "extra"))
        seal.FETCH_KEYS = frozenset((*seal.FETCH_KEYS, "extra"))
        assert seal.expectation() == expected
        assert seal.evaluate(valid)["receiptStructurallyValid"] is True
        forged = fixture()
        forged["repository"]["fullName"] = "fork/repo"
        rehash(forged)
        assert seal.evaluate(forged)["receiptStructurallyValid"] is False
    finally:
        for name, child in originals.items():
            setattr(seal, name, child)

    captured = [
        cell.cell_contents
        for function in (seal.expectation, seal.evaluate, seal.run)
        for cell in (function.__closure__ or ())
    ]
    assert not any(type(child) in (dict, list, set) for child in captured)


def test_output_is_sanitized_and_module_is_offline_and_formally_isolated():
    encoded = json.dumps(seal.evaluate(fixture()), sort_keys=True)
    for forbidden in (
        "https://", "apiResponseDigest", "mergedAt", "mainHeadSha",
        "headBranch", "filePins", "SECRET",
    ):
        assert forbidden not in encoded

    path = ROOT / "actual_comprehensive_main_seal_receipt.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "os", "subprocess", "requests", "urllib", "socket", "supabase",
        "psycopg", "openai", "scoring", "backtest", "strategy_backtest",
        "daily_report", "candidate_manifest", "investment_advice_gate",
        "promotion_status", "strategy_tracker",
    })
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source and "utcnow" not in source
    allowed = {
        path.resolve()
        for path in (
            ROOT / "actual_comprehensive_main_seal_receipt.py",
            ROOT / "tests" / "test_actual_comprehensive_main_seal_receipt.py",
        )
    }
    for candidate in ROOT.rglob("*.py"):
        if candidate.resolve() not in allowed:
            assert "actual_comprehensive_main_seal_receipt" not in (
                candidate.read_text(encoding="utf-8")
            )


def test_pipeline_safety_covers_node58a_without_oidc_attestation_or_secrets():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "actual_comprehensive_main_seal_receipt.py" in workflow
    assert "tests/**" in workflow
    assert "contents: read" in workflow
    assert workflow.count("fetch-depth: 0") == 1
    for forbidden in ("id-token: write", "attestations: write", "secrets."):
        assert forbidden not in workflow
    before_jobs, after_jobs = workflow.split("\njobs:\n", 1)
    assert before_jobs.rstrip().endswith("permissions:\n  contents: read")
    assert "\n  permissions:" not in after_jobs
    compile_step = after_jobs.split("- name: Compile changed Python modules", 1)[1]
    compile_step = compile_step.split("- name: Run deterministic safety tests", 1)[0]
    assert "actual_comprehensive_main_seal_receipt.py" in compile_step


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
