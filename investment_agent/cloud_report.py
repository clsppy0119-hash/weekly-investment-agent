"""Review only deterministic, quality-gated candidates from a manifest."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "candidate-manifest.json"
DEFAULT_CACHE = ROOT / ".private-data-cache" / "ai-review-v1"
NO_ELIGIBLE_CANDIDATES = (
    "目前沒有通過資料品質與樣本外策略門檻的候選；本次未呼叫 AI，"
    "亦不提供買進、賣出或加碼建議。"
)


def load_market_data() -> dict[str, Any]:
    return _load_json(ROOT / "quotes.json")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _candidate_codes(mode: str, manifest_path: Path, limit: int) -> list[str]:
    manifest = _load_json(manifest_path)
    gate = manifest.get("adviceGate", {})
    if (
        manifest.get("phase") != "final"
        or manifest.get("reportMode") != mode
        or gate.get("status") != "advice_candidate"
        or gate.get("adviceEnabled") is not True
    ):
        return []
    market = load_market_data().get("quotes", {})
    codes = [
        str(item.get("code", ""))
        for item in manifest.get("eligibleCandidates", [])
        if isinstance(item, dict) and item.get("quality", {}).get("passed") is True
    ]
    return list(dict.fromkeys(code for code in codes if code in market))[:limit]


def _research_components() -> tuple[Callable, Callable, Callable]:
    # Imported only after deterministic gates pass.  This makes the zero-call
    # path independent of the SDK and guarantees it cannot invoke a model.
    try:
        from .research_team import (
            build_evidence_packets,
            research_contract,
            run_research_team_result,
        )
    except ImportError:
        from research_team import (
            build_evidence_packets,
            research_contract,
            run_research_team_result,
        )

    return build_evidence_packets, research_contract, run_research_team_result


def _evidence_hash(
    manifest: dict[str, Any],
    packets: list[dict[str, Any]],
    contract: dict[str, str],
) -> str:
    payload = {
        "manifest": manifest,
        "packets": packets,
        "contract": contract,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, evidence_hash: str) -> str | None:
    payload = _load_json(cache_dir / f"{evidence_hash}.json")
    if payload.get("schemaVersion") != 1 or payload.get("evidenceHash") != evidence_hash:
        return None
    output = payload.get("output")
    return output if isinstance(output, str) and output.strip() else None


def _write_cache(cache_dir: Path, evidence_hash: str, output: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{evidence_hash}.json"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "evidenceHash": evidence_hash,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "output": output,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def _build(mode: str, manifest_path: Path, limit: int, cache_dir: Path) -> str:
    codes = _candidate_codes(mode, manifest_path, limit)
    if not codes:
        return NO_ELIGIBLE_CANDIDATES
    manifest = _load_json(manifest_path)
    build_packets, build_contract, run_team = _research_components()
    packets = build_packets(codes)
    contract = build_contract()
    evidence_hash = _evidence_hash(manifest, packets, contract)
    cached = _read_cache(cache_dir, evidence_hash)
    if cached is not None:
        return cached
    result = await run_team(codes, packets)
    output = str(result.get("output", "")).strip() or NO_ELIGIBLE_CANDIDATES
    if result.get("cacheable") is True and output != NO_ELIGIBLE_CANDIDATES:
        _write_cache(cache_dir, evidence_hash, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "long", "comprehensive"), required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        args.output.write_text("AI 研究未啟用：尚未設定 OPENAI_API_KEY。\n", encoding="utf-8")
        return 2
    args.output.write_text(
        asyncio.run(
            _build(
                args.mode,
                args.manifest,
                max(1, min(args.limit, 3)),
                args.cache_dir,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
