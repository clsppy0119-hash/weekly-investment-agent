import json
import os
import unittest

import lineage_shadow


class LineageShadowTests(unittest.TestCase):
    def make(self, **changes):
        value = lineage_shadow.build_record(
            provider="MOPS", dataset="filing", entity_id="2330", observation_period="2026Q1",
            source_revision="filing-1", available_at="2026-05-15T10:00:00+08:00",
            content={"metric": 1}, endpoint="https://example.test/file?token=secret", scope={"stock": "2330"},
        )
        value.update(changes)
        return value

    def test_metadata_is_private_and_does_not_expose_query_secret(self):
        row = self.make()
        self.assertNotIn("secret", json.dumps(row))
        self.assertEqual(row["visibility"], "private_lineage")
        artifact = lineage_shadow.artifact_summary([row], decision_as_of="2026-06-01T00:00:00+08:00", coverage=1.0)
        self.assertNotIn("endpoint", json.dumps(artifact))
        self.assertNotIn("scopeHash", json.dumps(artifact))

    def test_feature_flag_defaults_off_without_touching_advice(self):
        old = os.environ.pop(lineage_shadow.FEATURE_FLAG, None)
        try:
            self.assertFalse(lineage_shadow.enabled())
        finally:
            if old is not None:
                os.environ[lineage_shadow.FEATURE_FLAG] = old

    def test_unknown_available_or_partial_coverage_fails_closed(self):
        self.assertIn("lineage_availableAt_missing", lineage_shadow.validate(self.make(availableAt=None)))
        self.assertIn("lineage_coverage_incomplete", lineage_shadow.validate(self.make(), coverage=.99))

    def test_duplicate_is_idempotent_and_revision_is_append_only(self):
        first = self.make()
        duplicate = self.make()
        self.assertEqual(first["compositeKey"], duplicate["compositeKey"])
        self.assertEqual(first["contentHash"], duplicate["contentHash"])
        correction = lineage_shadow.build_record(
            provider="MOPS", dataset="filing", entity_id="2330", observation_period="2026Q1",
            source_revision="filing-2", available_at="2026-05-15T10:00:00+08:00",
            content={"metric": 2}, supersedes_content_hash=first["contentHash"],
        )
        self.assertNotEqual(first["compositeKey"], correction["compositeKey"])
        self.assertEqual(correction["supersedesContentHash"], first["contentHash"])

    def test_future_or_ambiguous_version_is_not_selected(self):
        future = self.make(availableAt="2026-07-01T00:00:00+08:00")
        self.assertEqual(lineage_shadow.select_for_shadow([future], decision_as_of="2026-06-01T00:00:00+08:00", coverage=1.0)["mode"], "research_only")
        first, other = self.make(), self.make(contentHash="other")
        self.assertEqual(lineage_shadow.select_for_shadow([first, other], decision_as_of="2026-06-01T00:00:00+08:00", coverage=1.0)["mode"], "research_only")

    def test_schema_marks_lineage_private_and_append_only(self):
        with open("supabase-persistence-schema.sql", encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn("investment_data_lineage_shadow", sql)
        self.assertIn("revoke all on public.investment_data_lineage_shadow from anon, authenticated", sql)
        self.assertNotIn("create policy \"authenticated can read lineage", sql)


if __name__ == "__main__":
    unittest.main()
