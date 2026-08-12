import ast
import copy
import hashlib
import json
import unittest
from pathlib import Path

import authoritative_pit_coverage_certification as cert
import production_strategy_validation_preflight as preflight


ROOT = Path(__file__).resolve().parents[1]
DECISION = "2024-06-14T16:00:00+08:00"
ENTITIES = ["1101", "2330", "2454", "6147", "7777"]
MARKETS = {
    "1101": ("TWSE", "active", None),
    "2330": ("TWSE", "suspended", None),
    "2454": ("TWSE", "zero_volume", "2024-12-31T00:00:00+08:00"),
    "6147": ("TPEx", "active", None),
    "7777": ("emerging", "active", None),
}
COMPONENT_ENTITIES = {
    "twse_active": ["1101", "2330", "2454"],
    "tpex_active": ["6147"],
    "emerging_active": ["7777"],
    "membership_events": list(ENTITIES),
}
H64 = "a" * 64


def canonical_source_hash(path):
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def schedule():
    value = {
        "marketScope": list(cert.MARKET_SCOPE),
        "timezone": cert.TIMEZONE,
        "studyFrom": "2024-06-14",
        "studyTo": "2024-06-14",
        "decisionAsOfs": [DECISION],
        "decisionCalendarHash": "",
        "studyCalendarPolicyVersion": "fixture-study-calendar-v1",
        "studyCalendarRegistered": True,
    }
    value["decisionCalendarHash"] = cert.digest({
        "timezone": cert.TIMEZONE,
        "policyVersion": value["studyCalendarPolicyVersion"],
        "decisionAsOfs": [cert._instant(DECISION)],
    })
    return value


def membership(entity):
    market, status, exit_at = MARKETS[entity]
    component_id = {
        "TWSE": "twse_active", "TPEx": "tpex_active", "emerging": "emerging_active",
    }[market]
    return {
        "entityId": entity,
        "market": market,
        "status": status,
        "entryEffectiveAt": "2020-01-01T00:00:00+08:00",
        "exitEffectiveAt": exit_at,
        "marketComponentId": component_id,
        "eventComponentId": "membership_events",
        "selectedVersionHash": cert.digest({"membership": entity, "decision": DECISION}),
    }


def component(component_id, frozen_digest="0" * 64, replay_digest="0" * 64):
    entities = COMPONENT_ENTITIES[component_id]
    return {
        "componentId": component_id,
        "market": cert.COMPONENT_MARKETS[component_id],
        "source": "official-population-fixture",
        "dataset": f"{component_id}-fixture-v1",
        "schemaVersion": 1,
        "effectiveAt": "2024-06-14T00:00:00+08:00",
        "availableAt": "2024-06-14T15:00:00+08:00",
        "availableAtEvidenceClass": "official_timezone_timestamp",
        "availableAtEvidenceId": f"{component_id}-publication-20240614",
        "sourceRevision": "official-20240614-v1",
        "producer": "fixture-official-population-producer-v1",
        "producerHash": cert.digest({"producer": component_id}),
        "contentHash": cert.digest({"content": component_id, "entities": entities}),
        "schemaHash": cert.digest({"schema": component_id}),
        "evidenceHash": cert.digest({"evidence": component_id}),
        "selectedVersionHash": cert.digest({"selected": component_id}),
        "entityCount": len(entities),
        "entitySetHash": cert.digest(entities),
        "entities": list(entities),
        "quality": "verified",
        "conflictStatus": "no_conflict",
        "frozenDigest": frozen_digest,
        "replayDigest": replay_digest,
    }


def record(entity, requirement, selected_hash, frozen_digest="0" * 64,
           replay_digest="0" * 64, status="observed_as_of"):
    policy = preflight.REQUIREMENT_POLICIES[requirement]
    contract_name = policy["authorityContract"]
    authority = preflight.AUTHORITY_CONTRACTS[contract_name]
    return {
        "decisionAsOf": DECISION,
        "entityId": entity,
        "requirementId": requirement,
        "status": status,
        "source": authority["source"],
        "dataset": authority["dataset"],
        "schemaVersion": 1,
        "effectiveAt": "2024-06-14T00:00:00+08:00",
        "availableAt": "2024-06-14T15:00:00+08:00",
        "availableAtEvidenceClass": "official_timezone_timestamp",
        "availableAtEvidenceId": "official-evidence-20240614",
        "evidenceHash": cert.digest({"e": entity, "r": requirement, "kind": "evidence"}),
        "authorityContractHash": preflight.authority_contract_hash(contract_name),
        "producer": authority["producer"],
        "producerHash": cert.CERTIFICATION_PRODUCER_PINS[authority["producer"]],
        "sourceRevision": "official-20240614-v1",
        "contentHash": cert.digest({"e": entity, "r": requirement, "kind": "content"}),
        "schemaHash": "7" * 64,
        "quality": "verified",
        "conflictStatus": "no_conflict",
        "evidenceRole": policy["states"][status],
        "selectedVersionHash": selected_hash,
        "frozenDigest": frozen_digest,
        "replayDigest": replay_digest,
    }


