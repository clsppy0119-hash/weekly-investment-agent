import ast
import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actual_comprehensive_selection as selection
import actual_comprehensive_selection_parity as parity
import candidate_manifest
import strategy_backtest


ROOT = Path(__file__).resolve().parent.parent
DECISION = "2026-08-12T14:00:00+08:00"


def fixture():
    codes = ("7777", "6666", "5555", "4444")
    quotes = {
        code: {
            "name": code,
            "price": 100.0,
            "volume": 400 - 100 * index,
            "change": 1.0,
            "ma5": 99.0,
            "ma20": 98.0,
        }
        for index, code in enumerate(codes)
    }
    fundamentals = {
        code: {
            "revenueYoY": 15.0,
            "eps": 10.0,
            "roe": 20.0,
            "debtRatio": 25.0,
            "pe": 15.0,
            "pb": 2.0,
            "dividendYield": 3.0,
            "financialHistoryYears": 4 if code == "7777" else 5,
        }
        for code in codes
    }
    actions = {
        "source": "FinMind",
        "dataset": "TaiwanStockDividendResult",
        "availableAt": DECISION,
        "updatedAt": DECISION,
        "conflictStatus": "no_conflict",
        "queried_codes": list(codes),
        "failures": {},
        "period": {"start": "2025-01-01", "end": "2026-08-12"},
        "events": [],
    }
    return quotes, fundamentals, actions


def parity_payload():
    quotes, fundamentals, actions = fixture()
    return {
        "schemaVersion": 1,
        "signalDate": "2026-08-12",
        "decisionAsOf": DECISION,
        "quotes": quotes,
        "fundamentals": fundamentals,
        "actions": actions,
        "contractBlockers": [],
    }


def test_actual_selector_ranks_once_applies_quality_and_never_backfills():
    quotes, fundamentals, actions = fixture()
    result = selection.rank_and_assess(
        quotes, fundamentals, actions=actions, contract_blockers=[]
    )

    assert [item["code"] for item in result["fullPool"]] == ["7777", "6666", "5555", "4444"]
    assert [item["code"] for item in result["preview"]] == ["7777", "6666", "5555"]
    assert result["qualityPassedCodes"] == ["6666", "5555"]
    assert "fewer_than_five_financial_years" in result["preview"][0]["quality"]["blockers"]
    assert "4444" not in result["qualityPassedCodes"], "rank four must not backfill a failed preview name"
    assert result["noBackfill"] is True


def test_candidate_manifest_and_shared_selector_use_the_same_quality_result():
    quotes, fundamentals, actions = fixture()
    ranked = selection.rank_pool(quotes, fundamentals)[:3]
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
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        news = root / "news.json"
        action_path = root / "actions.json"
        gate = root / "gate.json"
        pit = root / "pit.json"
        news.write_text(json.dumps({"updatedAt": DECISION, "items": []}), encoding="utf-8")
        action_path.write_text(json.dumps(actions), encoding="utf-8")
        gate.write_text("{}", encoding="utf-8")
        pit.write_text(json.dumps({"certified": True, "generatedAt": DECISION, "availableAt": DECISION}), encoding="utf-8")
        manifest = candidate_manifest.build_manifest(
            report_date="2026-08-12", report_mode="comprehensive", phase="final",
            ranked={"comprehensive": ranked}, quote_data=quote_data,
            advice_gate={"status": "advice_candidate", "adviceEnabled": True, "blockers": []},
            actions=actions, news_path=news, actions_path=action_path,
            gate_path=gate, pit_path=pit,
        )

    shared = selection.rank_and_assess(
        quotes, fundamentals, actions=actions, contract_blockers=[]
    )
    assert manifest["previewCandidates"] == shared["preview"]
    assert [item["code"] for item in manifest["eligibleCandidates"]] == shared["qualityPassedCodes"]


