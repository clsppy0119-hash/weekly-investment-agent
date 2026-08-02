"""CLI and local HTTP entrypoint for the review-gated investment research agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

from agent import load_market_data
from research_team import run_research_team


ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = Path(__file__).resolve().parent / "data" / "audit-log.jsonl"

# Ignored local files are read only at runtime; no secret is logged or committed.
load_dotenv(Path(__file__).resolve().parent / ".env.local")
load_dotenv(ROOT / ".env.local")


def audit_data() -> dict:
    data = load_market_data()
    quotes = data.get("quotes", {})
    fundamentals = data.get("fundamentals", {})
    priced = sum(1 for item in quotes.values() if isinstance(item.get("price"), (int, float)))
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "quotes": len(quotes),
        "priced_quotes": priced,
        "fundamentals": len(fundamentals),
        "updated_at": data.get("updatedAt"),
        "status": "ok" if priced else "needs_attention",
        "note": "僅檢查資料完整性；不會修改投資規則或交易紀錄。",
    }
    AUDIT_LOG.parent.mkdir(exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "audit": audit_data()})
        else:
            self._send(404, {"error": "not_found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve(port: int) -> None:
    print(f"Investment Agent running at http://127.0.0.1:{port}/health")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="本機台股研究 Agent")
    parser.add_argument("--health", action="store_true", help="檢查本機資料健康度")
    parser.add_argument("--research", metavar="CODE", help="以 Agent 研究一檔股票")
    parser.add_argument("--serve", action="store_true", help="啟動本機健康檢查服務")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(audit_data(), ensure_ascii=False, indent=2))
        return 0
    if args.research:
        if not os.environ.get("OPENAI_API_KEY"):
            print("找不到 OPENAI_API_KEY；不會執行研究。", file=sys.stderr)
            return 2
        try:
            print(asyncio.run(run_research_team([args.research])))
            return 0
        except Exception as error:  # Keep local operation safe even when the API account is unavailable.
            if "insufficient_quota" in str(error):
                print(
                    "OpenAI API 目前沒有可用額度，未產生研究報告。請在 Platform 設定計費或額度後重試。",
                    file=sys.stderr,
                )
                return 3
            print("研究服務暫時無法使用；未產生或傳送任何報告。", file=sys.stderr)
            return 1
    if args.serve or os.environ.get("PORT"):
        serve(int(os.environ.get("PORT", "8787")))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
