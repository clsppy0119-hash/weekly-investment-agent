import ast
import copy
import inspect
import json
from pathlib import Path
import unittest

import official_product_capability_matrix as matrix


ROOT = Path(__file__).resolve().parents[1]


def product(value, product_id):
    return next(item for item in value["products"] if item["productId"] == product_id)


def test_committed_matrix_is_deterministic_and_reports_real_technical_uses():
    value = matrix.artifact()
    result = matrix.evaluate(value)
    assert result["matrixStructurallyValid"] is True
    assert result["matrixHash"] == value["matrixHash"] == matrix.MATRIX_HASH
    assert value["matrixHash"] == matrix.digest(
        {key: child for key, child in value.items() if key != "matrixHash"}
    )
    assert value["populationPolicyHash"] == matrix.POPULATION_POLICY_HASH
    assert value["sourceAdmissionPolicyVersion"] == matrix.SOURCE_ADMISSION_POLICY_VERSION
    assert value["sourceAdmissionSourceHash"] == matrix.SOURCE_ADMISSION_SOURCE_HASH
    assert value["sourceAdmissionObligationMatrixHash"] == (
        matrix.SOURCE_ADMISSION_OBLIGATION_MATRIX_HASH
    )
    assert result["productCount"] == 11
    assert result["currentSnapshotEvidenceProductCount"] == 5
    assert result["supplementalEventEvidenceProductCount"] == 4
    assert result["zeroVolumeEvidenceProductCount"] == 0
    assert value == matrix.artifact()

    uses = value["derivedClassifications"]
    assert uses["currentSnapshotEvidenceMarkets"] == ["emerging", "tpex", "twse"]
    assert "twse-tranisin-daily-master-v1" in uses["currentSnapshotEvidenceProducts"]
    assert "tpex-s24-stkanou-event-file-v1" in uses[
        "supplementalMembershipEventEvidenceProducts"
    ]
    assert uses["zeroVolumePresenceEvidenceProducts"] == []


def test_default_off_never_inspects_input_and_public_signatures_are_closed():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    assert matrix.run(Explodes(), enabled=False) == {
        "schemaVersion": 1,
        "policyVersion": matrix.POLICY_VERSION,
        "mode": "disabled",
        "matrixStructurallyValid": False,
        "sourceAdmitted": False,
        "historicalEligible": False,
        "adviceEnabled": False,
        "tradingEnabled": False,
    }
    assert not inspect.signature(matrix.artifact).parameters
    assert tuple(inspect.signature(matrix.evaluate).parameters) == ("value",)
    assert tuple(inspect.signature(matrix.run).parameters) == ("value", "enabled")
    for attack in (
        lambda: matrix.artifact({"forged": True}),
        lambda: matrix.evaluate({}, _expected=b"{}"),
        lambda: matrix.run({}, enabled=True, _evaluate=lambda _: {"adviceEnabled": True}),
    ):
        with unittest.TestCase().assertRaises(TypeError):
            attack()


def test_current_snapshot_is_not_history_and_t48_events_are_not_transfer_or_identity():
    value = matrix.artifact()
    tranisin = product(value, "twse-tranisin-daily-master-v1")
    assert tranisin["axes"]["currentActiveSnapshot"] == matrix.PROVEN
    assert tranisin["axes"]["historyRange"] == matrix.CONFLICT
    assert tranisin["historicalPitCapable"] is False

    for product_id in (
        "twse-t48-listing-delisting-v1",
        "tpex-t48-listing-delisting-v1",
        "tpex-s24-stkanou-event-file-v1",
    ):
        item = product(value, product_id)
        assert item["axes"]["listingEvent"] == matrix.PROVEN
        assert item["axes"]["delistingEvent"] == matrix.PROVEN
        assert item["axes"]["transferLinkage"] == matrix.UNPROVEN
        assert item["axes"]["issuerIdentity"] == matrix.UNPROVEN
        assert item["axes"]["codeReuseLineage"] == matrix.UNPROVEN
        assert item["historicalPitCapable"] is False


