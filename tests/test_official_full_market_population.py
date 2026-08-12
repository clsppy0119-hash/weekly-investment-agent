import ast
import copy
import json
import time
import unittest
from pathlib import Path

import official_full_market_population as producer
import authoritative_pit_coverage_certification as certification
import market_population_contract as population_contract


ROOT = Path(__file__).resolve().parents[1]
DECISION = "2024-06-14T16:00:00+08:00"


def security(code, market, status="active", *, security_class="common_equity",
             entry="2020-01-01T00:00:00+08:00", exit_at=None, suffix=""):
    value = {
        "securityId": f"security-{code}-{market}{suffix}",
        "issuerId": f"issuer-{code}{suffix}",
        "securityCode": code,
        "securityClass": security_class,
        "market": market,
        "status": status,
        "entryEffectiveAt": entry,
        "exitEffectiveAt": exit_at,
        "identityEvidenceHash": producer.digest({"identity": code, "market": market, "suffix": suffix}),
        "selectedVersionHash": "",
    }
    value["selectedVersionHash"] = producer._security_hash(value)
    return value


def revision(component_id, records, *, revision_id=None, supersedes=None,
             effective="2024-06-14T00:00:00+08:00",
             available="2024-06-14T15:00:00+08:00",
             evidence_class="official_timezone_timestamp"):
    ordered = sorted(records, key=lambda row: (row["securityId"], row["securityCode"]))
    value = {
        "revisionId": revision_id or f"{component_id}-20240614-v1",
        "supersedesRevisionId": supersedes,
        "effectiveAt": effective,
        "availableAt": available,
        "availabilityEvidenceClass": evidence_class,
        "availabilityEvidenceId": f"{component_id}-published-20240614",
        "publicationSemanticsHash": producer.digest({"semantics": component_id}),
        "schemaHash": producer.security_schema_hash(),
        "recordSetHash": producer.digest(ordered),
        "records": copy.deepcopy(records),
        "revisionHash": "",
    }
    value["revisionHash"] = producer._revision_hash(value)
    return value


def component(component_id, records, *, revisions=None):
    contract = producer.SOURCE_CONTRACTS[component_id]
    selected_records = records
    return {
        "componentId": component_id,
        "sourceContractId": contract["sourceContractId"],
        "sourceContractHash": producer.source_contract_hash(component_id),
        "source": contract["source"],
        "dataset": contract["dataset"],
        "schemaVersion": 1,
        "producerId": contract["producerId"],
        "producerHash": producer.digest({"fixtureProducer": component_id}),
        "expectedRecordCount": len(selected_records),
        "parsedRecordCount": len(selected_records),
        "rejectedRecordCount": 0,
        "pageCount": 1,
        "parsedPageCount": 1,
        "revisions": revisions or [revision(component_id, records)],
    }


def base_records():
    return {
        "twse_active": [
            security("1101", "TWSE"),
            security("2330", "TWSE", "suspended"),
            security("2454", "TWSE", "zero_volume", exit_at="2024-12-31T00:00:00+08:00"),
        ],
        "tpex_active": [security("6147", "TPEx")],
        "emerging_active": [security("7777", "emerging")],
    }


def payload(*, decision=DECISION, records=None):
    source_records = records or base_records()
    all_records = [
        copy.deepcopy(row)
        for component_id in producer.ACTIVE_COMPONENTS
        for row in source_records[component_id]
    ]
    return {
        "schemaVersion": 1,
        "policyVersion": producer.POLICY_VERSION,
        "populationPolicyHash": producer.population_policy_hash(),
        "timezone": producer.TIMEZONE,
        "decisionAsOf": decision,
        "components": [
            component("twse_active", source_records["twse_active"]),
            component("tpex_active", source_records["tpex_active"]),
            component("emerging_active", source_records["emerging_active"]),
            component("membership_events", all_records),
        ],
    }


def replace_revision(component_value, revisions, selected_records):
    component_value["revisions"] = revisions
    component_value["expectedRecordCount"] = len(selected_records)
    component_value["parsedRecordCount"] = len(selected_records)


