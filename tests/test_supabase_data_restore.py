import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import supabase_data_restore


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class SupabaseRestoreTests(unittest.TestCase):
    @patch("supabase_data_restore.time.sleep")
    @patch("supabase_data_restore.urllib.request.urlopen")
    def test_retryable_server_error_is_retried(self, urlopen, sleep):
        error = HTTPError("https://example.test", 500, "server error", {}, None)
        urlopen.side_effect = [error, error, _Response(json.dumps([{"stock_id": "2330"}]).encode())]

        rows = supabase_data_restore.get_rows("https://example.test", "secret", "investment_market_daily", 0)

        self.assertEqual(rows, [{"stock_id": "2330"}])
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    @patch("supabase_data_restore.time.sleep")
    @patch("supabase_data_restore.urllib.request.urlopen")
    def test_non_retryable_auth_error_fails_immediately(self, urlopen, sleep):
        urlopen.side_effect = HTTPError("https://example.test", 401, "unauthorized", {}, None)

        with self.assertRaises(HTTPError):
            supabase_data_restore.get_rows("https://example.test", "bad", "investment_market_daily", 0)

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
