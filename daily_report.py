import json
from datetime import datetime, timezone, timedelta

with open("quotes.json", encoding="utf-8") as source:
    data = json.load(source)

quotes = data.get("quotes", {})
valid = [(code, row) for code, row in quotes.items() if isinstance(row.get("price"), (int, float))]
gainers = sorted(valid, key=lambda item: item[1].get("change", 0), reverse=True)[:5]
volume = sorted(valid, key=lambda item: item[1].get("volume", 0), reverse=True)[:5]
now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

def line(item):
    code, row = item
    change = row.get("change", 0)
    return f"{row.get('name', code)}（{code}） {row['price']:.2f}，漲跌 {change:+.2f}"

report = "\n".join([
    f"📊 台股每日摘要｜{now}",
    f"資料時間：{data.get('updatedAt', '未知')}",
    "",
    "今日漲幅觀察：",
    *[f"• {line(item)}" for item in gainers],
    "",
    "成交量觀察：",
    *[f"• {line(item)}" for item in volume],
    "",
    "此摘要僅供研究參考，不構成投資建議。",
])

with open("daily-report.txt", "w", encoding="utf-8") as output:
    output.write(report)
