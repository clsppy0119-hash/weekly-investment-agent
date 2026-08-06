import os
import unittest
import urllib.error

import lineage_shadow
import lineage_writer


class Response:
    status = 201
    def __enter__(self): return self
    def __exit__(self, *args): return False


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ["LINEAGE_SHADOW_ENABLED"] = "true"
        os.environ["SUPABASE_URL"] = "https://project.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "service-secret"
        self.row = lineage_shadow.build_record(provider="MOPS", dataset="filing", entity_id="2330", observation_period="2026Q1", source_revision="v1", available_at="2026-05-15T10:00:00+08:00", content={"x": 1})
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
    def test_default_off_does_not_need_credentials_or_network(self):
        os.environ.pop("LINEAGE_SHADOW_ENABLED")
        os.environ.pop("SUPABASE_URL"); os.environ.pop("SUPABASE_SERVICE_ROLE_KEY")
        self.assertEqual(lineage_writer.write([self.row]), {"status": "disabled", "written": 0, "duplicates": 0})
    def test_post_is_allowlisted_and_secret_free(self):
        seen = []
        def fake(request, timeout):
            seen.append(request); return Response()
        result = lineage_writer.write([self.row], request=fake)
        self.assertEqual(result["written"], 1)
        body = seen[0].data.decode()
        self.assertNotIn("service-secret", body)
        self.assertNotIn("endpoint", body)
        self.assertEqual(seen[0].get_method(), "POST")
        self.assertNotIn("merge-duplicates", seen[0].headers.get("Prefer", ""))
    def test_only_exact_duplicate_409_is_noop(self):
        def exact(request, timeout):
            text = self.row["compositeKey"] + self.row["contentHash"]
            raise urllib.error.HTTPError(request.full_url, 409, "Conflict", None, Body(text))
        self.assertEqual(lineage_writer.write([self.row], request=exact)["duplicates"], 1)
        def unsafe(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 409, "Conflict", None, Body("other"))
        with self.assertRaises(RuntimeError): lineage_writer.write([self.row], request=unsafe)

class Body:
    def __init__(self, text): self.text = text
    def read(self): return self.text.encode()
    def close(self): pass

if __name__ == "__main__": unittest.main()
