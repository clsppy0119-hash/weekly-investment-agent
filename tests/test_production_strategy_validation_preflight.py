import copy
import hashlib
import json
import ast
import tempfile
import unittest
from pathlib import Path

import candidate_manifest
import scoring
import production_strategy_validation_preflight as preflight


ROOT = Path(__file__).resolve().parent.parent
DECISION = "2026-08-12T14:00:00+08:00"


def record(entity, requirement, *, status="observed_as_of", available=DECISION):
    policy = preflight.REQUIREMENT_POLICIES[requirement]
    contract_name = policy["authorityContract"]
    authority = preflight.AUTHORITY_CONTRACTS[contract_name]
    return {
        "entityId": entity,
        "requirementId": requirement,
        "status": status,
        "source": authority["source"],
        "dataset": authority["dataset"],
        "schemaVersion": 1,
        "effectiveDate": "2026-08-12",
        "availableAt": available,
        "evidenceHash": hashlib.sha256(f"{entity}:{requirement}".encode()).hexdigest(),
        "quality": "verified",
        "conflictStatus": "no_conflict",
        "sourceRevision": "revision-1",
        "authorityContractHash": preflight.authority_contract_hash(contract_name),
        "producerHash": preflight.EVIDENCE_PRODUCER_PINS[authority["producer"]],
        "evidenceRole": policy["states"].get(status, "unresolved"),
    }


def payload(entities=("2330", "2454")):
    entity_list = list(entities)
    return {
        "schemaVersion": 1,
        "snapshots": [{
            "decisionAsOf": DECISION,
            "expectedEntities": entity_list,
            "universeEvidence": {
                "source": "TWSE-TPEx-official",
                "dataset": "historical-market-membership-v1",
                "schemaVersion": 1,
                "effectiveDate": "2026-08-12",
                "availableAt": DECISION,
                "evidenceHash": "c" * 64,
                "entitySetHash": preflight.digest(sorted(entity_list)),
                "expectedEntityCount": len(entity_list),
                "quality": "verified",
                "conflictStatus": "no_conflict",
                "sourceRevision": "revision-1",
                "authorityContractHash": preflight.authority_contract_hash("official_membership_v1"),
                "producerHash": preflight.EVIDENCE_PRODUCER_PINS["market_membership_snapshots.py"],
                "replayPolicyVersion": "lineage-replay-v1",
                "replayProducerHash": preflight.EVIDENCE_PRODUCER_PINS["lineage_replay.py"],
                "selectedVersionHash": "d" * 64,
            },
            "records": [
                record(entity, requirement)
                for entity in entities
                for requirement in preflight.REQUIRED_REQUIREMENTS
            ],
        }],
    }


def test_default_off_does_not_inspect_payload():
    class Explodes:
        def items(self):
            raise AssertionError("disabled mode inspected input")

    result = preflight.run(Explodes())
    assert result["mode"] == "disabled"
    assert result["strategyValidated"] is False


def test_complete_fixture_metadata_never_self_certifies_pit_or_strategy():
    result = preflight.run(payload(), enabled=True)
    expected = 2 * len(preflight.REQUIRED_REQUIREMENTS)
    assert result["coverage"]["expected"] == expected
    assert result["coverage"]["accepted"] == expected
    assert result["pitMetadataComplete"] is True
    assert result["provenanceMetadataComplete"] is True
    assert result["provenanceCoverageCertified"] is False
    assert result["scoringInputsMetadataComplete"] is True
    assert result["scoringInputsReady"] is False
    assert result["pitCertified"] is False
    assert result["dataReady"] is False
    assert result["strategyValidated"] is False
    assert result["promotionEligible"] is False
    assert result["mode"] == "research_only"


def test_current_production_and_backtest_contract_mismatch_is_explicit():
    result = preflight.evaluate(payload())
    assert result["fixtureParity"] is False
    assert result["engineContractParity"] is False
    assert result["productionBacktestParity"] is False
    assert result["selectionParityPolicyRegistered"] is True
    assert result["selectionParityPolicyVersion"] == "actual-comprehensive-selection-parity-v1"
    assert result["validationOutcomeAccountingStatus"] == "registered_for_measurement_only"
    assert result["eligiblePoolAccountingStatus"] == "registered_for_measurement_only"
    assert result["outcomeAccountingPolicyVersion"] == "actual-comprehensive-outcome-accounting-v1"
    assert "selection_parity_evidence_not_supplied_to_preflight" in result["blockers"]
    assert "production_backtest_engine_mismatch" in result["blockers"]


