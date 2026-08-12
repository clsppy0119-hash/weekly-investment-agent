"""Default-off E1C-A sandbox ledger for replayable decision/outcome events.

The sandbox is deliberately outside every formal recommendation path.  It uses
exclusive create-new files and publishes the sequence anchor last, so a crash
can leave harmless unreferenced blobs but never a committed anchor that points
at a blob this writer did not verify first.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

import decision_outcome_manifest as manifest_contract


SCHEMA_VERSION = 1
POLICY_VERSION = "decision-outcome-sandbox-ledger-v1"
GENESIS = "0" * 64
HEX = re.compile(r"^[0-9a-f]{64}$")
SEQUENCE_FILE = re.compile(r"^([0-9]{20})\.json$")
WRITER_CONTRACT_HASH = hashlib.sha256(POLICY_VERSION.encode("ascii")).hexdigest()
ANCHOR_KEYS = {
    "schemaVersion", "policyVersion", "scopeId", "sequence",
    "previousAnchorHash", "manifestDigest", "eventSetDigest",
    "expectedEventCount", "expectedDecisionCount", "expectedOutcomeCount",
    "expectedLegacyCount", "decisionDateCount", "writerContractHash",
    "diagnosticOnly", "researchOnly", "promotionEligible",
    "completenessExternallyAnchored", "anchorHash",
}
OUTPUT_KEYS = {
    "schemaVersion", "mode", "diagnosticOnly", "sandboxChainVerified",
    "completenessExternallyAnchored", "promotionEligible", "status",
    "anchorHash", "sequence", "eventCount", "decisionDateCount",
    "blockers", "limitations",
}


class SandboxError(ValueError):
    """Bounded internal error; its code contains no caller-controlled text."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SandboxError("non_canonical_value") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _blocked(*codes: str) -> dict[str, Any]:
    result = {
        "schemaVersion": SCHEMA_VERSION, "mode": "research_only",
        "diagnosticOnly": True, "sandboxChainVerified": False,
        "completenessExternallyAnchored": False, "promotionEligible": False,
        "status": "blocked", "anchorHash": None, "sequence": None,
        "eventCount": 0, "decisionDateCount": 0,
        "blockers": sorted(set(codes)),
        "limitations": ["sandbox_trust_boundary_only", "no_external_anchor",
                        "not_investment_or_promotion_evidence"],
    }
    assert set(result) == OUTPUT_KEYS
    return result


def _success(anchor: dict[str, Any], status_name: str) -> dict[str, Any]:
    result = {
        "schemaVersion": SCHEMA_VERSION, "mode": "research_only",
        "diagnosticOnly": True, "sandboxChainVerified": True,
        "completenessExternallyAnchored": False, "promotionEligible": False,
        "status": status_name, "anchorHash": anchor["anchorHash"],
        "sequence": anchor["sequence"],
        "eventCount": anchor["expectedEventCount"],
        "decisionDateCount": anchor["decisionDateCount"], "blockers": [],
        "limitations": ["sandbox_trust_boundary_only", "no_external_anchor",
                        "not_investment_or_promotion_evidence"],
    }
    assert set(result) == OUTPUT_KEYS
    return result


def _absolute_root(root: Any) -> Path:
    if not isinstance(root, (str, os.PathLike)):
        raise SandboxError("sandbox_root_invalid")
    value = Path(root)
    if not value.is_absolute():
        raise SandboxError("sandbox_root_not_absolute")
    return value


def _no_symlink(path: Path) -> None:
    current = path
    checked: list[Path] = []
    while True:
        checked.append(current)
        if current.parent == current:
            break
        current = current.parent
    for item in reversed(checked):
        try:
            mode = os.lstat(item).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or (not stat.S_ISDIR(mode) and item == path):
            raise SandboxError("sandbox_path_unsafe")


def _safe_dir(root: Path, *parts: str) -> Path:
    if any(not part or part in {".", ".."} or "/" in part or "\\" in part
           for part in parts):
        raise SandboxError("sandbox_path_unsafe")
    target = root.joinpath(*parts)
    _no_symlink(root)
    target.mkdir(parents=True, exist_ok=True)
    _no_symlink(target)
    try:
        if os.path.commonpath((str(root.resolve()), str(target.resolve()))) != str(root.resolve()):
            raise SandboxError("sandbox_path_escape")
    except ValueError as error:
        raise SandboxError("sandbox_path_escape") from error
    return target


def _read_exact_file(path: Path) -> bytes:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError as error:
        raise SandboxError("sandbox_file_missing") from error
    if not stat.S_ISREG(mode):
        raise SandboxError("sandbox_file_unsafe")
    return path.read_bytes()