def bundle():
    memberships = [membership(entity) for entity in ENTITIES]
    components = [component(component_id) for component_id in cert.REQUIRED_COMPONENTS]
    component_set = [
        {
            "componentId": row["componentId"],
            "entityCount": row["entityCount"],
            "entitySetHash": row["entitySetHash"],
            "selectedVersionHash": row["selectedVersionHash"],
        }
        for row in sorted(components, key=lambda item: item["componentId"])
    ]
    component_set_hash = cert.digest(component_set)
    selected = [
        cert.digest({"decision": DECISION, "entity": entity, "requirement": requirement})
        for entity in ENTITIES
        for requirement in preflight.REQUIRED_REQUIREMENTS
    ]
    records = []
    position = 0
    for entity in ENTITIES:
        for requirement in preflight.REQUIRED_REQUIREMENTS:
            records.append(record(entity, requirement, selected[position]))
            position += 1
    coverage_matrix = [
        {
            "decisionAsOf": cert._instant(row["decisionAsOf"]),
            "entityId": row["entityId"],
            "requirementId": row["requirementId"],
            "selectedVersionHash": row["selectedVersionHash"],
        }
        for row in sorted(records, key=lambda item: (
            cert._instant(item["decisionAsOf"]), item["entityId"], item["requirementId"],
        ))
    ]
    coverage_matrix_hash = cert.digest(coverage_matrix)
    frozen_summary = {
        "schemaVersion": 1,
        "policyVersion": "frozen-lineage-v1",
        "decisionCalendarHash": schedule()["decisionCalendarHash"],
        "populationPolicyHash": cert.population_policy_hash(),
        "componentSetHash": cert.digest([component_set_hash]),
        "coverageMatrixHash": coverage_matrix_hash,
    }
    frozen_digest = cert.digest(frozen_summary)
    selected_set_hash = cert.digest(sorted([
        *(row["selectedVersionHash"] for row in memberships),
        *(row["selectedVersionHash"] for row in records),
    ]))
    replay_summary = {
        "schemaVersion": 1,
        "policyVersion": "lineage-replay-v1",
        "frozenDigest": frozen_digest,
        "selectedVersionSetHash": selected_set_hash,
        "coverageMatrixHash": coverage_matrix_hash,
    }
    replay_digest = cert.digest(replay_summary)
    availability_summary = {
        "schemaVersion": 1,
        "policyVersion": "official-availability-capability-v1",
        "acceptedEvidenceClasses": sorted(cert.OFFICIAL_AVAILABLE_CLASSES),
        "evidenceSetHash": cert.digest(sorted(row["evidenceHash"] for row in records)),
    }
    for row in [*components, *records]:
        row["frozenDigest"] = frozen_digest
        row["replayDigest"] = replay_digest
    universe = {
        "decisionAsOf": DECISION,
        "schemaVersion": 1,
        "populationPolicyVersion": cert.POPULATION_POLICY_VERSION,
        "populationPolicyHash": cert.population_policy_hash(),
        "components": components,
        "componentSetHash": component_set_hash,
        "memberships": memberships,
        "entities": list(ENTITIES),
        "entitySetHash": cert.digest(ENTITIES),
        "entityCount": len(ENTITIES),
    }
    return {
        "schemaVersion": 1,
        "policyVersion": cert.POLICY_VERSION,
        "strategySpecHash": preflight.strategy_spec_hash(),
        "pitRequirementsHash": cert.pit_requirements_hash(),
        "populationPolicyHash": cert.population_policy_hash(),
        "producerPins": dict(cert.CERTIFICATION_PRODUCER_PINS),
        "scope": schedule(),
        "universes": [universe],
        "records": records,
        "lineage": {
            "schemaVersion": 1,
            "frozenSummary": frozen_summary,
            "frozenDigest": frozen_digest,
            "replaySummary": replay_summary,
            "replayDigest": replay_digest,
            "availabilitySummary": availability_summary,
            "availabilityDigest": cert.digest(availability_summary),
            "selectedVersionSetHash": selected_set_hash,
        },
    }


def test_complete_fixture_only_proves_structure_never_certification():
    result = cert.assess_structure(bundle())
    assert result["structuralCoverageComplete"] is True
    assert result["scoringInputsMetadataComplete"] is True
    assert result["coverage"]["expected"] == len(ENTITIES) * 19
    assert result["coverage"]["selected"] == len(ENTITIES) * 19
    assert result["coverage"]["coverageRate"] == 1.0
    for field in (
        "pitCoverageCertified", "authoritativeProvenanceCoverageCertified",
        "scoringInputsReady", "valuesCertified", "strategyValidated",
        "promotionEligible", "adviceEnabled", "readyForPerformanceEvaluation",
        "formalGateAttached",
    ):
        assert result[field] is False