def test_backtest_adapter_and_production_path_have_executable_parity():
    result = parity.run(parity_payload(), enabled=True)

    assert result["selectionParity"] is True
    assert result["productionDigest"] == result["backtestDigest"]
    assert result["fullPoolCount"] == 4
    assert result["previewCount"] == 3
    assert result["qualityPassedCount"] == 2
    assert result["strategyValidated"] is False
    assert result["promotionEligible"] is False
    assert result["adviceEnabled"] is False
    for blocker in (
        "pit_source_not_certified", "execution_spec_unregistered",
        "risk_policy_unregistered", "eligible_pool_benchmark_unregistered",
    ):
        assert blocker in result["blockers"]


def test_parity_detects_an_adapter_drift_instead_of_trusting_a_claim():
    original = strategy_backtest.rank_and_assess

    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        result["qualityPassedCodes"] = list(reversed(result["qualityPassedCodes"]))
        result["selectionDigest"] = selection.digest({
            key: value for key, value in result.items()
            if key not in {"poolTuples", "previewTuples", "selectedTuples", "selectionDigest"}
        })
        return result

    with mock.patch.object(strategy_backtest, "rank_and_assess", drift):
        result = parity.run(parity_payload(), enabled=True)

    assert result["selectionParity"] is False
    assert result["productionDigest"] != result["backtestDigest"]
    assert "production_backtest_selection_mismatch" in result["blockers"]


def test_missing_selection_evidence_fails_closed_without_changing_ranking():
    quotes, fundamentals, _actions = fixture()
    complete = selection.rank_and_assess(
        quotes, fundamentals, actions={}, contract_blockers=[]
    )
    missing = selection.rank_and_assess(
        quotes, fundamentals, actions=None, contract_blockers=None
    )

    assert missing["fullPool"] == complete["fullPool"]
    assert missing["qualityPassedCodes"] == []
    assert missing["selectionEvidenceSupplied"] is False
    assert all("selection_evidence_missing" in item["quality"]["blockers"] for item in missing["preview"])


def test_low_volume_name_is_not_removed_from_comprehensive_pool():
    quotes, fundamentals, actions = fixture()
    quotes["7777"]["volume"] = 1
    fundamentals["7777"]["revenueYoY"] = 25
    result = strategy_backtest.select_signal_candidates(
        quotes, fundamentals, "2026-08-12",
        {"byDate": {
            "2026-08-12": strategy_backtest.build_selection_evidence(
                "2026-08-12", DECISION, actions, []
            ),
        }},
    )

    assert "7777" in [item["code"] for item in result["fullPool"]]
    assert result["selectionEvidenceSupplied"] is True


def test_undated_historical_selection_evidence_is_rejected():
    quotes, fundamentals, actions = fixture()
    result = strategy_backtest.select_signal_candidates(
        quotes, fundamentals, "2026-08-12",
        {"actions": actions, "contractBlockers": []},
    )

    assert result["selectionEvidenceSupplied"] is False
    assert result["qualityPassedCodes"] == []


def test_future_naive_conflicted_or_mismatched_evidence_fails_closed():
    quotes, fundamentals, actions = fixture()
    mutations = []
    future = copy.deepcopy(actions)
    future["availableAt"] = "2099-01-01T00:00:00+08:00"
    mutations.append(strategy_backtest.build_selection_evidence("2026-08-12", DECISION, future, []))
    mutations.append(strategy_backtest.build_selection_evidence(
        "2026-08-12", "2026-08-12T14:00:00", actions, []
    ))
    conflicted = copy.deepcopy(actions)
    conflicted["conflictStatus"] = "provider_conflict"
    mutations.append(strategy_backtest.build_selection_evidence(
        "2026-08-12", DECISION, conflicted, []
    ))
    mutations.append(strategy_backtest.build_selection_evidence(
        "2026-08-11", "2026-08-11T14:00:00+08:00", actions, []
    ))
    after_cutoff = copy.deepcopy(actions)
    after_cutoff["availableAt"] = "2026-08-12T14:00:01+08:00"
    mutations.append(strategy_backtest.build_selection_evidence(
        "2026-08-12", DECISION, after_cutoff, []
    ))
    tampered = strategy_backtest.build_selection_evidence("2026-08-12", DECISION, actions, [])
    tampered["quality"] = "unverified"
    mutations.append(tampered)
    for evidence in mutations:
        result = strategy_backtest.select_signal_candidates(
            quotes, fundamentals, "2026-08-12", {"byDate": {"2026-08-12": evidence}}
        )
        assert result["selectionEvidenceSupplied"] is False
        assert result["qualityPassedCodes"] == []

    payload = parity_payload()
    payload["actions"]["availableAt"] = "2099-01-01T00:00:00+08:00"
    result = parity.run(payload, enabled=True)
    assert result["selectionParity"] is False
    assert result["selectionEvidenceShapeComplete"] is False
    assert "production_backtest_selection_mismatch" in result["blockers"]


