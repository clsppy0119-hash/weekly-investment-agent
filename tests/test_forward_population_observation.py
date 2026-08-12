import ast
import copy
import json
from pathlib import Path
import unittest

import forward_population_observation as observer
import official_population_source_admission as admission


ROOT = Path(__file__).resolve().parents[1]


def hx(seed):
    return observer.digest({"seed": seed})


def component(source_slot, count=10):
    pin = observer.FORWARD_SOURCE_PINS[source_slot]
    commitments = sorted(
        hx(source_slot + f"-identity-{number}") for number in range(count)
    )
    value = {
        "sourceSlot": source_slot,
        "componentId": pin["componentId"],
        "sourceContractHash": pin["sourceContractHash"],
        "producerId": pin["producerId"],
        "sourceSchemaHash": pin["sourceSchemaHash"],
        "termsContractHash": pin["termsContractHash"],
        "expectedPages": 1,
        "parsedPages": 1,
        "expectedRecords": count,
        "parsedRecords": count,
        "rejectedRecords": 0,
        "entityCount": count,
        "identityCommitments": commitments,
        "entitySetHash": observer.digest(commitments),
        "identitySetHash": observer.digest(commitments),
        "contentHash": hx(source_slot + "-content"),
    }
    value["componentHash"] = observer.component_hash(value)
    return value


def transition(kind, count=1, suffix="one"):
    return {
        "transitionType": kind,
        "authorityClass": observer.TRANSITION_AUTHORITY,
        "count": count,
        "evidenceHash": hx(kind + suffix),
    }


def observation(
    sequence=1, previous=None, completed="2026-08-13T00:00:00Z",
    event_type="complete_observation", event_id=None, transitions=None,
    correction_of=None, gap_reason=None, component_rows=None,
):
    event_id = event_id or f"observation-{sequence}"
    if event_type == "observation_gap":
        rows = []
        transitions = transitions or [transition("unknown_gap")]
        source_batch_hash = observer.digest({
            "eventId": event_id, "gapReason": gap_reason or "source_unavailable",
        })
        union_count = 0
        union_hash = None
    else:
        rows = component_rows or [component(slot) for slot in observer.CURRENT_SOURCE_SLOTS]
        transitions = transitions or []
        source_batch_hash = observer.digest(sorted(row["componentHash"] for row in rows))
        union_count = sum(row["entityCount"] for row in rows)
        union_commitments = sorted(
            commitment for row in rows for commitment in row["identityCommitments"]
        )
        union_hash = observer.digest(union_commitments)
    value = {
        "sequenceNumber": sequence,
        "eventId": event_id,
        "eventType": event_type,
        "previousEventHash": previous,
        "observationCompletedAt": completed,
        "clockEvidenceClass": observer.CLOCK_EVIDENCE_CLASS,
        "sourceBatchHash": source_batch_hash,
        "components": rows,
        "transitions": transitions,
        "gapReason": gap_reason or ("source_unavailable" if event_type == "observation_gap" else None),
        "correctionOfEventId": correction_of,
        "unionEntityCount": union_count,
        "unionIdentityCount": union_count,
        "unionEntitySetHash": union_hash,
        "unionIdentitySetHash": union_hash,
    }
    value["eventHash"] = observer.event_hash(value)
    return value


def ledger(events=None):
    events = events or [observation()]
    normalized = [
        observer._normalized_event(row) | {"eventHash": row["eventHash"]}
        for row in events
    ]
    return {
        "schemaVersion": observer.SCHEMA_VERSION,
        "policyVersion": observer.POLICY_VERSION,
        "observerContractHash": observer.OBSERVER_CONTRACT_HASH,
        "populationPolicyHash": observer.PINNED_POPULATION_POLICY_HASH,
        "sourceAdmissionPolicyHash": observer.PINNED_SOURCE_ADMISSION_POLICY_HASH,
        "ledgerEvents": events,
        "ledgerHash": observer.digest(normalized),
    }


def rehash_event(value):
    components = value.get("components", [])
    for row in components:
        row["componentHash"] = observer.component_hash(row)
    if value.get("eventType") == "observation_gap":
        value["sourceBatchHash"] = observer.digest({
            "eventId": value["eventId"], "gapReason": value["gapReason"],
        })
    else:
        value["sourceBatchHash"] = observer.digest(sorted(row["componentHash"] for row in components))
    value["eventHash"] = observer.event_hash(value)


def rehash_ledger(value):
    normalized = [
        observer._normalized_event(row) | {"eventHash": row.get("eventHash")}
        for row in value["ledgerEvents"]
    ]
    value["ledgerHash"] = observer.digest(normalized)