def test_current_isin_or_company_id_never_becomes_historical_lineage():
    value = matrix.artifact()
    tranisin = product(value, "twse-tranisin-daily-master-v1")
    assert tranisin["axes"]["securityIdentity"] == matrix.PROVEN
    assert tranisin["axes"]["issuerIdentity"] == matrix.UNPROVEN
    for product_id in (
        "twse-openapi-current-company-master-v1",
        "tpex-openapi-current-otc-company-master-v1",
        "tpex-openapi-current-emerging-company-master-v1",
    ):
        item = product(value, product_id)
        assert item["axes"]["issuerIdentity"] == matrix.PROVEN
        assert item["axes"]["codeReuseLineage"] == matrix.UNPROVEN
        assert item["axes"]["revisionSupersedes"] == matrix.UNPROVEN
        assert item["historicalPitCapable"] is False


def test_known_sample_and_history_conflicts_are_preserved_fail_closed():
    value = matrix.artifact()
    for product_id in (
        "twse-tranisin-daily-master-v1",
        "twse-bft51u-daily-security-data-v1",
    ):
        assert product(value, product_id)["axes"]["historyRange"] == matrix.CONFLICT
    emerging = product(value, "tpex-e01-emerging-market-info-v1")
    assert emerging["axes"]["schemaSampleConsistency"] == matrix.CONFLICT
    assert emerging["historicalPitCapable"] is False
    assert value["derivedClassifications"]["historicalPitCapableProducts"] == []
    assert value["derivedClassifications"]["independentCustodyReadyProducts"] == []


def test_zero_close_rows_are_only_partial_presence_not_zero_volume_coverage():
    value = matrix.artifact()
    bft51u = product(value, "twse-bft51u-daily-security-data-v1")
    assert bft51u["axes"]["zeroVolumeCoverage"] == matrix.PARTIAL
    assert "zero_volume_presence_evidence" not in bft51u["technicalUses"]
    assert value["derivedClassifications"]["zeroVolumePresenceEvidenceProducts"] == []
    assert "zeroVolumeCoverage_partial" in bft51u["gapCodes"]


def test_legal_terms_and_cross_product_completion_never_self_admit():
    value = matrix.artifact()
    assert all(
        item["axes"]["privateRetention"] != matrix.PROVEN
        and item["axes"]["independentCustodian"] != matrix.PROVEN
        and item["axes"]["longTermReplay"] != matrix.PROVEN
        for item in value["products"]
    )
    assert value["derivedClassifications"]["crossProductJoinRule"] == (
        "no_implicit_axis_completion_across_products"
    )
    assert value["derivedClassifications"]["forwardObserverAdmissionReady"] is False
    assert value["derivedClassifications"]["historicalPitAdmissionReady"] is False
    assert value["derivedClassifications"]["independentCustodyReady"] is False
    result = matrix.evaluate(value)
    for key in (
        "sourceAdmitted", "historicalEligible", "pitCoverageCertified",
        "strategyValidated", "promotionEligible", "adviceEnabled",
        "formalGateAttached", "tradingEnabled",
    ):
        assert result[key] is False


def test_caller_mutation_and_rehash_cannot_change_the_reviewed_matrix():
    base = matrix.artifact()
    mutations = []

    changed = copy.deepcopy(base)
    changed["derivedClassifications"]["historicalPitAdmissionReady"] = True
    mutations.append(changed)

    changed = copy.deepcopy(base)
    item = product(changed, "twse-t48-listing-delisting-v1")
    for axis in matrix.AXES:
        item["axes"][axis] = matrix.PROVEN
    item["historicalPitCapable"] = True
    changed["derivedClassifications"]["historicalPitCapableProducts"] = [item["productId"]]
    mutations.append(changed)

    changed = copy.deepcopy(base)
    changed["products"][0]["evidenceDescriptorHash"] = "f" * 64
    mutations.append(changed)

    for changed in mutations:
        changed["matrixHash"] = matrix.digest(
            {key: value for key, value in changed.items() if key != "matrixHash"}
        )
        result = matrix.evaluate(changed)
        assert result["matrixStructurallyValid"] is False
        assert result["sourceAdmitted"] is False
        assert result["historicalEligible"] is False