def test_strategy_hash_is_canonical_and_changes_with_any_rule():
    spec = preflight.strategy_spec()
    reordered = json.loads(json.dumps(spec, sort_keys=False))
    assert preflight.digest(spec) == preflight.digest(reordered)
    mutations = []
    for path, value in (
        (("selection", "minimumScore"), 61),
        (("selection", "previewPicks"), 4),
        (("selection", "sort"), ["score:desc", "code:asc"]),
        (("selection", "scoreRounding"), "half-up"),
        (("selection", "continuousTrend"), True),
        (("sourcePins", "scoring.py"), "0" * 64),
        (("selection", "weights"), {**spec["selection"]["weights"], "eps": 13}),
    ):
        changed = copy.deepcopy(spec)
        changed[path[0]][path[1]] = value
        mutations.append(changed)
    version_changed = copy.deepcopy(spec)
    version_changed["strategyTrackerVersion"] = "2.1"
    mutations.append(version_changed)
    assert all(preflight.digest(item) != preflight.strategy_spec_hash() for item in mutations)


def test_registered_manifest_semantics_are_exercised_by_production_functions():
    codes = ("7777", "6666", "5555", "4444")
    quotes = {
        code: {
            "name": code, "price": 100.0, "volume": 100 * (4 - index),
            "change": 1.0, "ma5": 99.0, "ma20": 98.0,
        }
        for index, code in enumerate(codes)
    }
    fundamentals = {
        code: {
            "revenueYoY": 15.0, "eps": 10.0, "roe": 20.0,
            "debtRatio": 25.0, "pe": 15.0, "pb": 2.0,
            "dividendYield": 3.0,
            "financialHistoryYears": 4 if code == "7777" else 5,
        }
        for code in codes
    }
    ranked = scoring.candidates("comprehensive", quotes, fundamentals, picks=None)
    assert [item[2] for item in ranked] == list(codes)
    preview_top_three = ranked[:3]

    quote_data = {
        "updatedAt": DECISION,
        "quotes": quotes,
        "fundamentals": fundamentals,
        "provenance": {
            "quote": {
                "source": "TWSE official", "dataset": "official_market",
                "effectiveDate": "2026-08-12", "availableAt": DECISION,
                "ingestedAt": DECISION, "conflictStatus": "no_conflict",
            },
            "fundamentals": {
                "source": "MOPS official", "dataset": "financial_statements",
                "effectiveDate": "2026-08-12", "availableAt": DECISION,
                "ingestedAt": DECISION, "conflictStatus": "no_conflict",
            },
        },
    }
    actions = {
        "source": "FinMind", "dataset": "TaiwanStockDividendResult",
        "availableAt": DECISION, "updatedAt": DECISION,
        "conflictStatus": "no_conflict", "queried_codes": list(codes),
        "failures": {}, "period": {"start": "2025-01-01", "end": "2026-08-12"},
        "events": [],
    }
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        news = root / "news.json"
        action_path = root / "actions.json"
        gate = root / "gate.json"
        pit = root / "pit.json"
        news.write_text(json.dumps({"updatedAt": DECISION, "items": []}), encoding="utf-8")
        action_path.write_text(json.dumps(actions), encoding="utf-8")
        gate.write_text("{}", encoding="utf-8")
        pit.write_text(json.dumps({
            "certified": True, "generatedAt": DECISION, "availableAt": DECISION,
        }), encoding="utf-8")

        def manifest(phase, advice_enabled):
            return candidate_manifest.build_manifest(
                report_date="2026-08-12", report_mode="comprehensive", phase=phase,
                ranked={"comprehensive": preview_top_three}, quote_data=quote_data,
                advice_gate={
                    "status": "advice_candidate" if advice_enabled else "research_only",
                    "adviceEnabled": advice_enabled, "blockers": [],
                },
                actions=actions, news_path=news, actions_path=action_path,
                gate_path=gate, pit_path=pit,
            )

        assert manifest("preview", True)["eligibleCandidates"] == []
        assert manifest("final", False)["eligibleCandidates"] == []
        final = manifest("final", True)
        assert final["candidateOrder"] == ["7777", "6666", "5555"]
        assert [item["code"] for item in final["eligibleCandidates"]] == ["6666", "5555"]
        assert "4444" not in final["candidateOrder"]
        assert "fewer_than_five_financial_years" in final["previewCandidates"][0]["quality"]["blockers"]


