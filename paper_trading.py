"""Prospective, paper-only tracking for the production comprehensive selector.

This is an operational ledger, not another authority gate.  A scheduled run:

1. observes the newest official quote session for already-open cohorts;
2. records a new signal only every 20 captured sessions;
3. uses the next captured session close as the virtual entry; and
4. derives immutable 5/20/60-session outcomes from the append-only event log.

The ledger never enables advice, promotion, notification, or trading.  It also
never accepts a caller-supplied date, which makes historical backfilling through
the CLI impossible.  Git history is useful operational custody, but is not an
external immutable timestamp; the progress report says so explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import actual_comprehensive_selection as selection
from backtest import BUY_FEE, ETF_SELL_TAX, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX
from provenance import schema_hash, stable_hash
from quote_provenance import available_at as modelled_available_at
from scoring import ranking_volume


SCHEMA_VERSION = 1
POLICY_VERSION = "prospective-comprehensive-paper-trading-v1"
STYLE = "comprehensive"
TARGET_SLOTS = 3
SIGNAL_SPACING = 20
HORIZONS = (5, 20, 60)
OBSERVATION_LIMIT = max(HORIZONS) + 1
MARK_OFFSETS = frozenset({1, *(horizon + 1 for horizon in HORIZONS)})
BENCHMARK_CODE = "0050"
GENESIS = "0" * 64
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 20_000
MAX_POOL_ROWS = 2_000
MAX_NUMBER_ABS = 10**18
MAX_ACTIVATION_LAG_DAYS = 10
MAX_REPORT_LAG_DAYS = 10
MAX_NEW_RECEIPT_AGE_HOURS = 96
TAIPEI = timezone(timedelta(hours=8))
ENTRY_ORDER_CUTOFF = time(13, 0)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[0-9]{4}$")
ACTION_SOURCE = "FinMind authorized API"
ACTION_DATASET = "TaiwanStockDividendResult"
ACTION_ENDPOINT = "https://api.finmindtrade.com/api/v4/data"


class PaperTradingError(ValueError):
    """A fail-closed operational input or append conflict."""


def _reject_constant(value: str) -> None:
    raise PaperTradingError(f"non_finite_json:{value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PaperTradingError("duplicate_json_key")
        result[key] = value
    return result


def _read_json(path: Path, *, missing: Any = None) -> Any:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PaperTradingError("json_file_too_large")
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise PaperTradingError(f"missing_input:{path.name}")
    except PaperTradingError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise PaperTradingError(f"invalid_json:{path.name}") from error


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as error:
        raise PaperTradingError("non_canonical_value") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso_day(value: Any) -> str:
    if type(value) is not str or len(value) != 10:
        raise PaperTradingError("session_date_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise PaperTradingError("session_date_invalid") from error
    if parsed.isoformat() != value:
        raise PaperTradingError("session_date_noncanonical")
    return value


def _aware(value: Any) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise PaperTradingError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PaperTradingError("timestamp_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperTradingError("timestamp_not_aware")
    return parsed


def _now(value: datetime | None = None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if type(current) is not datetime or current.tzinfo is None or current.utcoffset() is None:
        raise PaperTradingError("current_time_not_aware")
    return current.astimezone(timezone.utc)


def _finite(value: Any, *, positive: bool = False) -> float | None:
    if type(value) is int:
        if abs(value) > MAX_NUMBER_ABS:
            return None
        result = float(value)
    elif type(value) is float:
        if not math.isfinite(value) or abs(value) > MAX_NUMBER_ABS:
            return None
        result = value
    else:
        return None
    if positive and result <= 0:
        return None
    return result


def _bounded_json(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> bool:
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if budget[0] > 500_000 or depth > 40:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return abs(value) <= MAX_NUMBER_ABS
    if type(value) is float:
        return math.isfinite(value) and abs(value) <= MAX_NUMBER_ABS
    if type(value) is str:
        try:
            length = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            return False
        budget[1] += length
        return length <= 4096 and budget[1] <= MAX_FILE_BYTES
    if type(value) is list:
        return all(_bounded_json(item, depth=depth + 1, budget=budget) for item in value)
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not _bounded_json(key, depth=depth + 1, budget=budget):
                return False
            if not _bounded_json(item, depth=depth + 1, budget=budget):
                return False
        return True
    return False


def _new_ledger() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "paperOnly": True,
        "adviceEnabled": False,
        "tradingEnabled": False,
        "events": [],
        "headHash": GENESIS,
    }


def _verify_ledger(value: Any) -> dict[str, Any]:
    expected = {
        "schemaVersion", "policyVersion", "paperOnly", "adviceEnabled",
        "tradingEnabled", "events", "headHash",
    }
    if type(value) is not dict or set(value) != expected or not _bounded_json(value):
        raise PaperTradingError("ledger_schema_invalid")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != SCHEMA_VERSION
        or value["policyVersion"] != POLICY_VERSION
        or value["paperOnly"] is not True
        or value["adviceEnabled"] is not False
        or value["tradingEnabled"] is not False
        or type(value["events"]) is not list
        or len(value["events"]) > MAX_EVENTS
    ):
        raise PaperTradingError("ledger_identity_invalid")
    previous = GENESIS
    for index, event in enumerate(value["events"], 1):
        if type(event) is not dict or set(event) != {
            "sequence", "eventType", "recordedAt", "previousHash", "payload", "eventHash"
        }:
            raise PaperTradingError("event_schema_invalid")
        if type(event["sequence"]) is not int or event["sequence"] != index:
            raise PaperTradingError("event_sequence_invalid")
        _aware(event["recordedAt"])
        if event["previousHash"] != previous:
            raise PaperTradingError("event_chain_invalid")
        if (
            type(event["eventType"]) is not str
            or event["eventType"] not in {"session_observation", "signal_decision"}
        ):
            raise PaperTradingError("event_type_invalid")
        material = {key: item for key, item in event.items() if key != "eventHash"}
        if event["eventHash"] != _digest(material):
            raise PaperTradingError("event_hash_invalid")
        previous = event["eventHash"]
    if value["headHash"] != previous:
        raise PaperTradingError("ledger_head_invalid")
    _replay(value)
    return value


def load_ledger(path: Path) -> dict[str, Any]:
    return _verify_ledger(_read_json(path, missing=_new_ledger()))


def _encoded_json(value: Any) -> bytes:
    if not _bounded_json(value):
        raise PaperTradingError("output_out_of_bounds")
    try:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        encoded = content.encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as error:
        raise PaperTradingError("output_not_serializable") from error
    if len(encoded) > MAX_FILE_BYTES:
        raise PaperTradingError("output_too_large")
    return encoded


def _stage_json(path: Path, encoded: bytes) -> Path:
    if path.exists() and not path.is_file():
        raise PaperTradingError(f"output_path_invalid:{path.name}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(encoded)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise PaperTradingError(f"output_stage_failed:{path.name}") from error
    return temporary


def _write_json(path: Path, value: Any) -> None:
    temporary = _stage_json(path, _encoded_json(value))
    try:
        os.replace(temporary, path)
    except OSError as error:
        raise PaperTradingError(f"output_commit_failed:{path.name}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_pair(
    first_path: Path, first_value: Any, second_path: Path, second_value: Any,
) -> None:
    """Commit the authoritative ledger and its derived report as one recoverable unit."""
    first_encoded = _encoded_json(first_value)
    second_encoded = _encoded_json(second_value)
    first_temporary: Path | None = None
    second_temporary: Path | None = None
    original_first: bytes | None = None
    first_existed = first_path.exists()
    try:
        if first_existed:
            if not first_path.is_file():
                raise PaperTradingError(f"output_path_invalid:{first_path.name}")
            original_first = first_path.read_bytes()
        first_temporary = _stage_json(first_path, first_encoded)
        second_temporary = _stage_json(second_path, second_encoded)
        os.replace(first_temporary, first_path)
        first_temporary = None
        try:
            os.replace(second_temporary, second_path)
            second_temporary = None
        except OSError as error:
            try:
                if first_existed:
                    rollback = first_path.with_name(f".{first_path.name}.{os.getpid()}.rollback")
                    rollback.write_bytes(original_first if original_first is not None else b"")
                    os.replace(rollback, first_path)
                else:
                    first_path.unlink(missing_ok=True)
            except OSError as rollback_error:
                raise PaperTradingError("output_rollback_failed") from rollback_error
            raise PaperTradingError(f"output_commit_failed:{second_path.name}") from error
    except PaperTradingError:
        raise
    except OSError as error:
        raise PaperTradingError("output_pair_commit_failed") from error
    finally:
        if first_temporary is not None:
            first_temporary.unlink(missing_ok=True)
        if second_temporary is not None:
            second_temporary.unlink(missing_ok=True)


def _append(ledger: dict[str, Any], event_type: str, payload: dict[str, Any], now: datetime) -> None:
    if len(ledger["events"]) >= MAX_EVENTS:
        raise PaperTradingError("event_limit_reached")
    event = {
        "sequence": len(ledger["events"]) + 1,
        "eventType": event_type,
        "recordedAt": now.isoformat(),
        "previousHash": ledger["headHash"],
        "payload": payload,
    }
    event["eventHash"] = _digest(event)
    ledger["events"].append(event)
    ledger["headHash"] = event["eventHash"]


def _text(value: Any, *, maximum: int = 256, empty: bool = False) -> bool:
    if type(value) is not str or (not empty and not value):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeEncodeError:
        return False


def _decision_material_valid(
    material: Any,
    observation: dict[str, Any],
    prior_strategy_hashes: dict[str, str] | None,
) -> dict[str, str]:
    expected = {
        "schemaVersion", "policyVersion", "signalSession", "sourceAvailableAt", "sourceRetrievedAt",
        "sourceContentHash", "manifestDigest", "fundamentalsDigest",
        "quoteProvenanceDigest", "fundamentalsProvenanceDigest",
        "selectionPolicyVersion", "strategySourceHashes",
        "signalSpacingCapturedSessions", "entryConvention",
        "horizonsCapturedSessions", "targetSlots", "topSlots", "rankedPool",
        "rankedPoolDigest", "paperOnly", "adviceEnabled", "tradingEnabled",
    }
    if type(material) is not dict or set(material) != expected:
        raise PaperTradingError("decision_material_schema_invalid")
    signal = _iso_day(material["signalSession"])
    if (
        type(material["schemaVersion"]) is not int
        or material["schemaVersion"] != SCHEMA_VERSION
        or material["policyVersion"] != POLICY_VERSION
        or material["sourceAvailableAt"] != observation["sourceAvailableAt"]
        or material["sourceRetrievedAt"] != observation["sourceRetrievedAt"]
        or material["sourceContentHash"] != observation["sourceContentHash"]
        or signal != observation["sessionDate"]
        or not _text(material["selectionPolicyVersion"], maximum=128)
        or type(material["signalSpacingCapturedSessions"]) is not int
        or material["signalSpacingCapturedSessions"] != SIGNAL_SPACING
        or material["entryConvention"] != "next_captured_session_close_continuity_unverified"
        or material["horizonsCapturedSessions"] != list(HORIZONS)
        or type(material["targetSlots"]) is not int
        or material["targetSlots"] != TARGET_SLOTS
        or material["paperOnly"] is not True
        or material["adviceEnabled"] is not False
        or material["tradingEnabled"] is not False
    ):
        raise PaperTradingError("decision_material_identity_invalid")
    _aware(material["sourceAvailableAt"])
    for key in (
        "sourceContentHash", "manifestDigest", "fundamentalsDigest",
        "quoteProvenanceDigest", "fundamentalsProvenanceDigest",
    ):
        if type(material[key]) is not str or HEX64.fullmatch(material[key]) is None:
            raise PaperTradingError("decision_material_digest_invalid")
    strategy_hashes = material["strategySourceHashes"]
    required_hashes = {
        "scoring.py", "actual_comprehensive_selection.py", "candidate_manifest.py",
        "data_contract.py", "execution_accounting.py", "backtest.py", "paper_trading.py",
    }
    if (
        type(strategy_hashes) is not dict
        or set(strategy_hashes) != required_hashes
        or any(type(value) is not str or HEX64.fullmatch(value) is None for value in strategy_hashes.values())
        or (prior_strategy_hashes is not None and strategy_hashes != prior_strategy_hashes)
    ):
        raise PaperTradingError("strategy_identity_drift")
    pool = material["rankedPool"]
    if type(pool) is not list or not pool or len(pool) > MAX_POOL_ROWS:
        raise PaperTradingError("ranked_pool_invalid")
    pool_codes: list[str] = []
    for row in pool:
        if type(row) is not dict or set(row) != {"code", "score", "coverage", "signalClose", "volume"}:
            raise PaperTradingError("ranked_pool_row_invalid")
        code = row["code"]
        volume = _finite(row["volume"])
        if (
            type(code) is not str
            or CODE.fullmatch(code) is None
            or code in pool_codes
            or _finite(row["score"]) is None
            or _finite(row["coverage"]) is None
            or _finite(row["signalClose"], positive=True) is None
            or volume is None
            or volume < 0
            or (type(row["volume"]) is float and row["volume"] == 0 and math.copysign(1.0, row["volume"]) < 0)
        ):
            raise PaperTradingError("ranked_pool_value_invalid")
        pool_codes.append(code)
    if material["rankedPoolDigest"] != _digest(pool):
        raise PaperTradingError("ranked_pool_digest_invalid")
    slots = material["topSlots"]
    if type(slots) is not list or len(slots) != TARGET_SLOTS:
        raise PaperTradingError("top_slots_invalid")
    fundamental_keys = {*selection.REQUIRED_FINAL_METRICS, "financialHistoryYears"}
    for index, slot in enumerate(slots, 1):
        if type(slot) is not dict or set(slot) != {
            "slot", "code", "name", "rank", "score", "coverage", "signalClose",
            "manifestQualityPassed", "qualityBlockers", "fundamentals",
        }:
            raise PaperTradingError("top_slot_schema_invalid")
        blockers = slot["qualityBlockers"]
        fundamentals = slot["fundamentals"]
        if (
            type(slot["slot"]) is not int
            or slot["slot"] != index
            or type(slot["manifestQualityPassed"]) is not bool
            or type(blockers) is not list
            or any(not _text(item) for item in blockers)
            or slot["manifestQualityPassed"] is not (not blockers)
            or type(fundamentals) is not dict
            or set(fundamentals) != fundamental_keys
            or any(value is not None and (type(value) is not float or _finite(value) is None) for value in fundamentals.values())
        ):
            raise PaperTradingError("top_slot_value_invalid")
        code = slot["code"]
        if code is None:
            if any(slot[key] is not None for key in ("name", "rank", "score", "coverage", "signalClose")):
                raise PaperTradingError("empty_slot_not_cash")
            continue
        if type(code) is not str or CODE.fullmatch(code) is None or index > len(pool):
            raise PaperTradingError("top_slot_code_invalid")
        row = pool[index - 1]
        if (
            not _text(slot["name"])
            or type(slot["rank"]) is not int
            or slot["rank"] != index
            or _finite(slot["score"]) is None
            or _finite(slot["coverage"]) is None
            or _finite(slot["signalClose"], positive=True) is None
            or code != row["code"]
            or slot["score"] != row["score"]
            or slot["coverage"] != row["coverage"]
            or slot["signalClose"] != row["signalClose"]
        ):
            raise PaperTradingError("top_slot_pool_mismatch")
    return dict(strategy_hashes)


def _mark_valid(mark: Any, decision: dict[str, Any], session: str, expected_offset: int) -> None:
    if type(mark) is not dict or set(mark) != {
        "decisionKey", "sessionOffset", "entryTimingEligible", "selectedCloses",
        "benchmarkClose", "corporateActionCoverage", "corporateActionEvents", "poolCloses",
    }:
        raise PaperTradingError("cohort_mark_schema_invalid")
    if (
        mark["decisionKey"] != decision["decisionKey"]
        or type(mark["sessionOffset"]) is not int
        or mark["sessionOffset"] != expected_offset
        or (expected_offset == 1 and type(mark["entryTimingEligible"]) is not bool)
        or (expected_offset != 1 and mark["entryTimingEligible"] is not None)
    ):
        raise PaperTradingError("cohort_mark_identity_invalid")
    material = decision["material"]
    selected = [slot["code"] for slot in material["topSlots"] if slot["code"] is not None]
    closes = mark["selectedCloses"]
    coverage = mark["corporateActionCoverage"]
    if type(closes) is not dict or set(closes) != set(selected):
        raise PaperTradingError("selected_closes_invalid")
    if any(value is not None and (type(value) is not float or _finite(value, positive=True) is None) for value in closes.values()):
        raise PaperTradingError("selected_close_value_invalid")
    if type(coverage) is not dict or set(coverage) != set(selected) or any(type(value) is not bool for value in coverage.values()):
        raise PaperTradingError("action_coverage_invalid")
    if mark["benchmarkClose"] is not None and (
        type(mark["benchmarkClose"]) is not float or _finite(mark["benchmarkClose"], positive=True) is None
    ):
        raise PaperTradingError("benchmark_close_invalid")
    events = mark["corporateActionEvents"]
    if type(events) is not list:
        raise PaperTradingError("action_events_invalid")
    seen_codes: set[str] = set()
    for event in events:
        if type(event) is not dict or set(event) != {"code", "date", "beforeClose", "referencePrice"}:
            raise PaperTradingError("action_event_schema_invalid")
        code = event["code"]
        if type(code) is not str:
            raise PaperTradingError("action_event_value_invalid")
        if (
            code not in selected
            or code in seen_codes
            or event["date"] != session
            or type(event["beforeClose"]) is not float
            or type(event["referencePrice"]) is not float
            or _finite(event["beforeClose"], positive=True) is None
            or _finite(event["referencePrice"], positive=True) is None
            or coverage[code] is not True
        ):
            raise PaperTradingError("action_event_value_invalid")
        seen_codes.add(code)
    if events != sorted(events, key=lambda item: item["code"]):
        raise PaperTradingError("action_event_order_invalid")
    pool_closes = mark["poolCloses"]
    if expected_offset in MARK_OFFSETS:
        pool_codes = {row["code"] for row in material["rankedPool"]}
        if type(pool_closes) is not dict or set(pool_closes) != pool_codes:
            raise PaperTradingError("pool_closes_invalid")
        if any(value is not None and (type(value) is not float or _finite(value, positive=True) is None) for value in pool_closes.values()):
            raise PaperTradingError("pool_close_value_invalid")
    elif pool_closes is not None:
        raise PaperTradingError("unexpected_pool_closes")


def _replay(ledger: dict[str, Any]) -> dict[str, Any]:
    decisions: dict[str, dict[str, Any]] = {}
    decision_by_session: dict[str, dict[str, Any]] = {}
    observations: dict[str, dict[str, Any]] = {}
    last_session: str | None = None
    last_signal: str | None = None
    last_recorded: datetime | None = None
    strategy_hashes: dict[str, str] | None = None
    for event in ledger["events"]:
        recorded = _aware(event["recordedAt"]).astimezone(timezone.utc)
        if last_recorded is not None and recorded < last_recorded:
            raise PaperTradingError("event_time_order_invalid")
        last_recorded = recorded
        payload = event["payload"]
        if event["eventType"] == "session_observation":
            if type(payload) is not dict or set(payload) != {
                "sessionDate", "sourceAvailableAt", "sourceRetrievedAt", "sourceContentHash",
                "actionReceiptHash", "actionReceiptUpdatedAt",
                "observationDigest", "cohortMarks",
            }:
                raise PaperTradingError("observation_schema_invalid")
            session = _iso_day(payload["sessionDate"])
            available = _aware(payload["sourceAvailableAt"]).astimezone(timezone.utc)
            retrieved = _aware(payload["sourceRetrievedAt"]).astimezone(timezone.utc)
            if available > retrieved or retrieved > recorded:
                raise PaperTradingError("observation_before_source_available")
            if type(payload["sourceContentHash"]) is not str or HEX64.fullmatch(payload["sourceContentHash"]) is None:
                raise PaperTradingError("observation_source_hash_invalid")
            action_updated = _aware(payload["actionReceiptUpdatedAt"]).astimezone(timezone.utc)
            if (
                type(payload["actionReceiptHash"]) is not str
                or HEX64.fullmatch(payload["actionReceiptHash"]) is None
                or action_updated < available
                or action_updated > recorded
            ):
                raise PaperTradingError("observation_action_receipt_invalid")
            material = {key: item for key, item in payload.items() if key != "observationDigest"}
            if payload["observationDigest"] != _digest(material):
                raise PaperTradingError("observation_digest_invalid")
            if last_session is not None and session <= last_session:
                raise PaperTradingError("observation_order_invalid")
            if session in observations:
                raise PaperTradingError("duplicate_observation")
            marks = payload["cohortMarks"]
            if type(marks) is not list:
                raise PaperTradingError("cohort_marks_invalid")
            expected_marks: list[tuple[str, dict[str, Any], int]] = []
            for decision_key, decision in sorted(decisions.items()):
                signal = decision["material"]["signalSession"]
                if session <= signal:
                    continue
                offset = 1 + sum(1 for observed in observations if observed > signal)
                if offset <= OBSERVATION_LIMIT:
                    expected_marks.append((decision_key, decision, offset))
            if [mark.get("decisionKey") if type(mark) is dict else None for mark in marks] != [item[0] for item in expected_marks]:
                raise PaperTradingError("cohort_mark_set_invalid")
            for mark, (_, decision, offset) in zip(marks, expected_marks):
                _mark_valid(mark, decision, session, offset)
            observations[session] = payload
            last_session = session
        else:
            if type(payload) is not dict or set(payload) != {
                "decisionKey", "decisionAt", "decisionDigest", "material",
            }:
                raise PaperTradingError("decision_schema_invalid")
            material = payload["material"]
            if type(material) is not dict or payload["decisionDigest"] != _digest(material):
                raise PaperTradingError("decision_digest_invalid")
            signal = _iso_day(material.get("signalSession"))
            observation = observations.get(signal)
            if observation is None:
                raise PaperTradingError("decision_without_observation")
            strategy_hashes = _decision_material_valid(material, observation, strategy_hashes)
            if last_signal is not None and signal <= last_signal:
                raise PaperTradingError("decision_order_invalid")
            if payload["decisionKey"] != f"{POLICY_VERSION}:{STYLE}:{signal}":
                raise PaperTradingError("decision_key_invalid")
            if payload["decisionKey"] in decisions or signal in decision_by_session:
                raise PaperTradingError("duplicate_decision")
            decision_at = _aware(payload["decisionAt"]).astimezone(timezone.utc)
            if decision_at != recorded or decision_at < _aware(material["sourceAvailableAt"]).astimezone(timezone.utc):
                raise PaperTradingError("decision_time_invalid")
            if last_signal is None:
                if len(observations) != 1:
                    raise PaperTradingError("first_decision_schedule_invalid")
            else:
                captured = [day for day in observations if last_signal < day <= signal]
                if len(captured) != SIGNAL_SPACING:
                    raise PaperTradingError("decision_schedule_invalid")
            decisions[payload["decisionKey"]] = payload
            decision_by_session[signal] = payload
            last_signal = signal
    return {
        "decisions": decisions,
        "decisionBySession": decision_by_session,
        "observations": observations,
    }


def _source_info(quotes: dict[str, Any], now: datetime) -> dict[str, str]:
    provenance_root = quotes.get("provenance")
    provenance = provenance_root.get("quote") if type(provenance_root) is dict else None
    fundamental_provenance = provenance_root.get("fundamentals") if type(provenance_root) is dict else None
    quote_rows = quotes.get("quotes")
    fundamental_rows = quotes.get("fundamentals")
    if (
        type(provenance) is not dict
        or type(fundamental_provenance) is not dict
        or type(quote_rows) is not dict
        or type(fundamental_rows) is not dict
        or not _bounded_json(quote_rows)
        or not _bounded_json(fundamental_rows)
    ):
        raise PaperTradingError("quote_provenance_missing")
    session = _iso_day(provenance.get("effectiveDate"))
    available = _aware(provenance.get("availableAt"))
    retrieved = _aware(provenance.get("retrievedAt"))
    if (
        available.astimezone(timezone.utc) > retrieved.astimezone(timezone.utc)
        or retrieved.astimezone(timezone.utc) > now
        or provenance.get("ingestedAt") != provenance.get("retrievedAt")
    ):
        raise PaperTradingError("quote_source_not_yet_available")
    now_day = now.astimezone(TAIPEI).date()
    session_day = date.fromisoformat(session)
    if session_day > now_day:
        raise PaperTradingError("quote_session_not_current_for_activation")
    content_hash = provenance.get("contentHash")
    if (
        type(content_hash) is not str
        or HEX64.fullmatch(content_hash) is None
        or content_hash != stable_hash(quote_rows)
        or provenance.get("schemaHash") != schema_hash(quote_rows)
        or provenance.get("scopeHash") != stable_hash(sorted(quote_rows))
        or provenance.get("schemaVersion") != 1
        or type(provenance.get("schemaVersion")) is not int
        or provenance.get("provider") != "TWSE/TPEx OpenAPI"
        or provenance.get("source") != "TWSE/TPEx OpenAPI"
        or provenance.get("sourceDataset") != "STOCK_DAY_ALL + tpex_mainboard_daily_close_quotes"
        or provenance.get("dataset") != "STOCK_DAY_ALL + tpex_mainboard_daily_close_quotes"
        or provenance.get("endpoint") != "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        or provenance.get("availableAt") != modelled_available_at(session)
        or provenance.get("status") != "success"
        or provenance.get("quality") != "verified"
        or provenance.get("conflictStatus") != "no_conflict"
        or provenance.get("visibility") != "public_source"
        or provenance.get("overlappingCodes") != []
    ):
        raise PaperTradingError("quote_content_hash_invalid")
    fundamental_retrieved = _aware(fundamental_provenance.get("retrievedAt")).astimezone(timezone.utc)
    fundamental_effective = fundamental_provenance.get("effectiveDate")
    fundamental_available = fundamental_provenance.get("availableAt")
    if fundamental_effective is not None:
        _iso_day(fundamental_effective)
    if fundamental_available is not None:
        _iso_day(fundamental_available)
    if (
        (fundamental_available is not None and date.fromisoformat(fundamental_available) > fundamental_retrieved.date())
        or fundamental_retrieved > now
        or fundamental_provenance.get("ingestedAt") != fundamental_provenance.get("retrievedAt")
        or type(fundamental_provenance.get("schemaVersion")) is not int
        or fundamental_provenance.get("schemaVersion") != 1
        or fundamental_provenance.get("provider") != ACTION_SOURCE
        or fundamental_provenance.get("source") != ACTION_SOURCE
        or fundamental_provenance.get("sourceDataset") != "TaiwanStockFinancialStatements,TaiwanStockBalanceSheet,TaiwanStockInfo"
        or fundamental_provenance.get("dataset") != "TaiwanStockFinancialStatements,TaiwanStockBalanceSheet,TaiwanStockInfo"
        or fundamental_provenance.get("endpoint") != ACTION_ENDPOINT
        or type(fundamental_provenance.get("scopeHash")) is not str
        or HEX64.fullmatch(fundamental_provenance.get("scopeHash", "")) is None
        or type(fundamental_provenance.get("contentHash")) is not str
        or HEX64.fullmatch(fundamental_provenance.get("contentHash", "")) is None
        or type(fundamental_provenance.get("schemaHash")) is not str
        or HEX64.fullmatch(fundamental_provenance.get("schemaHash", "")) is None
        or fundamental_provenance.get("snapshotContentHash") != stable_hash(fundamental_rows)
        or fundamental_provenance.get("snapshotSchemaHash") != schema_hash(fundamental_rows)
        or type(fundamental_provenance.get("snapshotCodeCount")) is not int
        or fundamental_provenance.get("snapshotCodeCount") != len(fundamental_rows)
        or fundamental_provenance.get("status") != "success"
        or type(fundamental_provenance.get("quality")) is not str
        or fundamental_provenance.get("quality") not in {"verified", "conflict_unresolved", "as_of_missing", "effective_date_missing"}
        or type(fundamental_provenance.get("conflictStatus")) is not str
        or fundamental_provenance.get("conflictStatus") not in {"no_conflict", "conflict_unresolved"}
        or fundamental_provenance.get("visibility") != "private_cache"
        or type(fundamental_provenance.get("sourceDisagreements")) is not dict
    ):
        raise PaperTradingError("fundamentals_content_hash_invalid")
    return {
        "sessionDate": session,
        "sourceAvailableAt": provenance["availableAt"],
        "sourceContentHash": content_hash,
        "sourceRetrievedAt": provenance["retrievedAt"],
        "quoteProvenanceDigest": _digest(provenance),
        "fundamentalsProvenanceDigest": _digest(fundamental_provenance),
    }


def _action_info(
    actions: Any, source: dict[str, str], now: datetime,
) -> dict[str, Any]:
    if type(actions) is not dict or not _bounded_json(actions):
        raise PaperTradingError("action_receipt_invalid")
    required = {
        "scope", "period", "queried_codes", "successful_codes", "events", "failures",
        "source", "dataset", "effectiveDate", "availableAt", "updatedAt", "ingestedAt",
        "conflictStatus", "provenance",
    }
    if not required.issubset(actions):
        raise PaperTradingError("action_receipt_missing_fields")
    period = actions["period"]
    if type(period) is not dict or set(period) != {"start", "end"}:
        raise PaperTradingError("action_period_invalid")
    start = _iso_day(period["start"])
    end = _iso_day(period["end"])
    session = source["sessionDate"]
    if not start <= session <= end or date.fromisoformat(end) > now.astimezone(TAIPEI).date():
        raise PaperTradingError("action_period_does_not_cover_session")
    updated = _aware(actions["updatedAt"]).astimezone(timezone.utc)
    quote_available = _aware(source["sourceAvailableAt"]).astimezone(timezone.utc)
    action_available = _aware(actions["availableAt"]).astimezone(timezone.utc)
    if (
        action_available > updated
        or updated < quote_available
        or updated > now
        or actions["ingestedAt"] != actions["updatedAt"]
    ):
        raise PaperTradingError("action_receipt_time_invalid")
    codes = actions["queried_codes"]
    failures = actions["failures"]
    events = actions["events"]
    if (
        type(codes) is not list
        or any(type(code) is not str or CODE.fullmatch(code) is None for code in codes)
    ):
        raise PaperTradingError("action_coverage_shape_invalid")
    if (
        codes != sorted(codes)
        or len(codes) != len(set(codes))
        or type(failures) is not dict
        or any(code not in codes or not _text(reason) for code, reason in failures.items())
        or type(events) is not list
    ):
        raise PaperTradingError("action_coverage_shape_invalid")
    if (
        type(actions["successful_codes"]) is not int
        or actions["successful_codes"] != len(codes) - len(failures)
        or actions["conflictStatus"] != ("no_conflict" if not failures else "conflict_unresolved")
    ):
        raise PaperTradingError("action_coverage_count_invalid")
    seen: set[tuple[str, str]] = set()
    for event in events:
        if type(event) is not dict or set(event) != {
            "date", "code", "market", "before_close", "reference_price",
            "after_price", "kind", "source",
        }:
            raise PaperTradingError("action_source_event_schema_invalid")
        day = _iso_day(event["date"])
        code = event["code"]
        if type(code) is not str or CODE.fullmatch(code) is None:
            raise PaperTradingError("action_source_event_invalid")
        pair = (day, code)
        if (
            code not in codes
            or code in failures
            or pair in seen
            or not start <= day <= end
            or event["market"] != "candidate_pool"
            or event["source"] != "FinMind TaiwanStockDividendResult"
            or _finite(event["before_close"], positive=True) is None
            or _finite(event["reference_price"], positive=True) is None
            or (event["after_price"] is not None and _finite(event["after_price"], positive=True) is None)
            or not _text(event["kind"], maximum=4096, empty=True)
        ):
            raise PaperTradingError("action_source_event_invalid")
        seen.add(pair)
    if events != sorted(events, key=lambda row: (row["date"], row["code"])):
        raise PaperTradingError("action_source_event_order_invalid")
    effective = max((event["date"] for event in events), default=end)
    provenance = actions["provenance"]
    expected_conflict = "no_conflict" if not failures else "conflict_unresolved"
    if (
        actions["scope"] != "active candidate pool only; not full-market total-return coverage"
        or actions["source"] != ACTION_SOURCE
        or actions["dataset"] != ACTION_DATASET
        or actions["effectiveDate"] != effective
        or actions["availableAt"] != modelled_available_at(effective)
        or type(provenance) is not dict
        or type(provenance.get("schemaVersion")) is not int
        or provenance.get("schemaVersion") != 1
        or provenance.get("provider") != ACTION_SOURCE
        or provenance.get("source") != ACTION_SOURCE
        or provenance.get("sourceDataset") != ACTION_DATASET
        or provenance.get("dataset") != ACTION_DATASET
        or provenance.get("endpoint") != ACTION_ENDPOINT
        or provenance.get("scopeHash") != stable_hash({"codes": codes, "start": start, "end": end})
        or provenance.get("observationTime") is not None
        or provenance.get("effectiveDate") != effective
        or provenance.get("availableAt") != actions["availableAt"]
        or provenance.get("retrievedAt") != actions["updatedAt"]
        or provenance.get("ingestedAt") != actions["ingestedAt"]
        or provenance.get("timezone") != "UTC"
        or provenance.get("contentHash") != stable_hash(events)
        or provenance.get("schemaHash") != schema_hash(events)
        or provenance.get("status") != "success"
        or provenance.get("quality") != ("verified" if not failures else "conflict_unresolved")
        or provenance.get("conflictStatus") != expected_conflict
        or provenance.get("visibility") != "private_cache"
    ):
        raise PaperTradingError("action_provenance_invalid")
    receipt_material = {
        key: actions[key]
        for key in (
            "period", "queried_codes", "successful_codes", "events", "failures",
            "source", "dataset", "effectiveDate", "availableAt", "updatedAt",
            "ingestedAt", "conflictStatus", "provenance",
        )
    }
    return {
        "queried": set(codes),
        "failures": failures,
        "events": events,
        "receiptHash": _digest(receipt_material),
        "receiptUpdatedAt": actions["updatedAt"],
    }


def _safe_name(value: Any, fallback: str) -> str:
    return selection.display_name(value, fallback)


def _price(quotes: dict[str, Any], code: str) -> float | None:
    row = quotes.get("quotes", {}).get(code)
    return _finite(row.get("price"), positive=True) if type(row) is dict else None


def _metric_snapshot(fundamentals: dict[str, Any]) -> dict[str, float | None]:
    return {
        name: _finite(fundamentals.get(name))
        for name in (*selection.REQUIRED_FINAL_METRICS, "financialHistoryYears")
    }


def _strategy_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    names = (
        "scoring.py", "actual_comprehensive_selection.py", "candidate_manifest.py",
        "data_contract.py", "execution_accounting.py", "backtest.py", "paper_trading.py",
    )
    return {name: _file_digest(root / name) for name in names}


def _selection_material(
    manifest: dict[str, Any], quotes: dict[str, Any], source: dict[str, str], now: datetime,
) -> dict[str, Any]:
    if (
        type(manifest) is not dict
        or type(manifest.get("schemaVersion")) is not int
        or manifest.get("schemaVersion") != 1
        or manifest.get("reportMode") != STYLE
        or manifest.get("phase") != "final"
    ):
        raise PaperTradingError("final_manifest_required")
    report_day = _iso_day(manifest.get("reportDate"))
    signal_day = date.fromisoformat(source["sessionDate"])
    report_date = date.fromisoformat(report_day)
    if (
        report_date < signal_day
        or (report_date - signal_day).days > MAX_REPORT_LAG_DAYS
        or report_date > now.astimezone(TAIPEI).date()
    ):
        raise PaperTradingError("manifest_report_date_invalid")
    quote_rows = quotes.get("quotes")
    fundamentals = quotes.get("fundamentals")
    if type(quote_rows) is not dict or type(fundamentals) is not dict:
        raise PaperTradingError("quote_snapshot_invalid")
    if any(type(row) is not dict for row in quote_rows.values()) or any(
        type(row) is not dict for row in fundamentals.values()
    ):
        raise PaperTradingError("quote_snapshot_row_invalid")
    pool = selection.rank_pool(quote_rows, fundamentals)
    if len(pool) > MAX_POOL_ROWS:
        raise PaperTradingError("eligible_pool_too_large")
    pool_projection = [
        {
            "code": str(item[2]),
            "score": item[0],
            "coverage": item[1],
            "signalClose": _finite(item[3].get("price"), positive=True),
            "volume": ranking_volume(item[3]),
        }
        for item in pool
    ]
    preview = manifest.get("previewCandidates")
    if type(preview) is not list or any(type(item) is not dict for item in preview):
        raise PaperTradingError("manifest_preview_invalid")
    preview = preview[:TARGET_SLOTS]
    expected_codes = [row["code"] for row in pool_projection[:TARGET_SLOTS]]
    actual_codes = [str(item.get("code", "")) for item in preview]
    if actual_codes != expected_codes or manifest.get("candidateOrder") != actual_codes:
        raise PaperTradingError("manifest_selection_drift")
    top_slots = []
    for rank, item in enumerate(preview, 1):
        code = str(item.get("code", ""))
        if CODE.fullmatch(code) is None:
            raise PaperTradingError("candidate_code_invalid")
        quality = item.get("quality")
        blockers = quality.get("blockers") if type(quality) is dict else None
        if (
            type(quality) is not dict
            or type(blockers) is not list
            or any(not _text(blocker) for blocker in blockers)
            or quality.get("passed") is not (not blockers)
        ):
            raise PaperTradingError("candidate_quality_invalid")
        quote = quote_rows.get(code, {})
        fund = fundamentals.get(code, {})
        pool_row = pool_projection[rank - 1]
        if (
            item.get("style") != STYLE
            or type(item.get("rank")) is not int
            or item.get("rank") != rank
            or _finite(item.get("score")) is None
            or item.get("score") != pool_row["score"]
            or _finite(item.get("coverage")) is None
            or item.get("coverage") != pool_row["coverage"]
            or _finite(item.get("entryPrice"), positive=True) is None
            or item.get("entryPrice") != pool_row["signalClose"]
        ):
            raise PaperTradingError("manifest_candidate_projection_invalid")
        top_slots.append({
            "slot": rank,
            "code": code,
            "name": _safe_name(item.get("name"), code),
            "rank": item.get("rank"),
            "score": item.get("score"),
            "coverage": item.get("coverage"),
            "signalClose": _finite(quote.get("price"), positive=True) if type(quote) is dict else None,
            "manifestQualityPassed": quality["passed"],
            "qualityBlockers": blockers,
            "fundamentals": _metric_snapshot(fund if type(fund) is dict else {}),
        })
    while len(top_slots) < TARGET_SLOTS:
        top_slots.append({
            "slot": len(top_slots) + 1,
            "code": None,
            "name": None,
            "rank": None,
            "score": None,
            "coverage": None,
            "signalClose": None,
            "manifestQualityPassed": False,
            "qualityBlockers": ["no_candidate_for_slot"],
            "fundamentals": _metric_snapshot({}),
        })
    material = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "signalSession": source["sessionDate"],
        "sourceAvailableAt": source["sourceAvailableAt"],
        "sourceRetrievedAt": source["sourceRetrievedAt"],
        "sourceContentHash": source["sourceContentHash"],
        "manifestDigest": _digest(manifest),
        "fundamentalsDigest": _digest(fundamentals),
        "quoteProvenanceDigest": source["quoteProvenanceDigest"],
        "fundamentalsProvenanceDigest": source["fundamentalsProvenanceDigest"],
        "selectionPolicyVersion": selection.POLICY_VERSION,
        "strategySourceHashes": _strategy_hashes(),
        "signalSpacingCapturedSessions": SIGNAL_SPACING,
        "entryConvention": "next_captured_session_close_continuity_unverified",
        "horizonsCapturedSessions": list(HORIZONS),
        "targetSlots": TARGET_SLOTS,
        "topSlots": top_slots,
        "rankedPool": pool_projection,
        "rankedPoolDigest": _digest(pool_projection),
        "paperOnly": True,
        "adviceEnabled": False,
        "tradingEnabled": False,
    }
    if not _bounded_json(material):
        raise PaperTradingError("decision_material_out_of_bounds")
    return material


def _observation_material(
    replay: dict[str, Any], quotes: dict[str, Any], actions: dict[str, Any],
    source: dict[str, str], now: datetime,
) -> dict[str, Any]:
    session = source["sessionDate"]
    prior_sessions = sorted(replay["observations"])
    if prior_sessions and session <= prior_sessions[-1]:
        raise PaperTradingError("observation_not_forward")
    action = _action_info(actions, source, now)
    queried = action["queried"]
    failures = action["failures"]
    events = action["events"]
    marks: list[dict[str, Any]] = []
    for decision_key, decision in sorted(replay["decisions"].items()):
        material = decision["material"]
        signal = material["signalSession"]
        if session <= signal:
            continue
        offset = 1 + sum(1 for observed in prior_sessions if observed > signal)
        if offset > OBSERVATION_LIMIT:
            continue
        selected_codes = [slot["code"] for slot in material["topSlots"] if slot["code"]]
        closes = {code: _price(quotes, code) for code in selected_codes}
        coverage = {
            code: code in queried and code not in failures
            for code in selected_codes
        }
        day_events = []
        for event in events:
            if type(event) is not dict:
                continue
            code = str(event.get("code", ""))
            if code not in selected_codes or event.get("date") != session:
                continue
            before = _finite(event.get("before_close"), positive=True)
            reference = _finite(event.get("reference_price"), positive=True)
            if before is None or reference is None:
                coverage[code] = False
                continue
            day_events.append({
                "code": code,
                "date": session,
                "beforeClose": before,
                "referencePrice": reference,
            })
        decision_at = _aware(decision["decisionAt"])
        entry_cutoff = datetime.combine(date.fromisoformat(session), ENTRY_ORDER_CUTOFF, TAIPEI)
        mark: dict[str, Any] = {
            "decisionKey": decision_key,
            "sessionOffset": offset,
            "entryTimingEligible": decision_at < entry_cutoff if offset == 1 else None,
            "selectedCloses": closes,
            "benchmarkClose": _price(quotes, BENCHMARK_CODE),
            "corporateActionCoverage": coverage,
            "corporateActionEvents": sorted(day_events, key=lambda item: item["code"]),
            "poolCloses": None,
        }
        if offset in MARK_OFFSETS:
            mark["poolCloses"] = {
                row["code"]: _price(quotes, row["code"])
                for row in material["rankedPool"]
            }
        marks.append(mark)
    material = {
        "sessionDate": session,
        "sourceAvailableAt": source["sourceAvailableAt"],
        "sourceRetrievedAt": source["sourceRetrievedAt"],
        "sourceContentHash": source["sourceContentHash"],
        "actionReceiptHash": action["receiptHash"],
        "actionReceiptUpdatedAt": action["receiptUpdatedAt"],
        "cohortMarks": marks,
    }
    return {**material, "observationDigest": _digest(material)}


def _decision_due(replay: dict[str, Any], session: str) -> bool:
    decisions = replay["decisionBySession"]
    if session in decisions:
        return True
    if not decisions:
        return True
    last_signal = max(decisions)
    captured = [day for day in replay["observations"] if last_signal < day <= session]
    if len(captured) > SIGNAL_SPACING:
        raise PaperTradingError("signal_schedule_gap")
    return len(captured) == SIGNAL_SPACING


def _existing_payload(replay: dict[str, Any], event_type: str, session: str) -> dict[str, Any] | None:
    if event_type == "session_observation":
        return replay["observations"].get(session)
    return replay["decisionBySession"].get(session)


def action_universe(
    manifest_path: Path, ledger_path: Path, output_path: Path,
) -> dict[str, Any]:
    """Write the exact candidate/open-cohort universe for the action refresher."""
    ledger = load_ledger(ledger_path)
    replay = _replay(ledger)
    manifest = _read_json(manifest_path)
    if type(manifest) is not dict or type(manifest.get("previewCandidates")) is not list:
        raise PaperTradingError("manifest_preview_invalid")
    codes = {
        str(item.get("code", ""))
        for item in manifest.get("previewCandidates", [])
        if type(item) is dict and CODE.fullmatch(str(item.get("code", "")))
    }
    for decision in replay["decisions"].values():
        signal = decision["material"]["signalSession"]
        observed = sum(1 for day in replay["observations"] if day > signal)
        if observed <= OBSERVATION_LIMIT:
            codes.update(
                slot["code"] for slot in decision["material"]["topSlots"]
                if slot["code"] is not None
            )
    payload = {
        "schemaVersion": 1,
        "paperOnly": True,
        "previewCandidates": [{"code": code} for code in sorted(codes)],
    }
    _write_json(output_path, payload)
    return payload


def advance(
    manifest_path: Path,
    quotes_path: Path,
    actions_path: Path,
    ledger_path: Path,
    progress_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advance exactly one latest source session; never accepts a historical date."""
    current = _now(now)
    manifest = _read_json(manifest_path)
    quotes = _read_json(quotes_path)
    actions = _read_json(actions_path, missing={})
    if type(quotes) is not dict:
        raise PaperTradingError("quote_snapshot_invalid")
    ledger = load_ledger(ledger_path)
    replay = _replay(ledger)
    source = _source_info(quotes, current)
    session = source["sessionDate"]
    changed = False

    existing_observation = _existing_payload(replay, "session_observation", session)
    if existing_observation is None and (
        current.astimezone(TAIPEI).date() - date.fromisoformat(session)
    ).days > MAX_ACTIVATION_LAG_DAYS:
        raise PaperTradingError("quote_session_not_current_for_activation")
    if existing_observation is None and (
        current - _aware(source["sourceRetrievedAt"]).astimezone(timezone.utc)
    ) > timedelta(hours=MAX_NEW_RECEIPT_AGE_HOURS):
        raise PaperTradingError("quote_receipt_too_old_for_first_seen_append")
    if existing_observation is not None and (
        existing_observation["sourceAvailableAt"] != source["sourceAvailableAt"]
        or existing_observation["sourceContentHash"] != source["sourceContentHash"]
    ):
        raise PaperTradingError("same_session_observation_conflict")
    observation = _observation_material(replay, quotes, actions, source, current) if existing_observation is None else None
    if existing_observation is None:
        _append(ledger, "session_observation", observation, current)
        changed = True
        replay = _replay(ledger)

    if _decision_due(replay, session):
        existing_decision = _existing_payload(replay, "signal_decision", session)
        if existing_decision is None:
            material = _selection_material(manifest, quotes, source, current)
            key = f"{POLICY_VERSION}:{STYLE}:{session}"
            payload = {
                "decisionKey": key,
                "decisionAt": current.isoformat(),
                "decisionDigest": _digest(material),
                "material": material,
            }
            _append(ledger, "signal_decision", payload, current)
            changed = True

    _verify_ledger(ledger)
    progress = build_progress(ledger, generated_at=current)
    if changed:
        _write_json_pair(ledger_path, ledger, progress_path, progress)
    else:
        _write_json(progress_path, progress)
    return progress


