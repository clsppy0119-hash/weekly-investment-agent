"""Normalise cached official MOPS iXBRL filings into auditable core metrics.

Only values with an explicit XBRL concept are retained.  Normalised results
stay in the private Actions cache; the committed status file intentionally
contains coverage and source metadata only.
"""

from __future__ import annotations

import html
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONCEPTS = {
    "revenue": ("Revenue", "OperatingRevenue"),
    "grossProfit": ("GrossProfit",),
    "operatingIncome": ("OperatingIncome", "OperatingIncomeLoss", "IncomeFromOperations", "ProfitLossFromOperatingActivities"),
    "netIncome": ("ProfitLoss", "NetIncomeLoss", "ProfitLossFromContinuingOperations"),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("Equity",),
    "operatingCashFlow": ("NetCashFlowsFromUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivities", "NetCashFlowsFromOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
    "eps": ("BasicEarningsPerShare", "BasicEarningsLossPerShare", "EarningsPerShareBasic", "EarningsPerShare"),
}
TAG = re.compile(r"<ix:nonFraction\b(?P<attrs>[^>]*)>(?P<value>.*?)</ix:nonFraction>", re.I | re.S)
ATTRIBUTE = re.compile(r'(?P<key>[\w:-]+)=["\'](?P<value>.*?)["\']', re.S)


def load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_ixbrl(path: Path) -> str:
    content = path.read_bytes()
    if path.suffix == ".zip":
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith((".htm", ".html", ".xhtml"))]
            if not names:
                raise ValueError("XBRL ZIP 中沒有 iXBRL HTML")
            content = archive.read(names[0])
    return content.decode("utf-8", errors="replace")


def number(value: str, attrs: dict[str, str]) -> float | None:
    text = re.sub(r"<[^>]+>", "", html.unescape(value)).replace(",", "").strip()
    text = re.sub(r"\s+", "", text)
    if not text or text in {"-", "--"}:
        return None
    try:
        result = float(text)
        scale = int(attrs.get("scale", "0"))
        if attrs.get("sign") == "-":
            result = -result
        return result * (10 ** scale)
    except ValueError:
        return None


def extract_metrics(document: str) -> tuple[dict[str, float], dict[str, str], list[str]]:
    wanted = {concept: metric for metric, concepts in CONCEPTS.items() for concept in concepts}
    metrics: dict[str, float] = {}
    sources: dict[str, str] = {}
    candidate_concepts: set[str] = set()
    for match in TAG.finditer(document):
        attrs = {item.group("key").lower(): item.group("value") for item in ATTRIBUTE.finditer(match.group("attrs"))}
        concept = attrs.get("name", "").split(":")[-1]
        if any(term in concept.lower() for term in ("operating", "earning", "cashflow", "cashflows")):
            candidate_concepts.add(attrs.get("name", concept))
        metric = wanted.get(concept)
        if not metric or metric in metrics:
            continue
        parsed = number(match.group("value"), attrs)
        if parsed is not None:
            metrics[metric] = parsed
            sources[metric] = attrs.get("name", concept)
    return metrics, sources, sorted(candidate_concepts)


def main() -> None:
    cache_root = Path(os.environ.get("DATA_CACHE_DIR", ROOT / ".private-data-cache"))
    source_dir = cache_root / "mops-fundamentals-v2" / "stocks"
    output_dir = cache_root / "mops-normalized-v1"
    parsed: dict[str, dict[str, Any]] = {}
    candidate_concepts: set[str] = set()
    failures: dict[str, str] = {}
    for metadata_path in sorted(source_dir.glob("*.json")):
        code = metadata_path.stem
        try:
            metadata = load(metadata_path, {})
            raw = next((path for path in (source_dir / f"{code}.html", source_dir / f"{code}.zip") if path.exists()), None)
            if raw is None:
                raise ValueError("找不到私有 XBRL 原始檔")
            metrics, sources, candidates = extract_metrics(read_ixbrl(raw))
            if not metrics:
                raise ValueError("未辨識到可用的核心 XBRL 概念")
            record = {"code": code, "period": metadata.get("period"), "source": metadata.get("source"), "metrics": metrics, "concepts": sources, "normalizedAt": datetime.now(timezone.utc).isoformat()}
            save(output_dir / f"{code}.json", record)
            parsed[code] = {"period": metadata.get("period"), "metricCount": len(metrics), "metrics": sorted(metrics)}
            candidate_concepts.update(candidates)
        except Exception as error:
            failures[code] = f"{type(error).__name__}: {error}"
    status = {"schemaVersion": 2, "provider": "MOPS official XBRL normalizer", "cacheVisibility": "private GitHub Actions cache; no financial values are committed", "updatedAt": datetime.now(timezone.utc).isoformat(), "coverage": {"sourceFilings": len(list(source_dir.glob('*.json'))), "normalised": len(parsed), "failed": len(failures)}, "parsed": parsed, "conceptCandidates": sorted(candidate_concepts), "failures": failures}
    save(ROOT / "data" / "mops-xbrl-normalization-status.json", status)
    print(json.dumps(status, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