def test_no_helper_accepts_a_caller_anchor_or_can_certify():
    result = cert.assess_structure(bundle())
    assert result["pitCoverageCertified"] is False
    try:
        cert.assess_structure(bundle(), {"caller": "anchor"})
    except TypeError:
        pass
    else:
        raise AssertionError("caller anchor unexpectedly accepted")


def test_committed_registry_is_empty_and_public_evaluation_fails_closed():
    registry = json.loads((ROOT / "trusted_pit_bundle_registry_v1.json").read_text(encoding="utf-8"))
    assert registry == {
        "entries": [], "policyVersion": cert.REGISTRY_POLICY_VERSION, "schemaVersion": 1,
    }
    result = cert.run(bundle(), enabled=True)
    assert result["pitCoverageCertified"] is False
    assert "trusted_root_registry_empty" in result["blockers"]
    assert "registry_append_only_admission_unimplemented" in result["blockers"]
    assert "official_universe_producer_unregistered" in result["blockers"]
    assert "trusted_artifact_attestation_unimplemented" in result["blockers"]
    assert cert.OFFICIAL_UNIVERSE_PRODUCER_ALLOWLIST == frozenset()


def test_default_off_does_not_inspect_bundle_or_registry():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    original = cert.REGISTRY_PATH
    cert.REGISTRY_PATH = Explodes()
    try:
        assert cert.run(Explodes(), enabled=False) == {
            "schemaVersion": 1,
            "policyVersion": cert.POLICY_VERSION,
            "mode": "disabled",
            "pitCoverageCertified": False,
            "strategyValidated": False,
            "promotionEligible": False,
        }
    finally:
        cert.REGISTRY_PATH = original


def test_malformed_input_always_fails_closed_without_crashing():
    cycle = []
    cycle.append(cycle)
    malformed = [
        {"x": float("nan")}, {"x": {1, 2}}, cycle,
        {"x": {1: "non-string-key"}},
    ]
    value = bundle()
    value["records"][0]["entityId"] = []
    malformed.append(value)
    value = bundle()
    value["universes"][0]["entities"][0] = {"bad": "type"}
    malformed.append(value)
    for item in malformed:
        result = cert.run(item, enabled=True)
        assert result["pitCoverageCertified"] is False
        assert result["strategyValidated"] is False
        assert result["blockers"]


def test_existing_candidate_or_aggregate_artifact_is_ineligible():
    for legacy in (
        {"schemaVersion": 1, "contract": {"certified": True}, "records": []},
        {"schemaVersion": 1, "candidateOrder": ["2330"], "evidenceHash": H64},
    ):
        result = cert.run(legacy, enabled=True)
        assert result["pitCoverageCertified"] is False
        assert "bundle_contract_invalid" in result["blockers"]


def test_population_policy_contains_all_markets_and_nontrading_semantics():
    value = bundle()
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is True
    memberships = value["universes"][0]["memberships"]
    assert {row["market"] for row in memberships} == {"TWSE", "TPEx", "emerging"}
    assert {row["status"] for row in memberships} >= {"active", "suspended", "zero_volume"}
    assert any(row["exitEffectiveAt"] is not None for row in memberships)
    assert "price" in cert.POPULATION_POLICY["prohibitedDerivations"]
    assert "current-survivors" in cert.POPULATION_POLICY["prohibitedDerivations"]


def test_missing_emerging_component_or_membership_interval_fails():
    value = bundle()
    value["universes"][0]["components"] = [
        row for row in value["universes"][0]["components"]
        if row["componentId"] != "emerging_active"
    ]
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is False
    assert "official_population_component_missing" in result["blockers"]

    value = bundle()
    target = next(row for row in value["universes"][0]["memberships"] if row["entityId"] == "2454")
    target["exitEffectiveAt"] = "2024-01-01T00:00:00+08:00"
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is False
    assert "membership_interval_invalid" in result["blockers"]


def test_survivor_or_cached_subset_never_becomes_certified_even_if_rehashed():
    value = bundle()
    removed = "2454"
    universe = value["universes"][0]
    universe["memberships"] = [row for row in universe["memberships"] if row["entityId"] != removed]
    universe["entities"] = [row for row in universe["entities"] if row != removed]
    universe["entityCount"] = len(universe["entities"])
    universe["entitySetHash"] = cert.digest(universe["entities"])
    for component in universe["components"]:
        if removed in component["entities"]:
            component["entities"].remove(removed)
            component["entityCount"] = len(component["entities"])
            component["entitySetHash"] = cert.digest(component["entities"])
    universe["componentSetHash"] = cert.digest([
        {"componentId": row["componentId"], "entityCount": row["entityCount"],
         "entitySetHash": row["entitySetHash"], "selectedVersionHash": row["selectedVersionHash"]}
        for row in sorted(universe["components"], key=lambda item: item["componentId"])
    ])
    value["records"] = [row for row in value["records"] if row["entityId"] != removed]
    # A caller may recompute every self-declared hash; certification still has no true path.
    result = cert.run(value, enabled=True)
    assert result["pitCoverageCertified"] is False
    assert result["authoritativeProvenanceCoverageCertified"] is False


