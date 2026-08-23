from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import actual_comprehensive_selection as selection
import paper_trading as paper
from provenance import record, schema_hash, stable_hash
from quote_provenance import available_at, build as build_quote_provenance


TAIPEI = timezone(timedelta(hours=8))
CODES = ("1101", "2330", "2454", "2881")


def sessions(count: int, start: date = date(2026, 1, 2)) -> list[str]:
    result = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def quote_payload(session: str, step: int = 0, *, missing: str | None = None) -> dict:
    quotes = {
        "0050": {"name": "台灣50", "price": 100.0 + step * 0.2, "volume": 8_000_000, "ma5": 99.0, "ma20": 98.0, "change": 0.2},
    }
    fundamentals = {}
    for index, code in enumerate(CODES):
        quotes[code] = {
            "name": f"公司{code}",
            "price": 100.0 + index * 5 + step * (1.0 - index * 0.1),
            "volume": 5_000_000 - index * 100_000,
            "ma5": 99.0,
            "ma20": 98.0,
            "change": 1.0,
        }
        fundamentals[code] = {
            "revenueYoY": 20.0 - index,
            "eps": 8.0,
            "roe": 18.0,
            "debtRatio": 25.0,
            "pe": 15.0,
            "pb": 2.0,
            "dividendYield": 3.0,
            "financialHistoryYears": 6,
        }
    if missing:
        quotes.pop(missing, None)
    retrieved = f"{session}T15:00:00+08:00"
    provenance = build_quote_provenance(
        session, retrieved, quotes, fundamentals,
        {"twse": sorted(quotes), "tpex": []},
    )
    fundamental_receipt = record(
        provider=paper.ACTION_SOURCE,
        dataset="TaiwanStockFinancialStatements,TaiwanStockBalanceSheet,TaiwanStockInfo",
        endpoint=paper.ACTION_ENDPOINT,
        scope={"codes": sorted(fundamentals), "start": session},
        retrieved_at=retrieved,
        effective_date=session,
        available_at=session,
        content=fundamentals,
        visibility="private_cache",
        conflict_status="no_conflict",
    )
    fundamental_receipt.update({
        "sourceDisagreements": {},
        "snapshotContentHash": stable_hash(fundamentals),
        "snapshotSchemaHash": schema_hash(fundamentals),
        "snapshotCodeCount": len(fundamentals),
        "availableAtBasis": "synthetic statutory-date fixture",
    })
    provenance["fundamentals"] = fundamental_receipt
    return {
        "updatedAt": f"{session} 14:00 Taipei time",
        "provenance": provenance,
        "quotes": quotes,
        "fundamentals": fundamentals,
        "history": {},
    }


def manifest_for(payload: dict, session: str) -> dict:
    pool = selection.rank_pool(payload["quotes"], payload["fundamentals"])
    preview = pool[:3]
    actions = {"queried_codes": [item[2] for item in preview], "failures": {}}
    assessed = selection.assess_ranked_preview({"comprehensive": preview}, actions, ())
    return {
        "schemaVersion": 1,
        "reportDate": session,
        "reportMode": "comprehensive",
        "phase": "final",
        "candidateOrder": [item["code"] for item in assessed],
        "previewCandidates": assessed,
    }


def action_payload(
    codes: tuple[str, ...] = CODES, *, session: str = "2026-01-02",
    failures: dict | None = None, events: list | None = None,
) -> dict:
    codes = tuple(sorted(codes))
    failures = copy.deepcopy(failures or {})
    events = sorted(copy.deepcopy(events or []), key=lambda row: (row["date"], row["code"]))
    start = (date.fromisoformat(session) - timedelta(days=365)).isoformat()
    updated = f"{session}T14:30:00+08:00"
    effective = max((row["date"] for row in events), default=session)
    conflict = "no_conflict" if not failures else "conflict_unresolved"
    provenance = record(
        provider=paper.ACTION_SOURCE,
        dataset=paper.ACTION_DATASET,
        endpoint=paper.ACTION_ENDPOINT,
        scope={"codes": list(codes), "start": start, "end": session},
        retrieved_at=updated,
        effective_date=effective,
        available_at=available_at(effective),
        content=events,
        visibility="private_cache",
        conflict_status=conflict,
    )
    provenance["availableAtBasis"] = (
        "modelled: 14:00 Taipei on the ex-dividend trading date, when the exchange "
        "publishes the reference price; not the provider fetch time"
    )
    return {
        "scope": "active candidate pool only; not full-market total-return coverage",
        "period": {"start": start, "end": session},
        "queried_codes": list(codes),
        "successful_codes": len(codes) - len(failures),
        "events": events,
        "failures": failures,
        "cache": {
            "schemaVersion": 1, "candidateKey": "a" * 64, "hits": 0,
            "refreshed": len(codes) - len(failures), "failed": len(failures),
            "ttlHours": 12.0, "overlapDays": 14,
            "lastEventDates": {code: None for code in codes},
        },
        "source": provenance["source"], "dataset": provenance["dataset"],
        "effectiveDate": provenance["effectiveDate"], "availableAt": provenance["availableAt"],
        "updatedAt": updated, "ingestedAt": updated,
        "conflictStatus": conflict, "provenance": provenance,
    }


