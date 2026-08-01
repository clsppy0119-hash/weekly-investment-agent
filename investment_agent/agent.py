"""OpenAI Agents SDK research layer. It never places orders or alters strategy rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents import Agent, Runner, function_tool


ROOT = Path(__file__).resolve().parent.parent
QUOTE_FILE = ROOT / "quotes.json"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_market_data() -> dict[str, Any]:
    with QUOTE_FILE.open(encoding="utf-8") as source:
        return json.load(source)


@function_tool
def get_stock_snapshot(stock_code: str) -> str:
    """Return the latest locally stored Taiwan-stock quote and available fundamentals."""
    code = str(stock_code).strip()
    data = load_market_data()
    quote = data.get("quotes", {}).get(code)
    if not quote:
        return json.dumps({"found": False, "stock_code": code}, ensure_ascii=False)
    return json.dumps(
        {
            "found": True,
            "updated_at": data.get("updatedAt"),
            "stock_code": code,
            "quote": quote,
            "fundamentals": data.get("fundamentals", {}).get(code, {}),
        },
        ensure_ascii=False,
    )


@function_tool
def get_data_quality(stock_code: str) -> str:
    """Report which required long-term research metrics are missing locally."""
    code = str(stock_code).strip()
    data = load_market_data()
    fund = data.get("fundamentals", {}).get(code, {})
    required = ["revenueYoY", "eps", "roe", "debtRatio", "pe", "pb", "dividendYield"]
    missing = [key for key in required if not _is_number(fund.get(key))]
    return json.dumps(
        {"stock_code": code, "available": sorted(set(required) - set(missing)), "missing": missing},
        ensure_ascii=False,
    )


INSTRUCTIONS = """
你是台股研究助理，只以工具提供的資料做研究。全程使用繁體中文。
不可執行交易、不可要求或揭露任何密鑰、不可聲稱取得工具沒有提供的數字或事件。
先呼叫 get_stock_snapshot 與 get_data_quality。資料不足時，要清楚列為「資料不足」，
結論只能是「觀察」，不可把猜測寫成買進建議。
輸出使用這個精簡格式：股票代號／名稱、資料時間、研究結論、一句話論點、
支持證據、缺失資料、最大三項風險、追蹤條件。這是研究資訊，不構成投資建議。
""".strip()


research_agent = Agent(
    name="台股研究 Agent",
    instructions=INSTRUCTIONS,
    tools=[get_stock_snapshot, get_data_quality],
)


async def research(stock_code: str) -> str:
    result = await Runner.run(research_agent, f"請研究台股 {stock_code}。")
    return str(result.final_output)