def test_registered_source_pins_match_the_current_production_path():
    def canonical_source_hash(path):
        text = path.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    actual = {
        name: canonical_source_hash(ROOT / name)
        for name in preflight.SOURCE_PINS
    }
    assert actual == preflight.SOURCE_PINS
    producer_actual = {
        name: canonical_source_hash(ROOT / name)
        for name in preflight.EVIDENCE_PRODUCER_PINS
    }
    assert producer_actual == preflight.EVIDENCE_PRODUCER_PINS


def test_missing_entity_requirement_remains_in_fixed_denominator():
    value = payload()
    value["snapshots"][0]["records"].pop()
    result = preflight.evaluate(value)
    assert result["coverage"]["expected"] == 2 * len(preflight.REQUIRED_REQUIREMENTS)
    assert result["coverage"]["present"] == result["coverage"]["expected"] - 1
    assert result["pitCertified"] is False
    assert "pit_requirement_coverage_incomplete" in result["blockers"]


def test_missing_source_is_not_silently_removed():
    value = payload()
    item = value["snapshots"][0]["records"][0]
    item.update(status="source_missing", source="", dataset="", availableAt=None,
                effectiveDate=None, evidenceHash=None, quality="source_missing",
                conflictStatus="unknown", sourceRevision=None)
    result = preflight.evaluate(value)
    assert result["coverage"]["expected"] == 2 * len(preflight.REQUIRED_REQUIREMENTS)
    assert result["coverage"]["statusCounts"]["source_missing"] == 1
    assert "source_missing" in result["blockers"]
    assert result["pitCertified"] is False


def test_post_as_of_and_inferred_or_unknown_availability_fail_closed():
    for available in ("2026-08-13T09:00:00+08:00", None, "2026-08-12"):
        value = payload(("2330",))
        value["snapshots"][0]["records"][0]["availableAt"] = available
        result = preflight.evaluate(value)
        assert result["pitCertified"] is False
        assert any(code in result["blockers"] for code in ("post_as_of_evidence", "provenance_incomplete"))


def test_official_not_yet_published_is_valid_only_for_fundamentals():
    value = payload(("2330",))
    requirement = "fundamentals.eps"
    index = preflight.REQUIRED_REQUIREMENTS.index(requirement)
    value["snapshots"][0]["records"][index] = record(
        "2330", requirement, status="not_yet_published"
    )
    result = preflight.evaluate(value)
    assert result["pitMetadataComplete"] is True
    assert result["scoringInputsMetadataComplete"] is False
    assert result["scoringInputsReady"] is False
    assert result["pitCertified"] is False
    assert result["coverage"]["statusCounts"]["not_yet_published"] == 1

    invalid = payload(("2330",))
    invalid["snapshots"][0]["records"][0] = record(
        "2330", "market.membership", status="not_yet_published"
    )
    result = preflight.evaluate(invalid)
    assert result["pitCertified"] is False
    assert "status_not_allowed_for_requirement" in result["blockers"]


def test_universe_evidence_binds_the_fixed_denominator():
    value = payload()
    value["snapshots"][0]["expectedEntities"].pop()
    value["snapshots"][0]["records"] = [
        item for item in value["snapshots"][0]["records"] if item["entityId"] == "2330"
    ]
    result = preflight.evaluate(value)
    assert result["pitCertified"] is False
    assert "fixed_denominator_hash_mismatch" in result["blockers"]


def test_universe_evidence_must_be_official_as_of_and_conflict_free():
    for mutation in (
        {"availableAt": "2026-08-13T09:00:00+08:00"},
        {"availableAt": None},
        {"quality": "retrieved_only"},
        {"conflictStatus": "unresolved"},
    ):
        value = payload(("2330",))
        value["snapshots"][0]["universeEvidence"].update(mutation)
        result = preflight.evaluate(value)
        assert result["pitCertified"] is False
        assert "universe_evidence_not_certified" in result["blockers"]


def test_fake_authority_wrong_dataset_and_future_effective_date_fail_closed():
    mutations = (
        {"source": "not-official"},
        {"dataset": "made-up"},
        {"effectiveDate": "2099-01-01"},
        {"authorityContractHash": "0" * 64},
        {"producerHash": "0" * 64},
    )
    for mutation in mutations:
        value = payload(("2330",))
        value["snapshots"][0]["records"][0].update(mutation)
        result = preflight.evaluate(value)
        assert result["pitMetadataComplete"] is False
        assert result["pitCertified"] is False
        assert any(code in result["blockers"] for code in (
            "provenance_incomplete", "future_effective_evidence",
        ))