def test_one_missing_extra_or_duplicate_pair_never_rounds_to_complete():
    value = bundle()
    value["records"].pop()
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is False
    assert "pit_version_missing_or_not_unique" in result["blockers"]
    assert result["coverage"]["coverageRate"] < 1.0

    value = bundle()
    value["records"].append(copy.deepcopy(value["records"][0]))
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is False
    assert "pit_version_missing_or_not_unique" in result["blockers"]


def test_unknown_future_inferred_or_first_seen_availability_is_rejected():
    for field, replacement in (
        ("availableAt", "2099-01-01T00:00:00+08:00"),
        ("availableAt", "2024-06-14"),
        ("availableAtEvidenceClass", "retrieved_at"),
        ("availableAtEvidenceClass", "first_seen"),
    ):
        value = bundle()
        value["records"][0][field] = replacement
        result = cert.assess_structure(value)
        assert result["structuralCoverageComplete"] is False
        assert "authoritative_provenance_invalid" in result["blockers"]


def test_fundamental_not_yet_published_is_metadata_only_not_scoring_ready():
    value = bundle()
    target = next(row for row in value["records"] if row["requirementId"] == "fundamentals.eps")
    target["status"] = "not_yet_published"
    target["evidenceRole"] = "official_not_yet_published"
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is True
    assert result["scoringInputsMetadataComplete"] is False
    assert result["scoringInputsReady"] is False


def test_selection_requirement_cannot_use_not_applicable():
    value = bundle()
    target = next(row for row in value["records"] if row["requirementId"] == "quote.price")
    target["status"] = "not_applicable"
    target["evidenceRole"] = "official_not_applicable"
    result = cert.assess_structure(value)
    assert result["structuralCoverageComplete"] is False
    assert "pit_requirement_state_invalid" in result["blockers"]


def test_lineage_summaries_are_recomputed_not_accepted_as_hex_strings():
    for summary, field in (
        ("frozenSummary", "coverageMatrixHash"),
        ("replaySummary", "selectedVersionSetHash"),
        ("availabilitySummary", "evidenceSetHash"),
    ):
        value = bundle()
        value["lineage"][summary][field] = "0" * 64
        result = cert.assess_structure(value)
        assert result["structuralCoverageComplete"] is False
        assert result["blockers"]


def test_sensitive_raw_and_performance_content_is_rejected():
    forbidden = (
        {"raw": [{"x": 1}]}, {"score": 99}, {"return": 0.4},
        {"url": "https://provider.invalid"}, {"token": "secret"},
    )
    for item in forbidden:
        value = bundle()
        value["records"][0]["unexpected"] = item
        result = cert.run(value, enabled=True)
        assert result["pitCoverageCertified"] is False
        assert "bundle_contract_invalid" in result["blockers"]


def test_public_output_is_metadata_only_and_deterministic():
    first = cert.run(bundle(), enabled=True)
    second = cert.run(copy.deepcopy(bundle()), enabled=True)
    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in ("1101", "2330", "2454", "6147", "7777", "entityId", "availableAt", "price", "score", "rank"):
        assert forbidden not in encoded
    assert first["reportDigest"] == cert.digest({
        key: value for key, value in first.items() if key != "reportDigest"
    })


def test_source_pins_and_workflow_cover_the_entire_boundary():
    actual = {
        name: canonical_source_hash(ROOT / name)
        for name in cert.CERTIFICATION_PRODUCER_PINS
    }
    assert actual == cert.CERTIFICATION_PRODUCER_PINS
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(encoding="utf-8")
    for name in (
        *cert.CERTIFICATION_PRODUCER_PINS,
        "authoritative_pit_coverage_certification.py",
        "trusted_pit_bundle_registry_v1.json",
        "production_strategy_validation_preflight.py",
    ):
        assert name in workflow


def test_module_has_no_formal_network_database_env_or_subprocess_imports():
    tree = ast.parse((ROOT / "authoritative_pit_coverage_certification.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "scoring", "backtest", "strategy_backtest", "investment_advice_gate",
        "daily_report", "candidate_manifest", "telegram", "requests", "urllib",
        "socket", "subprocess", "os", "supabase", "psycopg", "openai",
    })


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
