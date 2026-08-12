"""A stock that stops trading during the holding period must book its loss.

The previous candidate filter required an exit price to exist before a stock
could be ranked, so any position that was delisted or halted mid-hold quietly
left the pool and the backtest never paid for it.  These tests pin the fixed
behaviour: selection uses signal-day information only, while an unverifiable
exit fails closed instead of being replaced with a stale quote.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import run_slice


def _history_with_delisting():
    """Highest-momentum name crashes, then stops trading before the exit day.

    index 1 is the signal day, 2 the entry day, 4 the exit day.  ``CRASH`` has
    the strongest momentum at the signal, so a correct engine must pick it and
    take the loss; ``SAFE`` is the name a leaking engine would fall back to.
    """
    volume = 1_000_000.0
    return [
        {"1111": (100.0, volume), "2222": (100.0, volume)},   # 0  lookback base
        {"1111": (200.0, volume), "2222": (110.0, volume)},   # 1  signal
        {"1111": (200.0, volume), "2222": (110.0, volume)},   # 2  entry
        {"1111": (50.0, volume), "2222": (115.0, volume)},    # 3  CRASH collapses
        {"2222": (121.0, volume)},                            # 4  exit; CRASH gone
    ]


def test_delisted_pick_is_selected_but_cannot_use_a_stale_sale():
    result = run_slice(_history_with_delisting(), lookback=1, count=1, holding=2)

    accounting = result["executionAccounting"]
    assert accounting["selectedSlots"] == 1
    assert accounting["filledSlots"] == 1, "the signal-day pick must not be replaced with SAFE"
    assert accounting["unresolvedExitSlots"] == 1
    assert result["stale_exits"] == 1
    assert result["executionComplete"] is False
    assert result["return"] is None, "a stale last quote is not an executable delisting value"


def test_missing_entry_price_leaves_the_slot_in_cash():
    """No fill on the entry day is not a trade, and must not be a free win."""
    volume = 1_000_000.0
    history = [
        {"1111": (100.0, volume), "2222": (100.0, volume)},
        {"1111": (200.0, volume), "2222": (110.0, volume)},   # signal: 1111 leads
        {"2222": (110.0, volume)},                            # entry: 1111 untradable
        {"1111": (300.0, volume), "2222": (115.0, volume)},
        {"1111": (400.0, volume), "2222": (121.0, volume)},   # exit
    ]
    result = run_slice(history, lookback=1, count=1, holding=2)

    assert result["unfilled"] == 1, "an unfillable pick must be counted"
    assert result["trades"] == 0, "no fill means no rebalance return"
    assert result["return"] == 0.0
    assert result["executionAccounting"]["scheduledPeriods"] == 1


def test_surviving_pick_is_unchanged():
    """The fix must not alter the ordinary path where every price exists."""
    volume = 1_000_000.0
    history = [
        {"1111": (100.0, volume)},
        {"1111": (200.0, volume)},
        {"1111": (200.0, volume)},
        {"1111": (210.0, volume)},
        {"1111": (240.0, volume)},
    ]
    result = run_slice(history, lookback=1, count=1, holding=2)

    assert result["trades"] == 1
    assert result["executionComplete"] is True
    assert result["stale_exits"] == 0 and result["unfilled"] == 0
    assert 0.18 < result["return"] < 0.20, f"240/200-1 net of costs, got {result['return']:+.4f}"


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
