"""Create an auditable research-eligibility record from normalised MOPS data.

This is a data-quality and risk gate, not a buy recommendation.  Every result
records the period, rule version, metric used, threshold and outcome so later
agent research can be reviewed rather than reconstructed from prose.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RULE_VERSION = "fundamental-gate-v1"
RULES = (
    ("grossMargin", ">=", 0.20, "毛利率至少 20%"),
    ("operatingMargin", ">=", 0.00, "營業利益率不得為負"),
    ("debtRatio", "<", 0.50, "負債比低於 50%（金融業不適用）"),
    ("cashEarningsRatio", ">=", 0.80, "營運現金流／淨利至少 0.8"),
    ("annualizedRoe", ">=", 0.12, "單季年化 ROE 至少 12%，僅作當期篩選"),
)
REQUIRED = {"revenue", "grossProfit", "operatingIncome", "netIncome", "assets", "liabilities", "equity", "operatingCashFlow", "eps"}


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evaluate(record: dict[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics", {})
    derived = record.get("derived", {})
    completeness = sorted(REQUIRED - set(metrics))
    checks = []
    for key, operator, threshold, description in RULES:
        value = derived.get(key)
        passed = value is not None and (value >= threshold if operator == ">=" else value < threshold)
        checks.append({"metric": key, "operator": operator, "threshold": threshold, "value": value, "passed": passed, "description": description})
    eligible = not completeness and all(check["passed"] for check in checks)
    return {
        "code": record.get("code"),
        "period": record.get("period"),
        "ruleVersion": RULE_VERSION,
        "source": record.get("source"),
        "sourceConcepts": record.get("concepts", {}),
        "missingRawMetrics": completeness,
        "checks": checks,
        "outcome": "priority_research" if eligible else "observe_or_reject",
        "reason": "資料完整且通過基本品質與風險門檻" if eligible else "資料不完整或至少一項基本品質／風險門檻未通過",
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    cache_root = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache"))
    source_dir = cache_root / "mops-normalized-v1"
    output_dir = cache_root / "fundamental-decisions-v1"
    decisions: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for path in sorted(source_dir.glob("*.json")):
        try:
            decision = evaluate(load(path, {}))
            save(output_dir / path.name, decision)
            decisions[path.stem] = {"period": decision["period"], "outcome": decision["outcome"], "checkCount": len(decision["checks"]), "passedChecks": sum(check["passed"] for check in decision["checks"])}
        except Exception as error:
            failures[path.stem] = f"{type(error).__name__}: {error}"
    status = {"schemaVersion": 1, "ruleVersion": RULE_VERSION, "cacheVisibility": "private GitHub Actions cache; decision values are not committed", "updatedAt": datetime.now(timezone.utc).isoformat(), "coverage": {"sourceRecords": len(list(source_dir.glob('*.json'))), "evaluated": len(decisions), "failed": len(failures)}, "decisions": decisions, "failures": failures}
    save(ROOT / "data" / "fundamental-decision-gate-status.json", status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
