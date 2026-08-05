"""Build a strict, auditable point-in-time universe gate.

Membership is based only on official listing and delisting dates.  Price
availability is never used as a substitute for membership evidence.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CODE_FIELDS = (
    "公司代號", "股票代號", "有價證券代號", "證券代號", "上市編號",
    "股票代號及名稱", "終止上市公司",
    "SecuritiesCompanyCode", "CompanyCode", "stock_id",
)
ENTRY_FIELDS = (
    "上市日期", "上櫃日期", "興櫃日期", "掛牌日期",
    "ListingDate", "DateOfListing", "list_date",
)
EXIT_FIELDS = (
    "終止上市日期", "終止上櫃日期", "終止興櫃日期", "終止買賣日期", "終止日期",
    "DelistingDate", "date", "null",
)


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def value(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        item = str(row.get(field, "")).strip()
        if item:
            return item
    return ""


def code(row: dict[str, Any]) -> str:
    candidate = value(row, CODE_FIELDS)
    match = re.search(r"\d{4,6}", candidate)
    return match.group(0) if match else ""


def iso_date(raw: str) -> str:
    digits = "".join(re.findall(r"\d", raw))
    if len(digits) == 7:  # ROC yyyMMdd
        return f"{int(digits[:3]) + 1911:04d}-{digits[3:5]}-{digits[5:7]}"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def certification(candidate_codes: list[str], entries: dict[str, str], exits: dict[str, str],
                  known_exited: set[str]) -> tuple[bool, list[str], list[str]]:
    """Require entry evidence for all candidates and exit evidence for known exits.

    Exited securities are required evidence rather than an automatic failure.
    This makes the gate strict but achievable and prevents a current-survivor
    list from being projected into historical rebalances.
    """
    missing_entry = [stock for stock in candidate_codes if stock not in entries]
    missing_exit = sorted(stock for stock in known_exited if stock not in exits)
    certified = bool(candidate_codes) and not missing_entry and not missing_exit
    return certified, missing_entry, missing_exit


def main() -> None:
    cache_dir = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache"))
    official = cache_dir / "official-listing-history-v1"
    historic = load(cache_dir / "historical-universe-v1" / "semiconductor.json", [])
    all_market = load(cache_dir / "historical-universe-v1" / "all-market.json", [])
    delisted = load(official / "finmind_delisted.json", [])
    candidate_codes = sorted({
        str(row.get("stock_id", row.get("SecuritiesCompanyCode", "")))
        for row in [*historic, *all_market, *delisted]
        if str(row.get("stock_id", row.get("SecuritiesCompanyCode", ""))).isdigit()
    })

    entries: dict[str, str] = {}
    exits: dict[str, str] = {}
    known_exited: set[str] = set()
    official_current_records = 0
    source_fields: dict[str, list[str]] = {}
    for source in ("twse_listed", "tpex_listed", "tpex_emerging"):
        rows = load(official / f"{source}.json", [])
        source_fields[source] = sorted({str(key) for row in rows if isinstance(row, dict) for key in row})
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock = code(row)
            listed = iso_date(value(row, ENTRY_FIELDS))
            if stock and listed:
                entries[stock] = min(entries.get(stock, listed), listed)
                official_current_records += 1

    terminated_rows = load(official / "twse_terminated.json", [])
    source_fields["twse_terminated"] = sorted({str(key) for row in terminated_rows if isinstance(row, dict) for key in row})
    for row in terminated_rows:
        if not isinstance(row, dict):
            continue
        stock, ended = code(row), iso_date(value(row, EXIT_FIELDS))
        if stock:
            known_exited.add(stock)
        if stock and ended:
            exits[stock] = max(exits.get(stock, ended), ended)

    finmind_rows = load(official / "finmind_delisted.json", [])
    source_fields["finmind_delisted"] = sorted({str(key) for row in finmind_rows if isinstance(row, dict) for key in row})
    for row in finmind_rows:
        if not isinstance(row, dict):
            continue
        stock, ended = code(row), iso_date(value(row, EXIT_FIELDS))
        if stock:
            known_exited.add(stock)
        if stock and ended:
            exits[stock] = max(exits.get(stock, ended), ended)

    evidence = {
        stock: {"entryDate": entries.get(stock), "exitDate": exits.get(stock)}
        for stock in candidate_codes
    }
    certified, missing_entry, missing_exit = certification(candidate_codes, entries, exits, known_exited)
    exited = [stock for stock, item in evidence.items() if item["exitDate"]]
    eligible_all_dates = [stock for stock, item in evidence.items() if item["entryDate"] and not item["exitDate"]]
    private = official / "semiconductor-membership-evidence.json"
    private.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    status = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rule": "official entry date required; official exit date required for known exits; no price-date inference",
        "candidateStocks": len(candidate_codes),
        "officialCurrentRecordsWithEntryDate": official_current_records,
        "evidence": {
            "entryVerified": len(candidate_codes) - len(missing_entry),
            "missingOfficialEntry": len(missing_entry),
            "knownExited": len(known_exited),
            "missingOfficialExit": len(missing_exit),
            "officiallyExited": len(exited),
            "currentlyEligible": len(eligible_all_dates),
        },
        "sourceFieldNames": source_fields,
        "certified": certified,
        "promotionGate": "open" if certified else "closed: official historical membership evidence is incomplete",
        "cacheVisibility": "private GitHub Actions cache; stock-level evidence is not committed",
    }
    (official / "universe-certification.json").write_text(
        json.dumps({"certified": certified, "rule": status["rule"]}, ensure_ascii=False), encoding="utf-8"
    )
    output = ROOT / "data" / "point-in-time-universe-status.json"
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
