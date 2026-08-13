"""Strict outcome accounting for the shipped comprehensive selector.

This module joins the executable selection boundary from Node54 with the
fixed-slot accounting boundary from Node47.  It is a measurement engine, not a
live trading policy: a successful fixture can prove that returns were measured
consistently, but it cannot authenticate PIT evidence, choose a holding period,
approve risk, or enable advice.

The public entry point is default-off.  When enabled it measures one frozen
cohort over an explicit signal/entry/exit calendar.  The selected top-three,
the complete eligible pool, and official 0050 total return all use the same
daily path and cost assumptions.  Missing entry prices remain cash; missing
marks, exits, corporate-action coverage, or terminal-value evidence make the
cohort unresolved instead of falling back to a stale quote.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from typing import Any

from actual_comprehensive_selection import (
    POLICY_VERSION as SELECTION_POLICY_VERSION,
    SCHEMA_VERSION as SELECTION_SCHEMA_VERSION,
    digest as selection_digest,
)
from backtest import BUY_FEE, ETF_SELL_TAX, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX
from execution_accounting import (
    POLICY_VERSION as ACCOUNTING_POLICY_VERSION,
    daily_equity_curve,
    max_drawdown_from_equity,
    settle_equal_weight_period,
    unconfigured_risk_policy,
)


SCHEMA_VERSION = 1
POLICY_VERSION = "actual-comprehensive-outcome-accounting-v1"
OUTCOME_EVIDENCE_SCHEMA_VERSION = 1
MEASUREMENT_HORIZONS = (5, 20, 60)
TARGET_SLOTS = 3
DECISION_CUTOFF = (14, 0, 0)
SETTLEMENT_CUTOFF = (18, 0, 0)

STOCK_BUY_COST = BUY_FEE + SLIPPAGE_BPS / 10_000
STOCK_SELL_COST = SELL_FEE + STOCK_SELL_TAX + SLIPPAGE_BPS / 10_000
ETF_BUY_COST = BUY_FEE + SLIPPAGE_BPS / 10_000
ETF_SELL_COST = SELL_FEE + ETF_SELL_TAX + SLIPPAGE_BPS / 10_000

ROOT_KEYS = frozenset({
    "schemaVersion", "selection", "dates", "pricePaths", "benchmarkTotalReturn",
    "outcomeEvidence",
})
SELECTION_KEYS = frozenset({
    "schemaVersion", "policyVersion", "style", "previewPicks", "fullPool", "preview",
    "qualityPassedCodes", "noBackfill", "cutoffTieDependent", "selectionEvidenceSupplied",
    "selectionDigest",
})
PREVIEW_KEYS = frozenset({
    "code", "name", "style", "rank", "score", "coverage", "entryPrice", "quality",
})
EVIDENCE_KEYS = frozenset({
    "schemaVersion", "signalDate", "entryDate", "exitDate", "settlementAsOf",
    "quality", "conflictStatus", "byCode", "evidenceHash",
})
CODE_EVIDENCE_KEYS = frozenset({
    "coverageComplete", "quality", "conflictStatus", "events", "terminal",
})
EVENT_KEYS = frozenset({
    "eventId", "effectiveDate", "availableAt", "factor", "quality", "conflictStatus",
})
TERMINAL_KEYS = frozenset({
    "date", "availableAt", "value", "quality", "conflictStatus",
})


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _bounded_json(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> bool:
    """Bound hostile callers before hashing, sorting, or numeric conversion."""
    if budget is None:
        budget = [200_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 10:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return abs(value) <= 10**15
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= 1_024 and not any(ord(ch) < 32 for ch in value)
    if isinstance(value, list):
        return len(value) <= 100_000 and all(
            _bounded_json(item, depth=depth + 1, budget=budget) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 100_000 and all(
            isinstance(key, str) and len(key) <= 128
            and _bounded_json(item, depth=depth + 1, budget=budget)
            for key, item in value.items()
        )
    return False


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _strict_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _finite(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _safe_text(value: Any, *, maximum: int = 200) -> bool:
    if not isinstance(value, str) or not value or len(value) > maximum \
            or any(ord(ch) < 32 for ch in value):
        return False
    lowered = value.casefold()
    return not any(marker in lowered for marker in (
        "://", "bearer ", "authorization:", "token=", "password=",
        "cookie:", "-----begin ",
    ))


def _public_selection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != SELECTION_KEYS:
        return None
    public = {key: item for key, item in value.items() if key in SELECTION_KEYS}
    if set(public) != SELECTION_KEYS:
        return None
    body = {key: item for key, item in public.items() if key != "selectionDigest"}
    if public.get("schemaVersion") != SELECTION_SCHEMA_VERSION \
            or public.get("policyVersion") != SELECTION_POLICY_VERSION \
            or public.get("style") != "comprehensive" \
            or public.get("previewPicks") != TARGET_SLOTS \
            or public.get("noBackfill") is not True \
            or public.get("selectionDigest") != selection_digest(body):
        return None
    full_pool = public.get("fullPool")
    preview = public.get("preview")
    passed = public.get("qualityPassedCodes")
    if not isinstance(full_pool, list) or not isinstance(preview, list) or not isinstance(passed, list):
        return None
    pool_codes: list[str] = []
    for row in full_pool:
        if not isinstance(row, dict) or set(row) != {"code", "score", "coverage", "volume"}:
            return None
        code = row.get("code")
        if not isinstance(code, str) or len(code) != 4 or not code.isdigit() or code in pool_codes:
            return None
        if _finite(row.get("score")) is None or _finite(row.get("coverage")) is None \
                or _finite(row.get("volume")) is None:
            return None
        pool_codes.append(code)
    preview_codes: list[str] = []
    expected_passed: list[str] = []
    for index, row in enumerate(preview):
        if not isinstance(row, dict) or set(row) != PREVIEW_KEYS:
            return None
        code = row.get("code")
        quality = row.get("quality")
        if not isinstance(code, str) or code in preview_codes or index >= TARGET_SLOTS \
                or index >= len(pool_codes) or code != pool_codes[index] \
                or not _safe_text(row.get("name")) \
                or row.get("style") != "comprehensive" \
                or row.get("rank") != index + 1 \
                or _finite(row.get("score")) is None \
                or _finite(row.get("coverage")) is None \
                or _finite(row.get("entryPrice"), positive=True) is None \
                or not isinstance(quality, dict) \
                or set(quality) != {"passed", "blockers"} \
                or not isinstance(quality.get("passed"), bool) \
                or not isinstance(quality.get("blockers"), list) \
                or any(not _safe_text(item, maximum=128) for item in quality["blockers"]):
            return None
        if quality["passed"] != (not quality["blockers"]):
            return None
        preview_codes.append(code)
        if quality["passed"]:
            expected_passed.append(code)
    if passed != expected_passed or any(code not in preview_codes for code in passed):
        return None
    return public


def build_outcome_evidence(
    signal_date: str,
    entry_date: str,
    exit_date: str,
    by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a hash-bound settlement evidence *shape* for fixtures/adapters.

    The builder does not authenticate the caller.  Authority remains a separate
    admission gate and all measurement reports remain non-promotable.
    """
    body = {
        "schemaVersion": OUTCOME_EVIDENCE_SCHEMA_VERSION,
        "signalDate": signal_date,
        "entryDate": entry_date,
        "exitDate": exit_date,
        "settlementAsOf": f"{exit_date}T18:00:00+08:00",
        "quality": "verified",
        "conflictStatus": "no_conflict",
        "byCode": by_code,
    }
    return {**body, "evidenceHash": digest(body)}