def _factor_for(mark: dict[str, Any], code: str) -> float | None:
    if mark["corporateActionCoverage"].get(code) is not True:
        return None
    product = 1.0
    for event in mark["corporateActionEvents"]:
        if event["code"] != code:
            continue
        ratio = event["beforeClose"] / event["referencePrice"]
        if not math.isfinite(ratio) or ratio <= 0:
            return None
        product *= ratio
        if not math.isfinite(product) or product <= 0:
            return None
    return product


def _round_trip(entry: float, exit_: float, *, etf: bool = False, factor: float = 1.0) -> float | None:
    if not all(_finite(value, positive=True) is not None for value in (entry, exit_, factor)):
        return None
    gross_factor = exit_ / entry * factor
    if not math.isfinite(gross_factor) or gross_factor <= 0:
        return None
    buy_cost = BUY_FEE + SLIPPAGE_BPS / 10_000
    sell_tax = ETF_SELL_TAX if etf else STOCK_SELL_TAX
    sell_cost = SELL_FEE + sell_tax + SLIPPAGE_BPS / 10_000
    result = gross_factor * (1 - buy_cost) * (1 - sell_cost) - 1
    return result if math.isfinite(result) and result > -1 else None


def _marks_by_decision(replay: dict[str, Any]) -> dict[str, dict[int, dict[str, Any]]]:
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for observation in replay["observations"].values():
        for mark in observation["cohortMarks"]:
            result.setdefault(mark["decisionKey"], {})[mark["sessionOffset"]] = mark
    return result