def test_complete_fixture_assembles_but_never_authenticates_or_certifies():
    result = producer.run(payload(), enabled=True)
    assert result["structuralPopulationComplete"] is True
    assert result["componentCount"] == 4
    assert result["universeEntityCount"] == 5
    assert result["officialProducerRegistered"] is False
    assert result["historicalEligible"] is False
    assert "official_source_admission_unregistered" in result["blockers"]
    assert "historical_available_at_authority_unregistered" in result["blockers"]
    for forbidden in (
        "pitCoverageCertified", "strategyValidated", "promotionEligible",
        "adviceEnabled", "valuesCertified",
    ):
        assert forbidden not in result
    assert producer.OFFICIAL_SOURCE_ADMISSION_ALLOWLIST == frozenset()


def test_default_off_does_not_inspect_payload():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    assert producer.run(Explodes(), enabled=False) == {
        "schemaVersion": 1,
        "policyVersion": producer.POLICY_VERSION,
        "mode": "disabled",
        "structuralPopulationComplete": False,
        "officialProducerRegistered": False,
        "historicalEligible": False,
    }


def test_missing_or_empty_component_and_activity_list_fail_closed():
    value = payload()
    value["components"] = [row for row in value["components"] if row["componentId"] != "emerging_active"]
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "required_component_missing" in result["blockers"]

    value = payload()
    emerging = next(row for row in value["components"] if row["componentId"] == "emerging_active")
    replace_revision(emerging, [revision("emerging_active", [])], [])
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "active_component_empty" in result["blockers"]

    value = payload()
    value["components"][0]["sourceContractId"] = "daily-price-activity-list"
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "component_source_contract_invalid" in result["blockers"]


def test_suspended_zero_volume_emerging_and_later_delisted_are_retained():
    value = payload()
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is True
    assert result["universeEntitySetHash"] == producer.digest(["1101", "2330", "2454", "6147", "7777"])
    assert "price" in producer.POPULATION_POLICY["prohibitedDerivations"]
    assert "volume" in producer.POPULATION_POLICY["prohibitedDerivations"]
    assert "current-survivors" in producer.POPULATION_POLICY["prohibitedDerivations"]


def test_population_policy_hash_is_shared_exactly_with_node49_consumer():
    assert producer.POPULATION_POLICY is not certification.POPULATION_POLICY
    assert producer.population_policy_hash() == certification.population_policy_hash()
    assert producer.population_policy_hash() == population_contract.population_policy_hash()
    before = producer.population_policy_hash()
    mutable_copy = population_contract.population_policy()
    mutable_copy["includedStates"].append("caller-injected")
    mutable_copy["marketComponents"].clear()
    mutable_copy["prohibitedDerivations"].clear()
    assert producer.population_policy_hash() == before
    assert certification.population_policy_hash() == before
    try:
        producer.POPULATION_POLICY["includedStates"] += ("caller-injected",)
    except TypeError:
        pass
    else:
        raise AssertionError("population policy view was mutable")
    assert producer.REQUIRED_COMPONENTS == (
        "twse_active", "tpex_active", "emerging_active", "membership_events",
    )


def test_entry_inclusive_exit_exclusive_and_later_delisted_boundary():
    before = producer.run(payload(), enabled=True)
    after = producer.run(payload(decision="2024-12-31T00:00:00+08:00"), enabled=True)
    assert before["universeEntityCount"] == 5
    assert after["universeEntityCount"] == 4
    assert after["structuralPopulationComplete"] is True

    records = base_records()
    ipo = security("8888", "emerging", entry="2024-06-14T16:00:00+08:00")
    records["emerging_active"].append(ipo)
    at_entry = producer.run(payload(records=records), enabled=True)
    assert at_entry["universeEntityCount"] == 6


