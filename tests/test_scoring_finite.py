import math
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import actual_comprehensive_selection as selection
import candidate_manifest
import data_contract
import production_strategy_validation_preflight as preflight
import scoring
import strategy_backtest
import strategy_tracker


def complete_fundamentals():
    return {
        "revenueYoY": 15.0,
        "eps": 10.0,
        "roe": 20.0,
        "debtRatio": 25.0,
        "pe": 15.0,
        "pb": 2.0,
        "dividendYield": 3.0,
        "financialHistoryYears": 5,
    }


def complete_quote(**changes):
    value = {
        "name": "fixture",
        "price": 100.0,
        "volume": 1000,
        "change": 1.0,
        "ma5": 99.0,
        "ma20": 98.0,
    }
    value.update(changes)
    return value


def contract_inputs(hostile_value):
    stamp = "2026-08-20T14:00:00+08:00"
    quote_data = {
        "updatedAt": stamp,
        "quotes": {"2330": complete_quote(change=hostile_value)},
        "fundamentals": {"2330": complete_fundamentals()},
        "provenance": {
            "quote": {
                "source": "official", "dataset": "quote",
                "effectiveDate": "2026-08-20", "availableAt": stamp,
                "ingestedAt": stamp, "conflictStatus": "no_conflict",
            },
            "fundamentals": {
                "source": "official", "dataset": "fundamentals",
                "effectiveDate": "2026-08-20", "availableAt": stamp,
                "ingestedAt": stamp, "conflictStatus": "no_conflict",
            },
        },
    }
    actions = {
        "source": "official", "dataset": "actions",
        "period": {"end": "2026-08-20"}, "availableAt": stamp,
        "updatedAt": stamp, "conflictStatus": "no_conflict", "events": [],
    }
    news = {"updatedAt": stamp, "items": []}
    pit = {"certified": True, "generatedAt": stamp, "availableAt": stamp}
    return quote_data, actions, news, pit