def test_complete_fixture_only_proves_forward_shape_never_evidence_or_pit():
    result = observer.run(ledger(), enabled=True)
    assert result["ledgerStructurallyValid"] is True
    assert result["forwardObservationShapeComplete"] is True
    assert result["completeObservationCount"] == 1
    assert result["eventCount"] == 1
    assert result["firstSeenBoundaryHash"]
    for key in (
        "coverageContinuityAssumed", "forwardEvidenceAdmitted", "sourceAdmitted",
        "historicalEligible", "officialProducerRegistered", "pitCoverageCertified",
        "strategyValidated", "promotionEligible", "adviceEnabled", "registryEligible",
        "formalGateAttached", "preFirstSeenBackfillAllowed",
        "officialMembershipTransitionsCertified",
    ):
        assert result[key] is False
    assert set(observer.FIXED_BLOCKERS).issubset(result["blockers"])


def test_default_off_does_not_inspect_payload():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError(name)

    assert observer.run(Explodes(), enabled=False) == {
        "schemaVersion": 1,
        "policyVersion": observer.POLICY_VERSION,
        "mode": "disabled",
        "forwardEvidenceAdmitted": False,
        "historicalEligible": False,
        "registryEligible": False,
    }


def test_exact_three_current_components_are_required_without_events_or_terminated():
    for mutation in ("missing", "duplicate", "events"):
        value = ledger()
        event = value["ledgerEvents"][0]
        if mutation == "missing":
            event["components"].pop()
        elif mutation == "duplicate":
            event["components"][1] = copy.deepcopy(event["components"][0])
        else:
            event["components"][0]["sourceSlot"] = "twse_membership_events"
        event["unionEntityCount"] = sum(row["entityCount"] for row in event["components"])
        event["unionIdentityCount"] = event["unionEntityCount"]
        rehash_event(event)
        rehash_ledger(value)
        result = observer.run(value, enabled=True)
        assert result["ledgerStructurallyValid"] is False
        assert "observation_event_contract_invalid" in result["blockers"]


def test_full_pages_counts_zero_rejections_and_nonempty_components_are_required():
    for field, replacement in (
        ("parsedPages", 0), ("rejectedRecords", 1),
        ("parsedRecords", 9), ("expectedRecords", 0),
    ):
        value = ledger()
        event = value["ledgerEvents"][0]
        event["components"][0][field] = replacement
        rehash_event(event)
        rehash_ledger(value)
        result = observer.run(value, enabled=True)
        assert result["ledgerStructurallyValid"] is False


def test_union_counts_are_fixed_and_cannot_hide_cross_component_shrinkage():
    value = ledger()
    event = value["ledgerEvents"][0]
    event["unionEntityCount"] -= 1
    event["eventHash"] = observer.event_hash(event)
    rehash_ledger(value)
    result = observer.run(value, enabled=True)
    assert result["ledgerStructurallyValid"] is False


def test_cross_component_identity_overlap_is_recomputed_and_rejected():
    value = ledger()
    event = value["ledgerEvents"][0]
    first = event["components"][0]
    second = event["components"][1]
    second["identityCommitments"][0] = first["identityCommitments"][0]
    second["identityCommitments"].sort()
    second["entitySetHash"] = observer.digest(second["identityCommitments"])
    second["identitySetHash"] = observer.digest(second["identityCommitments"])
    rehash_event(event)
    rehash_ledger(value)
    assert observer.run(value, enabled=True)["ledgerStructurallyValid"] is False


def test_component_and_global_commitment_hashes_are_recomputed_not_self_declared():
    value = ledger()
    event = value["ledgerEvents"][0]
    event["components"][0]["identityCommitments"][0] = "f" * 64
    rehash_event(event)
    rehash_ledger(value)
    assert observer.run(value, enabled=True)["ledgerStructurallyValid"] is False


def test_observation_completion_is_strict_utc_shape_not_available_at():
    for completed in (
        "2026-08-13", "2026-08-13T08:00:00", "2026-08-13T08:00:00+08:00",
    ):
        value = ledger()
        event = value["ledgerEvents"][0]
        event["observationCompletedAt"] = completed
        rehash_event(event)
        rehash_ledger(value)
        assert observer.run(value, enabled=True)["ledgerStructurallyValid"] is False
    encoded_source = (ROOT / "forward_population_observation.py").read_text(encoding="utf-8")
    assert '"availableAt"' not in encoded_source


def test_sequence_hash_chain_and_clock_must_move_strictly_forward():
    first = observation()
    second = observation(
        sequence=2, previous=first["eventHash"],
        completed="2026-08-14T00:00:00Z", event_id="observation-2",
    )
    assert observer.run(ledger([first, second]), enabled=True)["ledgerStructurallyValid"] is True
    mutations = (
        ("sequenceNumber", 3),
        ("previousEventHash", "f" * 64),
        ("observationCompletedAt", "2026-08-12T00:00:00Z"),
    )
    for field, replacement in mutations:
        broken = copy.deepcopy([first, second])
        broken[1][field] = replacement
        rehash_event(broken[1])
        result = observer.run(ledger(broken), enabled=True)
        assert result["ledgerStructurallyValid"] is False


