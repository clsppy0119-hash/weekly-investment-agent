import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import finmind_actions
import market_news
from quote_provenance import build as build_quote_provenance


class MarketEvidenceCacheTests(unittest.TestCase):
    def test_explicit_empty_paper_universe_never_falls_back_to_the_legacy_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tracker = root / "tracker.json"
            manifest = root / "paper-universe.json"
            tracker.write_text(json.dumps({
                "recommendations": [{"date": "2026-08-06", "code": "2330"}],
            }), encoding="utf-8")
            manifest.write_text(json.dumps({"previewCandidates": []}), encoding="utf-8")
            self.assertEqual(finmind_actions.active_codes(tracker, manifest), [])
            with self.assertRaisesRegex(ValueError, "missing"):
                finmind_actions.active_codes(tracker, root / "missing.json")

    def test_paper_action_cutoff_is_derived_from_bound_quote_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotes.json"
            rows = {"2330": {"price": 100.0}}
            fundamentals = {"2330": {"eps": 8.0}}
            provenance = build_quote_provenance(
                "2026-08-06", "2026-08-06T15:00:00+08:00",
                rows, fundamentals, {"twse": ["2330"], "tpex": []},
            )
            path.write_text(json.dumps({
                "quotes": rows, "fundamentals": fundamentals, "provenance": provenance,
            }), encoding="utf-8")
            self.assertEqual(finmind_actions.quote_session(path), date(2026, 8, 6))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["quotes"]["2330"]["price"] = 999.0
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not bound"):
                finmind_actions.quote_session(path)

    def test_later_cache_hit_is_clipped_to_the_quote_session_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "actions.json"
            now = datetime(2026, 8, 7, 1, tzinfo=timezone.utc)
            cache.write_text(json.dumps({
                "schemaVersion": 1,
                "entries": {"2330": {
                    "queriedThrough": "2026-08-07",
                    "verifiedAt": (now - timedelta(hours=1)).isoformat(),
                    "lastEventDate": "2026-08-07",
                    "events": [
                        {"code": "2330", "date": "2026-08-05"},
                        {"code": "2330", "date": "2026-08-07"},
                    ],
                }},
            }), encoding="utf-8")
            with patch.object(finmind_actions, "fetch", side_effect=AssertionError("must not fetch")):
                payload = finmind_actions.build_payload(
                    ["2330"], cache, 365, 12, 14, 1,
                    date(2026, 8, 6), now,
                )
            self.assertEqual([row["date"] for row in payload["events"]], ["2026-08-05"])
            self.assertEqual(payload["period"]["end"], "2026-08-06")

    def test_news_fresh_complete_cache_avoids_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "news.json"
            now = datetime(2026, 8, 6, 1, tzinfo=timezone.utc)
            cache.write_text(json.dumps({"schemaVersion": 1, "updatedAt": now.isoformat(), "items": [{"title": "verified"}], "errors": {}}), encoding="utf-8")
            with patch.object(market_news, "fetch_feed", side_effect=AssertionError("must not fetch")):
                payload = market_news.build_payload(cache, 6, now + timedelta(hours=1))
            self.assertEqual(payload["cache"]["status"], "hit")
            self.assertEqual(payload["items"][0]["title"], "verified")

    def test_news_expired_failed_refresh_never_reuses_stale_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "news.json"
            old = datetime(2026, 8, 5, 1, tzinfo=timezone.utc)
            cache.write_text(json.dumps({"schemaVersion": 1, "updatedAt": old.isoformat(), "items": [{"title": "stale"}], "errors": {}}), encoding="utf-8")
            with patch.object(market_news, "fetch_feed", return_value=([], "TimeoutError")):
                payload = market_news.build_payload(cache, 6, old + timedelta(hours=12))
            self.assertEqual(payload["items"], [])
            self.assertTrue(payload["errors"])
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["items"][0]["title"], "stale")

    def test_actions_fresh_cache_avoids_fetch_and_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "actions.json"
            now = datetime(2026, 8, 6, 1, tzinfo=timezone.utc)
            cache.write_text(json.dumps({"schemaVersion": 1, "entries": {"2330": {"queriedThrough": "2026-08-06", "verifiedAt": now.isoformat(), "lastEventDate": "2026-07-01", "events": [{"code": "2330", "date": "2026-07-01"}]}}}), encoding="utf-8")
            with patch.object(finmind_actions, "fetch", side_effect=AssertionError("must not fetch")):
                payload = finmind_actions.build_payload(["2330"], cache, 365, 12, 14, 1, date(2026, 8, 6), now + timedelta(hours=1))
            self.assertEqual(payload["successful_codes"], 1)
            self.assertEqual(payload["cache"]["hits"], 1)

    def test_actions_stale_failed_refresh_blocks_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "actions.json"
            old = datetime(2026, 8, 5, 1, tzinfo=timezone.utc)
            cache.write_text(json.dumps({"schemaVersion": 1, "entries": {"2330": {"queriedThrough": "2026-08-05", "verifiedAt": old.isoformat(), "lastEventDate": "2026-07-01", "events": [{"code": "2330", "date": "2026-07-01"}]}}}), encoding="utf-8")
            with patch.object(finmind_actions, "fetch", return_value=("2330", [], "TimeoutError")):
                payload = finmind_actions.build_payload(["2330"], cache, 365, 12, 14, 1, date(2026, 8, 6), old + timedelta(days=1))
            self.assertEqual(payload["successful_codes"], 0)
            self.assertEqual(payload["events"], [])
            self.assertEqual(payload["failures"], {"2330": "TimeoutError"})

    def test_candidate_key_changes_with_candidate_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "actions.json"
            now = datetime(2026, 8, 6, 1, tzinfo=timezone.utc)
            def fake_fetch(code, start, end):
                return code, [{"code": code, "date": "2026-07-01"}], None
            with patch.object(finmind_actions, "fetch", side_effect=fake_fetch):
                first = finmind_actions.build_payload(["2330"], cache, 365, 0, 14, 1, date(2026, 8, 6), now)
                second = finmind_actions.build_payload(["2317", "2330"], cache, 365, 0, 14, 1, date(2026, 8, 7), now + timedelta(days=1))
            self.assertNotEqual(first["cache"]["candidateKey"], second["cache"]["candidateKey"])


if __name__ == "__main__":
    unittest.main()