def _portfolio_outcome(
    decision: dict[str, Any], marks: dict[int, dict[str, Any]], horizon: int,
    *, qualified_only: bool,
) -> dict[str, Any]:
    entry = marks.get(1)
    exit_mark = marks.get(horizon + 1)
    if entry is None or exit_mark is None:
        return {"status": "pending"}
    if entry.get("entryTimingEligible") is not True:
        return {"status": "censored", "reason": "decision_missed_entry_cutoff"}
    returns: list[float] = []
    total_returns: list[float] = []
    total_complete = True
    for slot in decision["material"]["topSlots"]:
        code = slot["code"]
        if code is None or (qualified_only and slot["manifestQualityPassed"] is not True):
            returns.append(0.0)
            total_returns.append(0.0)
            continue
        entry_price = entry["selectedCloses"].get(code)
        if entry_price is None:
            returns.append(0.0)
            total_returns.append(0.0)
            continue
        exit_price = exit_mark["selectedCloses"].get(code)
        price_return = _round_trip(entry_price, exit_price)
        if price_return is None:
            return {"status": "censored", "reason": f"unresolved_exit:{code}"}
        returns.append(price_return)
        factor = 1.0
        for offset in range(2, horizon + 2):
            mark = marks.get(offset)
            if mark is None:
                total_complete = False
                break
            daily = _factor_for(mark, code)
            if daily is None:
                total_complete = False
                break
            factor *= daily
            if not math.isfinite(factor) or factor <= 0:
                total_complete = False
                break
        adjusted = _round_trip(entry_price, exit_price, factor=factor) if total_complete else None
        if adjusted is None:
            total_complete = False
            total_returns.append(price_return)
        else:
            total_returns.append(adjusted)
    while len(returns) < TARGET_SLOTS:
        returns.append(0.0)
        total_returns.append(0.0)
    portfolio = sum(returns) / TARGET_SLOTS
    total_portfolio = sum(total_returns) / TARGET_SLOTS if total_complete else None
    benchmark = _round_trip(entry.get("benchmarkClose"), exit_mark.get("benchmarkClose"), etf=True)
    if benchmark is None:
        return {"status": "censored", "reason": "benchmark_unresolved"}
    result = {
        "status": "complete",
        "priceOnlyDiagnostic": True,
        "performanceEvidenceQualified": False,
        "priceNetReturnPct": round(portfolio * 100, 6),
        "benchmarkPriceNetReturnPct": round(benchmark * 100, 6),
        "priceExcessVs0050Pct": round((portfolio - benchmark) * 100, 6),
        "totalReturnCoverageComplete": total_complete,
        "totalReturnNetPct": round(total_portfolio * 100, 6) if total_portfolio is not None else None,
    }
    return result


