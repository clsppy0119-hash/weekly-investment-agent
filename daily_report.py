import json
from datetime import datetime, timedelta, timezone


TZ_TAIPEI = timezone(timedelta(hours=8))


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def score_metric(value, thresholds, default=None):
    if not number(value):
        return default
    for limit, score in thresholds:
        if value >= limit:
            return score
    return thresholds[-1][1]


def stock_score(code, style, quotes, fundamentals):
    quote = quotes.get(code, {})
    fund = fundamentals.get(code, {})
    weights = {
        "value": {"revenue": 15, "eps": 15, "roe": 15, "debt": 15, "pe": 15, "pb": 10, "dividend": 5, "trend20": 10},
        "swing": {"revenue": 10, "eps": 5, "roe": 5, "debt": 5, "pe": 5, "trend20": 25, "trend5": 20, "change": 25},
        "dividend": {"revenue": 10, "eps": 15, "roe": 15, "debt": 15, "pe": 10, "pb": 5, "dividend": 25, "trend20": 5},
    }[style]

    revenue = fund.get("revenueYoY")
    metric = {
        "revenue": score_metric(revenue, [(20, 90), (10, 80), (0, 65), (-10, 45), (-999999, 20)]),
        "eps": 70 if number(fund.get("eps")) and fund["eps"] > 0 else (15 if number(fund.get("eps")) else None),
        "roe": score_metric(fund.get("roe"), [(20, 90), (15, 80), (8, 65), (5, 45), (-999999, 25)]),
        "debt": score_metric(-fund["debtRatio"], [(-30, 90), (-50, 75), (-70, 50), (-999999, 20)]) if number(fund.get("debtRatio")) else None,
        "pe": score_metric(-fund["pe"], [(-12, 85), (-20, 75), (-30, 60), (-45, 40), (-999999, 20)]) if number(fund.get("pe")) and fund["pe"] > 0 else None,
        "pb": score_metric(-fund["pb"], [(-1.5, 85), (-3, 70), (-6, 50), (-999999, 30)]) if number(fund.get("pb")) and fund["pb"] > 0 else None,
        "dividend": (85 if 3 <= fund["dividendYield"] <= 8 else 45 if fund["dividendYield"] > 10 else 65 if fund["dividendYield"] >= 2 else 35) if number(fund.get("dividendYield")) else None,
        "trend20": (75 if quote.get("price") >= quote.get("ma20") else 35) if number(quote.get("price")) and number(quote.get("ma20")) else None,
        "trend5": (75 if quote.get("price") >= quote.get("ma5") else 35) if number(quote.get("price")) and number(quote.get("ma5")) else None,
        "change": min(90, 65 + quote["change"] * 3) if number(quote.get("change")) and quote["change"] > 0 else max(15, 50 + quote["change"] * 3) if number(quote.get("change")) else None,
    }
    available_weight = sum(weight for key, weight in weights.items() if number(metric[key]))
    weighted_score = sum(metric[key] * weight for key, weight in weights.items() if number(metric[key]))
    return round(weighted_score / available_weight) if available_weight else 0, available_weight


def candidates(style, quotes, fundamentals):
    ranked = []
    minimum_coverage = {"value": 70, "swing": 45, "dividend": 70}[style]
    for code, fund in fundamentals.items():
        quote = quotes.get(code, {})
        if not (code.isdigit() and len(code) == 4 and number(quote.get("price"))):
            continue
        score, coverage = stock_score(code, style, quotes, fundamentals)
        if score >= 60 and coverage >= minimum_coverage:
            ranked.append((score, coverage, code, quote, fund))
    return sorted(ranked, reverse=True)[:3]


def candidate_line(item):
    score, coverage, code, quote, fund = item
    reasons = []
    if number(fund.get("revenueYoY")):
        reasons.append(f"營收年增 {fund['revenueYoY']:+.1f}%")
    if number(fund.get("dividendYield")):
        reasons.append(f"殖利率 {fund['dividendYield']:.1f}%")
    if number(quote.get("change")):
        reasons.append(f"當日 {quote['change']:+.2f}")
    detail = "、".join(reasons[:2]) or "可用資料有限"
    return f"• {quote.get('name', code)}（{code}）｜{quote['price']:.2f}｜評分 {score}｜{detail}"


with open("quotes.json", encoding="utf-8") as source:
    data = json.load(source)

quotes = data.get("quotes", {})
fundamentals = data.get("fundamentals", {})
valid = [(code, row) for code, row in quotes.items() if number(row.get("price"))]
gainers = sorted(valid, key=lambda item: item[1].get("change", 0), reverse=True)[:3]
volume = sorted(valid, key=lambda item: item[1].get("volume", 0), reverse=True)[:3]
today = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")


def quote_line(item):
    code, row = item
    return f"• {row.get('name', code)}（{code}）｜{row['price']:.2f}｜{row.get('change', 0):+.2f}"


styles = [("value", "長期價值投資")]
sections = []
for key, title in styles:
    items = candidates(key, quotes, fundamentals)
    lines = [candidate_line(item) for item in items] or ["• 暫無資料完整度足夠的候選，請待資料更新後再檢視。"]
    sections.extend(["", f"【{title}｜優先研究候選】", *lines])

report = "\n".join([
    f"台股每日投資日報｜長期價值投資｜{today}",
    f"行情更新：{data.get('updatedAt', '暫無時間')}",
    "",
    "【今日漲幅前段】",
    *[quote_line(item) for item in gainers],
    "",
    "【今日成交量前段】",
    *[quote_line(item) for item in volume],
    *sections,
    "",
    "以上為規則式研究排序，僅供研究參考，不構成買賣建議或報酬保證。",
])

with open("daily-report.txt", "w", encoding="utf-8") as output:
    output.write(report)