def test_market_transfer_duplicate_code_or_identity_conflict_fails():
    value = payload()
    tpex = next(row for row in value["components"] if row["componentId"] == "tpex_active")
    tpex_records = copy.deepcopy(tpex["revisions"][0]["records"])
    tpex_records.append(security("1101", "TPEx", suffix="-transfer"))
    replace_revision(tpex, [revision("tpex_active", tpex_records)], tpex_records)
    events = next(row for row in value["components"] if row["componentId"] == "membership_events")
    event_records = copy.deepcopy(events["revisions"][0]["records"])
    event_records.append(security("1101", "TPEx", suffix="-transfer"))
    replace_revision(events, [revision("membership_events", event_records)], event_records)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "market_union_or_membership_events_mismatch" in result["blockers"]


def test_security_class_is_official_not_inferred_from_four_digit_code():
    value = payload()
    twse = next(row for row in value["components"] if row["componentId"] == "twse_active")
    records = copy.deepcopy(twse["revisions"][0]["records"])
    records[0] = security("1101", "TWSE", security_class="etf")
    replace_revision(twse, [revision("twse_active", records)], records)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "revision_evidence_or_records_invalid" in result["blockers"]


def test_available_at_never_accepts_date_only_first_seen_retrieved_or_future():
    cases = (
        ("availableAt", "2024-06-14"),
        ("availabilityEvidenceClass", "first_seen"),
        ("availabilityEvidenceClass", "retrieved_at"),
        ("availableAt", "2099-01-01T00:00:00+08:00"),
    )
    for field, replacement in cases:
        value = payload()
        selected = value["components"][0]["revisions"][0]
        selected[field] = replacement
        selected["revisionHash"] = producer._revision_hash(selected)
        result = producer.run(value, enabled=True)
        assert result["structuralPopulationComplete"] is False
        assert result["blockers"]


def test_future_correction_does_not_flow_back_and_old_revision_is_preserved():
    value = payload()
    target = value["components"][0]
    old = target["revisions"][0]
    future_records = copy.deepcopy(old["records"])
    future = revision(
        "twse_active", future_records, revision_id="twse-active-future-v2",
        supersedes=old["revisionId"], effective="2024-07-01T00:00:00+08:00",
        available="2024-07-01T10:00:00+08:00",
    )
    baseline = producer.run(value, enabled=True)
    target["revisions"].append(future)
    corrected = producer.run(value, enabled=True)
    assert corrected["structuralPopulationComplete"] is True
    assert corrected["selectedRevisionSetHash"] == baseline["selectedRevisionSetHash"]
    assert corrected["inputDigest"] != baseline["inputDigest"]


def test_code_reuse_across_append_only_revisions_selects_only_as_of_version():
    value = payload()
    twse = next(row for row in value["components"] if row["componentId"] == "twse_active")
    old = twse["revisions"][0]
    old_records = copy.deepcopy(old["records"])
    boundary = "2024-07-01T00:00:00+08:00"
    new_records = copy.deepcopy(old_records)
    old_identity = next(row for row in new_records if row["securityCode"] == "1101")
    old_identity["exitEffectiveAt"] = boundary
    old_identity["selectedVersionHash"] = producer._security_hash(old_identity)
    new_records.append(security("1101", "TWSE", suffix="-new-issuer", entry=boundary))
    newer = revision(
        "twse_active", new_records, revision_id="twse-active-202407-v2",
        supersedes=old["revisionId"], effective=boundary,
        available="2024-07-01T10:00:00+08:00",
    )
    twse["revisions"].append(newer)
    before = producer.run(value, enabled=True)
    assert before["structuralPopulationComplete"] is True

    events = next(row for row in value["components"] if row["componentId"] == "membership_events")
    event_old = events["revisions"][0]
    event_new_records = copy.deepcopy(event_old["records"])
    event_old_identity = next(row for row in event_new_records if row["securityCode"] == "1101")
    event_old_identity["exitEffectiveAt"] = boundary
    event_old_identity["selectedVersionHash"] = producer._security_hash(event_old_identity)
    event_new_records.append(security("1101", "TWSE", suffix="-new-issuer", entry=boundary))
    event_new = revision(
        "membership_events", event_new_records, revision_id="events-202407-v2",
        supersedes=event_old["revisionId"], effective=boundary,
        available="2024-07-01T10:00:00+08:00",
    )
    events["revisions"].append(event_new)
    value["decisionAsOf"] = "2024-07-01T16:00:00+08:00"
    twse["expectedRecordCount"] = twse["parsedRecordCount"] = len(new_records)
    events["expectedRecordCount"] = events["parsedRecordCount"] = len(event_new_records)
    after = producer.run(value, enabled=True)
    assert after["structuralPopulationComplete"] is True
    assert after["universeEntityCount"] == before["universeEntityCount"]
    assert after["selectedRevisionSetHash"] != before["selectedRevisionSetHash"]


