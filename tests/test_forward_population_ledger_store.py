"""Durable CAS and isolation tests for the private forward ledger store."""
import json
import tempfile
import unittest
from pathlib import Path

import forward_population_ledger_store as store
import forward_population_observation as observer
from tests.test_forward_population_observation import observation


def test_disabled_does_not_inspect_paths_or_candidate():
    class Explodes:
        def __fspath__(self):
            raise RuntimeError("must not inspect")

    assert store.append(Explodes(), Explodes(), Explodes(), allowed_root=Explodes())[
        "status"
    ] == "disabled"


def test_append_is_durable_cas_and_duplicate_is_noop():
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp)
        root = allowed / "private-ledger"
        empty_hash = observer.ledger_hash([])
        first = observation()
        result = store.append(root, empty_hash, first, allowed_root=allowed, enabled=True)
        assert result["status"] == "appended"
        assert result["eventCount"] == 1
        before = (root / "event-00000001.json").read_bytes()
        duplicate = store.append(
            root, result["currentLedgerHash"], first, allowed_root=allowed, enabled=True
        )
        assert duplicate["status"] == "duplicate_noop"
        assert (root / "event-00000001.json").read_bytes() == before
        stale = observation(
            sequence=2, previous=first["eventHash"], event_id="observation-2",
            completed="2026-08-14T00:00:00Z",
        )
        assert store.append(root, empty_hash, stale, allowed_root=allowed, enabled=True)[
            "status"
        ] == "cas_mismatch"


def test_corrupt_chain_lock_extra_file_and_traversal_fail_without_rewrite():
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp)
        root = allowed / "private-ledger"
        first = observation()
        initial = store.append(
            root, observer.ledger_hash([]), first, allowed_root=allowed, enabled=True
        )
        original = (root / "event-00000001.json").read_bytes()
        (root / "unexpected.tmp").write_text("x", encoding="utf-8")
        second = observation(
            sequence=2, previous=first["eventHash"], event_id="observation-2",
            completed="2026-08-14T00:00:00Z",
        )
        assert store.append(
            root, initial["currentLedgerHash"], second, allowed_root=allowed, enabled=True
        )["status"] == "invalid"
        assert (root / "event-00000001.json").read_bytes() == original
        (root / "unexpected.tmp").unlink()
        (root / store.LOCK_NAME).write_text("busy", encoding="utf-8")
        assert store.append(
            root, initial["currentLedgerHash"], second, allowed_root=allowed, enabled=True
        )["status"] == "storage_unavailable"
        assert (root / "event-00000001.json").read_bytes() == original
        assert store.append(
            allowed / ".." / "escape", "x", {}, allowed_root=allowed, enabled=True
        )["status"] == "storage_unavailable"


def test_receipt_never_claims_authority_or_promotion():
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp)
        result = store.append(
            allowed / "ledger", observer.ledger_hash([]), observation(),
            allowed_root=allowed, enabled=True,
        )
        assert result["appendPublished"] is True
        for key in (
            "checkpointAuthorityRegistered", "appendOnlyCertified",
            "forwardEvidenceAdmitted", "historicalEligible",
            "pitCoverageCertified", "promotionEligible", "adviceEnabled",
        ):
            assert result[key] is False
        encoded = json.dumps(result, sort_keys=True)
        for forbidden in ("entity", "component", "availableAt", "price", "return"):
            assert forbidden not in encoded


def test_event_count_and_total_bytes_are_bounded():
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp)
        root = allowed / "ledger"
        old_limit = store.MAX_EVENT_FILES
        old_bytes = store.MAX_TOTAL_LEDGER_BYTES
        try:
            store.MAX_EVENT_FILES = 2
            head = observer.ledger_hash([])
            first = observation()
            first_result = store.append(root, head, first, allowed_root=allowed, enabled=True)
            second = observation(
                sequence=2, previous=first["eventHash"], event_id="observation-2",
                completed="2026-08-14T00:00:00Z",
            )
            second_result = store.append(
                root, first_result["currentLedgerHash"], second,
                allowed_root=allowed, enabled=True,
            )
            before = [(root / store._event_name(i)).read_bytes() for i in (1, 2)]
            third = observation(
                sequence=3, previous=second["eventHash"], event_id="observation-3",
                completed="2026-08-15T00:00:00Z",
            )
            assert store.append(
                root, second_result["currentLedgerHash"], third,
                allowed_root=allowed, enabled=True,
            )["status"] == "invalid"
            assert not (root / store._event_name(3)).exists()
            assert before == [(root / store._event_name(i)).read_bytes() for i in (1, 2)]
            store.MAX_TOTAL_LEDGER_BYTES = 1
            assert store.inspect(root, allowed_root=allowed, enabled=True)["status"] == "invalid"
        finally:
            store.MAX_EVENT_FILES = old_limit
            store.MAX_TOTAL_LEDGER_BYTES = old_bytes


def test_symlink_or_junction_root_is_rejected_when_supported():
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp)
        real = allowed / "real"
        real.mkdir()
        link = allowed / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            return
        result = store.append(
            link, observer.ledger_hash([]), observation(),
            allowed_root=allowed, enabled=True,
        )
        assert result["status"] == "storage_unavailable"
        assert not (real / "event-00000001.json").exists()


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case, description=name))
    return suite
