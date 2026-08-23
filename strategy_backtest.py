"""Backtest the ranking rule the product actually ships.

``backtest.py`` validates a plain price-momentum rule.  The daily report ranks
candidates with the weighted multi-factor score in ``scoring``.  Those are
different strategies, so a passing momentum backtest said nothing about the
recommendations users see.  This runs the real rule.

Factors are rebuilt from official TWSE closes at each signal date, so a style
whose weights are mostly technical (``swing``) can be measured today.  Styles
that lean on financials (``comprehensive``, ``value``, ``dividend``) need a
point-in-time fundamentals series; without one their coverage stays below the
production threshold and the run reports zero candidates rather than quietly
scoring on a thinner basis than production would.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from math import isclose, isfinite, sqrt
from pathlib import Path
from random import Random
from statistics import mean, stdev

from actual_comprehensive_selection import POLICY_VERSION as SELECTION_POLICY_VERSION
from actual_comprehensive_selection import rank_and_assess
from actual_comprehensive_outcome_accounting import (
    POLICY_VERSION as OUTCOME_ACCOUNTING_POLICY_VERSION,
    aggregate_measurements,
    measure_cohort,
)
from backtest import (
    BUY_FEE, DEFAULT_BENCHMARK, DEFAULT_DATA, ETF_SELL_TAX, SELL_FEE,
    SLIPPAGE_BPS, STOCK_SELL_TAX, load_history,
)
from point_in_time_fundamentals import PointInTimeFundamentals
from scoring import MINIMUM_COVERAGE, WEIGHTS, candidates, number, ranking_volume

MA_LONG = 20
SELECTION_EVIDENCE_SCHEMA_VERSION = 1
BENCHMARK_COST_MODEL_VERSION = "official-0050-total-return-single-split-round-trip-v1"
BENCHMARK_COMPARATOR_VERSION = "full-scheduled-calendar-0050-total-return-comparator-v2"
MAX_BENCHMARK_BYTES = 32 * 1024 * 1024
MAX_NUMERIC_ABS = 10**18


def _bounded_number(value: object) -> float | None:
    """Convert one exact builtin number without rounding a value past the cap."""
    if type(value) is int:
        return float(value) if abs(value) <= MAX_NUMERIC_ABS else None
    if type(value) is float and isfinite(value) and abs(value) <= MAX_NUMERIC_ABS:
        return value
    return None


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def build_selection_evidence(
    signal_date: str,
    decision_as_of: str,
    actions: dict,
    contract_blockers: list[str],
) -> dict:
    """Build a hash-bound PIT *shape*; this does not authenticate its source."""
    body = {
        "schemaVersion": SELECTION_EVIDENCE_SCHEMA_VERSION,
        "signalDate": signal_date,
        "decisionAsOf": decision_as_of,
        "quality": "verified",
        "conflictStatus": "no_conflict",
        "actions": actions,
        "contractBlockers": contract_blockers,
    }
    return {**body, "evidenceHash": _canonical_hash(body)}


def factor_quotes(history: list[dict], index: int, min_volume: float) -> dict[str, dict]:
    """Rebuild price, ma5, ma20 and change as of ``index``, using no later day."""
    today = history[index]
    quotes: dict[str, dict] = {}
    for code, (price, volume) in today.items():
        if not (number(price) and price > 0):
            continue
        normalized_volume = ranking_volume({"volume": volume})
        if min_volume > 0 and normalized_volume < min_volume:
            continue
        closes = [history[index - back].get(code) for back in range(MA_LONG)]
        closes = [
            item[0]
            for item in closes
            if item and number(item[0]) and item[0] > 0
        ]
        if len(closes) < MA_LONG:
            continue
        previous = history[index - 1].get(code)
        previous_close = (
            previous[0]
            if previous and number(previous[0]) and previous[0] > 0
            else None
        )
        change = (price / previous_close - 1) * 100 if previous_close else None
        quotes[code] = {
            "price": price,
            "volume": normalized_volume,
            "ma5": mean(closes[:5]),
            "ma20": mean(closes),
            "change": change if number(change) else None,
        }
    return quotes


def _benchmark_value(value: object) -> float | None:
    result = _bounded_number(value)
    if result is None:
        return None
    return result if isfinite(result) and 0 < result <= MAX_NUMERIC_ABS else None


def _benchmark_date(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate_or_invalid_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_standard_json_constant:{value}")


def _settle_benchmark_paths(paths: list[list[float]]) -> dict:
    """Allocate one buy and one sell boundary across adjacent intervals."""
    if type(paths) is not list or not paths:
        raise ValueError("benchmark_paths_missing")
    buy_factor = 1 - BUY_FEE - SLIPPAGE_BPS / 10_000
    sell_factor = 1 - SELL_FEE - ETF_SELL_TAX - SLIPPAGE_BPS / 10_000
    if not (0 < buy_factor <= 1 and 0 < sell_factor <= 1):
        raise ValueError("benchmark_cost_model_invalid")

    scheduled_returns: list[float] = []
    gross_factor = 1.0
    for index, path in enumerate(paths):
        if type(path) is not list or len(path) < 2:
            raise ValueError("benchmark_path_missing")
        values: list[float] = []
        for raw in path:
            value = _benchmark_value(raw)
            if value is None:
                raise ValueError("benchmark_path_value_invalid")
            values.append(value)
        if not isfinite(values[0]) or abs(values[0] - 1.0) > 1e-12:
            raise ValueError("benchmark_path_entry_not_one")
        interval_factor = values[-1]
        gross_factor *= interval_factor
        scheduled_factor = interval_factor
        if index == 0:
            scheduled_factor *= buy_factor
        if index == len(paths) - 1:
            scheduled_factor *= sell_factor
        if not isfinite(gross_factor) or not (0 < gross_factor <= MAX_NUMERIC_ABS) \
                or not isfinite(scheduled_factor) or not (0 < scheduled_factor <= MAX_NUMERIC_ABS):
            raise ValueError("benchmark_compound_invalid")
        scheduled_return = scheduled_factor - 1
        if scheduled_return <= -1 or not isclose(
            1 + scheduled_return, scheduled_factor, rel_tol=1e-12, abs_tol=0.0
        ):
            raise ValueError("benchmark_return_out_of_domain")
        scheduled_returns.append(scheduled_return)

    total_factor = gross_factor * buy_factor * sell_factor
    scheduled_factor = 1.0
    for value in scheduled_returns:
        scheduled_factor *= 1 + value
    total_return = total_factor - 1
    if not isfinite(total_factor) or not (0 < total_factor <= MAX_NUMERIC_ABS) or total_return <= -1 \
            or not isclose(1 + total_return, total_factor, rel_tol=1e-12, abs_tol=0.0) \
            or not isfinite(scheduled_factor) or not (0 < scheduled_factor <= MAX_NUMERIC_ABS) \
            or not isclose(total_factor, scheduled_factor, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError("benchmark_schedule_mismatch")
    return {
        "complete": True,
        "return": total_return,
        "scheduledReturns": scheduled_returns,
        "costedRoundTrips": 1,
        "costModel": BENCHMARK_COST_MODEL_VERSION,
        "comparatorVersion": BENCHMARK_COMPARATOR_VERSION,
        "blockers": [],
    }


def benchmark_series(path: Path) -> dict[str, float]:
    try:
        if path.stat().st_size > MAX_BENCHMARK_BYTES:
            return {}
        raw = path.read_bytes()
        if len(raw) > MAX_BENCHMARK_BYTES:
            return {}
        rows = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, OverflowError, RecursionError):
        return {}
    if type(rows) is not list:
        return {}
    result: dict[str, float] = {}
    for row in rows:
        if type(row) is not dict:
            return {}
        day = _benchmark_date(row.get("date"))
        value = _benchmark_value(row.get("total_return"))
        if day is None or value is None or day in result:
            return {}
        result[day] = value
    return result


def load_selection_evidence(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _dated_evidence_at(source: object, signal_date: str) -> dict | None:
    """Resolve a separate settlement snapshot; never reuse undated evidence."""
    if callable(source):
        value = source(signal_date)
    elif isinstance(source, dict) and isinstance(source.get("byDate"), dict):
        value = source["byDate"].get(signal_date)
    else:
        value = None
    return value if isinstance(value, dict) else None


def benchmark_between(series: dict[str, float], entry: str, exit_: str) -> float | None:
    """Net 0050 return for one standalone exact-boundary holding."""
    if _benchmark_date(entry) is None or _benchmark_date(exit_) is None or entry >= exit_:
        return None
    start = _benchmark_value(series.get(entry)) if type(series) is dict else None
    end = _benchmark_value(series.get(exit_)) if type(series) is dict else None
    if start is None or end is None:
        return None
    factor = end / start
    if not isfinite(factor) or factor <= 0:
        return None
    try:
        result = _settle_benchmark_paths([[1.0, factor]])
    except ValueError:
        return None
    return result["return"]


def benchmark_schedule(
    series: dict[str, float],
    comparison_dates: list[str],
    rebalances: list[dict],
) -> dict:
    """Build an all-or-nothing, single-round-trip benchmark schedule.

    Strategy rebalances divide one continuously held 0050 position into
    reporting intervals.  The intervals must cover the complete comparison
    calendar without overlap or gaps.  Costs are applied only at the outer
    boundaries, and every scheduled period—including strategy cash periods—
    receives one benchmark return for significance accounting.
    """
    def failed(blocker: str) -> dict:
        return {
            "complete": False,
            "return": None,
            "scheduledReturns": [],
            "costedRoundTrips": 0,
            "costModel": BENCHMARK_COST_MODEL_VERSION,
            "comparatorVersion": BENCHMARK_COMPARATOR_VERSION,
            "comparisonFrom": None,
            "comparisonTo": None,
            "comparisonTradingDays": 0,
            "blockers": [blocker],
        }

    if type(series) is not dict or type(comparison_dates) is not list \
            or type(rebalances) is not list or not rebalances:
        return failed("benchmark_schedule_missing")
    parsed_dates = [_benchmark_date(day) for day in comparison_dates]
    if len(comparison_dates) < 2 or any(day is None for day in parsed_dates) \
            or comparison_dates != sorted(set(comparison_dates)):
        return failed("benchmark_calendar_invalid")
    positions = {day: index for index, day in enumerate(comparison_dates)}
    values: list[float] = []
    for day in comparison_dates:
        value = _benchmark_value(series.get(day))
        if value is None:
            return failed("benchmark_exact_path_missing")
        values.append(value)

    paths: list[list[float]] = []
    expected_entry = comparison_dates[0]
    final_exit_index = -1
    for row in rebalances:
        if type(row) is not dict or type(row.get("entry")) is not str \
                or type(row.get("exit")) is not str:
            return failed("benchmark_interval_invalid")
        entry, exit_ = row["entry"], row["exit"]
        if entry != expected_entry or entry not in positions or exit_ not in positions:
            return failed("benchmark_intervals_not_contiguous")
        entry_index, exit_index = positions[entry], positions[exit_]
        if exit_index <= entry_index:
            return failed("benchmark_interval_invalid")
        base = values[entry_index]
        path = [value / base for value in values[entry_index:exit_index + 1]]
        if any(not isfinite(value) or value <= 0 for value in path):
            return failed("benchmark_interval_invalid")
        paths.append(path)
        expected_entry = exit_
        final_exit_index = exit_index
    if final_exit_index != len(comparison_dates) - 1:
        return failed("benchmark_intervals_do_not_cover_split")
    try:
        result = _settle_benchmark_paths(paths)
    except ValueError:
        return failed("benchmark_schedule_accounting_invalid")
    return {
        **result,
        "comparisonFrom": comparison_dates[0],
        "comparisonTo": comparison_dates[-1],
        "comparisonTradingDays": len(comparison_dates),
    }


def significance(excess: list[float], resamples: int = 2000, seed: int = 20260808) -> dict:
    """Is the per-rebalance edge distinguishable from luck?

    A cumulative return says nothing about reliability: nine rebalances can
    compound to a large number on one lucky pick.  The t statistic and the
    bootstrap interval are what decide whether an edge is real, and a interval
    spanning zero means the variant has not been shown to work.
    """
    if type(excess) is not list or type(resamples) is not int or type(seed) is not int \
            or resamples < 40 or any(
                _bounded_number(value) is None
                for value in excess
            ):
        return {"rebalances": 0, "conclusive": False, "reason": "invalid_significance_sample"}
    count = len(excess)
    if count < 3:
        return {"rebalances": count, "conclusive": False, "reason": "fewer_than_three_rebalances"}
    average = mean(excess)
    spread = stdev(excess)
    t_stat = average / (spread / sqrt(count)) if spread else None
    generator = Random(seed)
    means = []
    for _ in range(resamples):
        sample = [excess[generator.randrange(count)] for _ in range(count)]
        means.append(mean(sample))
    means.sort()
    low = means[int(0.025 * resamples)]
    high = means[int(0.975 * resamples) - 1]
    return {
        "rebalances": count,
        "meanExcessPerRebalance": average,
        "stdev": spread,
        "tStat": t_stat,
        "ci95": [low, high],
        # An interval containing zero cannot support a promotion decision.
        "conclusive": low > 0 or high < 0,
    }


def net_of_costs(gross: float) -> float:
    """One round trip in a stock, after fees, transaction tax and slippage."""
    bounded = _bounded_number(gross)
    if bounded is None or not (-1 < bounded <= MAX_NUMERIC_ABS):
        raise ValueError("invalid_strategy_return")
    result = (1 + bounded) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) \
        * (1 - SELL_FEE - STOCK_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1
    if not isfinite(result) or not (-1 < result <= MAX_NUMERIC_ABS):
        raise ValueError("invalid_strategy_return")
    return result


def _history_price(value: object) -> float | None:
    if type(value) not in (tuple, list) or not value:
        return None
    return _benchmark_value(value[0])


def _gross_price_return(entry: float | None, exit_: float | None) -> float | None:
    if entry is None or exit_ is None:
        return None
    factor = exit_ / entry
    if not isfinite(factor) or factor <= 0:
        return None
    result = factor - 1
    return result if isfinite(result) and -1 < result <= MAX_NUMERIC_ABS else None


def pool_summary(rebalances: list[dict]) -> dict:
    excess = [item["poolExcess"] for item in rebalances if item["poolExcess"] is not None]
    sizes = sorted(item["poolSize"] for item in rebalances)
    summary = significance(excess)
    summary["medianPoolSize"] = sizes[len(sizes) // 2] if sizes else 0
    return summary


def fundamental_records(quotes: dict[str, dict], published: dict[str, dict]) -> dict[str, dict]:
    """Merge published financials with the ratios that need a live price.

    Only companies with a published record carry financial factors; the rest keep
    an empty record so they still enter ranking on technicals alone, exactly as
    production behaves when its fundamentals cache is incomplete.
    """
    records = {}
    for code, quote in quotes.items():
        record = dict(published.get(code, {}))
        record.pop("pe", None)
        eps = record.get("eps")
        price = quote.get("price")
        if number(eps) and eps > 0 and number(price) and price > 0:
            derived_pe = price / eps
            if number(derived_pe):
                record["pe"] = derived_pe
        records[code] = record
    return records


def _selection_evidence_at(source: object, signal_date: str) -> dict | None:
    """Resolve a point-in-time selection-quality snapshot without fallback."""
    if callable(source):
        value = source(signal_date)
    elif isinstance(source, dict) and isinstance(source.get("byDate"), dict):
        value = source["byDate"].get(signal_date)
    else:
        # A single undated object is unsafe for historical use: it can silently
        # apply today's actions/data-contract evidence to every earlier signal.
        value = None
    if not isinstance(value, dict):
        return None
    if set(value) != {
        "schemaVersion", "signalDate", "decisionAsOf", "quality", "conflictStatus",
        "actions", "contractBlockers", "evidenceHash",
    } or value.get("schemaVersion") != SELECTION_EVIDENCE_SCHEMA_VERSION:
        return None
    try:
        if date.fromisoformat(signal_date).isoformat() != signal_date:
            return None
    except (TypeError, ValueError):
        return None
    decision = _aware(value.get("decisionAsOf"))
    actions = value.get("actions")
    blockers = value.get("contractBlockers")
    if decision is None or decision.utcoffset() != timedelta(hours=8) \
            or decision.date().isoformat() != signal_date \
            or (decision.hour, decision.minute, decision.second, decision.microsecond) != (14, 0, 0, 0) \
            or value.get("signalDate") != signal_date \
            or value.get("quality") != "verified" \
            or value.get("conflictStatus") != "no_conflict" \
            or not isinstance(actions, dict) or not isinstance(blockers, list) \
            or any(not isinstance(item, str) for item in blockers):
        return None
    available = _aware(actions.get("availableAt"))
    if available is None or available > decision \
            or actions.get("conflictStatus") != "no_conflict" \
            or not isinstance(actions.get("source"), str) \
            or not isinstance(actions.get("dataset"), str) \
            or not isinstance(actions.get("queried_codes"), list) \
            or not isinstance(actions.get("failures"), dict):
        return None
    body = {key: value[key] for key in value if key != "evidenceHash"}
    if value.get("evidenceHash") != _canonical_hash(body):
        return None
    return {
        "decisionAsOf": value["decisionAsOf"],
        "actions": actions,
        "contractBlockers": list(blockers),
        "evidenceHash": value["evidenceHash"],
    }


def select_signal_candidates(
    quotes: dict[str, dict],
    normalized_fundamentals: dict[str, dict],
    signal_date: str,
    selection_evidence: object,
) -> dict:
    """Run the actual comprehensive selection adapter for one signal date."""
    evidence = _selection_evidence_at(selection_evidence, signal_date)
    return rank_and_assess(
        quotes,
        normalized_fundamentals,
        actions=evidence["actions"] if evidence else None,
        contract_blockers=evidence["contractBlockers"] if evidence else None,
    )


def run_range(history: list[dict], dates: list[str], start: int, end: int, style: str, picks: int,
              holding: int, min_volume: float, weights: dict | None = None,
              minimum_coverage: int | None = None, pit: object | None = None,
              continuous_trend: bool = False, reversal_aware: bool = False,
              selection_evidence: object | None = None,
              outcome_evidence: object | None = None,
              benchmark_values: dict[str, float] | None = None) -> dict:
    """Signals inside ``[start, end)``, exits kept inside it too.

    Moving averages read days before ``start`` on purpose: that is past data at
    signal time, and restarting the warm-up per split would throw away the first
    twenty days of every window.  Exits are held below ``end`` so a position
    never reaches into the next split.
    """
    if type(start) is not int or type(end) is not int or type(holding) is not int \
            or type(picks) is not int or _bounded_number(min_volume) is None \
            or not (0 <= min_volume <= MAX_NUMERIC_ABS) \
            or holding < 1 or picks < 1 \
            or start < 0 or end <= start or end > len(history) or len(dates) != len(history):
        raise ValueError("invalid_backtest_schedule")
    returns: list[float] = []
    rebalances: list[dict] = []
    benchmark_intervals: list[dict] = []
    wins = 0
    unfilled = 0
    stale_exits = 0
    unresolved_exits = 0
    no_candidate = 0
    selection_blockers: set[str] = set()
    selection_periods = 0
    selection_evidence_periods = 0
    cutoff_tie_periods = 0
    comprehensive_cohorts: list[dict] = []
    if style == "comprehensive":
        selection_blockers.update({
            "selection_evidence_authority_unregistered",
            "legacy_execution_accounting_unregistered",
        })
    index = max(start, MA_LONG)
    while index + holding + 1 < end:
        selection_periods += 1
        # Production comprehensive selection has no explicit liquidity filter.
        # Applying the old CLI default here changed the eligible pool before
        # scoring and made the evaluator test a different strategy.
        quote_floor = 0 if style == "comprehensive" else min_volume
        quotes = factor_quotes(history, index, quote_floor)
        # Fundamentals are read as of the signal date, so only figures already
        # filed on that day can influence the pick.  Without a cache every name
        # carries an empty record and `coverage` reflects that honestly.
        published = pit.as_of(dates[index]) if pit is not None else {}
        # Rank the whole eligible pool once: the head is what the product would
        # recommend, the rest is the pool it was chosen from.
        records = fundamental_records(quotes, published)
        if style == "comprehensive":
            if picks != 3 or weights is not None or minimum_coverage is not None \
                    or continuous_trend or reversal_aware:
                raise ValueError("comprehensive_selection_policy_mismatch")
            evidence = _selection_evidence_at(selection_evidence, dates[index])
            selection = select_signal_candidates(
                quotes, records, dates[index], selection_evidence
            )
            eligible = selection["poolTuples"]
            selected = selection["selectedTuples"]
            if evidence:
                selection_evidence_periods += 1
            else:
                selection_blockers.add("selection_evidence_missing")
            if selection["cutoffTieDependent"]:
                cutoff_tie_periods += 1
                selection_blockers.add("cutoff_tie_dependent")
        else:
            eligible = candidates(style, quotes, records, None,
                                  weights=weights, minimum_coverage=minimum_coverage,
                                  continuous_trend=continuous_trend, reversal_aware=reversal_aware)
            selected = eligible[:picks]
        if style == "comprehensive":
            # Signal at this close, enter at the next official market date, and
            # measure exactly ``holding`` trading intervals.  The horizon is a
            # research measurement, not a registered live sell instruction.
            measurement_dates = dates[index:index + holding + 2]
            pool_codes = [row["code"] for row in selection["fullPool"]]
            price_paths = {
                code: {
                    dates[position]: float(history[position][code][0])
                    for position in range(index + 1, index + holding + 2)
                    if code in history[position]
                }
                for code in pool_codes
            }
            cohort = measure_cohort({
                "schemaVersion": 1,
                "selection": {
                    key: value for key, value in selection.items()
                    if key not in {"poolTuples", "previewTuples", "selectedTuples"}
                },
                "dates": measurement_dates,
                "pricePaths": price_paths,
                "benchmarkTotalReturn": {
                    day: benchmark_values[day]
                    for day in measurement_dates[1:]
                    if benchmark_values is not None and day in benchmark_values
                },
                "outcomeEvidence": _dated_evidence_at(outcome_evidence, dates[index]),
            }, enabled=True)
            comprehensive_cohorts.append(cohort)
            accounting = cohort.get("selectionAccounting", {})
            selection_blockers.update(cohort.get("blockers", []))
            unfilled += int(accounting.get("unfilledEntrySlots", 0))
            if not selection["qualityPassedCodes"]:
                no_candidate += 1
            if cohort.get("accountingComplete") is True:
                net = float(cohort["selectionReturn"])
                returns.append(net)
                if accounting.get("closedSlots", 0) > 0:
                    wins += net > 0
                pool_excess = cohort.get("selectionExcessVersusPool")
                # Every complete scheduled period belongs to the comparison
                # sample, including all-cash and all-unfilled periods.  Active
                # trade counts and win rate remain separate diagnostics.
                rebalances.append({
                    "entry": cohort["comparisonFrom"],
                    "exit": cohort["comparisonTo"],
                    "net": net,
                    "poolExcess": pool_excess,
                    "poolSize": cohort["selectionIdentity"]["poolSize"],
                    "active": accounting.get("closedSlots", 0) > 0,
                })
            index += holding
            continue
        window = history[index + 1:index + holding + 2]
        trade_returns: list[float] = []
        period_unresolved = False
        for _score, _coverage, code, _quote, _fund in selected:
            entry = _history_price(window[0].get(code))
            if entry is None:
                unfilled += 1
                continue
            exit_price = _history_price(window[-1].get(code))
            if exit_price is None:
                exit_price = next((
                    price for day in reversed(window[1:-1])
                    if (price := _history_price(day.get(code))) is not None
                ), None)
                stale_exits += 1
            if exit_price is None:
                unresolved_exits += 1
                period_unresolved = True
                selection_blockers.add("unresolved_exit_price")
                continue
            gross = _gross_price_return(entry, exit_price)
            if gross is None:
                unresolved_exits += 1
                period_unresolved = True
                selection_blockers.add("unresolved_exit_price")
                continue
            trade_returns.append(gross)
        if not selected:
            no_candidate += 1
        # What an equal-weighted holding of every eligible name would have
        # returned. Beating 0050 mixes the screen's skill with whatever the
        # size and sector tilt did; this isolates the screen itself, which is
        # the question that matters when the product is a shortlist.
        eligible_returns = []
        for _s, _c, code, _q, _f in eligible:
            entry = _history_price(window[0].get(code))
            if entry is None:
                continue
            close = _history_price(window[-1].get(code))
            if close is None:
                close = next((
                    price for day in reversed(window[1:-1])
                    if (price := _history_price(day.get(code))) is not None
                ), None)
            gross = _gross_price_return(entry, close)
            if gross is not None:
                eligible_returns.append(gross)
        active = bool(trade_returns) and not period_unresolved
        net = net_of_costs(mean(trade_returns)) if active else (None if period_unresolved else 0.0)
        if active:
            returns.append(net)
            wins += net > 0
        # Every scheduled interval remains in the benchmark/significance
        # denominator.  A legitimate no-candidate or unfilled interval is cash
        # with zero strategy return, not permission to omit a weak benchmark
        # period.  Active trade counts remain a separate diagnostic.
        pool_return = net_of_costs(mean(eligible_returns)) if eligible_returns else None
        benchmark_intervals.append({
            "entry": dates[index + 1], "exit": dates[index + holding + 1], "net": net,
        })
        if active:
            # Preserve the legacy pool-diagnostic sample: Node75 changes only
            # the benchmark denominator, not the separately versioned active
            # selection-versus-pool research statistic.
            rebalances.append({
                "entry": dates[index + 1], "exit": dates[index + holding + 1], "net": net,
                "poolExcess": net - pool_return if pool_return is not None else None,
                "poolSize": len(eligible_returns),
            })
        index += holding
    if style == "comprehensive":
        summary = aggregate_measurements(comprehensive_cohorts)
        if summary["complete"]:
            benchmark_periods = summary["benchmarkScheduledReturns"]
            if len(benchmark_periods) != len(rebalances):
                raise ValueError("scheduled_benchmark_comparison_mismatch")
            for item, reference in zip(rebalances, benchmark_periods):
                item["benchmarkReturn"] = reference
        else:
            # One unresolved cohort invalidates the split; never report a
            # partial significance sample from only the surviving periods.
            rebalances = []
        active = int(summary["executionAccounting"].get("investedPeriods", 0))
        return {
            "return": summary["return"],
            "mdd": summary["mdd"],
            "mddBasis": "daily_mark_to_market_including_costs",
            "trades": active,
            "scheduledPeriods": int(summary["executionAccounting"].get("scheduledPeriods", 0)),
            "win_rate": wins / active if active else 0.0,
            "unfilled": int(summary["executionAccounting"].get("unfilledEntrySlots", 0)),
            "stale_exits": 0,
            "rebalances_without_candidates": no_candidate,
            "returns": returns,
            "rebalances": rebalances,
            "selectionPolicyVersion": SELECTION_POLICY_VERSION,
            "selectionAdapterUsed": True,
            "selectionPeriods": selection_periods,
            "selectionEvidencePeriods": selection_evidence_periods,
            "selectionEvidenceShapeComplete": selection_evidence_periods == selection_periods,
            "selectionEvidenceComplete": False,
            "selectionCertified": False,
            "performanceEligible": False,
            "executionAccountingStatus": "registered_for_measurement_only",
            "liveExecutionSpecStatus": "decision_required",
            "riskPolicyStatus": "unregistered",
            "eligiblePoolAccountingStatus": "registered_for_measurement_only",
            "outcomeAccountingPolicyVersion": OUTCOME_ACCOUNTING_POLICY_VERSION,
            "outcomeAccountingComplete": summary["complete"],
            "executionAccounting": summary["executionAccounting"],
            "eligiblePoolAccounting": summary["eligiblePoolAccounting"],
            "eligiblePoolReturn": summary["eligiblePoolReturn"],
            "eligiblePoolMdd": summary["eligiblePoolMdd"],
            "benchmarkReturn": summary["benchmarkReturn"],
            "benchmarkMdd": summary["benchmarkMdd"],
            "benchmarkCostedRoundTrips": summary["benchmarkCostedRoundTrips"],
            "benchmarkCostModel": summary["benchmarkCostModel"],
            "benchmarkScheduledReturns": summary["benchmarkScheduledReturns"],
            "comparisonFrom": summary.get("comparisonFrom"),
            "comparisonTo": summary.get("comparisonTo"),
            "measurementDigests": summary.get("measurementDigests", []),
            "cutoffTieDependentPeriods": cutoff_tie_periods,
            "selectionBlockers": sorted(selection_blockers | set(summary["blockers"])),
            "versusEligiblePool": pool_summary(rebalances),
        }
    equity = peak = 1.0
    drawdown = 0.0
    legacy_return_complete = unresolved_exits == 0
    for value in returns:
        next_equity = equity * (1 + value)
        if not isfinite(next_equity) or not (0 < next_equity <= MAX_NUMERIC_ABS):
            legacy_return_complete = False
            selection_blockers.add("legacy_return_out_of_numeric_domain")
            break
        equity = next_equity
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    return {
        "return": equity - 1 if legacy_return_complete else None,
        "mdd": drawdown if legacy_return_complete else None,
        "legacyReturnComplete": legacy_return_complete,
        "trades": len(returns),
        "scheduledPeriods": len(benchmark_intervals),
        "win_rate": wins / len(returns) if returns else 0.0,
        "unfilled": unfilled, "stale_exits": stale_exits, "unresolved_exits": unresolved_exits,
        "rebalances_without_candidates": no_candidate,
        "returns": returns, "rebalances": rebalances, "benchmarkIntervals": benchmark_intervals,
        "selectionPolicyVersion": (
            SELECTION_POLICY_VERSION if style == "comprehensive" else "legacy-research-selection"
        ),
        "selectionAdapterUsed": style == "comprehensive",
        "selectionPeriods": selection_periods,
        "selectionEvidencePeriods": selection_evidence_periods,
        "selectionEvidenceShapeComplete": (
            style != "comprehensive" or selection_evidence_periods == selection_periods
        ),
        # Shape-valid caller evidence is not an admitted authority artifact.
        # Legacy execution below also remains explicitly non-promotable.
        "selectionEvidenceComplete": style != "comprehensive",
        "selectionCertified": style != "comprehensive",
        "performanceEligible": False,
        "executionAccountingStatus": "legacy_unregistered",
        "comparisonFrom": benchmark_intervals[0]["entry"] if benchmark_intervals else None,
        "comparisonTo": benchmark_intervals[-1]["exit"] if benchmark_intervals else None,
        "cutoffTieDependentPeriods": cutoff_tie_periods,
        "selectionBlockers": sorted(selection_blockers),
        # The screening question: did ranking beat holding the pool it ranked?
        # Beating 0050 also rewards the pool's size tilt; this does not.
        "versusEligiblePool": pool_summary(rebalances),
    }


def evaluate(data: Path, benchmark: Path, style: str, picks: int, holding: int,
             min_volume: float, drop: tuple[str, ...] = (), pit: object | None = None,
             continuous_trend: bool = False, reversal_aware: bool = False,
             selection_evidence: object | None = None,
             outcome_evidence: object | None = None) -> dict:
    dates, history = load_history(data)
    if len(history) < 120:
        raise SystemExit(f"歷史資料只有 {len(history)} 個交易日，不足 120，無法切出樣本外測試。")
    train_end = int(len(history) * 0.60)
    validation_end = int(len(history) * 0.80)
    parts = {
        "train": (0, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, len(history)),
    }
    weights = {key: value for key, value in WEIGHTS[style].items() if key not in drop} if drop else None
    # Dropping factors lowers the achievable coverage, so the production floor
    # would reject every candidate; scale it by what is still reachable.
    coverage_floor = None
    if weights is not None:
        reachable = sum(weights.get(key, 0) for key in ("trend20", "trend5", "change"))
        coverage_floor = min(MINIMUM_COVERAGE[style], reachable)
    series = benchmark_series(benchmark)
    result = {}
    for name, (start, end) in parts.items():
        run = run_range(history, dates, start, end, style, picks, holding, min_volume,
                        weights, coverage_floor, pit, continuous_trend, reversal_aware,
                        selection_evidence, outcome_evidence, series)
        comparison_dates = dates[start:end]
        if run.get("comparisonFrom") and run.get("comparisonTo"):
            comparison_dates = [
                day for day in dates
                if run["comparisonFrom"] <= day <= run["comparisonTo"]
            ]
        if style == "comprehensive":
            # Preserve the already-correct, independently implemented
            # comprehensive accounting path as the parity oracle for Node75.
            run["benchmark"] = run.get("benchmarkReturn")
            run["excess"] = (
                run["return"] - run["benchmark"]
                if run["benchmark"] is not None and run["return"] is not None else None
            )
            per_rebalance = []
            for item in run["rebalances"]:
                reference = item.get("benchmarkReturn")
                if reference is not None:
                    per_rebalance.append(item["net"] - reference)
            run["significance"] = significance(per_rebalance)
        else:
            comparison_rows = run.get("benchmarkIntervals", [])
            schedule = benchmark_schedule(series, comparison_dates, comparison_rows)
            benchmark_complete = schedule["complete"] is True
            benchmark_blockers = schedule["blockers"]
            run["benchmarkReturn"] = schedule["return"]
            run["benchmarkCostedRoundTrips"] = schedule["costedRoundTrips"]
            run["benchmarkCostModel"] = schedule["costModel"]
            run["benchmarkComparatorVersion"] = schedule["comparatorVersion"]
            run["benchmarkScheduledReturns"] = schedule["scheduledReturns"]
            if benchmark_complete and len(schedule["scheduledReturns"]) == len(comparison_rows):
                for item, reference in zip(comparison_rows, schedule["scheduledReturns"]):
                    item["benchmarkReturn"] = reference
            else:
                benchmark_complete = False
                benchmark_blockers = sorted(set(benchmark_blockers) | {"benchmark_schedule_mismatch"})
            run["benchmarkAccountingComplete"] = benchmark_complete
            run["benchmarkBlockers"] = benchmark_blockers
            run["benchmark"] = run.get("benchmarkReturn") if benchmark_complete else None
            performance_blockers = set(benchmark_blockers)
            legacy_return_complete = run.get("legacyReturnComplete") is True
            if not legacy_return_complete:
                legacy_blockers = set(run.get("selectionBlockers", []))
                performance_blockers.update(legacy_blockers or {"legacy_return_incomplete"})
            split_excess = None
            bounded_return = _bounded_number(run.get("return"))
            bounded_benchmark = _bounded_number(run["benchmark"])
            if legacy_return_complete and bounded_benchmark is not None \
                    and bounded_return is not None:
                candidate_excess = bounded_return - bounded_benchmark
                if isfinite(candidate_excess) and abs(candidate_excess) <= MAX_NUMERIC_ABS:
                    split_excess = candidate_excess
                else:
                    performance_blockers.add("legacy_split_excess_out_of_numeric_domain")
            run["excess"] = split_excess
            per_rebalance = []
            for item in comparison_rows if legacy_return_complete else []:
                reference = _bounded_number(item.get("benchmarkReturn"))
                item_net = _bounded_number(item.get("net"))
                if reference is not None and item_net is not None \
                        and -1 < item_net <= MAX_NUMERIC_ABS:
                    excess = item_net - reference
                    if isfinite(excess) and abs(excess) <= MAX_NUMERIC_ABS:
                        per_rebalance.append(excess)
                    else:
                        performance_blockers.add("legacy_period_excess_out_of_numeric_domain")
                else:
                    performance_blockers.add("legacy_period_return_unresolved")
            performance_complete = benchmark_complete and legacy_return_complete \
                and split_excess is not None and len(per_rebalance) == len(comparison_rows) \
                and not performance_blockers
            run["performanceAccountingComplete"] = performance_complete
            run["performanceBlockers"] = sorted(performance_blockers)
            run["significance"] = (
                significance(per_rebalance)
                if performance_complete
                else {
                    "rebalances": 0,
                    "conclusive": False,
                    "reason": "legacy_performance_accounting_incomplete",
                    "blockers": sorted(performance_blockers),
                }
            )
        run.pop("returns")
        run.pop("rebalances")
        run.pop("benchmarkIntervals", None)
        result[name] = run
    return {
        "schemaVersion": 2 if style == "comprehensive" else 1,
        "artifactPolicyVersion": (
            "actual-comprehensive-outcome-accounting-v1"
            if style == "comprehensive" else "legacy-strategy-backtest-single-split-benchmark-v2"
        ),
        **({"benchmarkComparatorVersion": BENCHMARK_COMPARATOR_VERSION}
           if style != "comprehensive" else {}),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rule": "production scoring rule from scoring.py",
        "style": style,
        "parameters": {"picks": picks, "holding": holding,
                       "minVolume": None if style == "comprehensive" else min_volume,
                       "minimumCoverage": MINIMUM_COVERAGE[style]},
        "dataStart": dates[0], "dataEnd": dates[-1], "tradingDays": len(history),
        "factorBasis": ("technical factors plus point-in-time fundamentals" if pit is not None
                        else "technical only (no point-in-time fundamentals available)"),
        "splits": result,
        "status": "research_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="以正式評分規則回測")
    parser.add_argument("--input", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--style", default="swing", choices=sorted(MINIMUM_COVERAGE))
    parser.add_argument("--picks", type=int, default=3)
    parser.add_argument("--holding", type=int, default=5)
    parser.add_argument("--min-volume", type=float, default=500_000)
    parser.add_argument("--drop", action="append", default=[],
                        help="研究用：從權重中移除某個因子，可重複（例如 --drop change）")
    parser.add_argument("--continuous-trend", action="store_true",
                        help="研究用：趨勢因子改用與均線的乖離幅度，取代二元的 75/35")
    parser.add_argument("--fundamentals-cache", type=Path, default=None,
                        help="私有歷史財報快取目錄；提供後才能回測依賴基本面的 style")
    parser.add_argument("--selection-evidence", type=Path, default=None,
                        help="point-in-time actions/data-contract evidence by signal date")
    parser.add_argument("--outcome-evidence", type=Path, default=None,
                        help="hash-bound corporate-action/terminal evidence by signal date")
    parser.add_argument("--output", type=Path, default=Path("data/strategy-backtest.json"))
    args = parser.parse_args()
    pit = PointInTimeFundamentals.from_cache(args.fundamentals_cache) if args.fundamentals_cache else None
    result = evaluate(args.input, args.benchmark, args.style, args.picks, args.holding,
                      args.min_volume, tuple(args.drop), pit, args.continuous_trend,
                      selection_evidence=load_selection_evidence(args.selection_evidence),
                      outcome_evidence=load_selection_evidence(args.outcome_evidence))
    result["droppedFactors"] = list(args.drop)
    result["continuousTrend"] = args.continuous_trend
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