def test_identity_replacement_requires_explicit_adjacent_exit_and_entry():
    def replacement_payload(mode):
        value = payload()
        target = next(row for row in value["components"] if row["componentId"] == "twse_active")
        old = target["revisions"][0]
        boundary = "2024-07-01T00:00:00+08:00"
        rows = copy.deepcopy(old["records"])
        old_row = next(row for row in rows if row["securityCode"] == "1101")
        if mode == "silent-removal":
            rows.remove(old_row)
        elif mode == "overlap":
            pass
        elif mode == "gap":
            old_row["exitEffectiveAt"] = "2024-06-30T00:00:00+08:00"
            old_row["selectedVersionHash"] = producer._security_hash(old_row)
        elif mode == "same-id-new-issuer":
            old_row["issuerId"] = "changed-issuer"
            old_row["selectedVersionHash"] = producer._security_hash(old_row)
            child = revision(
                "twse_active", rows, revision_id=f"bad-{mode}-v2",
                supersedes=old["revisionId"], effective=boundary,
                available="2024-07-01T10:00:00+08:00",
            )
            target["revisions"].append(child)
            return value
        rows.append(security("1101", "TWSE", suffix="-replacement", entry=boundary))
        child = revision(
            "twse_active", rows, revision_id=f"bad-{mode}-v2",
            supersedes=old["revisionId"], effective=boundary,
            available="2024-07-01T10:00:00+08:00",
        )
        target["revisions"].append(child)
        return value

    for mode in ("silent-removal", "overlap", "gap", "same-id-new-issuer"):
        value = replacement_payload(mode)
        result = producer.run(value, enabled=True)
        assert result["structuralPopulationComplete"] is False
        assert "identity_transition_invalid" in result["blockers"] \
            or "revision_security_identity_not_unique" in result["blockers"]


def test_duplicate_overwrite_broken_supersedes_and_ambiguous_revisions_fail():
    value = payload()
    target = value["components"][0]
    target["revisions"].append(copy.deepcopy(target["revisions"][0]))
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False

    value = payload()
    target = value["components"][0]
    orphan = revision(
        "twse_active", target["revisions"][0]["records"],
        revision_id="orphan-v2", supersedes="missing-v1",
    )
    target["revisions"].append(orphan)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "revision_supersedes_chain_invalid" in result["blockers"]

    value = payload()
    target = value["components"][0]
    independent = revision(
        "twse_active", target["revisions"][0]["records"], revision_id="independent-v2",
    )
    target["revisions"].append(independent)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "revision_not_uniquely_selectable_as_of" in result["blockers"]

    value = payload()
    target = value["components"][0]
    future_root = revision(
        "twse_active", target["revisions"][0]["records"],
        revision_id="unconnected-future-root",
        effective="2024-07-01T00:00:00+08:00",
        available="2024-07-01T10:00:00+08:00",
    )
    target["revisions"].append(future_root)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "revision_supersedes_chain_invalid" in result["blockers"]


def test_partial_pagination_rejected_rows_or_count_drift_fail():
    for field, replacement in (
        ("parsedPageCount", 0), ("rejectedRecordCount", 1),
        ("parsedRecordCount", 999), ("expectedRecordCount", 999),
    ):
        value = payload()
        value["components"][0][field] = replacement
        result = producer.run(value, enabled=True)
        assert result["structuralPopulationComplete"] is False
        assert "component_partial_or_rejected_input" in result["blockers"]

    value = payload()
    value["components"][0]["pageCount"] = "one"
    result = producer.assemble(value)
    assert result["structuralPopulationComplete"] is False
    assert "component_count_contract_invalid" in result["blockers"]


