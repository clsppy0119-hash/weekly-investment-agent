"""Immutable public contract for the Taiwan full-market population."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


POPULATION_POLICY_VERSION = "official-full-market-population-v1"


def population_policy() -> dict[str, Any]:
    """Return a fresh copy; callers cannot mutate the canonical definition."""
    return {
        "policyVersion": POPULATION_POLICY_VERSION,
        "securityIdRule": "four-digit-taiwan-equity",
        "securityClass": "common-equity-only-with-official-classification",
        "securityIdentity": "official-issuer-plus-instrument-identity-and-point-in-time-code",
        "marketComponents": [
            "twse_active", "tpex_active", "emerging_active", "membership_events",
        ],
        "includedStates": ["active", "suspended", "zero_volume"],
        "membershipInterval": "entry-inclusive-exit-exclusive",
        "includeLaterDelistedWhileEffective": True,
        "universeDerivation": "official-membership-components-only",
        "prohibitedDerivations": [
            "price", "volume", "fundamentals", "cache", "candidates",
            "current-survivors", "activity-list",
        ],
        "unknownAvailabilityPolicy": "fail-closed-no-retrieved-generated-firstSeen-fallback",
        "correctionPolicy": "append-only-supersedes-no-overwrite",
        "identityTransitionPolicy": "old-exit-equals-new-entry-no-silent-replacement-or-gap",
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def population_policy_view() -> Mapping[str, Any]:
    """Return a recursively immutable view backed by a fresh definition."""
    return _freeze(population_policy())


def population_policy_hash() -> str:
    canonical = json.dumps(
        population_policy(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