def dividend_event(session: str, code: str, *, before: float = 110.0, reference: float = 100.0) -> dict:
    return {
        "date": session, "code": code, "market": "candidate_pool",
        "before_close": before, "reference_price": reference,
        "after_price": reference, "kind": "cash", "source": "FinMind TaiwanStockDividendResult",
    }


def rehash_ledger(value: dict) -> dict:
    previous = paper.GENESIS
    for index, event in enumerate(value["events"], 1):
        event["sequence"] = index
        event["previousHash"] = previous
        if event["eventType"] == "session_observation":
            payload = event["payload"]
            payload["observationDigest"] = paper._digest({
                key: item for key, item in payload.items() if key != "observationDigest"
            })
        elif event["eventType"] == "signal_decision":
            event["payload"]["decisionDigest"] = paper._digest(event["payload"]["material"])
        event["eventHash"] = paper._digest({key: item for key, item in event.items() if key != "eventHash"})
        previous = event["eventHash"]
    value["headHash"] = previous
    return value


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.manifest = self.root / "manifest.json"
        self.quotes = self.root / "quotes.json"
        self.actions = self.root / "actions.json"
        self.ledger = self.root / "ledger.json"
        self.progress = self.root / "progress.json"

    def tearDown(self):
        self.folder.cleanup()

    def write_inputs(self, session: str, step: int = 0, *, missing: str | None = None, actions: dict | None = None):
        payload = quote_payload(session, step, missing=missing)
        self.quotes.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest_for(payload, session), ensure_ascii=False), encoding="utf-8")
        self.actions.write_text(
            json.dumps(actions or action_payload(session=session), ensure_ascii=False),
            encoding="utf-8",
        )
        return payload

    def advance(self, session: str, step: int, *, now: datetime | None = None, missing: str | None = None, actions: dict | None = None):
        self.write_inputs(session, step, missing=missing, actions=actions)
        now = now or datetime.combine(date.fromisoformat(session) + timedelta(days=1), datetime.min.time(), TAIPEI).replace(hour=8)
        return paper.advance(
            self.manifest, self.quotes, self.actions, self.ledger, self.progress,
            now=now,
        )

    def test_first_run_observes_then_seals_one_paper_only_decision(self):
        session = sessions(1)[0]
        result = self.advance(session, 0)
        ledger = paper.load_ledger(self.ledger)

        self.assertEqual([event["eventType"] for event in ledger["events"]], ["session_observation", "signal_decision"])
        self.assertEqual(result["capturedSessionCount"], 1)
        self.assertEqual(result["cohortCount"], 1)
        self.assertTrue(result["paperOnly"])
        self.assertFalse(result["adviceEnabled"])
        self.assertFalse(result["tradingEnabled"])
        decision = ledger["events"][1]["payload"]["material"]
        self.assertEqual(decision["signalSession"], session)
        self.assertEqual(decision["entryConvention"], "next_captured_session_close_continuity_unverified")
        self.assertEqual(len(decision["rankedPool"]), 4)
        self.assertEqual(len(decision["topSlots"]), 3)

    def test_same_session_is_first_seen_and_later_manifest_changes_are_noops(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        first = self.ledger.read_bytes()
        self.advance(session, 0, now=datetime(2026, 1, 3, 9, tzinfo=TAIPEI))
        self.assertEqual(self.ledger.read_bytes(), first)

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["previewCandidates"][0]["score"] += 1
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        paper.advance(
            self.manifest, self.quotes, self.actions, self.ledger, self.progress,
            now=datetime(2026, 1, 3, 10, tzinfo=TAIPEI),
        )
        self.assertEqual(self.ledger.read_bytes(), first)

    def test_same_session_revised_quote_source_cannot_replace_first_seen_observation(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        payload = quote_payload(session, 1)
        self.quotes.write_text(json.dumps(payload), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest_for(payload, session)), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "same_session_observation_conflict"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 10, tzinfo=TAIPEI),
            )

    def test_twenty_session_primary_outcome_uses_next_close_and_never_enables_advice(self):
        days = sessions(22)
        result = None
        for step, session in enumerate(days):
            result = self.advance(session, step)
        self.assertIsNotNone(result)
        self.assertEqual(result["capturedSessionCount"], 22)
        self.assertEqual(result["cohortCount"], 2, "signals occur at captured offsets 0 and 20")
        first = result["cohorts"][0]
        five = first["outcomes"]["5"]["top3Diagnostic"]
        twenty = first["outcomes"]["20"]["top3Diagnostic"]
        sixty = first["outcomes"]["60"]["top3Diagnostic"]
        self.assertEqual(five["status"], "complete")
        self.assertEqual(twenty["status"], "complete")
        self.assertEqual(sixty["status"], "pending")
        self.assertGreater(twenty["priceExcessVs0050Pct"], 0)
        self.assertIn("priceExcessVsEligiblePoolPct", twenty)
        self.assertFalse(result["formalValidationPassed"])
        self.assertFalse(result["adviceEnabled"])
        self.assertFalse(result["tradingEnabled"])

    def test_missing_exit_censors_instead_of_shrinking_the_portfolio(self):
        days = sessions(7)
        first = self.advance(days[0], 0)
        selected = first["cohorts"][0]["selectedCodes"][0]
        result = first
        for step, session in enumerate(days[1:], 1):
            result = self.advance(session, step, missing=selected if step == 6 else None)
        outcome = result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]
        self.assertEqual(outcome["status"], "censored")
        self.assertEqual(outcome["reason"], f"unresolved_exit:{selected}")
        summary = result["horizons"]["5"]["top3DiagnosticVs0050"]
        self.assertEqual(summary["completeCohorts"], 0)
        self.assertEqual(summary["censoredCohorts"], 1)
        self.assertIsNone(summary["meanPct"])

    def test_late_decision_cannot_use_the_next_close_as_an_entry(self):
        days = sessions(2)
        late = datetime.combine(date.fromisoformat(days[1]), datetime.min.time(), TAIPEI).replace(hour=13, minute=30)
        self.advance(days[0], 0, now=late)
        result = self.advance(days[1], 1, now=datetime.combine(date.fromisoformat(days[1]), datetime.min.time(), TAIPEI).replace(hour=15))
        outcome = result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]
        self.assertEqual(outcome["status"], "pending")
        ledger = paper.load_ledger(self.ledger)
        replay = paper._replay(ledger)
        marks = paper._marks_by_decision(replay)
        key = result["cohorts"][0]["decisionKey"]
        self.assertFalse(marks[key][1]["entryTimingEligible"])

    def test_action_gap_keeps_price_diagnostic_but_blocks_total_return_coverage(self):
        days = sessions(7)
        result = None
        for step, session in enumerate(days):
            actions = action_payload(session=session, failures={"2330": "provider_down"})
            result = self.advance(session, step, actions=actions)
        outcome = result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]
        self.assertEqual(outcome["status"], "complete")
        self.assertFalse(outcome["totalReturnCoverageComplete"])
        self.assertIsNone(outcome["totalReturnNetPct"])

    def test_entry_day_action_is_excluded_but_post_entry_action_is_included(self):
        days = sessions(7)
        first = self.advance(days[0], 0)
        selected = first["cohorts"][0]["selectedCodes"][0]
        result = first
        for step, session in enumerate(days[1:], 1):
            events = [dividend_event(session, selected)] if step == 1 else []
            result = self.advance(
                session, step,
                actions=action_payload(session=session, events=events),
            )
        outcome = result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]
        self.assertTrue(outcome["totalReturnCoverageComplete"])
        self.assertEqual(outcome["totalReturnNetPct"], outcome["priceNetReturnPct"])

        self.ledger.unlink()
        self.progress.unlink()
        first = self.advance(days[0], 0)
        selected = first["cohorts"][0]["selectedCodes"][0]
        for step, session in enumerate(days[1:], 1):
            events = [dividend_event(session, selected)] if step == 2 else []
            result = self.advance(
                session, step,
                actions=action_payload(session=session, events=events),
            )
        outcome = result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]
        self.assertTrue(outcome["totalReturnCoverageComplete"])
        self.assertGreater(outcome["totalReturnNetPct"], outcome["priceNetReturnPct"])

    def test_complete_only_survivors_never_create_an_aggregate_mean(self):
        days = sessions(27)
        result = None
        second_selected = None
        for step, session in enumerate(days):
            if step == 21:
                prior = paper.build_progress(paper.load_ledger(self.ledger))
                second_selected = prior["cohorts"][1]["selectedCodes"][0]
            result = self.advance(
                session, step,
                missing=second_selected if step == 26 else None,
            )
        summary = result["horizons"]["5"]["top3DiagnosticVs0050"]
        self.assertEqual(summary["scheduledCohorts"], 2)
        self.assertEqual(summary["completeCohorts"], 1)
        self.assertEqual(summary["censoredCohorts"], 1)
        self.assertFalse(summary["completeSet"])
        self.assertIsNone(summary["meanPct"])
        self.assertTrue(summary["aggregateStatisticsSuppressed"])

    def test_captured_dates_cannot_be_reported_as_authenticated_performance(self):
        start = date(2026, 1, 2)
        days = [(start + timedelta(days=index)).isoformat() for index in range(7)]
        result = None
        for step, session in enumerate(days):
            result = self.advance(session, step)
        self.assertEqual(result["cohorts"][0]["outcomes"]["5"]["top3Diagnostic"]["status"], "complete")
        self.assertFalse(result["capturedSessionContinuityAuthenticated"])
        self.assertFalse(result["performanceEvidenceQualified"])
        self.assertIsNone(result["horizons"]["5"]["top3DiagnosticVs0050"]["meanPct"])

    def test_stale_activation_and_unbound_quote_content_fail_closed(self):
        stale = "2020-01-02"
        self.write_inputs(stale)
        with self.assertRaisesRegex(paper.PaperTradingError, "quote_session_not_current_for_activation"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

    def test_delayed_old_receipt_cannot_be_replayed_as_a_first_seen_entry(self):
        first = "2026-01-02"
        self.advance(first, 0)
        delayed = "2026-01-03"
        self.write_inputs(delayed, 1)
        with self.assertRaisesRegex(paper.PaperTradingError, "quote_receipt_too_old_for_first_seen_append"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 13, 8, tzinfo=TAIPEI),
            )

    def test_fresh_post_holiday_receipt_is_collected_but_continuity_stays_unverified(self):
        self.advance("2026-02-06", 0)
        result = self.advance("2026-02-20", 1)
        self.assertEqual(result["capturedSessionCount"], 2)
        self.assertFalse(result["capturedSessionContinuityAuthenticated"])
        self.assertFalse(result["performanceEvidenceQualified"])

    def test_long_holiday_rerun_of_an_existing_session_is_an_idempotent_noop(self):
        session = "2026-02-06"
        original = self.advance(session, 0)
        original_events = original["eventCount"]
        self.write_inputs(session, 0)
        result = paper.advance(
            self.manifest, self.quotes, self.actions, self.ledger, self.progress,
            now=datetime(2026, 2, 20, 8, tzinfo=TAIPEI),
        )
        self.assertEqual(result["eventCount"], original_events)
        self.assertEqual(result["capturedSessionCount"], 1)
        self.assertFalse(result["performanceEvidenceQualified"])

    def test_fundamentals_are_bound_to_their_provenance_before_selection(self):
        session = sessions(1)[0]
        payload = self.write_inputs(session)
        payload["fundamentals"]["2881"]["roe"] = 999999.0
        self.quotes.write_text(json.dumps(payload), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest_for(payload, session)), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "fundamentals_content_hash_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

    def test_action_receipt_cannot_claim_future_availability(self):
        session = sessions(1)[0]
        self.write_inputs(session)
        actions = action_payload(session=session)
        future = "2026-01-03"
        actions["period"]["end"] = future
        actions["effectiveDate"] = future
        actions["availableAt"] = available_at(future)
        actions["updatedAt"] = f"{future}T08:00:00+08:00"
        actions["ingestedAt"] = actions["updatedAt"]
        provenance = actions["provenance"]
        provenance["scopeHash"] = stable_hash({
            "codes": actions["queried_codes"],
            "start": actions["period"]["start"],
            "end": future,
        })
        provenance["effectiveDate"] = future
        provenance["availableAt"] = actions["availableAt"]
        provenance["retrievedAt"] = actions["updatedAt"]
        provenance["ingestedAt"] = actions["ingestedAt"]
        self.actions.write_text(json.dumps(actions), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "action_receipt_time_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, 30, tzinfo=TAIPEI),
            )

    def test_public_time_boundary_fails_with_paper_error_not_raw_attribute_error(self):
        with self.assertRaisesRegex(paper.PaperTradingError, "current_time_not_aware"):
            paper.build_progress(paper._new_ledger(), generated_at=object())
        for value in (False, 0, 0.0, "", [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(paper.PaperTradingError, "current_time_not_aware"):
                    paper.build_progress(paper._new_ledger(), generated_at=value)

    def test_json_decoder_resource_boundaries_and_scalar_types_fail_closed(self):
        session = sessions(1)[0]
        payload = self.write_inputs(session)
        payload["provenance"]["fundamentals"]["quality"] = []
        self.quotes.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "fundamentals_content_hash_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

        self.write_inputs(session)
        for hostile in (
            "[" * 5000 + "0" + "]" * 5000,
            '{"value":' + "9" * 5000 + "}",
        ):
            with self.subTest(kind=hostile[:1], length=len(hostile)):
                self.manifest.write_text(hostile, encoding="utf-8")
                with self.assertRaisesRegex(paper.PaperTradingError, "invalid_json"):
                    paper.advance(
                        self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                        now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
                    )

        self.advance(session, 0)
        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        ledger["events"][0]["eventType"] = []
        rehash_ledger(ledger)
        self.ledger.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "event_type_invalid"):
            paper.load_ledger(self.ledger)

    def test_two_output_commit_rejects_a_bad_progress_target_before_writing_ledger(self):
        session = sessions(1)[0]
        self.write_inputs(session)
        self.progress.mkdir()
        with self.assertRaisesRegex(paper.PaperTradingError, "output_path_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )
        self.assertFalse(self.ledger.exists())

    def test_nested_rows_and_surrogate_blockers_fail_with_paper_errors(self):
        session = sessions(1)[0]
        payload = self.write_inputs(session)
        manifest = manifest_for(payload, session)
        payload["fundamentals"]["1101"] = []
        provenance = payload["provenance"]["fundamentals"]
        provenance["snapshotContentHash"] = stable_hash(payload["fundamentals"])
        provenance["snapshotSchemaHash"] = schema_hash(payload["fundamentals"])
        provenance["snapshotCodeCount"] = len(payload["fundamentals"])
        self.quotes.write_text(json.dumps(payload), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "quote_snapshot_row_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

        payload = self.write_inputs(session)
        manifest = manifest_for(payload, session)
        manifest["previewCandidates"][0]["quality"] = {"passed": False, "blockers": ["\ud800"]}
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "candidate_quality_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )
        current = sessions(1)[0]
        payload = self.write_inputs(current)
        payload["quotes"]["1101"]["price"] = 9999.0
        self.quotes.write_text(json.dumps(payload), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest_for(payload, current)), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "quote_content_hash_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

    def test_manifest_types_dates_and_action_receipt_are_not_caller_assertions(self):
        session = sessions(1)[0]
        payload = self.write_inputs(session)
        manifest = manifest_for(payload, session)
        manifest["previewCandidates"][0]["rank"] = True
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "manifest_candidate_projection_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )
        manifest = manifest_for(payload, session)
        manifest["reportDate"] = "2099-01-01"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "manifest_report_date_invalid"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )
        self.manifest.write_text(json.dumps(manifest_for(payload, session)), encoding="utf-8")
        self.actions.write_text(json.dumps({"queried_codes": list(CODES), "failures": {}, "events": []}))
        with self.assertRaisesRegex(paper.PaperTradingError, "action_receipt_missing_fields"):
            paper.advance(
                self.manifest, self.quotes, self.actions, self.ledger, self.progress,
                now=datetime(2026, 1, 3, 8, tzinfo=TAIPEI),
            )

    def test_rehashed_malformed_cohort_marks_fail_during_ledger_load(self):
        session = sessions(1)[0]
        self.advance(session, 0)
        value = json.loads(self.ledger.read_text(encoding="utf-8"))
        value["events"][0]["payload"]["cohortMarks"] = [{"bad": 1}]
        rehash_ledger(value)
        self.ledger.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "cohort_mark_set_invalid"):
            paper.load_ledger(self.ledger)

    def test_observation_seals_recomputed_quote_and_full_action_receipt_hashes(self):
        session = sessions(1)[0]
        payload = self.advance(session, 0)
        ledger = paper.load_ledger(self.ledger)
        observation = ledger["events"][0]["payload"]
        quote_rows = json.loads(self.quotes.read_text(encoding="utf-8"))["quotes"]
        self.assertEqual(observation["sourceContentHash"], stable_hash(quote_rows))
        self.assertRegex(observation["actionReceiptHash"], r"^[0-9a-f]{64}$")
        self.assertFalse(payload["performanceEvidenceQualified"])

    def test_action_universe_unions_current_preview_and_open_cohorts(self):
        day = sessions(1)[0]
        self.advance(day, 0)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["previewCandidates"] = [{"code": "3008"}]
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        output = self.root / "universe.json"
        result = paper.action_universe(self.manifest, self.ledger, output)
        codes = [item["code"] for item in result["previewCandidates"]]
        self.assertIn("3008", codes)
        for code in paper.build_progress(paper.load_ledger(self.ledger))["cohorts"][0]["selectedCodes"]:
            self.assertIn(code, codes)
        self.assertTrue(result["paperOnly"])

    def test_tampered_chain_and_nonfinite_json_fail_closed(self):
        day = sessions(1)[0]
        self.advance(day, 0)
        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        ledger["events"][0]["payload"]["sourceContentHash"] = "f" * 64
        self.ledger.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "event_hash_invalid"):
            paper.load_ledger(self.ledger)
        self.ledger.write_text('{"schemaVersion":NaN}', encoding="utf-8")
        with self.assertRaisesRegex(paper.PaperTradingError, "non_finite_json"):
            paper.load_ledger(self.ledger)

    def test_cli_has_no_date_override_and_source_contains_no_execution_side_effects(self):
        source = Path(paper.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "investment_advice_gate", "promotion_status", "telegram", "alpaca",
            "interactive_brokers", "urllib.request", "requests.", "subprocess",
        ):
            self.assertNotIn(forbidden, source.lower())
        self.assertNotIn('add_argument("--date"', source)
        self.assertIn('"adviceEnabled": False', source)
        self.assertIn('"tradingEnabled": False', source)

    def test_daily_workflow_runs_only_the_isolated_paper_path_and_persists_its_ledger(self):
        root = Path(paper.__file__).resolve().parent
        workflow = (root / ".github" / "workflows" / "daily-report.yml").read_text(encoding="utf-8")
        safety = (root / ".github" / "workflows" / "pipeline-safety-validation.yml").read_text(encoding="utf-8")
        self.assertIn("python paper_trading.py action-universe", workflow)
        self.assertIn("python paper_trading.py advance", workflow)
        self.assertIn("paper_data/comprehensive-v1.json", workflow)
        self.assertIn("data/prospective-paper-progress.json", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("Require and persist prospective paper ledger", workflow)
        self.assertIn('git rebase --autostash "origin/$GITHUB_REF_NAME"', workflow)
        self.assertGreater(
            workflow.index("Build isolated paper-cohort action universe"),
            workflow.index("Push to Telegram"),
        )
        paper_block = workflow[workflow.index("Build isolated paper-cohort action universe"):]
        self.assertIn(
            "!cancelled() && github.event_name == 'schedule' && steps.paper_universe.outcome == 'success'",
            paper_block,
        )
        self.assertIn(
            "steps.paper_universe.outcome == 'success' && steps.paper_actions.outcome == 'success'",
            paper_block,
        )
        for required in (
            "id: paper_universe", "id: paper_actions", "id: paper_advance",
            "github.event_name == 'schedule'", "continue-on-error: true",
            "Upload prospective paper artifact", "PAPER_ADVANCE_OUTCOME",
            "--as-of-quotes quotes.json",
            'git fetch origin "+refs/heads/$GITHUB_REF_NAME:refs/remotes/origin/$GITHUB_REF_NAME"',
        ):
            self.assertIn(required, paper_block)
        self.assertIn("fetch-depth: 0", workflow)
        for forbidden in ("OPENAI_API_KEY", "investment-advice-gate", "strategy_data/recommendations", "inputs.record"):
            self.assertNotIn(forbidden, paper_block)
        persistence = workflow[
            workflow.index("Persist verified financial data"):
            workflow.index("Push to Telegram")
        ]
        self.assertNotIn("paper_data/comprehensive-v1.json", persistence)
        self.assertIn('      - "paper_trading.py"', safety)
        self.assertIn("          paper_trading.py", safety)


if __name__ == "__main__":
    unittest.main()
