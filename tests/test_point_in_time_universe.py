import unittest

from point_in_time_universe import certification, code, iso_date
from total_return_backtest import Series, run_period


class PointInTimeUniverseTests(unittest.TestCase):
    def test_official_chinese_fields_are_parsed(self):
        self.assertEqual(code({"上市編號": "2330"}), "2330")
        self.assertEqual(code({"公司代號": "2454"}), "2454")
        self.assertEqual(iso_date("115年06月23日"), "2026-06-23")

    def test_known_exit_is_required_but_does_not_make_certification_impossible(self):
        candidates = ["1111", "2222"]
        entries = {"1111": "2000-01-01", "2222": "2005-01-01"}
        self.assertEqual(certification(candidates, entries, {}, {"2222"}), (False, [], ["2222"]))
        self.assertEqual(certification(candidates, entries, {"2222": "2020-01-01"}, {"2222"}), (True, [], []))

    def test_no_price_outside_the_official_listing_interval_is_ever_used(self):
        """Listing bounds constrain prices, not the decision to enter.

        The stock lists on 01-04 and delists on 01-09.  Two signals fall inside
        the listed window, and both may trade: on the second one the engine
        cannot know the delisting is coming, so it enters and is forced out at
        the last price inside the interval.  Refusing that entry would be
        look-ahead -- it would let the backtest sidestep precisely the
        positions that end badly.
        """
        dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
        values = {day: float(index + 1) for index, day in enumerate(dates)}
        stock = Series("1111", values, 0, "2026-01-04", "2026-01-09")
        result = run_period({"1111": stock}, dates, lookback=2, holding=2, picks_count=1)
        accounting = result["executionAccounting"]
        self.assertEqual(accounting["selectedSlots"], 2)
        self.assertEqual(accounting["filledSlots"], 2)
        self.assertEqual(accounting["closedSlots"], 1)
        self.assertEqual(accounting["unresolvedExitSlots"], 1)
        self.assertFalse(result["executionComplete"])
        self.assertIsNone(result["totalReturn"])

    def test_signal_before_listing_is_still_refused(self):
        """Entry-side point-in-time integrity must stay intact."""
        dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
        values = {day: float(index + 1) for index, day in enumerate(dates)}
        stock = Series("1111", values, 0, "2026-01-08", None)
        result = run_period({"1111": stock}, dates, lookback=2, holding=2, picks_count=1)
        self.assertEqual(result["periods"], 0)
        self.assertGreater(result["scheduledPeriods"], 0)


if __name__ == "__main__":
    unittest.main()