def _pool_outcome(decision: dict[str, Any], marks: dict[int, dict[str, Any]], horizon: int) -> dict[str, Any]:
    entry = marks.get(1)
    exit_mark = marks.get(horizon + 1)
    if entry is None or exit_mark is None:
        return {"status": "pending", "priceOnlyDiagnostic": True, "performanceEvidenceQualified": False}
    if entry.get("entryTimingEligible") is not True:
        return {
            "status": "censored", "reason": "decision_missed_entry_cutoff",
            "priceOnlyDiagnostic": True, "performanceEvidenceQualified": False,
        }
    entry_prices = entry.get("poolCloses")
    exit_prices = exit_mark.get("poolCloses")
    if type(entry_prices) is not dict or type(exit_prices) is not dict:
        return {
            "status": "censored", "reason": "eligible_pool_marks_unresolved",
            "priceOnlyDiagnostic": True, "performanceEvidenceQualified": False,
        }
    returns = []
    for row in decision["material"]["rankedPool"]:
        code = row["code"]
        start = entry_prices.get(code)
        if start is None:
            returns.append(0.0)
            continue
        result = _round_trip(start, exit_prices.get(code))
        if result is None:
            return {
                "status": "censored", "reason": f"eligible_pool_exit_unresolved:{code}",
                "priceOnlyDiagnostic": True, "performanceEvidenceQualified": False,
            }
        returns.append(result)
    result = sum(returns) / len(returns) if returns else 0.0
    return {
        "status": "complete", "priceOnlyDiagnostic": True,
        "performanceEvidenceQualified": False,
        "priceNetReturnPct": round(result * 100, 6),
    }


