import json
import tempfile
import unittest
from pathlib import Path

import backtest_data_cache


class BacktestCacheTests(unittest.TestCase):
    def test_official_snapshot_gap_has_first_priority(self):
        pending = ["1101", "2330", "2454", "3008"]
        ordered = backtest_data_cache.prioritize_pending(pending, {"2454", "3008"}, {"1101", "3008"})
        self.assertEqual(ordered, ["3008", "2454", "1101", "2330"])

    def test_restored_stock_files_are_treated_as_reviewed(self):
        with tempfile.TemporaryDirectory() as folder:
            stock_dir = Path(folder)
            (stock_dir / "2330.json").write_text("{}", encoding="utf-8")
            (stock_dir / "note.txt").write_text("ignore", encoding="utf-8")
            self.assertEqual(backtest_data_cache.existing_cached_codes(stock_dir), {"2330"})

    def test_all_market_universe_has_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            all_market = root / "historical-universe-v1" / "all-market.json"
            all_market.parent.mkdir(parents=True)
            all_market.write_text(json.dumps([{"stock_id": "1101"}, {"stock_id": "2330"}], ensure_ascii=False), encoding="utf-8")
            semiconductor = root / "historical-universe-v1" / "semiconductor.json"
            semiconductor.write_text(json.dumps([{"stock_id": "2330"}], ensure_ascii=False), encoding="utf-8")
            self.assertEqual(backtest_data_cache.eligible_codes(root), ["1101", "2330"])

    def test_universe_loader_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "historical-universe-v1" / "all-market.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([{"stock_id": "1101"}]), encoding="utf-8-sig")
            self.assertEqual(backtest_data_cache.eligible_codes(root), ["1101"])

    def test_delisted_codes_are_added_to_all_market_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            universe = root / "historical-universe-v1" / "all-market.json"
            universe.parent.mkdir(parents=True)
            universe.write_text(json.dumps([{"stock_id": "1101"}]), encoding="utf-8")
            listing = root / "official-listing-history-v1" / "finmind_delisted.json"
            listing.parent.mkdir(parents=True)
            listing.write_text(json.dumps([{"stock_id": "1230", "date": "2001-11-01"}]), encoding="utf-8")
            self.assertEqual(backtest_data_cache.eligible_codes(root), ["1101", "1230"])


if __name__ == "__main__":
    unittest.main()