def test_component_event_union_must_be_exact():
    value = payload()
    events = next(row for row in value["components"] if row["componentId"] == "membership_events")
    records = copy.deepcopy(events["revisions"][0]["records"][:-1])
    replace_revision(events, [revision("membership_events", records)], records)
    result = producer.run(value, enabled=True)
    assert result["structuralPopulationComplete"] is False
    assert "market_union_or_membership_events_mismatch" in result["blockers"]


def test_malformed_sensitive_raw_performance_and_url_input_fail_closed():
    cycle = []
    cycle.append(cycle)
    malformed = [
        {"x": float("nan")}, {"x": {1, 2}}, cycle,
        {"x": {1: "bad-key"}},
    ]
    for item in malformed:
        result = producer.run(item, enabled=True)
        assert result["structuralPopulationComplete"] is False
        assert result["blockers"]
    for key, value_to_add in (
        ("raw", [{"x": 1}]), ("url", "https://example.invalid"),
        ("token", "secret"), ("price", 100), ("score", 99), ("return", 0.5),
    ):
        value = payload()
        value["components"][0][key] = value_to_add
        result = producer.run(value, enabled=True)
        assert result["structuralPopulationComplete"] is False
        assert "input_contract_invalid" in result["blockers"]


def test_input_order_does_not_change_population_hashes():
    first_payload = payload()
    second_payload = copy.deepcopy(first_payload)
    second_payload["components"].reverse()
    for component_value in second_payload["components"]:
        component_value["revisions"][0]["records"].reverse()
    first = producer.run(first_payload, enabled=True)
    second = producer.run(second_payload, enabled=True)
    assert first["universeEntitySetHash"] == second["universeEntitySetHash"]
    assert first["componentSetHash"] == second["componentSetHash"]
    assert first["selectedRevisionSetHash"] == second["selectedRevisionSetHash"]
    assert first["inputDigest"] == second["inputDigest"]
    assert first["artifactDigest"] == second["artifactDigest"]


def test_public_output_is_sanitized_and_deterministic():
    first = producer.run(payload(), enabled=True)
    second = producer.run(copy.deepcopy(payload()), enabled=True)
    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in (
        "1101", "2330", "2454", "6147", "7777", "securityId",
        "issuerId", "availableAt", "price", "score", "return",
    ):
        assert forbidden not in encoded
    assert first["artifactDigest"] == producer.digest({
        key: value for key, value in first.items() if key != "artifactDigest"
    })


def test_single_decision_scale_above_three_thousand_is_bounded():
    records = {key: [] for key in producer.ACTIVE_COMPONENTS}
    for number in range(1000, 4001):
        code = str(number)
        if number == 3999:
            market = "TPEx"
            component_id = "tpex_active"
        elif number == 4000:
            market = "emerging"
            component_id = "emerging_active"
        else:
            market = "TWSE"
            component_id = "twse_active"
        records[component_id].append(security(code, market))
    started = time.perf_counter()
    result = producer.run(payload(records=records), enabled=True)
    elapsed = time.perf_counter() - started
    assert result["structuralPopulationComplete"] is True
    assert result["universeEntityCount"] == 3001
    assert elapsed < 10


def test_module_has_no_network_database_env_clock_subprocess_or_formal_imports():
    path = ROOT / "official_full_market_population.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "scoring", "backtest", "strategy_backtest", "investment_advice_gate", "daily_report",
        "candidate_manifest", "telegram", "requests", "urllib", "socket",
        "subprocess", "os", "supabase", "psycopg", "openai",
    })
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source


def test_workflow_covers_producer_and_tests_without_external_permissions():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(encoding="utf-8")
    assert "official_full_market_population.py" in workflow
    assert "market_population_contract.py" in workflow
    assert "tests/test_official_full_market_population.py" in workflow or "tests/**" in workflow
    assert "contents: read" in workflow


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
