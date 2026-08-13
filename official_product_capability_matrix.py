"""Return-free capability matrix for reviewed official Taiwan market products.

The matrix records what the public product documentation and samples can and
cannot establish.  It is deliberately separate from source admission: a
technical capability such as a current snapshot or a listing-event field is
not authority for historical PIT use, legal retention, or independent
custody.  The module performs no I/O and is not imported by formal consumers.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "official-product-capability-matrix-v1"
POPULATION_POLICY_HASH = "2b4d0b2b1c20b4a49f1e8cd6044499d5a7f866e3930a813768ce8caea72e4404"
SOURCE_ADMISSION_POLICY_VERSION = "official-population-source-admission-policy-v1"
SOURCE_ADMISSION_SOURCE_HASH = "73253532654f4cdb30661e4c534731363dbb2bab67590a56870c840af4587ce1"
SOURCE_ADMISSION_OBLIGATION_MATRIX_HASH = (
    "2df245b26a13451c1d179a5d71cafe670ccce2d10afb6ac49f9666ce8306c776"
)

PROVEN = "PROVEN"
PARTIAL = "PARTIAL"
UNPROVEN = "UNPROVEN"
CONFLICT = "CONFLICT"
STATUSES = (PROVEN, PARTIAL, UNPROVEN, CONFLICT)

AXES = (
    "marketScope",
    "currentActiveSnapshot",
    "zeroVolumeCoverage",
    "listingEvent",
    "delistingEvent",
    "transferLinkage",
    "securityIdentity",
    "issuerIdentity",
    "codeReuseLineage",
    "schemaSampleConsistency",
    "revisionSupersedes",
    "authoritativeAvailableAt",
    "historyRange",
    "privateRetention",
    "independentCustodian",
    "automatedProcessing",
    "longTermReplay",
)

MAX_NODES = 25_000
MAX_DEPTH = 12
MAX_STRING = 512
MAX_CANONICAL_BYTES = 750_000
MAX_INTEGER_ABS = 10**18

_SENSITIVE_KEY_TOKENS = (
    "raw", "rows", "body", "headers", "url", "uri", "query", "token",
    "secret", "authorization", "cookie", "password", "privatekey",
)
_PERFORMANCE_KEY_TOKENS = (
    "price", "return", "outcome", "score", "rank", "mdd", "pnl",
    "recommendation", "adviceenabled", "promotioneligible",
    "strategyvalidated", "tradingenabled",
)
_FORBIDDEN_TEXT = (
    "://", "bearer ", "authorization:", "token=", "password=", "cookie=",
    "-----begin ",
)


def _canonical(value: Any, _dumps: Any = json.dumps) -> bytes:
    return _dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(
    value: Any,
    _canonical_bytes: Any = _canonical,
    _sha256: Any = hashlib.sha256,
) -> str:
    return _sha256(_canonical_bytes(value)).hexdigest()


def _axes(**overrides: str) -> dict[str, str]:
    value = {axis: UNPROVEN for axis in AXES}
    value.update(overrides)
    if set(value) != set(AXES) or any(status not in STATUSES for status in value.values()):
        raise ValueError("invalid capability axes")
    return value


def _product(
    product_id: str,
    provider: str,
    product_code: str,
    market_scopes: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    fact_codes: tuple[str, ...],
    axes: dict[str, str],
) -> dict[str, Any]:
    descriptor = {
        "evidenceReferenceIds": list(evidence_refs),
        "reviewedFactCodes": list(fact_codes),
    }
    value = {
        "productId": product_id,
        "provider": provider,
        "productCode": product_code,
        "marketScopes": list(market_scopes),
        "evidenceDescriptorHash": digest(descriptor),
        "axes": axes,
        "technicalUses": [],
        "gapCodes": [],
    }
    if axes["currentActiveSnapshot"] == PROVEN:
        value["technicalUses"].append("current_active_snapshot_evidence")
    if axes["listingEvent"] == PROVEN or axes["delistingEvent"] == PROVEN:
        value["technicalUses"].append("supplemental_membership_event_evidence")
    if axes["zeroVolumeCoverage"] == PROVEN:
        value["technicalUses"].append("zero_volume_presence_evidence")
    if axes["securityIdentity"] == PROVEN:
        value["technicalUses"].append("security_identity_evidence")
    if axes["issuerIdentity"] == PROVEN:
        value["technicalUses"].append("issuer_identity_evidence")

    required_historical = (
        "marketScope", "zeroVolumeCoverage", "listingEvent", "delistingEvent",
        "transferLinkage",
        "securityIdentity", "issuerIdentity", "codeReuseLineage",
        "schemaSampleConsistency", "revisionSupersedes",
        "authoritativeAvailableAt", "historyRange", "privateRetention",
        "independentCustodian", "automatedProcessing", "longTermReplay",
    )
    value["historicalPitCapable"] = all(axes[axis] == PROVEN for axis in required_historical)
    value["independentCustodyReady"] = all(
        axes[axis] == PROVEN
        for axis in ("privateRetention", "independentCustodian", "longTermReplay")
    )
    for axis in required_historical:
        if axes[axis] != PROVEN:
            value["gapCodes"].append(
                axis + "_" + axes[axis].casefold()
            )
    value["technicalUses"].sort()
    value["gapCodes"].sort()
    value["productDigest"] = digest(value)
    return value


def _definitions() -> list[dict[str, Any]]:
    products = [
        _product(
            "twse-t48-listing-delisting-v1", "TWSE", "T48", ("twse",),
            (
                "twse-shop-product-000000006e0bbe8d016f182105d90328",
                "twse-t48-format", "twse-t48-public-sample",
            ),
            (
                "data-code-3-listing", "data-code-4-delisting",
                "effective-date-present", "scheduled-production-2300",
                "sample-list-delist-rows-not-four-digit-common-equity",
                "history-start-2019-12-23",
            ),
            _axes(
                marketScope=PARTIAL, listingEvent=PROVEN,
                delistingEvent=PROVEN, securityIdentity=PARTIAL,
                schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL, historyRange=PARTIAL,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "twse-tranisin-daily-master-v1", "TWSE", "TRANISIN", ("twse",),
            (
                "twse-shop-product-000000006f6a5e3401702de5988d004b",
                "twse-tranisin-format", "twse-tranisin-public-sample",
            ),
            (
                "daily-active-security-snapshot", "isin-present",
                "listing-date-present", "scheduled-production-0700",
                "sample-has-no-file-asof", "history-start-pages-conflict",
            ),
            _axes(
                marketScope=PROVEN, currentActiveSnapshot=PROVEN,
                listingEvent=PARTIAL, securityIdentity=PROVEN,
                schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL, historyRange=CONFLICT,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "twse-bfi85u-daily-security-master-v1", "TWSE", "BFI85U", ("twse",),
            (
                "twse-shop-bfi85u", "twse-bfi85u-format",
                "twse-bfi85u-public-sample",
            ),
            (
                "daily-security-master", "scheduled-production-2200",
                "security-category-present", "issuer-id-absent",
                "history-start-2020-03-02",
            ),
            _axes(
                marketScope=PARTIAL, currentActiveSnapshot=PARTIAL,
                securityIdentity=PARTIAL, schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL, historyRange=PARTIAL,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "twse-bft51u-daily-security-data-v1", "TWSE", "BFT51U", ("twse",),
            (
                "twse-shop-bft51u", "twse-bft51u-format",
                "twse-bft51u-public-sample",
            ),
            (
                "daily-security-data", "zero-close-rows-retained",
                "four-digit-zero-close-examples-present",
                "scheduled-production-1440", "history-start-pages-conflict",
                "not-membership-authority",
            ),
            _axes(
                marketScope=PARTIAL, currentActiveSnapshot=PARTIAL,
                zeroVolumeCoverage=PARTIAL, securityIdentity=PARTIAL,
                schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL, historyRange=CONFLICT,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "twse-terminated-company-list-v1", "TWSE", "terminated-list", ("twse",),
            ("twse-terminated-company-official-page",),
            (
                "effective-termination-date-present",
                "later-delisted-case-2456-present", "revision-chain-absent",
                "publication-time-absent",
            ),
            _axes(
                marketScope=PARTIAL, delistingEvent=PROVEN,
                securityIdentity=PARTIAL, schemaSampleConsistency=PARTIAL,
                historyRange=PARTIAL, privateRetention=PARTIAL,
                automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "twse-openapi-current-company-master-v1", "TWSE", "t187ap03-L", ("twse",),
            ("data-gov-tw-18419", "twse-openapi-t187ap03-L"),
            (
                "current-company-master", "company-identifier-present",
                "listing-date-present", "no-historical-revisions",
            ),
            _axes(
                marketScope=PROVEN, currentActiveSnapshot=PROVEN,
                listingEvent=PARTIAL, securityIdentity=PARTIAL,
                issuerIdentity=PROVEN, schemaSampleConsistency=PROVEN,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "tpex-s24-stkanou-event-file-v1", "TPEx", "S24", ("tpex",),
            (
                "tpex-ed-is-v1-33", "tpex-s24-format",
                "tpex-s24-public-sample",
            ),
            (
                "new-otc-event", "delist-event", "suspension-event",
                "resume-event", "header-date-time-count",
                "sample-event-rows-warrant-dominated", "s09-not-event-file",
            ),
            _axes(
                marketScope=PARTIAL, listingEvent=PROVEN,
                delistingEvent=PROVEN, securityIdentity=PARTIAL,
                schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "tpex-t48-listing-delisting-v1", "TPEx", "T48", ("tpex",),
            (
                "tpex-t48-format", "tpex-t48-public-sample",
                "tpex-shop-otc-basic-product",
            ),
            (
                "data-code-3-otc-listing", "data-code-4-otc-delisting",
                "effective-date-present", "delivered-before-effective-open",
                "sample-list-delist-rows-warrant-dominated",
                "history-start-2011-11-30",
            ),
            _axes(
                marketScope=PARTIAL, listingEvent=PROVEN,
                delistingEvent=PROVEN, securityIdentity=PARTIAL,
                schemaSampleConsistency=PROVEN,
                authoritativeAvailableAt=PARTIAL, historyRange=PARTIAL,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "tpex-e01-emerging-market-info-v1", "TPEx", "E01", ("emerging",),
            (
                "tpex-shop-emerging-product", "tpex-e01-current-format",
                "tpex-e01-public-old-sample",
            ),
            (
                "daily-emerging-snapshot", "listing-date-present",
                "otc-and-twse-application-progress-present",
                "header-date-time-count", "current-format-165-bytes",
                "public-sample-old-format-139-bytes",
                "history-start-2012-12-05",
            ),
            _axes(
                marketScope=PROVEN, currentActiveSnapshot=PROVEN,
                listingEvent=PARTIAL, transferLinkage=PARTIAL,
                securityIdentity=PARTIAL, schemaSampleConsistency=CONFLICT,
                authoritativeAvailableAt=PARTIAL, historyRange=PARTIAL,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "tpex-openapi-current-otc-company-master-v1", "TPEx", "t187ap03-O", ("tpex",),
            ("data-gov-tw-25036", "tpex-openapi-t187ap03-O"),
            (
                "current-company-master", "company-identifier-present",
                "listing-date-present", "no-historical-revisions",
            ),
            _axes(
                marketScope=PROVEN, currentActiveSnapshot=PROVEN,
                listingEvent=PARTIAL, securityIdentity=PARTIAL,
                issuerIdentity=PROVEN, schemaSampleConsistency=PROVEN,
                privateRetention=PARTIAL, automatedProcessing=PARTIAL,
            ),
        ),
        _product(
            "tpex-openapi-current-emerging-company-master-v1", "TPEx", "t187ap04-O", ("emerging",),
            ("tpex-openapi-t187ap04-O",),
            (
                "current-emerging-company-master",
                "company-identifier-present", "listing-date-present",
                "catalog-license-contract-unverified",
                "no-historical-revisions",
            ),
            _axes(
                marketScope=PROVEN, currentActiveSnapshot=PROVEN,
                listingEvent=PARTIAL, securityIdentity=PARTIAL,
                issuerIdentity=PROVEN, schemaSampleConsistency=PROVEN,
                privateRetention=UNPROVEN, automatedProcessing=UNPROVEN,
            ),
        ),
    ]
    products.sort(key=lambda item: item["productId"])
    return products


def _build_artifact() -> dict[str, Any]:
    products = _definitions()
    current_products = [
        item["productId"] for item in products
        if "current_active_snapshot_evidence" in item["technicalUses"]
    ]
    event_products = [
        item["productId"] for item in products
        if "supplemental_membership_event_evidence" in item["technicalUses"]
    ]
    zero_volume_products = [
        item["productId"] for item in products
        if "zero_volume_presence_evidence" in item["technicalUses"]
    ]
    identity_products = [
        item["productId"] for item in products
        if "security_identity_evidence" in item["technicalUses"]
        or "issuer_identity_evidence" in item["technicalUses"]
    ]
    current_markets = sorted({
        market
        for item in products
        if item["productId"] in current_products
        for market in item["marketScopes"]
    })
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": POLICY_VERSION,
        "mode": "research_only",
        "populationPolicyHash": POPULATION_POLICY_HASH,
        "sourceAdmissionPolicyVersion": SOURCE_ADMISSION_POLICY_VERSION,
        "sourceAdmissionSourceHash": SOURCE_ADMISSION_SOURCE_HASH,
        "sourceAdmissionObligationMatrixHash": SOURCE_ADMISSION_OBLIGATION_MATRIX_HASH,
        "evidenceClass": "reviewed_public_official_documentation_and_samples",
        "axes": list(AXES),
        "statusValues": list(STATUSES),
        "products": products,
        "derivedClassifications": {
            "currentSnapshotEvidenceProducts": current_products,
            "currentSnapshotEvidenceMarkets": current_markets,
            "supplementalMembershipEventEvidenceProducts": event_products,
            "zeroVolumePresenceEvidenceProducts": zero_volume_products,
            "identityEvidenceProducts": identity_products,
            "historicalPitCapableProducts": [
                item["productId"] for item in products if item["historicalPitCapable"]
            ],
            "independentCustodyReadyProducts": [
                item["productId"] for item in products if item["independentCustodyReady"]
            ],
            "crossProductJoinRule": "no_implicit_axis_completion_across_products",
            "forwardObserverAdmissionReady": False,
            "historicalPitAdmissionReady": False,
            "independentCustodyReady": False,
        },
        "blockers": [
            "authoritative_version_available_at_unproven",
            "complete_revision_supersedes_chain_unproven",
            "code_reuse_and_transfer_lineage_unproven",
            "cross_product_identity_join_not_version_bound",
            "full_common_equity_market_coverage_unproven",
            "independent_custodian_permission_unproven",
            "private_long_term_retention_and_replay_unproven",
            "source_admission_registry_unchanged",
        ],
    }
    base["matrixHash"] = digest(base)
    return base


def _json_domain(
    value: Any,
    _max_nodes: int = MAX_NODES,
    _max_depth: int = MAX_DEPTH,
    _max_string: int = MAX_STRING,
    _max_integer_abs: int = MAX_INTEGER_ABS,
    _finite: Any = math.isfinite,
) -> bool:
    seen: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > _max_nodes or depth > _max_depth:
            return False
        item_type = type(item)
        if item is None or item_type is bool:
            return True
        if item_type is int:
            return abs(item) <= _max_integer_abs
        if item_type is float:
            return _finite(item)
        if item_type is str:
            return len(item) <= _max_string and all(ord(char) >= 32 for char in item)
        if item_type not in (dict, list):
            return False
        identity = id(item)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            if item_type is dict:
                return all(
                    type(key) is str and len(key) <= 100 and visit(child, depth + 1)
                    for key, child in item.items()
                )
            return all(visit(child, depth + 1) for child in item)
        finally:
            seen.remove(identity)

    return visit(value, 0)


def _contains_forbidden(
    value: Any,
    _sensitive: tuple[str, ...] = _SENSITIVE_KEY_TOKENS,
    _performance: tuple[str, ...] = _PERFORMANCE_KEY_TOKENS,
    _text_markers: tuple[str, ...] = _FORBIDDEN_TEXT,
) -> bool:
    def visit(item: Any) -> bool:
        if type(item) is dict:
            for key, child in item.items():
                separated: list[str] = []
                for index, character in enumerate(key):
                    if (
                        index > 0 and character.isupper()
                        and (key[index - 1].islower() or key[index - 1].isdigit())
                    ):
                        separated.append("_")
                    separated.append(character.casefold() if character.isalnum() else "_")
                tokens = {token for token in "".join(separated).split("_") if token}
                normalized = "".join(
                    character for character in key.casefold() if character.isalnum()
                )
                if (
                    tokens.intersection(_sensitive + _performance)
                    or normalized in {
                        "adviceenabled", "promotioneligible", "strategyvalidated",
                        "tradingenabled", "accesstoken", "apitoken", "privatekey",
                    }
                ):
                    return True
                if visit(child):
                    return True
            return False
        if type(item) is list:
            return any(visit(child) for child in item)
        if type(item) is str:
            folded = item.casefold()
            return any(marker in folded for marker in _text_markers)
        return False

    return visit(value)


def _factory() -> tuple[Any, Any, Any]:
    initial = _build_artifact()
    canonical = _canonical
    bounded = _json_domain
    forbidden = _contains_forbidden
    loads = json.loads
    schema_version = SCHEMA_VERSION
    policy_version = POLICY_VERSION
    max_canonical_bytes = MAX_CANONICAL_BYTES
    expected_bytes = canonical(initial)
    expected_hash = initial["matrixHash"]
    fixed_blockers = tuple(initial["blockers"])
    product_count = len(initial["products"])
    current_snapshot_count = len(
        initial["derivedClassifications"]["currentSnapshotEvidenceProducts"]
    )
    supplemental_event_count = len(
        initial["derivedClassifications"]["supplementalMembershipEventEvidenceProducts"]
    )
    zero_volume_count = len(
        initial["derivedClassifications"]["zeroVolumePresenceEvidenceProducts"]
    )
    del initial

    def artifact() -> dict[str, Any]:
        return loads(expected_bytes.decode("utf-8"))

    def report(valid: bool, blockers: list[str]) -> dict[str, Any]:
        formal = {
            "sourceAdmitted": False,
            "historicalEligible": False,
            "pitCoverageCertified": False,
            "strategyValidated": False,
            "promotionEligible": False,
            "adviceEnabled": False,
            "formalGateAttached": False,
            "tradingEnabled": False,
        }
        return {
            "schemaVersion": schema_version,
            "policyVersion": policy_version,
            "mode": "research_only",
            "matrixStructurallyValid": valid,
            "matrixHash": expected_hash if valid else "",
            "productCount": product_count,
            "currentSnapshotEvidenceProductCount": current_snapshot_count,
            "supplementalEventEvidenceProductCount": supplemental_event_count,
            "zeroVolumeEvidenceProductCount": zero_volume_count,
            **formal,
            "blockers": list(fixed_blockers) if valid else blockers,
        }

    def evaluate(value: Any) -> dict[str, Any]:
        try:
            if not bounded(value) or forbidden(value):
                return report(False, ["input_not_bounded_metadata_only_json"])
            encoded = canonical(value)
            if len(encoded) > max_canonical_bytes or encoded != expected_bytes:
                return report(False, ["capability_matrix_contract_or_hash_mismatch"])
            return report(True, [])
        except Exception:
            return report(False, ["input_fail_closed"])

    def run(value: Any, *, enabled: bool = False) -> dict[str, Any]:
        if not enabled:
            return {
                "schemaVersion": schema_version,
                "policyVersion": policy_version,
                "mode": "disabled",
                "matrixStructurallyValid": False,
                "sourceAdmitted": False,
                "historicalEligible": False,
                "adviceEnabled": False,
                "tradingEnabled": False,
            }
        return evaluate(value)

    return artifact, evaluate, run


artifact, evaluate, run = _factory()


MATRIX_HASH = artifact()["matrixHash"]

del _factory
del _build_artifact
del _definitions
del _product
del _axes
