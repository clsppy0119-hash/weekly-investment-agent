"""Default-off writer for private, append-only shadow lineage metadata."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

import lineage_shadow

TABLE = "investment_data_lineage_shadow"
ALLOWLIST = ("provider", "dataset", "entityId", "observationPeriod", "sourceRevision", "availableAt", "schemaVersion", "contentHash", "compositeKey", "supersedesContentHash", "status", "conflictStatus", "visibility")


def payload(record: dict[str, Any]) -> dict[str, Any]:
    blockers = lineage_shadow.validate(record, coverage=1.0)
    if blockers:
        raise ValueError("lineage_not_writable:" + ",".join(blockers))
    row = {key: record.get(key) for key in ALLOWLIST}
    if row["visibility"] != "private_lineage":
        raise ValueError("lineage_visibility_invalid")
    return row


def write(records: list[dict[str, Any]], *, request: Callable[..., Any] = urllib.request.urlopen) -> dict[str, int | str]:
    """Append-only writer. It is intentionally a no-op unless explicitly enabled."""
    if not lineage_shadow.enabled():
        return {"status": "disabled", "written": 0, "duplicates": 0}
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("lineage_writer_requires_private_service_credentials")
    written = duplicates = 0
    for record in records:
        body = payload(record)
        req = urllib.request.Request(
            f"{url.rstrip('/')}/rest/v1/{TABLE}", data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=minimal"}, method="POST",
        )
        try:
            with request(req, timeout=30) as response:
                if response.status not in (200, 201, 204):
                    raise RuntimeError(f"lineage_write_http_{response.status}")
            written += 1
        except urllib.error.HTTPError as error:
            # The table's composite/content unique constraints make only the exact
            # duplicate conflict idempotent. Any other conflict remains unsafe.
            text = error.read().decode("utf-8", "replace")[:512]
            if error.code == 409 and record["compositeKey"] in text and record["contentHash"] in text:
                duplicates += 1
            else:
                raise RuntimeError(f"lineage_write_failed_{error.code}") from error
    return {"status": "ok", "written": written, "duplicates": duplicates}
