"""Provenance for the daily quote and fundamentals snapshot.

``quotes.json`` carried no provenance, so ``data_contract`` read its source as
unknown and every candidate failed the contract gate. The daily report then
printed "no candidate with sufficient data completeness" while the screener had
in fact ranked candidates -- the data was fine, it was unlabelled.

The gate is right to demand a source, so this fills it in rather than relaxing
it. Two values are judgements and are marked as such:

* ``availableAt`` is *not* the fetch time. ``provenance.py`` is explicit that
  retrieval time must never stand in for publication time. TWSE and TPEx publish
  the daily close after the 13:30 session ends, so the modelled availability is
  14:00 Taipei on the trading date. It is an assumption, not an observation.
* ``conflictStatus`` is only ``no_conflict`` when the exchanges are verified to
  have returned disjoint code sets. Overlap is reported, never assumed away.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from provenance import record

TAIPEI = timezone(timedelta(hours=8))
SESSION_CLOSE = time(14, 0)

QUOTE_ENDPOINTS = (
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
)
FUNDAMENTAL_ENDPOINTS = (
    "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
    "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L",
)


def available_at(trading_date: str) -> str:
    """When the close for ``trading_date`` became public, by session rules."""
    day = datetime.fromisoformat(trading_date).date()
    return datetime.combine(day, SESSION_CLOSE, tzinfo=TAIPEI).isoformat()


def trading_date(today: str, quotes: dict[str, Any], previous: dict[str, Any],
                 previous_date: str | None) -> str:
    """The date the prices belong to, which is not always the date of the run.

    The schedule is weekday-only, but a public holiday still fires it and the
    exchanges return the previous session unchanged. Stamping that with the
    run's date would backdate the next session's close onto a day the market
    never opened -- the exact mislabelling this module exists to prevent. When
    no price has moved, the previous trading date is kept.
    """
    if not previous or not previous_date:
        return today
    moved = any(
        isinstance(values, dict) and previous.get(code, {}).get("price") != values.get("price")
        for code, values in quotes.items()
    )
    return today if moved else previous_date


def conflict_status(code_sets: dict[str, Iterable[str]]) -> tuple[str, list[str]]:
    """``no_conflict`` only when no code was returned by two exchanges."""
    seen: dict[str, str] = {}
    overlaps: list[str] = []
    for market, codes in code_sets.items():
        for code in codes:
            if code in seen and seen[code] != market:
                overlaps.append(code)
            else:
                seen[code] = market
    return ("no_conflict" if not overlaps else "conflict_unresolved"), sorted(set(overlaps))


def build(trading_date: str, retrieved_at: str, quotes: dict[str, Any],
          fundamentals: dict[str, Any], code_sets: dict[str, Iterable[str]]) -> dict[str, Any]:
    """Provenance for the quote and fundamentals halves of the snapshot."""
    status, overlaps = conflict_status(code_sets)
    published = available_at(trading_date)
    quote_record = record(
        provider="TWSE/TPEx OpenAPI", dataset="STOCK_DAY_ALL + tpex_mainboard_daily_close_quotes",
        endpoint=QUOTE_ENDPOINTS[0], scope=sorted(quotes), retrieved_at=retrieved_at,
        effective_date=trading_date, available_at=published, content=quotes,
        conflict_status=status, visibility="public_source",
    )
    quote_record["overlappingCodes"] = overlaps
    quote_record["availableAtBasis"] = "modelled: 14:00 Taipei on the trading date, after the 13:30 close"
    fundamentals_record = record(
        provider="TWSE/TPEx OpenAPI", dataset="BWIBBU_ALL + t187ap05/06/07 (MOPS open data)",
        endpoint=FUNDAMENTAL_ENDPOINTS[0], scope=sorted(fundamentals), retrieved_at=retrieved_at,
        effective_date=trading_date, available_at=published, content=fundamentals,
        conflict_status=status, visibility="public_source",
    )
    fundamentals_record["availableAtBasis"] = quote_record["availableAtBasis"]
    return {"quote": quote_record, "fundamentals": fundamentals_record}