def test_exact_json_types_and_runtime_global_mutation_cannot_change_trust_root():
    base = matrix.artifact()
    for path, replacement in (
        (("schemaVersion",), 1.0),
        (("derivedClassifications", "forwardObserverAdmissionReady"), 0),
        (("products", 0, "historicalPitCapable"), 0),
    ):
        changed = copy.deepcopy(base)
        cursor = changed
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = replacement
        assert matrix.evaluate(changed)["matrixStructurallyValid"] is False

    old_policy = matrix.POLICY_VERSION
    old_hash = matrix.POPULATION_POLICY_HASH
    old_bounded = matrix._json_domain
    old_forbidden = matrix._contains_forbidden
    try:
        matrix.POLICY_VERSION = "caller-policy"
        matrix.POPULATION_POLICY_HASH = "f" * 64
        matrix._json_domain = lambda _: True
        matrix._contains_forbidden = lambda _: False
        assert matrix.evaluate(base)["matrixStructurallyValid"] is True
        forged = copy.deepcopy(base)
        forged["policyVersion"] = "caller-policy"
        assert matrix.evaluate(forged)["matrixStructurallyValid"] is False
    finally:
        matrix.POLICY_VERSION = old_policy
        matrix.POPULATION_POLICY_HASH = old_hash
        matrix._json_domain = old_bounded
        matrix._contains_forbidden = old_forbidden

    captured = [
        cell.cell_contents
        for function in (matrix.artifact, matrix.evaluate, matrix.run)
        for cell in (function.__closure__ or ())
    ]
    assert not any(type(value) in (dict, list, set) for value in captured)


def test_hostile_oversize_sensitive_and_performance_inputs_fail_closed():
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
        {"x": "a" * (matrix.MAX_STRING + 1)}, BadDict(), BadList(),
    ):
        result = matrix.evaluate(item)
        assert result["matrixStructurallyValid"] is False
        assert result["sourceAdmitted"] is False

    for key, unsafe in (
        ("raw_payload", {"x": 1}), ("apiToken", "SECRET"),
        ("authorization_value", "SECRET"), ("url_value", "https://example.invalid"),
        ("price_series", [1.0]), ("observedReturn", 0.5),
        ("strategyValidated", True),
    ):
        changed = copy.deepcopy(matrix.artifact())
        changed["products"][0][key] = unsafe
        changed["matrixHash"] = matrix.digest(
            {name: value for name, value in changed.items() if name != "matrixHash"}
        )
        result = matrix.evaluate(changed)
        assert result["matrixStructurallyValid"] is False


def test_output_is_sanitized_and_has_no_evidence_urls_or_market_values():
    encoded = json.dumps(matrix.artifact(), sort_keys=True)
    for forbidden in (
        "https://", "token=", "price", "return", "outcome", "ticker",
        "2456", "3702A", "SECRET",
    ):
        assert forbidden.casefold() not in encoded.casefold()
    report = json.dumps(matrix.evaluate(matrix.artifact()), sort_keys=True)
    for forbidden in ("evidenceReferenceIds", "reviewedFactCodes", "axes"):
        assert forbidden not in report


def test_static_isolation_and_workflow_coverage():
    path = ROOT / "official_product_capability_matrix.py"
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
    for consumer in (
        "official_full_market_population.py",
        "official_population_source_admission.py",
        "authoritative_pit_coverage_certification.py",
        "daily_report.py",
        "candidate_manifest.py",
        "investment_advice_gate.py",
        "promotion_status.py",
    ):
        assert "official_product_capability_matrix" not in (
            ROOT / consumer
        ).read_text(encoding="utf-8")

    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "official_product_capability_matrix.py" in workflow
    assert "tests/**" in workflow
    assert "contents: read" in workflow
    for forbidden in ("id-token: write", "attestations: write", "secrets."):
        assert forbidden not in workflow


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