def _exclusive_create(path: Path, content: bytes) -> str:
    """Create one file with OS-level O_EXCL; never replace or delete."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if _read_exact_file(path) == content:
            return "duplicate"
        raise SandboxError("sandbox_file_collision")
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Never delete or rewrite a partially created file.  Its hash check will
        # fail closed on every later attempt, while no anchor has been published.
        raise
    if _read_exact_file(path) != content:
        raise SandboxError("sandbox_write_verification_failed")
    return "created"


def _verified_input(events: Any, manifest: Any) -> list[dict[str, Any]]:
    result = manifest_contract.verify(events, manifest, enabled=True)
    if (result.get("verifiedCandidateSet") is not True
            or result.get("readyForWriterReview") is not True
            or result.get("promotionEligible") is not False
            or result.get("completenessExternallyAnchored") is not False):
        raise SandboxError("candidate_set_not_verified")
    # E1B rebuilt every envelope and required exact equality.  Canonical cloning
    # here prevents caller mutation after validation.
    return json.loads(_canonical(events).decode("utf-8"))


def _anchor_material(manifest: dict[str, Any], sequence: int,
                     previous_hash: str) -> dict[str, Any]:
    hashes = manifest["eventHashes"]
    return {
        "schemaVersion": SCHEMA_VERSION, "policyVersion": POLICY_VERSION,
        "scopeId": manifest["scopeId"], "sequence": sequence,
        "previousAnchorHash": previous_hash,
        "manifestDigest": manifest["manifestDigest"],
        "eventSetDigest": _digest(hashes),
        "expectedEventCount": manifest["expectedEventCount"],
        "expectedDecisionCount": manifest["expectedDecisionCount"],
        "expectedOutcomeCount": manifest["expectedOutcomeCount"],
        "expectedLegacyCount": manifest["expectedLegacyCount"],
        "decisionDateCount": manifest["decisionDateCount"],
        "writerContractHash": WRITER_CONTRACT_HASH,
        "diagnosticOnly": True, "researchOnly": True,
        "promotionEligible": False, "completenessExternallyAnchored": False,
    }


def _validate_anchor(anchor: Any, expected_scope: str) -> dict[str, Any]:
    if not isinstance(anchor, dict) or set(anchor) != ANCHOR_KEYS:
        raise SandboxError("anchor_schema_invalid")
    material = {key: value for key, value in anchor.items() if key != "anchorHash"}
    if (anchor["schemaVersion"] != SCHEMA_VERSION
            or anchor["policyVersion"] != POLICY_VERSION
            or anchor["scopeId"] != expected_scope
            or not isinstance(anchor["sequence"], int) or anchor["sequence"] < 1
            or any(not isinstance(anchor[name], int) or isinstance(anchor[name], bool)
                   or anchor[name] < 0 for name in (
                       "expectedEventCount", "expectedDecisionCount",
                       "expectedOutcomeCount", "expectedLegacyCount",
                       "decisionDateCount"))
            or anchor["expectedLegacyCount"] > 1
            or anchor["writerContractHash"] != WRITER_CONTRACT_HASH
            or any(not isinstance(anchor[name], str) or HEX.fullmatch(anchor[name]) is None
                   for name in ("previousAnchorHash", "manifestDigest",
                                "eventSetDigest", "anchorHash"))
            or anchor["diagnosticOnly"] is not True
            or anchor["researchOnly"] is not True
            or anchor["promotionEligible"] is not False
            or anchor["completenessExternallyAnchored"] is not False
            or anchor["anchorHash"] != _digest(material)):
        raise SandboxError("anchor_contract_invalid")
    return anchor


def _load_chain(root: Path, scope_id: str) -> list[dict[str, Any]]:
    anchor_dir = _safe_dir(root, "anchors", scope_id)
    entries = list(anchor_dir.iterdir())
    if any(item.is_symlink() or not item.is_file() or SEQUENCE_FILE.fullmatch(item.name) is None
           for item in entries):
        raise SandboxError("anchor_directory_polluted")
    numbered = sorted((int(SEQUENCE_FILE.fullmatch(item.name).group(1)), item)
                      for item in entries)
    if [number for number, _ in numbered] != list(range(1, len(numbered) + 1)):
        raise SandboxError("anchor_sequence_gap")
    previous = GENESIS
    chain: list[dict[str, Any]] = []
    for number, path in numbered:
        try:
            anchor = json.loads(_read_exact_file(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SandboxError("anchor_encoding_invalid") from error
        value = _validate_anchor(anchor, scope_id)
        if value["sequence"] != number or value["previousAnchorHash"] != previous:
            raise SandboxError("anchor_chain_invalid")
        previous = value["anchorHash"]
        chain.append(value)
    return chain


def _read_manifest(root: Path, digest: str) -> dict[str, Any]:
    path = _safe_dir(root, "manifests") / f"{digest}.json"
    try:
        value = json.loads(_read_exact_file(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxError("manifest_blob_invalid") from error
    if _digest({key: item for key, item in value.items() if key != "manifestDigest"}) != digest:
        raise SandboxError("manifest_blob_hash_invalid")
    return value


def _read_events(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    directory = _safe_dir(root, "event_blobs")
    result = []
    for digest in manifest["eventHashes"]:
        try:
            raw = _read_exact_file(directory / f"{digest}.json")
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SandboxError("event_blob_invalid") from error
        if event.get("eventHash") != digest or hashlib.sha256(
                _canonical({key: value for key, value in event.items()
                            if key != "eventHash"})).hexdigest() != digest:
            raise SandboxError("event_blob_hash_invalid")
        result.append(event)
    return result


def _verify_chain_content(root: Path, chain: list[dict[str, Any]]) -> None:
    prior_hashes: set[str] = set()
    prior_counts = (0, 0, 0, 0, 0)
    for anchor in chain:
        manifest = _read_manifest(root, anchor["manifestDigest"])
        events = _read_events(root, manifest)
        replay = manifest_contract.verify(events, manifest, enabled=True)
        if replay.get("verifiedCandidateSet") is not True:
            raise SandboxError("anchored_replay_failed")
        hashes = set(manifest["eventHashes"])
        counts = (
            anchor["expectedEventCount"], anchor["expectedDecisionCount"],
            anchor["expectedOutcomeCount"], anchor["expectedLegacyCount"],
            anchor["decisionDateCount"],
        )
        if (not prior_hashes.issubset(hashes)
                or any(current < previous for current, previous in zip(counts, prior_counts))
                or anchor["eventSetDigest"] != _digest(manifest["eventHashes"])
                or anchor["manifestDigest"] != manifest["manifestDigest"]
                or anchor["expectedEventCount"] != manifest["expectedEventCount"]
                or anchor["expectedDecisionCount"] != manifest["expectedDecisionCount"]
                or anchor["expectedOutcomeCount"] != manifest["expectedOutcomeCount"]
                or anchor["expectedLegacyCount"] != manifest["expectedLegacyCount"]
                or anchor["decisionDateCount"] != manifest["decisionDateCount"]):
            raise SandboxError("anchor_snapshot_regression")
        prior_hashes, prior_counts = hashes, counts


def replay(root: Any, scope_id: str, *, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return _blocked("feature_disabled")
    try:
        if not isinstance(scope_id, str) or HEX.fullmatch(scope_id) is None:
            raise SandboxError("scope_id_invalid")
        value = _absolute_root(root)
        if not value.exists():
            raise SandboxError("sandbox_root_missing")
        chain = _load_chain(value, scope_id)
        if not chain:
            raise SandboxError("anchor_chain_empty")
        _verify_chain_content(value, chain)
        return _success(chain[-1], "replayed")
    except (OSError, SandboxError):
        return _blocked("sandbox_replay_failed")


def write(events: Any, manifest: Any, root: Any, *, enabled: bool = False,
          phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Append one verified snapshot using anchor-last exclusive publication."""
    if not enabled:
        return _blocked("feature_disabled")
    hook = phase_hook or (lambda _phase: None)
    try:
        verified = _verified_input(events, manifest)
        if not isinstance(manifest, dict):
            raise SandboxError("manifest_invalid")
        value = _absolute_root(root)
        _safe_dir(value)
        event_dir = _safe_dir(value, "event_blobs")
        manifest_dir = _safe_dir(value, "manifests")
        chain = _load_chain(value, manifest["scopeId"])
        if chain:
            _verify_chain_content(value, chain)
            previous_manifest = _read_manifest(value, chain[-1]["manifestDigest"])
            if manifest["manifestDigest"] == previous_manifest["manifestDigest"]:
                return _success(chain[-1], "duplicate")
            if not set(previous_manifest["eventHashes"]).issubset(set(manifest["eventHashes"])):
                raise SandboxError("event_set_regression")
        for event in verified:
            _exclusive_create(event_dir / f"{event['eventHash']}.json", _canonical(event))
        hook("event_blobs_verified")
        _exclusive_create(manifest_dir / f"{manifest['manifestDigest']}.json",
                          _canonical(manifest))
        hook("manifest_verified")
        previous_hash = chain[-1]["anchorHash"] if chain else GENESIS
        sequence = len(chain) + 1
        material = _anchor_material(manifest, sequence, previous_hash)
        anchor = {**material, "anchorHash": _digest(material)}
        hook("before_anchor")
        anchor_path = _safe_dir(value, "anchors", manifest["scopeId"]) / f"{sequence:020d}.json"
        try:
            status_name = _exclusive_create(anchor_path, _canonical(anchor))
        except SandboxError as error:
            if str(error) == "sandbox_file_collision":
                raise SandboxError("concurrency_conflict") from error
            raise
        hook("anchor_published")
        replayed = replay(value, manifest["scopeId"], enabled=True)
        if replayed.get("sandboxChainVerified") is not True:
            raise SandboxError("post_write_replay_failed")
        return _success(anchor, "duplicate" if status_name == "duplicate" else "written")
    except SandboxError as error:
        code = str(error)
        return _blocked(code)
    except OSError:
        return _blocked("sandbox_io_failed")
    except Exception:
        return _blocked("sandbox_phase_failed")