def test_required_quote_and_membership_cannot_be_generic_not_applicable():
    for requirement in ("market.membership", "quote.price", "quote.volume"):
        value = payload(("2330",))
        index = preflight.REQUIRED_REQUIREMENTS.index(requirement)
        value["snapshots"][0]["records"][index] = record(
            "2330", requirement, status="not_applicable"
        )
        result = preflight.evaluate(value)
        assert result["pitMetadataComplete"] is False
        assert "status_not_allowed_for_requirement" in result["blockers"]


def test_allowed_scalar_fields_reject_urls_secrets_and_invalid_entity_codes():
    for field, bad in (
        ("sourceRevision", "https://provider.test/item?token=secret"),
        ("sourceRevision", "token-secret"),
        ("entityId", "2330?token=secret"),
    ):
        value = payload(("2330",))
        value["snapshots"][0]["records"][0][field] = bad
        result = preflight.evaluate(value)
        assert result["pitMetadataComplete"] is False


def test_equivalent_decision_instants_cannot_be_duplicated_with_another_timezone():
    value = payload(("2330",))
    duplicate = copy.deepcopy(value["snapshots"][0])
    duplicate["decisionAsOf"] = "2026-08-12T06:00:00Z"
    value["snapshots"].append(duplicate)
    result = preflight.evaluate(value)
    assert "decision_as_of_invalid" in result["blockers"]
    assert result["pitMetadataComplete"] is False


def test_conflict_duplicate_and_scope_drift_are_separate_blockers():
    value = payload(("2330",))
    conflict = value["snapshots"][0]["records"][0]
    conflict["status"] = "conflict"
    conflict["conflictStatus"] = "unresolved"
    value["snapshots"][0]["records"].append(copy.deepcopy(value["snapshots"][0]["records"][1]))
    extra = copy.deepcopy(value["snapshots"][0]["records"][2])
    extra["entityId"] = "9999"
    value["snapshots"][0]["records"].append(extra)
    result = preflight.evaluate(value)
    assert "conflict_unresolved" in result["blockers"]
    assert "multiple_versions_without_unique_selection" in result["blockers"]
    assert "denominator_scope_drift" in result["blockers"]
    assert result["pitCertified"] is False


def test_caller_supplied_parity_claim_is_rejected():
    value = payload()
    value["parityEvidence"] = {"productionResultHash": "a" * 64, "backtestResultHash": "a" * 64}
    result = preflight.evaluate(value)
    assert result["fixtureParity"] is False
    assert "input_contract_invalid" in result["blockers"]


def test_performance_rank_and_raw_payloads_are_rejected():
    for forbidden in (
        {"return": 0.2}, {"score": 90}, {"rank": 1}, {"raw": [{"x": 1}]},
        {"url": "https://provider.invalid/private"}, {"token": "secret"},
    ):
        value = payload()
        value["snapshots"][0]["records"][0]["unexpected"] = forbidden
        result = preflight.evaluate(value)
        assert "input_contract_invalid" in result["blockers"]
        assert result["strategyValidated"] is False


def test_output_is_metadata_only_and_replay_is_deterministic():
    first = preflight.evaluate(payload())
    second = preflight.evaluate(copy.deepcopy(payload()))
    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in ("2330", "2454", "entryPrice", "score", "rank", "return"):
        assert forbidden not in encoded


def test_unregistered_execution_risk_and_pool_always_block():
    result = preflight.evaluate(payload())
    assert result["executionSpecStatus"] == "unregistered"
    assert result["riskPolicyStatus"] == "unregistered"
    assert result["eligiblePoolBenchmarkStatus"] == "unregistered"
    assert result["validationOutcomeAccountingStatus"] == "registered_for_measurement_only"
    assert result["eligiblePoolAccountingStatus"] == "registered_for_measurement_only"
    for blocker in (
        "execution_spec_unregistered", "risk_policy_unregistered",
        "eligible_pool_benchmark_unregistered",
    ):
        assert blocker in result["blockers"]


def test_module_has_no_formal_or_external_runtime_imports():
    tree = ast.parse((ROOT / "production_strategy_validation_preflight.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "backtest", "strategy_backtest", "investment_advice_gate", "daily_report",
        "candidate_manifest", "strategy_tracker", "requests", "urllib", "socket",
        "subprocess", "os", "supabase", "psycopg", "openai",
    })


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
