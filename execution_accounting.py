"""Deterministic portfolio execution accounting for research backtests.

The signal engine supplies an ordered list of intended outcomes.  This module
never selects a security and never fetches data.  It only enforces the
pre-registered portfolio semantics:

* every target slot keeps its original equal weight;
* missing candidates or unfilled entries remain cash;
* a filled position without a complete, executable exit and valuation path
  makes the period uncertified instead of being silently dropped; and
* costs are charged only to slots that complete a round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Literal


SCHEMA_VERSION = 1
POLICY_VERSION = "fixed-equal-weight-slots-v1"
RISK_POLICY_VERSION = "daily-mdd-unconfigured-v1"


@dataclass(frozen=True)
class SchedulePoint:
    signal_index: int
    entry_index: int
    exit_index: int


def rebalance_schedule(
    length: int,
    lookback: int,
    holding: int,
    *,
    convention: Literal["signal_plus_holding", "entry_plus_holding"],
) -> list[SchedulePoint]:
    """Return a deterministic schedule without changing either legacy strategy.

    The two existing engines historically used different holding conventions.
    Making that policy explicit lets them share a tested helper without silently
    changing signals or exits during this accounting-only correction.
    """
    if length < 0 or lookback < 1 or holding < 1:
        raise ValueError("invalid_rebalance_schedule")
    result: list[SchedulePoint] = []
    exit_offset = holding if convention == "signal_plus_holding" else holding + 1
    signal = lookback
    while signal + exit_offset < length:
        result.append(SchedulePoint(signal, signal + 1, signal + exit_offset))
        signal += holding
    return result


def unconfigured_risk_policy() -> dict[str, Any]:
    """Return the immutable fail-closed risk policy for this measurement node."""
    return {
        "riskPolicyVersion": RISK_POLICY_VERSION,
        "dailyMddLimitConfigured": False,
        "dailyMddGatePassed": False,
        "riskGateEligible": False,
        "riskBlockers": ["daily_mdd_limit_unconfigured"],
    }


def round_trip_net(gross_return: float, *, buy_cost: float, sell_cost: float) -> float:
    if not all(math.isfinite(value) for value in (gross_return, buy_cost, sell_cost)):
        raise ValueError("non_finite_execution_value")
    if gross_return <= -1 or min(buy_cost, sell_cost) < 0 or max(buy_cost, sell_cost) >= 1:
        raise ValueError("invalid_execution_value")
    return (1 + gross_return) * (1 - buy_cost) * (1 - sell_cost) - 1


def settle_equal_weight_period(
    outcomes: Iterable[dict[str, Any]],
    target_slots: int,
    *,
    buy_cost: float,
    sell_cost: float,
) -> dict[str, Any]:
    """Settle a period without reallocating cash or unresolved slots.

    A ``closed`` outcome must include both ``grossReturn`` and a complete
    entry-to-exit ``dailyGrossFactors`` sequence.  Cash statuses need neither.
    An ``unresolved_exit`` outcome deliberately has no return: partial returns
    must never leak into a promotion comparison.
    """
    if target_slots < 1:
        raise ValueError("target_slots_must_be_positive")
    items = [dict(item) for item in outcomes]
    if len(items) > target_slots:
        raise ValueError("outcomes_exceed_target_slots")
    items.extend({"status": "cash_no_candidate"} for _ in range(target_slots - len(items)))

    allowed = {"closed", "cash_unfilled", "cash_no_candidate", "unresolved_exit"}
    counts = {status: 0 for status in allowed}
    slot_paths: list[list[float]] = []
    path_length: int | None = None
    blockers: set[str] = set()
    net_returns: list[float] = []

    for item in items:
        status = item.get("status")
        if status not in allowed:
            raise ValueError("unknown_execution_status")
        counts[status] += 1
        if status == "closed":
            gross = item.get("grossReturn")
            factors = item.get("dailyGrossFactors")
            if not isinstance(gross, (int, float)) or not math.isfinite(float(gross)):
                raise ValueError("closed_return_invalid")
            if not isinstance(factors, list) or len(factors) < 2:
                raise ValueError("daily_path_missing")
            numeric = [float(value) for value in factors]
            if any(not math.isfinite(value) or value <= 0 for value in numeric):
                raise ValueError("daily_path_invalid")
            if path_length is None:
                path_length = len(numeric)
            if len(numeric) != path_length:
                raise ValueError("daily_path_length_mismatch")
            if not math.isclose(numeric[0], 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("daily_path_entry_not_one")
            if not math.isclose(numeric[-1] - 1, float(gross), rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("daily_path_exit_mismatch")
            path = [(1 - buy_cost) * value for value in numeric]
            path[-1] *= 1 - sell_cost
            slot_paths.append(path)
            net_returns.append(round_trip_net(float(gross), buy_cost=buy_cost, sell_cost=sell_cost))
        elif status in {"cash_unfilled", "cash_no_candidate"}:
            slot_paths.append([])
            net_returns.append(0.0)
        else:
            blockers.add(str(item.get("reason") or "unresolved_exit"))

    complete = counts["unresolved_exit"] == 0
    if not complete:
        blockers.add("execution_accounting_incomplete")
    if path_length is None:
        path_length = int(next((item.get("pathLength") for item in items if item.get("pathLength")), 2))
    if path_length < 2:
        raise ValueError("daily_path_length_invalid")
    for index, path in enumerate(slot_paths):
        if not path:
            slot_paths[index] = [1.0] * path_length
        elif len(path) != path_length:
            raise ValueError("daily_path_length_mismatch")

    equity_factors = None
    period_return = None
    if complete:
        equity_factors = [sum(path[pos] for path in slot_paths) / target_slots for pos in range(path_length)]
        period_return = equity_factors[-1] - 1
        expected = sum(net_returns) / target_slots
        if not math.isclose(period_return, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("execution_accounting_mismatch")

    selected = target_slots - counts["cash_no_candidate"]
    filled = counts["closed"] + counts["unresolved_exit"]
    cash_slots = counts["cash_unfilled"] + counts["cash_no_candidate"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "complete": complete,
        "return": period_return,
        "equityFactors": equity_factors,
        "targetSlots": target_slots,
        "selectedSlots": selected,
        "filledSlots": filled,
        "closedSlots": counts["closed"],
        "unfilledEntrySlots": counts["cash_unfilled"],
        "noCandidateCashSlots": counts["cash_no_candidate"],
        "unresolvedExitSlots": counts["unresolved_exit"],
        "costedRoundTrips": counts["closed"],
        "cashWeight": cash_slots / target_slots,
        "blockers": sorted(blockers),
    }


def aggregate_periods(periods: list[dict[str, Any]], *, comparison_from: Any, comparison_to: Any) -> dict[str, Any]:
    blockers = sorted({blocker for period in periods for blocker in period.get("blockers", [])})
    complete = bool(periods) and all(period.get("complete") for period in periods)
    if not periods:
        blockers = sorted(set(blockers) | {"no_scheduled_periods"})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "complete": complete,
        "scheduledPeriods": len(periods),
        "investedPeriods": sum(1 for period in periods if period.get("closedSlots", 0) > 0),
        "targetSlotsTotal": sum(period.get("targetSlots", 0) for period in periods),
        "selectedSlots": sum(period.get("selectedSlots", 0) for period in periods),
        "filledSlots": sum(period.get("filledSlots", 0) for period in periods),
        "closedSlots": sum(period.get("closedSlots", 0) for period in periods),
        "unfilledEntrySlots": sum(period.get("unfilledEntrySlots", 0) for period in periods),
        "noCandidateCashSlots": sum(period.get("noCandidateCashSlots", 0) for period in periods),
        "unresolvedExitSlots": sum(period.get("unresolvedExitSlots", 0) for period in periods),
        "tieBreakDependentSlots": sum(period.get("tieBreakDependentSlots", 0) for period in periods),
        "costedRoundTrips": sum(period.get("costedRoundTrips", 0) for period in periods),
        "averageCashWeight": (
            sum(period.get("cashWeight", 0.0) for period in periods) / len(periods) if periods else 1.0
        ),
        "comparisonFrom": comparison_from,
        "comparisonTo": comparison_to,
        "blockers": blockers,
    }


def daily_equity_curve(periods: list[dict[str, Any]]) -> list[float] | None:
    """Compound certified per-period daily factors, retaining boundary costs."""
    if not periods or any(not period.get("complete") or period.get("equityFactors") is None for period in periods):
        return None
    curve = [1.0]
    equity = 1.0
    for period in periods:
        factors = period["equityFactors"]
        for factor in factors:
            curve.append(equity * factor)
        equity *= 1 + period["return"]
    return curve


def max_drawdown_from_equity(equity: list[float] | None) -> float | None:
    if not equity:
        return None
    peak = equity[0]
    drawdown = 0.0
    for value in equity:
        if not math.isfinite(value) or value <= 0:
            return None
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return drawdown


def max_drawdown_from_period_returns(returns: list[float] | None) -> float | None:
    """Legacy rebalance-endpoint MDD retained only for train parameter scoring."""
    if returns is None:
        return None
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        if not math.isfinite(value) or value <= -1:
            return None
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    return drawdown
