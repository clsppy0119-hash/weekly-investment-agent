import json
import tempfile
import unittest
from pathlib import Path

from market_membership_snapshots import evaluate_coverage, load_membership, ordinary_stock, signal_dates
from total_return_backtest import Series, run_period


class MarketMembershipSnapshotTests(unittest.TestCase):
    def test_only_four_digit_stock_codes_are_admitted(self):
        self.assertEqual(ordinary_stock("2330"), "2330")
        self.assertEqual(ordinary_stock("0050"), "")
        self.assertEqual(ordinary_stock("00679B"), "")

    def test_signal_dates_match_split_local_rebalances(self):
        calendar = [f"d{index:04d}" for index in range(300)]
        self.assertEqual(signal_dates(calendar, lookback=20, holding=10), [
            "d0020", "d0030", "d0040", "d0050", "d0060", "d0070", "d0080", "d0090",
            "d0100", "d0110", "d0120", "d0130", "d0140", "d0150", "d0160", "d0200",
            "d0210", "d0220", "d0260", "d0270", "d0280",
        ])

    def test_coverage_requires_every_snapshot_and_cached_stock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "2026-01-02.json").write_text(json.dumps({"date": "2026-01-02", "twse": ["2330"], "tpex": ["6488"]}), encoding="utf-8")
            incomplete = evaluate_coverage(["2026-01-02"], root, {"2330"})
            complete = evaluate_coverage(["2026-01-02"], root, {"2330", "6488"})
            self.assertFalse(incomplete["certified"])
            self.assertEqual(incomplete["missingCachedStocks"], 1)
            self.assertTrue(complete["certified"])
            self.assertEqual(load_membership(root)["2026-01-02"], {"2330", "6488"})

    def test_backtest_never_selects_stock_absent_from_snapshot(self):
        dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
        values = {day: float(index + 1) for index, day in enumerate(dates)}
        series = {"2330": Series("2330", values, 0)}
        result = run_period(series, dates, lookback=2, holding=2, picks_count=1, membership_by_date={})
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
