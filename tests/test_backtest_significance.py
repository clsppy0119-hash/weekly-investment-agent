"""A cumulative return is not evidence; the per-rebalance interval is.

Compounding turns a handful of lucky rebalances into a headline number, so the
harness reports the mean excess per rebalance with a bootstrap interval and
refuses to call a result conclusive while that interval contains zero.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_backtest import benchmark_between, significance


def test_a_noisy_edge_is_not_called_conclusive():
    # Mean is positive but the spread dwarfs it.
    noisy = [0.30, -0.28, 0.25, -0.22, 0.20, -0.18, 0.02] * 3
    result = significance(noisy)

    assert result["meanExcessPerRebalance"] > 0
    assert result["ci95"][0] < 0 < result["ci95"][1]
    assert result["conclusive"] is False, "an interval spanning zero decides nothing"


def test_a_consistent_edge_is_called_conclusive():
    steady = [0.02, 0.025, 0.018, 0.022, 0.021, 0.019, 0.023, 0.020] * 3
    result = significance(steady)

    assert result["ci95"][0] > 0
    assert result["conclusive"] is True
    assert result["tStat"] > 3


def test_a_consistent_loss_is_conclusive_too():
    losing = [-0.02, -0.025, -0.018, -0.022, -0.021, -0.019] * 3
    result = significance(losing)

    assert result["ci95"][1] < 0
    assert result["conclusive"] is True, "a reliably negative edge is also a finding"


def test_too_few_rebalances_never_conclude():
    result = significance([0.5, 0.6])

    assert result["conclusive"] is False
    assert result["reason"] == "fewer_than_three_rebalances"


def test_bootstrap_is_reproducible():
    sample = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.015]

    assert significance(sample)["ci95"] == significance(sample)["ci95"]


def test_benchmark_period_return_is_net_of_etf_costs():
    series = {"2026-01-02": 100.0, "2026-01-09": 110.0}
    net = benchmark_between(series, "2026-01-02", "2026-01-09")

    assert net is not None
    assert net < 0.10, "a 10% gross move must not survive costs intact"
    assert net > 0.09


def test_missing_benchmark_dates_yield_none():
    series = {"2026-01-02": 100.0}

    assert benchmark_between(series, "2026-01-02", "2026-01-09") is None
    assert benchmark_between(series, "2025-12-31", "2026-01-02") is None


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"PASS  {name}")