def test_explicit_gap_preserves_ledger_but_never_assumes_continuity_or_interpolates():
    first = observation()
    gap = observation(
        sequence=2, previous=first["eventHash"], event_type="observation_gap",
        event_id="gap-2", completed="2026-08-14T00:00:00Z",
        gap_reason="observation_window_missed",
    )
    result = observer.run(ledger([first, gap]), enabled=True)
    assert result["ledgerStructurallyValid"] is True
    assert result["gapCount"] == 1
    assert result["transitionCounts"]["unknown_gap"] == 1
    assert result["coverageContinuityAssumed"] is False
    assert result["pitCoverageCertified"] is False


def test_gap_requires_only_unknown_non_authoritative_transition_and_no_components():
    first = observation()
    cases = (
        {"components": [component("twse_current_master")]},
        {"transitions": [transition("observed_added")]},
        {"gapReason": "weekend"},
    )
    for change in cases:
        gap = observation(
            sequence=2, previous=first["eventHash"], event_type="observation_gap",
            event_id="gap-2", completed="2026-08-14T00:00:00Z",
        )
        gap.update(change)
        rehash_event(gap)
        result = observer.run(ledger([first, gap]), enabled=True)
        assert result["ledgerStructurallyValid"] is False


def test_snapshot_deltas_are_always_non_authoritative_and_never_official_events():
    first = observation()
    second = observation(
        sequence=2, previous=first["eventHash"], event_id="observation-2",
        completed="2026-08-14T00:00:00Z",
        transitions=[transition("observed_added", 2), transition("market_changed")],
    )
    result = observer.run(ledger([first, second]), enabled=True)
    assert result["ledgerStructurallyValid"] is True
    assert result["transitionCounts"]["observed_added"] == 2
    assert result["transitionCounts"]["market_changed"] == 1
    assert result["officialMembershipTransitionsCertified"] is False
    broken = copy.deepcopy([first, second])
    broken[1]["transitions"][0]["authorityClass"] = "official_membership_event"
    rehash_event(broken[1])
    assert observer.run(ledger(broken), enabled=True)["ledgerStructurallyValid"] is False


def test_append_candidate_exact_duplicate_noop_and_collisions_fail_closed():
    first = observation()
    appended, status = observer.append_candidate([], first)
    assert status == "appended" and appended == [first]
    duplicate, status = observer.append_candidate(appended, copy.deepcopy(first))
    assert status == "duplicate_noop" and duplicate == appended
    conflict = observation(event_id=first["eventId"])
    conflict["contentProbe"] = "not-allowed"
    assert observer.append_candidate(appended, conflict)[1] == "invalid"
    second = observation(
        sequence=2, previous=first["eventHash"], event_id="observation-2",
        completed="2026-08-14T00:00:00Z",
    )
    second["eventId"] = first["eventId"]
    rehash_event(second)
    assert observer.append_candidate(appended, second)[1] == "conflict"


def test_append_candidate_rejects_clock_rollback_equal_time_and_broken_existing_chain():
    first = observation()
    for completed in ("2026-08-12T00:00:00Z", first["observationCompletedAt"]):
        candidate = observation(
            sequence=2, previous=first["eventHash"], event_id="observation-2",
            completed=completed,
        )
        assert observer.append_candidate([first], candidate)[1] == "conflict"
    broken = copy.deepcopy(first)
    broken["previousEventHash"] = "f" * 64
    rehash_event(broken)
    candidate = observation(
        sequence=2, previous=broken["eventHash"], event_id="observation-2",
        completed="2026-08-14T00:00:00Z",
    )
    assert observer.append_candidate([broken], candidate)[1] == "invalid"


def test_append_candidate_rejects_missing_or_gap_correction_parent():
    first = observation()
    missing = observation(
        sequence=2, previous=first["eventHash"], event_type="correction_observation",
        event_id="correction-2", completed="2026-08-14T00:00:00Z",
        correction_of="missing",
    )
    assert observer.append_candidate([first], missing)[1] == "conflict"
    gap = observation(
        sequence=1, event_type="observation_gap", event_id="gap-1",
        gap_reason="source_unavailable",
    )
    correction = observation(
        sequence=2, previous=gap["eventHash"], event_type="correction_observation",
        event_id="correction-2", completed="2026-08-14T00:00:00Z",
        correction_of=gap["eventId"],
    )
    assert observer.append_candidate([gap], correction)[1] == "conflict"