def test_cutoff_tie_is_reported_and_never_certified_as_strategy_evidence():
    quotes, fundamentals, actions = fixture()
    for code in quotes:
        quotes[code]["volume"] = 100
    result = parity.run({
        **parity_payload(), "quotes": quotes, "fundamentals": fundamentals, "actions": actions,
    }, enabled=True)

    assert result["selectionParity"] is True
    assert result["cutoffTieDependent"] is True
    assert "cutoff_tie_dependent" in result["blockers"]
    assert result["strategyValidated"] is False


def test_payload_cannot_include_returns_or_self_asserted_certification():
    for mutation in ({"return": 0.2}, {"strategyValidated": True}, {"advice": True}):
        value = parity_payload()
        value.update(mutation)
        result = parity.run(value, enabled=True)
        assert result["selectionParity"] is False
        assert result["strategyValidated"] is False
        assert "input_contract_invalid" in result["blockers"]

    nested = parity_payload()
    nested["quotes"]["7777"]["total_return"] = 0.2
    result = parity.run(nested, enabled=True)
    assert result["selectionParity"] is False
    assert "input_contract_invalid" in result["blockers"]


def test_sensitive_nested_content_and_ambiguous_signal_dates_fail_closed():
    for path, key, value in (
        ("quotes", "apiToken", "SECRET"),
        ("quotes", "secret_value", "SECRET"),
        ("quotes", "token_value", "SECRET"),
        ("quotes", "private_key", "SECRET"),
        ("actions", "authorization", "Bearer SECRET"),
        ("actions", "authorization_value", "SECRET"),
        ("actions", "query_params", "code=7777"),
        ("actions", "headers_map", "x"),
        ("fundamentals", "raw", "payload"),
        ("fundamentals", "raw_payload", "payload"),
        ("fundamentals", "rows_data", "payload"),
        ("fundamentals", "url_value", "not-even-a-url"),
        ("actions", "sourceDocument", "https://example.test/private"),
    ):
        payload = parity_payload()
        if path == "quotes":
            payload[path]["7777"][key] = value
        elif path == "fundamentals":
            payload[path]["7777"][key] = value
        else:
            payload[path][key] = value
        result = parity.run(payload, enabled=True)
        assert result["selectionParity"] is False
        assert result["selectionEvidenceShapeComplete"] is False
        assert "input_contract_invalid" in result["blockers"]
    for invalid in ("2026-8-12", "2026-08-12T00:00:00", "secret"):
        payload = parity_payload()
        payload["signalDate"] = invalid
        result = parity.run(payload, enabled=True)
        assert result["selectionParity"] is False


def test_default_off_and_malformed_inputs_fail_closed():
    class Explodes(dict):
        def items(self):
            raise RuntimeError("boom")

    assert parity.run(Explodes())["mode"] == "disabled"
    result = parity.run(Explodes(), enabled=True)
    assert result["selectionParity"] is False
    assert result["strategyValidated"] is False


def test_parity_module_has_no_io_or_formal_gate_imports():
    tree = ast.parse((ROOT / "actual_comprehensive_selection_parity.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported.intersection({
        "os", "pathlib", "subprocess", "requests", "urllib", "socket",
        "psycopg", "supabase", "openai", "investment_advice_gate",
        "daily_report", "telegram",
    })


def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
