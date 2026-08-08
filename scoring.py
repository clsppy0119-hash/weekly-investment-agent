"""The ranking rule the daily report ships, in one importable place.

This used to live inside ``daily_report.py``, which runs its whole pipeline on
import, so nothing else could reuse it.  The backtest therefore validated a
plain price-momentum rule that the product never actually runs.  Keeping the
rule here lets the report and the backtest score candidates with the same code,
so a backtest result says something about the recommendations users see.
"""

from __future__ import annotations

WEIGHTS = {
    "value": {"revenue": 15, "eps": 15, "roe": 15, "debt": 15, "pe": 15, "pb": 10, "dividend": 5, "trend20": 10},
    "swing": {"revenue": 10, "eps": 5, "roe": 5, "debt": 5, "pe": 5, "trend20": 25, "trend5": 20, "change": 25},
    "dividend": {"revenue": 10, "eps": 15, "roe": 15, "debt": 15, "pe": 10, "pb": 5, "dividend": 25, "trend20": 5},
    "comprehensive": {"revenue": 15, "eps": 12, "roe": 12, "debt": 10, "pe": 10, "pb": 6, "dividend": 7, "trend20": 12, "trend5": 7, "change": 9},
}

MINIMUM_COVERAGE = {"value": 70, "swing": 45, "dividend": 70, "comprehensive": 70}
MINIMUM_SCORE = 60
DEFAULT_PICKS = 3


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def score_metric(value, thresholds, default=None):
    if not number(value):
        return default
    for limit, score in thresholds:
        if value >= limit:
            return score
    return thresholds[-1][1]


TREND_SLOPE = 2.5


def trend_score(price, average, continuous: bool):
    """Position relative to a moving average.

    The shipped form is binary, which discards how far above the average a
    stock sits and makes scores tie in large blocks.  The continuous form keeps
    the distance; it is opt-in until a backtest shows it is worth shipping.
    """
    if not (number(price) and number(average)) or average <= 0:
        return None
    if not continuous:
        return 75 if price >= average else 35
    return max(0.0, min(100.0, 50 + (price / average - 1) * 100 * TREND_SLOPE))


def metrics(quote: dict, fund: dict, continuous_trend: bool = False) -> dict:
    """Per-factor scores, or ``None`` where the input is missing."""
    return {
        "revenue": score_metric(fund.get("revenueYoY"), [(20, 90), (10, 80), (0, 65), (-10, 45), (-999999, 20)]),
        "eps": 70 if number(fund.get("eps")) and fund["eps"] > 0 else (15 if number(fund.get("eps")) else None),
        "roe": score_metric(fund.get("roe"), [(20, 90), (15, 80), (8, 65), (5, 45), (-999999, 25)]),
        "debt": score_metric(-fund["debtRatio"], [(-30, 90), (-50, 75), (-70, 50), (-999999, 20)]) if number(fund.get("debtRatio")) else None,
        "pe": score_metric(-fund["pe"], [(-12, 85), (-20, 75), (-30, 60), (-45, 40), (-999999, 20)]) if number(fund.get("pe")) and fund["pe"] > 0 else None,
        "pb": score_metric(-fund["pb"], [(-1.5, 85), (-3, 70), (-6, 50), (-999999, 30)]) if number(fund.get("pb")) and fund["pb"] > 0 else None,
        "dividend": (85 if 3 <= fund["dividendYield"] <= 8 else 45 if fund["dividendYield"] > 10 else 65 if fund["dividendYield"] >= 2 else 35) if number(fund.get("dividendYield")) else None,
        "trend20": trend_score(quote.get("price"), quote.get("ma20"), continuous_trend),
        "trend5": trend_score(quote.get("price"), quote.get("ma5"), continuous_trend),
        "change": min(90, 65 + quote["change"] * 3) if number(quote.get("change")) and quote["change"] > 0 else max(15, 50 + quote["change"] * 3) if number(quote.get("change")) else None,
    }


def score_quote(quote: dict, fund: dict, style: str, weights: dict | None = None,
                continuous_trend: bool = False) -> tuple[int, int]:
    """Weighted score and the weight actually backed by present data.

    ``weights`` overrides the style's table, which lets research measure one
    factor's contribution without editing the shipped weights.
    """
    weights = WEIGHTS[style] if weights is None else weights
    metric = metrics(quote, fund, continuous_trend)
    available_weight = sum(weight for key, weight in weights.items() if number(metric[key]))
    weighted = sum(metric[key] * weight for key, weight in weights.items() if number(metric[key]))
    return (round(weighted / available_weight) if available_weight else 0), available_weight


def stock_score(code, style, quotes, fundamentals, weights: dict | None = None,
                continuous_trend: bool = False):
    return score_quote(quotes.get(code, {}), fundamentals.get(code, {}), style, weights, continuous_trend)


def candidates(style, quotes, fundamentals, picks: int = DEFAULT_PICKS,
               weights: dict | None = None, minimum_coverage: int | None = None,
               continuous_trend: bool = False):
    ranked = []
    if minimum_coverage is None:
        minimum_coverage = MINIMUM_COVERAGE[style]
    for code, fund in fundamentals.items():
        quote = quotes.get(code, {})
        if not (code.isdigit() and len(code) == 4 and number(quote.get("price"))):
            continue
        score, coverage = stock_score(code, style, quotes, fundamentals, weights, continuous_trend)
        if score >= MINIMUM_SCORE and coverage >= minimum_coverage:
            ranked.append((score, coverage, code, quote, fund))
    # Scores tie constantly -- the trend factors are binary and `change` is
    # capped -- and sorting the raw tuple broke those ties on the stock code,
    # descending.  That silently bought the highest-numbered names, which in
    # Taiwan skews to small speculative listings.  Break on traded volume
    # instead, keeping the code only as a last resort for reproducibility.
    ranked.sort(key=lambda item: (item[0], item[1], item[3].get("volume") or 0, item[2]), reverse=True)
    return ranked[:picks]