def test_correction_is_append_only_and_requires_prior_non_gap_parent():
    first = observation()
    correction = observation(
        sequence=2, previous=first["eventHash"],
        completed="2026-08-14T00:00:00Z", event_type="correction_observation",
        event_id="correction-2", correction_of=first["eventId"],
    )
    result = observer.run(ledger([first, correction]), enabled=True)
    assert result["ledgerStructurallyValid"] is True
    assert result["correctionCount"] == 1
    correction["correctionOfEventId"] = "missing"
    rehash_event(correction)
    result = observer.run(ledger([first, correction]), enabled=True)
    assert result["ledgerStructurallyValid"] is False
    assert "correction_parent_invalid" in result["blockers"]


def test_empty_committed_ledger_is_exact_and_no_runtime_persistence_exists():
    committed = json.loads(
        (ROOT / "forward_population_observation_ledger_v1.json").read_text(encoding="utf-8")
    )
    assert set(committed) == observer.EMPTY_LEDGER_KEYS
    assert committed == observer.empty_ledger()
    assert committed["events"] == []
    assert "persist" not in observer.__dict__
    assert "schedule" not in observer.__dict__


def test_input_order_of_components_and_transitions_is_deterministic():
    first_value = ledger()
    second_value = copy.deepcopy(first_value)
    event = second_value["ledgerEvents"][0]
    event["components"].reverse()
    event["transitions"] = [transition("observed_removed"), transition("observed_added")]
    rehash_event(event)
    rehash_ledger(second_value)
    canonical_value = copy.deepcopy(second_value)
    canonical_event = canonical_value["ledgerEvents"][0]
    canonical_event["components"].sort(key=lambda row: row["sourceSlot"])
    canonical_event["transitions"].reverse()
    rehash_event(canonical_event)
    rehash_ledger(canonical_value)
    assert observer.run(second_value, enabled=True) == observer.run(canonical_value, enabled=True)


def test_output_is_sanitized_and_never_exposes_timestamps_or_component_hashes():
    result = observer.run(ledger(), enabled=True)
    encoded = json.dumps(result, sort_keys=True)
    for forbidden in (
        "2026-08-13", "observationCompletedAt", "sourceBatchHash",
        "entitySetHash", "identitySetHash", "contentHash", "availableAt",
    ):
        assert forbidden not in encoded
    assert result["reportDigest"] == observer.digest({
        key: value for key, value in result.items() if key != "reportDigest"
    })


def test_malformed_huge_sensitive_url_and_hostile_inputs_fail_closed():
    cycle = []
    cycle.append(cycle)

    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    class BadList(list):
        def __iter__(self):
            raise RuntimeError("boom")

    for item in (
        {"x": float("nan")}, {"x": {1, 2}}, {1: "bad"}, cycle,
        {"x": 10**10000}, {"x": -(10**10000)}, BadDict(), BadList(),
    ):
        result = observer.evaluate(item)
        assert result["forwardEvidenceAdmitted"] is False
        assert result["blockers"]
    for key, unsafe in (
        ("raw", [{}]), ("url", "https://example.invalid"),
        ("token", "secret"), ("availableAt", "2026-08-13T00:00:00Z"),
        ("return", 1.0),
    ):
        value = ledger()
        value["ledgerEvents"][0][key] = unsafe
        rehash_ledger(value)
        assert observer.evaluate(value)["ledgerStructurallyValid"] is False


def test_source_pins_are_frozen_against_upstream_runtime_mutation():
    before = observer.run(ledger(), enabled=True)
    old_hash = admission.source_slot_contract_hash
    old_slots = admission.SOURCE_SLOT_PINS
    try:
        admission.source_slot_contract_hash = lambda slot: "f" * 64
        admission.SOURCE_SLOT_PINS = {}
        after = observer.run(ledger(), enabled=True)
    finally:
        admission.source_slot_contract_hash = old_hash
        admission.SOURCE_SLOT_PINS = old_slots
    assert before == after
    try:
        observer.FORWARD_SOURCE_PINS["twse_current_master"]["producerId"] = "caller"
    except TypeError:
        pass
    else:
        raise AssertionError("forward source pins were mutable")


def test_module_has_no_network_database_env_clock_subprocess_or_formal_imports():
    path = ROOT / "forward_population_observation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source
    for consumer in (
        "authoritative_pit_coverage_certification.py",
        "official_full_market_population.py",
        "official_population_artifact_receipt.py",
        "official_population_source_admission.py",
        "investment_advice_gate.py", "daily_report.py",
    ):
        assert "forward_population_observation" not in (ROOT / consumer).read_text(encoding="utf-8")


def test_workflow_covers_observer_and_empty_ledger_without_external_permissions():
    workflow = (ROOT / ".github/workflows/pipeline-safety-validation.yml").read_text(encoding="utf-8")
    assert "forward_population_observation.py" in workflow
    assert "forward_population_observation_ledger_v1.json" in workflow
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
