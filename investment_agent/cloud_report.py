"""Coordinate the bounded multi-role review for deterministic report candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from agent import load_market_data
from research_team import run_research_team

ROOT = Path(__file__).resolve().parent.parent
TRACKER = ROOT / "strategy_data" / "recommendations.json"


def _candidate_codes(mode: str, report_path: Path, limit: int) -> list[str]:
    market = load_market_data().get("quotes", {})
    codes: list[str] = []
    if mode in ("daily", "comprehensive") and TRACKER.exists():
        state = json.loads(TRACKER.read_text(encoding="utf-8"))
        rows = [row for row in state.get("recommendations", []) if row.get("mode") == mode]
        if rows:
            newest = max(str(row.get("date", "")) for row in rows)
            rows = sorted(
                (row for row in rows if str(row.get("date", "")) == newest),
                key=lambda row: int(row.get("rank", 999)),
            )
            codes.extend(str(row.get("code", "")).strip() for row in rows)
    if report_path.exists():
        codes.extend(re.findall(r"(?<!\d)(\d{4})(?!\d)", report_path.read_text(encoding="utf-8")))
    return list(dict.fromkeys(code for code in codes if code in market))[:limit]


async def _build(mode: str, report_path: Path, limit: int) -> str:
    codes = _candidate_codes(mode, report_path, limit)
    if not codes:
        return "【AI 綜合投資研究】\n本次沒有可驗證的候選股票代碼。"
    return await run_research_team(codes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "long", "comprehensive"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        args.output.write_text("AI 綜合研究：未設定 OPENAI_API_KEY，本次只保留規則式報告。\n", encoding="utf-8")
        return 2
    args.output.write_text(asyncio.run(_build(args.mode, args.report, max(1, min(args.limit, 3))) ) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
