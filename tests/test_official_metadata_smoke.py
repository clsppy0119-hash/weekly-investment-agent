import copy
import json
import os
import unittest
from unittest import mock

import official_metadata_smoke as smoke


def swagger(paths):
    return {"openapi": "3.0.0", "paths": paths}


def operation(description="current membership list"):
    return {"get": {"description": description, "responses": {"200": {"description": "ok"}}}}


def dataset(dataset_id, title):
    return {
        "result": {
            "datasetId": dataset_id,
            "title": title,
            "license": "政府資料開放授權條款-第1版",
            "publisher": "官方提供機關",
            "modifiedDate": "2026-08-01 12:00:00",
        }
    }


class Client:
    def __init__(self, documents=None, failure=None):
        self.documents = documents or fixtures()
        self.failure = failure
        self.calls = []

    def get(self, url, *, max_bytes, timeout):
        self.calls.append((url, max_bytes, timeout))
        if self.failure and len(self.calls) == self.failure[0]:
            raise self.failure[1]
        body, content_type, status = self.documents[url]
        return smoke.Response(status, content_type, body)


def fixtures():
    twse = swagger({"/opendata/t187ap03_L": operation()})
    tpex = swagger({
        "/mopsfin_t187ap03_O": operation(),
        "/mopsfin_t187ap04_O": operation(),
    })
    values = {
        smoke.TWSE_SWAGGER: twse,
        smoke.DATASET_URLS["18419"]: dataset("18419", "上市公司基本資料"),
        smoke.DATASET_URLS["11543"]: dataset("11543", "終止上市公司"),
        smoke.DATASET_URLS["25036"]: dataset("25036", "上櫃公司基本資料"),
        smoke.TPEX_SWAGGER: tpex,
    }
    return {url: (json.dumps(value).encode(), "application/json; charset=utf-8", 200) for url, value in values.items()}


class OfficialMetadataSmokeTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ[smoke.FLAG] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def test_exact_five_gets_no_operation_invocation_and_research_only(self):
        client = Client()
        result = smoke.run(client)
        self.assertEqual(result["requestCount"], 5)
        self.assertEqual([call[0] for call in client.calls], [item[1] for item in smoke.REQUEST_PLAN])
        self.assertTrue(all(call[2] == 15 for call in client.calls))
        self.assertEqual(result["operationInvokeCount"], 0)
        self.assertEqual(result["persistenceWriteCount"], 0)
        self.assertEqual(result["mode"], "research_only")
        self.assertEqual(result["capabilityMatrix"]["counts"]["certifiable"], 0)
        by_source = {item["sourceKey"]: item for item in result["capabilityMatrix"]["records"]}
        self.assertEqual(by_source["twse_terminated"]["classification"], "UNSUPPORTED")

    def test_default_off_zero_calls(self):
        os.environ.pop(smoke.FLAG)
        client = Client()
        result = smoke.run(client)
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(result["requestCount"], 0)
        self.assertEqual(client.calls, [])

    def test_dataset_id_title_terms_and_attribution_are_pinned(self):
        for change in (
            {"datasetId": "wrong"}, {"title": "wrong"}, {"license": ""}, {"publisher": ""},
        ):
            docs = fixtures()
            value = json.loads(docs[smoke.DATASET_URLS["18419"]][0])
            value["result"].update(change)
            docs[smoke.DATASET_URLS["18419"]] = (json.dumps(value).encode(), "application/json", 200)
            result = smoke.run(Client(docs))
            record = result["capabilityMatrix"]["records"][0]
            self.assertNotEqual(record["classification"], "CERTIFIABLE")

    def test_never_invokes_membership_url_or_uses_auth_query_redirect(self):
        for _name, url, _maximum in smoke.REQUEST_PLAN:
            self.assertNotIn("?", url)
            self.assertTrue(url.startswith("https://"))
            self.assertNotIn("t187ap03_L", url)
            self.assertNotIn("mopsfin_t187ap", url)
        self.assertNotIn("Authorization", smoke.UrllibClient.get.__code__.co_names)
        self.assertIsNone(smoke._NoRedirect().redirect_request(None, None, 302, "", {}, "https://x"))

    def test_real_client_explicitly_disables_environment_proxies(self):
        with mock.patch.object(smoke.urllib.request, "build_opener") as build_opener:
            smoke.UrllibClient()
        handlers = build_opener.call_args.args
        proxy_handler = next(
            handler for handler in handlers if isinstance(handler, smoke.urllib.request.ProxyHandler)
        )
        self.assertEqual(proxy_handler.proxies, {})

    def test_size_status_content_type_malformed_and_timeout_fail_closed(self):
        cases = []
        for body, content_type, status in (
            (b"{}", "text/html", 200), (b"not-json", "application/json", 200), (b"{}", "application/json", 500),
        ):
            docs = fixtures()
            docs[smoke.TWSE_SWAGGER] = (body, content_type, status)
            cases.append(Client(docs))
        docs = fixtures()
        docs[smoke.DATASET_URLS["18419"]] = (b"x" * (smoke.MAX_DATASET_BYTES + 1), "application/json", 200)
        cases.append(Client(docs))
        cases.append(Client(failure=(1, TimeoutError("private details"))))
        for client in cases:
            result = smoke.run(client)
            self.assertEqual(result["mode"], "research_only")
            self.assertTrue(result["blockers"][0].startswith("metadata_fetch_failed:"))
            self.assertNotIn("private details", json.dumps(result))

    def test_schema_missing_duplicate_or_unsafe_operation_fails_closed(self):
        mutations = [
            {},
            {"/opendata/t187ap03_L": {"post": {}}},
            {"/opendata/t187ap03_L": {"get": {}, "post": {}}},
        ]
        for paths in mutations:
            docs = fixtures()
            docs[smoke.TWSE_SWAGGER] = (json.dumps(swagger(paths)).encode(), "application/json", 200)
            result = smoke.run(Client(docs))
            self.assertIn(result["capabilityMatrix"]["records"][0]["classification"], {"UNSUPPORTED", "CONFLICT"})

    def test_catalog_dates_current_and_inferred_semantics_never_certify(self):
        docs = fixtures()
        value = json.loads(docs[smoke.DATASET_URLS["18419"]][0])
        value["result"].update({
            "resourceModifiedDate": "2026-08-01 12:00:00",
            "resourceQualityCheckTime": "2026-08-01 12:00:00",
            "effectiveDate": "2026-08-01",
            "nextTradingDay": "2026-08-02",
        })
        docs[smoke.DATASET_URLS["18419"]] = (json.dumps(value).encode(), "application/json", 200)
        result = smoke.run(Client(docs))
        self.assertNotEqual(result["capabilityMatrix"]["records"][0]["classification"], "CERTIFIABLE")

    def test_explicit_documented_semantics_can_classify_but_no_time_leaks(self):
        description = (
            "official publishedAt date-time UTC timezone immutable revisionId "
            "supersedes revision semantics complete history all revisions"
        )
        docs = fixtures()
        twse = swagger({"/opendata/t187ap03_L": operation(description)})
        docs[smoke.TWSE_SWAGGER] = (json.dumps(twse).encode(), "application/json", 200)
        result = smoke.run(Client(docs))
        rendered = json.dumps(result)
        # Output allowlist forbids all actual timestamp field names; a violation
        # must fail closed rather than expose it.
        self.assertEqual(result["mode"], "research_only")
        self.assertNotIn("2026-08-01", rendered)
        self.assertNotIn("https://", rendered)

    def test_deterministic_in_memory_and_formal_modules_unchanged(self):
        first = smoke.run(Client())
        second = smoke.run(Client(copy.deepcopy(fixtures())))
        self.assertEqual(first["smokeDigest"], second["smokeDigest"])
        self.assertEqual(first["capabilityMatrix"]["capabilityDigest"], second["capabilityMatrix"]["capabilityDigest"])


if __name__ == "__main__":
    unittest.main()
