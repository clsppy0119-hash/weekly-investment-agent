"""Private, default-off CAS store for forward population metadata events.

This module persists only the already-sanitized event contract from
``forward_population_observation``.  It does not fetch data, read a clock,
connect to a database, or confer source/PIT authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import forward_population_observation as observation

SCHEMA_VERSION = "forward-population-ledger-store-v1"
POLICY_VERSION = "private-local-cas-append-only-v1"
EVENT_PREFIX = "event-"
EVENT_SUFFIX = ".json"
LOCK_NAME = ".append.lock"
MAX_EVENT_FILES = 4096
MAX_TOTAL_LEDGER_BYTES = 64 * 1024 * 1024


def _report(status: str, *, count: int = 0, previous: str = "", current: str = "") -> dict[str, Any]:
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "status": status,
        "eventCount": count,
        "previousLedgerHash": previous,
        "currentLedgerHash": current,
        "appendPublished": status == "appended",
        "checkpointAuthorityRegistered": False,
        "appendOnlyCertified": False,
        "forwardEvidenceAdmitted": False,
        "historicalEligible": False,
        "pitCoverageCertified": False,
        "promotionEligible": False,
        "adviceEnabled": False,
    }
    value["receiptDigest"] = observation.digest(value)
    return value


def _safe_root(root: Any, allowed_root: Any) -> Path | None:
    if not isinstance(root, (str, Path)) or not isinstance(allowed_root, (str, Path)):
        return None
    try:
        raw_allowed = Path(allowed_root).absolute()
        raw_target = Path(root).absolute()
        for raw in (raw_allowed, raw_target):
            cursor = Path(raw.anchor)
            for part in raw.parts[1:]:
                cursor /= part
                if not cursor.exists():
                    continue
                is_reparse = cursor.is_symlink()
                if hasattr(cursor, "is_junction"):
                    is_reparse = is_reparse or cursor.is_junction()
                if os.name == "nt":
                    is_reparse = is_reparse or bool(
                        cursor.stat(follow_symlinks=False).st_file_attributes & 0x400
                    )
                if is_reparse:
                    return None
        allowed = raw_allowed.resolve(strict=True)
        target = raw_target.resolve(strict=False)
        if target == allowed or allowed not in target.parents:
            return None
        relative = target.relative_to(allowed)
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            return None
        cursor = allowed
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                return None
        return target
    except (OSError, RuntimeError, ValueError):
        return None


def _event_name(sequence: int) -> str:
    return f"{EVENT_PREFIX}{sequence:08d}{EVENT_SUFFIX}"


def _read_events(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not root.exists():
        return [], None
    if not root.is_dir() or root.is_symlink():
        return [], "storage_unavailable"
    names: list[str] = []
    total_bytes = 0
    for item in root.iterdir():
        if len(names) >= MAX_EVENT_FILES + 2:
            return [], "invalid"
        names.append(item.name)
        if item.is_file() and not item.is_symlink():
            total_bytes += item.stat().st_size
            if total_bytes > MAX_TOTAL_LEDGER_BYTES:
                return [], "invalid"
    names.sort()
    allowed_names = {LOCK_NAME}
    event_names = [name for name in names if name.startswith(EVENT_PREFIX) and name.endswith(EVENT_SUFFIX)]
    if len(event_names) > MAX_EVENT_FILES:
        return [], "invalid"
    if any(name not in allowed_names and name not in event_names for name in names):
        return [], "invalid"
    events: list[dict[str, Any]] = []
    for index, name in enumerate(event_names, start=1):
        if name != _event_name(index):
            return [], "invalid"
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > observation.MAX_CANONICAL_BYTES:
            return [], "invalid"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return [], "invalid"
        events.append(value)
    if events:
        root_value = {
            "schemaVersion": observation.SCHEMA_VERSION,
            "policyVersion": observation.POLICY_VERSION,
            "observerContractHash": observation.OBSERVER_CONTRACT_HASH,
            "populationPolicyHash": observation.PINNED_POPULATION_POLICY_HASH,
            "sourceAdmissionPolicyHash": observation.PINNED_SOURCE_ADMISSION_POLICY_HASH,
            "ledgerEvents": events,
            "ledgerHash": observation.ledger_hash(events),
        }
        if not observation.evaluate(root_value)["ledgerStructurallyValid"]:
            return [], "invalid"
    return events, None


def inspect(root: Any, *, allowed_root: Any, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return _report("disabled")
    target = _safe_root(root, allowed_root)
    if target is None:
        return _report("storage_unavailable")
    try:
        events, error = _read_events(target)
        current = observation.ledger_hash(events)
        return _report(error or "valid", count=len(events), current=current)
    except Exception:
        return _report("storage_unavailable")


def append(
    root: Any, expected_ledger_hash: Any, candidate: Any, *,
    allowed_root: Any, enabled: bool = False,
) -> dict[str, Any]:
    """Atomically append one event when the durable head matches the caller's CAS token."""
    if not enabled:
        return _report("disabled")
    target = _safe_root(root, allowed_root)
    if target is None or not isinstance(expected_ledger_hash, str):
        return _report("storage_unavailable")
    lock = target / LOCK_NAME
    try:
        target.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            return _report("storage_unavailable")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return _report("storage_unavailable")
        try:
            os.write(descriptor, b"locked\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            events, error = _read_events(target)
            previous = observation.ledger_hash(events)
            if error:
                return _report(error, count=len(events), current=previous)
            if previous != expected_ledger_hash:
                return _report("cas_mismatch", count=len(events), previous=previous, current=previous)
            updated, status = observation.append_candidate(events, candidate)
            if status == "duplicate_noop":
                return _report(status, count=len(events), previous=previous, current=previous)
            if status not in {"appended"}:
                return _report(status, count=len(events), previous=previous, current=previous)
            if len(events) >= MAX_EVENT_FILES:
                return _report("invalid", count=len(events), previous=previous, current=previous)
            canonical = observation.canonical(updated[-1]) + "\n"
            final = target / _event_name(len(updated))
            temporary = target / f".{final.name}.tmp"
            if final.exists() or temporary.exists():
                return _report("conflict", count=len(events), previous=previous, current=previous)
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                output.write(canonical)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, final)
            # Directory fsync is supported on POSIX but Windows rejects opening
            # a directory as a file descriptor.  The event file itself was
            # flushed before the atomic publish in either case.
            try:
                directory_fd = os.open(target, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            current = observation.ledger_hash(updated)
            return _report("appended", count=len(updated), previous=previous, current=current)
        finally:
            try:
                lock.unlink()
            except OSError:
                pass
    except Exception:
        return _report("storage_unavailable")