def _validated_evidence(value: Any, dates: list[str], pool_codes: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != EVIDENCE_KEYS \
            or value.get("schemaVersion") != OUTCOME_EVIDENCE_SCHEMA_VERSION \
            or value.get("signalDate") != dates[0] \
            or value.get("entryDate") != dates[1] \
            or value.get("exitDate") != dates[-1] \
            or value.get("quality") != "verified" \
            or value.get("conflictStatus") != "no_conflict":
        return None
    settlement = _aware(value.get("settlementAsOf"))
    if settlement is None or settlement.utcoffset() != timedelta(hours=8) \
            or settlement.date().isoformat() != dates[-1] \
            or (settlement.hour, settlement.minute, settlement.second) != SETTLEMENT_CUTOFF:
        return None
    body = {key: item for key, item in value.items() if key != "evidenceHash"}
    if value.get("evidenceHash") != digest(body):
        return None
    by_code = value.get("byCode")
    if not isinstance(by_code, dict) or set(by_code) != set(pool_codes):
        return None
    for code, record in by_code.items():
        if not isinstance(code, str) or not isinstance(record, dict) \
                or set(record) != CODE_EVIDENCE_KEYS \
                or not isinstance(record.get("coverageComplete"), bool) \
                or record.get("quality") != "verified" \
                or record.get("conflictStatus") != "no_conflict" \
                or not isinstance(record.get("events"), list):
            return None
        seen_events: set[str] = set()
        for event in record["events"]:
            if not isinstance(event, dict) or set(event) != EVENT_KEYS \
                    or not _safe_text(event.get("eventId")) \
                    or event["eventId"] in seen_events \
                    or event.get("quality") != "verified" \
                    or event.get("conflictStatus") != "no_conflict" \
                    or _finite(event.get("factor"), positive=True) is None:
                return None
            effective = _strict_date(event.get("effectiveDate"))
            available = _aware(event.get("availableAt"))
            if effective is None or not dates[1] <= effective <= dates[-1] \
                    or available is None or available > settlement:
                return None
            seen_events.add(event["eventId"])
        terminal = record.get("terminal")
        if terminal is not None:
            if not isinstance(terminal, dict) or set(terminal) != TERMINAL_KEYS \
                    or terminal.get("date") != dates[-1] \
                    or terminal.get("quality") != "verified" \
                    or terminal.get("conflictStatus") != "no_conflict" \
                    or _finite(terminal.get("value"), positive=True) is None:
                return None
            available = _aware(terminal.get("availableAt"))
            if available is None or available > settlement:
                return None
    return value


def _outcome(
    code: str,
    dates: list[str],
    prices: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    entry = _finite(prices.get(dates[1]), positive=True) if isinstance(prices, dict) else None
    if entry is None:
        return {"status": "cash_unfilled", "pathLength": len(dates) - 1}
    record = evidence.get("byCode", {}).get(code) if evidence else None
    if not isinstance(record, dict) or record.get("coverageComplete") is not True:
        return {"status": "unresolved_exit", "reason": "corporate_action_coverage_incomplete"}
    values: list[float] = []
    terminal = record.get("terminal")
    for day in dates[1:]:
        value = _finite(prices.get(day), positive=True) if isinstance(prices, dict) else None
        if value is None and day == dates[-1] and isinstance(terminal, dict):
            value = _finite(terminal.get("value"), positive=True)
        if value is None:
            return {"status": "unresolved_exit", "reason": "daily_mark_or_exit_missing"}
        factor = 1.0
        for event in record.get("events", []):
            if event["effectiveDate"] <= day:
                factor *= float(event["factor"])
        values.append(value * factor / entry)
    return {
        "status": "closed",
        "grossReturn": values[-1] - 1,
        "dailyGrossFactors": values,
    }


def _benchmark(values: Any, dates: list[str]) -> dict[str, Any]:
    path: list[float] = []
    if not isinstance(values, dict) or set(values) != set(dates[1:]):
        return {"complete": False, "return": None, "mdd": None, "blockers": ["benchmark_path_missing"]}
    entry = _finite(values.get(dates[1]), positive=True)
    for day in dates[1:]:
        value = _finite(values.get(day), positive=True)
        if value is None or entry is None:
            return {"complete": False, "return": None, "mdd": None, "blockers": ["benchmark_exact_path_missing"]}
        path.append(value / entry)
    gross = path[-1] - 1
    period = settle_equal_weight_period(
        [{"status": "closed", "grossReturn": gross, "dailyGrossFactors": path}],
        1,
        buy_cost=ETF_BUY_COST,
        sell_cost=ETF_SELL_COST,
    )
    return {
        "complete": period["complete"],
        "return": period["return"],
        "mdd": max_drawdown_from_equity(daily_equity_curve([period])),
        "dailyEquityFactors": period["equityFactors"],
        "dailyGrossFactors": path,
        "comparisonFrom": dates[1],
        "comparisonTo": dates[-1],
        "comparisonTradingDays": len(dates) - 1,
        "costModel": "official-0050-total-return-single-round-trip-v1",
        "blockers": period["blockers"],
    }


def _failed_report(blocker: str, *, mode: str = "research_only") -> dict[str, Any]:
    risk = unconfigured_risk_policy()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": mode,
        "accountingComplete": False,
        "selectionReturn": None,
        "eligiblePoolReturn": None,
        "benchmarkReturn": None,
        "selectionMdd": None,
        "eligiblePoolMdd": None,
        "benchmarkMdd": None,
        "selectionCertified": False,
        "outcomeEvidenceAuthorityRegistered": False,
        "liveExecutionSpecRegistered": False,
        "eligiblePoolAccountingRegisteredForMeasurement": True,
        "performanceEligible": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "formalGateAttached": False,
        **risk,
        "blockers": sorted({
            blocker,
            "selection_evidence_authority_unregistered",
            "outcome_evidence_authority_unregistered",
            "live_execution_policy_decision_required",
            "daily_mdd_limit_unconfigured",
        }),
    }


def _measure(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != ROOT_KEYS or payload.get("schemaVersion") != SCHEMA_VERSION:
        return _failed_report("input_contract_invalid")
    selection = _public_selection(payload.get("selection"))
    dates = payload.get("dates")
    price_paths = payload.get("pricePaths")
    if selection is None or not isinstance(dates, list) or len(dates) < 3 \
            or not isinstance(price_paths, dict):
        return _failed_report("input_contract_invalid")
    normalized_dates = [_strict_date(item) for item in dates]
    if any(item is None for item in normalized_dates) or normalized_dates != sorted(set(normalized_dates)):
        return _failed_report("measurement_calendar_invalid")
    horizon = len(dates) - 2
    if horizon not in MEASUREMENT_HORIZONS:
        return _failed_report("measurement_horizon_not_preregistered")
    pool_codes = [row["code"] for row in selection["fullPool"]]
    if set(price_paths) != set(pool_codes):
        return _failed_report("price_path_population_mismatch")
    expected_path_dates = set(dates[1:])
    if any(
        not isinstance(path, dict) or not set(path) <= expected_path_dates
        for path in price_paths.values()
    ):
        return _failed_report("price_path_calendar_mismatch")
    evidence = _validated_evidence(payload.get("outcomeEvidence"), dates, pool_codes)
    passed = set(selection["qualityPassedCodes"])
    selected_outcomes: list[dict[str, Any]] = []
    for index in range(TARGET_SLOTS):
        if index >= len(selection["preview"]):
            selected_outcomes.append({"status": "cash_no_candidate", "pathLength": len(dates) - 1})
            continue
        code = selection["preview"][index]["code"]
        if code not in passed:
            selected_outcomes.append({"status": "cash_no_candidate", "pathLength": len(dates) - 1})
            continue
        selected_outcomes.append(_outcome(code, dates, price_paths[code], evidence))
    pool_outcomes = [_outcome(code, dates, price_paths[code], evidence) for code in pool_codes]
    if not pool_outcomes:
        pool_outcomes = [{"status": "cash_no_candidate", "pathLength": len(dates) - 1}]
    selection_period = settle_equal_weight_period(
        selected_outcomes, TARGET_SLOTS, buy_cost=STOCK_BUY_COST, sell_cost=STOCK_SELL_COST
    )
    # A legitimately empty signal-date pool is a scheduled all-cash comparator,
    # not a missing sample.  One synthetic cash slot represents 100% cash while
    # ``selectionIdentity.poolSize`` preserves the true zero-name denominator.
    pool_period = settle_equal_weight_period(
        pool_outcomes, len(pool_codes) or 1,
        buy_cost=STOCK_BUY_COST, sell_cost=STOCK_SELL_COST,
    )
    benchmark = _benchmark(payload.get("benchmarkTotalReturn"), dates)
    blockers = set(selection_period["blockers"]) | set(pool_period["blockers"]) \
        | set(benchmark["blockers"])
    if evidence is None:
        blockers.add("outcome_evidence_invalid_or_missing")
    if selection.get("selectionEvidenceSupplied") is not True:
        blockers.add("selection_evidence_shape_missing")
    if selection.get("cutoffTieDependent") is True:
        blockers.add("cutoff_tie_dependent")
    blockers.update({
        "selection_evidence_authority_unregistered",
        "outcome_evidence_authority_unregistered",
        "live_execution_policy_decision_required",
        "daily_mdd_limit_unconfigured",
    })
    complete = selection_period["complete"] and pool_period["complete"] and benchmark["complete"]
    selection_mdd = max_drawdown_from_equity(daily_equity_curve([selection_period]))
    pool_mdd = max_drawdown_from_equity(daily_equity_curve([pool_period]))
    risk = unconfigured_risk_policy()
    public_selection = {
        "selectionDigest": selection["selectionDigest"],
        "poolPopulationHash": digest(pool_codes),
        "selectedPopulationHash": digest(selection["qualityPassedCodes"]),
        "poolSize": len(pool_codes),
        "previewSlots": TARGET_SLOTS,
        "qualityPassedSlots": len(selection["qualityPassedCodes"]),
        "qualityRejectedOrEmptySlots": TARGET_SLOTS - len(selection["qualityPassedCodes"]),
    }
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "accountingPolicyVersion": ACCOUNTING_POLICY_VERSION,
        "mode": "research_only",
        "measurementHorizonTradingDays": horizon,
        "signalDate": dates[0],
        "comparisonFrom": dates[1],
        "comparisonTo": dates[-1],
        "comparisonTradingDays": len(dates) - 1,
        "costModel": {
            "buyFee": BUY_FEE,
            "sellFee": SELL_FEE,
            "stockSellTax": STOCK_SELL_TAX,
            "etfSellTax": ETF_SELL_TAX,
            "oneWaySlippageBps": SLIPPAGE_BPS,
        },
        "selectionIdentity": public_selection,
        "selectionAccounting": {key: value for key, value in selection_period.items() if key != "equityFactors"},
        "eligiblePoolAccounting": {key: value for key, value in pool_period.items() if key != "equityFactors"},
        "selectionDailyEquityFactors": selection_period["equityFactors"],
        "eligiblePoolDailyEquityFactors": pool_period["equityFactors"],
        "benchmark0050": benchmark,
        "accountingComplete": complete,
        "selectionReturn": selection_period["return"] if complete else None,
        "eligiblePoolReturn": pool_period["return"] if complete else None,
        "benchmarkReturn": benchmark["return"] if complete else None,
        "selectionExcessVersusPool": (
            selection_period["return"] - pool_period["return"] if complete else None
        ),
        "selectionExcessVersus0050": (
            selection_period["return"] - benchmark["return"] if complete else None
        ),
        "selectionMdd": selection_mdd if complete else None,
        "eligiblePoolMdd": pool_mdd if complete else None,
        "benchmarkMdd": benchmark["mdd"] if complete else None,
        "mddBasis": "daily_mark_to_market_including_costs",
        "selectionCertified": False,
        "outcomeEvidenceShapeComplete": evidence is not None,
        "outcomeEvidenceAuthorityRegistered": False,
        "liveExecutionSpecRegistered": False,
        "eligiblePoolAccountingRegisteredForMeasurement": True,
        "performanceEligible": False,
        "strategyValidated": False,
        "promotionEligible": False,
        "adviceEnabled": False,
        "formalGateAttached": False,
        **risk,
        "blockers": sorted(blockers),
    }
    digest_body = {key: value for key, value in report.items() if key != "measurementDigest"}
    report["measurementDigest"] = digest(digest_body)
    return report


def measure_cohort(payload: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Measure one cohort; disabled by default and fail closed on bad input."""
    if not enabled:
        return _failed_report("measurement_disabled", mode="disabled")
    try:
        if not isinstance(payload, dict) or not _bounded_json(payload):
            return _failed_report("input_contract_invalid")
        return _measure(payload)
    except Exception:
        return _failed_report("input_fail_closed")


def _compound(values: list[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1 + value
    return equity - 1


COHORT_REPORT_KEYS = frozenset({
    "schemaVersion", "policyVersion", "accountingPolicyVersion", "mode",
    "measurementHorizonTradingDays", "signalDate", "comparisonFrom", "comparisonTo",
    "comparisonTradingDays", "costModel", "selectionIdentity", "selectionAccounting",
    "eligiblePoolAccounting", "selectionDailyEquityFactors", "eligiblePoolDailyEquityFactors",
    "benchmark0050", "accountingComplete", "selectionReturn", "eligiblePoolReturn",
    "benchmarkReturn", "selectionExcessVersusPool", "selectionExcessVersus0050",
    "selectionMdd", "eligiblePoolMdd", "benchmarkMdd", "mddBasis", "selectionCertified",
    "outcomeEvidenceShapeComplete", "outcomeEvidenceAuthorityRegistered",
    "liveExecutionSpecRegistered", "eligiblePoolAccountingRegisteredForMeasurement",
    "performanceEligible", "strategyValidated", "promotionEligible", "adviceEnabled",
    "formalGateAttached", "riskPolicyVersion", "dailyMddLimitConfigured",
    "dailyMddGatePassed", "riskGateEligible", "riskBlockers", "blockers", "measurementDigest",
})
ACCOUNTING_KEYS = frozenset({
    "schemaVersion", "policyVersion", "complete", "return", "targetSlots", "selectedSlots",
    "filledSlots", "closedSlots", "unfilledEntrySlots", "noCandidateCashSlots",
    "unresolvedExitSlots", "costedRoundTrips", "cashWeight", "blockers",
})
BENCHMARK_KEYS = frozenset({
    "complete", "return", "mdd", "dailyEquityFactors", "dailyGrossFactors",
    "comparisonFrom", "comparisonTo", "comparisonTradingDays", "costModel", "blockers",
})
IDENTITY_KEYS = frozenset({
    "selectionDigest", "poolPopulationHash", "selectedPopulationHash", "poolSize",
    "previewSlots", "qualityPassedSlots", "qualityRejectedOrEmptySlots",
})


def _numeric_curve(value: Any, length: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != length:
        return None
    curve = [_finite(item, positive=True) for item in value]
    return None if any(item is None for item in curve) else [float(item) for item in curve]


def _accounting_valid(row: Any, curve: Any, *, target_slots: int, horizon: int) -> bool:
    if not isinstance(row, dict) or set(row) != ACCOUNTING_KEYS \
            or row.get("schemaVersion") != 1 \
            or row.get("policyVersion") != ACCOUNTING_POLICY_VERSION \
            or row.get("complete") is not True \
            or set(row.get("blockers", [])):
        return False
    integer_fields = (
        "targetSlots", "selectedSlots", "filledSlots", "closedSlots", "unfilledEntrySlots",
        "noCandidateCashSlots", "unresolvedExitSlots", "costedRoundTrips",
    )
    if any(isinstance(row.get(key), bool) or not isinstance(row.get(key), int) or row[key] < 0
           for key in integer_fields):
        return False
    if row["targetSlots"] != target_slots \
            or row["closedSlots"] + row["unresolvedExitSlots"] != row["filledSlots"] \
            or row["filledSlots"] + row["unfilledEntrySlots"] != row["selectedSlots"] \
            or row["selectedSlots"] + row["noCandidateCashSlots"] != row["targetSlots"] \
            or row["costedRoundTrips"] != row["closedSlots"] \
            or row["unresolvedExitSlots"] != 0:
        return False
    expected_cash = (row["unfilledEntrySlots"] + row["noCandidateCashSlots"]) / target_slots
    if _finite(row.get("cashWeight")) is None \
            or not math.isclose(float(row["cashWeight"]), expected_cash, abs_tol=1e-12):
        return False
    factors = _numeric_curve(curve, horizon + 1)
    result = _finite(row.get("return"))
    return factors is not None and result is not None \
        and math.isclose(factors[-1] - 1, result, abs_tol=1e-12)


def _cohort_valid(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) != COHORT_REPORT_KEYS:
        return False
    body = {key: value for key, value in item.items() if key != "measurementDigest"}
    if item.get("measurementDigest") != digest(body) \
            or item.get("schemaVersion") != SCHEMA_VERSION \
            or item.get("policyVersion") != POLICY_VERSION \
            or item.get("accountingPolicyVersion") != ACCOUNTING_POLICY_VERSION \
            or item.get("mode") != "research_only" \
            or item.get("accountingComplete") is not True \
            or item.get("mddBasis") != "daily_mark_to_market_including_costs" \
            or item.get("eligiblePoolAccountingRegisteredForMeasurement") is not True:
        return False
    blockers = item.get("blockers")
    mandatory_blockers = {
        "selection_evidence_authority_unregistered",
        "outcome_evidence_authority_unregistered",
        "live_execution_policy_decision_required",
        "daily_mdd_limit_unconfigured",
    }
    allowed_blockers = mandatory_blockers | {
        "cutoff_tie_dependent", "selection_evidence_shape_missing",
        "outcome_evidence_invalid_or_missing",
    }
    if not isinstance(blockers, list) or any(not isinstance(value, str) for value in blockers) \
            or not mandatory_blockers <= set(blockers) or not set(blockers) <= allowed_blockers:
        return False
    for key in (
        "selectionCertified", "outcomeEvidenceAuthorityRegistered", "liveExecutionSpecRegistered",
        "performanceEligible", "strategyValidated", "promotionEligible", "adviceEnabled",
        "formalGateAttached", "dailyMddLimitConfigured", "dailyMddGatePassed", "riskGateEligible",
    ):
        if item.get(key) is not False:
            return False
    if item.get("riskPolicyVersion") != unconfigured_risk_policy()["riskPolicyVersion"] \
            or item.get("riskBlockers") != ["daily_mdd_limit_unconfigured"]:
        return False
    horizon = item.get("measurementHorizonTradingDays")
    if isinstance(horizon, bool) or horizon not in MEASUREMENT_HORIZONS \
            or item.get("comparisonTradingDays") != horizon + 1:
        return False
    signal = _strict_date(item.get("signalDate"))
    start = _strict_date(item.get("comparisonFrom"))
    end = _strict_date(item.get("comparisonTo"))
    if signal is None or start is None or end is None or not signal < start < end:
        return False
    costs = item.get("costModel")
    if costs != {
        "buyFee": BUY_FEE, "sellFee": SELL_FEE, "stockSellTax": STOCK_SELL_TAX,
        "etfSellTax": ETF_SELL_TAX, "oneWaySlippageBps": SLIPPAGE_BPS,
    }:
        return False
    identity = item.get("selectionIdentity")
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS \
            or not all(_hex64(identity.get(key)) for key in (
                "selectionDigest", "poolPopulationHash", "selectedPopulationHash",
            )):
        return False
    if any(isinstance(identity.get(key), bool) or not isinstance(identity.get(key), int)
           or identity[key] < 0 for key in (
               "poolSize", "previewSlots", "qualityPassedSlots", "qualityRejectedOrEmptySlots",
           )) or identity["previewSlots"] != TARGET_SLOTS \
            or identity["qualityPassedSlots"] + identity["qualityRejectedOrEmptySlots"] != TARGET_SLOTS:
        return False
    pool_slots = identity["poolSize"] or 1
    selection_accounting = item.get("selectionAccounting")
    pool_accounting = item.get("eligiblePoolAccounting")
    if not _accounting_valid(
        selection_accounting, item.get("selectionDailyEquityFactors"),
        target_slots=TARGET_SLOTS, horizon=horizon,
    ) or not _accounting_valid(
        pool_accounting, item.get("eligiblePoolDailyEquityFactors"),
        target_slots=pool_slots, horizon=horizon,
    ):
        return False
    if selection_accounting["selectedSlots"] != identity["qualityPassedSlots"] \
            or selection_accounting["noCandidateCashSlots"] != identity["qualityRejectedOrEmptySlots"]:
        return False
    if identity["poolSize"] > 0:
        if pool_accounting["selectedSlots"] != identity["poolSize"] \
                or pool_accounting["noCandidateCashSlots"] != 0:
            return False
    elif pool_accounting["targetSlots"] != 1 or pool_accounting["selectedSlots"] != 0 \
            or pool_accounting["noCandidateCashSlots"] != 1:
        return False
    benchmark = item.get("benchmark0050")
    if not isinstance(benchmark, dict) or set(benchmark) != BENCHMARK_KEYS \
            or benchmark.get("complete") is not True \
            or benchmark.get("comparisonFrom") != start \
            or benchmark.get("comparisonTo") != end \
            or benchmark.get("comparisonTradingDays") != horizon + 1 \
            or benchmark.get("costModel") != "official-0050-total-return-single-round-trip-v1" \
            or benchmark.get("blockers") != []:
        return False
    benchmark_curve = _numeric_curve(benchmark.get("dailyEquityFactors"), horizon + 1)
    benchmark_gross = _numeric_curve(benchmark.get("dailyGrossFactors"), horizon + 1)
    selection_curve = item["selectionDailyEquityFactors"]
    pool_curve = item["eligiblePoolDailyEquityFactors"]
    returns = {
        "selectionReturn": selection_curve[-1] - 1,
        "eligiblePoolReturn": pool_curve[-1] - 1,
        "benchmarkReturn": benchmark_curve[-1] - 1 if benchmark_curve else None,
    }
    if benchmark_curve is None or benchmark_gross is None \
            or any(_finite(item.get(key)) is None or not math.isclose(item[key], value, abs_tol=1e-12)
                   for key, value in returns.items()):
        return False
    pool_excess = _finite(item.get("selectionExcessVersusPool"))
    benchmark_excess = _finite(item.get("selectionExcessVersus0050"))
    if pool_excess is None or benchmark_excess is None or not math.isclose(
        pool_excess, item["selectionReturn"] - item["eligiblePoolReturn"],
        abs_tol=1e-12,
    ) or not math.isclose(
        benchmark_excess, item["selectionReturn"] - item["benchmarkReturn"],
        abs_tol=1e-12,
    ):
        return False
    mdds = {
        "selectionMdd": max_drawdown_from_equity([1.0, *selection_curve]),
        "eligiblePoolMdd": max_drawdown_from_equity([1.0, *pool_curve]),
        "benchmarkMdd": max_drawdown_from_equity([1.0, *benchmark_curve]),
    }
    return _finite(benchmark.get("mdd")) is not None \
        and math.isclose(benchmark["mdd"], mdds["benchmarkMdd"], abs_tol=1e-12) \
        and all(_finite(item.get(key)) is not None and math.isclose(item[key], value, abs_tol=1e-12)
                for key, value in mdds.items())


def _summed_accounting(cohorts: list[dict[str, Any]], key: str) -> dict[str, Any]:
    rows = [item[key] for item in cohorts]
    integer_fields = (
        "targetSlots", "selectedSlots", "filledSlots", "closedSlots", "unfilledEntrySlots",
        "noCandidateCashSlots", "unresolvedExitSlots", "costedRoundTrips",
    )
    return {
        "schemaVersion": rows[0]["schemaVersion"] if rows else 1,
        "policyVersion": rows[0]["policyVersion"] if rows else ACCOUNTING_POLICY_VERSION,
        "complete": bool(rows) and all(row.get("complete") is True for row in rows),
        "scheduledPeriods": len(rows),
        "investedPeriods": sum(1 for row in rows if row.get("closedSlots", 0) > 0),
        **{field: sum(int(row.get(field, 0)) for row in rows) for field in integer_fields},
        "averageCashWeight": (
            sum(float(row.get("cashWeight", 0.0)) for row in rows) / len(rows) if rows else 1.0
        ),
        "blockers": sorted({blocker for row in rows for blocker in row.get("blockers", [])}),
    }


def _aggregate_measurements(cohorts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate non-overlapping/coincident scheduled cohorts without hiding gaps."""
    if not cohorts:
        return {
            "complete": False,
            "return": None,
            "eligiblePoolReturn": None,
            "mdd": None,
            "eligiblePoolMdd": None,
            "benchmarkReturn": None,
            "benchmarkScheduledReturns": [],
            "benchmarkMdd": None,
            "benchmarkCostedRoundTrips": 0,
            "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
            "executionAccounting": _summed_accounting([], "selectionAccounting"),
            "eligiblePoolAccounting": _summed_accounting([], "eligiblePoolAccounting"),
            "blockers": ["no_scheduled_cohorts"],
        }
    if any(item.get("accountingComplete") is True and not _cohort_valid(item) for item in cohorts):
        return {
            "complete": False,
            "return": None,
            "eligiblePoolReturn": None,
            "benchmarkReturn": None,
            "benchmarkScheduledReturns": [],
            "mdd": None,
            "eligiblePoolMdd": None,
            "benchmarkMdd": None,
            "benchmarkCostedRoundTrips": 0,
            "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
            "executionAccounting": _summed_accounting([], "selectionAccounting"),
            "eligiblePoolAccounting": _summed_accounting([], "eligiblePoolAccounting"),
            "comparisonFrom": None,
            "comparisonTo": None,
            "measurementHorizonTradingDays": None,
            "blockers": ["cohort_contract_or_digest_invalid"],
            "measurementDigests": [
                item.get("measurementDigest") for item in cohorts
                if isinstance(item, dict) and item.get("measurementDigest")
            ],
        }
    identities = [
        (item.get("signalDate"), item.get("comparisonFrom"), item.get("comparisonTo"))
        for item in cohorts
    ]
    horizons = {item.get("measurementHorizonTradingDays") for item in cohorts}
    sequential = all(
        cohorts[index - 1].get("comparisonTo") == item.get("comparisonFrom")
        for index, item in enumerate(cohorts[1:], start=1)
    )
    if len(set(identities)) != len(identities) or len(horizons) != 1 or not sequential:
        return {
            "complete": False,
            "return": None,
            "eligiblePoolReturn": None,
            "benchmarkReturn": None,
            "benchmarkScheduledReturns": [],
            "mdd": None,
            "eligiblePoolMdd": None,
            "benchmarkMdd": None,
            "benchmarkCostedRoundTrips": 0,
            "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
            "executionAccounting": _summed_accounting([], "selectionAccounting"),
            "eligiblePoolAccounting": _summed_accounting([], "eligiblePoolAccounting"),
            "comparisonFrom": None,
            "comparisonTo": None,
            "measurementHorizonTradingDays": None,
            "blockers": ["cohort_schedule_not_unique_and_contiguous"],
            "measurementDigests": [
                item.get("measurementDigest") for item in cohorts if item.get("measurementDigest")
            ],
        }
    if any("selectionAccounting" not in item or "eligiblePoolAccounting" not in item for item in cohorts):
        valid = [
            item for item in cohorts
            if "selectionAccounting" in item and "eligiblePoolAccounting" in item
        ]
        blockers = sorted({
            blocker for item in cohorts for blocker in item.get("blockers", [])
        } | {"actual_comprehensive_outcome_accounting_incomplete"})
        selection_accounting = _summed_accounting(valid, "selectionAccounting")
        pool_accounting = _summed_accounting(valid, "eligiblePoolAccounting")
        selection_accounting["scheduledPeriods"] = len(cohorts)
        pool_accounting["scheduledPeriods"] = len(cohorts)
        selection_accounting["complete"] = False
        pool_accounting["complete"] = False
        return {
            "complete": False,
            "return": None,
            "eligiblePoolReturn": None,
            "mdd": None,
            "eligiblePoolMdd": None,
            "benchmarkReturn": None,
            "benchmarkScheduledReturns": [],
            "benchmarkMdd": None,
            "benchmarkCostedRoundTrips": 0,
            "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
            "executionAccounting": selection_accounting,
            "eligiblePoolAccounting": pool_accounting,
            "comparisonFrom": None,
            "comparisonTo": None,
            "measurementHorizonTradingDays": None,
            "blockers": blockers,
            "measurementDigests": [
                item.get("measurementDigest") for item in cohorts if item.get("measurementDigest")
            ],
        }
    selection_accounting = _summed_accounting(cohorts, "selectionAccounting")
    pool_accounting = _summed_accounting(cohorts, "eligiblePoolAccounting")
    complete = all(item.get("accountingComplete") is True for item in cohorts)
    blockers = sorted({blocker for item in cohorts for blocker in item.get("blockers", [])})
    if not complete:
        blockers = sorted(set(blockers) | {"actual_comprehensive_outcome_accounting_incomplete"})
    selected_returns = [float(item["selectionReturn"]) for item in cohorts] if complete else []
    pool_returns = [float(item["eligiblePoolReturn"]) for item in cohorts] if complete else []
    selected_periods = [
        {
            "complete": True,
            "return": item["selectionReturn"],
            "equityFactors": item["selectionDailyEquityFactors"],
        }
        for item in cohorts
    ] if complete else []
    pool_periods = [
        {
            "complete": True,
            "return": item["eligiblePoolReturn"],
            "equityFactors": item["eligiblePoolDailyEquityFactors"],
        }
        for item in cohorts
    ] if complete else []
    benchmark_split = None
    benchmark_scheduled_returns: list[float] = []
    if complete:
        benchmark_gross: list[float] = []
        for index, item in enumerate(cohorts):
            path = item["benchmark0050"]["dailyGrossFactors"]
            factor = path[-1]
            if index == 0:
                factor *= 1 - ETF_BUY_COST
            if index == len(cohorts) - 1:
                factor *= 1 - ETF_SELL_COST
            benchmark_scheduled_returns.append(factor - 1)
            if not benchmark_gross:
                benchmark_gross.extend(path)
            else:
                prior = benchmark_gross[-1]
                benchmark_gross.extend(prior * factor for factor in path[1:])
        benchmark_split = settle_equal_weight_period(
            [{
                "status": "closed",
                "grossReturn": benchmark_gross[-1] - 1,
                "dailyGrossFactors": benchmark_gross,
            }],
            1,
            buy_cost=ETF_BUY_COST,
            sell_cost=ETF_SELL_COST,
        )
    return {
        "complete": complete,
        "return": _compound(selected_returns) if complete else None,
        "eligiblePoolReturn": _compound(pool_returns) if complete else None,
        "benchmarkReturn": benchmark_split["return"] if complete else None,
        "benchmarkScheduledReturns": benchmark_scheduled_returns,
        "mdd": max_drawdown_from_equity(daily_equity_curve(selected_periods)) if complete else None,
        "eligiblePoolMdd": max_drawdown_from_equity(daily_equity_curve(pool_periods)) if complete else None,
        "benchmarkMdd": max_drawdown_from_equity(daily_equity_curve([benchmark_split])) if complete else None,
        "benchmarkCostedRoundTrips": 1 if complete else 0,
        "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
        "executionAccounting": selection_accounting,
        "eligiblePoolAccounting": pool_accounting,
        "comparisonFrom": cohorts[0]["comparisonFrom"],
        "comparisonTo": cohorts[-1]["comparisonTo"],
        "measurementHorizonTradingDays": cohorts[0]["measurementHorizonTradingDays"],
        "blockers": blockers,
        "measurementDigests": [item["measurementDigest"] for item in cohorts],
    }


def _failed_aggregate(blocker: str) -> dict[str, Any]:
    return {
        "complete": False,
        "return": None,
        "eligiblePoolReturn": None,
        "benchmarkReturn": None,
        "benchmarkScheduledReturns": [],
        "mdd": None,
        "eligiblePoolMdd": None,
        "benchmarkMdd": None,
        "benchmarkCostedRoundTrips": 0,
        "benchmarkCostModel": "official-0050-total-return-single-split-round-trip-v1",
        "executionAccounting": _summed_accounting([], "selectionAccounting"),
        "eligiblePoolAccounting": _summed_accounting([], "eligiblePoolAccounting"),
        "comparisonFrom": None,
        "comparisonTo": None,
        "measurementHorizonTradingDays": None,
        "blockers": [blocker],
        "measurementDigests": [],
    }


def aggregate_measurements(cohorts: Any) -> dict[str, Any]:
    """Aggregate bounded cohort reports; malformed artifacts never escape."""
    try:
        if not isinstance(cohorts, list) or len(cohorts) > 10_000 \
                or any(not isinstance(item, dict) for item in cohorts) \
                or not _bounded_json(cohorts):
            return _failed_aggregate("cohort_contract_or_digest_invalid")
        return _aggregate_measurements(cohorts)
    except Exception:
        return _failed_aggregate("cohort_contract_or_digest_invalid")