def _audit_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    counts = {
        status: sum(1 for row in rows if row.get("status") == status)
        for status in ("complete", "censored", "pending")
    }
    values_valid = all(
        row.get("status") != "complete" or _finite(row.get(field)) is not None
        for row in rows
    )
    complete_set = bool(rows) and counts["complete"] == len(rows) and values_valid
    return {
        "scheduledCohorts": len(rows),
        "completeCohorts": counts["complete"],
        "censoredCohorts": counts["censored"],
        "pendingCohorts": counts["pending"],
        "completeSet": complete_set,
        "meanPct": None,
        "intervalPct": None,
        "aggregateStatisticsSuppressed": True,
        "performanceEvidenceQualified": False,
        "blockers": [
            "official_session_continuity_not_authenticated",
            "registered_total_return_comparators_unavailable",
            "confirmatory_inventory_not_sealed",
        ],
    }


def build_progress(ledger: dict[str, Any], *, generated_at: datetime | None = None) -> dict[str, Any]:
    ledger = _verify_ledger(ledger)
    replay = _replay(ledger)
    marks = _marks_by_decision(replay)
    decisions = list(replay["decisions"].values())
    horizon_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    cohort_rows = []
    for decision in decisions:
        key = decision["decisionKey"]
        outcomes = {}
        for horizon in HORIZONS:
            diagnostic = _portfolio_outcome(decision, marks.get(key, {}), horizon, qualified_only=False)
            qualified = _portfolio_outcome(decision, marks.get(key, {}), horizon, qualified_only=True)
            pool = _pool_outcome(decision, marks.get(key, {}), horizon)
            diagnostic_pool = {"status": diagnostic.get("status")}
            qualified_pool = {"status": qualified.get("status")}
            if diagnostic.get("status") == "complete" and pool.get("status") == "complete":
                diagnostic["priceExcessVsEligiblePoolPct"] = round(
                    diagnostic["priceNetReturnPct"] - pool["priceNetReturnPct"], 6
                )
                diagnostic_pool["status"] = "complete"
                diagnostic_pool["priceExcessVsEligiblePoolPct"] = diagnostic["priceExcessVsEligiblePoolPct"]
            elif diagnostic.get("status") == "complete":
                diagnostic_pool = {"status": pool.get("status", "censored"), "reason": pool.get("reason")}
            if qualified.get("status") == "complete" and pool.get("status") == "complete":
                qualified["priceExcessVsEligiblePoolPct"] = round(
                    qualified["priceNetReturnPct"] - pool["priceNetReturnPct"], 6
                )
                qualified_pool["status"] = "complete"
                qualified_pool["priceExcessVsEligiblePoolPct"] = qualified["priceExcessVsEligiblePoolPct"]
            elif qualified.get("status") == "complete":
                qualified_pool = {"status": pool.get("status", "censored"), "reason": pool.get("reason")}
            outcomes[str(horizon)] = {
                "top3Diagnostic": diagnostic,
                "manifestQualifiedShadow": qualified,
                "eligiblePoolDiagnostic": pool,
            }
            bucket = horizon_rows.setdefault(str(horizon), {
                "diagnostic": [], "qualified": [], "diagnosticPool": [], "qualifiedPool": [],
            })
            bucket["diagnostic"].append(diagnostic)
            bucket["qualified"].append(qualified)
            bucket["diagnosticPool"].append(diagnostic_pool)
            bucket["qualifiedPool"].append(qualified_pool)
        cohort_rows.append({
            "decisionKey": key,
            "signalSession": decision["material"]["signalSession"],
            "selectedCodes": [slot["code"] for slot in decision["material"]["topSlots"] if slot["code"]],
            "manifestQualifiedCodes": [
                slot["code"] for slot in decision["material"]["topSlots"]
                if slot["code"] and slot["manifestQualityPassed"] is True
            ],
            "outcomes": outcomes,
        })
    summaries = {}
    for horizon in HORIZONS:
        rows = horizon_rows.get(str(horizon), {
            "diagnostic": [], "qualified": [], "diagnosticPool": [], "qualifiedPool": [],
        })
        summaries[str(horizon)] = {
            "top3DiagnosticVs0050": _audit_summary(rows["diagnostic"], "priceExcessVs0050Pct"),
            "manifestQualifiedShadowVs0050": _audit_summary(rows["qualified"], "priceExcessVs0050Pct"),
            "top3DiagnosticVsEligiblePool": _audit_summary(
                rows["diagnosticPool"], "priceExcessVsEligiblePoolPct"
            ),
            "manifestQualifiedShadowVsEligiblePool": _audit_summary(
                rows["qualifiedPool"], "priceExcessVsEligiblePoolPct"
            ),
        }
    current = _now(generated_at)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "generatedAt": current.isoformat(),
        "mode": "prospective_paper_shadow",
        "paperOnly": True,
        "adviceEnabled": False,
        "tradingEnabled": False,
        "ledgerHeadHash": ledger["headHash"],
        "eventCount": len(ledger["events"]),
        "capturedSessionCount": len(replay["observations"]),
        "cohortCount": len(decisions),
        "capturedSessionContinuityAuthenticated": False,
        "performanceEvidenceQualified": False,
        "aggregateStatisticsSuppressed": True,
        "horizons": summaries,
        "cohorts": cohort_rows,
        "validationStatus": "collecting_unqualified_observations" if decisions else "not_started",
        "formalValidationPassed": False,
        "limitations": [
            "captured_session_continuity_not_externally_authenticated",
            "0050_is_price_return_not_registered_total_return",
            "git_history_is_not_external_immutable_custody",
            "aggregate_performance_is_suppressed_until_continuity_and_total_return_evidence_are_registered",
            "paper_results_cannot_enable_advice_or_trading",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prospective paper-only strategy tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    universe = subparsers.add_parser("action-universe")
    universe.add_argument("--manifest", type=Path, required=True)
    universe.add_argument("--ledger", type=Path, required=True)
    universe.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("advance")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--quotes", type=Path, required=True)
    run.add_argument("--actions", type=Path, required=True)
    run.add_argument("--ledger", type=Path, required=True)
    run.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "action-universe":
        result = action_universe(args.manifest, args.ledger, args.output)
        print(json.dumps({"paperOnly": True, "codes": len(result["previewCandidates"])}, sort_keys=True))
    else:
        result = advance(args.manifest, args.quotes, args.actions, args.ledger, args.progress)
        print(json.dumps({
            "paperOnly": True,
            "capturedSessions": result["capturedSessionCount"],
            "cohorts": result["cohortCount"],
            "formalValidationPassed": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