class NumericDomainTests(unittest.TestCase):
    def test_successor_identity_freezes_the_numeric_domain_without_self_registration(self):
        spec = preflight.strategy_spec()
        domain = spec["selection"]["numericDomain"]
        self.assertEqual(
            preflight.POLICY_VERSION,
            "production-strategy-validation-preflight-benchmark-accounting-v4",
        )
        self.assertEqual(spec["strategyIdentity"], "production-comprehensive-finite-input-v2")
        self.assertEqual(spec["strategyTrackerVersion"], "2.1")
        self.assertEqual(spec["registrationStatus"], "successor_unregistered_requires_new_preregistration")
        self.assertEqual(domain["maximumAbsoluteValue"], scoring.MAX_NUMBER_ABS)
        self.assertTrue(domain["finiteFloatRequired"])
        self.assertTrue(domain["positivePriceRequired"])
        self.assertTrue(domain["derivedValuesMustRemainInDomain"])
        self.assertEqual(domain["invalidOrNegativeVolumeSortValue"], 0)

    def test_number_accepts_only_bounded_finite_builtin_numbers(self):
        for value in (0, -1, scoring.MAX_NUMBER_ABS, 0.0, -1.5, 1e18):
            with self.subTest(value=value):
                self.assertTrue(scoring.number(value))

        class IntSubclass(int):
            pass

        class FloatSubclass(float):
            pass

        invalid = (
            True, False, None, "1",
            float("nan"), float("inf"), float("-inf"),
            scoring.MAX_NUMBER_ABS + 1,
            -(scoring.MAX_NUMBER_ABS + 1),
            10**10_000,
            IntSubclass(1), FloatSubclass(1.0),
        )
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(scoring.number(value))

    def test_ranking_volume_canonicalizes_every_zero_form(self):
        for value in (0, 0.0, -0.0, None, float("nan"), -1):
            with self.subTest(value=value):
                normalized = scoring.ranking_volume({"volume": value})
                self.assertIs(type(normalized), int)
                self.assertEqual(normalized, 0)
        self.assertEqual(scoring.ranking_volume({"volume": 1.5}), 1.5)

    def test_nonfinite_factors_are_missing_not_scores(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertIsNone(scoring.change_score(value))
                self.assertIsNone(scoring.trend_score(value, 100.0, False))
                self.assertIsNone(scoring.trend_score(100.0, value, True))
                metric = scoring.metrics(
                    complete_quote(change=value, ma5=value, ma20=value),
                    {**complete_fundamentals(), "revenueYoY": value},
                )
                self.assertIsNone(metric["change"])
                self.assertIsNone(metric["trend5"])
                self.assertIsNone(metric["trend20"])
                self.assertIsNone(metric["revenue"])

    def test_derived_nonfinite_trend_is_missing(self):
        self.assertIsNone(scoring.trend_score(1e18, 5e-324, True))

    def test_each_nonfinite_factor_is_removed_from_exact_coverage(self):
        cases = {
            ("fund", "revenueYoY"): 15,
            ("fund", "eps"): 12,
            ("fund", "roe"): 12,
            ("fund", "debtRatio"): 10,
            ("fund", "pe"): 10,
            ("fund", "pb"): 6,
            ("fund", "dividendYield"): 7,
            ("quote", "ma20"): 12,
            ("quote", "ma5"): 7,
            ("quote", "change"): 9,
        }
        for (owner, field), missing_weight in cases.items():
            for hostile in (float("nan"), float("inf"), float("-inf")):
                quote = complete_quote()
                fund = complete_fundamentals()
                (quote if owner == "quote" else fund)[field] = hostile
                with self.subTest(owner=owner, field=field, hostile=hostile):
                    _score, coverage = scoring.score_quote(
                        quote, fund, "comprehensive"
                    )
                    self.assertEqual(coverage, 100 - missing_weight)


class CandidateBoundaryTests(unittest.TestCase):
    def ranked(self, quote, code="2330"):
        return scoring.candidates(
            "comprehensive", {code: quote}, {code: complete_fundamentals()},
            picks=None,
        )

    def test_nonfinite_or_nonpositive_price_never_enters_pool(self):
        for price in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.subTest(price=price):
                self.assertEqual(self.ranked(complete_quote(price=price)), [])

    def test_only_four_ascii_digit_codes_are_eligible(self):
        for code in ("１２３４", "23300", "233", 2330):
            with self.subTest(code=code):
                self.assertEqual(self.ranked(complete_quote(), code=code), [])

    def test_invalid_volume_cannot_control_sort_or_public_digest(self):
        quotes = {
            "2330": complete_quote(volume=float("inf")),
            "2454": complete_quote(volume=float("nan")),
            "2881": complete_quote(volume=-1),
            "1101": complete_quote(volume=0),
        }
        funds = {code: complete_fundamentals() for code in quotes}
        result = selection.rank_and_assess(
            quotes, funds,
            actions={"queried_codes": list(quotes), "failures": {}},
            contract_blockers=[],
        )

        self.assertEqual(
            {row["code"]: row["volume"] for row in result["fullPool"]},
            {"2881": 0, "2454": 0, "2330": 0, "1101": 0},
        )
        self.assertEqual(
            [row["code"] for row in result["fullPool"]],
            ["2881", "2454", "2330", "1101"],
        )
        self.assertTrue(all(math.isfinite(row["volume"]) for row in result["fullPool"]))
        self.assertEqual(len(result["selectionDigest"]), 64)

    def test_non_string_name_cannot_break_public_digest(self):
        quotes = {"2330": complete_quote(name=float("nan"))}
        funds = {"2330": complete_fundamentals()}
        result = selection.rank_and_assess(
            quotes, funds,
            actions={"queried_codes": ["2330"], "failures": {}},
            contract_blockers=[],
        )
        self.assertEqual(result["preview"][0]["name"], "2330")
        self.assertEqual(len(result["selectionDigest"]), 64)

        for hostile in ("\ud800", "x" * 257, "bad\x00name", 10**10_000):
            quotes["2330"]["name"] = hostile
            result = selection.rank_and_assess(
                quotes, funds,
                actions={"queried_codes": ["2330"], "failures": {}},
                contract_blockers=[],
            )
            self.assertEqual(result["preview"][0]["name"], "2330")
            self.assertEqual(len(result["selectionDigest"]), 64)

    def test_nonfinite_required_inputs_or_history_never_pass_quality(self):
        for field in ("revenueYoY", "eps", "roe", "debtRatio", "financialHistoryYears"):
            for hostile in (float("nan"), float("inf"), float("-inf")):
                quote = complete_quote()
                fund = complete_fundamentals()
                fund[field] = hostile
                result = selection.rank_and_assess(
                    {"2330": quote}, {"2330": fund},
                    actions={"queried_codes": ["2330"], "failures": {}},
                    contract_blockers=[],
                )
                with self.subTest(field=field, hostile=hostile):
                    self.assertEqual([row["code"] for row in result["fullPool"]], ["2330"])
                    self.assertEqual(result["qualityPassedCodes"], [])
                    blocker = (
                        "fewer_than_five_financial_years"
                        if field == "financialHistoryYears"
                        else f"missing_{field}"
                    )
                    self.assertIn(blocker, result["preview"][0]["quality"]["blockers"])

    def test_optional_nonfinite_factor_is_missing_without_global_exclusion(self):
        quote = complete_quote()
        fund = {**complete_fundamentals(), "pb": float("nan")}
        result = selection.rank_and_assess(
            {"2330": quote}, {"2330": fund},
            actions={"queried_codes": ["2330"], "failures": {}},
            contract_blockers=[],
        )
        self.assertEqual(result["fullPool"][0]["coverage"], 94)
        self.assertEqual(result["qualityPassedCodes"], ["2330"])

    def test_finite_inputs_keep_existing_score_and_volume_order(self):
        quotes = {
            "2330": complete_quote(volume=100),
            "2454": complete_quote(volume=300),
            "2881": complete_quote(volume=200),
        }
        funds = {code: complete_fundamentals() for code in quotes}
        ranked = scoring.candidates("comprehensive", quotes, funds, picks=None)
        self.assertEqual([item[2] for item in ranked], ["2454", "2881", "2330"])
        self.assertEqual(
            scoring.score_quote(quotes["2330"], funds["2330"], "comprehensive"),
            (78, 100),
        )


class DownstreamBoundaryTests(unittest.TestCase):
    def test_manifest_evidence_hash_rejects_hostile_json_without_escaping(self):
        hostile_documents = (
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":"\\ud800"}',
            '{"value":' + "9" * 5000 + '}',
        )
        for document in hostile_documents:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "evidence.json"
                path.write_text(document, encoding="utf-8")
                with self.subTest(document=document[:20]):
                    self.assertIsNone(candidate_manifest.evidence_sha256(path))

    def test_contract_marks_hostile_numeric_payload_invalid(self):
        for hostile in (
            float("nan"), float("inf"), float("-inf"),
            scoring.MAX_NUMBER_ABS + 1, 10**1000,
        ):
            quote_data, actions, news, pit = contract_inputs(hostile)
            result = data_contract.build_contract(
                quote_data, actions, news, pit,
                generated_at="2026-08-20T06:00:00+00:00",
            )
            quote_record = next(
                row for row in result["records"] if row["name"] == "quote"
            )
            with self.subTest(hostile=type(hostile).__name__):
                self.assertIsNone(quote_record["evidenceHash"])
                self.assertEqual(quote_record["quality"], "payload_invalid")
                self.assertFalse(result["certified"])
                self.assertIn("contract_quote_payload_invalid", result["blockers"])
                self.assertEqual(len(result["contractHash"]), 64)

    def test_contract_never_stringifies_hostile_provenance_metadata(self):
        for hostile in (float("nan"), float("inf"), 10**1000, True, []):
            quote_data, actions, news, pit = contract_inputs(1.0)
            quote_data["provenance"]["quote"].update({
                "source": hostile,
                "dataset": hostile,
                "effectiveDate": hostile,
                "availableAt": hostile,
                "ingestedAt": hostile,
                "conflictStatus": hostile,
            })
            result = data_contract.build_contract(
                quote_data, actions, news, pit,
                generated_at="2026-08-20T06:00:00+00:00",
            )
            quote_record = next(
                row for row in result["records"] if row["name"] == "quote"
            )
            with self.subTest(hostile=type(hostile).__name__):
                self.assertFalse(result["certified"])
                self.assertEqual(quote_record["source"], "unknown")
                self.assertIsNone(quote_record["effectiveDate"])
                self.assertIsNone(quote_record["availableAt"])
                self.assertNotEqual(quote_record["quality"], "verified")

    def test_contract_never_certifies_an_unknown_dataset_with_valid_source(self):
        for hostile in (float("nan"), float("inf"), 10**1000, True, []):
            quote_data, actions, news, pit = contract_inputs(1.0)
            quote_data["provenance"]["quote"]["dataset"] = hostile
            result = data_contract.build_contract(
                quote_data, actions, news, pit,
                generated_at="2026-08-20T06:00:00+00:00",
            )
            quote_record = next(
                row for row in result["records"] if row["name"] == "quote"
            )
            with self.subTest(hostile=type(hostile).__name__):
                self.assertFalse(result["certified"])
                self.assertEqual(quote_record["sourceDataset"], "unknown")
                self.assertEqual(quote_record["quality"], "dataset_missing")
                self.assertIn("contract_quote_dataset_missing", result["blockers"])

    def test_contract_rejects_alias_cycle_and_aggregate_oversize(self):
        alias = []
        aliased_payload = {"a": alias, "b": alias}
        cycle = []
        cycle.append(cycle)
        self.assertIsNone(data_contract._payload_hash(aliased_payload))
        self.assertIsNone(data_contract._payload_hash(cycle))
        self.assertIsNone(
            data_contract._payload_hash(
                ["x" * data_contract.MAX_STRING_BYTES]
                * (data_contract.MAX_JSON_BYTES // data_contract.MAX_STRING_BYTES + 1)
            )
        )

    def test_tracker_snapshot_sanitizes_nonfinite_optional_values(self):
        fund = {
            **complete_fundamentals(),
            "pb": float("nan"),
            "financialHistoryYears": float("inf"),
        }
        snapshot = strategy_tracker._decision_snapshot(
            "2026-08-20", 79, 94, complete_quote(), fund
        )
        self.assertIsNone(snapshot["snapshot"]["pb"])
        self.assertFalse(snapshot["dataCompleteness"]["fiveYearHistory"])
        self.assertIn("近五年財務歷史不足", snapshot["riskFlags"])

    def test_tracker_persists_only_sanitized_name_and_numbers(self):
        ranked = {
            "comprehensive": [(
                79, 94, "2330",
                complete_quote(name=float("nan")),
                {**complete_fundamentals(), "pb": float("nan")},
            )]
        }
        quote_data = {
            "updatedAt": "2026-08-20",
            "quotes": {"0050": {"price": float("inf")}},
            "history": {},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recommendations.json"
            state = strategy_tracker.record_recommendations(
                "2026-08-20", "comprehensive", ranked, quote_data, path=path
            )
            row = state["recommendations"][0]
            self.assertEqual(row["name"], "2330")
            self.assertIsNone(row["benchmarkEntryPrice"])
            self.assertIsNone(row["decisionRecord"]["snapshot"]["pb"])
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("NaN", serialized)
            self.assertNotIn("Infinity", serialized)

    def test_tracker_extreme_finite_prices_never_create_infinite_outcomes(self):
        ranked = {
            "comprehensive": [(
                78, 100, "2330",
                complete_quote(price=5e-324),
                complete_fundamentals(),
            )]
        }
        history = {
            "2330": [
                {"date": f"2026-08-{day:02d}", "close": 1e18}
                for day in range(21, 26)
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recommendations.json"
            state = strategy_tracker.record_recommendations(
                "2026-08-20", "comprehensive", ranked,
                {"updatedAt": "2026-08-20", "quotes": {}, "history": history},
                path=path,
            )
            outcome = state["recommendations"][0]["outcomes"]["5"]
            self.assertEqual(outcome["status"], "pending")
            self.assertEqual(
                outcome["reason"], "derived_return_out_of_numeric_domain"
            )
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("Infinity", serialized)
            self.assertNotIn("NaN", serialized)

        self.assertEqual(
            strategy_tracker._extend_pool_trail(
                {}, history, {"2330": 5e-324}, "2026-08-20"
            ),
            {},
        )
        self.assertEqual(
            strategy_tracker._extend_dividend_factors(
                {}, [{
                    "code": "2330", "date": "2026-08-21",
                    "before_close": 1e18, "reference_price": 5e-324,
                }], "2330", "2026-08-20"
            ),
            {},
        )
        reverse_trail = {
            f"2026-08-{day:02d}": 5e-324 for day in range(21, 26)
        }
        reverse = strategy_tracker._settle(
            {}, reverse_trail, 1e18, {}, None
        )["5"]
        self.assertEqual(reverse["status"], "pending")
        self.assertEqual(
            reverse["reason"], "derived_return_out_of_numeric_domain"
        )
        self.assertIsNone(strategy_tracker._bounded_ratio(5e-324, 1e18))
        self.assertIsNone(strategy_tracker._bounded_ratio(1e18, 5e-324))
        self.assertIsNone(strategy_tracker._dividend_factor({
            "2026-08-21": 5e-324,
            "2026-08-22": 5e-324,
        }, "2026-08-22"))
        combined_underflow = strategy_tracker._settle(
            {}, reverse_trail, 1.0, {}, None,
            dividend_factors={"2026-08-21": 0.001},
        )["5"]
        self.assertEqual(combined_underflow["status"], "pending")
        self.assertEqual(
            combined_underflow["reason"],
            "derived_return_out_of_numeric_domain",
        )
        exact_combined = strategy_tracker._settle(
            {}, reverse_trail, 1.0, {}, None,
            dividend_factors={"2026-08-21": 5e-324},
        )["5"]
        self.assertEqual(exact_combined["status"], "pending")
        tiny_factor = strategy_tracker._settle(
            {}, {f"2026-08-{day:02d}": 1.0 for day in range(21, 26)},
            1.0, {}, None, dividend_factors={"2026-08-21": 5e-324},
        )["5"]
        self.assertEqual(tiny_factor["status"], "pending")

    def test_backtest_factor_builder_shares_price_and_volume_domain(self):
        history = [
            {"2330": (100.0 + index, 1000)}
            for index in range(strategy_backtest.MA_LONG)
        ]
        hostile_volume = copy.deepcopy(history)
        hostile_volume[-1]["2330"] = (120.0, float("nan"))
        quote = strategy_backtest.factor_quotes(
            hostile_volume, strategy_backtest.MA_LONG - 1, 0
        )["2330"]
        self.assertEqual(quote["volume"], 0)
        self.assertNotIn(
            "2330",
            strategy_backtest.factor_quotes(
                hostile_volume, strategy_backtest.MA_LONG - 1, 1
            ),
        )

        negative_zero_volume = copy.deepcopy(history)
        negative_zero_volume[-1]["2330"] = (120.0, -0.0)
        normalized = strategy_backtest.factor_quotes(
            negative_zero_volume, strategy_backtest.MA_LONG - 1, 0
        )["2330"]["volume"]
        self.assertIs(type(normalized), int)
        self.assertEqual(json.dumps(normalized), "0")

        for hostile in (float("nan"), float("inf"), float("-inf"), 0, -1):
            bad = copy.deepcopy(history)
            bad[-1]["2330"] = (hostile, 1000)
            with self.subTest(hostile=hostile):
                self.assertNotIn(
                    "2330",
                    strategy_backtest.factor_quotes(
                        bad, strategy_backtest.MA_LONG - 1, 0
                    ),
                )

        overflow_change = copy.deepcopy(history)
        overflow_change[-2]["2330"] = (5e-324, 1000)
        overflow_change[-1]["2330"] = (1e18, 1000)
        self.assertIsNone(
            strategy_backtest.factor_quotes(
                overflow_change, strategy_backtest.MA_LONG - 1, 0
            )["2330"]["change"]
        )

    def test_backtest_pe_derivation_rejects_invalid_or_overflowing_inputs(self):
        valid = strategy_backtest.fundamental_records(
            {"2330": {"price": 600.0}}, {"2330": {"eps": 30.0}}
        )
        self.assertEqual(valid["2330"]["pe"], 20.0)

        for eps in (
            float("nan"), float("inf"), float("-inf"),
            scoring.MAX_NUMBER_ABS + 1, 10**1000, 5e-324,
        ):
            result = strategy_backtest.fundamental_records(
                {"2330": {"price": 1e18}},
                {"2330": {"eps": eps, "pe": 7.0}},
            )
            with self.subTest(eps=type(eps).__name__):
                self.assertNotIn("pe", result["2330"])

    def test_daily_report_keeps_non_four_digit_market_rows_but_never_formats_inf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "quotes.json").write_text(json.dumps({
                "updatedAt": "2026-08-20T14:00:00+08:00",
                "quotes": {
                    "006208": {
                        "name": "ETF", "price": 100.0,
                        "volume": float("inf"), "change": float("inf"),
                    },
                    "2330": complete_quote(volume=10),
                },
                "fundamentals": {},
            }), encoding="utf-8")
            for name, payload in (
                ("actions.json", {}), ("gate.json", {}),
                ("news.json", {"items": []}), ("pit.json", {}),
                ("access.json", {}),
            ):
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            env = {
                **os.environ,
                "REPORT_PHASE": "preview",
                "REPORT_OUTPUT": str(root / "report.txt"),
                "CANDIDATE_MANIFEST": str(root / "manifest.json"),
                "DATA_CONTRACT_PATH": str(root / "contract.json"),
                "CANDIDATE_ACTIONS": str(root / "actions.json"),
                "ADVICE_GATE_PATH": str(root / "gate.json"),
                "MARKET_NEWS": str(root / "news.json"),
                "PIT_STATUS_PATH": str(root / "pit.json"),
                "DATA_ACCESS_STATUS": str(root / "access.json"),
            }
            subprocess.run(
                [sys.executable, "-B", str(ROOT / "daily_report.py")],
                cwd=root, env=env, check=True, capture_output=True, text=True,
            )
            report = (root / "report.txt").read_text(encoding="utf-8")
            self.assertIn("006208", report)
            self.assertNotIn("+inf", report.lower())
            self.assertIn("資料不可用", report)


if __name__ == "__main__":
    unittest.main()
